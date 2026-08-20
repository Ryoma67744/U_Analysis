# =============================================================================
# MSI Analysis Application - Project Callbacks
# プロジェクト管理・サブプロジェクト管理・ページ遷移 コールバック
# =============================================================================

import logging
import threading
from pathlib import Path

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
    scan_project_meta,
    restore_projects_from_meta,
    ProjectAccessDenied,
)
from app.services.share_manager import (
    create_share,
    list_shares,
    delete_share,
    build_share_url,
    cleanup_expired,
)
from app.services.annotation_inspect import has_compound_names
from app.utils.validation import param_default
from app.services.persistent_share_manager import (
    create_persistent_share,
    build_persistent_view_url,
    list_persistent_shares,
    revoke_persistent_share,
)
from app.services.seurat_bridge import SeuratBridge

logger = logging.getLogger(__name__)

# ver4.4: 共有リンク生成時に受信者の既定ビュー (RDS) を事前抽出してキャッシュを
# 温める。受信者は初回からウォームで開けるようになる。SeuratBridge は _cache_base
# のみ保持で実質ステートレスのため、module-level 1 インスタンスをスレッド共有可。
_prewarm_bridge = SeuratBridge()


def _prewarm_share_cache(rds_path: str) -> None:
    """共有対象 RDS の Seurat 抽出をバックグラウンドで先行実行 (best-effort)。

    extract_data 内の FileLock により、受信者の初回オープンと二重に R 抽出が
    走ることはない。失敗しても共有作成には影響させない。
    """
    try:
        _prewarm_bridge.extract_data(rds_path, with_expression=False)
        logger.info("share cache prewarmed: %s", rds_path)
    except Exception as e:
        logger.warning("share cache prewarm failed (%s): %s", rds_path, e)


# =========================================================================
# ソートヘルパー
# =========================================================================

def _sort_items(items, sort_order):
    """ソート順に応じてリストを並び替え"""
    # ver3.16: experiment_date_desc/asc を追加 (ISO date 文字列ソート)
    if sort_order == "experiment_date_desc":
        items.sort(key=lambda x: x.get("experiment_date", ""), reverse=True)
    elif sort_order == "experiment_date_asc":
        items.sort(key=lambda x: x.get("experiment_date", ""))
    elif sort_order == "modified_desc":
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
     Output("page_lite", "style")],
    Input("current_page", "data"),
)
def toggle_pages(current_page):
    """current_page Store の値に応じてページの表示/非表示を切り替え

    ★ ver52.3: "shared" の行を削除した。旧 read-only 共有ページ (page_shared)
      を削除したため。`current_page` に "shared" が入ることは無く
      （共有リンクは "analysis" を返し "shared" は `interactive_entry_mode` 側）、
      この行は一度も選ばれていなかった。
    """
    hide = {"display": "none"}
    pages = {
        "landing":  [{},   hide, hide, hide],
        "action":   [hide, {},   hide, hide],
        "analysis": [hide, hide, {},   hide],
        "lite":     [hide, hide, hide, {}],
    }
    return pages.get(current_page, pages["landing"])


# =========================================================================
# 共有モード表示制御 (ver4.0)
# =========================================================================

