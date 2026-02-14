# =============================================================================
# MSI Analysis Application - Interactive Analysis Callbacks
# インタラクティブ解析 コールバック
# =============================================================================

from pathlib import Path

import plotly.graph_objects as go
from dash import Input, Output, State, callback, ctx, no_update, html, dash_table

from app.services.seurat_bridge import SeuratBridge

# Seuratブリッジのシングルトン
_bridge = SeuratBridge()

# モジュールレベルのデータキャッシュ
_interactive_data = {
    "plot_data": None,
    "cluster_stats": None,
    "features_list": None,
    "meta": None,
    "rds_path": None,
}


# ---------------------------------------------------------------------------
# 結果フォルダスキャン → RDSファイル一覧
# ---------------------------------------------------------------------------

@callback(
    Output("interactive_rds_select", "options"),
    Input("scan_result_folder", "n_clicks"),
    State("interactive_result_folder", "value"),
    prevent_initial_call=True,
)
def scan_rds_files(n_clicks, folder_path):
    if not folder_path or not Path(folder_path).is_dir():
        return []

    rds_files = sorted(Path(folder_path).rglob("*.rds"))
    # Harmony ファイルを優先表示
    harmony = [f for f in rds_files if "harmony" in f.name.lower()]
    others = [f for f in rds_files if "harmony" not in f.name.lower()]
    ordered = harmony + others

    return [{"label": f.name, "value": str(f)} for f in ordered]


# ---------------------------------------------------------------------------
# MSIフォルダスキャン
# ---------------------------------------------------------------------------

@callback(
    Output("interactive_msi_samples", "children"),
    Input("scan_msi_folder", "n_clicks"),
    State("interactive_msi_folder", "value"),
    prevent_initial_call=True,
)
def scan_msi_files(n_clicks, folder_path):
    if not folder_path or not Path(folder_path).is_dir():
        return html.Div("MSIフォルダが見つかりません", className="text-muted")

    from app.services.data_manager import list_msi_files
    samples = list_msi_files(folder_path)
    if not samples:
        return html.Div("MSIファイルが見つかりません", className="text-warning")

    import dash_bootstrap_components as dbc
    return dbc.Checklist(
        id="interactive_msi_sample_checks",
        options=[{"label": s, "value": s} for s in samples],
        value=samples,
    )


# ---------------------------------------------------------------------------
# データ読み込み（Seuratブリッジ経由）
# ---------------------------------------------------------------------------

@callback(
    [Output("interactive_data_info", "children"),
     Output("interactive_viz_container", "style"),
     Output("umap_highlight_cluster", "options"),
     Output("interactive_sample", "options"),
     Output("feature_select", "options"),
     Output("seurat_rds_path_store", "data")],
    Input("load_interactive_data", "n_clicks"),
    State("interactive_rds_select", "value"),
    prevent_initial_call=True,
)
def load_interactive_data(n_clicks, rds_path):
    if not rds_path or not Path(rds_path).exists():
        return "RDSファイルを選択してください", {"display": "none"}, [], [], [], None

    try:
        result = _bridge.extract_data(rds_path)

        _interactive_data["plot_data"] = result["plot_data"]
        _interactive_data["cluster_stats"] = result["cluster_stats"]
        _interactive_data["features_list"] = result["features_list"]
        _interactive_data["meta"] = result["meta"]
        _interactive_data["rds_path"] = rds_path

        meta = result["meta"]
        info_text = (
            f"読み込み完了: {meta.get('n_cells', '?')} cells, "
            f"{meta.get('n_clusters', '?')} clusters, "
            f"samples: {', '.join(meta.get('samples', []))}"
        )

        # クラスタ選択肢
        clusters = sorted(_interactive_data["plot_data"]["Cluster"].unique())
        cluster_options = [{"label": f"Cluster {c}", "value": str(c)} for c in clusters]

        # サンプル選択肢
        samples = sorted(_interactive_data["plot_data"]["Sample"].unique())
        sample_options = [{"label": s, "value": s} for s in samples]

        # Feature選択肢
        features = result["features_list"]
        feature_options = [{"label": f, "value": f} for f in features[:500]]

        return (
            info_text,
            {},  # 可視化コンテナ表示
            cluster_options,
            sample_options,
            feature_options,
            rds_path,
        )

    except Exception as e:
        return (
            f"読み込みエラー: {e}",
            {"display": "none"}, [], [], [], None,
        )


