"""ROI 別サンプルの再解析でデータが丸ごと消える (A-5)。

本解析の DESI は「ROI をサンプルとして扱う」を選ぶと、1 つの .txt を
ROI ごとに切り分け、`<元名>_<ROI>` というサンプル名を付ける
(`260623_DESI-UMAP_Template_v16.R` の sub_samples 生成)。

ところが DESI 再解析は、元 .txt を探すときも RDS の行を選ぶときも
**`<元名>` のまま**で照合する:

    rows_sn <- md_keep[as.character(md_keep$sample) == as.character(sn), ]
    if (nrow(rows_sn) == 0) { message(".. skip (no remaining spots)"); next }

ROI 分割された RDS の sample は `sampleA_Brain` などなので 1 行も一致せず、
**そのサンプルは黙って落ちる**。全サンプルが ROI 別だと 1 件も書き出せず、
最後に「新規txtが1つも生成されませんでした」という、原因を指していない
エラーで終わる。

さらに ROI の設定そのものも再解析へ渡らない。Python は
`params["use_roi_as_sample"]` を注入するコードを持っているが、
受け手 (`USE_ROI_AS_SAMPLE` / `ROI_FILTER`) が再解析スクリプトに 1 つも無く、
`_replace_assign` は 0 件一致でも成功扱いで返るため**完全な空振り**だった。
（`tests/test_r_injection_completeness.py` の KNOWN_DEAD に記録されていた分）

直すと、これまで消えていた ROI 別サンプルが再解析に現れる。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import app.callbacks.analysis_callbacks as ac
from app.services.analysis_runner import generate_cluster_filter_config

SCRIPT = Path(__file__).resolve().parents[1] / "Script"
DESI_FILTER = SCRIPT / "DESI" / "DESI_RDS_ClusterFilter_ver3.R"
DESI_V16 = SCRIPT / "DESI" / "260623_DESI-UMAP_Template_v16.R"
TIMS_REUMAP = SCRIPT / "TIMS" / "260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R"
TIMS_V6 = SCRIPT / "TIMS" / "260623_DBSCAN_With_cluster_ver6_no-png_slim.R"


def _function_body(src: str, name: str) -> str:
    """`name <- function(...) { … }` の本体を波括弧の対応で切り出す。"""
    m = re.search(re.escape(name) + r"\s*<-\s*function\s*\([^)]*\)\s*\{", src)
    assert m, f"{name} の定義が見つからない"
    i = src.index("{", m.start())
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError(f"{name} の本体を閉じられない")


def _declares(src, var):
    return re.search(rf"^{re.escape(var)}\s*<-", src, re.M) is not None


# ---------------------------------------------------------------------------
# 前提: v16 側の受け手
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("var", ("USE_ROI_AS_SAMPLE", "ROI_FILTER"))
def test_the_desi_template_still_has_the_roi_receivers(var):
    assert _declares(DESI_V16.read_text(encoding="utf-8"), var), (
        f"v16 の受け手 {var} が失われている")


# ---------------------------------------------------------------------------
# ① 設定の伝播
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("var", ("V8_USE_ROI_AS_SAMPLE", "V8_ROI_FILTER"))
def test_the_desi_reanalysis_declares_the_roi_constants(var):
    """★ 受け手を新設すること（現状は無く、注入が空振りしている）。"""
    assert _declares(DESI_FILTER.read_text(encoding="utf-8"), var), (
        f"DESI 再解析に {var} が無い。Python は注入しているのに受け手が無く、"
        "_replace_assign は 0 件でも成功扱いで返るため黙って捨てられる")


@pytest.mark.parametrize("var", ("USE_ROI_AS_SAMPLE", "ROI_FILTER"))
def test_the_desi_reanalysis_forwards_the_roi_constants(var):
    """★ v16 コピーへ 0 件で停止する形で伝播すること。"""
    src = DESI_FILTER.read_text(encoding="utf-8")
    assert f'replace_assign_line(code, "{var}"' in src, (
        f"DESI 再解析が {var} を v16 コピーへ伝播していない。"
        "伝播しないと再解析だけ ROI 分割をやり直さない")


def _gen(tmp_path, extra, tims=False):
    params = {
        "template_path": str(TIMS_REUMAP if tims else DESI_FILTER),
        "rds_path": str(tmp_path / "in.rds"),
        "original_data_folder": str(tmp_path),
        "filter_mode": "keep",
        "target_clusters": [1],
        "sample_names": ["sampleA"],
        "main_analysis_script_path": str(TIMS_V6 if tims else DESI_V16),
    }
    params.update(extra)
    out = Path(generate_cluster_filter_config(params, str(tmp_path)))
    return out.read_text(encoding="utf-8").splitlines()


def _assign(lines, var):
    for ln in lines:
        if ln.strip().startswith(f"{var} <-"):
            return ln.strip()
    raise AssertionError(f"{var} の代入行が見つからない")


def test_the_config_writes_the_roi_settings(tmp_path):
    """★ 再解析の設定ファイルに ROI 設定が載ること。"""
    lines = _gen(tmp_path, {"use_roi_as_sample": True,
                            "roi_filter": ["Brain", "Heart"]})
    assert _assign(lines, "V8_USE_ROI_AS_SAMPLE") == "V8_USE_ROI_AS_SAMPLE <- TRUE"
    assert _assign(lines, "V8_ROI_FILTER") == 'V8_ROI_FILTER <- c("Brain", "Heart")'


def test_the_config_leaves_roi_alone_for_tims(tmp_path):
    """TIMS に ROI の概念は無い。受け手も無いので注入しないこと。"""
    lines = _gen(tmp_path, {"use_roi_as_sample": True}, tims=True)
    joined = "\n".join(lines)
    assert "V8_USE_ROI_AS_SAMPLE" not in joined
    assert "USE_ROI_AS_SAMPLE <- TRUE" not in joined


# ---------------------------------------------------------------------------
# ② 元ファイルへの逆引き
# ---------------------------------------------------------------------------

def test_the_reanalysis_has_a_reverse_lookup():
    """★ `<元名>_<ROI>` から元 .txt を引く関数があること。"""
    src = DESI_FILTER.read_text(encoding="utf-8")
    assert ".resolve_rds_samples" in src, (
        "RDS のサンプル名 (<元名>_<ROI>) を元 .txt 名へ逆引きする経路が無い")


def test_the_reanalysis_stops_instead_of_dropping_a_sample():
    """★ 逆引きに失敗したら理由を出して止めること（黙って消さない）。"""
    src = DESI_FILTER.read_text(encoding="utf-8")
    # 逆引きの結果が NULL のときに stop() していること
    i = src.find("is.null(.res)")
    assert i > 0, "逆引き失敗時の分岐が無い"
    branch = src[i:i + 1500]
    # 「該当なし」の分岐で next（＝黙って飛ばす）より前に stop() が来ること
    end = branch.index("next") if "next" in branch else len(branch)
    assert "stop(" in branch[:end], (
        "逆引きに失敗しても止めていない。"
        "現状のように next で飛ばすと、サンプルが丸ごと黙って消える")


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R が無い環境")
def test_the_reverse_lookup_really_resolves_roi_names(tmp_path):
    """★ 逆引きを R で実際に動かして確かめる。"""
    body = _function_body(DESI_FILTER.read_text(encoding="utf-8"),
                          ".resolve_rds_samples")
    harness = tmp_path / "h.R"
    harness.write_text(
        ".resolve_rds_samples <- function(sn, rds_samples, roi_filter = NULL) "
        + body + "\n"
        'rds <- c("sampleA_Brain","sampleA_Heart","sampleB_Brain");\n'
        'r1 <- .resolve_rds_samples("sampleA", rds);\n'
        'cat("A:", paste(r1$names, collapse=","), "|",'
        '    paste(r1$rois, collapse=","), "\\n");\n'
        'r2 <- .resolve_rds_samples("sampleA", c("sampleA","sampleB"));\n'
        'cat("B:", paste(r2$names, collapse=","), "|",'
        '    length(r2$rois), "\\n");\n'
        'r3 <- .resolve_rds_samples("sampleC", rds);\n'
        'cat("C:", is.null(r3), "\\n");\n'
        'r4 <- .resolve_rds_samples("sampleA", rds, roi_filter = c("Heart"));\n'
        'cat("D:", paste(r4$names, collapse=","), "\\n");\n'
        'r5 <- .resolve_rds_samples("s.A", c("sXA_Brain"));\n'
        'cat("E:", is.null(r5), "\\n");\n',
        encoding="utf-8")
    out = subprocess.run(["Rscript", str(harness)], capture_output=True,
                         text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    got = dict(ln.split(":", 1) for ln in out.stdout.strip().splitlines())
    assert got["A"].strip() == "sampleA_Brain,sampleA_Heart | Brain,Heart", (
        f"ROI 別サンプルを引けていない: {got['A']!r}")
    assert got["B"].strip() == "sampleA | 0", f"完全一致が壊れた: {got['B']!r}"
    assert got["C"].strip() == "TRUE", (
        f"該当なしを NULL で返していない（黙って消える）: {got['C']!r}")
    assert got["D"].strip() == "sampleA_Heart", f"ROI 絞り込みが効かない: {got['D']!r}"
    assert got["E"].strip() == "TRUE", (
        f"'.' が正規表現として効いて別サンプルに当たっている: {got['E']!r}")


def test_the_merge_map_covers_the_roi_sub_samples():
    """★ マージ用の対応表も ROI ごとに作ること。

    鍵は `sample|spot_index`。ROI モードでは元側が `sampleA_Brain`、
    再解析側が `sampleA_KEEP_Cl_8_Brain` になるので、ROI 単位の対応が要る。
    """
    src = DESI_FILTER.read_text(encoding="utf-8")
    seg = src[src.index(".merge_sample_map <- c()"):]
    assert re.search(r"\.merge_sample_map\[paste0\(", seg), (
        "ROI 別サンプルの対応表を作っていない。"
        "ROI モードの再解析ではマージが 1 点も一致しない")


# ---------------------------------------------------------------------------
# ③ 画面 → params
# ---------------------------------------------------------------------------

@pytest.fixture
def captured_params(monkeypatch, tmp_path):
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
    monkeypatch.setattr(dm, "list_msi_files", lambda folder: ["sampleA"])

    def _run(**over):
        seen.clear()
        kwargs = dict(_RUN_ARGS)
        kwargs.update({
            "n_clicks": 1, "downstream_clicks": 0,
            "desi_method": "desi_cluster_filter", "tims_method": None,
            "reanalysis_data_folder": str(tmp_path),
            "output_dir": str(tmp_path), "output_subfolder": "out",
            "target_clusters": "1", "filter_mode": "keep",
        })
        kwargs.update(over)
        ac.run_analysis(**kwargs)
        return dict(seen)

    return _run


def test_the_reanalysis_branch_carries_the_roi_settings(captured_params):
    """★ 再解析でも ROI 設定を params に載せること（現状は本解析分岐だけ）。"""
    params = captured_params(desi_use_roi_as_sample=True,
                             desi_roi_filter_list=["Brain"])
    assert params.get("use_roi_as_sample") is True, (
        "再解析に ROI 設定が載っていない（ROI 分割をやり直せない）")
    assert params.get("roi_filter") == ["Brain"]


def test_roi_off_is_carried_too(captured_params):
    """OFF も明示的に渡すこと（未指定＝テンプレ既定に化けさせない）。"""
    params = captured_params(desi_use_roi_as_sample=False)
    assert params.get("use_roi_as_sample") is False


def test_the_note_shows_the_roi_setting():
    """隠れた欄なので、何が使われるかを再解析パネルに出すこと。"""
    import app.callbacks.file_handlers as fh

    children, _s = fh.update_reanalysis_inherited_note(
        "desi_cluster_filter", None, "OFF", "log1p", 0, ["embedded"],
        True, ["Brain"])
    text = " ".join(str(c) for c in (children if isinstance(children, list)
                                     else [children]))
    assert "ROI" in text, f"ROI の扱いが画面に出ていない: {text}"
