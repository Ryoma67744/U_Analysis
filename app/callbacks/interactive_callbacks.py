# =============================================================================
# MSI Analysis Application - Interactive Analysis Callbacks
# インタラクティブ解析 コールバック
# =============================================================================

import io
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, ctx, no_update, html, dcc, dash_table, ALL

from app.config import DESI_COLORS_50, HIGHLIGHT_GRAY
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


def _cluster_sort_key(x):
    """クラスタIDの統一ソートキー（数値優先、文字列は末尾）"""
    s = str(x)
    return (int(s) if s.isdigit() else float("inf"), s)


def _get_cluster_color_map(clusters):
    """クラスタ値のリストから、ソート済みの色マップ dict を返す"""
    str_cls = list(set(str(c) for c in clusters))
    str_cls.sort(key=_cluster_sort_key)
    return {cl: DESI_COLORS_50[i % len(DESI_COLORS_50)] for i, cl in enumerate(str_cls)}


def _build_cluster_legend(color_map):
    """クラスタ番号 + カラー凡例パネルを生成する共通ヘルパー"""
    if not color_map:
        return html.Div()
    items = []
    for cluster_id, hex_color in color_map.items():
        items.append(
            html.Div(
                style={"display": "flex", "alignItems": "center", "gap": "6px",
                       "marginBottom": "2px"},
                children=[
                    html.Span(style={
                        "display": "inline-block", "width": "14px", "height": "14px",
                        "backgroundColor": hex_color,
                        "border": "1px solid #ccc", "flexShrink": "0",
                    }),
                    html.Span(f"{cluster_id}",
                              style={"fontSize": "0.8rem", "whiteSpace": "nowrap"}),
                ],
            )
        )
    return html.Div(
        style={"maxHeight": "400px", "overflowY": "auto", "padding": "8px"},
        children=items,
    )


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
        clusters = sorted(_interactive_data["plot_data"]["Cluster"].unique(), key=_cluster_sort_key)
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
# UMAP プロット — ヘルパー関数
# ---------------------------------------------------------------------------

def _build_umap_integrated_fig(df, color_by, highlight_clusters,
                                show_legend, show_labels, title=None):
    """統合UMAPのgo.Figureを生成（メイン/フルスクリーン共用）"""
    fig = go.Figure()
    color_map = _get_cluster_color_map(df["Cluster"])

    if highlight_clusters and len(highlight_clusters) > 0:
        highlight_set = set(str(c) for c in highlight_clusters)
        mask_bg = ~df["Cluster"].astype(str).isin(highlight_set)
        if mask_bg.any():
            fig.add_trace(go.Scattergl(
                x=df.loc[mask_bg, "UMAP_1"],
                y=df.loc[mask_bg, "UMAP_2"],
                mode="markers",
                marker=dict(size=2, color=HIGHLIGHT_GRAY, opacity=0.1),
                name="Other", showlegend=False, hoverinfo="skip",
            ))
        for cl in highlight_clusters:
            mask = df["Cluster"].astype(str) == str(cl)
            if mask.any():
                fig.add_trace(go.Scattergl(
                    x=df.loc[mask, "UMAP_1"],
                    y=df.loc[mask, "UMAP_2"],
                    mode="markers",
                    marker=dict(size=3, color=color_map.get(str(cl), "#999999")),
                    name=f"Cluster {cl}",
                    text=df.loc[mask, "CellID"],
                    hovertemplate="Cluster: %{meta}<br>%{text}<extra></extra>",
                    meta=[str(cl)] * mask.sum(),
                ))
    else:
        color_col = color_by if color_by in df.columns else "Cluster"
        categories = sorted(df[color_col].unique(), key=_cluster_sort_key)
        cat_color_map = _get_cluster_color_map(categories)
        for cat in categories:
            mask = df[color_col] == cat
            fig.add_trace(go.Scattergl(
                x=df.loc[mask, "UMAP_1"],
                y=df.loc[mask, "UMAP_2"],
                mode="markers",
                marker=dict(size=2, color=cat_color_map.get(str(cat), "#999999")),
                name=str(cat),
                text=df.loc[mask, "CellID"],
                hovertemplate=f"{color_col}: {cat}<br>" + "%{text}<extra></extra>",
            ))

    if show_labels:
        centroids = df.groupby("Cluster").agg(
            cx=("UMAP_1", "mean"), cy=("UMAP_2", "mean"),
        ).reset_index()
        for _, row in centroids.iterrows():
            fig.add_annotation(
                x=row["cx"], y=row["cy"],
                text=str(row["Cluster"]),
                showarrow=False,
                font=dict(size=14, color="black", family="Arial Black"),
                bgcolor="rgba(255,255,255,0.7)", borderpad=2,
            )

    layout_opts = dict(
        dragmode="select",
        showlegend=bool(show_legend),
        margin=dict(l=40, r=10, t=40 if title else 30, b=40),
        xaxis_title="UMAP_1", yaxis_title="UMAP_2",
        template="plotly_white",
    )
    if title:
        layout_opts["title"] = dict(text=title, font=dict(size=14), x=0.5)
    fig.update_layout(**layout_opts)
    return fig


