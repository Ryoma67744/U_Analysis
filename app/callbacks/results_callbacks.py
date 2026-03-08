# =============================================================================
# MSI Analysis Application - Results Callbacks
# 結果閲覧 コールバック
# =============================================================================

import base64
from pathlib import Path

from dash import Input, Output, State, callback, ctx, no_update, html, ALL
import dash_bootstrap_components as dbc

from app.services.data_manager import list_result_folders
from app.services.results_viewer import (
    categorize_image, get_available_clusters,
    sort_images_by_time, filter_images_by_cluster,
)
from app.services.notify import warn_user


# ---------------------------------------------------------------------------
# 結果フォルダ一覧
# ---------------------------------------------------------------------------

@callback(
    Output("result_folder_selector", "options", allow_duplicate=True),
    [Input("main_tabs", "active_tab"),
     Input("output_dir", "value")],
    prevent_initial_call=True,
)
def update_result_folders(active_tab, output_dir):
    if active_tab != "results" or not output_dir:
        return []
    folders = list_result_folders(output_dir)
    return [{"label": f["name"], "value": f["path"]} for f in folders]


# ---------------------------------------------------------------------------
# サブフォルダ一覧
# ---------------------------------------------------------------------------

@callback(
    Output("subfolder_selector", "options"),
    Input("result_folder_selector", "value"),
)
def update_subfolders(result_folder):
    if not result_folder or not Path(result_folder).is_dir():
        return []

    options = [{"label": "(ルート)", "value": ""}]
    root = Path(result_folder)

    # 最大深さ2で再帰
    for d in sorted(root.rglob("*")):
        if d.is_dir():
            rel = d.relative_to(root)
            if len(rel.parts) <= 2:
                options.append({"label": str(rel), "value": str(rel)})
    return options


# ---------------------------------------------------------------------------
# クラスタ一覧
# ---------------------------------------------------------------------------

@callback(
    Output("cluster_selector", "options"),
    Input("result_folder_selector", "value"),
)
def update_clusters(result_folder):
    if not result_folder:
        return []
    clusters = get_available_clusters(result_folder)
    options = [{"label": "すべて", "value": ""}]
    for c in clusters:
        options.append({"label": f"Cluster {c}", "value": str(c)})
    return options


# ---------------------------------------------------------------------------
# 画像ギャラリー
# ---------------------------------------------------------------------------

