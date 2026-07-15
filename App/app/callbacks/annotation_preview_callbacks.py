# =============================================================================
# MSI Analysis Application - Annotation Preview Callbacks
# サブプロジェクトカードの「化合物名」ボタン → 生データを開かずに注釈状況をモーダル表示。
# =============================================================================

import logging

from dash import (
    Input, Output, State, callback, ctx, no_update, html, dash_table, ALL,
)
import dash_bootstrap_components as dbc

from app.services.project_manager import get_sub_project
from app.services.annotation_inspect import inspect_annotations

logger = logging.getLogger("msi.annotation_preview")

# プレビュー表の列（サイドカーにある列のみ描画）
_PREVIEW_COLS = [
    ("mz", "m/z"),
    ("display_name", "表示名"),
    ("compound", "化合物名"),
    ("adduct", "アダクト"),
    ("formula", "組成式"),
]


def _render(info: dict) -> html.Div:
    """inspect_annotations の結果をモーダル本体に描画する。"""
    status = info.get("status")

    if status == "none":
        return html.Div(dbc.Alert(
            info.get("note") or "化合物名（注釈）は含まれていません（m/z のみ）。",
            color="secondary", className="mb-0 py-2"))
    if status == "unknown":
        return html.Div(dbc.Alert(
            info.get("note") or "確認できませんでした。",
            color="warning", className="mb-0 py-2"))

    # status == "annotated"
    children = []
    n_ann = int(info.get("n_annotated") or 0)
    n_tot = int(info.get("n_total") or 0)
    cov = info.get("coverage_pct")
    if n_tot:
        cov_txt = f"（{cov:.0f}%）" if cov is not None else ""
        head = f"化合物名: {n_ann:,} / {n_tot:,} feature に付与{cov_txt}"
    else:
        head = f"化合物名: {n_ann:,} 件"
    children.append(dbc.Alert(head, color="success", className="mb-2 py-2"))

    if info.get("note"):
        children.append(html.P(info["note"], className="text-muted small mb-1"))
    if info.get("source_file"):
        children.append(html.P(
            f"参照元: {info['source_file']}",
            className="text-muted small mb-2",
            style={"wordBreak": "break-all"}))

    examples = info.get("examples") or []
    if examples:
        present = [k for k, _ in _PREVIEW_COLS if any(k in e for e in examples)]
        columns = [{"name": lbl, "id": k} for k, lbl in _PREVIEW_COLS if k in present]
        rows = []
        for e in examples:
            row = dict(e)
            mz = row.get("mz")
            if isinstance(mz, (int, float)):
                row["mz"] = f"{mz:.4f}"
            rows.append(row)
        children.append(html.Div(
            f"化合物名の例（先頭 {len(examples):,} 件）",
            className="small fw-bold mb-1"))
        children.append(dash_table.DataTable(
            columns=columns,
            data=rows,
            style_cell={"fontSize": "13px", "padding": "6px", "textAlign": "left"},
            style_header={"fontWeight": "bold", "backgroundColor": "#f1f3f5"},
            sort_action="native", page_action="none",
            style_table={"maxHeight": "420px", "overflowY": "auto"},
        ))
    return html.Div(children)


@callback(
    [Output("annotation_preview_modal", "is_open", allow_duplicate=True),
     Output("annotation_preview_body", "children")],
    Input({"type": "sub_action_annotations", "index": ALL}, "n_clicks"),
    State("selected_project", "data"),
    prevent_initial_call=True,
)
def open_annotation_preview(clicks, project):
    """「化合物名」ボタン → サブプロジェクトの注釈状況を読み、モーダルを開いて描画。"""
    if not ctx.triggered_id or not any(c for c in (clicks or []) if c):
        return no_update, no_update
    sub_id = ctx.triggered_id["index"]
    project_id = project.get("id", "") if project else ""
    sub = get_sub_project(project_id, sub_id)
    if not sub:
        return True, dbc.Alert("サブプロジェクトが見つかりません。", color="danger",
                               className="mb-0 py-2")
    try:
        info = inspect_annotations(sub)
    except Exception as e:  # noqa: BLE001 — UI にエラーを出して継続
        logger.exception("annotation inspect 失敗")
        return True, dbc.Alert(f"確認中にエラーが発生しました: {e}", color="danger",
                               className="mb-0 py-2")
    return True, _render(info)


@callback(
    Output("annotation_preview_modal", "is_open", allow_duplicate=True),
    Input("annotation_preview_close_btn", "n_clicks"),
    prevent_initial_call=True,
)
def close_annotation_preview(n_clicks):
    """「閉じる」→ モーダルを閉じる。"""
    if not n_clicks:
        return no_update
    return False
