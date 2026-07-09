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
#   /open/<pid>/<sid> → 指定プロジェクトをフル解析画面へ直接ロード
#                       (ChatGPT が返す「解析ページを開くリンク」の着地点)
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
    Output("url_bar", "pathname", allow_duplicate=True),
    Input("app_path_target_store", "data"),
    State("current_page", "data"),
    prevent_initial_call=True,
)
def _route_app_url_to_analysis(target, current_page):
    """/app/* deep link の扱い。

    in-session（既に analysis）でタブ→URL同期により /app/* が来た場合は何もしない
    （タブ→URL のブックマークを維持）。一方、リロードやランディングからの /app/* deep
    link（current_page が "analysis" でない）は、ブラウザにプロジェクト選択が残っておらず
    空のインタラクティブ解析が開いてしまうため、analysis へ遷移させず URL を "/" に
    正規化してプロジェクト一覧に留める。
    """
    if not target:
        return no_update, no_update
    if current_page == "analysis":
        return no_update, no_update
    # 初回ロード/ランディングからの /app/* → 一覧に留め、URL をクリーンにする
    return no_update, "/"

# tab_id ↔ URL path セグメントのマッピング
_TAB_TO_PATH = {
    "settings": "/app/settings",
    "interactive": "/app/interactive",
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
    # 初回ロード（リロード）では URL からタブを復元しない（=空のインタラクティブ解析を
    # 開かせない）。in-session の URL 変化（タブ→URL 同期や back/forward）には追従する。
    # allow_duplicate 出力には prevent_initial_call の指定が必須（True は可、False は不可）。
    prevent_initial_call=True,
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


# ---------------------------------------------------------------------------
# 3) URL → フル解析画面: /open/<pid>/<sid> で特定プロジェクトを直接開く
#
#    ChatGPT が返す「解析ページを開くリンク」の着地点。共有リンク
#    (share_callbacks.route_share_url) と同じロード経路をたどるが、shared_session
#    を立てないため、共有モードではなく解析者自身の通常表示になる。
#
#    /app/* と違い URL 自体に pid/sid を含むので、リロード/deep link でもプロジェクト
#    を特定でき、_route_app_url_to_analysis のような "/" 正規化は不要。
#
#    二段パターン（既存の app_path / lite と同じ）:
#      step 1: url_bar.pathname       → open_target_store.data   (中間 Store)
#      step 2: open_target_store.data → interactive_* / current_page / main_tabs
#    step 2 の Input は open_target_store なので、url_bar.pathname を Input に持つ
#    既存 callback (route_share_url / _sync_tab_from_url) と Output ペアが衝突しない
#    （current_page / main_tabs.active_tab 等は他 callback が書くため allow_duplicate 必須）。
# ---------------------------------------------------------------------------

@callback(
    Output("open_target_store", "data"),
    Input("url_bar", "pathname"),
    prevent_initial_call=True,
)
def _detect_open_path(pathname):
    """pathname が /open/<pid>/<sid> なら (pid, sid) を Store に書く。それ以外は no_update。"""
    from app.services.deeplink import parse_open_path
    parsed = parse_open_path(pathname)
    if not parsed:
        return no_update
    pid, sid = parsed
    return {"project_id": pid, "sub_project_id": sid}


@callback(
    [Output("current_page", "data", allow_duplicate=True),
     Output("main_tabs", "active_tab", allow_duplicate=True),
     Output("interactive_result_folder", "value", allow_duplicate=True),
     Output("interactive_msi_folder", "value", allow_duplicate=True),
     Output("interactive_project_select", "value", allow_duplicate=True),
     Output("interactive_sub_project_select", "value", allow_duplicate=True),
     Output("interactive_entry_mode", "data", allow_duplicate=True),
     Output("current_sub_project_id", "data", allow_duplicate=True)],
    Input("open_target_store", "data"),
    prevent_initial_call=True,
)
def _open_project_in_interactive(target):
    """指定プロジェクトをフル解析画面（インタラクティブ解析タブ）へ自動ロードする。

    通常のカードクリック経路 sub_action_interactive と同じストア群を設定する。
    要点は interactive_entry_mode="sub_project" で、これが
    auto_load_on_rds_ready（interactive_result_folder → rds スキャン → 自動ロード）
    を起動する。shared_session は立てないので共有モードにはならず、解析者自身の
    通常表示になる。
    """
    if not target:
        return (no_update,) * 8
    pid = target.get("project_id")
    sid = target.get("sub_project_id")
    if not pid or not sid:
        return (no_update,) * 8
    from app.services.project_manager import get_sub_project
    sub = get_sub_project(pid, sid)
    if not sub:
        logger.info("open deep link: project/sub not found pid=%s sid=%s", pid, sid)
        return (no_update,) * 8
    result_dir = sub.get("last_result_dir") or sub.get("output_dir", "")
    data_folder = sub.get("data_folder", "")
    return (
        "analysis",          # current_page
        "interactive",       # main_tabs.active_tab
        result_dir or no_update,    # interactive_result_folder.value
        data_folder or no_update,   # interactive_msi_folder.value
        pid,                 # interactive_project_select.value
        sid,                 # interactive_sub_project_select.value
        "sub_project",       # interactive_entry_mode.data（自動ロードの鍵）
        sid,                 # current_sub_project_id.data
    )
