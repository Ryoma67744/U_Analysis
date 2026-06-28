# =============================================================================
# MSI Analysis Application - Loupe-inspired interactive additions (Phase 1)
# Loupe Browser 9 を参考にした追加機能:
#   - 共有選択 Store への取り込み (lasso/box 選択)
#   - 選択範囲のライブ統計カード
#   - Feature の violin 分布パネル
#   - ソート可能なマーカー DataTable + Top-N CSV 出力
#
# 本モジュールは「新規コールバックの追加のみ」で既存コールバックは変更しない。
# interactive_callbacks.py から import されて @callback が登録される。
# 重い処理 (R 往復) は避け、可能な限り読み込み済み plot_data / parquet を使う。
# =============================================================================

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, no_update, html, dcc
from dash.exceptions import PreventUpdate

from app.utils.selection_utils import (
    extract_selected_cell_ids,
    compute_selection_summary,
    natural_cluster_key,
)
from app.utils.color_utils import get_cluster_color_map as _get_cluster_color_map

logger = logging.getLogger("msi.interactive.loupe")


def _active_items_list(active_items):
    if isinstance(active_items, list):
        return active_items
    return [active_items] if active_items else []


# ---------------------------------------------------------------------------
# P1.2: lasso/box 選択 → 共有 CellID Store
# 任意プロットの selectedData を単一の選択ソースに集約する (逆リンク・統計の土台)。
# ---------------------------------------------------------------------------
@callback(
    Output("selected_cell_ids_store", "data"),
    Input("interactive_umap_plot", "selectedData"),
    prevent_initial_call=True,
)
def capture_umap_selection(selected_data):
    return extract_selected_cell_ids(selected_data)


# ---------------------------------------------------------------------------
# P1.3: 選択範囲のライブ統計カード
# ---------------------------------------------------------------------------
def _composition_block(rows, label):
    if not rows:
        return None
    items = []
    for r in rows[:12]:
        items.append(html.Span([
            html.Span(f"{r['key']}", className="fw-bold me-1"),
            html.Span(f"{r['count']} ({r['pct']}%)", className="text-muted"),
        ], className="me-3 d-inline-block"))
    return html.Div([
        html.Div(label, className="small fw-bold text-secondary"),
        html.Div(items),
    ], className="mb-1")


def _render_summary_card(summary):
    n = summary["n_selected"]
    children = [
        html.Div([
            html.Span(f"選択 {n:,} px", className="badge bg-primary me-2"),
            html.Span(f"全体の {summary['pct']}%", className="text-muted small"),
        ], className="mb-1"),
    ]
    if summary.get("mean_intensity") is not None and summary.get("feature_name"):
        children.append(html.Div([
            html.Span(f"{summary['feature_name']} 平均強度: ",
                      className="small fw-bold"),
            html.Span(f"{summary['mean_intensity']:.4g}", className="small"),
        ], className="mb-1"))
    cl = _composition_block(summary["by_cluster"], "クラスタ構成")
    if cl:
        children.append(cl)
    sm = _composition_block(summary["by_sample"], "サンプル構成")
    if sm:
        children.append(sm)
    return html.Div(children)


@callback(
    Output("selection_summary_card", "children"),
    Input("selected_cell_ids_store", "data"),
    [State("seurat_rds_path_store", "data"),
     State("seurat_cache_dir_store", "data"),
     State("feature_select", "value")],
    prevent_initial_call=True,
)
def render_selection_summary(selected_ids, rds_path, cache_dir_str, feature_name):
    if not selected_ids:
        return html.Div(
            "UMAP 上で投げ縄/ボックス選択すると、選択範囲の統計をここに表示します。",
            className="text-muted small")
    from app.callbacks.interactive_callbacks import (
        _interactive_data, _set_active_key, _bridge)
    _set_active_key(rds_path)
    df = _interactive_data.get("plot_data")
    if df is None:
        return no_update
    # 表示中 feature の平均強度は parquet 高速路がある時のみ算出 (R 往復しない)
    expr = None
    if feature_name and cache_dir_str:
        try:
            expr = _bridge.get_feature_expression_fast(
                Path(cache_dir_str), feature_name)
        except Exception:
            expr = None
    summary = compute_selection_summary(
        df, selected_ids,
        expr=expr, feature_name=feature_name if expr is not None else None,
    )
    return _render_summary_card(summary)


