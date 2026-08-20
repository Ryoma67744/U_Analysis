"""再解析に「画面で指定した条件」が一つも届かない (A-6 / A-7 / A-10)。

再解析スクリプトは本体テンプレートを **コピーして書き換えてから** 呼ぶ。
書き換えの受け手（`V13_*` / `V8_*`）が無い設定は、画面でいくら指定しても
テンプレートの既定値のまま計算される。**警告もログも出ない。**

--------------------------------------------------------------------------
A-6 DESI 再解析に正規化ポリシーが届かない
--------------------------------------------------------------------------
`params["input_normalized"]` / `params["norm_mode"]` を組み立てるのは
`if analysis_type == "tims_cluster_filter":` の中だけで、DESI 再解析には
そもそも値が載らない。載せても `DESI_RDS_ClusterFilter_ver3.R` に
`V8_INPUT_NORMALIZED` / `V8_NORM_MODE` が無いので受け取れない。
v16 の既定は `INPUT_NORMALIZED <- FALSE`（＝ LogNormalize する）なので、
**正規化済み入力に対して二重に正規化がかかる**。

--------------------------------------------------------------------------
A-7 再解析に UMAP 条件が届かない
--------------------------------------------------------------------------
PreFlight パネル（`umap_n_neighbors_input` ほか）は再解析中も画面に出ている。
利用者は推奨値を入れて実行できるが、両方の再解析スクリプトに受け手が無いため
**常にテンプレート既定で計算される**。

--------------------------------------------------------------------------
A-10 TIMS 再解析に m/z 条件・サンプル別キャリブレーションが届かない
--------------------------------------------------------------------------
`MZ_ALIGN_PPM` / `USE_EMBEDDED_COMPOUND_NAMES` / `CALIBRATION_BY_SAMPLE` の
受け手が無い。とくにサンプル別キャリブレーションは、全体共通の回帰式が
未設定だと共通係数が `[0.0]`（＝補正量ゼロ）に潰れる仕様のため、
画面に「✅ 前回の解析から回帰式を検出」と出ているのに **実際には
一切補正されない**。表示と実処理が食い違っている。
"""

import inspect
import re
from pathlib import Path

import pytest

import app.callbacks.analysis_callbacks as ac
from app.services.analysis_runner import generate_cluster_filter_config

SCRIPT = Path(__file__).resolve().parents[1] / "Script"
TIMS_REUMAP = SCRIPT / "TIMS" / "260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R"
TIMS_V6 = SCRIPT / "TIMS" / "260623_DBSCAN_With_cluster_ver6_no-png_slim.R"
DESI_FILTER = SCRIPT / "DESI" / "DESI_RDS_ClusterFilter_ver3.R"
DESI_V16 = SCRIPT / "DESI" / "260623_DESI-UMAP_Template_v16.R"

UMAP_VARS = ("UMAP_N_NEIGHBORS", "UMAP_MIN_DIST", "UMAP_METRIC", "UMAP_DIMS_N")


def _declares(src, var):
    """`VAR <- ...` のトップレベル宣言があるか（桁揃えの空白を許容）。"""
    return re.search(rf"^{re.escape(var)}\s*<-", src, re.M) is not None


# ---------------------------------------------------------------------------
# 前提: 本体テンプレ側の受け手が存在すること
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("var", ("INPUT_NORMALIZED", "NORM_MODE") + UMAP_VARS)
def test_the_desi_template_still_has_the_receivers(var):
    src = DESI_V16.read_text(encoding="utf-8")
    assert _declares(src, var), f"v16 の受け手 {var} が失われている"


@pytest.mark.parametrize("var", UMAP_VARS + (
    "MZ_ALIGN_PPM", "USE_EMBEDDED_COMPOUND_NAMES", "CALIBRATION_BY_SAMPLE"))
def test_the_tims_template_still_has_the_receivers(var):
    src = TIMS_V6.read_text(encoding="utf-8")
    assert _declares(src, var), f"ver6 の受け手 {var} が失われている"


# ---------------------------------------------------------------------------
# A-6: DESI 再解析の正規化
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("var", ("V8_INPUT_NORMALIZED", "V8_NORM_MODE"))
def test_the_desi_reanalysis_declares_the_normalize_constants(var):
    """★ 受け手の宣言があること（現状は無い）。"""
    src = DESI_FILTER.read_text(encoding="utf-8")
    assert _declares(src, var), (
        f"DESI 再解析に {var} が無い。V8_DEG_* と同じ形で足せる")