@callback(
    [Output("image_gallery", "children"),
     Output("page_info", "children")],
    [Input("result_folder_selector", "value"),
     Input("subfolder_selector", "value"),
     Input("image_category", "value"),
     Input("cluster_selector", "value"),
     Input("gallery_page_store", "data"),
     Input("progress_interval", "n_intervals")],
    State("app_state", "data"),
)
def render_gallery(result_folder, subfolder, category, cluster, page, n_intervals, app_state):
    # progress_interval トリガー時は解析実行中のみ再描画
    if ctx.triggered_id == "progress_interval":
        if not app_state or not app_state.get("is_running"):
            return no_update, no_update
    if not result_folder or not Path(result_folder).is_dir():
        return [html.Div("結果フォルダを選択してください", className="text-muted p-4")], ""

    # 画像を収集
    target = Path(result_folder)
    if subfolder:
        target = target / subfolder

    extensions = {".png", ".jpg", ".jpeg"}
    images = [str(f) for f in target.rglob("*") if f.suffix.lower() in extensions]

    # カテゴリフィルタ
    if category and category != "all":
        images = [img for img in images if categorize_image(img) == category]

    # クラスタフィルタ
    if cluster:
        images = filter_images_by_cluster(images, int(cluster))

    # 時間順ソート
    images = sort_images_by_time(images)

    if not images:
        return [html.Div("画像が見つかりません", className="text-muted p-4")], ""

    # ページネーション
    per_page = 20
    total_pages = max(1, -(-len(images) // per_page))
    current_page = min(page or 1, total_pages)
    start = (current_page - 1) * per_page
    page_images = images[start:start + per_page]

    # 画像カードを生成
    cards = []
    for img_path in page_images:
        p = Path(img_path)
        # Base64エンコードで画像を埋め込む（Flask route不要）
        try:
            with open(img_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()
            src = f"data:image/png;base64,{img_data}"
        except Exception as e:
            print(f"[WARNING] 画像読み込み失敗 ({p.name}): {e}")
            src = ""

        cards.append(
            html.Div(
                className="image-item",
                children=[
                    html.Img(src=src, style={"width": "100%", "height": "180px",
                                              "objectFit": "contain", "background": "#f8f9fa"}),
                    html.Div(className="caption", children=p.name, title=str(p)),
                ],
                id={"type": "gallery_image", "path": str(p)},
                n_clicks=0,
            )
        )

    page_info = f"{current_page} / {total_pages} ({len(images)} 枚)"
    return cards, page_info


# ---------------------------------------------------------------------------
# ページネーション
# ---------------------------------------------------------------------------

@callback(
    Output("gallery_page_store", "data"),
    [Input("prev_page", "n_clicks"),
     Input("next_page", "n_clicks")],
    State("gallery_page_store", "data"),
    prevent_initial_call=True,
)
def handle_pagination(prev_clicks, next_clicks, current_page):
    triggered = ctx.triggered_id
    page = current_page or 1
    if triggered == "prev_page":
        return max(1, page - 1)
    elif triggered == "next_page":
        return page + 1
    return page


# ---------------------------------------------------------------------------
# 画像クリック → モーダル表示
# ---------------------------------------------------------------------------

@callback(
    [Output("image_modal", "is_open"),
     Output("modal_body", "children"),
     Output("modal_filename", "children"),
     Output("clicked_image_store", "data")],
    Input({"type": "gallery_image", "path": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def open_image_modal(clicks):
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update, no_update, no_update, no_update

    img_path = ctx.triggered_id["path"]
    p = Path(img_path)

    try:
        with open(img_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode()
        src = f"data:image/png;base64,{img_data}"
    except Exception as e:
        warn_user(f"画像の読み込みに失敗: {e}")
        return True, html.Div("画像を読み込めません"), p.name, img_path

    return (
        True,
        html.Img(src=src, className="modal-image",
                  style={"maxWidth": "90vw", "maxHeight": "80vh", "objectFit": "contain"}),
        p.name,
        img_path,
    )


# ---------------------------------------------------------------------------
# パスコピーボタン
# ---------------------------------------------------------------------------

@callback(
    [Output("notification_toast", "children", allow_duplicate=True),
     Output("notification_toast", "is_open", allow_duplicate=True)],
    Input("copy_path_btn", "n_clicks"),
    State("clicked_image_store", "data"),
    prevent_initial_call=True,
)
def copy_image_path(n, path):
    if not n or not path:
        return no_update, no_update
    # クリップボードコピーはクライアントサイドで行う必要があるが、
    # ここではパスを通知に表示する
    return f"パス: {path}", True


# ---------------------------------------------------------------------------
# ファイルブラウザ → 結果フォルダ Dropdown 連携
# ---------------------------------------------------------------------------

@callback(
    [Output("result_folder_selector", "options", allow_duplicate=True),
     Output("result_folder_selector", "value", allow_duplicate=True)],
    Input("result_folder_manual", "data"),
    State("result_folder_selector", "options"),
    prevent_initial_call=True,
)
def apply_manual_result_folder(manual_path, current_options):
    """隠しInputに設定されたパスをDropdownに反映する"""
    if not manual_path or not Path(manual_path).is_dir():
        return no_update, no_update
    current_options = current_options or []
    existing_values = {opt["value"] for opt in current_options}
    if manual_path not in existing_values:
        current_options.append({"label": Path(manual_path).name, "value": manual_path})
    return current_options, manual_path


# ---------------------------------------------------------------------------
# プロジェクト / サブプロジェクト選択コールバック
# ---------------------------------------------------------------------------

@callback(
    Output("results_project_select", "options"),
    [Input("main_tabs", "active_tab"),
     Input("current_page", "data")],
    prevent_initial_call=True,
)
def populate_results_projects(active_tab, current_page):
    """resultsタブがアクティブになった時にプロジェクト一覧を取得"""
    if current_page != "analysis" or active_tab != "results":
        return no_update
    from app.services.project_manager import list_projects
    projects = list_projects()
    return [{"label": p["name"], "value": p["id"]} for p in projects]


@callback(
    [Output("results_sub_project_select", "options", allow_duplicate=True),
     Output("results_sub_project_select", "value", allow_duplicate=True)],
    Input("results_project_select", "value"),
    prevent_initial_call=True,
)
def populate_results_sub_projects(project_id):
    """プロジェクト選択時にサブプロジェクト一覧を取得し、先頭を自動選択"""
    if not project_id:
        return [], None
    from app.services.project_manager import list_sub_projects
    subs = list_sub_projects(project_id)
    options = [{"label": s["name"], "value": s["id"]} for s in subs]
    first_value = options[0]["value"] if options else None
    return options, first_value


@callback(
    [Output("result_folder_manual", "data", allow_duplicate=True),
     Output("results_project_info", "children", allow_duplicate=True)],
    Input("results_sub_project_select", "value"),
    State("results_project_select", "value"),
    prevent_initial_call=True,
)
def set_results_folder_from_sub_project(sub_id, project_id):
    """サブプロジェクト選択時に結果フォルダを自動設定"""
    if not sub_id or not project_id:
        return no_update, no_update
    from app.services.project_manager import get_sub_project
    sub = get_sub_project(project_id, sub_id)
    if not sub:
        return no_update, no_update
    result_dir = sub.get("last_result_dir") or sub.get("output_dir", "")
    msg = "⚠ 結果フォルダが未設定です" if not result_dir else ""
    return result_dir, msg


# ---------------------------------------------------------------------------
# 解析パラメータ表示 (C1)
# ---------------------------------------------------------------------------

@callback(
    Output("result_params_display", "children"),
    [Input("result_folder_selector", "value"),
     Input("subfolder_selector", "value")],
    prevent_initial_call=True,
)
def display_analysis_params(result_folder, subfolder):
    """結果フォルダ内の analysis_params.json を読み込んで表示"""
    import json

    if not result_folder:
        return html.Span("結果フォルダを選択してください", className="text-muted")

    # サブフォルダが指定されている場合はそちらを優先
    folder = Path(subfolder) if subfolder else Path(result_folder)
    params_file = folder / "analysis_params.json"

    # 親フォルダも探索
    if not params_file.exists():
        params_file = Path(result_folder) / "analysis_params.json"
    if not params_file.exists():
        return html.Span("パラメータ情報なし", className="text-muted")

    try:
        params = json.loads(params_file.read_text(encoding="utf-8"))
    except Exception as e:
        warn_user(f"解析パラメータの読み込みに失敗: {e}")
        return html.Span("パラメータファイル読み込みエラー", className="text-danger")

    # パラメータをテーブル形式で表示
    # 表示用ラベルマッピング
    label_map = {
        "analysis_type": "解析タイプ",
        "data_folder": "データフォルダ",
        "output_dir": "出力フォルダ",
        "annotation_path": "アノテーションファイル",
        "annotation_csv": "アノテーションCSV",
        "ion_mode": "イオンモード",
        "tolerance_mz": "m/z許容誤差",
        "adduct_filter": "付加イオン",
        "p_thresh": "DEG p値閾値",
        "logfc_thresh": "DEG LogFC閾値",
        "calibration_enable": "キャリブレーション",
        "calibration_matrix": "マトリックス",
        "calibration_regression": "回帰モード",
        "timestamp": "実行日時",
        "filter_mode": "フィルタモード",
        "target_clusters": "対象クラスタ",
    }

    rows = []
    for key, value in params.items():
        label = label_map.get(key, key)
        # 値の表示を整形
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        elif isinstance(value, bool):
            value = "有効" if value else "無効"
        elif isinstance(value, dict):
            continue  # ネストした辞書はスキップ
        rows.append(html.Tr([
            html.Td(label, style={"fontWeight": "bold", "paddingRight": "15px",
                                  "whiteSpace": "nowrap", "verticalAlign": "top"}),
            html.Td(str(value), style={"wordBreak": "break-all"}),
        ]))

    if not rows:
        return html.Span("パラメータ情報なし", className="text-muted")

    return html.Table(
        rows,
        style={"width": "100%", "borderCollapse": "collapse"},
        className="table table-sm table-borderless",
    )


# ---------------------------------------------------------------------------
# 過去ログ閲覧 (E3)
# ---------------------------------------------------------------------------

def _find_log_file(result_folder, subfolder=None):
    """結果フォルダからログファイルのパスを検索"""
    if not result_folder:
        return None
    base = Path(result_folder)
    if subfolder:
        base = base / subfolder
    # 直下の log/ を探す
    log_path = base / "log" / "analysis_log.txt"
    if log_path.exists():
        return log_path
    # サブフォルダ内の log/ を探す（rglob フォールバック）
    for p in base.rglob("analysis_log.txt"):
        return p
    return None


@callback(
    Output("past_log_modal", "is_open"),
    Output("past_log_content", "children"),
    Input("view_past_log_btn", "n_clicks"),
    [State("result_folder_selector", "value"),
     State("subfolder_selector", "value")],
    prevent_initial_call=True,
)
def open_past_log(n_clicks, result_folder, subfolder):
    """過去の解析ログをモーダルに表示"""
    if not n_clicks:
        return no_update, no_update

    log_path = _find_log_file(result_folder, subfolder)
    if not log_path:
        return True, [html.P("ログファイルが見つかりません", className="text-muted")]

    from app.services.analysis_runner import format_log_lines_styled
    try:
        log_text = log_path.read_text(encoding="utf-8")
    except Exception as e:
        warn_user(f"ログファイルの読み込みに失敗: {e}")
        return True, [html.P("ログファイルの読み込みに失敗しました", className="text-muted")]

    styled = format_log_lines_styled(log_text)
    if not styled:
        styled = [html.P("ログが空です", className="text-muted")]
    return True, styled


@callback(
    Output("past_log_content", "children", allow_duplicate=True),
    [Input("past_log_search", "value"),
     Input("past_log_level_filter", "value")],
    [State("result_folder_selector", "value"),
     State("subfolder_selector", "value")],
    prevent_initial_call=True,
)
def filter_past_log(search, level, result_folder, subfolder):
    """過去ログの検索/レベルフィルタ"""
    log_path = _find_log_file(result_folder, subfolder)
    if not log_path:
        return no_update

    from app.services.analysis_runner import format_log_lines_styled
    try:
        log_text = log_path.read_text(encoding="utf-8")
    except Exception as e:
        warn_user(f"ログフィルタに失敗: {e}")
        return no_update

    styled = format_log_lines_styled(log_text, search=search or "", level=level or "all")
    if not styled:
        styled = [html.P("該当する行がありません", className="text-muted")]
    return styled
