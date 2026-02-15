# =============================================================================
# MSI Analysis Application - Interactive Analysis Callbacks
# インタラクティブ解析 コールバック
# =============================================================================

import io
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from dash import Input, Output, State, callback, ctx, no_update, html, dcc

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
    "cache_dir": None,
}


# ---------------------------------------------------------------------------
# 統合手法検出ヘルパー
# ---------------------------------------------------------------------------

def _detect_integration_methods(folder_path: str) -> dict:
    """結果フォルダ内のRDSファイルを検出し、統合手法→パスのマッピングを返す。

    Returns:
        {"Harmony": "path/to/seu_harmony.rds", "RPCA": "path/to/seu_rpca.rds", ...}
    """
    rds_map = {}
    base = Path(folder_path)
    if not base.is_dir():
        return rds_map

    # RDS_Files/ フォルダ内を検索
    rds_dir = base / "RDS_Files"
    search_dirs = [rds_dir, base] if rds_dir.is_dir() else [base]

    for search_dir in search_dirs:
        for rds_file in search_dir.glob("*.rds"):
            name_lower = rds_file.name.lower()
            if "harmony" in name_lower and "Harmony" not in rds_map:
                rds_map["Harmony"] = str(rds_file)
            elif "rpca" in name_lower and "RPCA" not in rds_map:
                rds_map["RPCA"] = str(rds_file)
            elif "single" in name_lower and "PCA" not in rds_map:
                rds_map["PCA"] = str(rds_file)

    # rglob でサブフォルダも検索（上記で見つからない場合のフォールバック）
    if not rds_map:
        for rds_file in base.rglob("*.rds"):
            name_lower = rds_file.name.lower()
            if "harmony" in name_lower and "Harmony" not in rds_map:
                rds_map["Harmony"] = str(rds_file)
            elif "rpca" in name_lower and "RPCA" not in rds_map:
                rds_map["RPCA"] = str(rds_file)
            elif "single" in name_lower and "PCA" not in rds_map:
                rds_map["PCA"] = str(rds_file)

    return rds_map


def _build_msi_samples_ui(folder_path: str):
    """MSIフォルダ内のサンプル一覧UIを生成"""
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
# 結果フォルダスキャン → 統合手法検出
# ---------------------------------------------------------------------------

@callback(
    [Output("interactive_integration_method", "options"),
     Output("interactive_integration_method", "value"),
     Output("interactive_rds_map", "data")],
    Input("scan_result_folder", "n_clicks"),
    State("interactive_result_folder", "value"),
    prevent_initial_call=True,
)
def scan_rds_files(n_clicks, folder_path):
    if not folder_path or not Path(folder_path).is_dir():
        return [], None, None

    rds_map = _detect_integration_methods(folder_path)
    if not rds_map:
        return [], None, None

    options = [{"label": k, "value": k} for k in rds_map.keys()]
    # Harmony を優先デフォルト、なければ最初の手法
    default = "Harmony" if "Harmony" in rds_map else list(rds_map.keys())[0]

    return options, default, rds_map


# ---------------------------------------------------------------------------
# 結果フォルダ変更時 → 自動スキャン
# ---------------------------------------------------------------------------

@callback(
    [Output("interactive_integration_method", "options", allow_duplicate=True),
     Output("interactive_integration_method", "value", allow_duplicate=True),
     Output("interactive_rds_map", "data", allow_duplicate=True)],
    Input("interactive_result_folder", "value"),
    prevent_initial_call=True,
)
def auto_scan_rds_files(folder_path):
    """結果フォルダのパスが設定された時、自動で統合手法を検出"""
    if not folder_path or not Path(folder_path).is_dir():
        return no_update, no_update, no_update

    rds_map = _detect_integration_methods(folder_path)
    if not rds_map:
        return no_update, no_update, no_update

    options = [{"label": k, "value": k} for k in rds_map.keys()]
    default = "Harmony" if "Harmony" in rds_map else list(rds_map.keys())[0]

    return options, default, rds_map


# ---------------------------------------------------------------------------
# MSIフォルダスキャン（ボタンクリック）
# ---------------------------------------------------------------------------

@callback(
    Output("interactive_msi_samples", "children"),
    Input("scan_msi_folder", "n_clicks"),
    State("interactive_msi_folder", "value"),
    prevent_initial_call=True,
)
def scan_msi_files(n_clicks, folder_path):
    return _build_msi_samples_ui(folder_path)