@callback(
    [Output("main_tabs_wrapper", "className"),
     Output("back_to_action_from_analysis", "style"),
     Output("header_analysis_buttons", "style")],
    Input("shared_session", "data"),
)
def apply_shared_mode(shared):
    """共有 URL アクセス時はインタラクティブ解析のみを表示する。

    他タブ (settings/results/history) のヘッダー・戻るボタン・ヘッダー操作
    ボタン群を隠す。active_tab は route_share_url が "interactive" に設定し、
    サイドバー非表示と全幅化は既存の toggle_sidebar_content が担当する。
    """
    if shared and shared.get("active"):
        return ("shared-mode-tabs",
                {"display": "none"}, {"display": "none"})
    return ("", {}, {})


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

    # ソート (ver3.16: デフォルトを実験日新しい順に変更)
    projects = _sort_items(projects, sort_order or "experiment_date_desc")

    cards = []
    for p in projects:
        # プロジェクト情報の表示テキスト
        info_parts = []
        if p.get("experiment_date"):
            info_parts.append(f"実験日: {p['experiment_date']}")
        sub_count = len(p.get("sub_projects", []))
        info_parts.append(f"サブプロジェクト: {sub_count}件")

        # ver3.12: 「左サムネ + 右内容」の 2 列レイアウトに変更
        # CardBody を padding=0 の flex container 化し、左にサムネ画像 (固定幅
        # 130px / 全高 stretch)、右に縦並びの内容ブロックを配置する。
        card = dbc.Col(
            width=4,
            className="mb-3",
            children=[
                dbc.Card(
                    className="project-card h-100",
                    style={"overflow": "hidden"},  # 角丸内に img を収める
                    children=[
                        dbc.CardBody(
                            style={
                                "padding": "12px",
                                "display": "flex",
                                "alignItems": "flex-start",
                                "gap": "12px",
                            },
                            children=[
                                # 左カラム: サムネ画像 (150x150 固定サイズ)
                                # ver3.14: 100x100 → 150x150 に拡大。
                                # 横長 source (R 出力の per_sample 連結画像) は
                                # サーバー側で最左端の正方形にクロップ済 (1 枚目のみ)
                                # ver3.15: ?t=<last_modified> でキャッシュバスター。
                                # サムネ更新時に last_modified が変わる → URL も変わる
                                # → ブラウザは新画像を即 fetch (古いキャッシュを無視)
                                html.Img(
                                    src=(
                                        f"/api/project_thumb/{p['id']}"
                                        f"?t={(p.get('last_modified') or '').replace(':', '-')}"
                                    ),
                                    style={
                                        "width": "150px",
                                        "minWidth": "150px",
                                        "height": "150px",
                                        "objectFit": "cover",
                                        "background": "#f0f0f0",
                                        "display": "block",
                                        "borderRadius": "6px",
                                        "border": "1px solid #e0e0e0",
                                        "flexShrink": 0,
                                    },
                                    **{"data-no-thumb-hide": "1"},
                                ),
                                # 右カラム: タイトル + メタ + 「開く」ボタン
                                # ver3.13: CardBody 側に padding=12px を持たせたため
                                # 右カラム自体は padding=0 (重複防止)
                                html.Div(
                                    style={
                                        "flexGrow": 1,
                                        "padding": 0,
                                        "minWidth": 0,
                                        "display": "flex",
                                        "flexDirection": "column",
                                    },
                                    children=[
                                        # タイトル行: H5 + ✎ x
                                        html.Div(
                                            style={
                                                "display": "flex",
                                                "justifyContent": "space-between",
                                                "alignItems": "flex-start",
                                                "gap": "8px",
                                            },
                                            children=[
                                                html.H5(
                                                    p["name"],
                                                    className="card-title mb-1",
                                                    style={
                                                        "flexGrow": 1,
                                                        "minWidth": 0,
                                                        "wordBreak": "break-word",
                                                    },
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
                                        # メタ情報: 実験日 | サブプロジェクト数
                                        html.P(
                                            " | ".join(info_parts),
                                            className="card-text text-muted small",
                                        ),
                                        # ver3.16: memo 表示を削除 (タイトル/実験日/
                                        # サブプロ数/最終更新 のみカードに表示)
                                        # データ自体は projects.json に残し、編集モーダル
                                        # では引き続き編集可能
                                        html.Hr(className="my-2"),
                                        # 最終更新
                                        html.Small(
                                            f"最終更新: {p.get('last_modified', 'N/A')}",
                                            className="text-muted",
                                        ),
                                        # 「開く」ボタン (右カラム幅)
                                        dbc.Button(
                                            "開く",
                                            id={
                                                "type": "select_project_btn",
                                                "index": p["id"],
                                            },
                                            color="primary",
                                            size="sm",
                                            className="w-100 mt-2",
                                        ),
                                    ],
                                ),
                            ],
                        ),
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
        # 化合物名（注釈）バッジ: サイドカー/ヘッダの安価チェックのみ（生データは開かない）
        try:
            _has_comp = has_compound_names(s)
        except Exception:
            _has_comp = False
        if _has_comp:
            badges.append(dbc.Badge("化合物名 ✓", color="success", className="me-1"))
        else:
            badges.append(dbc.Badge("化合物名 なし", color="light",
                                    text_color="secondary", className="me-1"))

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
                                    dbc.Button(
                                        "化合物名",
                                        id={
                                            "type": "sub_action_annotations",
                                            "index": s["id"],
                                        },
                                        color="success",
                                        outline=True,
                                        title="登録データに化合物名が含まれるか確認",
                                    ),
                                    dbc.Button(
                                        "分子情報を登録",
                                        id={
                                            "type": "sub_action_add_molinfo",
                                            "index": s["id"],
                                        },
                                        color="success",
                                        outline=True,
                                        title="SCiLS feature-list CSV から化合物名を後付け登録",
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
    "annotation_path",
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
     Output("annotation_path", "value", allow_duplicate=True),
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
     Output("reanalysis_tolerance_mz", "value", allow_duplicate=True),
     # ★ ver58.1 (デバッグ総点検 B-1〜B-3): 「いま復元している」と宣言する。
     #   ここは analysis_method と ion_mode を同一レスポンスで書くため、
     #   従来は auto_switch_data_folder / auto_switch_adduct /
     #   reset_reanalysis_defaults が発火し、復元したばかりの
     #   **データフォルダ・付加イオン・再解析のイオンモードと許容誤差**を
     #   既定値で塗り潰していた。とくにデータフォルダは、そのまま実行すると
     #   別の場所のデータを解析してしまう。
     Output("settings_restore_pending", "data", allow_duplicate=True)],
    Input({"type": "sub_action_analysis", "index": ALL}, "n_clicks"),
    State("selected_project", "data"),
    prevent_initial_call=True,
)
def sub_action_new_analysis(clicks, project):
    """サブプロジェクト「解析」→ 解析設定画面に遷移 + 前回設定を復元"""
    _n_outputs = 23   # ver58.1: settings_restore_pending を追加
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

    # ★ ver56.5 (デバッグ総点検 §4.2 / C01-1): `x or no_update` をやめ、常に明示値を返す。
    #   `no_update` は「画面を変更しない」なので、**切り替える前のサブプロジェクトの
    #   データフォルダ / 出力先 / 閾値がそのまま残る**。current_sub_project_id だけは
    #   必ず新しくなるため、
    #       見出し・サブプロジェクト ID → B
    #       データフォルダ / 出力先     → A のまま
    #   という食い違いになり、そのまま実行すると **A のデータを解析して A の出力先に書き、
    #   記録上は B の解析**になる (実ブラウザで再現済み)。解析画面には
    #   「今どのサブプロジェクトを操作中か」の表示が無く、気づく手段が無い。
    #
    #   同じ修正は ver52.3 に `sub_action_interactive` (下の :747-755) で入っており、
    #   **解析設定側にだけ適用されていなかった**。
    #
    #   既定値は発明せず `PARAM_BOUNDS` を単一出典とする (`param_default`、ver52.3 ⑤)。
    #   `settings.get(k) or 既定` ではなく `is None` 判定にすること —
    #   `0` (= 絞り込まない) や `False` は正当な保存値であり、falsy 判定だと既定へ化ける。
    def _restored(key, default):
        value = settings.get(key)
        return default if value is None else value

    return (
        "analysis",                                             # current_page
        "settings",                                             # main_tabs
        sub_id,                                                 # current_sub_project_id
        data_folder,                                            # data_folder
        output_dir,                                             # output_dir
        # ★ 解析手法だけは保存値が無ければ触らない。file_handlers.py:39 の
        #   DESI/TIMS 相互クリア対と set_default_* が現在値を前提に連鎖するため、
        #   ここで空にすると「どちらも未選択」に落ちうる。手法はラジオボタンとして
        #   画面に明示されており、フォルダのように隠れた取り違えは起きない。
        settings.get("analysis_method") or no_update,           # analysis_method
        settings.get("analysis_method_tims") or no_update,      # analysis_method_tims
        settings.get("annotation_path", settings.get("mrm_path", "")),  # annotation_path
        _restored("p_thresh", param_default("p_thresh")),       # p_thresh
        _restored("logfc_thresh", param_default("logfc_thresh")),  # logfc_thresh
        _restored("ion_mode", "Positive"),                      # ion_mode
        _restored("tolerance_mz", param_default("tolerance_mz")),  # tolerance_mz
        _restored("resume_rds", False),                         # resume_rds
        settings.get("rds_folder", ""),                         # rds_folder
        settings.get("reanalysis_data_folder", ""),
        settings.get("rds_path", ""),
        _restored("filter_mode", "exclude"),                    # filter_mode
        settings.get("target_clusters", ""),
        _restored("reanalysis_p_thresh", param_default("reanalysis_p_thresh")),
        _restored("reanalysis_logfc_thresh", param_default("reanalysis_logfc_thresh")),
        _restored("reanalysis_ion_mode", "Positive"),
        _restored("reanalysis_tolerance_mz", param_default("reanalysis_tolerance_mz")),
        True,                                                   # settings_restore_pending
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
    # ★ ver52.3: `x or no_update` をやめ、常に明示的な値を入れる。
    #   `no_update` は「変更しない」なので、**切り替える前のサブプロジェクトの
    #   結果フォルダ / MSI データフォルダがそのまま残る**。
    #   project_id と sub_id は必ず更新されるので、
    #     見出し・サブプロジェクト ID → 新しいもの
    #     結果 / MSI データフォルダ    → 前のサブプロジェクトのもの
    #   という食い違いになる。共有リンク (share_callbacks.route_share_url) と
    #   同じ形で、しかもこちらは通常操作なので踏む頻度が高い。
    #   未設定なら空にして「未設定」として下流に扱わせる。
    return (
        "analysis",
        "interactive",
        result_dir,
        data_folder,
        project_id,
        sub_id,
        "sub_project",      # interactive_entry_mode
        sub_id,              # current_sub_project_id
    )


# =========================================================================
# ver3.9: ヘッダータイトル「MSI Analysis Application」クリック → ホーム
# =========================================================================

@callback(
    Output("current_page", "data", allow_duplicate=True),
    [Input("header_title_home_btn", "n_clicks"),
     # ver3.16: action_page (サブプロ一覧) のヘッダーボタンも対応
     # 別 ID にしている理由: 同じ ID を複数 page に置くと Dash の DOM
     # 重複問題が起きるため
     Input("header_title_home_btn_action", "n_clicks")],
    prevent_initial_call=True,
)
def header_title_to_landing(n_analysis, n_action):
    """ヘッダーのタイトルクリックでプロジェクト一覧に戻る。
    解析画面 (analysis) / サブプロ一覧 (action) どちらの header_title_home_btn*
    クリックでも landing に遷移する。"""
    if not (n_analysis or n_action):
        return no_update
    return "landing"


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
     Input("cancel_create_project", "n_clicks")],
    prevent_initial_call=True,
)
def toggle_create_modal(open_clicks, cancel_clicks):
    """新規プロジェクト作成モーダルの open/cancel のみ制御。
    confirm 時の close は handle_create_project が validation 後に行う。
    """
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
     Output("new_project_google_keep_url", "value"),
     Output("new_project_msi_share_url", "value"),
     Output("new_project_other_url", "value"),
     Output("project_list_refresh", "data"),
     Output("create_project_modal", "is_open", allow_duplicate=True),
     Output("new_project_error", "children")],
    Input("confirm_create_project", "n_clicks"),
    [State("new_project_name", "value"),
     State("new_project_experiment_date", "value"),
     State("new_project_memo", "value"),
     State("new_project_google_keep_url", "value"),
     State("new_project_msi_share_url", "value"),
     State("new_project_other_url", "value"),
     State("project_list_refresh", "data")],
    prevent_initial_call=True,
)
def handle_create_project(n_clicks, name, experiment_date, memo,
                          google_keep_url, msi_share_url, other_url,
                          refresh):
    """ver3.16: タイトル + 実験日を必須化、URL 3 種を保存。

    バリデーション失敗時はモーダルを閉じずエラーメッセージを表示。
    """
    if not n_clicks:
        return (no_update,) * 9
    name = (name or "").strip()
    experiment_date = (experiment_date or "").strip()
    if not name or not experiment_date:
        # バリデーション失敗 → モーダル open 維持 + エラー表示
        msg = "「プロジェクトタイトル」と「実験日」は必須です。"
        return (no_update, no_update, no_update,
                no_update, no_update, no_update,
                no_update, True, msg)

    create_project(
        name=name,
        experiment_date=experiment_date,
        memo=memo or "",
        google_keep_url=(google_keep_url or "").strip(),
        msi_share_url=(msi_share_url or "").strip(),
        other_url=(other_url or "").strip(),
    )

    # フォーム入力クリア + モーダル close + リフレッシュ
    return "", None, "", "", "", "", (refresh or 0) + 1, False, ""


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
     Output("project_list_refresh", "data", allow_duplicate=True),
     Output("notification_toast", "is_open", allow_duplicate=True),
     Output("notification_toast", "children", allow_duplicate=True),
     Output("notification_toast", "icon", allow_duplicate=True)],
    Input("confirm_delete_project", "n_clicks"),
    [State("delete_target_project_id", "data"),
     State("project_list_refresh", "data")],
    prevent_initial_call=True,
)
def handle_delete_project(n_clicks, project_id, refresh):
    if not n_clicks or not project_id:
        return no_update, no_update, no_update, no_update, no_update
    try:
        # ログイン中のユーザーなら作成者に関わらず削除可（所有権ガードは無効化）
        delete_project(project_id, enforce_owner=False)
    except ProjectAccessDenied as e:
        # 所有権が無いユーザーの削除試行を拒否
        return "", no_update, True, str(e), "danger"
    except Exception as e:
        import logging as _logging
        _logging.getLogger("msi.project_callbacks").exception("削除失敗")
        return "", no_update, True, "削除に失敗しました。ログを確認してください。", "danger"
    return "", (refresh or 0) + 1, no_update, no_update, no_update


# =========================================================================
# プロジェクト編集モーダル制御 (開く / キャンセル / 保存)
# =========================================================================

@callback(
    [Output("edit_project_modal", "is_open"),
     Output("edit_target_project_id", "data"),
     Output("edit_project_name", "value"),
     Output("edit_project_experiment_date", "value"),
     Output("edit_project_memo", "value"),
     Output("edit_project_thumbnail", "value"),
     Output("edit_project_google_keep_url", "value"),
     Output("edit_project_msi_share_url", "value"),
     Output("edit_project_other_url", "value")],
    [Input({"type": "edit_project_btn", "index": ALL}, "n_clicks"),
     Input("cancel_edit_project", "n_clicks")],
    State("edit_target_project_id", "data"),
    prevent_initial_call=True,
)
def toggle_edit_project_modal(edit_clicks, cancel_clicks, target_id):
    """ver3.16: open/cancel のみ制御。confirm 時の close は
    handle_edit_project が validation 後に行う。"""
    triggered = ctx.triggered_id

    # キャンセル → 閉じる
    if triggered == "cancel_edit_project":
        return False, "", "", None, "", "", "", "", ""

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
                    project.get("thumbnail_source", ""),
                    project.get("google_keep_url", ""),
                    project.get("msi_share_url", ""),
                    project.get("other_url", ""),
                )

    return (no_update,) * 9


