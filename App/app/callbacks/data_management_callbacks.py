# =============================================================================
# MSI Analysis Application - Data Management Callbacks
# 設定タブ内「データ管理」サブタブのコールバック
# =============================================================================

from datetime import datetime
from pathlib import Path

from dash import Input, Output, State, callback, ctx, no_update, html, ALL
import dash_bootstrap_components as dbc

from app.services.data_browser import (
    get_layout_summary,
    get_directory_listing,
    get_storage_stats,
    find_meta_projects_everywhere,
    list_backup_generations,
    format_bytes,
    audit_result_dirs,
    move_entry,
    preview_move,
    RESULT_DIR_STATES,
)
from app.services.project_manager import restore_projects_from_meta


# ---------------------------------------------------------------------------
# 1. 場所選択トグル: dm_loc_btn クリック → dm_state.location_key 更新
# ---------------------------------------------------------------------------

@callback(
    Output("dm_state", "data", allow_duplicate=True),
    Input({"type": "dm_loc_btn", "key": ALL}, "n_clicks"),
    State("dm_state", "data"),
    prevent_initial_call=True,
)
def on_location_select(clicks, state):
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update
    new_state = dict(state or {})
    new_state["location_key"] = ctx.triggered_id.get("key")
    new_state["subpath"] = ""  # 場所変更時はルートにリセット
    return new_state


# ---------------------------------------------------------------------------
# 2. 配置サマリ表示
# ---------------------------------------------------------------------------

@callback(
    Output("dm_layout_summary", "children"),
    Input("dm_state", "data"),
    Input("dm_refresh_btn", "n_clicks"),
)
def render_layout_summary(_state, _n):
    rows = get_layout_summary()
    header = html.Tr([
        html.Th("場所"),
        html.Th("コンテナ内パス"),
        html.Th("環境変数"),
        html.Th("状態"),
    ])
    body_rows = []
    for r in rows:
        if r["exists"]:
            status = html.Span("✅ 存在", className="text-success")
        else:
            status = html.Span("⚠ 未作成", className="text-warning")
        if r.get("env_var") and r.get("env_value"):
            env_cell = html.Code(
                f"{r['env_var']}={r['env_value']}",
                style={"fontSize": "0.8rem"},
            )
        elif r.get("env_var"):
            env_cell = html.Small(
                f"{r['env_var']} (未設定)",
                className="text-muted",
            )
        else:
            env_cell = html.Span("-", className="text-muted")
        body_rows.append(html.Tr([
            html.Td([
                html.Strong(r["label"]),
                html.Br(),
                html.Small(r["description"], className="text-muted"),
            ]),
            html.Td(
                html.Code(r["path"], style={"fontSize": "0.85rem"}),
            ),
            html.Td(env_cell),
            html.Td(status),
        ]))
    return dbc.Table(
        [html.Thead(header), html.Tbody(body_rows)],
        hover=True, striped=True, size="sm", responsive=True,
    )


# ---------------------------------------------------------------------------
# 3. ツリービュー更新（場所変更 + パンくず/フォルダクリック）
# ---------------------------------------------------------------------------

