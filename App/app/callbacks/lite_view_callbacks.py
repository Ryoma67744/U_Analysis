# =============================================================================
# MSI Analysis Application - Lite View Callbacks (Report Style)
#
# /lite/<project_id>/<sub_project_id> の「レポート型」サマリビュー。
# PPT 出力と同等の構造をブラウザ上で即時表示する。
#
# 大きく 3 系統の callback:
#   1. URL ルーティング (route_lite_url + navigate_to_lite_page)
#       url_bar.pathname を lite_target_store に変換し、その後ページ遷移
#       2 段に分けているのは share_callbacks.route_share_url とのハッシュ衝突回避のため
#   2. レポート初期化 (initialize_lite_view)
#       lite_target_store → 全レポート HTML を一気に組み立てて lv_report_body に投入
#   3. Volcano 開閉 (toggle_volcano_section)
#       pattern-matching で各カードの Volcano セクションを折りたたむ
# =============================================================================

import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import (
    Input, Output, State, callback, html, dcc, dash_table, no_update, MATCH,
)

from app.services.project_manager import get_project, get_sub_project
from app.callbacks.interactive_callbacks import (
    _detect_integration_methods,
    _cluster_sort_key,
    _get_cluster_color_map,
    _load_deg_results,
)
from app.callbacks.share_callbacks import _shared_data, _sv_bridge
from app.callbacks.interactive_umap import _build_umap_integrated_fig
from app.callbacks.interactive_spatial import _create_single_spatial_fig
from app.utils.deg_utils import is_meaningful_annotation

_LITE_URL_RE = re.compile(r"^/lite/([^/]+)/([^/]+)/?$")


# =============================================================================
# URL ルーティング
# =============================================================================

@callback(
    Output("lite_target_store", "data"),
    Input("url_bar", "pathname"),
    prevent_initial_call=True,
)
def route_lite_url(pathname):
    """URL パスが /lite/<project_id>/<sub_project_id> なら lite_target_store に書く"""
    if not pathname:
        return no_update
    m = _LITE_URL_RE.match(pathname)
    if not m:
        return no_update
    return {"project_id": m.group(1), "sub_project_id": m.group(2)}


@callback(
    Output("current_page", "data", allow_duplicate=True),
    Input("lite_target_store", "data"),
    prevent_initial_call=True,
)
def navigate_to_lite_page(target):
    """lite_target_store の更新をトリガに lite ページへ遷移。

    route_lite_url から current_page.data 出力を分離することで、
    share_callbacks.route_share_url（同じ Input=url_bar.pathname,
    Output=current_page.data allow_duplicate）と Dash の
    allow_duplicate ハッシュが衝突しないようにする。
    """
    if target and target.get("project_id") and target.get("sub_project_id"):
        return "lite"
    return no_update


# =============================================================================
# レポート初期化（メイン callback）
# =============================================================================

@callback(
    [Output("lv_report_body", "children"),
     Output("lv_error", "is_open"),
     Output("lv_error", "children")],
    Input("lite_target_store", "data"),
    prevent_initial_call=True,
)
def initialize_lite_view(target):
    """lite_target_store の更新で全レポートを構築する。"""
    if not target or not target.get("project_id"):
        return no_update, False, ""

    project_id = target["project_id"]
    sub_id = target["sub_project_id"]
    project = get_project(project_id)
    sub = get_sub_project(project_id, sub_id) if project else None
    if not sub:
        return (
            html.Div(),
            True,
            f"プロジェクトまたはサブプロジェクトが見つかりません: "
            f"{project_id}/{sub_id}",
        )

    result_dir = sub.get("last_result_dir") or sub.get("output_dir", "")
    rds_map = _detect_integration_methods(result_dir) if result_dir else {}
    if not rds_map:
        return (
            html.Div(),
            True,
            "解析結果が見つかりません（解析がまだ実行されていない可能性があります）。",
        )

    integration_method = "Harmony" if "Harmony" in rds_map else next(iter(rds_map))
    rds_path = rds_map[integration_method]

    # データロード（lite 専用 cache key で _shared_data を流用）
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
            return html.Div(), True, f"RDS 読込エラー: {e}"

    data = _shared_data[cache_key]
    df_plot = data["plot_data"]
    df_stats = data["cluster_stats"]

    deg_records = []
    if result_dir and Path(result_dir).is_dir():
        deg_records = _load_deg_results(Path(result_dir), integration_method) or []

    # レポート組み立て
    body = _build_report_body(
        project=project,
        sub=sub,
        integration_method=integration_method,
        df_plot=df_plot,
        df_stats=df_stats,
        deg_records=deg_records,
    )
    return body, False, ""