# =========================================================================
# プロジェクト編集保存 → リフレッシュStoreを更新
# =========================================================================

@callback(
    [Output("project_list_refresh", "data", allow_duplicate=True),
     Output("edit_project_modal", "is_open", allow_duplicate=True),
     Output("edit_project_error", "children")],
    Input("confirm_edit_project", "n_clicks"),
    [State("edit_target_project_id", "data"),
     State("edit_project_name", "value"),
     State("edit_project_experiment_date", "value"),
     State("edit_project_memo", "value"),
     State("edit_project_thumbnail", "value"),
     State("edit_project_google_keep_url", "value"),
     State("edit_project_msi_share_url", "value"),
     State("edit_project_other_url", "value"),
     State("project_list_refresh", "data")],
    prevent_initial_call=True,
)
def handle_edit_project(n_clicks, project_id, name, experiment_date, memo,
                        thumbnail_source, google_keep_url, msi_share_url,
                        other_url, refresh):
    """ver3.16: タイトル + 実験日を必須化、URL 3 種を保存。"""
    if not n_clicks or not project_id:
        return no_update, no_update, no_update
    name = (name or "").strip()
    experiment_date = (experiment_date or "").strip()
    if not name or not experiment_date:
        return (no_update, True,
                "「プロジェクトタイトル」と「実験日」は必須です。")

    update_project(project_id, {
        "name": name,
        "experiment_date": experiment_date,
        "memo": memo or "",
        "thumbnail_source": (thumbnail_source or "").strip(),
        "google_keep_url": (google_keep_url or "").strip(),
        "msi_share_url": (msi_share_url or "").strip(),
        "other_url": (other_url or "").strip(),
    })

    return (refresh or 0) + 1, False, ""


