"""expression_matrix.parquet の読み出しキャッシュ (ver51.3)。

Feature 切替のたびに `pd.read_parquet` がファイルを開き直し、約 18,000 列の
フッタを毎回パースしていた。しかも 1 回の切替で update_feature_plot と
update_feature_violin が独立に同じ列を読むので、その固定費を 2 回払っていた。

ここで固定するのは 4 点:
  ① フッタ解析はファイルにつき 1 回だけ (ParquetFile ハンドルの保持)
  ② 同じ列への 2 回目以降は物理読みが発生しない
  ③ 同時に走る複数コールバックは 1 回の読みを共有する (in-flight)
  ④ ファイルが差し替わったらキャッシュは自動失効する

★ キャッシュは「速いが古い値を返す」形になると最悪なので、②③④は
   **値が正しいこと**まで必ず突き合わせる。
"""

import threading

import pandas as pd
import pytest

import app.services.seurat_bridge as SB
from app.services.seurat_bridge import SeuratBridge

pytestmark = pytest.mark.usefixtures("_clean_caches")


@pytest.fixture
def _clean_caches():
    SB.clear_expression_caches()
    yield
    SB.clear_expression_caches()


def _write_matrix(folder, cols):
    """expression_matrix.parquet を書いてパスを返す。"""
    pytest.importorskip("pyarrow")
    path = folder / "expression_matrix.parquet"
    pd.DataFrame(cols).to_parquet(str(path), index=False)
    return path


def test_repeat_read_hits_cache_and_keeps_values(tmp_path):
    """同じ列を 2 回読むと物理読みは 1 回。値は毎回正しい。"""
    _write_matrix(tmp_path, {"mz_100": [1.0, 2.0, 3.0], "mz_200": [4.0, 5.0, 6.0]})
    bridge = SeuratBridge()

    calls = []
    real = SB._read_parquet_columns

    def counting(entry, columns):
        calls.append(list(columns))
        return real(entry, columns)

    SB._read_parquet_columns = counting
    try:
        first = bridge.get_feature_expression_fast(tmp_path, "mz_100")
        second = bridge.get_feature_expression_fast(tmp_path, "mz_100")
    finally:
        SB._read_parquet_columns = real

    assert list(first) == [1.0, 2.0, 3.0]
    assert list(second) == [1.0, 2.0, 3.0], "キャッシュ経由でも値が一致すること"
    assert calls == [["mz_100"]], f"物理読みが 1 回に収まっていない: {calls}"


def test_footer_is_parsed_once_per_file(tmp_path):
    """列を何本読んでも ParquetFile の生成 (= フッタ解析) は 1 回。"""
    pq = pytest.importorskip("pyarrow.parquet")
    _write_matrix(tmp_path, {"mz_100": [1.0, 2.0], "mz_200": [3.0, 4.0],
                             "mz_300": [5.0, 6.0]})
    bridge = SeuratBridge()

    opened = []
    real_pf = pq.ParquetFile

    def counting_pf(*a, **kw):
        opened.append(a[0] if a else None)
        return real_pf(*a, **kw)

    pq.ParquetFile = counting_pf
    try:
        for name in ("mz_100", "mz_200", "mz_300", "mz_100"):
            got = bridge.get_feature_expression_fast(tmp_path, name)
            assert got is not None
    finally:
        pq.ParquetFile = real_pf

    assert len(opened) == 1, (
        f"フッタが {len(opened)} 回パースされている (1 回であるべき)")


def test_concurrent_readers_share_one_read(tmp_path):
    """同じ列への同時要求が 1 回の物理読みに畳まれ、全員が正しい値を得る。

    1 回の m/z 切替で update_feature_plot と update_feature_violin が
    別スレッドから同じ列を要求する状況の再現。
    """
    _write_matrix(tmp_path, {"mz_100": [7.0, 8.0, 9.0]})
    bridge = SeuratBridge()

    calls = []
    real = SB._read_parquet_columns
    started = threading.Event()
    release = threading.Event()

    def slow(entry, columns):
        calls.append(list(columns))
        started.set()
        release.wait(timeout=10)   # 後続スレッドが in-flight に相乗りする隙を作る
        return real(entry, columns)

    SB._read_parquet_columns = slow
    results = {}

    def worker(tag):
        results[tag] = bridge.get_feature_expression_fast(tmp_path, "mz_100")

    try:
        leader = threading.Thread(target=worker, args=("leader",))
        leader.start()
        assert started.wait(timeout=10), "先行スレッドが読み始めていない"
        followers = [threading.Thread(target=worker, args=(f"f{i}",))
                     for i in range(3)]
        for t in followers:
            t.start()
        # follower が in-flight を掴むまで待ってから解放する
        threading.Event().wait(0.2)
        release.set()
        leader.join(timeout=15)
        for t in followers:
            t.join(timeout=15)
    finally:
        SB._read_parquet_columns = real
        release.set()

    assert len(results) == 4, f"完走しなかったスレッドがある: {sorted(results)}"
    for tag, series in results.items():
        assert series is not None, f"{tag} が None を受け取った"
        assert list(series) == [7.0, 8.0, 9.0], f"{tag} の値が違う"
    assert len(calls) == 1, f"物理読みが {len(calls)} 回走っている (1 回であるべき)"


