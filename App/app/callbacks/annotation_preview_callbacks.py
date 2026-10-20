# =============================================================================
# MSI Analysis Application - Annotation Preview Callbacks
# サブプロジェクトカードの「化合物名」ボタン → 生データを開かずに注釈状況をモーダル表示。
# =============================================================================

import logging
from uuid import uuid4

from dash import (
    Input, Output, State, callback, clientside_callback, ctx, no_update,
    html, dcc, dash_table, ALL,
)
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc

from app.services.project_manager import get_sub_project
from app.services.annotation_inspect import inspect_annotations
from app.services.annotation_export import export_compound_names, AnnotationExportError

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
     Output("annotation_preview_target", "data")],
    Input({"type": "sub_action_annotations", "index": ALL}, "n_clicks"),
    State("selected_project", "data"),
    prevent_initial_call=True,
)
def open_annotation_preview(clicks, project):
    """「化合物名」ボタン → まずモーダルを即座に開く（重い判定は populate へ委譲）。

    注釈判定はファイル読取を伴い、サブプロジェクトによっては時間がかかる。ここで
    それを実行すると「押しても開かない（＝実際は待ち時間）」に見えるため、クリック時は
    モーダルを開いて対象を Store に積むだけにし、本文の描画は populate 側で行う。
    表示は現在の target と読取結果の target が一致した場合だけ更新する。
    """
    if not ctx.triggered_id or not any(c for c in (clicks or []) if c):
        return no_update, no_update
    sub_id = ctx.triggered_id["index"]
    project_id = project.get("id", "") if project else ""
    # 同じ対象を閉じて再度開いた場合にも、前回の読取・出力結果と区別する。
    return True, {"project_id": project_id, "sub_id": sub_id, "nonce": uuid4().hex}


@callback(
    Output("annotation_preview_result", "data"),
    Input("annotation_preview_target", "data"),
    prevent_initial_call=True,
)
def populate_annotation_preview(target):
    """読取結果に対象を添付し、遅れて到着した別対象の結果を表示させない。"""
    if not target:
        return no_update
    result = {"target": target, "source": None, "n_annotated": 0, "n_unique": 0, "note": ""}
    try:
        sub = get_sub_project(target.get("project_id", ""), target.get("sub_id"))
        if not sub:
            result["body"] = dbc.Alert(
                "サブプロジェクトが見つかりません。", color="danger", className="mb-0 py-2")
            result["note"] = "対象を選び直してください。"
            return result
        info = inspect_annotations(sub)
        result.update(
            body=_render(info), source=info.get("export_source"),
            n_annotated=int(info.get("n_annotated") or 0),
            n_unique=int(info.get("n_unique") or 0),
        )
        if not result["source"]:
            if info.get("source_kind") == "desi_header":
                result["note"] = "この形式の化合物名リスト出力には、まだ対応していません。"
            else:
                result["note"] = info.get("note") or "出力できる化合物名がありません。"
        return result
    except Exception as e:  # noqa: BLE001 — UI にエラーを出して継続（描画失敗も捕捉）
        logger.exception("annotation inspect/render 失敗")
        result["body"] = dbc.Alert(
            f"確認中にエラーが発生しました: {e}", color="danger", className="mb-0 py-2")
        result["note"] = "読み込みに失敗しました。画面を閉じて再度開いてください。"
        return result


# ネットワークの応答順に依存せず、現在の対象に一致する結果だけを反映する。
# 全件の名称は Store に置かない（body 内も既存の先頭200件プレビューのみ）。
clientside_callback(
    """
    function(result, target) {
        if (!target || !result || !result.target ||
            !['project_id', 'sub_id', 'nonce'].every(k => target[k] === result.target[k])) {
            return window.dash_clientside.no_update;
        }
        return result;
    }
    """,
    Output("annotation_preview_ready", "data"),
    Input("annotation_preview_result", "data"),
    State("annotation_preview_target", "data"),
    prevent_initial_call=True,
)


clientside_callback(
    """
    function(target, ready, isOpen) {
        if (!target || !isOpen) return '';
        if (!ready || !ready.target ||
            !['project_id', 'sub_id', 'nonce'].every(k => target[k] === ready.target[k])) {
            return '読み込み中…';
        }
        return ready.body;
    }
    """,
    Output("annotation_preview_body", "children"),
    Input("annotation_preview_target", "data"),
    Input("annotation_preview_ready", "data"),
    Input("annotation_preview_modal", "is_open"),
)