@callback(
    [Output("dm_directory_listing", "children"),
     Output("dm_breadcrumb", "children")],
    Input("dm_state", "data"),
    Input("dm_refresh_btn", "n_clicks"),
)
def render_directory(state, _n):
    state = state or {}
    key = state.get("location_key") or "desi"
    subpath = state.get("subpath") or ""
    result = get_directory_listing(key, subpath)

    # パンくず構築
    crumbs = []
    for i, crumb in enumerate(result.get("breadcrumb", [])):
        if i > 0:
            crumbs.append(html.Span(" / ", className="text-muted mx-1"))
        crumbs.append(dbc.Button(
            crumb["name"] or "(root)",
            id={"type": "dm_crumb", "path": crumb["path"]},
            size="sm", color="link", className="p-0",
            style={"fontSize": "0.85rem"},
        ))
    if not result.get("exists"):
        crumbs.append(html.Small(
            " (フォルダが存在しません)",
            className="text-warning ms-2",
        ))

    # ファイル一覧
    items = result.get("items", [])
    current_dir = result.get("current_dir") or ""
    root = result.get("root") or ""
    list_items = []

    # 「..」(root より上には行かせない)
    if current_dir and root:
        try:
            if Path(current_dir).resolve() != Path(root).resolve():
                parent_path = str(Path(current_dir).parent)
                list_items.append(html.Div(
                    "\U0001F4C1 ..",
                    id={"type": "dm_item", "path": parent_path},
                    className="dm-item",
                    n_clicks=0,
                    style={
                        "cursor": "pointer", "padding": "4px 8px",
                        "borderRadius": "3px",
                    },
                ))
        except (OSError, ValueError):
            pass

    for item in items:
        list_items.append(html.Div(
            f"{item['icon']} {item['name']}",
            id={"type": "dm_item", "path": item["path"]},
            className="dm-item",
            n_clicks=0,
            style={
                "cursor": "pointer" if item["is_dir"] else "default",
                "padding": "4px 8px", "borderRadius": "3px",
                "color": "#212529" if item["is_dir"] else "#6c757d",
            },
        ))

    if not list_items:
        listing = html.Div(
            "（空フォルダ、または閲覧権限なし）",
            className="text-muted", style={"padding": "10px"},
        )
    else:
        listing = html.Div(list_items)

    return listing, crumbs


# ファイル一覧のフォルダクリック → subpath 更新
@callback(
    Output("dm_state", "data", allow_duplicate=True),
    Input({"type": "dm_item", "path": ALL}, "n_clicks"),
    State("dm_state", "data"),
    prevent_initial_call=True,
)
def on_item_click(clicks, state):
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update
    clicked_path = ctx.triggered_id.get("path", "")
    if not clicked_path or not Path(clicked_path).is_dir():
        return no_update
    new_state = dict(state or {})
    # 絶対パスで保存 (get_directory_listing 内の _safe_resolve が root 内に制限する)
    new_state["subpath"] = clicked_path
    return new_state


# パンくずクリック → subpath 更新
@callback(
    Output("dm_state", "data", allow_duplicate=True),
    Input({"type": "dm_crumb", "path": ALL}, "n_clicks"),
    State("dm_state", "data"),
    prevent_initial_call=True,
)
def on_crumb_click(clicks, state):
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update
    new_state = dict(state or {})
    new_state["subpath"] = ctx.triggered_id.get("path", "")
    return new_state


# ---------------------------------------------------------------------------
# 4. プロジェクトスキャン
# ---------------------------------------------------------------------------

@callback(
    [Output("dm_scan_results", "children"),
     Output("dm_scan_cache", "data"),
     Output("dm_scan_summary", "children")],
    Input("dm_scan_btn", "n_clicks"),
    prevent_initial_call=True,
)
def on_scan(n_clicks):
    if not n_clicks:
        return no_update, no_update, no_update
    # ver56.2: 「解析出力」だけでなく 4 か所すべてを見る。実運用では結果を
    # 生データフォルダの隣に出しているため、従来は大半が検出できなかった。
    meta_list = find_meta_projects_everywhere()
    if not meta_list:
        return (
            html.Div(
                "（_project_meta.json を持つフォルダが見つかりません）",
                className="text-muted",
            ),
            [],
            "スキャン結果: 0 件",
        )

    cards = []
    for i, meta in enumerate(meta_list):
        proj_info = meta.get("project", {})
        sub_info = meta.get("sub_project", {})
        proj_name = proj_info.get("name", "(名称未設定)")
        sub_name = sub_info.get("name", "(名称未設定)")
        found_dir = meta.get("_found_dir", "")
        cards.append(dbc.Card(className="mb-2", body=True, children=[
            dbc.Row(align="center", children=[
                dbc.Col(width=9, children=[
                    html.Strong(f"{proj_name} / {sub_name}"),
                    dbc.Badge(
                        meta.get("_found_location", ""),
                        color="info", className="ms-2",
                    ) if meta.get("_found_location") else "",
                    html.Br(),
                    html.Small(
                        found_dir,
                        className="text-muted",
                        style={"wordBreak": "break-all"},
                    ),
                ]),
                dbc.Col(width=3, className="text-end", children=[
                    dbc.Button(
                        "↩ 復元",
                        id={"type": "dm_restore_btn", "index": i},
                        color="success", size="sm",
                    ),
                ]),
            ]),
        ]))

    return cards, meta_list, f"スキャン結果: {len(meta_list)} 件"