def test_returned_series_is_a_copy(tmp_path):
    """呼び出し元が戻り値を書き換えてもキャッシュが汚れない。"""
    _write_matrix(tmp_path, {"mz_100": [1.0, 2.0, 3.0]})
    bridge = SeuratBridge()

    first = bridge.get_feature_expression_fast(tmp_path, "mz_100")
    first.iloc[0] = 999.0

    second = bridge.get_feature_expression_fast(tmp_path, "mz_100")
    assert list(second) == [1.0, 2.0, 3.0], \
        "戻り値の書き換えがキャッシュに漏れている"


def test_cache_invalidated_when_file_replaced(tmp_path):
    """ファイルが差し替わったら新しい値を返すこと（古い値を出さない）。"""
    import time as _time

    path = _write_matrix(tmp_path, {"mz_100": [1.0, 2.0]})
    bridge = SeuratBridge()
    assert list(bridge.get_feature_expression_fast(tmp_path, "mz_100")) == [1.0, 2.0]

    _time.sleep(0.01)
    pd.DataFrame({"mz_100": [10.0, 20.0, 30.0]}).to_parquet(str(path), index=False)

    got = bridge.get_feature_expression_fast(tmp_path, "mz_100")
    assert list(got) == [10.0, 20.0, 30.0], "差し替え後も古いキャッシュを返している"


def test_column_cache_is_bounded(tmp_path, monkeypatch):
    """列キャッシュが上限を超えて溜まらないこと（1 列 ≈ 1.6MB なので上限は必須）。"""
    monkeypatch.setattr(SB, "_FEATURE_COL_CACHE_MAX", 3)
    names = [f"mz_{i}" for i in range(8)]
    _write_matrix(tmp_path, {n: [float(i)] for i, n in enumerate(names)})
    bridge = SeuratBridge()

    for n in names:
        assert bridge.get_feature_expression_fast(tmp_path, n) is not None

    assert len(SB._FEATURE_COL_CACHE) <= 3, \
        f"列キャッシュが上限を超えている: {len(SB._FEATURE_COL_CACHE)}"
    # 追い出された列も読み直して同じ値が出ること（正しさはキーの側にある）
    assert list(bridge.get_feature_expression_fast(tmp_path, "mz_0")) == [0.0]


def test_handle_cache_is_bounded(tmp_path, monkeypatch):
    """ParquetFile ハンドル（= 開いた fd）が無制限に溜まらないこと。"""
    monkeypatch.setattr(SB, "_PARQUET_FILE_CACHE_MAX", 2)
    bridge = SeuratBridge()
    for i in range(5):
        d = tmp_path / f"p{i}"
        d.mkdir()
        _write_matrix(d, {"mz_100": [float(i)]})
        assert list(bridge.get_feature_expression_fast(d, "mz_100")) == [float(i)]
    assert len(SB._PARQUET_FILE_CACHE) <= 2, \
        f"ハンドルが上限を超えている: {len(SB._PARQUET_FILE_CACHE)}"


def test_missing_column_still_returns_none(tmp_path, caplog):
    """既存の契約: 列が無ければ None + 警告 (R フォールバックへ落ちる)。"""
    import logging

    _write_matrix(tmp_path, {"mz_100": [1.0, 2.0]})
    bridge = SeuratBridge()
    with caplog.at_level(logging.WARNING, logger="msi.seurat_bridge"):
        assert bridge.get_feature_expression_fast(tmp_path, "mz_NOPE") is None
    assert any("mz_NOPE" in r.getMessage() for r in caplog.records)


def test_get_features_matrix_matches_direct_read(tmp_path):
    """複数列読み (共発現用) がハンドル経由でも従来と同じ結果を返すこと。"""
    path = _write_matrix(tmp_path, {"mz_100": [1.0, 2.0], "mz_200": [3.0, 4.0],
                                    "mz_300": [5.0, 6.0]})
    bridge = SeuratBridge()
    df, present = bridge.get_features_matrix(tmp_path, ["mz_300", "mz_100", "nope"])

    assert present == ["mz_300", "mz_100"]
    expected = pd.read_parquet(str(path), columns=["mz_300", "mz_100"])
    pd.testing.assert_frame_equal(df, expected)
