"""ver52.5 ②: DESI で保存した化合物名が二度と読み込まれなかった。

■ 何が起きていたか

`deg_utils._CSV_NAMES` の優先順位:

    "*deg*markers*.csv"        ← 1 番目
    "*top*markers*.csv"
    "markers_annotated*.csv"   ← 3 番目
    "markers_mz_only*.csv"

- **DESI** テンプレは `analysis_deg_all_markers_harmony.csv` を出す
  (`Script/DESI/…v16.R:2642`、出力先は `od_harmony = <od>/Harmony`) が、
  **annotation 列を持たない**
- アプリの再アノテーションは **同じフォルダ** へ `markers_annotated.csv` を書く
- 優先順位により前者が勝つため、保存した注釈は二度と読まれない

利用者から見ると「再アノテーション完了: N 件更新」と出て画面にも化合物名が
出るのに、**開き直すと消えている**。TIMS は `markers_annotated.csv` が
`markers_mz_only.csv` より先なので正常 —— **DESI だけの問題**だった。

■ なぜ優先順位を入れ替えないのか

入れ替えると、解析を再実行しても古い `markers_annotated.csv` が優先され続け、
**新しい解析に古い DEG 表が出る**。いま直そうとしている型と同じ欠陥を、
自分の手で作ることになる。

代わりに annotation 列だけを重ねる（本表は常に最新のまま）。
解析を再実行して feature が入れ替われば、古い注釈は join に一致せず
**自然に外れる**ので、鮮度のメタデータが要らない。
"""

import pandas as pd
import pytest

import app.utils.deg_utils as DU
from app.utils.deg_utils import load_deg_results

# ★ `read_annotation_overlay` は ver52.5 で足した関数。`from ... import` で
#   書くと修正前のコードでは収集ごと失敗し、各テストの可否が見えなくなる。


def _write(path, genes, ann=None, fc=1.5):
    d = {"gene": list(genes),
         "cluster": ["0"] * len(genes),
         "avg_log2FC": [fc] * len(genes),
         "p_val_adj": [1e-10] * len(genes)}
    if ann is not None:
        d["annotation"] = list(ann)
    pd.DataFrame(d).to_csv(path, index=False)


@pytest.fixture
def desi(tmp_path):
    """DESI 構成: R が出す注釈なしの本表 + アプリが保存した注釈。"""
    (tmp_path / "Harmony").mkdir()
    _write(tmp_path / "Harmony" / "analysis_deg_all_markers_harmony.csv",
           ["mz_100.0", "mz_200.0"])
    _write(tmp_path / "Harmony" / "markers_annotated.csv",
           ["mz_100.0", "mz_200.0"], ann=["ATP (M+H)", "GTP (M+H)"])
    return tmp_path


@pytest.fixture
def tims(tmp_path):
    """TIMS 構成: R が注釈付きの本表と、注釈を落とした版を出す。"""
    (tmp_path / "Harmony").mkdir()
    _write(tmp_path / "Harmony" / "markers_annotated.csv",
           ["mz_100.0"], ann=["ATP (M+H)"])
    _write(tmp_path / "Harmony" / "markers_mz_only.csv", ["mz_100.0"])
    return tmp_path


class TestSavedAnnotationsAreVisibleAgain:

    def test_desi_gets_the_saved_annotation(self, desi):
        """★ 本丸。修正前はここで '<列なし>' になっていた。"""
        recs = load_deg_results(desi, "Harmony")
        assert recs is not None
        assert [r.get("annotation") for r in recs] == ["ATP (M+H)", "GTP (M+H)"], (
            f"保存した注釈が読み込まれていない: {recs}")

    def test_tims_is_unchanged(self, tims):
        """★ 過剰修正の番人: 従来どおり動く経路を壊していないこと。"""
        recs = load_deg_results(tims, "Harmony")
        assert [r.get("annotation") for r in recs] == ["ATP (M+H)"]

    def test_lowercase_method_dir_is_also_found(self, tmp_path):
        """書き側は `method_dir.lower()` に落ちることがある。読み側も追うこと。

        片方だけ見ると、大小文字が違う環境で注釈が外れる。
        """
        (tmp_path / "harmony").mkdir()
        _write(tmp_path / "harmony" / "analysis_deg_all_markers.csv", ["mz_100.0"])
        _write(tmp_path / "harmony" / "markers_annotated.csv",
               ["mz_100.0"], ann=["ATP (M+H)"])
        assert DU.read_annotation_overlay(tmp_path, "Harmony") == {
            "mz_100.0": "ATP (M+H)"}


