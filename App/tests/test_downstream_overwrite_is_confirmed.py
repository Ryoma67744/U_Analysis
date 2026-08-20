"""④「reduction を再利用」も上書き前に確認すること (C03-3)。

--------------------------------------------------------------------------
症状: ④ だけ確認なしで前回の結果を消す
--------------------------------------------------------------------------
「解析実行」と「reduction のみ作成」は、出力先に既存結果があると
『既に結果があります。上書きしますか』という確認画面を出す。

ところが ④「reduction を再利用して UMAP 以降だけ」だけは、この確認画面の
発火元にも実行本体の上書きゲートにも入っていなかった。④ の出力フォルダ名は
UMAP のハイパーパラメータから自動生成される（例: `umap_nn15_md0p3_dim20`）ので、
**同じ条件でもう一度押すと前回と同じ名前になり、前回の ④ の結果が黙って
上書きされて消える**。しかもトーストは『解析を開始しました』と普通の成功表示。

このファイル自身のコードにこう書かれている:

    「確認なしで前の解析結果を上書きする」のとでは、後者の損害が桁違いに大きい

--------------------------------------------------------------------------
併せて直す: 確認後に ④ のモードが失われる
--------------------------------------------------------------------------
確認画面で「実行する」を押したあとの本実行は、押されたボタンの種類を
`overwrite_pending_mode` から復元する。ところが ④ は「警告対象外」という前提で
`downstream_mode = False` に潰されていた。④ を確認対象にする以上、
ここを直さないと **確認を経ると ④ ではなく通常解析が走ってしまう**。

--------------------------------------------------------------------------
出力先の決め方を 1 か所に集約する
--------------------------------------------------------------------------
確認画面と実行本体が「どのフォルダに書くか」をそれぞれ計算していると、
片方だけ直したときに **確認した先と実際に書く先が食い違う**。
④ はサフィックス自動命名があるぶん、その危険が大きい。
`_resolve_full_output_dir()` に集約し、両方がそれを呼ぶ。
"""

import inspect

import pytest
from dash import no_update

import app.callbacks.analysis_callbacks as ac


# ---------------------------------------------------------------------------
# 出力先の決め方が 1 か所に集約されていること
# ---------------------------------------------------------------------------

def test_output_dir_is_resolved_in_one_place():
    """★ 確認画面と実行本体が同じ関数で出力先を決めること。"""
    assert hasattr(ac, "_resolve_full_output_dir")
    for fn in (ac.open_overwrite_modal, ac.run_analysis):
        assert "_resolve_full_output_dir" in inspect.getsource(fn), (
            f"{fn.__name__} が出力先を自前で組み立てている"
            "（確認した先と実際に書く先が食い違う）")


def test_resolve_full_output_dir_matches_the_downstream_naming():
    """④ は UMAP ハイパラのサフィックスが付いた先に書くこと。"""
    normal = ac._resolve_full_output_dir("/out", "umap", downstream=False)
    assert normal == "/out/umap"

    down = ac._resolve_full_output_dir(
        "/out", "umap", downstream=True,
        umap_nn=15, umap_md=0.3, umap_dims=20, umap_metric="cosine")
    assert down == "/out/umap_nn15_md0p3_dim20", down

    # 同じ条件なら同じ先 = 上書きになる（これが症状の原因）
    again = ac._resolve_full_output_dir(
        "/out", "umap", downstream=True,
        umap_nn=15, umap_md=0.3, umap_dims=20, umap_metric="cosine")
    assert again == down

    # サフィックスを二重に付けない
    twice = ac._resolve_full_output_dir(
        "/out", "umap_nn15_md0p3_dim20", downstream=True,
        umap_nn=30, umap_md=0.3, umap_dims=20, umap_metric="cosine")
    assert twice == "/out/umap_nn30_md0p3_dim20", twice


def test_resolve_full_output_dir_refuses_a_blank_base():
    """出力先が空欄なら None（ver56.6 の空欄ガードを維持）。"""
    for blank in ("", "   ", None):
        assert ac._resolve_full_output_dir(blank, "umap", downstream=False) is None


def test_resolve_full_output_dir_tolerates_a_missing_subfolder():
    assert ac._resolve_full_output_dir("/out", None, downstream=False) == "/out"


# ---------------------------------------------------------------------------
# 確認画面が ④ でも開くこと
# ---------------------------------------------------------------------------

def _modal(trigger, monkeypatch, *, has_results=True):
    class _Ctx:
        triggered_id = trigger

    monkeypatch.setattr(ac, "ctx", _Ctx)
    monkeypatch.setattr(ac, "_output_has_existing_results",
                        lambda target: has_results)
    monkeypatch.setattr(
        "app.callbacks.interactive_callbacks._detect_integration_methods",
        lambda t: {"Harmony": "x"})
    return ac.open_overwrite_modal(
        1, 1, 1, "/out", "umap", 15, 0.3, 20, "cosine")


def test_modal_opens_for_the_downstream_button(monkeypatch):
    """★ ④ でも既存結果があれば確認画面を開くこと。"""
    is_open, detail, mode = _modal("btn_run_downstream", monkeypatch)
    assert is_open is True, "④ だけ確認なしで上書きしている"
    assert mode == "downstream"
    assert "umap_nn15_md0p3_dim20" in str(detail), (
        f"確認画面が ④ の実際の出力先を示していない: {detail}")