@pytest.mark.parametrize("var", ("INPUT_NORMALIZED", "NORM_MODE"))
def test_the_desi_reanalysis_forwards_the_normalize_constants(var):
    """★ v16 コピーへ **0 件で停止する形** で伝播すること。

    `replace_assign_line` は `.stopif` で 0 件なら止まる。Python 側の
    `_replace_assign` と違い、受け手を失っても無言では通らない。
    """
    src = DESI_FILTER.read_text(encoding="utf-8")
    assert f'replace_assign_line(code, "{var}"' in src, (
        f"DESI 再解析が {var} を v16 コピーへ伝播していない")


# ---------------------------------------------------------------------------
# A-7: UMAP 条件（両方）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("var", UMAP_VARS)
def test_the_tims_reanalysis_declares_the_umap_constants(var):
    src = TIMS_REUMAP.read_text(encoding="utf-8")
    assert _declares(src, f"V13_{var}"), f"TIMS 再解析に V13_{var} が無い"


@pytest.mark.parametrize("var", UMAP_VARS)
def test_the_desi_reanalysis_declares_the_umap_constants(var):
    src = DESI_FILTER.read_text(encoding="utf-8")
    assert _declares(src, f"V8_{var}"), f"DESI 再解析に V8_{var} が無い"


@pytest.mark.parametrize("var", UMAP_VARS)
def test_both_reanalysis_scripts_forward_the_umap_constants(var):
    for path in (TIMS_REUMAP, DESI_FILTER):
        src = path.read_text(encoding="utf-8")
        assert f'replace_assign_line(code, "{var}"' in src, (
            f"{path.name} が {var} を本体コピーへ伝播していない")


# ---------------------------------------------------------------------------
# A-10: TIMS 再解析の残り 3 項目
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("var", ("V13_MZ_ALIGN_PPM",
                                 "V13_USE_EMBEDDED_COMPOUND_NAMES",
                                 "V13_CALIBRATION_BY_SAMPLE"))
def test_the_tims_reanalysis_declares_the_remaining_constants(var):
    src = TIMS_REUMAP.read_text(encoding="utf-8")
    assert _declares(src, var), f"TIMS 再解析に {var} が無い"


@pytest.mark.parametrize("var", ("MZ_ALIGN_PPM", "USE_EMBEDDED_COMPOUND_NAMES",
                                 "CALIBRATION_BY_SAMPLE"))
def test_the_tims_reanalysis_forwards_the_remaining_constants(var):
    src = TIMS_REUMAP.read_text(encoding="utf-8")
    assert f'replace_assign_line(code, "{var}"' in src, (
        f"TIMS 再解析が {var} を ver6 コピーへ伝播していない")


# ---------------------------------------------------------------------------
# Python: 設定生成が正しい接頭辞へ書くこと
# ---------------------------------------------------------------------------

def _gen(tmp_path, extra, tims=True):
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


def test_the_desi_config_uses_the_v8_prefix(tmp_path):
    """★ DESI 再解析は V8_ に書くこと（V13_ 固定だと空振りする）。"""
    lines = _gen(tmp_path, {"input_normalized": True, "norm_mode": "sqrt"},
                 tims=False)
    assert _assign(lines, "V8_INPUT_NORMALIZED") == "V8_INPUT_NORMALIZED <- TRUE"
    assert _assign(lines, "V8_NORM_MODE") == 'V8_NORM_MODE <- "sqrt"'


def test_the_tims_config_keeps_the_v13_prefix(tmp_path):
    """接頭辞の振り分けで TIMS 側を壊さないこと。"""
    lines = _gen(tmp_path, {"input_normalized": False, "norm_mode": "log1p"})
    assert _assign(lines, "V13_INPUT_NORMALIZED") == "V13_INPUT_NORMALIZED <- FALSE"
    assert _assign(lines, "V13_NORM_MODE") == 'V13_NORM_MODE <- "log1p"'