# =============================================================================
# Volcano 折りたたみ
# =============================================================================

@callback(
    Output({"type": "lv_volcano_collapse", "cluster": MATCH}, "is_open"),
    Input({"type": "lv_volcano_toggle", "cluster": MATCH}, "n_clicks"),
    State({"type": "lv_volcano_collapse", "cluster": MATCH}, "is_open"),
    prevent_initial_call=True,
)
def toggle_volcano_section(n_clicks, is_open):
    if n_clicks:
        return not is_open
    return is_open


# =============================================================================
# レポート構築ヘルパー（Phase 2 で /share/ に流用できる純関数として分離）
# =============================================================================

def _build_report_body(project, sub, integration_method, df_plot,
                       df_stats, deg_records):
    """全体レポートを html.Div の children リストとして返す。"""
    color_map = _get_cluster_color_map(df_plot["Cluster"], None)
    return [
        _build_header(project, sub, integration_method, df_plot, df_stats),
        _build_overview_section(df_plot, df_stats, color_map),
        _build_per_cluster_cards(df_plot, deg_records, color_map),
        _build_heatmap_section(deg_records),
    ]


def _build_header(project, sub, integration_method, df_plot, df_stats):
    """ヘッダー: プロジェクト名 / 統計サマリ / サンプル名リスト"""
    samples = (
        sorted(df_plot["Sample"].unique())
        if "Sample" in df_plot.columns
        else []
    )
    n_clusters = (
        int(df_stats["Cluster"].nunique())
        if df_stats is not None and "Cluster" in df_stats.columns
        else 0
    )
    n_cells = len(df_plot) if df_plot is not None else 0

    meta_items = [
        ("統合手法", integration_method or "—"),
        ("サンプル数", str(len(samples))),
        ("クラスタ数", str(n_clusters)),
        ("総セル数", f"{n_cells:,}"),
        ("解析日時",
         sub.get("last_modified") or sub.get("created_at", "不明")),
    ]
    meta_row = html.Div(
        style={"display": "flex", "gap": "20px", "flexWrap": "wrap"},
        children=[
            html.Span(
                [html.Strong(f"{label}: ", className="text-secondary"),
                 html.Span(value)]
            ) for label, value in meta_items
        ],
    )

    samples_line = (
        html.Small(
            [html.Strong("サンプル: "), ", ".join(samples)],
            className="text-muted d-block mt-2",
        )
        if samples else None
    )

    return html.Div(
        className="report-header py-3 px-4 mb-4",
        style={
            "background": "#f8f9fa",
            "borderLeft": "4px solid #0d6efd",
            "borderRadius": "4px",
        },
        children=[
            html.H3(
                [project.get("name", ""), " / ", sub.get("name", "")],
                className="mb-2",
            ),
            meta_row,
            samples_line,
        ],
    )