# 出力処理の完了待ちが、新しい対象のプレビュー描画を止めないように分離する。
clientside_callback(
    """
    function(target, ready, unique, isOpen, busy) {
        if (!isOpen || !target || !ready || !ready.target ||
            !['project_id', 'sub_id', 'nonce'].every(k => target[k] === ready.target[k])) {
            return ['', true];
        }
        const count = (unique || []).includes('unique') ? ready.n_unique : ready.n_annotated;
        const available = !!ready.source && count > 0;
        const summary = available
            ? '化合物名あり：' + Number(ready.n_annotated).toLocaleString('ja-JP') +
              ' feature ／ 出力予定：' + Number(count).toLocaleString('ja-JP') + '件'
            : '';
        return [summary, !available || !!busy];
    }
    """,
    Output("annotation_export_count", "children"),
    Output("annotation_export_btn", "disabled"),
    Input("annotation_preview_target", "data"),
    Input("annotation_preview_ready", "data"),
    Input("annotation_export_unique", "value"),
    Input("annotation_preview_modal", "is_open"),
    Input("annotation_export_busy", "data"),
)


clientside_callback(
    """
    function(target, ready, exported, isOpen, busy) {
        const matches = value => target && value && value.target &&
            ['project_id', 'sub_id', 'nonce'].every(k => target[k] === value.target[k]);
        if (!isOpen || !matches(ready)) return '';
        if (busy) return 'ファイルを準備中…';
        return matches(exported) ? exported.status : (ready.note || '');
    }
    """,
    Output("annotation_export_status", "children"),
    Input("annotation_preview_target", "data"),
    Input("annotation_preview_ready", "data"),
    Input("annotation_export_result", "data"),
    Input("annotation_preview_modal", "is_open"),
    Input("annotation_export_busy", "data"),
)


def _same_target(target, ready):
    return (
        isinstance(target, dict) and isinstance(ready, dict)
        and isinstance(ready.get("target"), dict)
        and all(target.get(key) is not None and target.get(key) == ready["target"].get(key)
                for key in ("project_id", "sub_id", "nonce"))
    )


@callback(
    Output("annotation_export_result", "data"),
    Input("annotation_export_btn", "n_clicks"),
    State("annotation_preview_target", "data"),
    State("annotation_preview_ready", "data"),
    State("annotation_export_format", "value"),
    State("annotation_export_unique", "value"),
    prevent_initial_call=True,
    running=[(Output("annotation_export_busy", "data"), True, False)],
)
def download_annotation_names(n_clicks, target, ready, fmt, unique):
    """クリック時の対象を固定し、表示と同じ参照元の全名称を返す。"""
    if not n_clicks:
        raise PreventUpdate
    result = {"target": target, "status": "", "download": None}
    if not _same_target(target, ready) or not ready.get("source"):
        result["status"] = "化合物名の読み込み完了を待ち、必要なら画面を閉じて再度開いてください。"
        return result
    try:
        sub = get_sub_project(target["project_id"], target["sub_id"])
        if not sub:
            raise AnnotationExportError("サブプロジェクトが見つかりません。対象を選び直してください。")
        if not isinstance(unique, list) or any(value != "unique" for value in unique):
            raise AnnotationExportError("重複の設定を選び直してください。")
        exported = export_compound_names(
            sub, ready["source"], fmt=fmt, deduplicate="unique" in unique,
        )
        result["download"] = dcc.send_bytes(
            exported.content, exported.filename, type=exported.mime_type,
        )
        result["status"] = f"{exported.count:,}件の化合物名を出力しました。"
    except AnnotationExportError as exc:
        result["status"] = str(exc)
    except Exception:
        logger.exception("annotation export 失敗")
        result["status"] = "出力に失敗しました。画面を閉じて再度開いてください。"
    return result


clientside_callback(
    """
    function(result, target, isOpen) {
        if (!isOpen || !result || !result.download || !target || !result.target ||
            !['project_id', 'sub_id', 'nonce'].every(k => target[k] === result.target[k])) {
            return window.dash_clientside.no_update;
        }
        return result.download;
    }
    """,
    Output("annotation_export_download", "data"),
    Input("annotation_export_result", "data"),
    State("annotation_preview_target", "data"),
    State("annotation_preview_modal", "is_open"),
    prevent_initial_call=True,
)


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