def _build_umap_per_sample_graphs(df, color_map, highlight_clusters,
                                   show_labels, graph_height="300px"):
    """サンプル別UMAPのhtml.Divリストを生成（メイン/フルスクリーン共用）"""
    samples = sorted(df["Sample"].unique())
    if len(samples) <= 1:
        return [html.Div("サンプルが1つのみです", className="text-muted small mt-2")]

    graphs = []
    for s in samples:
        fig = go.Figure()
        mask_sample = df["Sample"] == s
        mask_other = ~mask_sample

        if mask_other.any():
            fig.add_trace(go.Scattergl(
                x=df.loc[mask_other, "UMAP_1"],
                y=df.loc[mask_other, "UMAP_2"],
                mode="markers",
                marker=dict(size=1, color=HIGHLIGHT_GRAY, opacity=0.1),
                name="Other", showlegend=False, hoverinfo="skip",
            ))

        df_s = df[mask_sample]
        if highlight_clusters and len(highlight_clusters) > 0:
            hl_set = set(str(c) for c in highlight_clusters)
            mask_hl = df_s["Cluster"].astype(str).isin(hl_set)
            mask_bg_s = ~mask_hl
            if mask_bg_s.any():
                fig.add_trace(go.Scattergl(
                    x=df_s.loc[mask_bg_s, "UMAP_1"],
                    y=df_s.loc[mask_bg_s, "UMAP_2"],
                    mode="markers",
                    marker=dict(size=2, color=HIGHLIGHT_GRAY, opacity=0.3),
                    name="Other", showlegend=False, hoverinfo="skip",
                ))
            for cl in highlight_clusters:
                mask_cl = df_s["Cluster"].astype(str) == str(cl)
                if mask_cl.any():
                    fig.add_trace(go.Scattergl(
                        x=df_s.loc[mask_cl, "UMAP_1"],
                        y=df_s.loc[mask_cl, "UMAP_2"],
                        mode="markers",
                        marker=dict(size=3, color=color_map.get(str(cl), "#999999")),
                        name=f"Cluster {cl}", showlegend=False,
                    ))
        else:
            for cl in sorted(df_s["Cluster"].unique(), key=_cluster_sort_key):
                mask_cl = df_s["Cluster"] == cl
                fig.add_trace(go.Scattergl(
                    x=df_s.loc[mask_cl, "UMAP_1"],
                    y=df_s.loc[mask_cl, "UMAP_2"],
                    mode="markers",
                    marker=dict(size=2, color=color_map.get(str(cl), "#999999")),
                    name=str(cl), showlegend=False,
                ))

        if show_labels:
            centroids = df_s.groupby("Cluster").agg(
                cx=("UMAP_1", "mean"), cy=("UMAP_2", "mean"),
            ).reset_index()
            for _, row in centroids.iterrows():
                fig.add_annotation(
                    x=row["cx"], y=row["cy"],
                    text=str(row["Cluster"]),
                    showarrow=False,
                    font=dict(size=11, color="black", family="Arial Black"),
                    bgcolor="rgba(255,255,255,0.7)", borderpad=1,
                )

        fig.update_layout(
            margin=dict(l=30, r=10, t=10, b=30),
            xaxis_title="", yaxis_title="",
            template="plotly_white", showlegend=False,
        )

        cfg = dict(_UMAP_PER_SAMPLE_CONFIG)
        cfg["toImageButtonOptions"] = dict(cfg["toImageButtonOptions"],
                                           filename=f"UMAP_{s}")
        graphs.append(
            html.Div(
                style={"flex": "1 1 46%", "minWidth": "280px", "maxWidth": "50%",
                        "border": "1px solid #dee2e6", "borderRadius": "6px",
                        "padding": "5px", "backgroundColor": "#fff"},
                children=[
                    html.H6(s, className="text-center mb-1",
                             style={"fontSize": "0.85rem", "fontWeight": "600"}),
                    dcc.Graph(figure=fig, style={"height": graph_height}, config=cfg),
                ],
            )
        )
    return graphs


# ---------------------------------------------------------------------------
# UMAP プロット — コールバック
# ---------------------------------------------------------------------------

@callback(
    Output("interactive_umap_plot", "figure"),
    [Input("umap_color_by", "value"),
     Input("umap_highlight_cluster", "value"),
     Input("umap_show_legend", "value"),
     Input("umap_show_labels", "value"),
     Input("umap_display_mode", "value")],
    State("seurat_rds_path_store", "data"),
)
def update_umap_plot(color_by, highlight_clusters, show_legend, show_labels,
                     display_mode, rds_path):
    if display_mode == "per_sample":
        return go.Figure()
    df = _interactive_data.get("plot_data")
    if df is None:
        return go.Figure()
    return _build_umap_integrated_fig(df, color_by, highlight_clusters,
                                       show_legend, show_labels)