# ---------------------------------------------------------------------------
# 5. ワンクリック復元
# ---------------------------------------------------------------------------

@callback(
    [Output("dm_toast", "is_open"),
     Output("dm_toast", "header"),
     Output("dm_toast", "children"),
     Output("dm_toast", "icon"),
     Output("project_list_refresh", "data", allow_duplicate=True)],
    Input({"type": "dm_restore_btn", "index": ALL}, "n_clicks"),
    State("dm_scan_cache", "data"),
    State("project_list_refresh", "data"),
    prevent_initial_call=True,
)
def on_restore(clicks, scan_cache, refresh_token):
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update, no_update, no_update, no_update, no_update
    idx = ctx.triggered_id.get("index")
    if idx is None or not scan_cache or idx >= len(scan_cache):
        return no_update, no_update, no_update, no_update, no_update
    meta = scan_cache[idx]
    sub_id = (meta.get("sub_project") or {}).get("id", "")
    if not sub_id:
        return True, "復元失敗", "メタデータに sub_id がありません", "danger", no_update
    try:
        messages = restore_projects_from_meta([meta], {sub_id: "restore"})
    except Exception as exc:
        return True, "復元失敗", f"例外発生: {exc}", "danger", no_update
    msg = messages[0] if messages else "（変更なし）"
    icon = "success" if msg.startswith("✅") else "info"
    try:
        new_refresh = int(refresh_token or 0) + 1
    except (TypeError, ValueError):
        new_refresh = 1
    return True, "プロジェクト復元", msg, icon, new_refresh


# ---------------------------------------------------------------------------
# 4.5. 要注意の結果フォルダ
# ---------------------------------------------------------------------------

@callback(
    Output("dm_result_audit", "children"),
    Input("dm_state", "data"),
    Input("dm_refresh_btn", "n_clicks"),
)
def render_result_audit(_state, _n):
    rows = audit_result_dirs()
    if not rows:
        return dbc.Alert(
            "✅ 登録済みの結果フォルダはすべて永続化された場所にあります。",
            color="success", className="py-2 mb-0 small",
        )

    header = html.Tr([
        html.Th("プロジェクト / サブ"),
        html.Th("結果フォルダ"),
        html.Th("状態"),
        html.Th(""),
    ])
    body_rows = []
    for r in rows:
        volatile = r["state"] == "volatile"
        body_rows.append(html.Tr([
            html.Td([
                html.Strong(r["project_name"]),
                html.Br(),
                html.Small(r["sub_name"], className="text-muted"),
            ]),
            html.Td(html.Code(r["path"], style={
                "fontSize": "0.8rem", "wordBreak": "break-all",
            })),
            html.Td(html.Span(
                ("⚠ " if volatile else "✗ ") + RESULT_DIR_STATES[r["state"]],
                className="text-warning" if volatile else "text-danger",
                style={"fontSize": "0.85rem"},
            )),
            html.Td(
                # 一時領域のものはそのまま下の「フォルダの移動」に送れる。
                # 実体が無いものは移動しようがないのでボタンを出さない。
                dbc.Button(
                    "移動元に入れる",
                    id={"type": "dm_audit_pick", "path": r["path"]},
                    color="warning", outline=True, size="sm",
                ) if volatile else "",
                className="text-end",
            ),
        ]))
    return dbc.Table(
        [html.Thead(header), html.Tbody(body_rows)],
        hover=True, striped=True, size="sm", responsive=True,
    )


