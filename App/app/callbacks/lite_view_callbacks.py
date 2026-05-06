# =============================================================================
# MSI Analysis Application - Lite View Callbacks
# 軽量ビューア（/lite/<project_id>/<sub_project_id>）
# =============================================================================
# 解析結果フォルダから直接 URL で開ける読み取り専用ビュー。
# /share/<token> ページのデータ抽出ロジック（_shared_data キャッシュ）を再利用し、
# 異なる URL ルーティングと最小機能セット（UMAP / Volcano / Feature / 統計）に絞る。
# =============================================================================

import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import (
    Input, Output, State, callback, ctx, no_update,
    html, dcc, clientside_callback,
)

from app.services.project_manager import get_project, get_sub_project
from app.callbacks.interactive_callbacks import (
    _detect_integration_methods,
    _cluster_sort_key,
)
from app.callbacks.share_callbacks import (
    _shared_data, _sv_bridge,
    _load_deg_results, _is_meaningful_annotation,
)
from app.callbacks.interactive_umap import _build_umap_integrated_fig

_LITE_URL_RE = re.compile(r"^/lite/([^/]+)/([^/]+)/?$")


# =========================================================================
# URL ルーティング
# =========================================================================

@callback(
    [Output("current_page", "data", allow_duplicate=True),
     Output("lite_target_store", "data")],
    Input("url_bar", "pathname"),
    prevent_initial_call=True,
)
def route_lite_url(pathname):
    """URL パスが /lite/<project_id>/<sub_project_id> なら軽量ビューに遷移"""
    if not pathname:
        return no_update, no_update
    m = _LITE_URL_RE.match(pathname)
    if not m:
        return no_update, no_update
    return "lite", {"project_id": m.group(1), "sub_project_id": m.group(2)}


# =========================================================================
# 軽量ビュー初期化
# =========================================================================

@callback(
    [Output("lv_content", "style"),
     Output("lv_error", "style"),
     Output("lv_error", "children"),
     Output("lv_metadata", "children"),
     Output("lv_rds_path", "data"),
     Output("lv_integration_method", "data"),
     Output("lv_data_info", "children"),
     Output("lv_umap_highlight_cluster", "options"),
     Output("lv_feature_select", "options"),
     Output("lv_cluster_stats_table", "data"),
     Output("lv_cluster_stats_table", "columns"),
     Output("lv_deg_data_store", "data"),
     Output("lv_volcano_section", "style")],
    Input("lite_target_store", "data"),
    prevent_initial_call=True,
)
def initialize_lite_view(target):
    """target = {"project_id": ..., "sub_project_id": ...} を受け、データをロードして UI を初期化"""
    hide, show = {"display": "none"}, {}
    if not target or not target.get("project_id"):
        return (hide, hide, "", "", "", "", "", [], [], [], [], None, hide)

    project_id = target["project_id"]
    sub_id = target["sub_project_id"]
    project = get_project(project_id)
    sub = get_sub_project(project_id, sub_id) if project else None
    if not sub:
        return (hide, show,
                f"プロジェクトまたはサブプロジェクトが見つかりません: {project_id}/{sub_id}",
                "", "", "", "", [], [], [], [], None, hide)

    result_dir = sub.get("last_result_dir") or sub.get("output_dir", "")
    rds_map = _detect_integration_methods(result_dir) if result_dir else {}
    if not rds_map:
        return (hide, show, "解析結果が見つかりません（解析がまだ実行されていない可能性があります）。",
                "", "", "", "", [], [], [], [], None, hide)

    integration_method = "Harmony" if "Harmony" in rds_map else next(iter(rds_map))
    rds_path = rds_map[integration_method]

    # メタデータカード
    metadata = dbc.Row([
        dbc.Col([html.Strong("プロジェクト: "), html.Span(project.get("name", ""))],
                width="auto"),
        dbc.Col([html.Strong("サブプロジェクト: "), html.Span(sub.get("name", ""))],
                width="auto"),
        dbc.Col([html.Strong("統合手法: "), html.Span(integration_method)],
                width="auto"),
        dbc.Col([html.Strong("解析日時: "),
                 html.Span(sub.get("last_modified") or sub.get("created_at", "不明"))],
                width="auto"),
    ], className="g-3")

    # データロード（共有ページの _shared_data を流用、prefix で分離）
    cache_key = f"lite::{project_id}::{sub_id}::{integration_method}"
    if cache_key not in _shared_data:
        try:
            extracted = _sv_bridge.extract_data(rds_path)
            _shared_data[cache_key] = {
                "plot_data": extracted["plot_data"],
                "cluster_stats": extracted["cluster_stats"],
                "features_list": extracted["features_list"],
                "meta": extracted["meta"],
                "rds_path": rds_path,
                "cache_dir": str(extracted["cache_dir"]),
            }
        except Exception as e:
            return (hide, show, f"RDS読込エラー: {e}", metadata,
                    "", "", "", [], [], [], [], None, hide)

    data = _shared_data[cache_key]
    df_plot = data["plot_data"]
    df_stats = data["cluster_stats"]
    features = data["features_list"]

    cluster_options = []
    if df_plot is not None and "Cluster" in df_plot.columns:
        clusters = sorted(df_plot["Cluster"].unique(), key=_cluster_sort_key)
        cluster_options = [{"label": f"Cluster {c}", "value": str(c)}
                           for c in clusters]
    feature_options = ([{"label": f, "value": f} for f in features[:100]]
                       if features else [])
    stats_data = df_stats.to_dict("records") if df_stats is not None else []
    stats_columns = ([{"name": col, "id": col} for col in df_stats.columns]
                     if df_stats is not None else [])

    deg_records = []
    if result_dir and Path(result_dir).is_dir():
        deg_records = _load_deg_results(Path(result_dir), integration_method) or []
    volcano_style = show if deg_records else hide

    n_cells = len(df_plot) if df_plot is not None else 0
    n_features = len(features) if features else 0
    info = f"データ読込完了: {n_cells:,} cells, {n_features:,} features"

    return (show, hide, "", metadata,
            rds_path, integration_method, info,
            cluster_options, feature_options,
            stats_data, stats_columns,
            deg_records, volcano_style)