# ---------------------------------------------------------------------------
# UMAP 統合/サンプル別 表示切替
# ---------------------------------------------------------------------------

@callback(
    Output("umap_integrated_wrapper", "style"),
    Input("umap_display_mode", "value"),
)
def toggle_umap_integrated_visibility(mode):
    """「統合」選択時のみ統合UMAPを表示、「サンプル別」では非表示"""
    if mode == "per_sample":
        return {"display": "none"}
    return {"display": "block"}


# ---------------------------------------------------------------------------
# サンプル別 UMAP 表示
# ---------------------------------------------------------------------------

_UMAP_PER_SAMPLE_CONFIG = {
    "scrollZoom": True,
    "toImageButtonOptions": {"format": "png", "scale": 3},
}


@callback(
    Output("umap_per_sample_container", "children"),
    [Input("umap_display_mode", "value"),
     Input("umap_highlight_cluster", "value"),
     Input("umap_show_labels", "value")],
    State("seurat_rds_path_store", "data"),
)
def update_umap_per_sample(display_mode, highlight_clusters, show_labels, rds_path):
    """表示モード「サンプル別」の場合、各サンプルのUMAPを並列表示"""
    if display_mode != "per_sample":
        return ""
    df = _interactive_data.get("plot_data")
    if df is None:
        return ""
    color_map = _get_cluster_color_map(df["Cluster"])
    graphs = _build_umap_per_sample_graphs(df, color_map, highlight_clusters,
                                            show_labels, graph_height="300px")
    return html.Div(
        style={"display": "flex", "flexWrap": "wrap", "gap": "15px", "marginTop": "10px"},
        children=graphs,
    )


# ---------------------------------------------------------------------------
# クラスタ凡例パネル（UMAP / Spatial 共通）
# ---------------------------------------------------------------------------

@callback(
    Output("umap_cluster_legend_panel", "children"),
    Input("seurat_rds_path_store", "data"),
)
def update_umap_cluster_legend(rds_path):
    df = _interactive_data.get("plot_data")
    if df is None:
        return ""
    color_map = _get_cluster_color_map(df["Cluster"])
    return _build_cluster_legend(color_map)


@callback(
    Output("spatial_cluster_legend_panel", "children"),
    Input("seurat_rds_path_store", "data"),
)
def update_spatial_cluster_legend(rds_path):
    df = _interactive_data.get("plot_data")
    if df is None:
        return ""
    color_map = _get_cluster_color_map(df["Cluster"])
    return _build_cluster_legend(color_map)


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

def _transform_coords(x, y, angle_deg, flip_h=False, flip_v=False):
    """中心基準で座標を反転+2D回転"""
    cx, cy = x.mean(), y.mean()
    # 反転（回転の前に適用）
    if flip_h:
        x = 2 * cx - x
    if flip_v:
        y = 2 * cy - y
    # 回転
    if angle_deg == 0:
        return x, y
    # 反転後の中心を再計算
    cx, cy = x.mean(), y.mean()
    rad = np.radians(angle_deg)
    cos_a, sin_a = np.cos(rad), np.sin(rad)
    x_rot = cos_a * (x - cx) - sin_a * (y - cy) + cx
    y_rot = sin_a * (x - cx) + cos_a * (y - cy) + cy
    return x_rot, y_rot