# ---------------------------------------------------------------------------
# UMAP プロット
# ---------------------------------------------------------------------------

@callback(
    Output("interactive_umap_plot", "figure"),
    [Input("umap_color_by", "value"),
     Input("umap_highlight_cluster", "value"),
     Input("umap_show_legend", "value")],
    State("seurat_rds_path_store", "data"),
)
def update_umap_plot(color_by, highlight_clusters, show_legend, rds_path):
    df = _interactive_data.get("plot_data")
    if df is None:
        return go.Figure()

    fig = go.Figure()

    if highlight_clusters and len(highlight_clusters) > 0:
        # ハイライトモード
        highlight_set = set(str(c) for c in highlight_clusters)

        # 非ハイライト
        mask_bg = ~df["Cluster"].astype(str).isin(highlight_set)
        if mask_bg.any():
            fig.add_trace(go.Scattergl(
                x=df.loc[mask_bg, "UMAP_1"],
                y=df.loc[mask_bg, "UMAP_2"],
                mode="markers",
                marker=dict(size=2, color="lightgray", opacity=0.1),
                name="Other",
                showlegend=False,
                hoverinfo="skip",
            ))

        # ハイライト
        for cl in highlight_clusters:
            mask = df["Cluster"].astype(str) == str(cl)
            if mask.any():
                fig.add_trace(go.Scattergl(
                    x=df.loc[mask, "UMAP_1"],
                    y=df.loc[mask, "UMAP_2"],
                    mode="markers",
                    marker=dict(size=3),
                    name=f"Cluster {cl}",
                    text=df.loc[mask, "CellID"],
                    hovertemplate="Cluster: %{meta}<br>%{text}<extra></extra>",
                    meta=[str(cl)] * mask.sum(),
                ))
    else:
        # 全体表示
        color_col = color_by if color_by in df.columns else "Cluster"
        categories = sorted(df[color_col].unique())

        for cat in categories:
            mask = df[color_col] == cat
            fig.add_trace(go.Scattergl(
                x=df.loc[mask, "UMAP_1"],
                y=df.loc[mask, "UMAP_2"],
                mode="markers",
                marker=dict(size=2),
                name=str(cat),
                text=df.loc[mask, "CellID"],
                hovertemplate=f"{color_col}: {cat}<br>" + "%{text}<extra></extra>",
            ))

    fig.update_layout(
        dragmode="select",
        showlegend=bool(show_legend),
        margin=dict(l=40, r=10, t=30, b=40),
        xaxis_title="UMAP_1",
        yaxis_title="UMAP_2",
        template="plotly_white",
    )

    return fig


# ---------------------------------------------------------------------------
# クラスタ統計テーブル
# ---------------------------------------------------------------------------

@callback(
    Output("cluster_stats_table", "data"),
    Input("seurat_rds_path_store", "data"),
)
def update_cluster_stats(rds_path):
    df = _interactive_data.get("plot_data")
    if df is None:
        return []

    total = len(df)
    stats = df["Cluster"].value_counts().sort_index()
    return [
        {"Cluster": str(c), "Pixels": int(n), "Percent": f"{n / total * 100:.1f}%"}
        for c, n in stats.items()
    ]


# ---------------------------------------------------------------------------
# クラスタ情報テキスト
# ---------------------------------------------------------------------------