class TestTheMainTableIsAlwaysTheFreshOne:
    """★ 優先順位を変えていないことの表明。

    ここが崩れると「新しい解析に古い DEG 表が出る」——
    いま直している型と同じ欠陥になる。
    """

    def test_the_table_comes_from_the_analysis_output(self, desi):
        """本表の行は R の出力から来ること（注釈ファイルからではない）。"""
        _write(desi / "Harmony" / "analysis_deg_all_markers_harmony.csv",
               ["mz_100.0", "mz_200.0"], fc=9.99)
        recs = load_deg_results(desi, "Harmony")
        assert all(r["avg_log2FC"] == 9.99 for r in recs), (
            f"本表が注釈ファイル側から来ている: {recs}")

    def test_stale_annotations_fall_off_after_reanalysis(self, desi):
        """★ 解析を再実行して feature が入れ替わったら、古い注釈は付かない。"""
        _write(desi / "Harmony" / "analysis_deg_all_markers_harmony.csv",
               ["mz_999.0"])                      # 新しい解析（別の feature）
        recs = load_deg_results(desi, "Harmony")
        assert [r["gene"] for r in recs] == ["mz_999.0"]
        assert not recs[0].get("annotation"), (
            f"消えた feature の古い注釈が付いている: {recs[0]}")

    def test_partial_overlap_only_annotates_the_matching_gene(self, desi):
        _write(desi / "Harmony" / "analysis_deg_all_markers_harmony.csv",
               ["mz_100.0", "mz_999.0"])
        recs = load_deg_results(desi, "Harmony")
        got = {r["gene"]: r.get("annotation") or "" for r in recs}
        assert got["mz_100.0"] == "ATP (M+H)"
        assert got["mz_999.0"] == ""


class TestOverlayIsSafeToFail:
    """重ね合わせは付加機能。失敗しても本表は返ること。"""

    def test_missing_overlay_file_is_fine(self, tmp_path):
        (tmp_path / "Harmony").mkdir()
        _write(tmp_path / "Harmony" / "analysis_deg_all_markers.csv", ["mz_100.0"])
        recs = load_deg_results(tmp_path, "Harmony")
        assert recs is not None and recs[0]["gene"] == "mz_100.0"

    def test_broken_overlay_file_does_not_lose_the_table(self, desi):
        (desi / "Harmony" / "markers_annotated.csv").write_text(
            "これは CSV ではない\n\x00\x01", encoding="utf-8")
        recs = load_deg_results(desi, "Harmony")
        assert recs is not None, "注釈ファイルが壊れているだけで本表まで失っている"
        assert [r["gene"] for r in recs] == ["mz_100.0", "mz_200.0"]

    def test_meaningless_annotations_are_ignored(self, desi):
        """数値だけ・gene と同一の注釈は重ねない（既存の判定を再利用）。"""
        _write(desi / "Harmony" / "markers_annotated.csv",
               ["mz_100.0", "mz_200.0"], ann=["240.984", "mz_200.0"])
        assert DU.read_annotation_overlay(desi, "Harmony") == {}


class TestOverlayRunsOnEverySuccessPath:
    """★ 成功経路の出口が増えても注釈が付くこと。

    `load_deg_results` の成功経路は全部 `_cache_and_return` を通る。
    そこ 1 箇所で重ねているので、出口が増えても自動的に効く——
    その構造が保たれていることを固定する。
    """

    def test_the_overlay_is_applied_in_the_single_funnel(self):
        import ast
        import inspect
        src = inspect.getsource(load_deg_results)
        tree = ast.parse(src.lstrip())
        fn = tree.body[0]
        funnel = next(n for n in ast.walk(fn)
                      if isinstance(n, ast.FunctionDef)
                      and n.name == "_cache_and_return")
        calls = {n.func.id for n in ast.walk(funnel)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "_apply_annotation_overlay" in calls, (
            "重ね合わせが `_cache_and_return` の外にある。"
            "出口ごとに書くと、あとで足した出口だけ注釈が付かなくなる")

    def test_the_deg_index_fast_path_also_annotates(self, desi):
        """2 回目以降は deg_index.json の高速経路を通る。そこでも注釈が付くこと。"""
        first = load_deg_results(desi, "Harmony")
        assert (desi / "deg_index.json").is_file(), "高速経路の索引が作られていない"
        again = load_deg_results(desi, "Harmony")
        assert [r.get("annotation") for r in again] == \
               [r.get("annotation") for r in first] == ["ATP (M+H)", "GTP (M+H)"]