@pytest.mark.parametrize("tims,pre", [(True, "V13_"), (False, "V8_")])
def test_the_config_writes_the_umap_values(tmp_path, tims, pre):
    """★ 画面の UMAP 条件が再解析の設定に載ること。"""
    lines = _gen(tmp_path, {
        "umap_n_neighbors": 15, "umap_min_dist": 0.05,
        "umap_metric": "euclidean", "umap_dims_n": 25,
    }, tims=tims)
    assert _assign(lines, f"{pre}UMAP_N_NEIGHBORS") == f"{pre}UMAP_N_NEIGHBORS <- 15L"
    assert _assign(lines, f"{pre}UMAP_DIMS_N") == f"{pre}UMAP_DIMS_N <- 25L"
    assert "0.05" in _assign(lines, f"{pre}UMAP_MIN_DIST")
    assert _assign(lines, f"{pre}UMAP_METRIC") == f'{pre}UMAP_METRIC <- "euclidean"'


@pytest.mark.parametrize("tims,pre", [(True, "V13_"), (False, "V8_")])
def test_the_config_leaves_umap_alone_when_nothing_is_given(tmp_path, tims, pre):
    """未指定なら書き換えない＝従来どおり本体既定で走ること。"""
    lines = _gen(tmp_path, {}, tims=tims)
    assert _assign(lines, f"{pre}UMAP_DIMS_N").endswith("NA"), (
        "未指定なのに UMAP 条件が入っている（既定の意味が変わる）")


def test_the_config_writes_the_per_sample_calibration(tmp_path):
    """★ サンプル別の回帰式が再解析へ渡ること。"""
    lines = _gen(tmp_path, {
        "calibration_enable": True,
        "calibration_coefficients": [0.0],
        "calibration_by_sample": {"sampleA": [0.0, 1.0, 2.0]},
    })
    joined = "\n".join(lines)
    assert "V13_CALIBRATION_BY_SAMPLE <- list(" in joined, (
        "サンプル別キャリブレーションが再解析に渡っていない")
    assert '"sampleA" = c(0.0, 1.0, 2.0)' in joined


def test_the_config_writes_ppm_and_the_name_source(tmp_path):
    lines = _gen(tmp_path, {"mz_align_ppm": 5.0,
                            "use_embedded_annotation": True})
    assert _assign(lines, "V13_MZ_ALIGN_PPM") == "V13_MZ_ALIGN_PPM <- 5.0"
    assert (_assign(lines, "V13_USE_EMBEDDED_COMPOUND_NAMES")
            == "V13_USE_EMBEDDED_COMPOUND_NAMES <- TRUE")


# ---------------------------------------------------------------------------
# 画面 → params
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
    monkeypatch.setattr(dm, "list_tims_files", lambda folder: ["sampleA"])
    monkeypatch.setattr(dm, "build_tims_input_paths",
                        lambda folder: [f"{folder}/sampleA.parquet"])
    monkeypatch.setattr(dm, "list_msi_files", lambda folder: ["sampleA"])

    def _run(**over):
        seen.clear()
        kwargs = dict(_RUN_ARGS)
        kwargs.update({
            "n_clicks": 1, "downstream_clicks": 0,
            "reanalysis_data_folder": str(tmp_path),
            "output_dir": str(tmp_path), "output_subfolder": "out",
            "target_clusters": "1", "filter_mode": "keep",
        })
        kwargs.update(over)
        ac.run_analysis(**kwargs)
        return dict(seen)

    return _run


def test_the_desi_reanalysis_carries_the_normalize_choice(captured_params):
    """★ A-6: DESI 再解析にも画面の正規化設定が載ること。"""
    params = captured_params(
        desi_method="desi_cluster_filter", tims_method=None,
        normalize_input="OFF", norm_mode="sqrt")
    assert params.get("input_normalized") is True, (
        "DESI 再解析に正規化ポリシーが載っていない（二重正規化になる）")
    assert params.get("norm_mode") == "sqrt"


def test_both_reanalyses_carry_the_umap_settings(captured_params):
    """★ A-7: 画面の UMAP 条件が再解析の params に載ること。"""
    for desi, tims in (("desi_cluster_filter", None),
                       (None, "tims_cluster_filter")):
        params = captured_params(
            desi_method=desi, tims_method=tims,
            umap_n_neighbors_input=15, umap_min_dist_input=0.05,
            umap_metric_input="euclidean", umap_dims_input=25)
        assert params.get("umap_n_neighbors") == 15, f"{desi or tims}: n.neighbors"
        assert params.get("umap_min_dist") == 0.05, f"{desi or tims}: min.dist"
        assert params.get("umap_metric") == "euclidean", f"{desi or tims}: metric"
        assert params.get("umap_dims_n") == 25, f"{desi or tims}: dims"