# =========================================================================
# UMAP（軽量、ホバーでクラスタ番号表示）
# =========================================================================

@callback(
    Output("lv_umap_plot", "figure"),
    [Input("lv_umap_color_by", "value"),
     Input("lv_umap_highlight_cluster", "value"),
     Input("lv_umap_show_legend", "value"),
     Input("lv_umap_show_labels", "value"),
     Input("lv_umap_marker_size", "value")],
    [State("lite_target_store", "data"),
     State("lv_integration_method", "data")],
)
def lv_update_umap(color_by, highlight_clusters, show_legend, show_labels,
                   marker_size, target, integration_method):
    empty = go.Figure()
    if not target or not target.get("project_id"):
        empty.add_annotation(text="データなし", showarrow=False,
                             xref="paper", yref="paper", x=0.5, y=0.5)
        return empty

    cache_key = f"lite::{target['project_id']}::{target['sub_project_id']}::{integration_method}"
    if cache_key not in _shared_data:
        return empty

    df = _shared_data[cache_key].get("plot_data")
    if df is None or df.empty:
        return empty

    return _build_umap_integrated_fig(
        df, color_by, highlight_clusters or [],
        show_legend, show_labels,
        marker_size=marker_size or 2,
    )


# =========================================================================
# Volcano Plot（軽量、ホバーで化合物名表示）
# =========================================================================

@callback(
    [Output("lv_volcano_plot", "figure"),
     Output("lv_volcano_cluster_select", "options")],
    [Input("lv_volcano_cluster_select", "value"),
     Input("lv_deg_data_store", "data")],
)
def lv_update_volcano_plot(cluster, deg_data):
    """軽量ビューア用 Volcano Plot（ホバーで化合物名）"""
    empty = go.Figure()
    if not deg_data:
        return empty, []

    df = pd.DataFrame(deg_data)
    if "p_val_adj_raw" in df.columns:
        df["p_num"] = pd.to_numeric(df["p_val_adj_raw"], errors="coerce")
    else:
        df["p_num"] = pd.to_numeric(df["p_val_adj"], errors="coerce")
    df["avg_log2FC"] = pd.to_numeric(df["avg_log2FC"], errors="coerce")
    min_p = (df.loc[df["p_num"] > 0, "p_num"].min()
             if (df["p_num"] > 0).any() else 5e-324)
    df["neg_log10_p"] = -np.log10(df["p_num"].clip(lower=min_p))

    if "annotation" in df.columns:
        df["display_text"] = df.apply(
            lambda r: f"{r['gene']}<br>({r['annotation']})"
            if _is_meaningful_annotation(r.get('annotation', ''), r.get('gene', ''))
            else r['gene'],
            axis=1,
        )
    else:
        df["display_text"] = df["gene"]

    clusters = sorted(df["cluster"].astype(str).unique(), key=_cluster_sort_key)
    cluster_opts = [{"label": f"Cluster {c}", "value": c} for c in clusters]

    if cluster:
        df = df[df["cluster"].astype(str) == str(cluster)]

    fc_thresh, p_thresh = 0.5, 1.3

    fig = go.Figure()
    for reg, color, label in [
        ("Up", "#FF2D2D", "Up-regulated"),
        ("Down", "#1E5BFF", "Down-regulated"),
        ("NS", "#7A7A7A", "Not significant"),
    ]:
        if reg == "Up":
            mask = (df["neg_log10_p"] >= p_thresh) & (df["avg_log2FC"] >= fc_thresh)
        elif reg == "Down":
            mask = (df["neg_log10_p"] >= p_thresh) & (df["avg_log2FC"] <= -fc_thresh)
        else:
            mask = ~((df["neg_log10_p"] >= p_thresh)
                     & (df["avg_log2FC"].abs() >= fc_thresh))
        sub = df[mask]
        if len(sub) > 0:
            fig.add_trace(go.Scatter(
                x=sub["avg_log2FC"], y=sub["neg_log10_p"],
                mode="markers",
                marker=dict(size=8, color=color, opacity=0.7),
                name=label,
                text=sub["display_text"],
                hovertemplate=("<b>%{text}</b><br>"
                               "log2FC: %{x:.3f}<br>"
                               "-log10(p): %{y:.2f}<extra></extra>"),
            ))

    fig.add_hline(y=p_thresh, line_dash="dash", line_color="gray", opacity=0.5)
    fig.add_vline(x=fc_thresh, line_dash="dash", line_color="gray", opacity=0.5)
    fig.add_vline(x=-fc_thresh, line_dash="dash", line_color="gray", opacity=0.5)

    title = f"Volcano Plot - Cluster {cluster}" if cluster else "Volcano Plot (全クラスタ)"
    fig.update_layout(
        title=dict(text=title, x=0.5),
        xaxis_title="avg_log2FC",
        yaxis_title="-log10(p_val_adj)",
        template="plotly_white",
        margin=dict(l=50, r=20, t=40, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=-0.18,
                    xanchor="center", x=0.5),
    )
    return fig, cluster_opts


