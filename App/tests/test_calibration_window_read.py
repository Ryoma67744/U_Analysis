"""キャリブレーションが参照窓内の列だけ読むこと (ver51.5)。

従来は `pd.read_parquet(expr_path)` を **列指定なし**で呼び、全 feature の平均
スペクトルを作っていた。実データ規模 (203,078 行 × 1,536 列 float64) で 1 回
2.32GB、18,000 列なら 29.2GB になり 12GB コンテナでは OOM する。
しかも実際に参照するのは各参照 m/z の ±search_window 内にある列だけだった。

★ ここで最も大事なのは速さではなく **答えが変わらないこと**。
   窓外を読まなくなったせいで別のピークが選ばれる、という壊れ方が最悪なので、
   新旧の `_calibrate_mz` 出力を突き合わせて固定する。
"""

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("dash")
pytest.importorskip("pyarrow")

import app.services.seurat_bridge as SB  # noqa: E402
from app.callbacks.interactive_calibration import (  # noqa: E402
    _calibrate_mz, _features_within_windows, window_avg_spectrum,
)


N_ROWS = 40
# 参照 m/z 3 本。ピークはそのすぐ近くに置く (ppm ずれを作る)
REFS = [200.0, 400.0, 600.0]


def _make_matrix(tmp_path, n_features=300):
    """m/z 名の feature を持つ合成 expression_matrix.parquet を作る。

    参照 m/z の近傍には「窓内で最大強度」になる列を仕込み、遠方には
    さらに強い列を置く。**窓外を誤って拾うと結果が変わる**ように仕向ける。
    """
    rng = np.random.default_rng(0)
    cols = {}
    mzs = np.linspace(100.0, 900.0, n_features)
    for mz in mzs:
        cols[f"m/z {mz:.5f}"] = rng.uniform(0.0, 1.0, N_ROWS)
    # 各参照のすぐ横 (+0.02 Da) に中程度のピークを置く
    for r in REFS:
        cols[f"m/z {r + 0.02:.5f}"] = rng.uniform(5.0, 6.0, N_ROWS)
    # 窓の外 (±5 Da) に **さらに強い** 囮を置く
    for r in REFS:
        cols[f"m/z {r + 5.0:.5f}"] = rng.uniform(90.0, 100.0, N_ROWS)
    df = pd.DataFrame(cols)
    path = tmp_path / "expression_matrix.parquet"
    df.to_parquet(str(path), index=False)
    return path, df


@pytest.fixture(autouse=True)
def _clean():
    SB.clear_expression_caches()
    yield
    SB.clear_expression_caches()


def test_window_selection_picks_only_nearby_features():
    names = ["m/z 199.90000", "m/z 200.10000", "m/z 205.00000", "no_mz_here"]
    wanted, mz_values = _features_within_windows(names, [200.0], 0.5)
    assert wanted == ["m/z 199.90000", "m/z 200.10000"]
    # 全 feature の m/z は返る (候補探索に要るため)
    assert set(mz_values) == {"m/z 199.90000", "m/z 200.10000", "m/z 205.00000"}


def test_window_selection_handles_bad_input():
    assert _features_within_windows([], [200.0], 0.5) == ([], {})
    assert _features_within_windows(["m/z 200.0"], [], 0.5)[0] == []
    # 参照が数値でなくても落ちない
    assert _features_within_windows(["m/z 200.0"], ["abc", None], 0.5)[0] == []


def test_calibration_result_is_identical_to_full_read(tmp_path):
    """★ 新旧で **同じ参照ピークが選ばれ、同じ補正になる** こと。"""
    path, df = _make_matrix(tmp_path)
    features_list = list(df.columns)

    # --- 旧実装相当: 全列を読んで全 feature の平均を作る ---
    full = pd.read_parquet(str(path))
    old_avg = {f: float(full[f].mean()) for f in features_list}
    old = _calibrate_mz(features_list, old_avg, REFS,
                        search_window=0.5, min_peaks=2)

    # --- 新実装: 窓内の列だけ読む ---
    new_avg = window_avg_spectrum(path, features_list, REFS, 0.5)
    new = _calibrate_mz(features_list, new_avg, REFS,
                        search_window=0.5, min_peaks=2)

    assert old["calibrated"] is True, "テスト前提: 旧実装で補正が成立すること"
    assert new["calibrated"] == old["calibrated"]
    # 選ばれた観測ピークが 1 本も違わないこと
    assert [r["obs_mz"] for r in new["report"]] == [r["obs_mz"] for r in old["report"]]
    assert [r["ref_mz"] for r in new["report"]] == [r["ref_mz"] for r in old["report"]]
    assert new["corrected_mz_map"] == old["corrected_mz_map"]