@callback(
    Output("cluster_info_text", "children"),
    [Input("cluster_stats_table", "selected_rows"),
     Input("umap_highlight_cluster", "value")],
    State("cluster_stats_table", "data"),
)
def update_cluster_info(selected_rows, highlight, table_data):
    df = _interactive_data.get("plot_data")
    if df is None:
        return "データを読み込んでください"

    cluster_id = None
    if selected_rows and table_data:
        cluster_id = table_data[selected_rows[0]].get("Cluster")
    elif highlight and len(highlight) == 1:
        cluster_id = str(highlight[0])

    if cluster_id is None:
        meta = _interactive_data.get("meta", {})
        return (
            f"Total cells: {meta.get('n_cells', '?')}\n"
            f"Clusters: {meta.get('n_clusters', '?')}\n"
            f"Samples: {', '.join(meta.get('samples', []))}"
        )

    mask = df["Cluster"].astype(str) == str(cluster_id)
    n = mask.sum()
    total = len(df)
    samples = df.loc[mask, "Sample"].value_counts()
    sample_info = "\n".join(f"  {s}: {c} pixels" for s, c in samples.items())

    return f"Cluster {cluster_id}: {n} pixels ({n / total * 100:.1f}%)\n{sample_info}"


# ---------------------------------------------------------------------------
# Spatial Mapping プロット
# ---------------------------------------------------------------------------

@callback(
    Output("spatial_mapping_plot", "figure"),
    [Input("interactive_sample", "value"),
     Input("umap_highlight_cluster", "value")],
    State("seurat_rds_path_store", "data"),
)
def update_spatial_plot(sample, highlight_clusters, rds_path):
    df = _interactive_data.get("plot_data")
    if df is None or "SpatialX" not in df.columns:
        fig = go.Figure()
        fig.add_annotation(text="空間座標データがありません", showarrow=False,
                           xref="paper", yref="paper", x=0.5, y=0.5)
        return fig

    filtered = df.copy()
    if sample:
        filtered = filtered[filtered["Sample"] == sample]

    fig = go.Figure()

    if highlight_clusters and len(highlight_clusters) > 0:
        highlight_set = set(str(c) for c in highlight_clusters)
        for cl in highlight_clusters:
            mask = filtered["Cluster"].astype(str) == str(cl)
            if mask.any():
                fig.add_trace(go.Scattergl(
                    x=filtered.loc[mask, "SpatialX"],
                    y=-filtered.loc[mask, "SpatialY"],  # Y軸反転
                    mode="markers",
                    marker=dict(size=3),
                    name=f"Cluster {cl}",
                ))
    else:
        for cl in sorted(filtered["Cluster"].unique()):
            mask = filtered["Cluster"] == cl
            fig.add_trace(go.Scattergl(
                x=filtered.loc[mask, "SpatialX"],
                y=-filtered.loc[mask, "SpatialY"],
                mode="markers",
                marker=dict(size=2),
                name=str(cl),
            ))

    fig.update_layout(
        yaxis=dict(scaleanchor="x"),
        margin=dict(l=40, r=10, t=30, b=40),
        template="plotly_white",
    )
    return fig


# ---------------------------------------------------------------------------
# Feature プロット
# ---------------------------------------------------------------------------

@callback(
    Output("feature_plot", "figure"),
    Input("show_feature_plot", "n_clicks"),
    [State("feature_select", "value"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def update_feature_plot(n_clicks, feature_name, rds_path):
    if not feature_name or not rds_path:
        return go.Figure()

    df = _interactive_data.get("plot_data")
    if df is None:
        return go.Figure()

    try:
        expression = _bridge.get_feature_expression(rds_path, feature_name)

        fig = go.Figure(go.Scattergl(
            x=df["UMAP_1"],
            y=df["UMAP_2"],
            mode="markers",
            marker=dict(
                size=2,
                color=expression,
                colorscale="Plasma",
                colorbar=dict(title=feature_name),
                showscale=True,
            ),
            text=df["CellID"],
            hovertemplate=f"{feature_name}: " + "%{marker.color:.4f}<br>%{text}<extra></extra>",
        ))

        fig.update_layout(
            margin=dict(l=40, r=10, t=30, b=40),
            xaxis_title="UMAP_1",
            yaxis_title="UMAP_2",
            template="plotly_white",
        )
        return fig

    except Exception as e:
        fig = go.Figure()
        fig.add_annotation(text=f"エラー: {e}", showarrow=False,
                           xref="paper", yref="paper", x=0.5, y=0.5)
        return fig