def _create_single_spatial_fig(df_sample, color_map, highlight_clusters,
                               selected_cell_ids, rotation_deg=0,
                               show_labels=False, flip_h=False, flip_v=False,
                               title=None, embed_legend=False):
    """単一サンプルのSpatial Mapping figureを生成"""
    fig = go.Figure()

    # 座標の取得と変換適用（反転+回転）
    raw_x = df_sample["SpatialX"].values
    raw_y = -df_sample["SpatialY"].values  # Y軸反転
    plot_x, plot_y = _transform_coords(raw_x, raw_y, rotation_deg,
                                        flip_h=flip_h, flip_v=flip_v)

    if selected_cell_ids:
        mask_selected = df_sample["CellID"].isin(selected_cell_ids).values
        mask_bg = ~mask_selected
        if mask_bg.any():
            if "TotalCount" in df_sample.columns:
                tc_values = df_sample["TotalCount"].values[mask_bg]
                bg_marker = dict(size=3, color=tc_values, colorscale="Greys",
                                 opacity=0.5, showscale=False)
                bg_name = "TIC"
            else:
                bg_marker = dict(size=3, color=HIGHLIGHT_GRAY, opacity=0.2)
                bg_name = "Other"
            fig.add_trace(go.Scattergl(
                x=plot_x[mask_bg],
                y=plot_y[mask_bg],
                mode="markers",
                marker=bg_marker,
                name=bg_name, showlegend=False, hoverinfo="skip",
            ))
        if mask_selected.any():
            fig.add_trace(go.Scattergl(
                x=plot_x[mask_selected],
                y=plot_y[mask_selected],
                mode="markers",
                marker=dict(size=5, color="red"),
                name=f"Selected ({mask_selected.sum()})",
            ))
    elif highlight_clusters and len(highlight_clusters) > 0:
        highlight_set = set(str(c) for c in highlight_clusters)
        # 非ハイライトクラスタをTIC or 灰色で描画
        mask_bg = ~df_sample["Cluster"].astype(str).isin(highlight_set)
        if mask_bg.values.any():
            if "TotalCount" in df_sample.columns:
                tc_values = df_sample["TotalCount"].values[mask_bg.values]
                bg_marker = dict(size=3, color=tc_values, colorscale="Greys",
                                 opacity=0.5, showscale=False)
                bg_name = "TIC"
            else:
                bg_marker = dict(size=3, color=HIGHLIGHT_GRAY, opacity=0.2)
                bg_name = "Other"
            fig.add_trace(go.Scattergl(
                x=plot_x[mask_bg.values],
                y=plot_y[mask_bg.values],
                mode="markers",
                marker=bg_marker,
                name=bg_name, showlegend=False, hoverinfo="skip",
            ))
        # ハイライトクラスタを色付きで描画
        for cl in sorted(highlight_clusters, key=lambda x: _cluster_sort_key(x), reverse=True):
            mask = (df_sample["Cluster"].astype(str) == str(cl)).values
            if mask.any():
                fig.add_trace(go.Scattergl(
                    x=plot_x[mask],
                    y=plot_y[mask],
                    mode="markers",
                    marker=dict(size=5, color=color_map.get(str(cl), "#999999")),
                    name=f"Cluster {cl}",
                ))
    else:
        # 全ポイントに個別色を割り当て（単一トレース - WebGL重なり問題を回避）
        point_colors = [color_map.get(str(cl), "#999999") for cl in df_sample["Cluster"]]
        fig.add_trace(go.Scattergl(
            x=plot_x,
            y=plot_y,
            mode="markers",
            marker=dict(size=4, color=point_colors),
            text=[f"Cluster {cl}" for cl in df_sample["Cluster"]],
            hovertemplate="%{text}<extra></extra>",
            showlegend=False,
        ))
        # 凡例用ダミートレース（大きいマーカーで見やすく）
        if embed_legend:
            for cl in sorted(df_sample["Cluster"].unique(), key=_cluster_sort_key):
                rank = _cluster_sort_key(cl)[0] if str(cl).isdigit() else 1000
                fig.add_trace(go.Scattergl(
                    x=[None], y=[None],
                    mode="markers",
                    marker=dict(size=10, color=color_map.get(str(cl), "#999999")),
                    name=f"Cluster {cl}",
                    showlegend=True,
                    legendrank=rank,
                ))

    # クラスタ番号ラベル
    if show_labels:
        for cl in sorted(df_sample["Cluster"].unique(), key=_cluster_sort_key):
            mask = (df_sample["Cluster"] == cl).values
            if mask.any():
                cx = plot_x[mask].mean()
                cy = plot_y[mask].mean()
                fig.add_annotation(
                    x=cx, y=cy, text=str(cl),
                    showarrow=False,
                    font=dict(size=10, color="black"),
                    bgcolor="rgba(255,255,255,0.7)",
                )

    layout_opts = dict(
        yaxis=dict(scaleanchor="x"),
        margin=dict(l=10, r=10, t=30 if title else 10, b=10),
        template="plotly_white",
        showlegend=embed_legend,
        legend=dict(itemsizing="constant", font=dict(size=12), tracegroupgap=2),
    )
    if title:
        layout_opts["title"] = dict(text=title, font=dict(size=14), x=0.5)
    fig.update_layout(**layout_opts)
    return fig


_SPATIAL_IMG_CONFIG = {
    "scrollZoom": True,
    "toImageButtonOptions": {
        "format": "png", "scale": 3,
    },
}


# ---------------------------------------------------------------------------
# Spatial サンプル別コントロール生成
# ---------------------------------------------------------------------------

@callback(
    Output("spatial_controls_container", "children"),
    Input("seurat_rds_path_store", "data"),
    State("spatial_rotation_store", "data"),
)
def create_spatial_controls(rds_path, rotation_store):
    """データ読み込み後、サンプル別の回転/反転コントロールを生成"""
    df = _interactive_data.get("plot_data")
    if df is None or "SpatialX" not in df.columns:
        return ""
    if not rotation_store:
        rotation_store = {}

    samples = sorted(df["Sample"].unique())
    controls = []
    for s in samples:
        transform = rotation_store.get(
            s, rotation_store.get("__all__", {"angle": 0, "flip_h": False, "flip_v": False}))
        if isinstance(transform, (int, float)):
            transform = {"angle": int(transform), "flip_h": False, "flip_v": False}

        controls.append(
            html.Div(
                style={"padding": "4px 8px"},
                children=[
                    dcc.Slider(
                        id={"type": "per_sample_rotation", "index": s},
                        min=0, max=270, step=90,
                        value=transform.get("angle", 0),
                        marks={0: "0°", 90: "90°", 180: "180°", 270: "270°"},
                    ),
                    html.Div(className="d-flex gap-2 justify-content-center", children=[
                        dbc.Checkbox(
                            id={"type": "per_sample_flip_h", "index": s},
                            label="↔ 左右", value=transform.get("flip_h", False),
                        ),
                        dbc.Checkbox(
                            id={"type": "per_sample_flip_v", "index": s},
                            label="↕ 上下", value=transform.get("flip_v", False),
                        ),
                    ]),
                ],
            )
        )
    accordion_items = []
    for s, ctrl in zip(samples, controls):
        accordion_items.append(
            dbc.AccordionItem(title=f"回転/反転: {s}", children=[ctrl])
        )
    return dbc.Accordion(
        accordion_items,
        start_collapsed=True,
        flush=True,
        always_open=True,
        style={"marginBottom": "8px"},
    )