# =========================================================================
# Feature Plot
# =========================================================================

@callback(
    Output("lv_feature_plot", "figure"),
    Input("lv_feature_select", "value"),
    [State("lite_target_store", "data"),
     State("lv_integration_method", "data")],
)
def lv_update_feature_plot(feature, target, integration_method):
    empty = go.Figure()
    if not feature or not target or not target.get("project_id"):
        empty.add_annotation(text="Feature を選択してください", showarrow=False,
                             xref="paper", yref="paper", x=0.5, y=0.5)
        return empty

    cache_key = f"lite::{target['project_id']}::{target['sub_project_id']}::{integration_method}"
    if cache_key not in _shared_data:
        return empty

    data = _shared_data[cache_key]
    rds_path = data.get("rds_path")
    cache_dir_str = data.get("cache_dir")
    df = data.get("plot_data")
    if not rds_path or df is None:
        return empty

    try:
        expression = None
        if cache_dir_str:
            expression = _sv_bridge.get_feature_expression_fast(
                Path(cache_dir_str), feature
            )
        if expression is None:
            expression = _sv_bridge.get_feature_expression(rds_path, feature)

        fig = go.Figure(go.Scatter(
            x=df["UMAP_1"], y=df["UMAP_2"],
            mode="markers",
            marker=dict(
                size=2, color=expression, colorscale="Plasma",
                colorbar=dict(title=feature), showscale=True,
            ),
            text=df["CellID"],
            hovertemplate=(f"{feature}: " + "%{marker.color:.4f}"
                           "<br>%{text}<extra></extra>"),
        ))
        fig.update_layout(
            xaxis_title="UMAP_1", yaxis_title="UMAP_2",
            template="plotly_white",
            margin=dict(l=40, r=10, t=30, b=40),
        )
        return fig

    except Exception as e:
        empty.add_annotation(text=f"エラー: {e}", showarrow=False,
                             xref="paper", yref="paper", x=0.5, y=0.5)
        return empty


# Feature 検索（サーバーサイドフィルタ）
@callback(
    Output("lv_feature_select", "options", allow_duplicate=True),
    Input("lv_feature_select", "search_value"),
    [State("lite_target_store", "data"),
     State("lv_integration_method", "data")],
    prevent_initial_call=True,
)
def lv_filter_features(search_value, target, integration_method):
    if not search_value or len(search_value) < 2 or not target:
        return no_update
    cache_key = f"lite::{target['project_id']}::{target['sub_project_id']}::{integration_method}"
    data = _shared_data.get(cache_key, {})
    features = data.get("features_list", [])
    if not features:
        return no_update
    q = search_value.lower()
    filtered = [f for f in features if q in f.lower()]
    return [{"label": f, "value": f} for f in filtered[:100]]


# =========================================================================
# 「軽量ビューアを開く」ボタン → 新タブで /lite/<project_id>/<sub_project_id> を開く
# =========================================================================

clientside_callback(
    """
    function(n_clicks, project_id, sub_project_id) {
        if (!n_clicks || !project_id || !sub_project_id) {
            return window.dash_clientside.no_update;
        }
        const url = `/lite/${encodeURIComponent(project_id)}/${encodeURIComponent(sub_project_id)}`;
        window.open(url, '_blank');
        return window.dash_clientside.no_update;
    }
    """,
    Output("btn_open_lite_viewer", "n_clicks"),
    Input("btn_open_lite_viewer", "n_clicks"),
    [State("interactive_project_select", "value"),
     State("interactive_sub_project_select", "value")],
    prevent_initial_call=True,
)
