# =============================================================================
# MSI Analysis Application - Session Callbacks
# セッション管理 コールバック
# =============================================================================

from dash import Input, Output, State, callback, ctx, no_update, html
import dash_bootstrap_components as dbc

from app.services.backup_manager import list_backups


# ---------------------------------------------------------------------------
# バックアップ一覧モーダル
# ---------------------------------------------------------------------------

@callback(
    [Output("backup_list_modal", "is_open"),
     Output("backup_list_body", "children")],
    [Input("open_backup_list_btn", "n_clicks"),
     Input("close_backup_list_btn", "n_clicks")],
    State("backup_list_modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_backup_list_modal(open_clicks, close_clicks, is_open):
    if not ctx.triggered_id:
        return no_update, no_update

    if ctx.triggered_id == "close_backup_list_btn":
        return False, no_update

    # モーダルを開く → バックアップ一覧を取得
    backups = list_backups()
    if not backups:
        body = html.P("バックアップファイルはありません。",
                       className="text-muted")
    else:
        rows = []
        for b in backups:
            rows.append(html.Tr([
                html.Td(b["name"], style={"fontSize": "0.85rem"}),
                html.Td(f"{b['size_kb']:.1f} KB",
                         style={"fontSize": "0.85rem", "textAlign": "right"}),
                html.Td(b["created_at"],
                         style={"fontSize": "0.85rem"}),
            ]))
        body = dbc.Table([
            html.Thead(html.Tr([
                html.Th("ファイル名"),
                html.Th("サイズ", style={"textAlign": "right"}),
                html.Th("作成日時"),
            ])),
            html.Tbody(rows),
        ], bordered=True, hover=True, size="sm", striped=True)

    return True, body