# ---------------------------------------------------------------------------
# Spatial 回転 Store 管理 (パターンマッチング)
# ---------------------------------------------------------------------------

@callback(
    Output("spatial_rotation_store", "data"),
    [Input({"type": "per_sample_rotation", "index": ALL}, "value"),
     Input({"type": "per_sample_flip_h", "index": ALL}, "value"),
     Input({"type": "per_sample_flip_v", "index": ALL}, "value")],
    State("spatial_rotation_store", "data"),
    prevent_initial_call=True,
)
def update_rotation_store_from_per_sample(rotations, flip_hs, flip_vs, current_store):
    """各プロットのコントロール変更時に Store を更新"""
    if current_store is None:
        current_store = {}

    triggered = ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        return no_update

    sample_name = triggered["index"]

    # 現在のサンプルの設定を取得
    transform = current_store.get(sample_name, {"angle": 0, "flip_h": False, "flip_v": False})
    if isinstance(transform, (int, float)):
        transform = {"angle": int(transform), "flip_h": False, "flip_v": False}

    # ctx.inputs_list から現在の全値を取得してトリガーサンプルの値を特定
    all_inputs = ctx.inputs_list
    rotation_inputs = all_inputs[0]
    flip_h_inputs = all_inputs[1]
    flip_v_inputs = all_inputs[2]

    for r_input in rotation_inputs:
        if r_input.get("id", {}).get("index") == sample_name:
            transform["angle"] = r_input.get("value", 0) or 0
    for fh_input in flip_h_inputs:
        if fh_input.get("id", {}).get("index") == sample_name:
            transform["flip_h"] = bool(fh_input.get("value", False))
    for fv_input in flip_v_inputs:
        if fv_input.get("id", {}).get("index") == sample_name:
            transform["flip_v"] = bool(fv_input.get("value", False))

    current_store[sample_name] = transform
    return current_store


@callback(
    [Output("spatial_plots_container", "children"),
     Output("last_spatial_figure_store", "data")],
    [Input("interactive_sample", "value"),
     Input("umap_highlight_cluster", "value"),
     Input("interactive_umap_plot", "selectedData"),
     Input("spatial_rotation_store", "data"),
     Input("spatial_show_labels", "value")],
    State("seurat_rds_path_store", "data"),
)
def update_spatial_plots(sample, highlight_clusters, selected_data,
                         rotation_store, show_labels, rds_path):
    df = _interactive_data.get("plot_data")
    if df is None or "SpatialX" not in df.columns:
        return html.Div("空間座標データがありません", className="text-muted p-3"), None

    if not rotation_store:
        rotation_store = {}

    # UMAP選択セルID
    selected_cell_ids = set()
    if selected_data and selected_data.get("points"):
        for pt in selected_data["points"]:
            if pt.get("text"):
                selected_cell_ids.add(pt["text"])

    color_map = _get_cluster_color_map(df["Cluster"])

    # 表示対象サンプル
    if sample:
        samples_to_show = [sample]
    else:
        samples_to_show = sorted(df["Sample"].unique())

    graphs = []
    representative_fig = None
    for s in samples_to_show:
        df_s = df[df["Sample"] == s]
        # サンプル別の変換設定を取得
        transform = rotation_store.get(s, rotation_store.get("__all__", {"angle": 0, "flip_h": False, "flip_v": False}))
        # 後方互換: 旧形式(int)の場合
        if isinstance(transform, (int, float)):
            transform = {"angle": int(transform), "flip_h": False, "flip_v": False}
        rotation_deg = transform.get("angle", 0)
        flip_h = transform.get("flip_h", False)
        flip_v = transform.get("flip_v", False)
        fig = _create_single_spatial_fig(df_s, color_map, highlight_clusters,
                                         selected_cell_ids,
                                         rotation_deg=rotation_deg,
                                         show_labels=show_labels,
                                         flip_h=flip_h, flip_v=flip_v,
                                         title=s, embed_legend=True)
        if representative_fig is None:
            representative_fig = fig
        cfg = dict(_SPATIAL_IMG_CONFIG)
        cfg["toImageButtonOptions"] = dict(cfg["toImageButtonOptions"],
                                           filename=f"Spatial_{s}")
        n_samples = len(samples_to_show)
        flex_basis = f"{max(30, 90 // n_samples)}%"
        graphs.append(
            html.Div(
                style={"flex": f"1 1 {flex_basis}", "minWidth": "300px",
                        "border": "1px solid #dee2e6", "borderRadius": "6px",
                        "padding": "5px", "backgroundColor": "#fff"},
                children=[
                    html.H6(s, className="text-center mb-1",
                             style={"fontSize": "0.85rem", "fontWeight": "600"}),
                    dcc.Graph(figure=fig, style={"height": "350px"}, config=cfg),
                ],
            )
        )

    container = html.Div(
        style={"display": "flex", "flexWrap": "wrap", "gap": "15px"},
        children=graphs,
    )
    # 代表figureをStoreに保存（HTMLエクスポート用）
    store_data = representative_fig.to_dict() if representative_fig else None
    return container, store_data


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
     State("last_spatial_figure_store", "data"),
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


