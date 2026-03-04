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
)
from app.services.analysis_runner import (
    generate_v8_config,
    generate_cluster_filter_config,
    start_analysis_process,
    get_analysis_log,
    get_analysis_status,
    check_process_completion,
    stop_analysis_process,
)
from app.services.session_manager import save_last_settings
from app.services.project_manager import save_sub_project_settings, save_sub_project_result_dir


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
    [State("analysis_method", "value"),
     State("analysis_method_tims", "value"),
     State("data_folder", "value"),
     State("mrm_path", "value"),
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
     State("desi_v8_script_path", "value"),
     State("desi_cluster_filter_script_path", "value"),
     State("tims_v8_script_path", "value"),
     State("tims_cluster_filter_script_path", "value"),
     State("app_state", "data"),
     State("selected_project", "data"),
     State("current_sub_project_id", "data")],
    prevent_initial_call=True,
)
def run_analysis(
    n_clicks,
    desi_method, tims_method,
    data_folder, mrm_path, p_thresh, logfc_thresh,
    resume_rds, rds_folder,
    output_subfolder, output_dir,
    rds_path, reanalysis_data_folder, filter_mode, target_clusters,
    ion_mode, tolerance_mz, adduct_filter,
    reanalysis_p_thresh, reanalysis_logfc_thresh,
    reanalysis_ion_mode, reanalysis_tolerance_mz, reanalysis_adduct_filter,
    annotation_csv,
    desi_v8_script, desi_cluster_script,
    tims_v8_script, tims_cluster_script,
    app_state,
    selected_project, current_sub_project_id,
):
    if not n_clicks:
        return no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update

    # 現在の設定を自動保存（次回起動時に復元される）
    try:
        save_last_settings({
            "analysis_method": desi_method,
            "analysis_method_tims": tims_method,
            "data_folder": data_folder,
            "mrm_path": mrm_path,
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
        })
    except Exception:
        pass  # 保存失敗しても解析は続行

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
                    "mrm_path": mrm_path,
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
    except Exception:
        pass  # 保存失敗しても解析は続行

    analysis_type = desi_method or tims_method or "desi_v8"
    full_output_dir = str(Path(output_dir) / output_subfolder)
    Path(full_output_dir).mkdir(parents=True, exist_ok=True)

    try:
        if analysis_type in ("desi_v8", "tims_v8"):
            # UMAP解析
            if analysis_type == "desi_v8":
                template = desi_v8_script or str(DESI_V8_TEMPLATE_PATH)
            else:
                template = tims_v8_script or str(TIMS_V8_TEMPLATE_PATH)

            # サンプル名は data_folder 内のファイル名（UI上の選択状態は
            # selected_samples チェックリストから取得すべきだが、
            # Dash の動的コンポーネントの制約により、ここでは data_folder 全体を対象にする）
            if analysis_type == "tims_v8":
                from app.services.data_manager import list_tims_files
                sample_names = list_tims_files(data_folder)
            else:
                from app.services.data_manager import list_msi_files
                sample_names = list_msi_files(data_folder)

            params = {
                "template_path": template,
                "data_folder": data_folder,
                "sample_names": sample_names,
                "mrm_path": mrm_path or "",
                "p_thresh": float(p_thresh) if p_thresh else 0.05,
                "logfc_thresh": float(logfc_thresh) if logfc_thresh else 0.10,
                "resume_from_rds": bool(resume_rds),
                "resume_rds_paths": [],
            }

            if resume_rds and rds_folder:
                rds_files = sorted(Path(rds_folder).glob("*.rds"))
                params["resume_rds_paths"] = [str(f) for f in rds_files]

            # TIMS固有パラメータ
            if analysis_type == "tims_v8":
                params["ion_mode"] = ion_mode or "Positive"
                params["tolerance_mz"] = float(tolerance_mz) if tolerance_mz else 0.01
                if adduct_filter:
                    params["adduct_patterns"] = adduct_filter
                # INPUT_PATHS: data_folder内のファイルのフルパスリスト
                from app.services.data_manager import build_tims_input_paths
                params["input_paths"] = build_tims_input_paths(data_folder)
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

            params = {
                "template_path": template,
                "rds_path": rds_path or "",
                "original_data_folder": reanalysis_data_folder or data_folder,
                "filter_mode": filter_mode or "exclude",
                "target_clusters": clusters,
                "sample_names": sample_names,
            }

            # TIMSクラスターフィルター固有パラメータ
            if analysis_type == "tims_cluster_filter":
                from app.services.data_manager import build_tims_input_paths
                src_folder = reanalysis_data_folder or data_folder
                params["original_input_paths"] = build_tims_input_paths(src_folder)
                params["export_data_dir"] = full_output_dir
                # RDS_RUN_DIR: rds_pathの親ディレクトリから推定
                if rds_path:
                    params["rds_run_dir"] = str(Path(rds_path).parent)

            config_path = generate_cluster_filter_config(params, full_output_dir)

        # サブプロセス開始
        result = start_analysis_process(config_path, full_output_dir)

        if not result["success"]:
            return (
                app_state, True,
                {"display": "none"}, {"display": "none"}, {"display": "none"},
                no_update,
                f"解析開始に失敗: {result['message']}", True,
            )

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
            "解析を開始しました", True,
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
    State("app_state", "data"),
    prevent_initial_call=True,
)
def update_progress(n_intervals, app_state):
    if not app_state or not app_state.get("is_running"):
        return (no_update,) * 11

    log_file = app_state.get("log_file")
    status_file = app_state.get("status_file")
    output_dir = app_state.get("full_output_dir")
    start_time_iso = app_state.get("start_time", "")
    analysis_type = app_state.get("analysis_type", "desi_v8")

    # ログ取得
    log_text = get_analysis_log(log_file, last_n=50) if log_file else ""

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

    # ステップ検出（ステップ定義リストに基づく）
    step_current, step_total, step_name = _detect_current_step(log_text, analysis_type)

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
            except Exception:
                pass
        else:
            msg = "解析でエラーが発生しました"
            section_text = f"出力: {file_count} ファイル | ❌ エラー ({step_current}/{step_total}) | {elapsed_text}"
            log_header = "❌ エラー発生"

        return (
            log_text, 100, "100%", section_text,
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
            log_text, progress, f"{progress}%", section_text,
            app_state, True,
            {"display": "none"},  # 停止ボタン非表示
            {"display": "none"},  # 進捗バー非表示
            "⏹ 解析停止",
            "解析を停止しました", True,
        )

    return (
        log_text, progress, f"{progress}%", section_text,
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
     State("analysis_method_tims", "value")],
    prevent_initial_call=True,
)
def auto_detect_observed_peaks(n, table_data, search_window, cache_dir,
                               data_folder, analysis_method, analysis_method_tims):
    """リファレンスm/z値に対応する実測ピークを自動検出"""
    import numpy as np
    import pandas as pd
    from app.services.data_manager import read_raw_mz_spectrum

    if not n or not table_data:
        return no_update, no_update

    # --- データ読み込み: 優先順位 1) cache_dir  2) data_folder 生データ ---
    expr_df = None
    if cache_dir:
        expr_path = Path(cache_dir) / "expression_matrix.parquet"
        if expr_path.exists():
            try:
                expr_df = pd.read_parquet(expr_path)
            except Exception:
                expr_df = None

    if expr_df is None:
        # data_folder から生データを読み込むフォールバック
        is_tims = bool(analysis_method_tims)
        expr_df = read_raw_mz_spectrum(data_folder, is_tims=is_tims)

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

    status = f"✓ 検出完了: {matched_count}/{len(table_data)} ピークがマッチしました"
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
