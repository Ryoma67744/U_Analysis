# =============================================================================
# MSI Analysis Application - Analysis Execution Callbacks
# 解析実行・進捗監視 コールバック
# =============================================================================

import re
from datetime import datetime
from pathlib import Path

from dash import Input, Output, State, callback, ctx, no_update

from app.config import (
    DESI_V8_TEMPLATE_PATH, DESI_CLUSTER_FILTER_PATH,
    TIMS_V8_TEMPLATE_PATH, TIMS_CLUSTER_FILTER_PATH,
    MERGE_CLUSTERS_SCRIPT_PATH,
)
from app.services.analysis_runner import (
    generate_v8_config,
    generate_cluster_filter_config,
    start_analysis_process,
    get_analysis_log,
    get_analysis_log_full,
    format_log_lines_styled,
    get_analysis_status,
    check_process_completion,
    stop_analysis_process,
    compute_calibration_coefficients,
)
from app.services.session_manager import save_last_settings
from app.services.project_manager import save_sub_project_settings, save_sub_project_result_dir, update_sub_project
from app.services.notify import warn_user
from app.services import receipt as _receipt
from app.version import version_label


# 解析シナリオ → 補正ポリシー (ANNOTATION_ROLE, BATCH_VAR, ALLOW_CONDITION_CORRECTION)。
# 既定 within_slice = 現状（無補正PCA）。settings_tab.py の tims_scenario と対応。
#   within_slice/condition_compare : 無補正（補正しない）
#   serial_section                 : section_id → 単一sampleでも RPCA(slice_id統合)
#   batch_correct                  : slice_id を技術バッチとして Harmony 補正（非推奨）
#   integrate_correct              : section_id+slice_id+許可 → 単一sampleでも Harmony と
#                                    RPCA を両方 slice_id に適用（測定間の技術差を補正。
#                                    交絡下では条件差も縮小するが、技術差まみれで比較不能に
#                                    なるより補正して共有埋め込み/クラスタを得たい場合に使う。
#                                    DEG は reduction と独立に condition で算出されるため不変）。
_SCENARIO_MAP = {
    "within_slice":      ("biological", "sample",   False),
    # condition_compare は within_slice と同一方針。UI ドロップダウンでは within_slice に
    # 統合済み（settings_tab._norm_scenario）。旧セッション値の後方互換のため写像は残す。
    "condition_compare": ("biological", "sample",   False),
    "serial_section":    ("section_id", "sample",   False),
    "batch_correct":     ("biological", "slice_id", True),
    "integrate_correct": ("section_id", "slice_id", True),
}


# アプリケーションレベルの状態（サブプロセス参照など）
# Dash の dcc.Store はシリアライズ可能な値しか保持できないため、
# process オブジェクトはモジュールレベルで保持する
_process_state = {
    "process": None,
    "log_file_handle": None,
}


# ---------------------------------------------------------------------------
# 解析手法セクションの表示制御
# ---------------------------------------------------------------------------

@callback(
    [Output("sidebar_col", "style"),
     Output("main_content_col", "width")],
    Input("main_tabs", "active_tab"),
)
def toggle_sidebar_content(active_tab):
    """「解析設定」タブ選択時のみサイドバーを表示し、他タブではメインを全幅に"""
    if active_tab == "settings":
        return {"display": "block"}, 9
    return {"display": "none"}, 12


# ---------------------------------------------------------------------------
# 解析実行
# ---------------------------------------------------------------------------

_HP_SUFFIX_RE = re.compile(r"(?:_nn\d+_md[0-9p]+_dim\d+(?:_[A-Za-z]+)?)+$")


def _umap_hp_suffix(nn, md, dims, metric) -> str:
    """UMAPハイパラから FS 安全な短いサフィックスを生成（例: _nn15_md0p3_dim20）。

    ④（reduction再利用）の出力フォルダを試行ごとに自動命名し、上書きせず
    比較できるようにするためのもの。None のトークンは省略、metric は cosine
    以外のときのみ付与する。
    """
    parts = []
    if nn is not None:
        try:
            parts.append(f"nn{int(nn)}")
        except (TypeError, ValueError):
            pass
    if md is not None:
        try:
            parts.append("md" + str(float(md)).replace(".", "p"))
        except (TypeError, ValueError):
            pass
    if dims is not None:
        try:
            parts.append(f"dim{int(dims)}")
        except (TypeError, ValueError):
            pass
    if metric and str(metric).lower() != "cosine":
        parts.append(re.sub(r"[^A-Za-z]", "", str(metric)) or "metric")
    return ("_" + "_".join(parts)) if parts else ""


def _strip_hp_suffix(name: str) -> str:
    """末尾の自動命名サフィックスを除去（④を繰り返しても多重付与しないため）。"""
    return _HP_SUFFIX_RE.sub("", name or "")


def _output_has_existing_results(full_output_dir: str) -> bool:
    """出力先フォルダに既存の解析結果があるか判定（上書き警告ゲート用）。

    reduction RDS（_detect_integration_methods が非空）/ analysis_params.json /
    RDS_Files/*.rds のいずれかがあれば True。フォルダ非存在は False。
    """
    try:
        p = Path(full_output_dir)
    except (TypeError, ValueError):
        return False
    if not p.is_dir():
        return False
    try:
        # 循環 import 回避のためローカル import（既存コードの踏襲）
        from app.callbacks.interactive_callbacks import _detect_integration_methods
        if _detect_integration_methods(str(p)):
            return True
    except Exception:
        pass
    if (p / "analysis_params.json").exists():
        return True
    rds_dir = p / "RDS_Files"
    if rds_dir.is_dir() and any(rds_dir.glob("*.rds")):
        return True
    return False


@callback(
    [Output("overwrite_results_modal", "is_open", allow_duplicate=True),
     Output("overwrite_results_detail", "children"),
     Output("overwrite_pending_mode", "data")],
    Input("run_analysis", "n_clicks"),
    Input("btn_make_reduction", "n_clicks"),
    [State("output_dir", "value"),
     State("output_subfolder", "value")],
    prevent_initial_call=True,
)
def open_overwrite_modal(run_clicks, reduction_clicks, output_dir, output_subfolder):
    """フル/reductionのみ実行時、出力先に既存結果があれば上書き確認モーダルを開く。

    既存結果が無ければモーダルは開かない（従来どおり即実行）。pending mode に
    どちらのボタンだったか("run"/"reduction")を記録し、確認後の本実行で復元する。
    """
    trig = ctx.triggered_id
    mode = "reduction" if trig == "btn_make_reduction" else "run"
    if not output_dir:
        return False, no_update, mode
    target = str(Path(output_dir) / (output_subfolder or ""))
    if not _output_has_existing_results(target):
        return False, no_update, mode
    try:
        from app.callbacks.interactive_callbacks import _detect_integration_methods
        methods = list(_detect_integration_methods(target).keys())
    except Exception:
        methods = []
    detail = [html.Div(f"出力先: {target}", className="small text-muted")]
    if methods:
        detail.append(html.Div("既存の手法: " + ", ".join(methods)))
    else:
        detail.append(html.Div("このフォルダには既存の解析結果ファイルがあります。"))
    return True, detail, mode


@callback(
    Output("overwrite_results_modal", "is_open", allow_duplicate=True),
    Input("cancel_overwrite_results", "n_clicks"),
    Input("confirm_overwrite_results", "n_clicks"),
    prevent_initial_call=True,
)
def close_overwrite_modal(cancel_clicks, confirm_clicks):
    """「キャンセル」「実行する」どちらでも上書き確認モーダルを閉じる。"""
    return False