def _build_overview_section(df_plot, df_stats, color_map):
    """Overview: 統合 UMAP / Per-sample Spatial / Stats Table / Ratio Pie"""
    # Integrated UMAP
    umap_fig = _build_umap_integrated_fig(
        df_plot, color_by="Cluster", highlight_clusters=None,
        show_legend=True, show_labels=True, marker_size=2,
        custom_colors=color_map,
    )
    umap_fig.update_layout(
        height=400, margin=dict(l=10, r=10, t=10, b=10),
    )

    spatial_grid = _build_per_sample_spatial(
        df_plot, color_map, highlight_clusters=None, panel_height=300,
    )

    # Cluster Stats Table
    stats_table = _build_cluster_stats_table(df_stats)

    # Ratio Pie
    pie_fig = _build_cluster_ratio_pie(df_plot, color_map)

    return html.Div(
        className="overview-section mb-5",
        children=[
            html.H4("Overview", className="mb-3 border-bottom pb-2"),
            dbc.Row([
                dbc.Col([
                    html.H6("Integrated UMAP", className="text-muted small"),
                    dcc.Graph(figure=umap_fig,
                              config={"displayModeBar": True}),
                ], lg=6, md=12, className="mb-3"),
                dbc.Col([
                    html.H6("Per-sample Spatial Mapping",
                            className="text-muted small"),
                    spatial_grid,
                ], lg=6, md=12, className="mb-3"),
            ]),
            dbc.Row([
                dbc.Col([
                    html.H6("Cluster Statistics", className="text-muted small"),
                    stats_table,
                ], lg=6, md=12, className="mb-3"),
                dbc.Col([
                    html.H6("Cluster Ratio", className="text-muted small"),
                    dcc.Graph(figure=pie_fig,
                              config={"displayModeBar": False}),
                ], lg=6, md=12, className="mb-3"),
            ]),
        ],
    )


def _build_per_sample_spatial(df_plot, color_map, highlight_clusters,
                              panel_height=250):
    """各サンプル 1 パネルの Spatial グリッド（横並び・改行可）"""
    if "Sample" not in df_plot.columns:
        return html.Div("Spatial データなし",
                        className="text-muted small")
    samples = sorted(df_plot["Sample"].unique())
    if not samples:
        return html.Div("サンプルなし", className="text-muted small")

    cols = []
    for s in samples:
        df_sample = df_plot[df_plot["Sample"] == s]
        fig = _create_single_spatial_fig(
            df_sample, color_map, highlight_clusters,
            selected_cell_ids=None,
            title=s, marker_size=2, embed_legend=False,
        )
        fig.update_layout(
            height=panel_height,
            showlegend=False,
            margin=dict(l=10, r=10, t=30, b=10),
        )
        cols.append(
            dbc.Col(
                dcc.Graph(figure=fig,
                          config={"displayModeBar": False}),
                lg=6, md=12, className="mb-2",
            )
        )
    return dbc.Row(cols, className="g-2")


def _build_cluster_stats_table(df_stats):
    """クラスタ統計テーブル"""
    if df_stats is None or df_stats.empty:
        return html.Div("統計データなし", className="text-muted small")
    return dash_table.DataTable(
        columns=[{"name": c, "id": c} for c in df_stats.columns],
        data=df_stats.to_dict("records"),
        style_cell={"fontSize": "13px", "padding": "6px",
                    "textAlign": "left"},
        style_header={"fontWeight": "bold", "backgroundColor": "#f1f3f5"},
        sort_action="native",
        page_action="none",
        style_table={"maxHeight": "400px", "overflowY": "auto"},
    )


def _build_cluster_ratio_pie(df_plot, color_map):
    """クラスタ構成比の Donut Pie"""
    if "Cluster" not in df_plot.columns:
        return go.Figure()
    counts = (
        df_plot["Cluster"].astype(str)
        .value_counts()
    )
    labels = sorted(counts.index, key=_cluster_sort_key)
    values = [int(counts[c]) for c in labels]
    colors = [color_map.get(c, "#888") for c in labels]

    fig = go.Figure(data=[
        go.Pie(
            labels=[f"Cluster {c}" for c in labels],
            values=values,
            marker=dict(colors=colors),
            hole=0.3,
            sort=False,
        )
    ])
    fig.update_layout(
        height=400,
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=True,
        legend=dict(orientation="v", x=1.02, y=0.5),
    )
    return fig