# ---------------------------------------------------------------------------
# フルスクリーン拡大モーダル
# ---------------------------------------------------------------------------

@callback(
    [Output("fullscreen_plot_modal", "is_open"),
     Output("fullscreen_modal_title", "children"),
     Output("fullscreen_modal_body", "children")],
    [Input("expand_umap_btn", "n_clicks"),
     Input("expand_feature_btn", "n_clicks"),
     Input("expand_spatial_btn", "n_clicks"),
     Input("expand_deg_btn", "n_clicks")],
    [State("interactive_umap_plot", "figure"),
     State("feature_plot", "figure"),
     State("last_spatial_figure_store", "data"),
     State("deg_data_store", "data"),
     State("spatial_rotation_store", "data")],
    prevent_initial_call=True,
)
def toggle_fullscreen(umap_n, feat_n, spatial_n, deg_n,
                      umap_fig, feat_fig, spatial_fig_data, deg_data,
                      rotation_store):
    trigger = ctx.triggered_id
    if not trigger:
        return False, "", ""

    fs_graph_style = {"height": "80vh"}
    fs_config = {
        "scrollZoom": True,
        "toImageButtonOptions": {"format": "png", "scale": 3},
    }

    # ===== UMAP (インタラクティブ) =====
    if trigger == "expand_umap_btn":
        df = _interactive_data.get("plot_data")
        if df is None:
            return False, "", ""

        clusters = sorted(df["Cluster"].unique(), key=_cluster_sort_key)
        cluster_opts = [{"label": f"Cluster {c}", "value": str(c)} for c in clusters]
        color_map = _get_cluster_color_map(df["Cluster"])

        # タイトル（RDSファイル名から生成）
        rds_path = _interactive_data.get("rds_path", "")
        umap_title = Path(rds_path).stem if rds_path else "UMAP"

        # 初期グラフ（統合モード）
        init_fig = _build_umap_integrated_fig(df, "Cluster", None, True, False,
                                               title=umap_title)
        init_fs_config = dict(fs_config)
        init_fs_config["toImageButtonOptions"] = dict(init_fs_config["toImageButtonOptions"],
                                                       filename=f"UMAP_{umap_title}")
        init_graph = dcc.Graph(figure=init_fig, style={"height": "78vh"}, config=init_fs_config)

        body = html.Div([
            dbc.Row(className="mb-2 align-items-center", children=[
                dbc.Col(width=2, children=[
                    dbc.RadioItems(id="fs_umap_display_mode",
                                   options=[{"label": "統合", "value": "integrated"},
                                            {"label": "サンプル別", "value": "per_sample"}],
                                   value="integrated", inline=True),
                ]),
                dbc.Col(width=2, children=[
                    dbc.RadioItems(id="fs_umap_color_by",
                                   options=[{"label": "Cluster", "value": "Cluster"},
                                            {"label": "Sample", "value": "Sample"}],
                                   value="Cluster", inline=True),
                ]),
                dbc.Col(width=3, children=[
                    dcc.Dropdown(id="fs_umap_highlight_cluster",
                                 options=cluster_opts, multi=True,
                                 placeholder="ハイライト"),
                ]),
                dbc.Col(width=2, children=[
                    dbc.Checkbox(id="fs_umap_show_labels", label="ラベル", value=False),
                ]),
                dbc.Col(width=2, children=[
                    dbc.Checkbox(id="fs_umap_show_legend", label="凡例", value=True),
                ]),
            ]),
            html.Div(
                style={"display": "flex", "gap": "10px"},
                children=[
                    html.Div(id="fs_umap_graph_container", style={"flex": "1"},
                             children=[init_graph]),
                    html.Div(id="fs_umap_legend",
                             style={"width": "140px", "flexShrink": "0",
                                    "borderLeft": "1px solid #dee2e6", "paddingLeft": "8px"},
                             children=_build_cluster_legend(color_map)),
                ],
            ),
        ])
        return True, "UMAP", body

    # ===== Feature Plot (静的) =====
    if trigger == "expand_feature_btn" and feat_fig:
        fig = go.Figure(feat_fig)
        fig.update_layout(height=None)
        return (
            True, "Feature Plot",
            dcc.Graph(figure=fig, style=fs_graph_style, config=fs_config),
        )

    # ===== Spatial Mapping (インタラクティブ) =====
    if trigger == "expand_spatial_btn":
        df = _interactive_data.get("plot_data")
        if df is None or "SpatialX" not in df.columns:
            return False, "", ""

        samples = sorted(df["Sample"].unique())
        sample_opts = [{"label": s, "value": s} for s in samples]
        clusters = sorted(df["Cluster"].unique(), key=_cluster_sort_key)
        cluster_opts = [{"label": f"Cluster {c}", "value": str(c)} for c in clusters]
        color_map = _get_cluster_color_map(df["Cluster"])

        # 初期グラフ（全サンプル、rotation_store適用）
        if not rotation_store:
            rotation_store = {}
        init_graphs = []
        for s in samples:
            df_s = df[df["Sample"] == s]
            transform = rotation_store.get(
                s, rotation_store.get("__all__", {"angle": 0, "flip_h": False, "flip_v": False}))
            if isinstance(transform, (int, float)):
                transform = {"angle": int(transform), "flip_h": False, "flip_v": False}
            fig = _create_single_spatial_fig(df_s, color_map, None, set(),
                                             rotation_deg=transform.get("angle", 0),
                                             flip_h=transform.get("flip_h", False),
                                             flip_v=transform.get("flip_v", False),
                                             title=s, embed_legend=True)
            n = len(samples)
            flex_basis = f"{max(30, 90 // n)}%"
            init_cfg = dict(fs_config)
            init_cfg["toImageButtonOptions"] = dict(init_cfg["toImageButtonOptions"],
                                                     filename=f"Spatial_{s}")
            init_graphs.append(
                html.Div(
                    style={"flex": f"1 1 {flex_basis}", "minWidth": "350px",
                            "border": "1px solid #dee2e6", "borderRadius": "6px",
                            "padding": "5px", "backgroundColor": "#fff"},
                    children=[
                        html.H6(s, className="text-center mb-1",
                                 style={"fontWeight": "600"}),
                        dcc.Graph(figure=fig, style={"height": "60vh"}, config=init_cfg),
                    ],
                )
            )
        init_container = html.Div(
            style={"display": "flex", "flexWrap": "wrap", "gap": "15px"},
            children=init_graphs,
        )

        # フルスクリーン用サンプル別コントロール（Accordion）
        fs_accordion_items = []
        for s in samples:
            t = rotation_store.get(
                s, rotation_store.get("__all__", {"angle": 0, "flip_h": False, "flip_v": False}))
            if isinstance(t, (int, float)):
                t = {"angle": int(t), "flip_h": False, "flip_v": False}
            fs_accordion_items.append(
                dbc.AccordionItem(
                    title=f"回転/反転: {s}",
                    children=[html.Div(
                        style={"padding": "4px 8px"},
                        children=[
                            dcc.Slider(
                                id={"type": "per_sample_rotation", "index": s},
                                min=0, max=270, step=90,
                                value=t.get("angle", 0),
                                marks={0: "0°", 90: "90°", 180: "180°", 270: "270°"},
                            ),
                            html.Div(className="d-flex gap-2 justify-content-center", children=[
                                dbc.Checkbox(
                                    id={"type": "per_sample_flip_h", "index": s},
                                    label="↔ 左右", value=t.get("flip_h", False),
                                ),
                                dbc.Checkbox(
                                    id={"type": "per_sample_flip_v", "index": s},
                                    label="↕ 上下", value=t.get("flip_v", False),
                                ),
                            ]),
                        ],
                    )],
                )
            )

        body = html.Div([
            dbc.Row(className="mb-2 align-items-center", children=[
                dbc.Col(width=2, children=[
                    dcc.Dropdown(id="fs_spatial_sample", options=sample_opts,
                                 placeholder="サンプル(空=全表示)", clearable=True),
                ]),
                dbc.Col(width=1, children=[
                    dbc.Checkbox(id="fs_spatial_show_labels", label="番号", value=False),
                ]),
                dbc.Col(width=4, children=[
                    dcc.Dropdown(id="fs_spatial_highlight_cluster",
                                 options=cluster_opts, multi=True,
                                 placeholder="ハイライト"),
                ]),
            ]),
            dbc.Accordion(
                fs_accordion_items, start_collapsed=True,
                flush=True, always_open=True,
                style={"marginBottom": "8px"},
            ),
            html.Div(
                style={"display": "flex", "gap": "10px"},
                children=[
                    html.Div(id="fs_spatial_graph_container", style={"flex": "1"},
                             children=[init_container]),
                    html.Div(id="fs_spatial_legend",
                             style={"width": "140px", "flexShrink": "0",
                                    "borderLeft": "1px solid #dee2e6", "paddingLeft": "8px"},
                             children=_build_cluster_legend(color_map)),
                ],
            ),
        ])
        return True, "Spatial Mapping", body

    # ===== DEG テーブル =====
    if trigger == "expand_deg_btn" and deg_data:
        table = dash_table.DataTable(
            columns=[
                {"name": "Gene/m/z", "id": "gene"},
                {"name": "Cluster", "id": "cluster"},
                {"name": "avg_log2FC", "id": "avg_log2FC"},
                {"name": "p_val_adj", "id": "p_val_adj"},
                {"name": "pct.1", "id": "pct.1"},
                {"name": "pct.2", "id": "pct.2"},
            ],
            data=deg_data,
            sort_action="native",
            filter_action="native",
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "left", "padding": "6px", "fontSize": "0.85rem"},
            style_header={"backgroundColor": "#f8f9fa", "fontWeight": "600"},
            page_size=50,
        )
        return True, "DEG マーカー", table

    return False, "", ""