def test_only_window_columns_are_read(tmp_path):
    """窓内の列しか parquet から読まないこと (読む列数を数える)。"""
    path, df = _make_matrix(tmp_path)
    features_list = list(df.columns)

    read_cols = []
    real = SB._read_parquet_columns

    def spy(entry, columns):
        read_cols.extend(columns)
        return real(entry, columns)

    SB._read_parquet_columns = spy
    try:
        avg = window_avg_spectrum(path, features_list, REFS, 0.5)
    finally:
        SB._read_parquet_columns = real

    assert avg, "平均が取れていない"
    assert len(read_cols) < len(features_list) / 10, (
        f"{len(read_cols)} / {len(features_list)} 列も読んでいる")
    # 読んだ列はすべてどれかの参照窓の中
    for c in read_cols:
        mz = float(c.split()[-1])
        assert any(abs(mz - r) <= 0.5 for r in REFS), f"窓外の列を読んでいる: {c}"
    # ★ 窓外の「より強い囮」を拾っていないこと
    assert not any(abs(float(c.split()[-1]) - (r + 5.0)) < 1e-6
                   for c in read_cols for r in REFS), "窓外の囮を読んでいる"


def test_missing_parquet_returns_none(tmp_path):
    """parquet が無ければ None (呼び出し側はエラー表示へ倒す)。"""
    assert window_avg_spectrum(
        tmp_path / "expression_matrix.parquet", ["m/z 200.0"], REFS, 0.5) is None


def test_calibrate_accepts_empty_avg_spectrum():
    """平均が空でも落ちず「補正できない」と返すこと。"""
    out = _calibrate_mz(["m/z 200.00000"], {}, REFS, search_window=0.5, min_peaks=2)
    assert out["calibrated"] is False


# ---------------------------------------------------------------------------
# scipy 依存の除去 (ver51.5)
# ---------------------------------------------------------------------------
# `_calibrate_mz` / `_calibrate_mz_from_pairs` は scipy.stats.linregress を
# import していたが、**scipy は requirements.txt にも Dockerfile にも無く、
# どの依存からも入らない**。つまり本番イメージでは linear 回帰を選んだ瞬間に
# ModuleNotFoundError になり、読み込み経路の `except Exception` に拾われて
# 「m/zキャリブレーションに失敗したため未適用」と出るだけだった。

def test_no_scipy_import_in_source():
    """★ scipy を import し直さないこと。

    requirements.txt に無いので、復活すると本番で機能が無言で止まる。
    """
    import re
    from pathlib import Path as _P

    root = _P(__file__).resolve().parents[1]
    offenders = []
    for pyf in (root / "app").rglob("*.py"):
        src = pyf.read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            if re.match(r"\s*(from scipy|import scipy)", line):
                offenders.append(f"{pyf.relative_to(root)}:{i}")
    assert not offenders, (
        "scipy の import が復活している。requirements.txt に無いので "
        f"本番で ModuleNotFoundError になる: {offenders}")


def test_linear_fit_matches_scipy():
    """numpy 実装が scipy.stats.linregress と数値一致すること。

    scipy がある環境でのみ実行 (本番には無いのでスキップされる)。
    """
    linregress = pytest.importorskip("scipy.stats").linregress
    from app.callbacks.interactive_calibration import _linear_fit

    rng = np.random.default_rng(7)
    for n in (2, 3, 10, 100):
        x = np.sort(rng.uniform(100, 900, n))
        y = 3.5 * x - 12.0 + rng.normal(0, 0.5, n)
        slope, intercept, r2 = _linear_fit(x, y)
        s_slope, s_intercept, s_r, _, _ = linregress(x, y)
        assert slope == pytest.approx(float(s_slope), rel=1e-9, abs=1e-9)
        assert intercept == pytest.approx(float(s_intercept), rel=1e-9, abs=1e-6)
        assert r2 == pytest.approx(float(s_r) ** 2, rel=1e-9, abs=1e-12)