# =========================================================================
# 新規サブプロジェクト作成モーダル制御
# =========================================================================

@callback(
    Output("create_sub_project_modal", "is_open"),
    [Input("open_create_sub_project_modal", "n_clicks"),
     Input("cancel_create_sub_project", "n_clicks")],
    prevent_initial_call=True,
)
def toggle_create_sub_modal(open_clicks, cancel_clicks):
    """作成モーダルの open/cancel のみ制御。

    ★ ver56.5 (§4.2 / C01-3): confirm を Input から外した。
      以前は「作成」押下で**無条件にモーダルを閉じて**いたため、タイトル未入力だと
      `handle_create_sub_project` が何もせずに終わり、モーダルだけ閉じて
      サブプロジェクトは作られず、理由も表示されないという無音の失敗になっていた。
      confirm 時の close は handle 側が検証を通してから行う
      (プロジェクト版 `toggle_create_modal` が ver3.16 で採った形と同じ)。
    """
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
     Output("new_sub_matrix", "value"),
     Output("new_sub_polarity", "value"),
     Output("new_sub_data_folder", "value"),
     Output("new_sub_output_dir", "value"),
     Output("new_sub_memo", "value"),
     Output("sub_project_list_refresh", "data"),
     # ★ ver56.5 (§4.2 / C01-3): 検証を通ってから閉じる + 失敗理由を出す
     Output("create_sub_project_modal", "is_open", allow_duplicate=True),
     Output("new_sub_error", "children")],
    Input("confirm_create_sub_project", "n_clicks"),
    [State("selected_project", "data"),
     State("new_sub_name", "value"),
     State("new_sub_experiment_date", "value"),
     State("new_sub_target_compound", "value"),
     State("new_sub_ms_instrument", "value"),
     State("new_sub_matrix", "value"),
     State("new_sub_polarity", "value"),
     State("new_sub_data_folder", "value"),
     State("new_sub_output_dir", "value"),
     State("new_sub_memo", "value"),
     State("sub_project_list_refresh", "data")],
    prevent_initial_call=True,
)
def handle_create_sub_project(
    n_clicks, project, name, experiment_date,
    target_compound, ms_instrument, matrix, polarity,
    data_folder, output_dir, memo, refresh,
):
    _n_out = 12
    if not n_clicks:
        return (no_update,) * _n_out
    name = (name or "").strip()
    if not name or not project:
        # ★ ver56.5: 検証失敗 → モーダルは開いたままにし、理由を表示する。
        #   以前はここで全 no_update を返し、一方 toggle 側が無条件に閉じていたため、
        #   「モーダルは閉じたのに作成されず、理由も出ない」という無音の失敗だった。
        msg = ("「タイトル」は必須です。" if not name
               else "プロジェクトが選択されていません。")
        return (no_update,) * 10 + (True, msg)

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
        extra_fields={"matrix": matrix or ""},
    )

    # フォーム入力をクリア + リフレッシュ + モーダルを閉じる (検証を通った後)
    return "", None, "", "", "", [], "", "", "", (refresh or 0) + 1, False, ""


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
     Output("sub_project_list_refresh", "data", allow_duplicate=True),
     Output("notification_toast", "is_open", allow_duplicate=True),
     Output("notification_toast", "children", allow_duplicate=True),
     Output("notification_toast", "icon", allow_duplicate=True)],
    Input("confirm_delete_sub_project", "n_clicks"),
    [State("delete_target_sub_project_id", "data"),
     State("selected_project", "data"),
     State("sub_project_list_refresh", "data")],
    prevent_initial_call=True,
)
def handle_delete_sub_project(n_clicks, sub_id, project, refresh):
    if not n_clicks or not sub_id or not project:
        return no_update, no_update, no_update, no_update, no_update
    project_id = project.get("id", "")
    try:
        # ログイン中のユーザーなら作成者に関わらず削除可（所有権ガードは無効化）
        delete_sub_project(project_id, sub_id, enforce_owner=False)
    except ProjectAccessDenied as e:
        return "", no_update, True, str(e), "danger"
    except Exception:
        import logging as _logging
        _logging.getLogger("msi.project_callbacks").exception("サブプロジェクト削除失敗")
        return "", no_update, True, "削除に失敗しました。ログを確認してください。", "danger"
    return "", (refresh or 0) + 1, no_update, no_update, no_update


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
     Output("edit_sub_matrix", "value"),
     Output("edit_sub_polarity", "value"),
     Output("edit_sub_data_folder", "value"),
     Output("edit_sub_output_dir", "value"),
     Output("edit_sub_memo", "value")],
    [Input({"type": "edit_sub_btn", "index": ALL}, "n_clicks"),
     Input("cancel_edit_sub_project", "n_clicks")],
    [State("edit_target_sub_project_id", "data"),
     State("selected_project", "data")],
    prevent_initial_call=True,
)
def toggle_edit_sub_modal(edit_clicks, cancel_clicks, target_id, project):
    """編集モーダルの open/cancel のみ制御。

    ★ ver56.5 (§4.2 / C01-4): confirm を Input から外した。
      以前は「保存」押下で**無条件に閉じて全 11 欄をクリア**していた。一方
      `handle_edit_sub_project` はタイトルが空だと何も保存しないため、
      タイトルを打ち直そうとして一瞬空にしたまま保存すると、
      **メモやフォルダの編集内容が復旧不能に失われ、エラーも出ない**
      (モーダルは閉じ、一覧のカードも元のままなので成功と区別がつかない)。
      confirm 時の close/クリアは handle 側が検証を通してから行う。
    """
    triggered = ctx.triggered_id

    # キャンセル → 閉じる
    if triggered == "cancel_edit_sub_project":
        return False, "", "", None, "", "", "", [], "", "", ""

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
                    sub.get("matrix", ""),
                    sub.get("polarity", []),
                    sub.get("data_folder", ""),
                    sub.get("output_dir", ""),
                    sub.get("memo", ""),
                )

    return (no_update,) * 11


