# =============================================================================
# MSI Analysis Application - Tab URL Routing
# 解析画面のタブ ↔ URL 双方向同期
#
# 解析画面 (current_page="analysis") の dbc.Tabs (id="main_tabs") を URL に
# 反映し、URL を共有しただけで他人が同じタブを開けるようにする。
#
# URL スキーム:
#   /app/settings     → 解析設定タブ
#   /app/results      → 結果閲覧タブ
#   /app/interactive  → インタラクティブ解析タブ
#   /app/history      → セッション履歴タブ
#   /                 → デフォルト (landing 経由で settings)
#
# 認証: /app/* は Tier A 必須 (auth_middleware のデフォルト)。共有用ではなく
# 解析者自身が deep link / ブックマークするための機能。
# 共有目的の URL は /share/<token> (期間付き、Tier B) または
# /view/<token> (無期限、認証不要) を使う。
# =============================================================================

import logging

from dash import Input, Output, State, callback, no_update

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 0) URL → current_page: /app/* で直接 deep link されたら analysis ページへ
#
#    重要: url_bar.pathname を Input、current_page.data を Output に取る
#    callback は既に share_callbacks.route_share_url が存在する。Dash 2.x
#    では同じ (Input, Output) ペアが複数 callback に存在すると allow_
#    duplicate=True を付けても **実行時** に "Output ... is already in use"
#    エラーとなり、UI 全体が反応しなくなる。
#
#    そのため、lite_view_callbacks の route_lite_url / navigate_to_lite_page
#    と同じ二段パターンを採用:
#      step 1: url_bar.pathname  → app_path_target_store.data  (中間 Store)
#      step 2: app_path_target_store.data → current_page.data  (Input が違う)
# ---------------------------------------------------------------------------

@callback(
    Output("app_path_target_store", "data"),
    Input("url_bar", "pathname"),
    prevent_initial_call=True,
)
def _detect_app_path(pathname):
    """pathname が /app/* なら target を Store に書く。それ以外は no_update。"""
    if not pathname:
        return no_update
    if pathname.startswith("/app/"):
        return {"pathname": pathname}
    return no_update


@callback(
    Output("current_page", "data", allow_duplicate=True),
    Input("app_path_target_store", "data"),
    State("current_page", "data"),
    prevent_initial_call=True,
)
def _route_app_url_to_analysis(target, current_page):
    """中間 Store が更新されたら current_page を analysis に遷移。"""
    if not target:
        return no_update
    if current_page != "analysis":
        return "analysis"
    return no_update

# tab_id ↔ URL path セグメントのマッピング
_TAB_TO_PATH = {
    "settings": "/app/settings",
    "results": "/app/results",
    "interactive": "/app/interactive",
    "history": "/app/history",
}
_PATH_TO_TAB = {v: k for k, v in _TAB_TO_PATH.items()}


# ---------------------------------------------------------------------------
# 1) URL → タブ: ページロード時 / URL 直打ち時に対応するタブを active 化
# ---------------------------------------------------------------------------

@callback(
    # main_tabs.active_tab は project_callbacks.py / session_callbacks.py で
    # 既に allow_duplicate=True で複数 callback が書込んでいるため、ここでも
    # allow_duplicate=True が必須。欠落すると Dash が DuplicateCallback を
    # 投げ、suppress_callback_exceptions=True 下で当該 Output 連動の他
    # callback (復元ボタン等) が静かに無効化される。
    Output("main_tabs", "active_tab", allow_duplicate=True),
    Input("url_bar", "pathname"),
    State("main_tabs", "active_tab"),
    # duplicate output で初回ロード時にも URL → タブ同期したいので
    # "initial_duplicate" を指定 (False は duplicate output と非互換)。
    prevent_initial_call="initial_duplicate",
)
def _sync_tab_from_url(pathname, current_active):
    if not pathname:
        return no_update
    # 完全一致または末尾スラッシュ吸収
    normalized = pathname.rstrip("/") or "/"
    if normalized == "/":
        # ルートでは現在タブを変更しない (landing からの遷移を尊重)
        return no_update
    tab_id = _PATH_TO_TAB.get(normalized)
    if tab_id and tab_id != current_active:
        return tab_id
    return no_update


# ---------------------------------------------------------------------------
# 2) タブ → URL: タブクリックで URL パスを更新 (履歴は積まない)
# ---------------------------------------------------------------------------

@callback(
    Output("url_bar", "pathname", allow_duplicate=True),
    Input("main_tabs", "active_tab"),
    [State("url_bar", "pathname"),
     State("current_page", "data"),
     State("shared_session", "data")],
    prevent_initial_call=True,
)
def _sync_url_from_tab(active_tab, current_pathname, current_page, shared):
    # 解析画面 (analysis ページ) 以外は URL を書き換えない
    # (landing / shared / lite / persistent_view 等で誤動作させない)
    if current_page != "analysis":
        return no_update
    # ver4.0: 共有モードでは /share/<token> /view/<token> の URL を保持する。
    # /app/interactive に書き換えると Tier B/匿名ユーザーが再読込で弾かれる。
    if shared and shared.get("active"):
        return no_update
    if not active_tab:
        return no_update
    target = _TAB_TO_PATH.get(active_tab)
    if not target:
        return no_update
    # 既に同じパスなら no-op (リダイレクトループ防止)
    if current_pathname and current_pathname.rstrip("/") == target:
        return no_update
    return target