def _build_per_cluster_cards(df_plot, deg_records, color_map):
    """クラスタごとに 1 カード"""
    if "Cluster" not in df_plot.columns:
        return html.Div()

    clusters = sorted(df_plot["Cluster"].astype(str).unique(),
                      key=_cluster_sort_key)
    n_total = len(df_plot)

    deg_by_cluster = {}
    for r in deg_records:
        c = str(r.get("cluster", ""))
        deg_by_cluster.setdefault(c, []).append(r)

    cards = []
    for c in clusters:
        df_c = df_plot[df_plot["Cluster"].astype(str) == c]
        n_c = len(df_c)
        pct = (n_c / n_total * 100) if n_total > 0 else 0.0
        color = color_map.get(c, "#888888")
        cards.append(
            _build_one_cluster_card(
                cluster_id=c,
                color=color,
                n_cells=n_c,
                pct=pct,
                df_plot=df_plot,
                color_map=color_map,
                deg_records=deg_by_cluster.get(c, []),
            )
        )

    return html.Div(
        className="per-cluster-section mb-5",
        children=[
            html.H4("Per-cluster Summary",
                    className="mb-3 border-bottom pb-2"),
            *cards,
        ],
    )


def _build_one_cluster_card(cluster_id, color, n_cells, pct,
                            df_plot, color_map, deg_records):
    """1 クラスタぶんのカード"""
    # ハイライト UMAP
    hl_umap = _build_umap_integrated_fig(
        df_plot, color_by="Cluster",
        highlight_clusters=[cluster_id],
        show_legend=False, show_labels=False, marker_size=2,
        custom_colors=color_map, bg_opacity=0.08,
    )
    hl_umap.update_layout(
        height=350, margin=dict(l=10, r=10, t=10, b=10),
    )

    # ハイライト Spatial グリッド
    hl_spatial = _build_per_sample_spatial(
        df_plot, color_map, highlight_clusters=[cluster_id],
        panel_height=220,
    )

    # Top 5 Up-regulated markers テーブル
    top_markers_view = _build_top_markers_table(deg_records, top_n=5)

    # Volcano 折りたたみ
    if deg_records:
        volcano_fig = _build_volcano_fig(deg_records, cluster_id)
        volcano_toggle = dbc.Button(
            "▼ Volcano Plot を表示",
            id={"type": "lv_volcano_toggle", "cluster": cluster_id},
            size="sm", color="secondary", outline=True,
            className="mt-2",
        )
        volcano_collapse = dbc.Collapse(
            dcc.Graph(figure=volcano_fig,
                      config={"displayModeBar": True})
            if volcano_fig is not None
            else html.Div("Volcano 描画不可", className="text-muted small"),
            id={"type": "lv_volcano_collapse", "cluster": cluster_id},
            is_open=False,
        )
    else:
        volcano_toggle = None
        volcano_collapse = None

    return dbc.Card(
        className="cluster-card mb-3",
        children=dbc.CardBody([
            html.H5(
                [
                    html.Span(
                        "●",
                        style={"color": color, "marginRight": "8px",
                               "fontSize": "1.4em"},
                    ),
                    f"Cluster {cluster_id}",
                    html.Span(
                        f"  {n_cells:,} cells ({pct:.1f}%)",
                        style={"fontWeight": "normal", "color": "#666",
                               "marginLeft": "8px", "fontSize": "0.85em"},
                    ),
                ],
                className="cluster-card-header mb-3 pb-2 border-bottom",
            ),
            dbc.Row([
                dbc.Col([
                    html.H6("Highlighted UMAP", className="text-muted small"),
                    dcc.Graph(figure=hl_umap,
                              config={"displayModeBar": False}),
                ], lg=6, md=12, className="mb-2"),
                dbc.Col([
                    html.H6("Highlighted Spatial",
                            className="text-muted small"),
                    hl_spatial,
                ], lg=6, md=12, className="mb-2"),
            ]),
            html.Div([
                html.H6(
                    "Top 5 Up-regulated Markers",
                    className="text-muted small mt-3",
                ),
                top_markers_view,
            ], className="mb-2"),
            volcano_toggle,
            volcano_collapse,
        ]),
    )