def test_the_tims_reanalysis_carries_ppm_and_the_name_source(captured_params):
    """★ A-10: m/z アライメントと化合物名の由来が載ること。"""
    params = captured_params(
        desi_method=None, tims_method="tims_cluster_filter",
        mz_align_ppm=5, use_annotation_check=["embedded"])
    assert params.get("mz_align_ppm") == 5.0
    assert params.get("use_embedded_annotation") is True


def test_the_tims_reanalysis_carries_the_per_sample_calibration(captured_params):
    """★ A-10: 前回のサンプル別回帰式が再解析へ渡ること。"""
    params = captured_params(
        desi_method=None, tims_method="tims_cluster_filter",
        reanalysis_cal_use_previous=True,
        reanalysis_cal_data={"coefficients": [0.0],
                             "by_sample": {"sampleA": [0.0, 1.0]}})
    assert params.get("calibration_by_sample") == {"sampleA": [0.0, 1.0]}, (
        "サンプル別の回帰式が落ちている（共通係数 [0.0] ＝ 無補正のまま走る）")


# ---------------------------------------------------------------------------
# 画面表示が実処理と一致すること
# ---------------------------------------------------------------------------

def test_the_calibration_loader_picks_up_the_per_sample_table():
    """★ A-10: ローダがサンプル別の回帰式を拾うこと。"""
    src = inspect.getsource(ac.load_calibration_from_first_analysis)
    assert "calibration_by_sample" in src, (
        "再解析ローダがサンプル別の回帰式を読んでいない。"
        "analysis_params.json には保存されているのに捨てている")


def test_the_calibration_note_admits_when_nothing_is_corrected():
    """★ A-10: 共通の回帰式が無いとき、その旨を画面に出すこと。

    共通係数 `[0.0]` は「どの m/z でも補正量 0」＝無補正。
    それを伏せたまま「✅ 回帰式を検出」とだけ出すと、実態と食い違う。
    """
    src = inspect.getsource(ac.load_calibration_from_first_analysis)
    assert "補正しません" in src, (
        "個別設定の無いサンプルが無補正になることを画面に出していない")


def test_the_reanalysis_note_shows_what_will_be_used():
    """★ A-6/A-7/A-10: 隠れた欄の値が黙って使われない担保。

    正規化・UMAP・m/z の各欄は `umap_settings_panel` の中にあり、
    再解析中は画面から見えない。見えない値で計算するなら、
    **何が使われるのかを再解析パネルに書く**。
    """
    import app.callbacks.file_handlers as fh

    fn = getattr(fh, "update_reanalysis_inherited_note", None)
    assert fn is not None, "再解析パネルに設定表示のコールバックが無い"

    children, style = fn("desi_cluster_filter", None, "OFF", "sqrt", 5,
                         ["embedded"])
    text = " ".join(str(c) for c in (children if isinstance(children, list)
                                     else [children]))
    assert style.get("display") != "none", "再解析なのに表示されていない"
    assert "sqrt" in text and "OFF" in text, f"正規化の実値が出ていない: {text}"
    assert "cosine" in text or "n.neighbors" in text, f"UMAP 条件が出ていない: {text}"

    # 本解析中は出さない（画面に二重の主張を出さない）
    _c, style2 = fn("desi_v8", None, "OFF", "sqrt", 5, ["embedded"])
    assert style2.get("display") == "none", "本解析中にも再解析用の注記が出ている"


def test_the_note_reports_the_tims_only_settings():
    """TIMS 再解析では m/z と化合物名の由来も出すこと。"""
    import app.callbacks.file_handlers as fh

    children, _s = fh.update_reanalysis_inherited_note(
        None, "tims_cluster_filter", "OFF", "log1p", 5, ["embedded"])
    text = " ".join(str(c) for c in (children if isinstance(children, list)
                                     else [children]))
    assert "ppm" in text, f"m/z アライメントが出ていない: {text}"
    assert "化合物名" in text, f"化合物名の由来が出ていない: {text}"