# ---------------------------------------------------------------------------
# P1.5: Feature violin 分布パネル
# ---------------------------------------------------------------------------
@callback(
    Output("feature_violin_plot", "figure"),
    [Input("feature_select", "value"),
     Input("feature_violin_group_by", "value"),
     Input("interactive_accordion", "active_item")],
    [State("seurat_rds_path_store", "data"),
     State("seurat_cache_dir_store", "data"),
     State("custom_color_map_store", "data")],
    prevent_initial_call=True,
)
def update_feature_violin(feature_name, group_by, active_items,
                          rds_path, cache_dir_str, custom_colors):
    if "acc_feature" not in _active_items_list(active_items):
        return no_update
    if not feature_name or not rds_path:
        return go.Figure()
    from app.callbacks.interactive_callbacks import (
        _interactive_data, _set_active_key, _bridge)
    _set_active_key(rds_path)
    df = _interactive_data.get("plot_data")
    if df is None:
        return go.Figure()
    expr = None
    try:
        if cache_dir_str:
            expr = _bridge.get_feature_expression_fast(
                Path(cache_dir_str), feature_name)
        if expr is None:
            # parquet 未生成時は一度だけ生成 (Feature Plot と同じ前提)
            _bridge.ensure_expression_matrix(rds_path)
            if cache_dir_str:
                expr = _bridge.get_feature_expression_fast(
                    Path(cache_dir_str), feature_name)
        if expr is None:
            expr = _bridge.get_feature_expression(rds_path, feature_name)
    except Exception as e:
        return go.Figure(layout={"annotations": [
            {"text": f"発現取得エラー: {e}", "showarrow": False,
             "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.5}]})
    if expr is None:
        return go.Figure()

    arr = np.asarray(expr, dtype=float)
    dfp = df.copy()
    if len(arr) != len(dfp):
        return go.Figure()
    dfp["_expr"] = arr
    gcol = group_by if group_by in dfp.columns else "Cluster"
    cats = sorted(dfp[gcol].astype(str).unique(), key=natural_cluster_key)
    color_map = _get_cluster_color_map(cats, custom_colors) if gcol == "Cluster" else {}

    fig = go.Figure()
    for c in cats:
        vals = dfp.loc[dfp[gcol].astype(str) == c, "_expr"].values
        kwargs = dict(y=vals, name=str(c), box_visible=True,
                      meanline_visible=True, points=False, opacity=0.75)
        col = color_map.get(str(c)) if color_map else None
        if col:
            kwargs["fillcolor"] = col
            kwargs["line"] = dict(color=col)
        fig.add_trace(go.Violin(**kwargs))
    fig.update_layout(
        showlegend=False, margin=dict(l=55, r=10, t=32, b=45),
        title=dict(text=f"{feature_name} 分布 ({gcol})", x=0.5,
                   font=dict(size=13)),
        yaxis_title="強度 (data layer)", xaxis_title=gcol,
        plot_bgcolor="white",
    )
    return fig


# ---------------------------------------------------------------------------
# P1.6: ソート可能なマーカー DataTable + Top-N CSV 出力
# ---------------------------------------------------------------------------
_MARKER_TABLE_COLUMNS = [
    {"name": "feature", "id": "gene"},
    {"name": "cluster", "id": "cluster"},
    {"name": "avg_log2FC", "id": "avg_log2FC", "type": "numeric"},
    {"name": "p_val_adj", "id": "p_val_adj_raw", "type": "numeric"},
    {"name": "pct.1", "id": "pct.1", "type": "numeric"},
    {"name": "pct.2", "id": "pct.2", "type": "numeric"},
    {"name": "annotation", "id": "annotation"},
]


@callback(
    [Output("deg_markers_table", "data"),
     Output("deg_markers_table", "columns"),
     Output("deg_markers_cluster_filter", "options")],
    [Input("deg_data_store", "data"),
     Input("deg_markers_cluster_filter", "value")],
    prevent_initial_call=True,
)
def populate_marker_table(deg_data, cluster_filter):
    if not deg_data:
        return [], _MARKER_TABLE_COLUMNS, []
    clusters = sorted(
        {str(r.get("cluster")) for r in deg_data if r.get("cluster") is not None},
        key=natural_cluster_key)
    options = [{"label": c, "value": c} for c in clusters]
    recs = deg_data
    if cluster_filter:
        recs = [r for r in recs if str(r.get("cluster")) == str(cluster_filter)]
    return recs, _MARKER_TABLE_COLUMNS, options


@callback(
    Output("dl_marker_table_csv", "data"),
    Input("btn_export_marker_table", "n_clicks"),
    [State("deg_markers_table", "derived_virtual_data"),
     State("marker_table_top_n", "value")],
    prevent_initial_call=True,
)
def export_marker_table(n_clicks, virtual_data, top_n):
    """現在の並び替え/絞り込み (derived_virtual_data) を反映して Top-N を CSV 出力。"""
    if not n_clicks or not virtual_data:
        raise PreventUpdate
    recs = virtual_data
    if top_n and int(top_n) > 0:
        recs = recs[:int(top_n)]
    df = pd.DataFrame(recs)
    return dcc.send_data_frame(df.to_csv, "markers_topN.csv", index=False)
