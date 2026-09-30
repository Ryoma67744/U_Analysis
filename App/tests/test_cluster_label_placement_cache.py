"""配置キャッシュがデータ・行順・設定をまたいで位置を取り違えないこと。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from app.utils import cluster_label_placement as P


@pytest.fixture(autouse=True)
def clear_cache():
    P._clear_anchor_cache()
    yield
    P._clear_anchor_cache()


@pytest.fixture
def spy(monkeypatch):
    calls = []
    original = P._compute_label_anchors_uncached

    def tracked(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(P, "_compute_label_anchors_uncached", tracked)
    return calls


def _grid():
    x, y = np.meshgrid(np.arange(7, dtype=float), np.arange(5, dtype=float))
    return x.ravel(), y.ravel(), np.where(x.ravel() < 3, "a", "b")


def test_identical_geometry_reuses_immutable_result_but_returns_new_dict(spy):
    x, y, labels = _grid()
    first = P.compute_label_anchors(x, y, labels, kind="spatial")
    second = P.compute_label_anchors(x.copy(), y.copy(), labels.copy(), kind="spatial")
    assert len(spy) == 1
    assert first == second
    assert first is not second
    with pytest.raises(FrozenInstanceError):
        first["a"].x = 300
    first.clear()
    assert P.compute_label_anchors(x, y, labels, kind="spatial") == second


def test_input_mutation_dataset_change_and_label_assignment_invalidate(spy):
    x, y, labels = _grid()
    initial = P.compute_label_anchors(x, y, labels, kind="spatial")
    x += 100
    moved = P.compute_label_anchors(x, y, labels, kind="spatial")
    assert moved["a"].x == initial["a"].x + 100
    labels[:] = "c"
    changed = P.compute_label_anchors(x, y, labels, kind="spatial")
    assert set(changed) == {"c"}
    assert len(spy) == 3


def test_reordered_rows_recompute_original_index(spy):
    x, y, labels = _grid()
    initial = P.compute_label_anchors(x, y, labels, kind="spatial")
    order = np.random.default_rng(34).permutation(len(x))
    reordered = P.compute_label_anchors(x[order], y[order], labels[order], kind="spatial")
    assert len(spy) == 2
    for key in initial:
        assert reordered[key].x == initial[key].x
        assert reordered[key].y == initial[key].y
        assert x[order][reordered[key].index] == initial[key].x
        assert labels[order][reordered[key].index] == key


def test_exclusion_reference_kind_and_parameters_have_distinct_keys(spy, monkeypatch):
    x, y, labels = _grid()
    P.compute_label_anchors(x, y, labels, kind="spatial")
    keep = x != 0
    P.compute_label_anchors(x[keep], y[keep], labels[keep], kind="spatial")
    P.compute_label_anchors(x[keep], y[keep], labels[keep], kind="spatial", grid_reference=(x, y))
    rx, ry = np.meshgrid(np.arange(0, 7, 0.5), np.arange(5))
    P.compute_label_anchors(x[keep], y[keep], labels[keep], kind="spatial",
                            grid_reference=(rx.ravel(), ry.ravel()))
    P.compute_label_anchors(x, y, labels, kind="umap")
    monkeypatch.setattr(P, "_SUPPORT_RADIUS_FACTOR", 0.8)
    P.compute_label_anchors(x, y, labels, kind="umap")
    assert len(spy) == 6


def test_cache_evicts_by_entries_points_and_anchor_count(spy, monkeypatch):
    monkeypatch.setattr(P, "_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(P, "_CACHE_MAX_POINTS", 200)
    monkeypatch.setattr(P, "_CACHE_MAX_ANCHORS", 4)
    x, y, labels = _grid()
    for shift in (0, 10, 20):
        P.compute_label_anchors(x + shift, y, labels, kind="spatial")
    assert len(P._ANCHOR_CACHE) == 2
    assert P._CACHE_POINTS <= 200
    assert P._CACHE_ANCHORS <= 4
    P.compute_label_anchors(x, y, labels, kind="spatial")
    assert len(spy) == 4
    monkeypatch.setattr(P, "_CACHE_MAX_POINTS", 40)
    P.compute_label_anchors(x + 30, y, labels, kind="spatial")
    assert len(P._ANCHOR_CACHE) == 1
    assert P._CACHE_POINTS == len(x)


def test_point_limit_bypasses_fingerprint_and_tree(monkeypatch):
    x, y, labels = _grid()
    monkeypatch.setattr(P, "_MAX_POINTS", 10)

    def forbidden(*args, **kwargs):
        raise AssertionError("資源上限を超えた入力で重い処理を実行した")

    monkeypatch.setattr(P, "_fingerprint", forbidden)
    monkeypatch.setattr(P, "cKDTree", forbidden)
    anchors = P.compute_label_anchors(x, y, labels)
    assert all(a.reason == "fallback_point_limit" for a in anchors.values())


def test_parallel_calls_keep_cache_accounting_and_indices_consistent():
    x, y, labels = _grid()

    def calculate(shift):
        return P.compute_label_anchors(x + shift, y, labels, kind="spatial")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(calculate, [0, 20, 0, 40, 20, 40, 0, 20]))
    assert results[0] == results[2] == results[6]
    assert results[1] == results[4] == results[7]
    assert len(P._ANCHOR_CACHE) == 3
    assert P._CACHE_POINTS == 3 * len(x)
    assert P._CACHE_ANCHORS == 6