def _build_top_markers_table(deg_records, top_n=5):
    """このクラスタの DEG レコードから Up-regulated 上位 N 件のテーブル"""
    if not deg_records:
        return html.Div(
            "マーカーデータなし", className="text-muted small",
        )

    df = pd.DataFrame(deg_records)
    if "avg_log2FC" not in df.columns:
        return html.Div(
            "log2FC データなし", className="text-muted small",
        )
    df["avg_log2FC"] = pd.to_numeric(df["avg_log2FC"], errors="coerce")
    df = df.dropna(subset=["avg_log2FC"]).sort_values(
        "avg_log2FC", ascending=False,
    ).head(top_n)
    if df.empty:
        return html.Div(
            "上位マーカーなし", className="text-muted small",
        )

    # 表示カラム
    cols_order = []
    if "gene" in df.columns:
        cols_order.append("gene")
    if "annotation" in df.columns:
        cols_order.append("annotation")
    for c in ["avg_log2FC", "p_val", "p_val_adj"]:
        if c in df.columns:
            cols_order.append(c)

    # フォーマット
    df_view = df.copy()
    if "annotation" in df_view.columns and "gene" in df_view.columns:
        df_view["annotation"] = df_view.apply(
            lambda r: r["annotation"]
            if is_meaningful_annotation(
                r.get("annotation", ""), r.get("gene", "")
            ) else "",
            axis=1,
        )
    for c in ["avg_log2FC"]:
        if c in df_view.columns:
            df_view[c] = df_view[c].map(lambda v: f"{v:.2f}")
    for c in ["p_val", "p_val_adj"]:
        if c in df_view.columns:
            df_view[c] = pd.to_numeric(df_view[c], errors="coerce").map(
                lambda v: f"{v:.2e}" if pd.notna(v) else ""
            )

    return dash_table.DataTable(
        columns=[{"name": c, "id": c} for c in cols_order],
        data=df_view[cols_order].to_dict("records"),
        style_cell={"fontSize": "12px", "padding": "4px",
                    "textAlign": "left"},
        style_header={"fontWeight": "bold", "backgroundColor": "#f1f3f5",
                      "fontSize": "12px"},
        style_table={"overflowX": "auto"},
    )


def _build_volcano_fig(deg_records, cluster_id):
    """シンプルな Volcano Plot（このクラスタ分のみ）"""
    if not deg_records:
        return None
    df = pd.DataFrame(deg_records)
    if "cluster" in df.columns:
        df = df[df["cluster"].astype(str) == str(cluster_id)]
    if df.empty or "avg_log2FC" not in df.columns:
        return None

    if "p_val_adj_raw" in df.columns:
        df["p_num"] = pd.to_numeric(df["p_val_adj_raw"], errors="coerce")
    elif "p_val_adj" in df.columns:
        df["p_num"] = pd.to_numeric(df["p_val_adj"], errors="coerce")
    else:
        df["p_num"] = pd.to_numeric(df.get("p_val"), errors="coerce")
    df["avg_log2FC"] = pd.to_numeric(df["avg_log2FC"], errors="coerce")
    df = df.dropna(subset=["p_num", "avg_log2FC"])
    if df.empty:
        return None

    min_pos = df.loc[df["p_num"] > 0, "p_num"].min() if (df["p_num"] > 0).any() else 5e-324
    df["neg_log10_p"] = -np.log10(df["p_num"].clip(lower=min_pos))

    fc_thresh = 0.5
    p_thresh = 1.3  # -log10(0.05)

    df["category"] = "NS"
    df.loc[
        (df["avg_log2FC"] >= fc_thresh) & (df["neg_log10_p"] >= p_thresh),
        "category",
    ] = "Up"
    df.loc[
        (df["avg_log2FC"] <= -fc_thresh) & (df["neg_log10_p"] >= p_thresh),
        "category",
    ] = "Down"

    # hover テキスト
    if "gene" in df.columns and "annotation" in df.columns:
        df["hover"] = df.apply(
            lambda r: f"{r['gene']}<br>({r['annotation']})"
            if is_meaningful_annotation(
                r.get("annotation", ""), r.get("gene", "")
            ) else r["gene"],
            axis=1,
        )
    elif "gene" in df.columns:
        df["hover"] = df["gene"].astype(str)
    else:
        df["hover"] = ""

    fig = go.Figure()
    for cat, color in [
        ("NS", "#bbbbbb"),
        ("Up", "#FF2D2D"),
        ("Down", "#2D6FFF"),
    ]:
        d = df[df["category"] == cat]
        if d.empty:
            continue
        fig.add_trace(go.Scatter(
            x=d["avg_log2FC"],
            y=d["neg_log10_p"],
            mode="markers",
            marker=dict(size=6, color=color, opacity=0.7),
            name=cat,
            text=d["hover"],
            hoverinfo="text+x+y",
        ))
    fig.add_hline(y=p_thresh, line_dash="dash",
                  line_color="gray", opacity=0.5)
    fig.add_vline(x=fc_thresh, line_dash="dash",
                  line_color="gray", opacity=0.5)
    fig.add_vline(x=-fc_thresh, line_dash="dash",
                  line_color="gray", opacity=0.5)
    fig.update_layout(
        xaxis_title="log2 Fold Change",
        yaxis_title="-log10(p-value)",
        height=400,
        margin=dict(l=50, r=10, t=10, b=40),
        showlegend=True,
        legend=dict(orientation="h", x=0, y=1.02),
    )
    return fig