@pytest.mark.parametrize("trig,mode", [
    ("run_analysis", "run"),
    ("btn_make_reduction", "reduction"),
])
def test_modal_still_works_for_the_existing_buttons(monkeypatch, trig, mode):
    """既存 2 ボタンの挙動は変わらないこと。"""
    is_open, detail, got = _modal(trig, monkeypatch)
    assert is_open is True and got == mode
    assert "/out/umap" in str(detail)


def test_modal_stays_closed_without_existing_results(monkeypatch):
    """既存結果が無ければ従来どおり即実行（確認画面は出さない）。"""
    is_open, _, mode = _modal("btn_run_downstream", monkeypatch, has_results=False)
    assert is_open is False and mode == "downstream"


# ---------------------------------------------------------------------------
# 実行本体が ④ でも止まること / 確認後に ④ として走ること
# ---------------------------------------------------------------------------

def _run(trigger, monkeypatch, *, has_results=True, pending=None):
    class _Ctx:
        triggered_id = trigger

    monkeypatch.setattr(ac, "ctx", _Ctx)
    monkeypatch.setattr(ac, "_output_has_existing_results",
                        lambda target: has_results)
    # ver56.7: 入力チェックのゲートが上書きゲートより手前にある。ここで見たいのは
    #   **上書きの扱い**なので、入力は正常だったことにして切り分ける
    #   （入力チェック側は tests/test_preflight_blocks_execution.py が見る）。
    monkeypatch.setattr(ac, "_collect_preflight_errors",
                        lambda *a, **kw: ([], []))
    seen = {}
    monkeypatch.setattr(ac, "save_last_settings", lambda d: seen.update(d))
    kwargs = dict(_RUN_ARGS)
    kwargs["overwrite_pending_mode"] = pending
    return ac.run_analysis(**kwargs), seen


# run_analysis は引数が多いので、既定は「何も起きない安全な値」で埋める。
_RUN_ARGS = {
    "n_clicks": 0, "reduction_clicks": 0, "downstream_clicks": 1,
    "confirm_overwrite_clicks": 0,
    "desi_method": None, "tims_method": "tims_v8",
    "data_folder": "/data", "annotation_path": "", "use_annotation_check": [],
    "p_thresh": 0.05, "logfc_thresh": 0.25,
    "resume_rds": False, "rds_folder": "",
    "output_subfolder": "umap", "output_dir": "/out",
    "rds_path": "", "reanalysis_data_folder": "", "filter_mode": "",
    "target_clusters": "", "ion_mode": "Positive", "tolerance_mz": 0.01,
    "adduct_filter": ["+H"], "reanalysis_p_thresh": 0.05,
    "reanalysis_logfc_thresh": 0.25, "reanalysis_ion_mode": "Positive",
    "reanalysis_tolerance_mz": 0.01, "reanalysis_adduct_filter": ["+H"],
    "annotation_csv": "", "rds_folder_reanalysis": "", "cluster_source": "",
    "resume_reanalysis": False, "resume_reanalysis_dir": "",
    "reanalysis_annotation_path": "",
    "desi_v8_script": "", "desi_cluster_script": "",
    "tims_v8_script": "", "tims_cluster_script": "",
    "app_state": {}, "selected_project": None, "current_sub_project_id": None,
    "calibration_enable": False, "calibration_table_data": [],
    "calibration_regression_mode": "poly3", "calibration_min_peaks": 2,
    "reanalysis_cal_use_previous": False, "reanalysis_cal_data": None,
    "annotation_filter_data": None, "annotation_filter_reanalysis_data": None,
    "extra_data_folders": [], "mz_align_ppm": 0, "selected_samples": [],
    "cal_per_sample_store": {}, "cal_sample_selector_prev": "__all__",
    "desi_use_roi_as_sample": False,
    # ver58.0 (A-1): DESI のバッチ補正の有無
    "desi_scenario": "correct", "desi_roi_filter_list": None,
    "normalize_input": "OFF", "norm_mode": "log1p",
    "normalize_input_reanalysis": "OFF", "norm_mode_reanalysis": "log1p",
    "umap_n_neighbors_input": 15, "umap_min_dist_input": 0.3,
    "umap_metric_input": "cosine", "umap_dims_input": 20,
    "tims_scenario": None, "reanalysis_tims_scenario": None,
    "overwrite_pending_mode": None,
}


def test_run_stops_for_the_downstream_button(monkeypatch):
    """★ ④ も既存結果があれば実行本体で止まること（確認画面に委ねる）。"""
    (out, _seen) = _run("btn_run_downstream", monkeypatch)
    assert out == (no_update,) * 10, (
        "④ が確認を経ずに走り、前回の結果を上書きしている")


def test_run_proceeds_without_existing_results(monkeypatch):
    """既存結果が無ければ ④ は従来どおり進むこと（止めすぎない）。"""
    (out, seen) = _run("btn_run_downstream", monkeypatch, has_results=False)
    assert out != (no_update,) * 10, "既存結果が無いのに止めている"


def test_confirm_restores_the_downstream_mode():
    """★ 確認後の本実行で ④ のモードが復元されること。

    ここが `False` に潰されていると、確認を経ると ④ ではなく通常解析が走る。
    """
    src = inspect.getsource(ac.run_analysis)
    head = src[:src.index("# 現在の設定を自動保存")]
    assert 'overwrite_pending_mode == "downstream"' in head, (
        "確認後に ④ のモードを復元していない（通常解析が走ってしまう）")
    assert "downstream_mode = False" not in head, (
        "④ のモードを無条件に潰す行が残っている")