# ---------------------------------------------------------------------------
# フルスクリーン UMAP インタラクティブ更新
# ---------------------------------------------------------------------------

@callback(
    Output("fs_umap_graph_container", "children"),
    [Input("fs_umap_display_mode", "value"),
     Input("fs_umap_color_by", "value"),
     Input("fs_umap_highlight_cluster", "value"),
     Input("fs_umap_show_labels", "value"),
     Input("fs_umap_show_legend", "value")],
    prevent_initial_call=True,
)
def update_fs_umap(display_mode, color_by, highlight, show_labels, show_legend):
    df = _interactive_data.get("plot_data")
    if df is None:
        return ""
    color_map = _get_cluster_color_map(df["Cluster"])
    fs_config = {"scrollZoom": True, "toImageButtonOptions": {"format": "png", "scale": 3}}

    # タイトル（RDSファイル名から生成）
    rds_path = _interactive_data.get("rds_path", "")
    umap_title = Path(rds_path).stem if rds_path else "UMAP"

    if display_mode == "integrated":
        fig = _build_umap_integrated_fig(df, color_by, highlight, show_legend, show_labels,
                                          title=umap_title)
        fs_cfg = dict(fs_config)
        fs_cfg["toImageButtonOptions"] = dict(fs_cfg["toImageButtonOptions"],
                                               filename=f"UMAP_{umap_title}")
        return dcc.Graph(figure=fig, style={"height": "78vh"}, config=fs_cfg)
    else:
        graphs = _build_umap_per_sample_graphs(df, color_map, highlight,
                                                show_labels, graph_height="35vh")
        return html.Div(
            style={"display": "flex", "flexWrap": "wrap", "gap": "15px"},
            children=graphs,
        )