def _build_heatmap_section(deg_records, top_n_per_cluster=3):
    """全クラスタ × 各クラスタ Top N markers の Z-score ヒートマップ"""
    if not deg_records:
        return html.Div(
            className="heatmap-section mb-5",
            children=[
                html.H4("Cross-cluster Heatmap",
                        className="mb-3 border-bottom pb-2"),
                html.Div("DEG データがありません",
                         className="text-muted small"),
            ],
        )

    df = pd.DataFrame(deg_records)
    needed = {"cluster", "gene", "avg_log2FC"}
    if not needed.issubset(df.columns):
        return html.Div(
            className="heatmap-section mb-5",
            children=[
                html.H4("Cross-cluster Heatmap",
                        className="mb-3 border-bottom pb-2"),
                html.Div("ヒートマップ用列が不足しています",
                         className="text-muted small"),
            ],
        )

    df["avg_log2FC"] = pd.to_numeric(df["avg_log2FC"], errors="coerce")
    df = df.dropna(subset=["avg_log2FC", "cluster", "gene"])

    clusters = sorted(df["cluster"].astype(str).unique(), key=_cluster_sort_key)

    # 各クラスタ上位 N gene を集約
    top_genes = []
    seen = set()
    for c in clusters:
        df_c = df[df["cluster"].astype(str) == c]
        for g in df_c.nlargest(top_n_per_cluster, "avg_log2FC")["gene"].tolist():
            if g not in seen:
                top_genes.append(g)
                seen.add(g)
    if not top_genes:
        return html.Div(
            className="heatmap-section mb-5",
            children=[
                html.H4("Cross-cluster Heatmap",
                        className="mb-3 border-bottom pb-2"),
                html.Div("上位マーカーが抽出できません",
                         className="text-muted small"),
            ],
        )

    # gene × cluster pivot
    pivot = df.pivot_table(
        index="gene", columns="cluster",
        values="avg_log2FC", aggfunc="mean",
    )
    pivot = pivot.reindex(top_genes)
    cluster_cols = [c for c in clusters if c in pivot.columns]
    pivot = pivot[cluster_cols].fillna(0.0)

    fig = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=[f"C{c}" for c in pivot.columns],
        y=pivot.index.astype(str),
        colorscale="RdBu_r",
        zmid=0,
        colorbar=dict(title="log2FC"),
        hovertemplate="Cluster %{x}<br>Gene: %{y}<br>log2FC: %{z:.2f}<extra></extra>",
    ))
    fig.update_layout(
        height=max(300, 22 * len(pivot.index) + 80),
        margin=dict(l=140, r=20, t=20, b=60),
        xaxis=dict(tickangle=0),
    )
    return html.Div(
        className="heatmap-section mb-5",
        children=[
            html.H4(
                f"Cross-cluster Heatmap (Top {top_n_per_cluster} markers / cluster)",
                className="mb-3 border-bottom pb-2",
            ),
            dcc.Graph(figure=fig, config={"displayModeBar": True}),
        ],
    )