def test_linear_fit_handles_degenerate_input():
    """全点が同じ y でも落ちない (ss_tot=0 → R²=0)。"""
    from app.callbacks.interactive_calibration import _linear_fit

    slope, intercept, r2 = _linear_fit([1.0, 2.0, 3.0], [5.0, 5.0, 5.0])
    assert slope == pytest.approx(0.0, abs=1e-12)
    assert intercept == pytest.approx(5.0)
    assert r2 == 0.0


def test_calibration_works_without_scipy(tmp_path, monkeypatch):
    """★ scipy を import 不能にしても linear 回帰が通ること。

    本番イメージの状態を再現する。
    """
    import builtins

    real_import = builtins.__import__

    def no_scipy(name, *a, **kw):
        if name == "scipy" or name.startswith("scipy."):
            raise ModuleNotFoundError("No module named 'scipy'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_scipy)

    path, df = _make_matrix(tmp_path)
    features_list = list(df.columns)
    avg = window_avg_spectrum(path, features_list, REFS, 0.5)
    out = _calibrate_mz(features_list, avg, REFS,
                        search_window=0.5, min_peaks=2,
                        regression_mode="linear")
    assert out["calibrated"] is True, "scipy 不在で linear 回帰が失敗している"
    assert out["r_squared"] is not None


# ---------------------------------------------------------------------------
# R フォールバックの CSV ヘッダ行 / lite キャッシュキー (ver51.5)
# ---------------------------------------------------------------------------

def test_r_fallback_csv_tolerates_header_row(tmp_path, monkeypatch):
    """★ R の write.csv が書くヘッダ行 "expression" を取り除いて読むこと。

    extract_features.R:78 は col.names=FALSE を渡しているが、R の write.csv は
    **その指定を無視する**仕様なのでヘッダ行が書かれる。従来は header=None で
    読んでいたため、先頭に文字列が入った長さ N+1 の Series が返り、
    呼び出し元 (interactive_deg / interactive_loupe) で必ず落ちていた。
    """
    from app.services.seurat_bridge import SeuratBridge

    bridge = SeuratBridge()
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(bridge, "_get_cache_dir", lambda _p: cache)

    # R が実際に書く形 (ヘッダ付き)
    (cache / "feature_mz_100.csv").write_text(
        "expression\n1.5\n2.5\n3.5\n", encoding="utf-8")
    got = bridge.get_feature_expression("/rds/x.rds", "mz_100")
    assert list(got) == [1.5, 2.5, 3.5], f"ヘッダ行が残っている: {list(got)}"
    assert got.dtype.kind == "f"

    # ヘッダ無しでも従来どおり読めること (別経路/旧ファイルへの保険)
    (cache / "feature_mz_200.csv").write_text("4.0\n5.0\n", encoding="utf-8")
    got2 = bridge.get_feature_expression("/rds/x.rds", "mz_200")
    assert list(got2) == [4.0, 5.0]


def test_lite_cache_key_includes_rds_mtime(tmp_path):
    """★ 再解析で RDS が差し替わったらキャッシュキーが変わること。

    従来は (project, sub, method) だけだったので、同じパスへ上書きすると
    プロセス再起動までずっと古い plot_data を返し続けた。
    """
    import time as _t
    from app.callbacks.lite_view_callbacks import _lite_cache_key

    rds = tmp_path / "x.rds"
    rds.write_bytes(b"a" * 10)
    k1 = _lite_cache_key("p", "s", "Harmony", str(rds))

    _t.sleep(0.01)
    rds.write_bytes(b"b" * 20)          # 再解析で上書き
    k2 = _lite_cache_key("p", "s", "Harmony", str(rds))
    assert k1 != k2, "RDS を差し替えてもキャッシュキーが変わらない"

    # stat できなくても落ちない
    assert _lite_cache_key("p", "s", "Harmony", None)
    assert _lite_cache_key("p", "s", "Harmony", str(tmp_path / "nope.rds"))