# ---------------------------------------------------------------------------
# フルスクリーン Spatial インタラクティブ更新
# ---------------------------------------------------------------------------

@callback(
    Output("fs_spatial_graph_container", "children"),
    [Input("fs_spatial_sample", "value"),
     Input("spatial_rotation_store", "data"),
     Input("fs_spatial_show_labels", "value"),
     Input("fs_spatial_highlight_cluster", "value")],
    prevent_initial_call=True,
)
def update_fs_spatial(sample, rotation_store, show_labels, highlight):
    df = _interactive_data.get("plot_data")
    if df is None or "SpatialX" not in df.columns:
        return ""
    color_map = _get_cluster_color_map(df["Cluster"])
    if not rotation_store:
        rotation_store = {}

    if sample:
        samples_to_show = [sample]
    else:
        samples_to_show = sorted(df["Sample"].unique())

    fs_config = {"scrollZoom": True, "toImageButtonOptions": {"format": "png", "scale": 3}}
    graphs = []
    for s in samples_to_show:
        df_s = df[df["Sample"] == s]
        transform = rotation_store.get(
            s, rotation_store.get("__all__", {"angle": 0, "flip_h": False, "flip_v": False}))
        if isinstance(transform, (int, float)):
            transform = {"angle": int(transform), "flip_h": False, "flip_v": False}
        fig = _create_single_spatial_fig(df_s, color_map, highlight, set(),
                                         rotation_deg=transform.get("angle", 0),
                                         show_labels=show_labels,
                                         flip_h=transform.get("flip_h", False),
                                         flip_v=transform.get("flip_v", False),
                                         title=s, embed_legend=True)
        n = len(samples_to_show)
        flex_basis = f"{max(30, 90 // n)}%"
        fs_cfg = dict(fs_config)
        fs_cfg["toImageButtonOptions"] = dict(fs_cfg["toImageButtonOptions"],
                                               filename=f"Spatial_{s}")
        graphs.append(
            html.Div(
                style={"flex": f"1 1 {flex_basis}", "minWidth": "350px",
                        "border": "1px solid #dee2e6", "borderRadius": "6px",
                        "padding": "5px", "backgroundColor": "#fff"},
                children=[
                    html.H6(s, className="text-center mb-1", style={"fontWeight": "600"}),
                    dcc.Graph(figure=fig, style={"height": "60vh"}, config=fs_cfg),
                ],
            )
        )
    return html.Div(
        style={"display": "flex", "flexWrap": "wrap", "gap": "15px"},
        children=graphs,
    )