# ---------------------------------------------------------------------------
# MSIフォルダ変更時 → 自動スキャン
# ---------------------------------------------------------------------------

@callback(
    Output("interactive_msi_samples", "children", allow_duplicate=True),
    Input("interactive_msi_folder", "value"),
    prevent_initial_call=True,
)
def auto_scan_msi_files(folder_path):
    """MSIフォルダのパスが設定された時、自動でサンプル一覧を表示"""
    if not folder_path or not Path(folder_path).is_dir():
        return no_update
    return _build_msi_samples_ui(folder_path)


# ---------------------------------------------------------------------------
# データ読み込み（Seuratブリッジ経由）
# ---------------------------------------------------------------------------

@callback(
    [Output("interactive_data_info", "children"),
     Output("interactive_viz_container", "style"),
     Output("umap_highlight_cluster", "options"),
     Output("interactive_sample", "options"),
     Output("feature_select", "options"),
     Output("seurat_rds_path_store", "data"),
     Output("seurat_cache_dir_store", "data"),
     Output("deg_data_store", "data"),
     Output("deg_results_section", "style")],
    [Input("load_interactive_data", "n_clicks"),
     Input("interactive_integration_method", "value")],
    [State("interactive_rds_map", "data"),
     State("interactive_result_folder", "value")],
    prevent_initial_call=True,
)
def load_interactive_data(n_clicks, integration_method, rds_map, result_folder):
    _n_out = 9
    if not integration_method or not rds_map:
        return (
            "統合手法を選択してください（結果フォルダをスキャンしてください）",
            {"display": "none"}, [], [], [], None, None, None,
            {"display": "none"},
        )

    rds_path = rds_map.get(integration_method)
    if not rds_path or not Path(rds_path).exists():
        return (
            f"RDSファイルが見つかりません: {integration_method}",
            {"display": "none"}, [], [], [], None, None, None,
            {"display": "none"},
        )

    try:
        result = _bridge.extract_data(rds_path)

        _interactive_data["plot_data"] = result["plot_data"]
        _interactive_data["cluster_stats"] = result["cluster_stats"]
        _interactive_data["features_list"] = result["features_list"]
        _interactive_data["meta"] = result["meta"]
        _interactive_data["rds_path"] = rds_path
        _interactive_data["cache_dir"] = result.get("cache_dir")

        meta = result["meta"]
        info_text = (
            f"読み込み完了 [{integration_method}]: "
            f"{meta.get('n_cells', '?')} cells, "
            f"{meta.get('n_clusters', '?')} clusters, "
            f"samples: {', '.join(meta.get('samples', []))}"
        )

        # クラスタ選択肢
        clusters = sorted(_interactive_data["plot_data"]["Cluster"].unique())
        cluster_options = [
            {"label": f"Cluster {c}", "value": str(c)} for c in clusters
        ]

        # サンプル選択肢
        samples = sorted(_interactive_data["plot_data"]["Sample"].unique())
        sample_options = [{"label": s, "value": s} for s in samples]

        # Feature選択肢（全件、dcc.Dropdownのsearch機能でフィルタ）
        features = result["features_list"]
        feature_options = [{"label": f, "value": f} for f in features]

        # DEG 結果を探す（選択した統合手法のフォルダを優先）
        deg_data = None
        deg_style = {"display": "none"}
        if result_folder:
            result_base = Path(result_folder)
            deg_data = _load_deg_results(result_base, integration_method)
        else:
            rds_dir = Path(rds_path).parent
            result_base = rds_dir.parent if rds_dir.name == "RDS_Files" else rds_dir
            deg_data = _load_deg_results(result_base, integration_method)
        if deg_data:
            deg_style = {}

        return (
            info_text,
            {},  # 可視化コンテナ表示
            cluster_options,
            sample_options,
            feature_options,
            rds_path,
            str(result.get("cache_dir", "")),
            deg_data,
            deg_style,
        )

    except Exception as e:
        return (
            f"読み込みエラー: {e}",
            {"display": "none"}, [], [], [], None, None, None,
            {"display": "none"},
        )