# =========================================================================
# サブプロジェクト編集保存 → リフレッシュStoreを更新
# =========================================================================

@callback(
    [Output("sub_project_list_refresh", "data", allow_duplicate=True),
     # ★ ver56.5 (§4.2 / C01-4): 検証を通ってから閉じる + 失敗理由を出す
     Output("edit_sub_project_modal", "is_open", allow_duplicate=True),
     Output("edit_sub_error", "children")],
    Input("confirm_edit_sub_project", "n_clicks"),
    [State("selected_project", "data"),
     State("edit_target_sub_project_id", "data"),
     State("edit_sub_name", "value"),
     State("edit_sub_experiment_date", "value"),
     State("edit_sub_target_compound", "value"),
     State("edit_sub_ms_instrument", "value"),
     State("edit_sub_matrix", "value"),
     State("edit_sub_polarity", "value"),
     State("edit_sub_data_folder", "value"),
     State("edit_sub_output_dir", "value"),
     State("edit_sub_memo", "value"),
     State("sub_project_list_refresh", "data")],
    prevent_initial_call=True,
)
def handle_edit_sub_project(
    n_clicks, project, sub_id, name, experiment_date,
    target_compound, ms_instrument, matrix, polarity,
    data_folder, output_dir, memo, refresh,
):
    if not n_clicks:
        return no_update, no_update, no_update
    name = (name or "").strip()
    if not name or not project or not sub_id:
        # ★ ver56.5: 検証失敗 → モーダルを開いたまま保ち、入力内容を守る。
        #   以前は no_update を返すだけで、toggle 側が無条件に閉じて全欄を
        #   クリアしていたため、編集内容が黙って消えていた。
        msg = ("「タイトル」は必須です。空のままでは保存できません。" if not name
               else "編集対象を特定できませんでした。開き直してください。")
        return no_update, True, msg

    project_id = project.get("id", "")
    update_sub_project(project_id, sub_id, {
        "name": name,
        "experiment_date": experiment_date or "",
        "target_compound": target_compound or "",
        "ms_instrument": ms_instrument or "",
        "matrix": matrix or "",
        "polarity": polarity or [],
        "memo": memo or "",
        "data_folder": data_folder or "",
        "output_dir": output_dir or "",
    })

    # 保存できたのでモーダルを閉じる (エラー表示はクリア)
    return (refresh or 0) + 1, False, ""


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
     State("share_kind_radio", "value"),
     State("share_expiry_days", "value"),
     State("share_integration_method", "value"),
     State("share_require_password", "value"),
     State("share_memo", "value")],
    prevent_initial_call=True,
)
def generate_share_link(n_clicks, sub_id, project, share_kind, expiry_days,
                        integration_method, require_password, memo):
    """期間付き共有 (share_manager) または無期限共有 (persistent_share_manager) を
    生成する。share_kind_radio で分岐する。"""
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
        # ver4.4: 受信者が最初に見る既定手法の RDS を事前ウォーム (バックグラウンド)。
        # auto_scan_rds_files の既定 (Harmony 優先、無ければ rds_path) に合わせる。
        warm_rds = rds_map.get("Harmony") or rds_path
        if warm_rds and Path(warm_rds).exists():
            threading.Thread(
                target=_prewarm_share_cache, args=(warm_rds,), daemon=True
            ).start()

    # ver4.2: パス要否は期限と独立。スイッチ値 (既定 True) をそのまま渡す
    require_pw = bool(require_password)
    if share_kind == "persistent":
        # 無期限共有 (/view/<token>)
        share = create_persistent_share(
            project_id=project_id,
            sub_project_id=sub_id,
            project_name=project_data.get("name", ""),
            sub_project_name=sub.get("name", ""),
            result_dir=result_dir,
            rds_path=rds_path,
            integration_method=integration_method or "Harmony",
            memo=memo or "",
            require_password=require_pw,
        )
        url = build_persistent_view_url(share["token"])
    else:
        # 期間付き共有 (/share/<token>)
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
            require_password=require_pw,
        )
        url = build_share_url(share["token"])

    # 共有リンク一覧も更新 (期間付き shares のみ表示。
    # 無期限 shares 一覧は別途追加可能だが、まずは MVP として URL を Modal で
    # ユーザーに渡して終了)
    links_ui = _render_share_links(project_id)

    return {}, url, links_ui


