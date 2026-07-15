# =============================================================================
# MSI Analysis Application - In-app on-the-fly Differential Expression (Phase 2)
# Loupe Browser 風の「選択 → その場で DE 検定」。
#   - Globally Distinguishing: 現在の選択 vs 残り全体
#   - Locally Distinguishing : 現在の選択 vs 指定クラスタ(群)
# 結果は専用のソート可能 DataTable + Volcano + Top-N CSV に表示（既存 DEG は不変）。
#
# DE は R (Seurat::FindMarkers wilcox + BH) を SeuratBridge 経由で実行。
# 前景コールバック（~30-60s, presto 使用時）。in-memory plot_data に依存するため
# 別プロセス background callback ではなく前景で実行する。
# =============================================================================

import logging
import math
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, no_update, html, dcc
from dash.exceptions import PreventUpdate

from app.utils.deg_utils import standardize_deg_df as _standardize_deg_df
from app.utils.deg_utils import backfill_annotations
from app.utils.annotation_label import feature_display_label
from app.utils.selection_utils import natural_cluster_key, cells_in_clusters
from app.callbacks.interactive_loupe import _MARKER_TABLE_COLUMNS

logger = logging.getLogger("msi.interactive.de")


def _err(msg):
    return html.Span(msg, className="text-danger small")


def _build_de_volcano(records, fc_th=0.5, p_th=1.3):
    fig = go.Figure()
    if records:
        xs, ys, colors, texts = [], [], [], []
        for r in records:
            fc = r.get("avg_log2FC")
            p = r.get("p_val_adj_raw")
            if fc is None or p is None:
                continue
            try:
                fc = float(fc)
                p = float(p)
            except (TypeError, ValueError):
                continue
            y = -math.log10(p) if p > 0 else 300.0
            xs.append(fc)
            ys.append(y)
            texts.append(feature_display_label(
                r.get("gene", ""), deg_annotation=r.get("annotation"), style="paren"))
            if y >= p_th and abs(fc) >= fc_th:
                colors.append("#d6334c" if fc > 0 else "#2c6fbb")
            else:
                colors.append("#c8c8c8")
        fig.add_trace(go.Scattergl(
            x=xs, y=ys, mode="markers",
            marker=dict(size=6, color=colors), text=texts,
            hovertemplate="%{text}<br>log2FC=%{x:.2f}"
                          "<br>-log10(p_adj)=%{y:.2f}<extra></extra>",
        ))
        fig.add_vline(x=fc_th, line=dict(dash="dash", color="gray", width=1))
        fig.add_vline(x=-fc_th, line=dict(dash="dash", color="gray", width=1))
        fig.add_hline(y=p_th, line=dict(dash="dash", color="gray", width=1))
    fig.update_layout(
        margin=dict(l=55, r=10, t=32, b=42),
        title=dict(text="選択 DE Volcano", x=0.5, font=dict(size=13)),
        xaxis_title="avg_log2FC", yaxis_title="-log10(p_val_adj)",
        plot_bgcolor="white", showlegend=False,
    )
    return fig


# ---------------------------------------------------------------------------
# 比較対象クラスタ (Locally の ident.2) の選択肢を plot_data から構築
# ---------------------------------------------------------------------------
@callback(
    Output("onthefly_de_target", "options"),
    Input("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def fill_onthefly_target_options(rds_path):
    if not rds_path:
        return []
    from app.callbacks.interactive_callbacks import _interactive_data, _set_active_key
    _set_active_key(rds_path)
    df = _interactive_data.get("plot_data")
    if df is None or "Cluster" not in df.columns:
        return []
    clusters = sorted(df["Cluster"].astype(str).unique(), key=natural_cluster_key)
    return [{"label": c, "value": c} for c in clusters]


# ---------------------------------------------------------------------------
# DE 実行（前景）。選択範囲 → FindMarkers → 標準化レコードを store へ
# ---------------------------------------------------------------------------
@callback(
    [Output("onthefly_de_store", "data"),
     Output("onthefly_de_status", "children")],
    Input("btn_run_onthefly_de", "n_clicks"),
    [State("selected_cell_ids_store", "data"),
     State("onthefly_de_mode", "value"),
     State("onthefly_de_target", "value"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def run_onthefly_de(n_clicks, selected_ids, mode, target_clusters, rds_path):
    if not n_clicks:
        raise PreventUpdate
    if not rds_path:
        return no_update, _err("データが読み込まれていません")
    if not selected_ids:
        return no_update, _err("先に UMAP で投げ縄/ボックス選択してください")

    from app.callbacks.interactive_callbacks import (
        _interactive_data, _set_active_key, _bridge)
    _set_active_key(rds_path)

    ident2 = None
    if mode == "local":
        df = _interactive_data.get("plot_data")
        if df is None:
            return no_update, _err("データが読み込まれていません")
        if not target_clusters:
            return no_update, _err("Locally では比較対象クラスタを選択してください")
        ident2 = cells_in_clusters(df, target_clusters)
        if len(ident2) < 3:
            return no_update, _err("比較対象クラスタの細胞が少なすぎます")

    try:
        result_df = _bridge.run_differential_expression(
            rds_path, selected_ids, ident2_ids=ident2, mode=mode, timeout=600)
    except Exception as e:
        logger.warning("on-the-fly DE failed: %s", e)
        return no_update, _err(f"DE 失敗: {e}")

    records = _standardize_deg_df(result_df) or []
    # 空 annotation を annotation_map から補完（表・Volcano で化合物名を表示）
    try:
        backfill_annotations(records, _interactive_data.get("annotation_map"))
    except Exception:
        pass
    n_sig = 0
    for r in records:
        v = r.get("p_val_adj_raw")
        try:
            if v is not None and float(v) < 0.05:
                n_sig += 1
        except (TypeError, ValueError):
            pass
    label = "選択 vs 全体" if mode == "global" else "選択 vs 指定群"
    msg = html.Span(
        f"完了: {label} — {len(records)} features（有意 p_adj<0.05: {n_sig}）",
        className="text-success small")
    return records, msg


# ---------------------------------------------------------------------------
# 結果テーブル + Volcano の描画
# ---------------------------------------------------------------------------
@callback(
    [Output("onthefly_de_table", "data"),
     Output("onthefly_de_table", "columns"),
     Output("onthefly_de_volcano", "figure")],
    [Input("onthefly_de_store", "data"),
     Input("onthefly_de_fc", "value"),
     Input("onthefly_de_p", "value")],
    prevent_initial_call=True,
)
def render_onthefly_de(records, fc_th, p_th):
    records = records or []
    fc = fc_th if fc_th is not None else 0.5
    p = p_th if p_th is not None else 1.3
    fig = _build_de_volcano(records, fc_th=fc, p_th=p)
    return records, _MARKER_TABLE_COLUMNS, fig


# ---------------------------------------------------------------------------
# Top-N CSV 出力（現在の並び替え/絞り込みを反映）
# ---------------------------------------------------------------------------
@callback(
    Output("dl_onthefly_de_csv", "data"),
    Input("btn_export_onthefly_de", "n_clicks"),
    [State("onthefly_de_table", "derived_virtual_data"),
     State("onthefly_de_top_n", "value")],
    prevent_initial_call=True,
)
def export_onthefly_de(n_clicks, virtual_data, top_n):
    if not n_clicks or not virtual_data:
        raise PreventUpdate
    recs = virtual_data
    if top_n and int(top_n) > 0:
        recs = recs[:int(top_n)]
    df = pd.DataFrame(recs)
    return dcc.send_data_frame(df.to_csv, "onthefly_DE.csv", index=False)
