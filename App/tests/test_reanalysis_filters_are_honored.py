"""再解析でも「切片の選択」と「サンプルの選択」が効くこと (S2・2 件)。

--------------------------------------------------------------------------
① 切片 (Annotation) フィルタが R に一度も届かない
--------------------------------------------------------------------------
再解析画面の「Annotation（切片）選択」でチェックを外しても、その切片の
スポットが再解析にそのまま含まれる。画面は正常に完了し、警告も出ない。

Python 側は `params["annotation_filter"]` を**組み立てている**
(`analysis_callbacks.run_analysis` の再解析ブロック)。しかし注入を行うのは
本解析用の `generate_v8_config` だけで、**再解析用の
`generate_cluster_filter_config` には注入コードが無い**。
受け手の `V13_ANNOTATION_FILTER` も ver18 に存在しない。
組み立てた値がどこにも渡らない、完全な空振りである。

--------------------------------------------------------------------------
② サンプル選択のチェックが読まれない
--------------------------------------------------------------------------
再解析ブロックはフォルダを丸ごと列挙する:

    sample_names = list_tims_files(reanalysis_data_folder)
    params["original_input_paths"] = build_tims_input_paths(src_folder)

本解析側には選択で絞り込む実装が既にある（`selected_samples` で
`sample_names` と `input_paths` を絞る）。再解析だけが素通りしている。

--------------------------------------------------------------------------
どちらも「外したはずのデータが解析に入る」型で、直すと対象が減る
（＝過去の再解析より結果が変わる）。
"""

from pathlib import Path

import pytest

import app.callbacks.analysis_callbacks as ac
from app.services.analysis_runner import generate_cluster_filter_config

SCRIPT = Path(__file__).resolve().parents[1] / "Script"
TIMS_REUMAP = SCRIPT / "TIMS" / "260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R"
TIMS_V6 = SCRIPT / "TIMS" / "260623_DBSCAN_With_cluster_ver6_no-png_slim.R"


# ---------------------------------------------------------------------------
# ① 切片フィルタ
# ---------------------------------------------------------------------------

def test_ver6_still_has_the_filter_variable():
    """注入先（本体テンプレ側の受け手）が存在すること。"""
    src = TIMS_V6.read_text(encoding="utf-8")
    assert "ANNOTATION_FILTER <- NULL" in src, (
        "ver6 の ANNOTATION_FILTER が無い。注入先が失われている")


def test_the_reanalysis_script_declares_the_filter_constant():
    """★ ver18 が V13_ANNOTATION_FILTER を持つこと（現状は無い）。"""
    src = TIMS_REUMAP.read_text(encoding="utf-8")
    assert "V13_ANNOTATION_FILTER" in src, (
        "ver18 に V13_ANNOTATION_FILTER が無い。"
        "V13_NORM_MODE などと同じ 4 段の注入パターンで足せる")


def test_the_reanalysis_script_forwards_the_filter_to_the_template():
    """★ ver18 が受け取った値を ANNOTATION_FILTER へ流すこと。"""
    src = TIMS_REUMAP.read_text(encoding="utf-8")
    assert 'replace_assign_line(code, "ANNOTATION_FILTER"' in src, (
        "ver18 が ANNOTATION_FILTER を本体テンプレのコピーへ書き込んでいない。"
        "定数を足しただけでは届かない")


def test_the_config_generator_injects_the_filter(tmp_path):
    """★ 本丸: 再解析の設定生成が V13_ANNOTATION_FILTER を書き出すこと。"""
    params = {
        "template_path": str(TIMS_REUMAP),
        "rds_path": "",
        "original_data_folder": str(tmp_path),
        "filter_mode": "keep",
        "target_clusters": [8],
        "sample_names": ["sampleA"],
        "main_analysis_script_path": str(TIMS_V6),
        "annotation_filter": ["slice_1", "slice_3"],
    }
    out = Path(generate_cluster_filter_config(params, str(tmp_path)))
    text = out.read_text(encoding="utf-8")
    assert "V13_ANNOTATION_FILTER" in text, (
        "再解析の設定に切片フィルタが書き出されていない。"
        "Python 側は params に値を持っているのに、注入コードが無いため"
        "**どこにも渡らない**")
    assert 'c("slice_1", "slice_3")' in text, (
        f"選んだ切片が値として書かれていない:\n"
        f"{[l for l in text.splitlines() if 'ANNOTATION_FILTER' in l]}")


