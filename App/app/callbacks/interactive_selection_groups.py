# =============================================================================
# MSI Analysis Application - Selection Groups callbacks (Phase 3)
# lasso/box 選択を「名前付き永続グループ」として保存・改名・削除・結合・CSV 入出力し、
# 「現在の選択に読込」で下流 (選択統計 P1 / アプリ内DE P2) に再利用できるようにする。
#
# 変更系 (保存/改名/削除/結合/取込/ディスク読込) は ctx で 1 コールバックに集約し、
# selection_groups_store への多重 Output を避ける。永続化は services.selection_groups。
# =============================================================================

import base64
import logging

from dash import Input, Output, State, callback, ctx, no_update, html, dcc
from dash.exceptions import PreventUpdate

from app.services import selection_groups as sg

logger = logging.getLogger("msi.interactive.selgroups")


def _status(msg, cls="text-muted small"):
    return html.Span(msg, className=cls)


# ---------------------------------------------------------------------------
# 変更系: ディスク読込 + 保存/改名/削除/結合/CSV取込
# ---------------------------------------------------------------------------
@callback(
    [Output("selection_groups_store", "data"),
     Output("selection_groups_status", "children")],
    [Input("seurat_rds_path_store", "data"),
     Input("btn_save_selection_group", "n_clicks"),
     Input("btn_rename_group", "n_clicks"),
     Input("btn_delete_group", "n_clicks"),
     Input("btn_combine_groups", "n_clicks"),
     Input("upload_selection_groups", "contents")],
    [State("selection_groups_store", "data"),
     State("selected_cell_ids_store", "data"),
     State("selection_group_name", "value"),
     State("selection_group_select", "value"),
     State("selection_group_rename", "value"),
     State("selection_groups_combine", "value"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def mutate_selection_groups(rds_trigger, _n_save, _n_ren, _n_del, _n_comb,
                            upload_contents, state, selected_ids, new_name,
                            sel_gid, rename_text, combine_gids, rds_path):
    trig = ctx.triggered_id
    state = state or sg.empty_state()

    # データロード時はディスクから読み直す (保存はしない)
    if trig == "seurat_rds_path_store":
        if not rds_path:
            return sg.empty_state(), no_update
        return sg.load_groups(rds_path), no_update

    if trig == "btn_save_selection_group":
        if not selected_ids:
            return no_update, _status("先に UMAP で選択してください", "text-warning small")
        state = sg.add_group(state, new_name, selected_ids)
        msg = _status(f"保存しました: {state['groups'][-1]['name']} "
                      f"({len(selected_ids)} px)", "text-success small")

    elif trig == "btn_rename_group":
        if not sel_gid:
            return no_update, _status("改名するグループを選択してください", "text-warning small")
        state = sg.rename_group(state, sel_gid, rename_text)
        msg = _status("改名しました", "text-success small")

    elif trig == "btn_delete_group":
        if not sel_gid:
            return no_update, _status("削除するグループを選択してください", "text-warning small")
        state = sg.delete_group(state, sel_gid)
        msg = _status("削除しました", "text-success small")

    elif trig == "btn_combine_groups":
        if not combine_gids or len(combine_gids) < 2:
            return no_update, _status("結合するグループを2つ以上選択してください", "text-warning small")
        state = sg.combine_groups(state, combine_gids)
        msg = _status("結合グループを作成しました", "text-success small")

    elif trig == "upload_selection_groups":
        if not upload_contents:
            raise PreventUpdate
        try:
            _ctype, b64 = str(upload_contents).split(",", 1)
            text = base64.b64decode(b64).decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            return no_update, _status(f"CSV 読込失敗: {e}", "text-danger small")
        imported = sg.groups_from_csv(text)
        merged = dict(state)
        groups = list(state.get("groups", []))
        for g in imported.get("groups", []):
            state2 = sg.add_group({"groups": groups}, g["name"], g["cell_ids"])
            groups = state2["groups"]
        merged["groups"] = groups
        state = merged
        msg = _status(f"CSV から {len(imported.get('groups', []))} グループ取込",
                      "text-success small")
    else:
        raise PreventUpdate

    if rds_path:
        sg.save_groups(rds_path, state)
    return state, msg


# ---------------------------------------------------------------------------
# 描画: テーブル + セレクタ選択肢
# ---------------------------------------------------------------------------
@callback(
    [Output("selection_groups_table", "data"),
     Output("selection_group_select", "options"),
     Output("selection_groups_combine", "options")],
    Input("selection_groups_store", "data"),
    prevent_initial_call=True,
)
def render_selection_groups(state):
    groups = (state or {}).get("groups", [])
    rows = [{"name": g.get("name", ""), "n": len(g.get("cell_ids", [])),
             "color": g.get("color", "")} for g in groups]
    options = [{"label": f"{g.get('name','')} ({len(g.get('cell_ids', []))})",
                "value": g.get("id")} for g in groups]
    return rows, options, options


# ---------------------------------------------------------------------------
# グループを「現在の選択」に読込 → 選択統計 (P1) / アプリ内DE (P2) で再利用
# selected_cell_ids_store は P1 capture も書くため allow_duplicate=True。
# ---------------------------------------------------------------------------
@callback(
    [Output("selected_cell_ids_store", "data", allow_duplicate=True),
     Output("selection_groups_load_status", "children")],
    Input("btn_load_group_to_selection", "n_clicks"),
    [State("selection_group_select", "value"),
     State("selection_groups_store", "data")],
    prevent_initial_call=True,
)
def load_group_to_selection(n_clicks, gid, state):
    if not n_clicks or not gid:
        raise PreventUpdate
    for g in (state or {}).get("groups", []):
        if g.get("id") == gid:
            ids = g.get("cell_ids", [])
            return ids, _status(
                f"「{g.get('name','')}」を現在の選択に読込 ({len(ids)} px) "
                "— 選択統計・選択DE に反映", "text-success small")
    raise PreventUpdate


# ---------------------------------------------------------------------------
# CSV 出力 (CellID,Group)
# ---------------------------------------------------------------------------
@callback(
    Output("dl_selection_groups_csv", "data"),
    Input("btn_export_groups", "n_clicks"),
    State("selection_groups_store", "data"),
    prevent_initial_call=True,
)
def export_selection_groups(n_clicks, state):
    if not n_clicks:
        raise PreventUpdate
    groups = (state or {}).get("groups", [])
    if not groups:
        raise PreventUpdate
    return dict(content=sg.groups_to_csv(state), filename="selection_groups.csv")