@callback(
    [Output("app_state", "data", allow_duplicate=True),
     Output("progress_interval", "disabled", allow_duplicate=True),
     Output("stop_button_container", "style", allow_duplicate=True),
     Output("progress_container", "style"),
     Output("log_container", "style"),
     Output("log_header", "children", allow_duplicate=True),
     Output("notification_toast", "children", allow_duplicate=True),
     Output("notification_toast", "is_open", allow_duplicate=True)],
    Input("run_analysis", "n_clicks"),
    Input("btn_make_reduction", "n_clicks"),
    Input("btn_run_downstream", "n_clicks"),
    Input("confirm_overwrite_results", "n_clicks"),
    [State("analysis_method", "value"),
     State("analysis_method_tims", "value"),
     State("data_folder", "value"),
     State("annotation_path", "value"),
     State("p_thresh", "value"),
     State("logfc_thresh", "value"),
     State("resume_rds", "value"),
     State("rds_folder", "value"),
     State("output_subfolder", "value"),
     State("output_dir", "value"),
     State("rds_path", "value"),
     State("reanalysis_data_folder", "value"),
     State("filter_mode", "value"),
     State("target_clusters", "value"),
     State("ion_mode", "value"),
     State("tolerance_mz", "value"),
     State("adduct_filter", "value"),
     State("reanalysis_p_thresh", "value"),
     State("reanalysis_logfc_thresh", "value"),
     State("reanalysis_ion_mode", "value"),
     State("reanalysis_tolerance_mz", "value"),
     State("reanalysis_adduct_filter", "value"),
     State("default_annotation_csv", "value"),
     State("rds_folder_reanalysis", "value"),
     State("cluster_source", "value"),
     State("reanalysis_annotation_path", "value"),
     State("desi_v8_script_path", "value"),
     State("desi_cluster_filter_script_path", "value"),
     State("tims_v8_script_path", "value"),
     State("tims_cluster_filter_script_path", "value"),
     State("app_state", "data"),
     State("selected_project", "data"),
     State("current_sub_project_id", "data"),
     State("calibration_enable", "value"),
     State("calibration_table", "data"),
     State("calibration_regression_mode", "value"),
     State("calibration_min_peaks", "value"),
     State("reanalysis_calibration_use_previous", "value"),
     State("reanalysis_calibration_data", "data"),
     State("annotation_filter_store", "data"),
     State("annotation_filter_store_reanalysis", "data"),
     State("extra_data_folders_store", "data"),
     State("mz_align_ppm", "value"),
     State("selected_samples_store", "data"),
     State("cal_per_sample_store", "data"),
     State("cal_sample_selector_prev", "data"),
     State("desi_use_roi_as_sample", "value"),
     State("desi_roi_filter_store", "data"),
     State("normalize_input", "value"),
     State("norm_mode", "value"),
     State("normalize_input_reanalysis", "value"),
     State("norm_mode_reanalysis", "value"),
     State("umap_n_neighbors_input", "value"),
     State("umap_min_dist_input", "value"),
     State("umap_metric_input", "value"),
     State("umap_dims_input", "value"),
     State("tims_scenario", "value"),
     State("reanalysis_tims_scenario", "value"),
     State("overwrite_pending_mode", "data")],
    prevent_initial_call=True,
)
def run_analysis(
    n_clicks,
    reduction_clicks,
    downstream_clicks,
    confirm_overwrite_clicks,
    desi_method, tims_method,
    data_folder, annotation_path, p_thresh, logfc_thresh,
    resume_rds, rds_folder,
    output_subfolder, output_dir,
    rds_path, reanalysis_data_folder, filter_mode, target_clusters,
    ion_mode, tolerance_mz, adduct_filter,
    reanalysis_p_thresh, reanalysis_logfc_thresh,
    reanalysis_ion_mode, reanalysis_tolerance_mz, reanalysis_adduct_filter,
    annotation_csv,
    rds_folder_reanalysis, cluster_source, reanalysis_annotation_path,
    desi_v8_script, desi_cluster_script,
    tims_v8_script, tims_cluster_script,
    app_state,
    selected_project, current_sub_project_id,
    calibration_enable, calibration_table_data,
    calibration_regression_mode, calibration_min_peaks,
    reanalysis_cal_use_previous, reanalysis_cal_data,
    annotation_filter_data,
    annotation_filter_reanalysis_data,
    extra_data_folders,
    mz_align_ppm,
    selected_samples,
    cal_per_sample_store,
    cal_sample_selector_prev,
    desi_use_roi_as_sample,
    desi_roi_filter_list,
    normalize_input, norm_mode,
    normalize_input_reanalysis, norm_mode_reanalysis,
    umap_n_neighbors_input, umap_min_dist_input,
    umap_metric_input, umap_dims_input,
    tims_scenario, reanalysis_tims_scenario,
    overwrite_pending_mode,
):
    # トリガー判定: 通常の「解析実行」(run_analysis) か、
    # PreFlight 用の「reduction のみ作成」(btn_make_reduction) か。
    # reduction_only モードでは PIPELINE_STAGE=reduction_only を注入し、
    # UMAP/クラスタリング/DEG/作図をスキップして reduction RDS だけ生成する。
    trig = ctx.triggered_id
    reduction_only_mode = (trig == "btn_make_reduction")
    downstream_mode = (trig == "btn_run_downstream")
    if (not n_clicks and not reduction_clicks and not downstream_clicks
            and not confirm_overwrite_clicks):
        return no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update

    # ── 上書き警告ゲート ──
    # 出力先に既存結果がある状態で「解析実行」/「reductionのみ」を押した場合は、
    # 確認モーダル（open_overwrite_modal が表示）で「実行する」が押されるまで本実行しない。
    if trig == "confirm_overwrite_results":
        # 確認後の本実行: 元のモードを pending から復元（downstream は警告対象外）
        reduction_only_mode = (overwrite_pending_mode == "reduction")
        downstream_mode = False
        if not confirm_overwrite_clicks:
            return no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update
    elif trig in ("run_analysis", "btn_make_reduction") and output_dir:
        _target = str(Path(output_dir) / (output_subfolder or ""))
        if _output_has_existing_results(_target):
            # 実行は止める（モーダル表示は open_overwrite_modal が担当）
            return no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update

    # 現在の設定を自動保存（次回起動時に復元される）
    try:
        save_last_settings({
            "analysis_method": desi_method,
            "analysis_method_tims": tims_method,
            "data_folder": data_folder,
            "annotation_path": annotation_path,
            "p_thresh": p_thresh,
            "logfc_thresh": logfc_thresh,
            "resume_rds": resume_rds,
            "rds_folder": rds_folder,
            "output_dir": output_dir,
            "rds_path": rds_path,
            "reanalysis_data_folder": reanalysis_data_folder,
            "filter_mode": filter_mode,
            "target_clusters": target_clusters,
            "ion_mode": ion_mode,
            "tolerance_mz": tolerance_mz,
            "reanalysis_p_thresh": reanalysis_p_thresh,
            "reanalysis_logfc_thresh": reanalysis_logfc_thresh,
            "reanalysis_ion_mode": reanalysis_ion_mode,
            "reanalysis_tolerance_mz": reanalysis_tolerance_mz,
            "desi_v8_script_path": desi_v8_script,
            "desi_cluster_filter_script_path": desi_cluster_script,
            "tims_v8_script_path": tims_v8_script,
            "tims_cluster_filter_script_path": tims_cluster_script,
            "desi_use_roi_as_sample": bool(desi_use_roi_as_sample),
            "normalize_input": normalize_input,
            "norm_mode": norm_mode,
            "normalize_input_reanalysis": normalize_input_reanalysis,
            "norm_mode_reanalysis": norm_mode_reanalysis,
            "tims_scenario": tims_scenario,
            "reanalysis_tims_scenario": reanalysis_tims_scenario,
        })
    except Exception as e:
        warn_user(f"解析設定の保存に失敗: {e}")

    # サブプロジェクトに解析設定を紐づけて保存
    try:
        if selected_project and current_sub_project_id:
            project_id = selected_project.get("id", "")
            if project_id:
                save_sub_project_settings(project_id, current_sub_project_id, {
                    "analysis_method": desi_method,
                    "analysis_method_tims": tims_method,
                    "data_folder": data_folder,
                    "output_dir": output_dir,
                    "annotation_path": annotation_path,
                    "p_thresh": p_thresh,
                    "logfc_thresh": logfc_thresh,
                    "ion_mode": ion_mode,
                    "tolerance_mz": tolerance_mz,
                    "resume_rds": resume_rds,
                    "rds_folder": rds_folder,
                    "reanalysis_data_folder": reanalysis_data_folder,
                    "rds_path": rds_path,
                    "filter_mode": filter_mode,
                    "target_clusters": target_clusters,
                    "reanalysis_p_thresh": reanalysis_p_thresh,
                    "reanalysis_logfc_thresh": reanalysis_logfc_thresh,
                    "reanalysis_ion_mode": reanalysis_ion_mode,
                    "reanalysis_tolerance_mz": reanalysis_tolerance_mz,
                })
    except Exception as e:
        warn_user(f"サブプロジェクト設定の保存に失敗: {e}")

    analysis_type = desi_method or tims_method or "desi_v8"
    # ④ downstream_from_reduction は「reduction を再利用して UMAP 以降のみ」をメイン解析
    # 経路で行う。再解析モードで④を押した場合も cluster_filter ではなく main(v8) 経路へ
    # リマップする（last_result_dir の部分集合 reduction を④がロードして再UMAP）。
    if downstream_mode and analysis_type in ("desi_cluster_filter", "tims_cluster_filter"):
        analysis_type = "tims_v8" if analysis_type == "tims_cluster_filter" else "desi_v8"
    full_output_dir = str(Path(output_dir) / output_subfolder)
    if downstream_mode:
        # ④: ハイパラ反復を上書きせず比較できるよう、UMAPハイパラ値で出力サブフォルダを自動命名
        _suf = _umap_hp_suffix(umap_n_neighbors_input, umap_min_dist_input,
                               umap_dims_input, umap_metric_input)
        if _suf:
            _base = _strip_hp_suffix(output_subfolder or "umap")
            full_output_dir = str(Path(output_dir) / f"{_base}{_suf}")
    Path(full_output_dir).mkdir(parents=True, exist_ok=True)

    try:
        if analysis_type in ("desi_v8", "tims_v8"):
            # UMAP解析
            if analysis_type == "desi_v8":
                template = desi_v8_script or str(DESI_V8_TEMPLATE_PATH)
            else:
                template = tims_v8_script or str(TIMS_V8_TEMPLATE_PATH)

            # サンプル名: UIのチェックリスト（selected_samples）から取得
            if selected_samples:
                sample_names = list(selected_samples)
            elif analysis_type == "tims_v8":
                from app.services.data_manager import list_tims_files_multi
                all_folders = [data_folder] + (extra_data_folders or [])
                sample_names = list_tims_files_multi(all_folders)
            else:
                from app.services.data_manager import list_msi_files
                sample_names = list_msi_files(data_folder)

            params = {
                "template_path": template,
                "data_folder": data_folder,
                "sample_names": sample_names,
                "annotation_path": annotation_path or "",
                "p_thresh": float(p_thresh) if p_thresh else 0.05,
                "logfc_thresh": float(logfc_thresh) if logfc_thresh else 0.25,
                "resume_from_rds": bool(resume_rds),
                "resume_rds_paths": [],
                # 入力正規化ポリシー（UIトグル）: OFF=正規化済み入力 → INPUT_NORMALIZED=TRUE
                "input_normalized": (normalize_input == "OFF"),
                "norm_mode": norm_mode or "log1p",
            }

            # --- UMAP ハイパーパラメータ（PreFlight 推奨値を手動反映可能。
            #     analysis_runner の _hp_* 機構でテンプレ定数へ注入される）---
            if umap_n_neighbors_input is not None:
                params["umap_n_neighbors"] = int(umap_n_neighbors_input)
            if umap_min_dist_input is not None:
                params["umap_min_dist"] = float(umap_min_dist_input)
            if umap_metric_input:
                params["umap_metric"] = str(umap_metric_input)
            if umap_dims_input is not None:
                params["umap_dims_n"] = int(umap_dims_input)

            # PIPELINE_STAGE: reduction_only（PreFlight 診断用に reduction だけ作る
            #   軽量モード。テンプレ定数 PIPELINE_STAGE へ注入され、UMAP 以降を
            #   スキップ。未指定時はテンプレ既定の "full"）
            if reduction_only_mode:
                params["pipeline_stage"] = "reduction_only"

            # ④ downstream_from_reduction: ①の reduction RDS を読み込み UMAP 以降のみ
            #   実行。重い reduction(ScaleData/PCA/Harmony/RPCA)は再計算せず再利用する。
            #   既存 RESUME 機構を流用: resume_from_rds=True + resume_rds_paths を①の
            #   RDS_Files に向ける → analysis_runner が RESUME_DIR_PATH を自動解決。
            if downstream_mode:
                params["pipeline_stage"] = "downstream_from_reduction"
                params["resume_from_rds"] = True
                from app.services.project_manager import get_sub_project
                from app.callbacks.interactive_callbacks import (
                    _detect_integration_methods,
                )
                _pid = (selected_project or {}).get("id", "")
                _sub = (get_sub_project(_pid, current_sub_project_id)
                        if (_pid and current_sub_project_id) else None)
                _src = ((_sub.get("last_result_dir") or _sub.get("output_dir", ""))
                        if _sub else "")
                _rds_map = _detect_integration_methods(_src) if _src else {}
                if not _rds_map:
                    return (
                        no_update, no_update, no_update, no_update, no_update,
                        no_update,
                        "④を実行できません: ①の reduction RDS が見つかりません。"
                        "先に①「reduction のみ作成」を実行してください。",
                        True,
                    )
                params["resume_rds_paths"] = [str(p) for p in _rds_map.values()]

            if resume_rds and rds_folder and not downstream_mode:
                rds_files = sorted(Path(rds_folder).glob("*.rds"))
                params["resume_rds_paths"] = [str(f) for f in rds_files]

            # TIMS固有パラメータ
            if analysis_type == "tims_v8":
                params["ion_mode"] = ion_mode or "Positive"
                params["tolerance_mz"] = float(tolerance_mz) if tolerance_mz else 0.01
                if adduct_filter:
                    params["adduct_patterns"] = adduct_filter
                # 解析シナリオ → 補正ポリシーを注入（ver6 の ANNOTATION_ROLE 等）
                _role, _bv, _allow = _SCENARIO_MAP.get(
                    tims_scenario or "within_slice", _SCENARIO_MAP["within_slice"])
                params["annotation_role"] = _role
                params["batch_var"] = _bv
                params["allow_condition_correction"] = _allow
                # INPUT_PATHS: 選択サンプルに対応するファイルのフルパスリスト
                from app.services.data_manager import build_tims_input_paths_multi
                all_folders = [data_folder] + (extra_data_folders or [])
                all_paths = build_tims_input_paths_multi(all_folders)
                if selected_samples:
                    selected_set = set(selected_samples)
                    all_paths = [p for p in all_paths
                                 if Path(p).stem in selected_set]
                params["input_paths"] = all_paths
                # OUTPUT_DIR: TIMSスクリプトはOUTPUT_DIR（大文字）を使用
                params["output_dir_var"] = "OUTPUT_DIR"
                # ANNOTATION_CSV_PATH: UIの初期設定から取得
                if annotation_csv:
                    _acsv = Path(annotation_csv)
                    if _acsv.is_file():
                        params["annotation_csv_path"] = annotation_csv
                    elif _acsv.is_dir():
                        # ディレクトリが指定された場合、中の CSV を自動検索
                        csvs = sorted(_acsv.glob("*.csv"))
                        if csvs:
                            params["annotation_csv_path"] = str(csvs[0])

            # --- DESI ROI 設定 (ROI 列があれば各 ROI を別サンプルとして扱う) ---
            # desi_roi_filter_list は Store から取得した list[str]。
            # update_desi_roi_selector / sync_desi_roi_to_store callback で
            # ファイルから読み取った ROI 候補のチェックボックス選択値を集約済み。
            if analysis_type == "desi_v8":
                params["use_roi_as_sample"] = bool(desi_use_roi_as_sample)
                if desi_roi_filter_list:
                    params["roi_filter"] = list(desi_roi_filter_list)

            # --- m/z アライメント (ppm) ---
            if analysis_type == "tims_v8":
                params["mz_align_ppm"] = float(mz_align_ppm) if mz_align_ppm else 0

            # --- Annotation Filter（TIMS: Parquet内の切片選択） ---
            if analysis_type == "tims_v8" and annotation_filter_data:
                params["annotation_filter"] = annotation_filter_data

            # --- m/z キャリブレーション（TIMS UMAP解析） ---
            if calibration_enable and analysis_type == "tims_v8":
                # 現在表示中のテーブルを Store に同期
                per_store = dict(cal_per_sample_store or {})
                if cal_sample_selector_prev:
                    per_store[cal_sample_selector_prev] = calibration_table_data

                reg_mode = calibration_regression_mode or "poly3"
                min_pk = int(calibration_min_peaks or 2)

                # サンプル固有エントリの抽出
                sample_specific = {
                    k: v for k, v in per_store.items()
                    if k != "__all__" and v
                }

                if sample_specific:
                    # サンプル別係数を算出
                    cal_by_sample = {}
                    for sname, tbl in sample_specific.items():
                        result = compute_calibration_coefficients(
                            tbl, reg_mode, min_pk)
                        if result:
                            cal_by_sample[sname] = result["coefficients"]

                    # グローバルフォールバック
                    global_tbl = per_store.get("__all__", [])
                    global_result = compute_calibration_coefficients(
                        global_tbl, reg_mode, min_pk) if global_tbl else None

                    if cal_by_sample:
                        params["calibration_enable"] = True
                        params["calibration_by_sample"] = cal_by_sample
                        if global_result:
                            params["calibration_coefficients"] = global_result["coefficients"]
                        else:
                            params["calibration_coefficients"] = list(
                                cal_by_sample.values())[0]
                        params["calibration_result"] = global_result or {
                            "coefficients": params["calibration_coefficients"],
                            "degree": len(params["calibration_coefficients"]) - 1,
                        }
                else:
                    # 従来動作: 全サンプル共通
                    cal_result = compute_calibration_coefficients(
                        calibration_table_data, reg_mode, min_pk)
                    if cal_result:
                        params["calibration_enable"] = True
                        params["calibration_coefficients"] = cal_result["coefficients"]
                        params["calibration_result"] = cal_result

            config_path = generate_v8_config(params, full_output_dir)

        else:
            # 再解析（クラスターフィルタ）
            if analysis_type == "desi_cluster_filter":
                template = desi_cluster_script or str(DESI_CLUSTER_FILTER_PATH)
            else:
                template = tims_cluster_script or str(TIMS_CLUSTER_FILTER_PATH)

            # target_clusters をパース
            clusters = []
            if target_clusters:
                clusters = [int(c.strip()) for c in target_clusters.split(",") if c.strip().isdigit()]

            if analysis_type == "tims_cluster_filter":
                from app.services.data_manager import list_tims_files
                sample_names = list_tims_files(reanalysis_data_folder)
            else:
                from app.services.data_manager import list_msi_files
                sample_names = list_msi_files(reanalysis_data_folder)

            # RDSパス解決: 直接指定 > フォルダ+クラスタソースから構築
            resolved_rds_path = rds_path or ""
            resolved_cluster_source = cluster_source or "harmony"
            if not resolved_rds_path and rds_folder_reanalysis:
                if analysis_type == "desi_cluster_filter":
                    # DESI: Python側でファイル名を構築（DESI命名規則）
                    rds_filename = ("DESI_SeuratCombined_harmony.rds"
                                    if resolved_cluster_source == "harmony"
                                    else "DESI_SeuratCombined_RPCA.rds")
                    resolved_rds_path = str(Path(rds_folder_reanalysis) / rds_filename)
                # TIMS: R側の resolve_rds_path() に委譲するため rds_path は空のまま

            params = {
                "template_path": template,
                "rds_path": resolved_rds_path,
                "original_data_folder": reanalysis_data_folder or data_folder,
                "filter_mode": filter_mode or "exclude",
                "target_clusters": clusters,
                "sample_names": sample_names,
                # マージスクリプトパス（DESI/TIMS共通）
                "merge_script_path": str(MERGE_CLUSTERS_SCRIPT_PATH),
                # 本体スクリプトパス（動的注入: V13_SCRIPT_PATH / V8_SCRIPT_PATH）
                "main_analysis_script_path": (
                    tims_v8_script or str(TIMS_V8_TEMPLATE_PATH)
                ) if analysis_type == "tims_cluster_filter" else (
                    desi_v8_script or str(DESI_V8_TEMPLATE_PATH)
                ),
            }

            # 再解析DEG閾値（フル解析と同じ要領で V13_/V8_ 経由でメインテンプレ copy に反映）
            if reanalysis_p_thresh is not None:
                params["p_thresh"] = float(reanalysis_p_thresh)
            if reanalysis_logfc_thresh is not None:
                params["logfc_thresh"] = float(reanalysis_logfc_thresh)

            # ① PreFlight: 再解析でも reduction_only（絞り込んだ部分集合の reduction だけ
            #    作り UMAP 前で停止）。RERUN_PIPELINE_STAGE 経由でメインテンプレ copy の
            #    PIPELINE_STAGE へ伝播し、後段 merge/ReUMAP もスキップされる。
            if reduction_only_mode:
                params["pipeline_stage"] = "reduction_only"

            # 再解析用アノテーションファイル
            if reanalysis_annotation_path:
                params["reanalysis_annotation_path"] = reanalysis_annotation_path

            # 再解析用 Annotation Filter（TIMS: 切片選択）
            if analysis_type == "tims_cluster_filter" and annotation_filter_reanalysis_data:
                params["annotation_filter"] = annotation_filter_reanalysis_data

            # TIMSクラスターフィルター固有パラメータ
            if analysis_type == "tims_cluster_filter":
                from app.services.data_manager import build_tims_input_paths
                src_folder = reanalysis_data_folder or data_folder
                params["original_input_paths"] = build_tims_input_paths(src_folder)
                params["export_data_dir"] = full_output_dir
                # 入力正規化ポリシー（再解析UIのトグル → V13_INPUT_NORMALIZED/NORM_MODE 注入）
                params["input_normalized"] = (normalize_input_reanalysis == "OFF")
                params["norm_mode"] = norm_mode_reanalysis or "log1p"
                # 解析シナリオ → V13_ 経由で ver6 コピーへ伝播（subset の reduction に効かせる）
                _r_role, _r_bv, _r_allow = _SCENARIO_MAP.get(
                    reanalysis_tims_scenario or "within_slice", _SCENARIO_MAP["within_slice"])
                params["v13_annotation_role"] = _r_role
                params["v13_batch_var"] = _r_bv
                params["v13_allow_condition_correction"] = _r_allow
                # 再解析の m/z アノテーション（TIMS専用。ion/tolerance は V13_、adduct は env 経路で反映）
                if reanalysis_ion_mode:
                    params["ion_mode"] = reanalysis_ion_mode
                if reanalysis_tolerance_mz is not None:
                    params["tolerance_mz"] = float(reanalysis_tolerance_mz)
                if reanalysis_adduct_filter:
                    params["adduct_patterns"] = reanalysis_adduct_filter
                # RDSフォルダ+クラスタソース → R側で解決
                if rds_folder_reanalysis:
                    params["rds_run_dir"] = rds_folder_reanalysis
                    params["cluster_source"] = resolved_cluster_source
                elif rds_path:
                    params["rds_run_dir"] = str(Path(rds_path).parent)

            # --- m/z キャリブレーション（TIMS 再解析） ---
            if analysis_type == "tims_cluster_filter":
                if reanalysis_cal_use_previous and reanalysis_cal_data:
                    params["calibration_enable"] = True
                    params["calibration_coefficients"] = reanalysis_cal_data["coefficients"]

            config_path = generate_cluster_filter_config(params, full_output_dir)

        # サブプロセス開始（TIMS再解析の adduct は ver6 の env フック ANNOT_ADDUCTS で反映）
        _env_extra = None
        if analysis_type == "tims_cluster_filter" and params.get("adduct_patterns"):
            _env_extra = {"ANNOT_ADDUCTS": ",".join(params["adduct_patterns"])}
        result = start_analysis_process(config_path, full_output_dir, env_extra=_env_extra)

        if not result["success"]:
            return (
                app_state, True,
                {"display": "none"}, {"display": "none"}, {"display": "none"},
                no_update,
                f"解析開始に失敗: {result['message']}", True,
            )

        # 解析パラメータを結果フォルダに保存 (C1: パラメータ履歴)
        try:
            import json as _json
            _params_to_save = {
                "analysis_type": analysis_type,
                "data_folder": data_folder,
                "output_dir": full_output_dir,
                "annotation_path": annotation_path or "",
                "annotation_csv": annotation_csv or "",
                "ion_mode": ion_mode or "",
                "tolerance_mz": tolerance_mz,
                "adduct_filter": adduct_filter or [],
                "p_thresh": p_thresh,
                "logfc_thresh": logfc_thresh,
                "filter_mode": filter_mode or "",
                "target_clusters": target_clusters or "",
                "resume_rds": bool(resume_rds),
                "timestamp": datetime.now().isoformat(),
                "umap_n_neighbors": params.get("umap_n_neighbors"),
                "umap_min_dist": params.get("umap_min_dist"),
                "umap_metric": params.get("umap_metric"),
                "umap_dims_n": params.get("umap_dims_n"),
                "pipeline_stage": params.get("pipeline_stage", "full"),
            }
            # キャリブレーション情報の保存
            cal_r = params.get("calibration_result", {})
            _params_to_save["calibration_enable"] = bool(params.get("calibration_enable"))
            _params_to_save["calibration_coefficients"] = cal_r.get("coefficients")
            _params_to_save["calibration_degree"] = cal_r.get("degree")
            _params_to_save["calibration_r_squared"] = cal_r.get("r_squared")
            _params_to_save["calibration_n_points"] = cal_r.get("n_points")
            _params_to_save["calibration_regression_mode"] = calibration_regression_mode
            _params_to_save["calibration_table"] = calibration_table_data
            _pf = Path(full_output_dir) / "analysis_params.json"
            _pf.write_text(
                _json.dumps(_params_to_save, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            warn_user(f"パラメータ保存に失敗: {e}")

        # プロセス参照をモジュールレベルで保持
        _process_state["process"] = result["process"]
        _process_state["log_file_handle"] = result.get("log_file_handle")

        new_state = {
            "is_running": True,
            "process_pid": result["pid"],
            "progress_file": result["progress_file"],
            "log_file": result["log_file"],
            "status_file": result["status_file"],
            "full_output_dir": full_output_dir,
            "start_time": datetime.now().isoformat(),
            "analysis_type": analysis_type,
            "project_id": selected_project.get("id", "") if selected_project else "",
            "sub_project_id": current_sub_project_id or "",
        }

        return (
            new_state, False,  # Interval 有効化
            {"flex": "0 0 auto"},  # 停止ボタン表示
            {"flex": "1"},         # 進捗バー表示
            {"marginTop": "20px"}, # ログ表示
            "⏳ 解析中...",        # ログヘッダーリセット
            ("解析を開始しました（出力: " + Path(full_output_dir).name + "）"
             if downstream_mode else "解析を開始しました"), True,
        )

    except Exception as e:
        return (
            app_state, True,
            {"display": "none"}, {"display": "none"}, {"display": "none"},
            no_update,
            f"エラー: {e}", True,
        )


# ---------------------------------------------------------------------------
# 進捗監視（2秒ごと）
# ---------------------------------------------------------------------------

# 解析タイプ別のステップ定義リスト
# 各ステップ: (表示名, 検出キーワード) — ログにキーワードが出現したらそのステップに到達
_STEP_DEFINITIONS = {
    "desi_v8": [
        ("Loading", "reading desi data"),
        ("Filtering", "spot filtering"),
        ("PCA", "pca"),
        ("UMAP", "umap"),
        ("Clustering", "findclusters"),
        ("Harmony/RPCA", "harmony"),
        ("DEG", "deg"),
        ("Heatmap", "heatmap"),
        ("Volcano", "volcano"),
        ("MSI Images", "msi"),
        ("Saving", "saving"),
        ("Done", "all done"),
    ],
    "tims_v8": [
        ("Loading", "reading parquet"),
        ("Preprocessing", "preprocessing"),
        ("Harmony correction", "harmony"),
        ("Clustering", "findclusters"),
        ("Markers", "finding markers"),
        ("Annotation", "annotating"),
        ("Heatmap", "heatmap"),
        ("Volcano", "volcano"),
        ("MSI Images", "msi images"),
        ("TIC Overlay", "tic overlay"),
        ("RPCA", "running rpca"),
        ("Saving", "saving"),
        ("Done", "all done"),
    ],
    "desi_cluster_filter": [
        ("Loading", "loading"),
        ("Filtering", "filter"),
        ("DEG", "deg"),
        ("Heatmap", "heatmap"),
        ("Volcano", "volcano"),
        ("MSI Images", "msi"),
        ("Saving", "saving"),
        ("Merge", "merge"),
        ("Done", "done"),
    ],
    "tims_cluster_filter": [
        ("Loading", "loading"),
        ("Filtering", "filter"),
        ("Markers", "finding markers"),
        ("Heatmap", "heatmap"),
        ("Volcano", "volcano"),
        ("MSI Images", "msi"),
        ("Saving", "saving"),
        ("Merge", "merge"),
        ("Done", "done"),
    ],
}


def _detect_current_step(log_text: str, analysis_type: str):
    """ログテキストからステップ定義に基づいて現在のステップを検出する。
    Returns: (step_number, total_steps, step_name)
    step_number は 1始まり。未検出なら (0, total, "準備中")
    """
    steps = _STEP_DEFINITIONS.get(analysis_type, _STEP_DEFINITIONS["desi_v8"])
    total = len(steps)
    log_lower = log_text.lower()

    # 後ろから探して最後に到達したステップを見つける
    current_idx = -1
    for i in range(len(steps) - 1, -1, -1):
        _, keyword = steps[i]
        if keyword in log_lower:
            current_idx = i
            break

    if current_idx < 0:
        return 0, total, "準備中"

    step_name = steps[current_idx][0]
    return current_idx + 1, total, step_name


def _format_remaining_time(start_time_iso: str, step_current: int, step_total: int) -> str:
    """経過時間と現在のステップ位置から残り時間を推定する。
    ステップが進むたびに毎回再計算されるため、動的に精度が向上する。
    """
    if step_current <= 0:
        return "残り時間: 計算中..."

    try:
        start_time = datetime.fromisoformat(start_time_iso)
    except (ValueError, TypeError):
        return "残り時間: 計算中..."

    elapsed = (datetime.now() - start_time).total_seconds()
    if elapsed < 1:
        return "残り時間: 計算中..."

    progress_ratio = step_current / step_total
    if progress_ratio >= 1.0:
        return "残り時間: まもなく完了"

    remaining_sec = elapsed / progress_ratio * (1 - progress_ratio)

    if remaining_sec >= 3600:
        h = int(remaining_sec // 3600)
        m = int((remaining_sec % 3600) // 60)
        return f"残り約 {h}時間{m}分"
    elif remaining_sec >= 60:
        m = int(remaining_sec // 60)
        return f"残り約 {m}分"
    else:
        return "残り約 1分未満"


def _format_elapsed_time(start_time_iso: str) -> str:
    """開始時刻からの経過時間を「所要時間」として整形する。"""
    try:
        start_time = datetime.fromisoformat(start_time_iso)
    except (ValueError, TypeError):
        return ""

    elapsed = (datetime.now() - start_time).total_seconds()
    if elapsed >= 3600:
        h = int(elapsed // 3600)
        m = int((elapsed % 3600) // 60)
        return f"所要時間: {h}時間{m}分"
    elif elapsed >= 60:
        m = int(elapsed // 60)
        return f"所要時間: {m}分"
    else:
        return "所要時間: 1分未満"


@callback(
    [Output("analysis_log", "children"),
     Output("analysis_progress_bar", "value"),
     Output("analysis_progress_bar", "label"),
     Output("section_progress_text", "children"),
     Output("app_state", "data", allow_duplicate=True),
     Output("progress_interval", "disabled", allow_duplicate=True),
     Output("stop_button_container", "style", allow_duplicate=True),
     Output("progress_container", "style", allow_duplicate=True),
     Output("log_header", "children"),
     Output("notification_toast", "children", allow_duplicate=True),
     Output("notification_toast", "is_open", allow_duplicate=True)],
    Input("progress_interval", "n_intervals"),
    [State("app_state", "data"),
     State("log_search_input", "value"),
     State("log_level_filter", "value"),
     State("log_lines_count", "value")],
    prevent_initial_call=True,
)
def update_progress(n_intervals, app_state, log_search, log_level, log_lines_count):
    if not app_state or not app_state.get("is_running"):
        return (no_update,) * 11

    log_file = app_state.get("log_file")
    status_file = app_state.get("status_file")
    output_dir = app_state.get("full_output_dir")
    start_time_iso = app_state.get("start_time", "")
    analysis_type = app_state.get("analysis_type", "desi_v8")

    # ログ取得（フィルタ用の行数設定）
    n_lines = log_lines_count if log_lines_count else 50
    if n_lines == 0 and log_file:
        raw_log = get_analysis_log_full(log_file)
    elif log_file:
        raw_log = get_analysis_log(log_file, last_n=n_lines)
    else:
        raw_log = ""
    # ステップ検出用の生テキスト（フィルタ前に取得）
    log_text_for_steps = get_analysis_log(log_file, last_n=600) if log_file else ""
    # 表示用のスタイル付きログ
    styled_log = format_log_lines_styled(
        raw_log, search=log_search or "", level=log_level or "all")

    # プロセス完了チェック
    process = _process_state.get("process")
    log_fh = _process_state.get("log_file_handle")
    completed_status = None
    if process:
        completed_status = check_process_completion(process, status_file, log_fh)

    # ステータス確認
    status = get_analysis_status(status_file) if status_file else "unknown"

    # 出力ファイル数
    file_count = 0
    if output_dir and Path(output_dir).is_dir():
        for ext in ("*.png", "*.csv", "*.rds"):
            file_count += len(list(Path(output_dir).rglob(ext)))

    # ステップ検出（ステップ定義リストに基づく — フィルタ前の生テキスト使用）
    step_current, step_total, step_name = _detect_current_step(log_text_for_steps, analysis_type)

    # 進捗バーをステップベースで計算
    if step_current > 0:
        progress = min(95, int(step_current / step_total * 100))
    else:
        progress = min(95, file_count * 2)  # ステップ未検出時はファイル数ベース

    # 残り時間を毎回再計算
    remaining_text = _format_remaining_time(start_time_iso, step_current, step_total)

    # 表示テキスト組み立て
    step_display = f"{step_name} ({step_current}/{step_total})" if step_current > 0 else "準備中"
    section_text = f"出力: {file_count} ファイル | ステップ: {step_display} | {remaining_text}"

    if status in ("finished", "error") or completed_status:
        final_status = completed_status or status
        _process_state["process"] = None
        _process_state["log_file_handle"] = None

        app_state["is_running"] = False
        elapsed_text = _format_elapsed_time(start_time_iso)

        if final_status == "finished":
            msg = "解析が完了しました"
            section_text = f"出力: {file_count} ファイル | ✅ 完了 ({step_total}/{step_total}) | {elapsed_text}"
            log_header = "✅ 解析完了"

            # 解析結果ディレクトリをサブプロジェクトに保存
            try:
                proj_id = app_state.get("project_id")
                sub_id = app_state.get("sub_project_id")
                if proj_id and sub_id and output_dir:
                    save_sub_project_result_dir(proj_id, sub_id, output_dir)
                # 解析に使った生データフォルダもサブプロジェクトに保存しておく
                # （出力時の「MSIデータフォルダ」自動推定フォールバックを不要にする）
                if proj_id and sub_id and data_folder:
                    update_sub_project(proj_id, sub_id, {"data_folder": data_folder})
            except Exception as e:
                warn_user(f"結果ディレクトリの保存に失敗: {e}")

            # 解析レシートを確定（analysis_params.json + R サイドカーを 1 つに集約）。
            # 失敗しても解析完了表示は壊さない。
            try:
                if output_dir:
                    _receipt.finalize_receipt(
                        output_dir,
                        app_version=version_label(),
                        ended_at=datetime.now().isoformat(),
                    )
            except Exception as e:
                warn_user(f"解析レシートの作成に失敗: {e}")
        else:
            msg = "解析でエラーが発生しました"
            section_text = f"出力: {file_count} ファイル | ❌ エラー ({step_current}/{step_total}) | {elapsed_text}"
            log_header = "❌ エラー発生"

        return (
            styled_log, 100, "100%", section_text,
            app_state, True,  # Interval 無効化
            {"display": "none"},  # 停止ボタン非表示
            {"display": "none"},  # 進捗バー非表示
            log_header,
            msg, True,
        )

    if status == "stopped":
        _process_state["process"] = None
        _process_state["log_file_handle"] = None
        app_state["is_running"] = False
        elapsed_text = _format_elapsed_time(start_time_iso)
        section_text = f"出力: {file_count} ファイル | ⏹ 停止 ({step_current}/{step_total}) | {elapsed_text}"

        return (
            styled_log, progress, f"{progress}%", section_text,
            app_state, True,
            {"display": "none"},  # 停止ボタン非表示
            {"display": "none"},  # 進捗バー非表示
            "⏹ 解析停止",
            "解析を停止しました", True,
        )

    return (
        styled_log, progress, f"{progress}%", section_text,
        no_update, no_update,
        no_update,
        no_update,  # progress_container: 変更なし
        no_update,  # log_header: 変更なし
        no_update, no_update,
    )


# ---------------------------------------------------------------------------
# 解析停止
# ---------------------------------------------------------------------------

@callback(
    [Output("notification_toast", "children", allow_duplicate=True),
     Output("notification_toast", "is_open", allow_duplicate=True)],
    Input("stop_analysis", "n_clicks"),
    State("app_state", "data"),
    prevent_initial_call=True,
)
def handle_stop(n_clicks, app_state):
    if not n_clicks:
        return no_update, no_update

    process = _process_state.get("process")
    log_fh = _process_state.get("log_file_handle")
    output_dir = app_state.get("full_output_dir", "")

    stop_analysis_process(process, output_dir, log_fh)

    return "停止リクエストを送信しました", True


# ---------------------------------------------------------------------------
# RDS ファイル自動検出
# ---------------------------------------------------------------------------

# DESI/TIMS別のRDSファイル名マッピング
_RDS_FILE_NAMES = {
    "desi": {
        "harmony": "DESI_SeuratCombined_harmony.rds",
        "rpca": "DESI_SeuratCombined_RPCA.rds",
    },
    "tims": {
        "harmony": "Step2_HarmonyPCA_Result.rds",
        "rpca": "Step3_RPCA_Result.rds",
    },
}


@callback(
    [Output("rds_detection_badge", "children"),
     Output("cluster_source", "options"),
     Output("cluster_source", "value", allow_duplicate=True),
     Output("cluster_source_container", "style")],
    [Input("rds_folder_reanalysis", "value"),
     Input("analysis_method", "value"),
     Input("analysis_method_tims", "value")],
    prevent_initial_call=True,
)
def detect_rds_files(folder, desi_method, tims_method):
    """RDSフォルダ内のファイルをスキャンし、Harmony/RPCA選択肢を動的に更新する"""
    _default_options = [
        {"label": "Harmony/PCA", "value": "harmony"},
        {"label": "RPCA", "value": "rpca"},
    ]
    _hide = {"display": "none"}

    if not folder or not folder.strip():
        return "", _default_options, no_update, _hide

    p = Path(folder.strip())
    if not p.is_dir():
        return (
            html.Span("⚠ フォルダが見つかりません",
                       style={"color": "#dc3545", "fontSize": "0.8rem"}),
            _default_options, no_update, _hide,
        )

    # DESI/TIMSを判定
    instrument = "tims" if tims_method else "desi"
    names = _RDS_FILE_NAMES[instrument]

    has_harmony = (p / names["harmony"]).exists()
    has_rpca = (p / names["rpca"]).exists()

    if has_harmony and has_rpca:
        badge = html.Span(
            f"✓ {names['harmony']}, {names['rpca']} 検出",
            style={"color": "#28a745", "fontSize": "0.8rem"},
        )
        options = [
            {"label": "Harmony/PCA", "value": "harmony"},
            {"label": "RPCA", "value": "rpca"},
        ]
        return badge, options, no_update, {}

    if has_harmony:
        badge = html.Span(
            f"✓ {names['harmony']} 検出",
            style={"color": "#28a745", "fontSize": "0.8rem"},
        )
        options = [
            {"label": "Harmony/PCA", "value": "harmony"},
            {"label": "RPCA", "value": "rpca", "disabled": True},
        ]
        return badge, options, "harmony", {}

    if has_rpca:
        badge = html.Span(
            f"✓ {names['rpca']} 検出",
            style={"color": "#28a745", "fontSize": "0.8rem"},
        )
        options = [
            {"label": "Harmony/PCA", "value": "harmony", "disabled": True},
            {"label": "RPCA", "value": "rpca"},
        ]
        return badge, options, "rpca", {}

    # どちらも見つからない
    rds_files = list(p.glob("*.rds"))
    if rds_files:
        file_list = ", ".join(f.name for f in rds_files[:3])
        suffix = f" 他{len(rds_files)-3}件" if len(rds_files) > 3 else ""
        msg = f"⚠ 標準ファイル未検出（{file_list}{suffix}）"
    else:
        msg = "⚠ RDSファイルが見つかりません"
    return (
        html.Span(msg, style={"color": "#dc3545", "fontSize": "0.8rem"}),
        _default_options, no_update, _hide,
    )


# ---------------------------------------------------------------------------
# m/z キャリブレーション UI 表示切替
# ---------------------------------------------------------------------------

@callback(
    Output("calibration_detail_panel", "style"),
    Input("calibration_enable", "value"),
    prevent_initial_call=True,
)
def toggle_calibration_panel(enabled):
    if enabled:
        return {"display": "block", "marginTop": "10px",
                "padding": "10px", "background": "#f8f9fa",
                "borderRadius": "5px"}
    return {"display": "none"}


@callback(
    Output("calibration_table_data", "data"),
    [Input("calibration_matrix", "value"),
     Input("ion_mode", "value")],
    prevent_initial_call=True,
)
def update_calibration_table_on_matrix(matrix_type, ion_mode):
    """マトリックス種/イオンモード変更時にテーブルデータを初期化"""
    from app.config import MATRIX_REFERENCE_MZ
    if not matrix_type or matrix_type == "custom":
        return no_update
    polarity = ion_mode or "Positive"
    ref_list = MATRIX_REFERENCE_MZ.get(matrix_type, {}).get(polarity, [])
    if not ref_list:
        return []
    return [{"ref_mz": round(r, 4), "formula": "", "obs_mz": "",
             "ppm_drift": "--", "use": "Yes"} for r in ref_list]


@callback(
    [Output("calibration_table", "data"),
     Output("calibration_table", "selected_rows")],
    Input("calibration_table_data", "data"),
)
def sync_calibration_store_to_table(store_data):
    """Store → DataTable 同期（selected_rows も use フィールドから復元）"""
    data = store_data or []
    selected = [i for i, r in enumerate(data) if r.get("use") == "Yes"]
    return data, selected


@callback(
    Output("calibration_table_data", "data", allow_duplicate=True),
    Input("calibration_table", "selected_rows"),
    State("calibration_table", "data"),
    prevent_initial_call=True,
)
def sync_selection_to_use(selected_rows, table_data):
    """チェックボックス変更 → use フィールドを更新"""
    if table_data is None:
        return no_update
    selected_set = set(selected_rows or [])
    changed = False
    new_data = []
    for i, row in enumerate(table_data):
        new_use = "Yes" if i in selected_set else "No"
        if row.get("use") != new_use:
            changed = True
        new_data.append({**row, "use": new_use})
    if not changed:
        return no_update
    return new_data


@callback(
    Output("calibration_table_data", "data", allow_duplicate=True),
    Input("calibration_add_row", "n_clicks"),
    State("calibration_table", "data"),
    prevent_initial_call=True,
)
def add_calibration_row(n, data):
    """行追加ボタン"""
    if not n:
        return no_update
    data = list(data or [])
    data.append({"ref_mz": "", "formula": "", "obs_mz": "", "ppm_drift": "--", "use": "Yes"})
    return data


@callback(
    Output("calibration_table_data", "data", allow_duplicate=True),
    Input("calibration_delete_rows", "n_clicks"),
    [State("calibration_table", "selected_rows"),
     State("calibration_table", "data")],
    prevent_initial_call=True,
)
def delete_calibration_rows(n, selected, data):
    """チェックされた行を削除"""
    if not n or not selected or not data:
        return no_update
    return [r for i, r in enumerate(data) if i not in set(selected)]


@callback(
    [Output("calibration_table_data", "data", allow_duplicate=True),
     Output("calibration_status_text", "children")],
    Input("calibration_auto_detect", "n_clicks"),
    [State("calibration_table", "data"),
     State("calibration_search_window", "value"),
     State("seurat_cache_dir_store", "data"),
     State("data_folder", "value"),
     State("analysis_method", "value"),
     State("analysis_method_tims", "value"),
     State("cal_sample_selector", "value")],
    prevent_initial_call=True,
)
def auto_detect_observed_peaks(n, table_data, search_window, cache_dir,
                               data_folder, analysis_method, analysis_method_tims,
                               cal_sample_value):
    """リファレンスm/z値に対応する実測ピークを自動検出"""
    import numpy as np
    import pandas as pd
    from app.services.data_manager import read_raw_mz_spectrum

    if not n or not table_data:
        return no_update, no_update

    # サンプル指定: __all__ 以外ならそのサンプルのみ読込
    target_sample = cal_sample_value if cal_sample_value and cal_sample_value != "__all__" else None

    # --- データ読み込み: 優先順位 1) cache_dir  2) data_folder 生データ ---
    expr_df = None
    if cache_dir:
        # サンプル固有のキャッシュがあればそちらを優先
        if target_sample:
            sample_expr_path = Path(cache_dir) / f"{target_sample}_expression.parquet"
            if sample_expr_path.exists():
                try:
                    expr_df = pd.read_parquet(sample_expr_path)
                except Exception:
                    expr_df = None
        # サンプル固有キャッシュがなければ全サンプル結合データ
        if expr_df is None:
            expr_path = Path(cache_dir) / "expression_matrix.parquet"
            if expr_path.exists():
                try:
                    expr_df = pd.read_parquet(expr_path)
                except Exception:
                    expr_df = None

    if expr_df is None:
        # data_folder から生データを読み込むフォールバック
        is_tims = bool(analysis_method_tims)
        expr_df = read_raw_mz_spectrum(data_folder, is_tims=is_tims,
                                       sample_name=target_sample)

    if expr_df is None:
        return no_update, "⚠ データが見つかりません。データフォルダを確認してください。"

    # Feature名 → m/z数値マッピング
    mz_values = {}
    for col in expr_df.columns:
        match = re.search(r"(\d+\.?\d*)", col)
        if match:
            mz_values[col] = float(match.group(1))

    if not mz_values:
        return no_update, "⚠ m/z値を含むフィーチャーが見つかりません。"

    mz_array = np.array(list(mz_values.values()))
    feature_names = list(mz_values.keys())
    avg_spectrum = {f: float(expr_df[f].mean()) for f in feature_names}

    sw = float(search_window or 0.5)
    matched_count = 0
    updated_data = []

    for row in table_data:
        row = dict(row)  # コピー
        ref = row.get("ref_mz")
        if not ref or ref == "":
            updated_data.append(row)
            continue
        try:
            ref_f = float(ref)
        except (ValueError, TypeError):
            updated_data.append(row)
            continue

        within = np.where(np.abs(mz_array - ref_f) <= sw)[0]
        if len(within) == 0:
            row["obs_mz"] = ""
            row["ppm_drift"] = "--"
            updated_data.append(row)
            continue

        # ウィンドウ内で最大強度のピークを選択
        best_idx = max(within, key=lambda i: avg_spectrum.get(feature_names[i], 0))
        obs = float(mz_array[best_idx])
        ppm = (obs - ref_f) / ref_f * 1e6
        row["obs_mz"] = round(obs, 5)
        row["ppm_drift"] = f"{ppm:+.1f}"
        matched_count += 1
        updated_data.append(row)

    sample_label = target_sample or "全サンプル"
    status = f"✓ 検出完了 [{sample_label}]: {matched_count}/{len(table_data)} ピークがマッチしました"
    return updated_data, status


@callback(
    Output("calibration_table_data", "data", allow_duplicate=True),
    Input("calibration_table", "data_timestamp"),
    State("calibration_table", "data"),
    prevent_initial_call=True,
)
def recalculate_ppm_on_edit(ts, table_data):
    """テーブル編集時にppm driftを再計算してStoreに反映"""
    if not table_data:
        return no_update
    changed = False
    updated = []
    for row in table_data:
        row = dict(row)
        ref = row.get("ref_mz")
        obs = row.get("obs_mz")
        if ref and obs and str(ref).strip() and str(obs).strip():
            try:
                ref_f = float(ref)
                obs_f = float(obs)
                ppm = (obs_f - ref_f) / ref_f * 1e6
                new_drift = f"{ppm:+.1f}"
                if row.get("ppm_drift") != new_drift:
                    row["ppm_drift"] = new_drift
                    changed = True
            except (ValueError, TypeError):
                if row.get("ppm_drift") != "--":
                    row["ppm_drift"] = "--"
                    changed = True
        else:
            if row.get("ppm_drift") != "--":
                row["ppm_drift"] = "--"
                changed = True
        updated.append(row)
    return updated if changed else no_update


@callback(
    [Output("calibration_table_data", "data", allow_duplicate=True),
     Output("calibration_status_text", "children", allow_duplicate=True)],
    Input("calibration_reset_list", "n_clicks"),
    prevent_initial_call=True,
)
def reset_calibration_list(n):
    """リセットボタン: 直前のList保存状態に復元"""
    if not n:
        return no_update, no_update
    saved = load_last_settings()
    table_data = saved.get("calibration_table_data", [])
    if not table_data:
        return no_update, "保存データがありません"
    return table_data, "保存済みリストに復元しました ✓"


# =========================================================================
# キャリブレーション設定の自動保存
# =========================================================================
@callback(
    Output("calibration_save_trigger", "data"),
    [Input("calibration_enable", "value"),
     Input("calibration_matrix", "value"),
     Input("calibration_table_data", "data"),
     Input("calibration_search_window", "value"),
     Input("calibration_min_peaks", "value"),
     Input("calibration_regression_mode", "value")],
    prevent_initial_call=True,
)
def auto_save_calibration_settings(enable, matrix, table_data,
                                    search_window, min_peaks, regression_mode):
    """キャリブレーション関連の設定が変更されたら自動保存する"""
    save_last_settings({
        "calibration_enable": enable,
        "calibration_matrix": matrix,
        "calibration_table_data": table_data,
        "calibration_search_window": search_window,
        "calibration_min_peaks": min_peaks,
        "calibration_regression_mode": regression_mode,
    })
    return no_update


@callback(
    Output("calibration_status_text", "children", allow_duplicate=True),
    Input("calibration_save_list", "n_clicks"),
    [State("calibration_enable", "value"),
     State("calibration_matrix", "value"),
     State("calibration_table_data", "data"),
     State("calibration_search_window", "value"),
     State("calibration_min_peaks", "value"),
     State("calibration_regression_mode", "value")],
    prevent_initial_call=True,
)
def save_calibration_list(n, enable, matrix, table_data,
                          search_window, min_peaks, regression_mode):
    """List保存ボタン: キャリブレーション設定を明示的に保存"""
    if not n:
        return no_update
    save_last_settings({
        "calibration_enable": enable,
        "calibration_matrix": matrix,
        "calibration_table_data": table_data,
        "calibration_search_window": search_window,
        "calibration_min_peaks": min_peaks,
        "calibration_regression_mode": regression_mode,
    })
    return "リストを保存しました ✓"


# =========================================================================
# キャリブレーション プリセット (保存・読込・削除)
# =========================================================================
# =========================================================================
# C2-b: サンプル別キャリブレーション
# =========================================================================

@callback(
    Output("cal_sample_selector", "options"),
    Input("selected_samples_store", "data"),
    prevent_initial_call=True,
)
def update_cal_sample_options(selected_samples):
    """サンプルチェックリスト変更時にキャリブレーション対象ドロップダウンを更新"""
    opts = [{"label": "全サンプル共通", "value": "__all__"}]
    if selected_samples:
        for s in selected_samples:
            opts.append({"label": f"  {s}", "value": s})
    return opts


@callback(
    [Output("calibration_table_data", "data", allow_duplicate=True),
     Output("cal_per_sample_store", "data", allow_duplicate=True),
     Output("cal_sample_selector_prev", "data")],
    Input("cal_sample_selector", "value"),
    [State("cal_sample_selector_prev", "data"),
     State("calibration_table", "data"),
     State("cal_per_sample_store", "data")],
    prevent_initial_call=True,
)
def switch_cal_sample(new_sample, prev_sample, current_table, store):
    """キャリブレーション対象サンプル切替時にテーブルを保存/復元
    NOTE: State は calibration_table (DataTable) から読み取り（手動編集を含む最新値）、
    Output は calibration_table_data (Store) へ書き込み →
    sync_calibration_store_to_table 経由で DataTable に反映
    """
    store = dict(store or {})
    # 現在のテーブルデータを前のサンプルキーに保存
    if prev_sample and current_table is not None:
        store[prev_sample] = current_table
    # 新サンプルのデータをロード
    if new_sample in store and store[new_sample]:
        new_data = store[new_sample]
    elif "__all__" in store and store["__all__"]:
        # __all__ から ref_mz/formula を引き継ぎ、obs_mz はクリア
        new_data = [
            {**row, "obs_mz": "", "ppm_drift": "--"}
            for row in store["__all__"]
        ]
    else:
        new_data = current_table or []
    return new_data, store, new_sample


from app.services.calibration_preset_manager import (
    list_calibration_presets,
    save_calibration_preset,
    load_calibration_preset,
    delete_calibration_preset,
)


def _cal_preset_options():
    """ドロップダウン用の選択肢リストを生成"""
    presets = list_calibration_presets()
    return [
        {
            "label": f"{p['name']}  [{p['matrix']} / {p['ion_mode']}]",
            "value": p["name"],
        }
        for p in presets
    ]


@callback(
    [Output("calibration_table_data", "data", allow_duplicate=True),
     Output("calibration_regression_mode", "value", allow_duplicate=True),
     Output("calibration_search_window", "value", allow_duplicate=True),
     Output("calibration_min_peaks", "value", allow_duplicate=True),
     Output("cal_preset_status", "children"),
     Output("cal_per_sample_store", "data", allow_duplicate=True),
     Output("cal_sample_selector", "value", allow_duplicate=True)],
    Input("cal_preset_select", "value"),
    State("ion_mode", "value"),
    prevent_initial_call=True,
)
def load_cal_preset(preset_name, current_ion_mode):
    """プリセット選択時に即座にテーブル・設定を復元
    NOTE: calibration_matrix は出力しない（変更すると
    update_calibration_table_on_matrix が発火しテーブルがリセットされるため）
    """
    if not preset_name:
        return [no_update] * 4 + ["", no_update, no_update]
    p = load_calibration_preset(preset_name)
    if not p:
        return [no_update] * 4 + ["⚠ プリセットが見つかりません", no_update, no_update]

    matrix_info = p.get("matrix", "?")
    ion_info = p.get("ion_mode", "?")
    status = f"✓ 「{preset_name}」を読み込みました ({matrix_info} / {ion_info})"
    if p.get("ion_mode") and p["ion_mode"] != current_ion_mode:
        status += f"  ⚠ イオンモード不一致 (現在: {current_ion_mode})"

    # per_sample_data があればStoreに復元、セレクターを __all__ にリセット
    per_sample = p.get("per_sample_data", {})
    table_data = p.get("calibration_table_data", [])
    # __all__ のテーブルデータを表示
    if per_sample and "__all__" in per_sample:
        table_data = per_sample["__all__"]

    return [
        table_data,
        p.get("regression_mode", no_update),
        p.get("search_window", no_update),
        p.get("min_peaks", no_update),
        status,
        per_sample,
        "__all__",
    ]


@callback(
    [Output("cal_preset_select", "options"),
     Output("cal_preset_status", "children", allow_duplicate=True),
     Output("cal_preset_name_input", "value")],
    Input("cal_preset_save_btn", "n_clicks"),
    [State("cal_preset_name_input", "value"),
     State("calibration_matrix", "value"),
     State("ion_mode", "value"),
     State("calibration_table_data", "data"),
     State("calibration_regression_mode", "value"),
     State("calibration_search_window", "value"),
     State("calibration_min_peaks", "value"),
     State("cal_per_sample_store", "data"),
     State("cal_sample_selector_prev", "data")],
    prevent_initial_call=True,
)
def save_cal_preset(n, name, matrix, ion_mode, table_data,
                    reg_mode, window, min_peaks,
                    cal_per_sample_store, cal_sample_selector_prev):
    """プリセット保存（サンプル別データ含む）"""
    if not n:
        return no_update, no_update, no_update
    if not name or not name.strip():
        return no_update, "⚠ プリセット名を入力してください", no_update
    # 現在表示中のテーブルを Store に同期してから保存
    per_store = dict(cal_per_sample_store or {})
    if cal_sample_selector_prev:
        per_store[cal_sample_selector_prev] = table_data or []
    save_calibration_preset(name.strip(), {
        "matrix": matrix,
        "ion_mode": ion_mode,
        "calibration_table_data": table_data or [],
        "per_sample_data": per_store,
        "regression_mode": reg_mode,
        "search_window": window,
        "min_peaks": min_peaks,
    })
    return _cal_preset_options(), f"✓ 「{name.strip()}」を保存しました", ""


@callback(
    [Output("cal_preset_select", "options", allow_duplicate=True),
     Output("cal_preset_select", "value", allow_duplicate=True),
     Output("cal_preset_status", "children", allow_duplicate=True)],
    Input("cal_preset_delete_btn", "n_clicks"),
    State("cal_preset_select", "value"),
    prevent_initial_call=True,
)
def delete_cal_preset(n, preset_name):
    """プリセット削除"""
    if not n:
        return no_update, no_update, no_update
    if not preset_name:
        return no_update, no_update, "⚠ 削除するプリセットを選択してください"
    delete_calibration_preset(preset_name)
    return _cal_preset_options(), None, f"✓ 「{preset_name}」を削除しました"


# =========================================================================
# C3: リアルタイム入力バリデーション
# =========================================================================

from app.services.data_manager import (
    validate_data_folder, validate_rds_folder,
    validate_output_dir, validate_numeric_param,
    validate_msi_file, list_msi_files,
)

import dash_bootstrap_components as dbc
from dash import html


def _badge(result: dict) -> html.Span:
    """バリデーション結果を色付きバッジに変換する。"""
    if result["ok"]:
        return html.Span(
            f"✓ {result['msg']}",
            style={"color": "#28a745", "fontSize": "0.8rem"},
        )
    return html.Span(
        f"✗ {result['msg']}",
        style={"color": "#dc3545", "fontSize": "0.8rem"},
    )


@callback(
    Output("data_folder_badge", "children"),
    Input("data_folder", "value"),
    State("analysis_method_tims", "value"),
)
def validate_data_folder_input(folder, tims_method):
    if not folder or not folder.strip():
        return ""
    is_tims = bool(tims_method)
    result = validate_data_folder(folder, is_tims=is_tims)
    if not result["ok"]:
        return _badge(result)
    # フォルダOKの場合、最初のMSIファイルの形式も検証（DESI .txt のみ）
    if not is_tims:
        from pathlib import Path
        txt_files = sorted(Path(folder).glob("*.txt"))
        if txt_files:
            file_result = validate_msi_file(str(txt_files[0]))
            if not file_result["valid"]:
                return html.Span(
                    f"⚠ {result['count']} ファイル検出 (形式警告: {file_result['message']})",
                    style={"color": "#e67e22", "fontSize": "0.8rem"},
                )
    return _badge(result)


@callback(
    Output("rds_folder_badge", "children"),
    Input("rds_folder", "value"),
)
def validate_rds_folder_input(folder):
    if not folder or not folder.strip():
        return ""
    result = validate_rds_folder(folder)
    return _badge(result)


@callback(
    Output("output_dir_badge", "children"),
    Input("output_dir", "value"),
)
def validate_output_dir_input(folder):
    if not folder or not folder.strip():
        return ""
    result = validate_output_dir(folder)
    return _badge(result)


@callback(
    Output("validation_summary", "children"),
    Output("validation_summary", "style"),
    Input("run_analysis", "n_clicks"),
    [State("analysis_method", "value"),
     State("analysis_method_tims", "value"),
     State("data_folder", "value"),
     State("reanalysis_data_folder", "value"),
     State("output_dir", "value"),
     State("p_thresh", "value"),
     State("logfc_thresh", "value"),
     State("tolerance_mz", "value"),
     State("resume_rds", "value"),
     State("rds_folder", "value"),
     State("rds_folder_reanalysis", "value")],
    prevent_initial_call=True,
)
def preflight_validation(
    n_clicks, desi_method, tims_method,
    data_folder, reanalysis_data_folder, output_dir,
    p_thresh, logfc_thresh, tolerance_mz,
    resume_rds, rds_folder, rds_folder_reanalysis,
):
    """解析実行ボタン押下時にプリフライトチェックを実行する。
    問題がなければ非表示のまま。問題があればエラー一覧を表示。"""
    if not n_clicks:
        return "", {"display": "none"}

    errors = []
    analysis_type = desi_method or tims_method or "desi_v8"
    is_tims = bool(tims_method)
    is_reanalysis = analysis_type in ("desi_cluster_filter", "tims_cluster_filter")

    if is_reanalysis:
        # 再解析: reanalysis_data_folder を検証
        r = validate_data_folder(reanalysis_data_folder, is_tims=is_tims)
        if not r["ok"]:
            errors.append(f"データフォルダ: {r['msg']}")

        # 再解析: RDSフォルダを検証
        r = validate_rds_folder(rds_folder_reanalysis)
        if not r["ok"]:
            errors.append(f"RDSフォルダ: {r['msg']}")
    else:
        # UMAP解析: data_folder を検証
        r = validate_data_folder(data_folder, is_tims=is_tims)
        if not r["ok"]:
            errors.append(f"データフォルダ: {r['msg']}")

        # RDSフォルダ (途中再開時のみ)
        if resume_rds:
            r = validate_rds_folder(rds_folder)
            if not r["ok"]:
                errors.append(f"RDSフォルダ: {r['msg']}")

    # 出力先
    r = validate_output_dir(output_dir)
    if not r["ok"]:
        errors.append(f"出力先: {r['msg']}")

    # 数値パラメータ
    for val, name, lo, hi in [
        (p_thresh, "p値閾値", 0, 1),
        (logfc_thresh, "log2FC閾値", 0, None),
        (tolerance_mz, "m/z許容誤差", 0, None),
    ]:
        if val is not None and val != "":
            r = validate_numeric_param(val, name, min_val=lo, max_val=hi)
            if not r["ok"]:
                errors.append(r["msg"])

    if not errors:
        return "", {"display": "none"}

    items = [html.Li(e, style={"fontSize": "0.85rem"}) for e in errors]
    alert = dbc.Alert(
        children=[
            html.Strong("入力チェックでエラーが見つかりました:"),
            html.Ul(items, style={"marginBottom": 0, "marginTop": "5px"}),
        ],
        color="danger",
        dismissable=True,
        style={"marginBottom": "10px"},
    )
    return alert, {"display": "block"}


# ---------------------------------------------------------------------------
# 再解析: キャリブレーション回帰情報の自動読込
# ---------------------------------------------------------------------------

@callback(
    [Output("reanalysis_calibration_data", "data"),
     Output("reanalysis_calibration_details", "children"),
     Output("reanalysis_calibration_info", "style"),
     Output("reanalysis_calibration_use_previous", "value", allow_duplicate=True)],
    Input("rds_folder_reanalysis", "value"),
    prevent_initial_call=True,
)
def load_calibration_from_first_analysis(rds_folder):
    """RDSフォルダの親ディレクトリから analysis_params.json を読み、
    キャリブレーション回帰情報を自動表示する。"""
    import json as _json

    _no_data = (None, "", {"display": "none"}, False)

    if not rds_folder or not rds_folder.strip():
        return _no_data

    rds_path = Path(rds_folder.strip())
    if not rds_path.is_dir():
        return _no_data

    # analysis_params.json を上位3階層まで探索
    params_data = None
    source_dir = None
    search_path = rds_path
    for _ in range(4):
        candidate = search_path / "analysis_params.json"
        if candidate.is_file():
            try:
                params_data = _json.loads(candidate.read_text(encoding="utf-8"))
                source_dir = str(search_path)
                break
            except Exception:
                pass
        search_path = search_path.parent
        if search_path == search_path.parent:
            break

    if not params_data:
        return _no_data

    # キャリブレーション情報を抽出
    cal_enable = params_data.get("calibration_enable", False)
    cal_coefficients = params_data.get("calibration_coefficients")

    if not cal_enable or not cal_coefficients:
        return _no_data

    # 回帰情報をまとめる
    cal_data = {
        "coefficients": cal_coefficients,
        "degree": params_data.get("calibration_degree"),
        "r_squared": params_data.get("calibration_r_squared"),
        "n_points": params_data.get("calibration_n_points"),
        "regression_mode": params_data.get("calibration_regression_mode"),
    }

    # 表示用テキスト
    mode = cal_data.get("regression_mode", "?")
    degree = cal_data.get("degree", "?")
    r2 = cal_data.get("r_squared", "?")
    n_pts = cal_data.get("n_points", "?")

    detail_children = [
        html.Div("✅ 前回の解析から回帰式を検出:",
                 style={"fontWeight": "bold", "marginBottom": "5px"}),
        html.Div(f"回帰モデル: {mode} (次数: {degree})"),
        html.Div(f"R²: {r2}  |  マッチピーク数: {n_pts}"),
    ]

    # 使用ピーク一覧を表示
    cal_table = params_data.get("calibration_table", [])
    used_rows = [r for r in cal_table if r.get("use") == "Yes"]
    if used_rows:
        peak_lines = []
        for row in used_rows:
            ref = row.get("ref_mz", "")
            obs = row.get("obs_mz", "")
            ppm = row.get("ppm_drift", "--")
            formula = row.get("formula", "")
            label = f"  {ref} → {obs}  ({ppm} ppm)"
            if formula:
                label += f"  [{formula}]"
            peak_lines.append(html.Div(label, style={"fontFamily": "monospace"}))
        detail_children.append(
            html.Div([
                html.Div("使用ピーク:", style={"marginTop": "8px", "fontWeight": "bold"}),
                *peak_lines,
            ])
        )

    detail_children.append(
        html.Div(f"参照元: {source_dir}",
                 style={"color": "#666", "fontSize": "12px", "marginTop": "5px"}),
    )

    details = html.Div(detail_children)

    return (
        cal_data,
        details,
        {"display": "block", "marginTop": "5px"},
        True,  # チェックボックスを自動ON
    )


@callback(
    [Output("reanalysis_calibration_info", "style", allow_duplicate=True),
     Output("reanalysis_calibration_details", "children", allow_duplicate=True)],
    Input("reanalysis_calibration_use_previous", "value"),
    [State("reanalysis_calibration_data", "data"),
     State("rds_folder_reanalysis", "value")],
    prevent_initial_call=True,
)
def toggle_reanalysis_calibration_panel(checked, cal_data, rds_folder):
    """チェックボックス操作時にキャリブレーション情報パネルの表示を制御する。"""
    if not checked:
        return {"display": "none"}, no_update
    if cal_data:
        # データ読込済み → パネル表示（内容は load_calibration で設定済み）
        return {"display": "block", "marginTop": "5px"}, no_update
    # チェック ON だがデータなし → ガイドメッセージ
    if not rds_folder or not rds_folder.strip():
        msg = "RDSフォルダを指定すると、前回のキャリブレーション情報を自動検出します。"
    else:
        msg = "指定されたRDSフォルダから前回のキャリブレーション情報が見つかりませんでした。"
    return (
        {"display": "block", "marginTop": "5px"},
        html.Div(msg, style={"color": "#888", "fontSize": "13px"}),
    )
