# =============================================================================
# MSI Analysis Application - Project Callbacks
# プロジェクト管理・サブプロジェクト管理・ページ遷移 コールバック
# =============================================================================

from dash import Input, Output, State, callback, ctx, no_update, html, dcc, ALL
import dash_bootstrap_components as dbc

from app.services.project_manager import (
    list_projects,
    get_project,
    create_project,
    update_project,
    delete_project,
    list_sub_projects,
    get_sub_project,
    create_sub_project,
    update_sub_project,
    delete_sub_project,
    get_sub_project_settings,
)
from app.services.share_manager import (
    create_share,
    list_shares,
    delete_share,
    build_share_url,
    cleanup_expired,
)


# =========================================================================
# ソートヘルパー
# =========================================================================

def _sort_items(items, sort_order):
    """ソート順に応じてリストを並び替え"""
    if sort_order == "modified_desc":
        items.sort(key=lambda x: x.get("last_modified", ""), reverse=True)
    elif sort_order == "modified_asc":
        items.sort(key=lambda x: x.get("last_modified", ""))
    elif sort_order == "name_asc":
        items.sort(key=lambda x: x.get("name", "").lower())
    elif sort_order == "name_desc":
        items.sort(key=lambda x: x.get("name", "").lower(), reverse=True)
    elif sort_order == "created_desc":
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    elif sort_order == "created_asc":
        items.sort(key=lambda x: x.get("created_at", ""))
    return items


# =========================================================================
# ページ表示切替
# =========================================================================

@callback(
    [Output("page_landing", "style"),
     Output("page_action", "style"),
     Output("page_analysis", "style"),
     Output("page_shared", "style")],
    Input("current_page", "data"),
)
def toggle_pages(current_page):
    """current_page Store の値に応じてページの表示/非表示を切り替え"""
    hide = {"display": "none"}
    pages = {
        "landing": [{}, hide, hide, hide],
        "action": [hide, {}, hide, hide],
        "analysis": [hide, hide, {}, hide],
        "shared": [hide, hide, hide, {}],
    }
    return pages.get(current_page, pages["landing"])


# =========================================================================
# プロジェクトカード一覧のレンダリング
# =========================================================================

@callback(
    Output("project_cards_container", "children"),
    [Input("current_page", "data"),
     Input("project_list_refresh", "data"),
     Input("project_sort_order", "value"),
     Input("project_search", "value")],
)
def render_project_cards(current_page, _refresh, sort_order, search_text):
    """ランディングページ表示時にプロジェクトカードを生成"""
    projects = list_projects()

    if not projects:
        return html.Div(
            className="text-center text-muted mt-5",
            children=[
                html.H5("プロジェクトがありません"),
                html.P(
                    "「+ 新規プロジェクト」ボタンから"
                    "プロジェクトを作成してください"
                ),
            ],
        )

    # 検索フィルタ
    if search_text:
        keyword = search_text.lower()
        projects = [
            p for p in projects
            if keyword in p.get("name", "").lower()
        ]
        if not projects:
            return html.Div(
                className="text-center text-muted mt-5",
                children=[
                    html.H5("該当するプロジェクトがありません"),
                    html.P(f"「{search_text}」に一致するプロジェクトは"
                           "見つかりませんでした"),
                ],
            )

    # ソート
    projects = _sort_items(projects, sort_order or "modified_desc")

    cards = []
    for p in projects:
        # プロジェクト情報の表示テキスト
        info_parts = []
        if p.get("experiment_date"):
            info_parts.append(f"実験日: {p['experiment_date']}")
        sub_count = len(p.get("sub_projects", []))
        info_parts.append(f"サブプロジェクト: {sub_count}件")

        card = dbc.Col(
            width=4,
            className="mb-3",
            children=[
                dbc.Card(
                    className="project-card h-100",
                    children=[
                        dbc.CardBody([
                            html.Div(
                                style={
                                    "display": "flex",
                                    "justifyContent": "space-between",
                                    "alignItems": "flex-start",
                                },
                                children=[
                                    html.H5(
                                        p["name"],
                                        className="card-title mb-1",
                                    ),
                                    html.Div([
                                        dbc.Button(
                                            "✎",
                                            id={
                                                "type": "edit_project_btn",
                                                "index": p["id"],
                                            },
                                            color="link",
                                            size="sm",
                                            className="text-primary p-0 me-2",
                                        ),
                                        dbc.Button(
                                            "x",
                                            id={
                                                "type": "delete_project_btn",
                                                "index": p["id"],
                                            },
                                            color="link",
                                            size="sm",
                                            className="text-danger p-0",
                                        ),
                                    ]),
                                ],
                            ),
                            html.P(
                                " | ".join(info_parts),
                                className="card-text text-muted small",
                            ),
                            html.P(
                                p.get("memo", "") or "",
                                className="card-text small",
                                style={
                                    "whiteSpace": "pre-wrap",
                                    "maxHeight": "60px",
                                    "overflow": "hidden",
                                },
                            ) if p.get("memo") else None,
                            html.Hr(className="my-2"),
                            html.Small(
                                f"最終更新: {p.get('last_modified', 'N/A')}",
                                className="text-muted",
                            ),
                            dbc.Button(
                                "開く",
                                id={
                                    "type": "select_project_btn",
                                    "index": p["id"],
                                },
                                color="primary",
                                size="sm",
                                className="mt-2",
                            ),
                        ]),
                    ],
                ),
            ],
        )
        cards.append(card)

    return dbc.Row(children=cards)