def test_no_filter_leaves_the_default_untouched(tmp_path):
    """★ 直しすぎの検出: 未選択なら既定 (NULL) のままにすること。"""
    params = {
        "template_path": str(TIMS_REUMAP),
        "rds_path": "",
        "original_data_folder": str(tmp_path),
        "filter_mode": "keep",
        "target_clusters": [8],
        "sample_names": ["sampleA"],
        "main_analysis_script_path": str(TIMS_V6),
    }
    out = Path(generate_cluster_filter_config(params, str(tmp_path)))
    for ln in out.read_text(encoding="utf-8").splitlines():
        if ln.strip().startswith("V13_ANNOTATION_FILTER"):
            assert "NULL" in ln, (
                f"未選択なのに切片フィルタが入っている: {ln.strip()}")


# ---------------------------------------------------------------------------
# ② サンプル選択
# ---------------------------------------------------------------------------

@pytest.fixture
def captured_params(monkeypatch, tmp_path):
    """再解析を 1 回走らせ、R へ渡される params を捕まえる。"""
    from tests.test_downstream_overwrite_is_confirmed import _RUN_ARGS

    seen = {}

    class _Ctx:
        triggered_id = "run_analysis"

    monkeypatch.setattr(ac, "ctx", _Ctx)
    monkeypatch.setattr(ac, "_collect_preflight_errors", lambda *a, **kw: ([], []))
    monkeypatch.setattr(ac, "_output_has_existing_results", lambda t: False)
    monkeypatch.setattr(ac, "save_last_settings", lambda d: None)
    monkeypatch.setattr(ac, "save_sub_project_settings", lambda *a, **kw: None,
                        raising=False)

    def _fake_config(params, outdir):
        seen.update(params)
        p = Path(outdir) / "config.R"
        p.write_text("# dummy", encoding="utf-8")
        return str(p)

    monkeypatch.setattr(ac, "generate_cluster_filter_config", _fake_config)
    monkeypatch.setattr(ac, "start_analysis_process",
                        lambda *a, **kw: {"success": False, "message": "テスト"})

    import app.services.data_manager as dm
    monkeypatch.setattr(dm, "list_tims_files",
                        lambda folder: ["sampleA", "sampleB", "sampleC"])
    monkeypatch.setattr(
        dm, "build_tims_input_paths",
        lambda folder: [f"{folder}/sampleA.parquet",
                        f"{folder}/sampleB.parquet",
                        f"{folder}/sampleC.parquet"])

    def _run(selected):
        seen.clear()
        kwargs = dict(_RUN_ARGS)
        kwargs.update({
            "n_clicks": 1, "downstream_clicks": 0,
            "tims_method": "tims_cluster_filter", "desi_method": None,
            "reanalysis_data_folder": str(tmp_path),
            "output_dir": str(tmp_path), "output_subfolder": "out",
            "target_clusters": "8", "filter_mode": "keep",
            "selected_samples": selected,
        })
        ac.run_analysis(**kwargs)
        return dict(seen)

    return _run


def test_all_samples_are_used_when_nothing_is_selected(captured_params):
    """前提: 未選択ならフォルダ全件が対象（従来どおり）。"""
    params = captured_params([])
    assert params.get("sample_names") == ["sampleA", "sampleB", "sampleC"]


def test_the_sample_checkboxes_restrict_the_reanalysis(captured_params):
    """★ 本丸: チェックを外したサンプルが再解析から外れること。"""
    params = captured_params(["sampleA", "sampleC"])
    assert params.get("sample_names") == ["sampleA", "sampleC"], (
        f"チェックを外した sampleB が残っている: {params.get('sample_names')}")


def test_the_input_paths_are_restricted_too(captured_params):
    """★ 入力ファイルの一覧も同じ選択で絞ること（片方だけでは食い違う）。"""
    params = captured_params(["sampleA", "sampleC"])
    stems = [Path(p).stem for p in params.get("original_input_paths", [])]
    assert stems == ["sampleA", "sampleC"], (
        f"入力ファイル側が絞られていない: {stems}。"
        "サンプル名だけ絞ると、名前と実ファイルが食い違う")