# --- 有効期限欄=期限種別に連動 / 警告=パスワード保護OFF に連動 (ver4.2) ---
@callback(
    [Output("share_expiry_wrapper", "style"),
     Output("share_persistent_warning", "style")],
    [Input("share_kind_radio", "value"),
     Input("share_require_password", "value")],
    prevent_initial_call=False,
)
def _toggle_share_kind_inputs(share_kind, require_password):
    # 有効期限は「期間付き」のときのみ表示
    expiry_style = ({"display": "none"} if share_kind == "persistent"
                    else {"display": "block"})
    # 認証なし警告は「パスワード保護OFF」のときのみ表示 (期限種別とは独立)
    warning_style = ({"display": "block"} if not require_password
                     else {"display": "none"})
    return expiry_style, warning_style


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


# ver3.17: --- プロジェクト関連情報 (URL 3 種 + memo) を input にロード ---
# ver3.16 は表示専用だったが、ユーザー要望でその場で編集 + 保存可能に変更。
@callback(
    [Output("project_info_google_keep_url", "value"),
     Output("project_info_msi_share_url", "value"),
     Output("project_info_other_url", "value"),
     Output("project_info_memo", "value"),
     Output("project_info_status", "children", allow_duplicate=True)],
    [Input("current_page", "data"),
     Input("selected_project", "data"),
     Input("project_list_refresh", "data")],
    prevent_initial_call="initial_duplicate",
)
def load_project_info(current_page, project, _refresh):
    """サブプロ一覧ページに来たら、親プロジェクトの URL 3 種 + memo を
    編集 input にロードする。"""
    if current_page != "action" or not project:
        return "", "", "", "", ""
    project_id = project.get("id", "")
    proj = get_project(project_id)
    if not proj:
        return "", "", "", "", ""
    return (
        proj.get("google_keep_url", "") or "",
        proj.get("msi_share_url", "") or "",
        proj.get("other_url", "") or "",
        proj.get("memo", "") or "",
        "",  # status クリア
    )


