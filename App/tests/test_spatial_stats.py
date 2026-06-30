"""spatial_stats（Moran's I）の単体テスト。"""
import numpy as np

from app.services import spatial_stats as ss


def _grid(n):
    """n x n 格子の座標 (N,2) を返す。"""
    xs, ys = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    return np.column_stack([xs.ravel(), ys.ravel()])


def test_structured_feature_has_high_positive_moran():
    coords = _grid(20)
    # 左半分が高、右半分が低（強い空間構造）
    vals = (coords[:, 0] < 10).astype(float)
    I = ss.morans_i(coords, vals, connectivity="rook")
    assert I > 0.8


def test_checkerboard_is_negative():
    coords = _grid(20)
    vals = ((coords[:, 0] + coords[:, 1]) % 2).astype(float)
    I = ss.morans_i(coords, vals, connectivity="rook")
    assert I < -0.8


def test_constant_feature_is_nan():
    coords = _grid(10)
    vals = np.ones(coords.shape[0])
    assert np.isnan(ss.morans_i(coords, vals))


def test_batch_matches_single():
    coords = _grid(15)
    v1 = (coords[:, 0] < 7).astype(float)
    v2 = ((coords[:, 0] + coords[:, 1]) % 2).astype(float)
    mat = np.column_stack([v1, v2])
    batch = ss.morans_i_batch(coords, mat, connectivity="rook")
    assert np.isclose(batch[0], ss.morans_i(coords, v1))
    assert np.isclose(batch[1], ss.morans_i(coords, v2))


def test_queen_vs_rook_differ():
    coords = _grid(12)
    vals = (coords[:, 1] < 6).astype(float)
    i_rook = ss.morans_i(coords, vals, connectivity="rook")
    i_queen = ss.morans_i(coords, vals, connectivity="queen")
    assert i_rook > 0.8 and i_queen > 0.8
    assert not np.isclose(i_rook, i_queen)


def test_permutation_pvalue_small_for_structured():
    coords = _grid(12)
    vals = (coords[:, 0] < 6).astype(float)
    I, p = ss.morans_i_permutation(coords, vals, n_perm=99, seed=1)
    assert I > 0.8
    assert p <= 0.05


def test_table_sorted_descending():
    coords = _grid(12)
    structured = (coords[:, 0] < 6).astype(float)
    rng = np.random.default_rng(0)
    noise = rng.random(coords.shape[0])
    mat = np.column_stack([noise, structured])
    rows = ss.spatial_autocorr_table(coords, mat, feature_names=["noise", "structured"])
    assert rows[0]["feature"] == "structured"
    assert rows[0]["morans_i"] > rows[1]["morans_i"]