def _load_deg_results(
    result_base: Path, integration_method: str | None = None
) -> list[dict] | None:
    """解析結果フォルダ内の DEG CSV を読み込む"""
    # 選択した統合手法のフォルダを優先検索
    if integration_method and integration_method in ("Harmony", "RPCA", "PCA"):
        method_dir = integration_method
    else:
        method_dir = "Harmony"

    search_patterns = [
        f"{method_dir}/*deg*markers*.csv",
        f"{method_dir}/*top*markers*.csv",
        "Harmony/*deg*markers*.csv",
        "Harmony/*top*markers*.csv",
        "RPCA/*deg*markers*.csv",
        "RPCA/*top*markers*.csv",
        "PCA/*deg*markers*.csv",
        "PCA/*top*markers*.csv",
        "*deg*markers*.csv",
        "*top*markers*.csv",
    ]
    for pattern in search_patterns:
        matches = list(result_base.glob(pattern))
        if matches:
            try:
                df = pd.read_csv(matches[0])
                # 列名を標準化
                col_map = {}
                for col in df.columns:
                    cl = col.lower().strip()
                    if cl in ("gene", "row.names", "x", "...1"):
                        col_map[col] = "gene"
                    elif "cluster" in cl:
                        col_map[col] = "cluster"
                    elif "avg_log2fc" in cl or "avg_logfc" in cl:
                        col_map[col] = "avg_log2FC"
                    elif "p_val_adj" in cl:
                        col_map[col] = "p_val_adj"
                    elif cl == "pct.1":
                        col_map[col] = "pct.1"
                    elif cl == "pct.2":
                        col_map[col] = "pct.2"

                df = df.rename(columns=col_map)
                # gene列がない場合、最初の列をgeneとする
                if "gene" not in df.columns and len(df.columns) > 0:
                    df = df.rename(columns={df.columns[0]: "gene"})

                # 必要な列のみ抽出
                keep = [c for c in ["gene", "cluster", "avg_log2FC", "p_val_adj",
                                     "pct.1", "pct.2"] if c in df.columns]
                df = df[keep]

                # 数値列を丸める
                for col in ["avg_log2FC", "p_val_adj", "pct.1", "pct.2"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                        if col == "p_val_adj":
                            df[col] = df[col].map(
                                lambda x: f"{x:.2e}" if pd.notna(x) else ""
                            )
                        else:
                            df[col] = df[col].round(4)

                return df.to_dict("records")
            except Exception:
                continue
    return None


# ---------------------------------------------------------------------------
# フィーチャー検索（サーバーサイドフィルタ）
# ---------------------------------------------------------------------------

@callback(
    Output("feature_select", "options", allow_duplicate=True),
    Input("feature_select", "search_value"),
    prevent_initial_call=True,
)
def filter_features(search_value):
    """フィーチャードロップダウンの検索値に基づいてオプションをフィルタ"""
    features = _interactive_data.get("features_list")
    if not features:
        return []

    if not search_value:
        # 検索なしの場合は全件（最大500件）
        return [{"label": f, "value": f} for f in features[:500]]

    # 検索値でフィルタ（大文字小文字区別なし）
    keyword = search_value.lower()
    filtered = [f for f in features if keyword in f.lower()]
    return [{"label": f, "value": f} for f in filtered[:100]]


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
# Spatial Mapping プロット (UMAP選択連動)
# ---------------------------------------------------------------------------

@callback(
    Output("spatial_mapping_plot", "figure"),
    [Input("interactive_sample", "value"),
     Input("umap_highlight_cluster", "value"),
     Input("interactive_umap_plot", "selectedData")],
    State("seurat_rds_path_store", "data"),
)
def update_spatial_plot(sample, highlight_clusters, selected_data, rds_path):
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

    # UMAP での範囲選択があれば、選択されたセルをハイライト
    selected_cell_ids = set()
    if selected_data and selected_data.get("points"):
        for pt in selected_data["points"]:
            if pt.get("text"):
                selected_cell_ids.add(pt["text"])

    if selected_cell_ids:
        # 選択されたセルをハイライト表示
        mask_selected = filtered["CellID"].isin(selected_cell_ids)
        mask_bg = ~mask_selected

        if mask_bg.any():
            fig.add_trace(go.Scattergl(
                x=filtered.loc[mask_bg, "SpatialX"],
                y=-filtered.loc[mask_bg, "SpatialY"],
                mode="markers",
                marker=dict(size=2, color="lightgray", opacity=0.2),
                name="Other",
                showlegend=False,
                hoverinfo="skip",
            ))

        if mask_selected.any():
            fig.add_trace(go.Scattergl(
                x=filtered.loc[mask_selected, "SpatialX"],
                y=-filtered.loc[mask_selected, "SpatialY"],
                mode="markers",
                marker=dict(size=3, color="red"),
                name=f"Selected ({mask_selected.sum()})",
            ))

    elif highlight_clusters and len(highlight_clusters) > 0:
        highlight_set = set(str(c) for c in highlight_clusters)
        for cl in highlight_clusters:
            mask = filtered["Cluster"].astype(str) == str(cl)
            if mask.any():
                fig.add_trace(go.Scattergl(
                    x=filtered.loc[mask, "SpatialX"],
                    y=-filtered.loc[mask, "SpatialY"],
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
# Feature プロット（Parquet高速読み込み優先 → R fallback）
# ---------------------------------------------------------------------------

@callback(
    Output("feature_plot", "figure"),
    Input("show_feature_plot", "n_clicks"),
    [State("feature_select", "value"),
     State("seurat_rds_path_store", "data"),
     State("seurat_cache_dir_store", "data")],
    prevent_initial_call=True,
)
def update_feature_plot(n_clicks, feature_name, rds_path, cache_dir_str):
    if not feature_name or not rds_path:
        return go.Figure()

    df = _interactive_data.get("plot_data")
    if df is None:
        return go.Figure()

    try:
        # Parquet からの高速読み込みを優先
        expression = None
        if cache_dir_str:
            cache_dir = Path(cache_dir_str)
            expression = _bridge.get_feature_expression_fast(
                cache_dir, feature_name
            )

        # Parquet にない場合は R subprocess で取得
        if expression is None:
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


# ---------------------------------------------------------------------------
# DEG テーブル更新（クラスタ選択/ハイライト連動）
# ---------------------------------------------------------------------------

@callback(
    Output("deg_results_table", "data"),
    [Input("cluster_stats_table", "selected_rows"),
     Input("umap_highlight_cluster", "value")],
    [State("cluster_stats_table", "data"),
     State("deg_data_store", "data")],
)
def update_deg_table(selected_rows, highlight, table_data, deg_data):
    if not deg_data:
        return []

    cluster_id = None
    if selected_rows and table_data:
        cluster_id = table_data[selected_rows[0]].get("Cluster")
    elif highlight and len(highlight) == 1:
        cluster_id = str(highlight[0])

    if cluster_id is not None:
        # 指定クラスタのみ表示
        filtered = [
            r for r in deg_data
            if str(r.get("cluster", "")) == str(cluster_id)
        ]
        return filtered if filtered else deg_data[:50]

    # クラスタ未選択時は全データ（上位50件）
    return deg_data[:50]


# ---------------------------------------------------------------------------
# DEG テーブル行クリック → Feature Plot に反映
# ---------------------------------------------------------------------------

@callback(
    [Output("feature_select", "value", allow_duplicate=True),
     Output("show_feature_plot", "n_clicks", allow_duplicate=True)],
    Input("deg_results_table", "selected_rows"),
    State("deg_results_table", "data"),
    prevent_initial_call=True,
)
def deg_row_to_feature(selected_rows, table_data):
    if not selected_rows or not table_data:
        return no_update, no_update

    row = table_data[selected_rows[0]]
    gene = row.get("gene", "")
    if not gene:
        return no_update, no_update

    # feature_select にセット + show_feature_plot をトリガ
    return gene, 1


# ---------------------------------------------------------------------------
# HTML レポートエクスポート
# ---------------------------------------------------------------------------

@callback(
    [Output("download_html", "data"),
     Output("export_status", "children", allow_duplicate=True)],
    Input("export_html_report", "n_clicks"),
    [State("interactive_umap_plot", "figure"),
     State("spatial_mapping_plot", "figure"),
     State("feature_plot", "figure"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def export_html_report(n_clicks, umap_fig, spatial_fig, feature_fig, rds_path):
    if not n_clicks:
        return no_update, no_update

    try:
        # 各プロットを HTML に変換
        parts = []
        parts.append("<html><head><meta charset='utf-8'>")
        parts.append("<title>MSI Interactive Analysis Report</title>")
        parts.append("</head><body>")
        parts.append(f"<h1>MSI Interactive Analysis Report</h1>")
        parts.append(
            f"<p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>"
        )
        if rds_path:
            parts.append(f"<p>Source: {Path(rds_path).name}</p>")

        # クラスタ統計
        meta = _interactive_data.get("meta", {})
        parts.append(f"<p>Cells: {meta.get('n_cells', '?')} | ")
        parts.append(f"Clusters: {meta.get('n_clusters', '?')} | ")
        parts.append(f"Samples: {', '.join(meta.get('samples', []))}</p>")

        parts.append("<hr>")

        # UMAP
        if umap_fig:
            fig = go.Figure(umap_fig)
            parts.append("<h2>UMAP Plot</h2>")
            parts.append(pio.to_html(
                fig, include_plotlyjs="cdn", full_html=False,
            ))

        # Spatial
        if spatial_fig:
            fig = go.Figure(spatial_fig)
            if fig.data:  # 空でなければ
                parts.append("<h2>Spatial Mapping</h2>")
                parts.append(pio.to_html(
                    fig, include_plotlyjs=False, full_html=False,
                ))

        # Feature
        if feature_fig:
            fig = go.Figure(feature_fig)
            if fig.data:
                parts.append("<h2>Feature Plot</h2>")
                parts.append(pio.to_html(
                    fig, include_plotlyjs=False, full_html=False,
                ))

        parts.append("</body></html>")
        html_content = "\n".join(parts)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"interactive_report_{timestamp}.html"

        return (
            dcc.send_string(html_content, filename=filename),
            f"HTMLレポートを出力しました: {filename}",
        )

    except Exception as e:
        return no_update, f"エクスポートエラー: {e}"


# ---------------------------------------------------------------------------
# CSV データエクスポート
# ---------------------------------------------------------------------------

@callback(
    [Output("download_csv", "data"),
     Output("export_status", "children")],
    Input("export_csv_data", "n_clicks"),
    prevent_initial_call=True,
)
def export_csv_data(n_clicks):
    if not n_clicks:
        return no_update, no_update

    df = _interactive_data.get("plot_data")
    if df is None:
        return no_update, "データが読み込まれていません"

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"interactive_data_{timestamp}.csv"

        return (
            dcc.send_data_frame(df.to_csv, filename=filename, index=False),
            f"CSVデータを出力しました: {filename}",
        )

    except Exception as e:
        return no_update, f"エクスポートエラー: {e}"


# ---------------------------------------------------------------------------
# プロジェクト / サブプロジェクト選択コールバック
# ---------------------------------------------------------------------------

@callback(
    Output("interactive_project_select", "options"),
    [Input("main_tabs", "active_tab"),
     Input("current_page", "data")],
    prevent_initial_call=True,
)
def populate_interactive_projects(active_tab, current_page):
    """interactiveタブがアクティブになった時にプロジェクト一覧を取得"""
    if current_page != "analysis" or active_tab != "interactive":
        return no_update
    from app.services.project_manager import list_projects
    projects = list_projects()
    return [{"label": p["name"], "value": p["id"]} for p in projects]


@callback(
    [Output("interactive_sub_project_select", "options", allow_duplicate=True),
     Output("interactive_sub_project_select", "value", allow_duplicate=True)],
    Input("interactive_project_select", "value"),
    prevent_initial_call=True,
)
def populate_interactive_sub_projects(project_id):
    """プロジェクト選択時にサブプロジェクト一覧を取得し、先頭を自動選択"""
    if not project_id:
        return [], None
    from app.services.project_manager import list_sub_projects
    subs = list_sub_projects(project_id)
    options = [{"label": s["name"], "value": s["id"]} for s in subs]
    first_value = options[0]["value"] if options else None
    return options, first_value


@callback(
    [Output("interactive_result_folder", "value", allow_duplicate=True),
     Output("interactive_msi_folder", "value", allow_duplicate=True),
     Output("interactive_data_info", "children", allow_duplicate=True)],
    Input("interactive_sub_project_select", "value"),
    State("interactive_project_select", "value"),
    prevent_initial_call=True,
)
def set_interactive_folders_from_sub_project(sub_id, project_id):
    """サブプロジェクト選択時にフォルダパスを自動設定"""
    if not sub_id or not project_id:
        return no_update, no_update, no_update
    from app.services.project_manager import get_sub_project
    sub = get_sub_project(project_id, sub_id)
    if not sub:
        return no_update, no_update, no_update
    result_dir = sub.get("last_result_dir") or sub.get("output_dir", "")
    data_folder = sub.get("data_folder", "")
    # 未設定フォルダの警告メッセージ
    warnings = []
    if not result_dir:
        warnings.append("結果フォルダが未設定です")
    if not data_folder:
        warnings.append("MSIデータフォルダが未設定です")
    msg = "⚠ " + "、".join(warnings) if warnings else ""
    return (result_dir, data_folder, msg)