# ver3.17: --- プロジェクト関連情報の保存 ---
@callback(
    [Output("project_info_status", "children", allow_duplicate=True),
     Output("project_list_refresh", "data", allow_duplicate=True)],
    Input("project_info_save_btn", "n_clicks"),
    [State("selected_project", "data"),
     State("project_info_google_keep_url", "value"),
     State("project_info_msi_share_url", "value"),
     State("project_info_other_url", "value"),
     State("project_info_memo", "value"),
     State("project_list_refresh", "data")],
    prevent_initial_call=True,
)
def save_project_info(n_clicks, project, google_keep, msi_share, other,
                      memo, refresh):
    """「💾 保存」クリックでプロジェクトの URL 3 種 + memo を更新。"""
    if not n_clicks or not project:
        return no_update, no_update
    project_id = project.get("id", "")
    if not project_id:
        return "プロジェクトが選択されていません", no_update
    try:
        updated = update_project(project_id, {
            "google_keep_url": (google_keep or "").strip(),
            "msi_share_url": (msi_share or "").strip(),
            "other_url": (other or "").strip(),
            "memo": memo or "",
        })
        if updated is None:
            return "保存失敗 (プロジェクト未発見)", no_update
    except Exception as e:
        return f"保存失敗: {e}", no_update
    from datetime import datetime
    return (
        f"✓ 保存しました ({datetime.now().strftime('%H:%M:%S')})",
        (refresh or 0) + 1,
    )


def _share_link_row(s, kind):
    """共有リンク 1 件の行を生成 (kind='expiring' or 'persistent')。"""
    require_pw = s.get("require_password", kind == "expiring")
    pw_badge = (dbc.Badge("🔒 パス必要", color="secondary", className="ms-1")
                if require_pw
                else dbc.Badge("🔓 パス不要", color="warning", className="ms-1"))
    if kind == "persistent":
        kind_badge = dbc.Badge("無期限", color="info", className="ms-2")
        url = build_persistent_view_url(s["token"])
        info = (f"統合: {s.get('integration_method', '')} | "
                f"閲覧数: {s.get('view_count', 0)}"
                + (f" | メモ: {s.get('memo', '')}" if s.get("memo") else ""))
    else:
        kind_badge = (dbc.Badge("期限切れ", color="danger", className="ms-2")
                      if s.get("is_expired")
                      else dbc.Badge("期間付き", color="success", className="ms-2"))
        url = build_share_url(s["token"])
        info = (f"統合: {s.get('integration_method', '')} | "
                f"期限: {s.get('expires_at', '')}"
                + (f" | メモ: {s.get('memo', '')}" if s.get("memo") else ""))
    return html.Div(
        className="d-flex justify-content-between align-items-center "
                  "border rounded p-2 mb-2",
        children=[
            html.Div([
                html.Strong(s.get("sub_project_name", ""), className="me-2"),
                kind_badge,
                pw_badge,
                html.Br(),
                html.Code(url, style={"fontSize": "0.8rem", "wordBreak": "break-all"}),
                html.Br(),
                html.Small(info, className="text-muted"),
            ]),
            dbc.Button(
                "削除",
                id={"type": "delete_share_btn", "token": s["token"]},
                color="outline-danger",
                size="sm",
            ),
        ],
    )


def _render_share_links(project_id):
    """プロジェクトに属する共有リンク (期間付き + 無期限) をレンダリング"""
    cleanup_expired()
    expiring = [s for s in list_shares() if s.get("project_id") == project_id]
    persistent = [s for s in list_persistent_shares()
                  if s.get("project_id") == project_id]

    if not expiring and not persistent:
        return html.Div("共有リンクはありません", className="text-muted small")

    rows = [_share_link_row(s, "expiring") for s in expiring]
    rows += [_share_link_row(s, "persistent") for s in persistent]
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
    # token は期間付き/無期限で一意。該当する方を削除 (ver4.2)
    if not delete_share(token):
        revoke_persistent_share(token)
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


# =========================================================================
# プロジェクト復元
# =========================================================================