@callback(
    Output("dm_move_src", "value", allow_duplicate=True),
    Input({"type": "dm_audit_pick", "path": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def on_audit_pick(clicks):
    """一覧の「移動元に入れる」→ 移動元フォルダ欄に流し込む。"""
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update
    return ctx.triggered_id.get("path", "") or no_update


# ---------------------------------------------------------------------------
# 5.5. フォルダ移動（確認モーダル → 実行）
# ---------------------------------------------------------------------------

@callback(
    [Output("dm_move_confirm_modal", "is_open"),
     Output("dm_move_confirm_body", "children"),
     Output("dm_move_pending", "data"),
     Output("dm_toast", "is_open", allow_duplicate=True),
     Output("dm_toast", "header", allow_duplicate=True),
     Output("dm_toast", "children", allow_duplicate=True),
     Output("dm_toast", "icon", allow_duplicate=True)],
    Input("dm_move_btn", "n_clicks"),
    [State("dm_move_src", "value"),
     State("dm_move_dest_path", "value")],
    prevent_initial_call=True,
)
def on_move_request(n_clicks, src, dest):
    """移動ボタン → 事前検証。問題なければ確認モーダルを開く。"""
    if not n_clicks:
        return (no_update,) * 7
    pre = preview_move(src or "", dest or "")
    if not pre["ok"]:
        return (
            False, no_update, None,
            True, "移動できません", pre["msg"], "danger",
        )

    rows = [
        ("移動元", pre["src"]),
        ("移動先", f"[{pre['dest_label']}] {pre['target']}"),
        ("内容", f"{pre['file_count']:,} ファイル / {format_bytes(pre['used_bytes'])}"),
    ]
    body = [
        html.Div([
            html.Strong(f"{label}: "),
            html.Span(value, style={"wordBreak": "break-all"}),
        ], className="mb-1")
        for label, value in rows
    ]
    if not pre["same_fs"]:
        body.append(dbc.Alert(
            "別のファイルシステムへの移動のためコピー＋削除になります。"
            "容量によっては数分かかり、完了までブラウザの操作が返りません。",
            color="warning", className="py-2 mb-1 small",
        ))
    body.append(html.Small(
        "移動後、結果フォルダ内の _project_meta.json をもとに"
        "プロジェクトの参照先パスを自動更新します。",
        className="text-muted",
    ))
    # 検証結果をそのまま持ち回す。実行時に入力欄を読み直すと、
    # モーダルを開いたまま移動先を書き換えられてしまう。
    return True, body, pre, no_update, no_update, no_update, no_update


@callback(
    Output("dm_move_confirm_modal", "is_open", allow_duplicate=True),
    Input("dm_move_cancel_btn", "n_clicks"),
    prevent_initial_call=True,
)
def on_move_cancel(n_clicks):
    if not n_clicks:
        return no_update
    return False


def _remap_open_result_folder(current: str, old_path: str, new_path: str) -> str:
    """インタラクティブ解析が開いているパスを、移動後のパスに読み替える。

    移動したフォルダ自身か、その配下を指しているときだけ書き換える。
    無関係なフォルダを開いているなら空文字を返す（＝触らない）。
    プロジェクト ID ではなくパスで判定するので、`_project_meta.json` を持たない
    結果フォルダでも差替えが効く。
    """
    if not current or not old_path or not new_path:
        return ""
    try:
        cur = Path(current).resolve()
        old = Path(old_path).resolve()
    except OSError:
        return ""
    if cur == old:
        return new_path
    try:
        rel = cur.relative_to(old)
    except ValueError:
        return ""
    return str(Path(new_path) / rel)


@callback(
    [Output("dm_move_confirm_modal", "is_open", allow_duplicate=True),
     Output("dm_move_src", "value", allow_duplicate=True),
     Output("dm_state", "data", allow_duplicate=True),
     Output("dm_toast", "is_open", allow_duplicate=True),
     Output("dm_toast", "header", allow_duplicate=True),
     Output("dm_toast", "children", allow_duplicate=True),
     Output("dm_toast", "icon", allow_duplicate=True),
     Output("project_list_refresh", "data", allow_duplicate=True),
     Output("interactive_result_folder", "value", allow_duplicate=True),
     Output("dm_move_skip_autoload", "data", allow_duplicate=True)],
    Input("dm_move_exec_btn", "n_clicks"),
    [State("dm_move_pending", "data"),
     State("project_list_refresh", "data"),
     State("interactive_result_folder", "value")],
    prevent_initial_call=True,
)
def on_move_execute(n_clicks, pending, refresh_token, open_result_folder):
    """確認モーダルの「移動を実行」→ 移動＋パス更新。"""
    if not n_clicks or not pending or not pending.get("src"):
        return (no_update,) * 10

    try:
        result = move_entry(pending["src"], pending.get("dest") or "")
    except Exception as exc:  # noqa: BLE001
        return (
            False, no_update, no_update,
            True, "移動失敗", f"例外発生: {exc}", "danger",
            no_update, no_update, no_update,
        )

    if not result["ok"]:
        return (
            False, no_update, no_update,
            True, "移動失敗", result["msg"], "danger",
            no_update, no_update, no_update,
        )

    updates = result.get("path_updates") or []
    message = [html.Div(result["msg"], style={"wordBreak": "break-all"})]
    if updates:
        message += [html.Div(u, className="small") for u in updates]
    else:
        message.append(html.Small(
            "（_project_meta.json が無いため、パス更新は行っていません）",
            className="text-muted",
        ))

    # 今インタラクティブ解析が開いているのが移動したフォルダなら、新パスに差し替える。
    # 自動読込まで走ると移動直後は Seurat 抽出キャッシュがミスして数分かかるので、
    # skip フラグを立てて auto_load_on_rds_ready を 1 回だけ黙らせる。
    remapped = _remap_open_result_folder(
        open_result_folder or "", result.get("old_path", ""), result["new_path"],
    )
    if remapped:
        message.append(html.Small(
            "インタラクティブ解析の結果フォルダを新しいパスに切り替えました。"
            "再読込は「データを読み込む」から。",
            className="text-muted d-block",
        ))

    # 移動先の一覧に切り替えて、着地したことを目で確認できるようにする
    new_state = {
        "location_key": pending.get("dest_key") or "output",
        "subpath": str(Path(result["new_path"]).parent),
    }
    try:
        new_refresh = int(refresh_token or 0) + 1
    except (TypeError, ValueError):
        new_refresh = 1

    return (
        False, "", new_state,
        True, "移動完了", message, "success",
        new_refresh,
        remapped or no_update,
        True if remapped else no_update,
    )


# ---------------------------------------------------------------------------
# 6. ストレージ統計
# ---------------------------------------------------------------------------

@callback(
    Output("dm_storage_stats", "children"),
    Input("dm_state", "data"),
    Input("dm_refresh_btn", "n_clicks"),
)
def render_storage_stats(_state, _n):
    stats = get_storage_stats()
    header = html.Tr([
        html.Th("場所"),
        html.Th("ファイル数"),
        html.Th("使用容量"),
        html.Th("ディスク空き / 全体"),
    ])
    body_rows = []
    for s in stats:
        if not s["exists"]:
            body_rows.append(html.Tr([
                html.Td(s["label"]),
                html.Td(html.Span("未作成", className="text-warning")),
                html.Td("-"),
                html.Td("-"),
            ]))
            continue
        if s["disk_total_bytes"]:
            disk_cell = (
                f"{format_bytes(s['disk_free_bytes'])} / "
                f"{format_bytes(s['disk_total_bytes'])}"
            )
        else:
            disk_cell = "-"
        body_rows.append(html.Tr([
            html.Td(s["label"]),
            html.Td(f"{s['file_count']:,}"),
            html.Td(format_bytes(s["used_bytes"])),
            html.Td(disk_cell, style={"fontSize": "0.85rem"}),
        ]))
    return dbc.Table(
        [html.Thead(header), html.Tbody(body_rows)],
        hover=True, striped=True, size="sm", responsive=True,
    )


# ---------------------------------------------------------------------------
# 7. バックアップ世代一覧
# ---------------------------------------------------------------------------

@callback(
    Output("dm_backup_list", "children"),
    Input("dm_refresh_btn", "n_clicks"),
)
def render_backup_list(_n):
    backups = list_backup_generations(limit=20)
    if not backups:
        return html.Div(
            "（バックアップなし）",
            className="text-muted",
        )
    items = []
    for b in backups:
        created = b.get("created_at", "")
        if created:
            try:
                created = datetime.fromisoformat(created).strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass
        items.append(dbc.ListGroupItem(
            children=[
                html.Small(
                    b.get("name", ""),
                    style={"fontFamily": "monospace", "wordBreak": "break-all"},
                ),
                html.Br(),
                html.Small(
                    f"{created}  /  {b.get('size_kb', 0)} KB",
                    className="text-muted",
                ),
            ],
            className="py-1 px-2",
        ))
    return dbc.ListGroup(items, flush=True)