# =========================================================================
# プロジェクト選択 → サブプロジェクト一覧画面に遷移
# =========================================================================

@callback(
    [Output("current_page", "data", allow_duplicate=True),
     Output("selected_project", "data", allow_duplicate=True),
     Output("action_page_project_name", "children"),
     Output("action_page_project_description", "children")],
    Input({"type": "select_project_btn", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def select_project(clicks):
    """プロジェクトカード「開く」→ サブプロジェクト一覧画面に遷移"""
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update, no_update, no_update, no_update

    project_id = ctx.triggered_id["index"]
    project = get_project(project_id)
    if not project:
        return no_update, no_update, no_update, no_update

    # 説明表示: 実験日 + メモ
    desc_parts = []
    if project.get("experiment_date"):
        desc_parts.append(f"実験日: {project['experiment_date']}")
    if project.get("memo"):
        desc_parts.append(project["memo"])

    return (
        "action",
        project,
        project["name"],
        " | ".join(desc_parts) if desc_parts else "",
    )


# =========================================================================
# ランディングページ → インタラクティブ解析に直接遷移
# =========================================================================

@callback(
    [Output("current_page", "data", allow_duplicate=True),
     Output("main_tabs", "active_tab", allow_duplicate=True),
     Output("interactive_entry_mode", "data", allow_duplicate=True)],
    Input("open_interactive_from_landing_btn", "n_clicks"),
    prevent_initial_call=True,
)
def open_interactive_from_landing(n_clicks):
    """ランディングページ「インタラクティブ解析」→ インタラクティブ解析タブに遷移（standalone mode）"""
    if not n_clicks:
        return no_update, no_update, no_update

    return (
        "analysis",        # current_page
        "interactive",     # main_tabs
        "standalone",      # interactive_entry_mode（プロジェクト・サブプロジェクトDD表示）
    )


# =========================================================================
# サブプロジェクトカード一覧のレンダリング
# =========================================================================

@callback(
    Output("sub_project_cards_container", "children"),
    [Input("current_page", "data"),
     Input("selected_project", "data"),
     Input("sub_project_list_refresh", "data"),
     Input("sub_project_sort_order", "value"),
     Input("sub_project_search", "value")],
)
def render_sub_project_cards(
    current_page, selected_project, _refresh, sort_order, search_text,
):
    """サブプロジェクト一覧ページ表示時にカードを生成"""
    if current_page != "action" or not selected_project:
        return html.Div()

    project_id = selected_project.get("id", "")
    subs = list_sub_projects(project_id)

    if not subs:
        return html.Div(
            className="text-center text-muted mt-5",
            children=[
                html.H5("サブプロジェクトがありません"),
                html.P(
                    "「+ 新規サブプロジェクト」ボタンから"
                    "サブプロジェクトを作成してください"
                ),
            ],
        )

    # 検索フィルタ
    if search_text:
        keyword = search_text.lower()
        subs = [
            s for s in subs
            if keyword in s.get("name", "").lower()
        ]
        if not subs:
            return html.Div(
                className="text-center text-muted mt-5",
                children=[
                    html.H5("該当するサブプロジェクトがありません"),
                    html.P(f"「{search_text}」に一致するサブプロジェクトは"
                           "見つかりませんでした"),
                ],
            )

    # ソート
    subs = _sort_items(subs, sort_order or "modified_desc")

    cards = []
    for s in subs:
        # バッジ群
        badges = []
        if s.get("ms_instrument"):
            badges.append(dbc.Badge(
                s["ms_instrument"], color="info", className="me-1",
            ))
        for pol in s.get("polarity", []):
            badges.append(dbc.Badge(
                pol, color="secondary", className="me-1",
            ))

        # 情報テキスト
        info_parts = []
        if s.get("experiment_date"):
            info_parts.append(f"実験日: {s['experiment_date']}")
        if s.get("target_compound"):
            info_parts.append(f"対象: {s['target_compound']}")

        card = dbc.Col(
            width=4,
            className="mb-3",
            children=[
                dbc.Card(
                    className="sub-project-card h-100",
                    children=[
                        dbc.CardBody([
                            # タイトル + 編集・削除ボタン
                            html.Div(
                                style={
                                    "display": "flex",
                                    "justifyContent": "space-between",
                                    "alignItems": "flex-start",
                                },
                                children=[
                                    html.H5(
                                        s["name"],
                                        className="card-title mb-1",
                                    ),
                                    html.Div([
                                        dbc.Button(
                                            "✎",
                                            id={
                                                "type": "edit_sub_btn",
                                                "index": s["id"],
                                            },
                                            color="link",
                                            size="sm",
                                            className="text-primary p-0 me-2",
                                        ),
                                        dbc.Button(
                                            "x",
                                            id={
                                                "type": "delete_sub_btn",
                                                "index": s["id"],
                                            },
                                            color="link",
                                            size="sm",
                                            className="text-danger p-0",
                                        ),
                                    ]),
                                ],
                            ),

                            # バッジ
                            html.Div(
                                badges, className="mb-1",
                            ) if badges else None,

                            # 情報テキスト
                            html.P(
                                " | ".join(info_parts),
                                className="card-text text-muted small",
                            ) if info_parts else None,

                            # メモ
                            html.P(
                                s.get("memo", ""),
                                className="card-text small",
                                style={
                                    "whiteSpace": "pre-wrap",
                                    "maxHeight": "40px",
                                    "overflow": "hidden",
                                },
                            ) if s.get("memo") else None,

                            html.Hr(className="my-2"),

                            # アクションボタン群
                            dbc.ButtonGroup(
                                size="sm",
                                style={"width": "100%"},
                                children=[
                                    dbc.Button(
                                        "解析",
                                        id={
                                            "type": "sub_action_analysis",
                                            "index": s["id"],
                                        },
                                        color="primary",
                                        outline=True,
                                    ),
                                    dbc.Button(
                                        "結果閲覧",
                                        id={
                                            "type": "sub_action_results",
                                            "index": s["id"],
                                        },
                                        color="success",
                                        outline=True,
                                    ),
                                    dbc.Button(
                                        "インタラクティブ",
                                        id={
                                            "type": "sub_action_interactive",
                                            "index": s["id"],
                                        },
                                        color="info",
                                        outline=True,
                                    ),
                                    dbc.Button(
                                        "共有",
                                        id={
                                            "type": "sub_action_share",
                                            "index": s["id"],
                                        },
                                        color="warning",
                                        outline=True,
                                    ),
                                ],
                            ),
                        ]),
                    ],
                ),
            ],
        )
        cards.append(card)

    return dbc.Row(children=cards)


# =========================================================================
# サブプロジェクト アクション: 新規解析 → 解析画面 (settings タブ)
# =========================================================================

# 復元対象の設定フィールド一覧
_ANALYSIS_SETTINGS_KEYS = [
    "analysis_method",
    "analysis_method_tims",
    "data_folder",
    "output_dir",
    "mrm_path",
    "p_thresh",
    "logfc_thresh",
    "ion_mode",
    "tolerance_mz",
    "resume_rds",
    "rds_folder",
    "reanalysis_data_folder",
    "rds_path",
    "filter_mode",
    "target_clusters",
    "reanalysis_p_thresh",
    "reanalysis_logfc_thresh",
    "reanalysis_ion_mode",
    "reanalysis_tolerance_mz",
]


@callback(
    [Output("current_page", "data", allow_duplicate=True),
     Output("main_tabs", "active_tab", allow_duplicate=True),
     Output("current_sub_project_id", "data"),
     Output("data_folder", "value", allow_duplicate=True),
     Output("output_dir", "value", allow_duplicate=True),
     Output("analysis_method", "value", allow_duplicate=True),
     Output("analysis_method_tims", "value", allow_duplicate=True),
     Output("mrm_path", "value", allow_duplicate=True),
     Output("p_thresh", "value", allow_duplicate=True),
     Output("logfc_thresh", "value", allow_duplicate=True),
     Output("ion_mode", "value", allow_duplicate=True),
     Output("tolerance_mz", "value", allow_duplicate=True),
     Output("resume_rds", "value", allow_duplicate=True),
     Output("rds_folder", "value", allow_duplicate=True),
     Output("reanalysis_data_folder", "value", allow_duplicate=True),
     Output("rds_path", "value", allow_duplicate=True),
     Output("filter_mode", "value", allow_duplicate=True),
     Output("target_clusters", "value", allow_duplicate=True),
     Output("reanalysis_p_thresh", "value", allow_duplicate=True),
     Output("reanalysis_logfc_thresh", "value", allow_duplicate=True),
     Output("reanalysis_ion_mode", "value", allow_duplicate=True),
     Output("reanalysis_tolerance_mz", "value", allow_duplicate=True)],
    Input({"type": "sub_action_analysis", "index": ALL}, "n_clicks"),
    State("selected_project", "data"),
    prevent_initial_call=True,
)
def sub_action_new_analysis(clicks, project):
    """サブプロジェクト「解析」→ 解析設定画面に遷移 + 前回設定を復元"""
    _n_outputs = 22
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return (no_update,) * _n_outputs

    sub_id = ctx.triggered_id["index"]
    project_id = project.get("id", "") if project else ""
    sub = get_sub_project(project_id, sub_id)
    if not sub:
        return (no_update,) * _n_outputs

    # 保存済み解析設定を取得
    settings = get_sub_project_settings(project_id, sub_id) or {}

    # data_folder / output_dir はサブプロジェクト本体の値を優先
    data_folder = sub.get("data_folder", "") or settings.get("data_folder", "")
    output_dir = sub.get("output_dir", "") or settings.get("output_dir", "")

    return (
        "analysis",                                             # current_page
        "settings",                                             # main_tabs
        sub_id,                                                 # current_sub_project_id
        data_folder or no_update,                               # data_folder
        output_dir or no_update,                                # output_dir
        settings.get("analysis_method") or no_update,           # analysis_method
        settings.get("analysis_method_tims") or no_update,      # analysis_method_tims
        settings.get("mrm_path", "") if settings else no_update,  # mrm_path
        settings.get("p_thresh") if settings else no_update,    # p_thresh
        settings.get("logfc_thresh") if settings else no_update,  # logfc_thresh
        settings.get("ion_mode") or no_update,                  # ion_mode
        settings.get("tolerance_mz") if settings else no_update,  # tolerance_mz
        settings.get("resume_rds") if settings else no_update,  # resume_rds
        settings.get("rds_folder", "") if settings else no_update,  # rds_folder
        settings.get("reanalysis_data_folder", "") if settings else no_update,
        settings.get("rds_path", "") if settings else no_update,
        settings.get("filter_mode") or no_update,               # filter_mode
        settings.get("target_clusters", "") if settings else no_update,
        settings.get("reanalysis_p_thresh") if settings else no_update,
        settings.get("reanalysis_logfc_thresh") if settings else no_update,
        settings.get("reanalysis_ion_mode") or no_update,
        settings.get("reanalysis_tolerance_mz") if settings else no_update,
    )


# =========================================================================
# サブプロジェクト アクション: 結果閲覧 → 解析画面 (results タブ)
# =========================================================================

@callback(
    [Output("current_page", "data", allow_duplicate=True),
     Output("main_tabs", "active_tab", allow_duplicate=True),
     Output("result_folder_manual", "data", allow_duplicate=True),
     Output("results_project_select", "value", allow_duplicate=True),
     Output("results_sub_project_select", "value", allow_duplicate=True)],
    Input({"type": "sub_action_results", "index": ALL}, "n_clicks"),
    State("selected_project", "data"),
    prevent_initial_call=True,
)
def sub_action_view_results(clicks, project):
    """サブプロジェクト「結果閲覧」→ 結果閲覧タブに遷移"""
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update, no_update, no_update, no_update, no_update

    sub_id = ctx.triggered_id["index"]
    project_id = project.get("id", "") if project else ""
    sub = get_sub_project(project_id, sub_id)
    if not sub:
        return no_update, no_update, no_update, no_update, no_update

    # last_result_dir を優先、なければ output_dir にフォールバック
    result_dir = sub.get("last_result_dir") or sub.get("output_dir", "")
    return (
        "analysis",
        "results",
        result_dir or no_update,
        project_id,
        sub_id,
    )


# =========================================================================
# サブプロジェクト アクション: インタラクティブ解析 → interactive タブ
# =========================================================================

@callback(
    [Output("current_page", "data", allow_duplicate=True),
     Output("main_tabs", "active_tab", allow_duplicate=True),
     Output("interactive_result_folder", "value", allow_duplicate=True),
     Output("interactive_msi_folder", "value", allow_duplicate=True),
     Output("interactive_project_select", "value", allow_duplicate=True),
     Output("interactive_sub_project_select", "value", allow_duplicate=True),
     Output("interactive_entry_mode", "data", allow_duplicate=True),
     Output("current_sub_project_id", "data", allow_duplicate=True)],
    Input({"type": "sub_action_interactive", "index": ALL}, "n_clicks"),
    State("selected_project", "data"),
    prevent_initial_call=True,
)
def sub_action_interactive(clicks, project):
    """サブプロジェクト「インタラクティブ」→ インタラクティブ解析タブに遷移"""
    _n_out = 8
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return (no_update,) * _n_out

    sub_id = ctx.triggered_id["index"]
    project_id = project.get("id", "") if project else ""
    sub = get_sub_project(project_id, sub_id)
    if not sub:
        return (no_update,) * _n_out

    # last_result_dir を優先、なければ output_dir にフォールバック
    result_dir = sub.get("last_result_dir") or sub.get("output_dir", "")
    # MSIデータフォルダもサブプロジェクトから自動セット
    data_folder = sub.get("data_folder", "")
    return (
        "analysis",
        "interactive",
        result_dir or no_update,
        data_folder or no_update,
        project_id,
        sub_id,
        "sub_project",      # interactive_entry_mode
        sub_id,              # current_sub_project_id
    )


# =========================================================================
# 戻るボタン: サブプロジェクト一覧 → ランディング
# =========================================================================

@callback(
    Output("current_page", "data", allow_duplicate=True),
    Input("back_to_landing", "n_clicks"),
    prevent_initial_call=True,
)
def back_to_landing(n_clicks):
    if not n_clicks:
        return no_update
    return "landing"


# =========================================================================
# 戻るボタン: 解析画面 → サブプロジェクト一覧
# =========================================================================

@callback(
    Output("back_to_action_from_analysis", "children"),
    Input("interactive_entry_mode", "data"),
    prevent_initial_call=True,
)
def update_back_button_text(entry_mode):
    """エントリーモードに応じて戻るボタンのテキストを切替"""
    if entry_mode == "standalone":
        return "< プロジェクト一覧に戻る"
    return "< プロジェクトに戻る"


@callback(
    Output("current_page", "data", allow_duplicate=True),
    Input("back_to_action_from_analysis", "n_clicks"),
    State("interactive_entry_mode", "data"),
    prevent_initial_call=True,
)
def back_to_action(n_clicks, entry_mode):
    if not n_clicks:
        return no_update
    if entry_mode == "standalone":
        return "landing"
    return "action"


# =========================================================================
# 新規プロジェクト作成モーダル制御
# =========================================================================

@callback(
    Output("create_project_modal", "is_open"),
    [Input("open_create_project_modal", "n_clicks"),
     Input("cancel_create_project", "n_clicks"),
     Input("confirm_create_project", "n_clicks")],
    State("create_project_modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_create_modal(open_clicks, cancel_clicks, confirm_clicks, is_open):
    triggered = ctx.triggered_id
    if triggered == "open_create_project_modal":
        return True
    return False


# =========================================================================
# 新規プロジェクト作成実行 → リフレッシュStoreを更新
# =========================================================================

@callback(
    [Output("new_project_name", "value"),
     Output("new_project_experiment_date", "value"),
     Output("new_project_memo", "value"),
     Output("project_list_refresh", "data")],
    Input("confirm_create_project", "n_clicks"),
    [State("new_project_name", "value"),
     State("new_project_experiment_date", "value"),
     State("new_project_memo", "value"),
     State("project_list_refresh", "data")],
    prevent_initial_call=True,
)
def handle_create_project(n_clicks, name, experiment_date, memo, refresh):
    if not n_clicks or not name:
        return no_update, no_update, no_update, no_update

    create_project(
        name=name,
        experiment_date=experiment_date or "",
        memo=memo or "",
    )

    # フォーム入力をクリア + リフレッシュ
    return "", None, "", (refresh or 0) + 1


# =========================================================================
# プロジェクト削除モーダル制御
# =========================================================================

@callback(
    [Output("delete_project_modal", "is_open"),
     Output("delete_target_project_id", "data")],
    [Input({"type": "delete_project_btn", "index": ALL}, "n_clicks"),
     Input("cancel_delete_project", "n_clicks"),
     Input("confirm_delete_project", "n_clicks")],
    State("delete_target_project_id", "data"),
    prevent_initial_call=True,
)
def toggle_delete_modal(delete_clicks, cancel_clicks, confirm_clicks, target_id):
    triggered = ctx.triggered_id
    if triggered == "cancel_delete_project" or triggered == "confirm_delete_project":
        return False, ""

    if isinstance(triggered, dict) and triggered.get("type") == "delete_project_btn":
        if any(c for c in delete_clicks if c):
            return True, triggered["index"]

    return no_update, no_update


# =========================================================================
# プロジェクト削除実行 → リフレッシュStoreを更新
# =========================================================================

@callback(
    [Output("delete_target_project_id", "data", allow_duplicate=True),
     Output("project_list_refresh", "data", allow_duplicate=True)],
    Input("confirm_delete_project", "n_clicks"),
    [State("delete_target_project_id", "data"),
     State("project_list_refresh", "data")],
    prevent_initial_call=True,
)
def handle_delete_project(n_clicks, project_id, refresh):
    if not n_clicks or not project_id:
        return no_update, no_update
    delete_project(project_id)
    return "", (refresh or 0) + 1


# =========================================================================
# プロジェクト編集モーダル制御 (開く / キャンセル / 保存)
# =========================================================================

@callback(
    [Output("edit_project_modal", "is_open"),
     Output("edit_target_project_id", "data"),
     Output("edit_project_name", "value"),
     Output("edit_project_experiment_date", "value"),
     Output("edit_project_memo", "value")],
    [Input({"type": "edit_project_btn", "index": ALL}, "n_clicks"),
     Input("cancel_edit_project", "n_clicks"),
     Input("confirm_edit_project", "n_clicks")],
    State("edit_target_project_id", "data"),
    prevent_initial_call=True,
)
def toggle_edit_project_modal(edit_clicks, cancel_clicks, confirm_clicks, target_id):
    triggered = ctx.triggered_id

    # キャンセル or 保存 → 閉じる
    if triggered == "cancel_edit_project" or triggered == "confirm_edit_project":
        return False, "", "", None, ""

    # 編集ボタン → モーダルを開いて既存値をセット
    if isinstance(triggered, dict) and triggered.get("type") == "edit_project_btn":
        if any(c for c in edit_clicks if c):
            project_id = triggered["index"]
            project = get_project(project_id)
            if project:
                return (
                    True,
                    project_id,
                    project.get("name", ""),
                    project.get("experiment_date", None) or None,
                    project.get("memo", ""),
                )

    return no_update, no_update, no_update, no_update, no_update


# =========================================================================
# プロジェクト編集保存 → リフレッシュStoreを更新
# =========================================================================

@callback(
    Output("project_list_refresh", "data", allow_duplicate=True),
    Input("confirm_edit_project", "n_clicks"),
    [State("edit_target_project_id", "data"),
     State("edit_project_name", "value"),
     State("edit_project_experiment_date", "value"),
     State("edit_project_memo", "value"),
     State("project_list_refresh", "data")],
    prevent_initial_call=True,
)
def handle_edit_project(n_clicks, project_id, name, experiment_date, memo, refresh):
    if not n_clicks or not project_id or not name:
        return no_update

    update_project(project_id, {
        "name": name,
        "experiment_date": experiment_date or "",
        "memo": memo or "",
    })

    return (refresh or 0) + 1


# =========================================================================
# 新規サブプロジェクト作成モーダル制御
# =========================================================================

@callback(
    Output("create_sub_project_modal", "is_open"),
    [Input("open_create_sub_project_modal", "n_clicks"),
     Input("cancel_create_sub_project", "n_clicks"),
     Input("confirm_create_sub_project", "n_clicks")],
    State("create_sub_project_modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_create_sub_modal(open_clicks, cancel_clicks, confirm_clicks, is_open):
    triggered = ctx.triggered_id
    if triggered == "open_create_sub_project_modal":
        return True
    return False


# =========================================================================
# 新規サブプロジェクト作成実行 → リフレッシュStoreを更新
# =========================================================================

@callback(
    [Output("new_sub_name", "value"),
     Output("new_sub_experiment_date", "value"),
     Output("new_sub_target_compound", "value"),
     Output("new_sub_ms_instrument", "value"),
     Output("new_sub_polarity", "value"),
     Output("new_sub_data_folder", "value"),
     Output("new_sub_output_dir", "value"),
     Output("new_sub_memo", "value"),
     Output("sub_project_list_refresh", "data")],
    Input("confirm_create_sub_project", "n_clicks"),
    [State("selected_project", "data"),
     State("new_sub_name", "value"),
     State("new_sub_experiment_date", "value"),
     State("new_sub_target_compound", "value"),
     State("new_sub_ms_instrument", "value"),
     State("new_sub_polarity", "value"),
     State("new_sub_data_folder", "value"),
     State("new_sub_output_dir", "value"),
     State("new_sub_memo", "value"),
     State("sub_project_list_refresh", "data")],
    prevent_initial_call=True,
)
def handle_create_sub_project(
    n_clicks, project, name, experiment_date,
    target_compound, ms_instrument, polarity,
    data_folder, output_dir, memo, refresh,
):
    if not n_clicks or not name or not project:
        return (no_update,) * 9

    project_id = project.get("id", "")
    create_sub_project(
        project_id=project_id,
        name=name,
        experiment_date=experiment_date or "",
        target_compound=target_compound or "",
        ms_instrument=ms_instrument or "",
        polarity=polarity or [],
        memo=memo or "",
        data_folder=data_folder or "",
        output_dir=output_dir or "",
    )

    # フォーム入力をクリア + リフレッシュ
    return "", None, "", "", [], "", "", "", (refresh or 0) + 1


# =========================================================================
# サブプロジェクト削除モーダル制御
# =========================================================================

@callback(
    [Output("delete_sub_project_modal", "is_open"),
     Output("delete_target_sub_project_id", "data")],
    [Input({"type": "delete_sub_btn", "index": ALL}, "n_clicks"),
     Input("cancel_delete_sub_project", "n_clicks"),
     Input("confirm_delete_sub_project", "n_clicks")],
    State("delete_target_sub_project_id", "data"),
    prevent_initial_call=True,
)
def toggle_delete_sub_modal(
    delete_clicks, cancel_clicks, confirm_clicks, target_id
):
    triggered = ctx.triggered_id
    if (triggered == "cancel_delete_sub_project"
            or triggered == "confirm_delete_sub_project"):
        return False, ""

    if isinstance(triggered, dict) and triggered.get("type") == "delete_sub_btn":
        if any(c for c in delete_clicks if c):
            return True, triggered["index"]

    return no_update, no_update


# =========================================================================
# サブプロジェクト削除実行 → リフレッシュStoreを更新
# =========================================================================

@callback(
    [Output("delete_target_sub_project_id", "data", allow_duplicate=True),
     Output("sub_project_list_refresh", "data", allow_duplicate=True)],
    Input("confirm_delete_sub_project", "n_clicks"),
    [State("delete_target_sub_project_id", "data"),
     State("selected_project", "data"),
     State("sub_project_list_refresh", "data")],
    prevent_initial_call=True,
)
def handle_delete_sub_project(n_clicks, sub_id, project, refresh):
    if not n_clicks or not sub_id or not project:
        return no_update, no_update
    project_id = project.get("id", "")
    delete_sub_project(project_id, sub_id)
    return "", (refresh or 0) + 1


# =========================================================================
# サブプロジェクト編集モーダル制御 (開く / キャンセル / 保存)
# =========================================================================

@callback(
    [Output("edit_sub_project_modal", "is_open"),
     Output("edit_target_sub_project_id", "data"),
     Output("edit_sub_name", "value"),
     Output("edit_sub_experiment_date", "value"),
     Output("edit_sub_target_compound", "value"),
     Output("edit_sub_ms_instrument", "value"),
     Output("edit_sub_polarity", "value"),
     Output("edit_sub_data_folder", "value"),
     Output("edit_sub_output_dir", "value"),
     Output("edit_sub_memo", "value")],
    [Input({"type": "edit_sub_btn", "index": ALL}, "n_clicks"),
     Input("cancel_edit_sub_project", "n_clicks"),
     Input("confirm_edit_sub_project", "n_clicks")],
    [State("edit_target_sub_project_id", "data"),
     State("selected_project", "data")],
    prevent_initial_call=True,
)
def toggle_edit_sub_modal(
    edit_clicks, cancel_clicks, confirm_clicks, target_id, project,
):
    triggered = ctx.triggered_id

    # キャンセル or 保存 → 閉じる
    if (triggered == "cancel_edit_sub_project"
            or triggered == "confirm_edit_sub_project"):
        return False, "", "", None, "", "", [], "", "", ""

    # 編集ボタン → モーダルを開いて既存値をセット
    if isinstance(triggered, dict) and triggered.get("type") == "edit_sub_btn":
        if any(c for c in edit_clicks if c):
            sub_id = triggered["index"]
            project_id = project.get("id", "") if project else ""
            sub = get_sub_project(project_id, sub_id)
            if sub:
                return (
                    True,
                    sub_id,
                    sub.get("name", ""),
                    sub.get("experiment_date", None) or None,
                    sub.get("target_compound", ""),
                    sub.get("ms_instrument", ""),
                    sub.get("polarity", []),
                    sub.get("data_folder", ""),
                    sub.get("output_dir", ""),
                    sub.get("memo", ""),
                )

    return (no_update,) * 10


# =========================================================================
# サブプロジェクト編集保存 → リフレッシュStoreを更新
# =========================================================================

@callback(
    Output("sub_project_list_refresh", "data", allow_duplicate=True),
    Input("confirm_edit_sub_project", "n_clicks"),
    [State("selected_project", "data"),
     State("edit_target_sub_project_id", "data"),
     State("edit_sub_name", "value"),
     State("edit_sub_experiment_date", "value"),
     State("edit_sub_target_compound", "value"),
     State("edit_sub_ms_instrument", "value"),
     State("edit_sub_polarity", "value"),
     State("edit_sub_data_folder", "value"),
     State("edit_sub_output_dir", "value"),
     State("edit_sub_memo", "value"),
     State("sub_project_list_refresh", "data")],
    prevent_initial_call=True,
)
def handle_edit_sub_project(
    n_clicks, project, sub_id, name, experiment_date,
    target_compound, ms_instrument, polarity,
    data_folder, output_dir, memo, refresh,
):
    if not n_clicks or not project or not sub_id or not name:
        return no_update

    project_id = project.get("id", "")
    update_sub_project(project_id, sub_id, {
        "name": name,
        "experiment_date": experiment_date or "",
        "target_compound": target_compound or "",
        "ms_instrument": ms_instrument or "",
        "polarity": polarity or [],
        "memo": memo or "",
        "data_folder": data_folder or "",
        "output_dir": output_dir or "",
    })

    return (refresh or 0) + 1


# =========================================================================
# 共有リンク管理 コールバック
# =========================================================================

# --- 共有ボタンクリック → モーダル表示 ---
@callback(
    [Output("share_create_modal", "is_open", allow_duplicate=True),
     Output("share_target_sub_id", "data"),
     Output("share_target_info", "children"),
     Output("share_result_area", "style", allow_duplicate=True),
     Output("share_generated_url", "children", allow_duplicate=True)],
    Input({"type": "sub_action_share", "index": ALL}, "n_clicks"),
    State("selected_project", "data"),
    prevent_initial_call=True,
)
def open_share_modal(clicks, project):
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update, no_update, no_update, no_update, no_update

    sub_id = ctx.triggered_id["index"]
    project_id = project.get("id", "") if project else ""
    sub = get_sub_project(project_id, sub_id)
    if not sub:
        return no_update, no_update, no_update, no_update, no_update

    project_data = get_project(project_id) or {}
    info = html.Div([
        html.Strong(f"プロジェクト: {project_data.get('name', '')}"),
        html.Br(),
        html.Span(f"サブプロジェクト: {sub.get('name', '')}"),
    ])

    return True, sub_id, info, {"display": "none"}, ""


# --- 共有リンク生成 ---
@callback(
    [Output("share_result_area", "style"),
     Output("share_generated_url", "children"),
     Output("share_links_container", "children", allow_duplicate=True)],
    Input("generate_share_link", "n_clicks"),
    [State("share_target_sub_id", "data"),
     State("selected_project", "data"),
     State("share_expiry_days", "value"),
     State("share_integration_method", "value"),
     State("share_memo", "value")],
    prevent_initial_call=True,
)
def generate_share_link(n_clicks, sub_id, project, expiry_days,
                        integration_method, memo):
    if not n_clicks or not sub_id or not project:
        return no_update, no_update, no_update

    project_id = project.get("id", "") if project else ""
    project_data = get_project(project_id) or {}
    sub = get_sub_project(project_id, sub_id)
    if not sub:
        return no_update, no_update, no_update

    result_dir = sub.get("last_result_dir") or sub.get("output_dir", "")

    # RDSパスを結果フォルダから自動検索
    rds_path = ""
    if result_dir:
        from app.callbacks.interactive_callbacks import _detect_integration_methods
        rds_map = _detect_integration_methods(result_dir)
        rds_path = rds_map.get(integration_method, "")
        if not rds_path:
            # integration_method に該当するRDSがなければ最初のものを使用
            if rds_map:
                rds_path = next(iter(rds_map.values()))

    share = create_share(
        project_id=project_id,
        sub_project_id=sub_id,
        project_name=project_data.get("name", ""),
        sub_project_name=sub.get("name", ""),
        result_dir=result_dir,
        rds_path=rds_path,
        integration_method=integration_method or "Harmony",
        expires_days=int(expiry_days) if expiry_days else None,
        memo=memo or "",
    )

    url = build_share_url(share["token"])

    # 共有リンク一覧も更新
    links_ui = _render_share_links(project_id)

    return {}, url, links_ui


# --- モーダルを閉じる ---
@callback(
    Output("share_create_modal", "is_open"),
    Input("close_share_modal", "n_clicks"),
    prevent_initial_call=True,
)
def close_share_modal(n_clicks):
    if n_clicks:
        return False
    return no_update


# --- 共有リンク一覧のレンダリング ---
@callback(
    Output("share_links_container", "children"),
    [Input("current_page", "data"),
     Input("selected_project", "data")],
)
def render_share_links(current_page, project):
    if current_page != "action" or not project:
        return "共有リンクはありません"
    project_id = project.get("id", "")
    return _render_share_links(project_id)


def _render_share_links(project_id):
    """プロジェクトに属する共有リンクをレンダリング"""
    cleanup_expired()
    all_shares = list_shares()
    project_shares = [s for s in all_shares if s.get("project_id") == project_id]

    if not project_shares:
        return html.Div("共有リンクはありません", className="text-muted small")

    rows = []
    for s in project_shares:
        expired_badge = dbc.Badge("期限切れ", color="danger", className="ms-2") \
            if s.get("is_expired") else dbc.Badge("有効", color="success", className="ms-2")

        url = build_share_url(s["token"])
        rows.append(
            html.Div(
                className="d-flex justify-content-between align-items-center "
                          "border rounded p-2 mb-2",
                children=[
                    html.Div([
                        html.Strong(s.get("sub_project_name", ""), className="me-2"),
                        expired_badge,
                        html.Br(),
                        html.Code(url, style={"fontSize": "0.8rem", "wordBreak": "break-all"}),
                        html.Br(),
                        html.Small(
                            f"統合: {s.get('integration_method', '')} | "
                            f"期限: {s.get('expires_at', '')}"
                            + (f" | メモ: {s.get('memo', '')}" if s.get("memo") else ""),
                            className="text-muted",
                        ),
                    ]),
                    dbc.Button(
                        "削除",
                        id={"type": "delete_share_btn", "token": s["token"]},
                        color="outline-danger",
                        size="sm",
                    ),
                ],
            )
        )
    return html.Div(rows)


# --- 共有リンク削除確認モーダル ---
@callback(
    [Output("share_delete_modal", "is_open", allow_duplicate=True),
     Output("share_delete_target_token", "data")],
    Input({"type": "delete_share_btn", "token": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def open_share_delete_modal(clicks):
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update, no_update
    token = ctx.triggered_id["token"]
    return True, token


# --- 共有リンク削除実行 ---
@callback(
    [Output("share_delete_modal", "is_open"),
     Output("share_links_container", "children", allow_duplicate=True)],
    Input("confirm_delete_share", "n_clicks"),
    [State("share_delete_target_token", "data"),
     State("selected_project", "data")],
    prevent_initial_call=True,
)
def confirm_delete_share_link(n_clicks, token, project):
    if not n_clicks or not token:
        return no_update, no_update
    delete_share(token)
    project_id = project.get("id", "") if project else ""
    return False, _render_share_links(project_id)


# --- 共有リンク削除キャンセル ---
@callback(
    Output("share_delete_modal", "is_open", allow_duplicate=True),
    Input("cancel_delete_share", "n_clicks"),
    prevent_initial_call=True,
)
def cancel_delete_share_link(n_clicks):
    if n_clicks:
        return False
    return no_update