@callback(
    Output("restore_project_modal", "is_open"),
    [Input("open_restore_modal_btn", "n_clicks"),
     Input("close_restore_modal_btn", "n_clicks")],
    State("restore_project_modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_restore_modal(open_clicks, close_clicks, is_open):
    """復元モーダルの開閉"""
    if ctx.triggered_id in ("open_restore_modal_btn", "close_restore_modal_btn"):
        return not is_open
    return no_update


@callback(
    [Output("restore_scan_results", "children"),
     Output("restore_scan_data", "data"),
     Output("restore_execute_btn", "disabled")],
    Input("restore_scan_btn", "n_clicks"),
    State("restore_scan_folder", "value"),
    prevent_initial_call=True,
)
def execute_scan(n_clicks, folder):
    """スキャンフォルダを再帰検索し _project_meta.json を収集"""
    if not n_clicks or not folder:
        return (
            html.P("スキャンフォルダを指定してください。",
                   className="text-warning"),
            None,
            True,
        )

    from pathlib import Path
    if not Path(folder).is_dir():
        return (
            html.P(f"指定されたフォルダが見つかりません: {folder}",
                   className="text-danger"),
            None,
            True,
        )

    meta_list = scan_project_meta(folder)
    if not meta_list:
        return (
            html.P(
                "メタデータ (_project_meta.json) が見つかりませんでした。"
                "解析済みの結果フォルダを含むフォルダを指定してください。",
                className="text-warning text-center py-3",
            ),
            None,
            True,
        )

    # --- 既存プロジェクトとの照合 ---
    existing_projects = {p["id"]: p for p in list_projects()}

    cards = []
    for i, meta in enumerate(meta_list):
        proj = meta.get("project", {})
        sub = meta.get("sub_project", {})
        proj_id = proj.get("id", "")
        sub_id = sub.get("id", "")
        found_dir = meta.get("_found_dir", "")

        # ステータス判定
        existing = existing_projects.get(proj_id)
        if existing:
            existing_sub = None
            for s in existing.get("sub_projects", []):
                if s["id"] == sub_id:
                    existing_sub = s
                    break
            if existing_sub:
                status_badge = dbc.Badge(
                    "既存（ID一致）", color="warning", className="ms-2",
                )
                default_action = "update_paths"
            else:
                status_badge = dbc.Badge(
                    "新規サブプロジェクト", color="info", className="ms-2",
                )
                default_action = "restore"
        else:
            status_badge = dbc.Badge(
                "新規", color="success", className="ms-2",
            )
            default_action = "restore"

        card = dbc.Card(
            className="mb-2",
            children=dbc.CardBody(
                className="py-2",
                children=[
                    dbc.Row(
                        className="align-items-center",
                        children=[
                            dbc.Col(
                                width=7,
                                children=[
                                    html.Div([
                                        html.Strong(
                                            proj.get("name", "不明"),
                                        ),
                                        status_badge,
                                    ]),
                                    html.Small(
                                        f"サブ: {sub.get('name', '不明')}",
                                        className="text-muted d-block",
                                    ),
                                    html.Small(
                                        f"パス: {found_dir}",
                                        className="text-muted d-block",
                                        style={
                                            "overflow": "hidden",
                                            "textOverflow": "ellipsis",
                                            "whiteSpace": "nowrap",
                                            "maxWidth": "100%",
                                        },
                                        title=found_dir,
                                    ),
                                ],
                            ),
                            dbc.Col(
                                width=5,
                                className="text-end",
                                children=[
                                    dbc.Select(
                                        id={
                                            "type": "restore_action",
                                            "index": sub_id,
                                        },
                                        options=[
                                            {"label": "復元",
                                             "value": "restore"},
                                            {"label": "パス更新",
                                             "value": "update_paths"},
                                            {"label": "スキップ",
                                             "value": "skip"},
                                        ],
                                        value=default_action,
                                        size="sm",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        )
        cards.append(card)

    summary = html.P(
        f"{len(meta_list)} 件のメタデータが見つかりました。",
        className="text-info fw-bold mb-2",
    )
    return [summary] + cards, meta_list, False


@callback(
    [Output("restore_status", "children"),
     Output("project_list_refresh", "data", allow_duplicate=True),
     Output("restore_execute_btn", "disabled", allow_duplicate=True)],
    Input("restore_execute_btn", "n_clicks"),
    [State("restore_scan_data", "data"),
     State({"type": "restore_action", "index": ALL}, "value"),
     State({"type": "restore_action", "index": ALL}, "id")],
    prevent_initial_call=True,
)
def execute_restore(n_clicks, scan_data, action_values, action_ids):
    """選択されたアクションに基づいてプロジェクトを復元"""
    if not n_clicks or not scan_data:
        return no_update, no_update, no_update

    # action_map を構築: {sub_id: action}
    action_map = {}
    for aid, val in zip(action_ids, action_values):
        sub_id = aid.get("index", "")
        if sub_id:
            action_map[sub_id] = val

    messages = restore_projects_from_meta(scan_data, action_map)
    if not messages:
        return (
            dbc.Alert("復元対象がありませんでした。", color="info"),
            no_update,
            True,
        )

    from datetime import datetime
    result_items = [html.Li(m) for m in messages]
    return (
        dbc.Alert(
            children=[
                html.Strong("復元完了"),
                html.Ul(result_items, className="mb-0 mt-1"),
            ],
            color="success",
        ),
        datetime.now().isoformat(),  # project_list_refresh を更新
        True,
    )
