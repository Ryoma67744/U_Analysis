# =============================================================================
# MSI Analysis Application - Interactive Analysis Callbacks
# インタラクティブ解析 コールバック
# =============================================================================

import io
import json
import re
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


def _get_label_positions_path():
    """label_positions.json のパスを返す（RDSと同ディレクトリ）"""
    rds_path = _interactive_data.get("rds_path")
    if not rds_path:
        return None
    return Path(rds_path).parent / "label_positions.json"


def _load_label_positions():
    """label_positions.json を読み込んで dict を返す。ファイルなし or エラー時は空dict"""
    path = _get_label_positions_path()
    if path and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _extract_annotation_positions(relayout_data):
    """relayoutDataからアノテーション位置を抽出 → {インデックス文字列: {"x": v, "y": v}}"""
    if not relayout_data:
        return {}
    positions = {}
    for key, val in relayout_data.items():
        if key.startswith("annotations["):
            m = re.match(r"annotations\[(\d+)\]\.([xy])", key)
            if m:
                idx = m.group(1)
                attr = m.group(2)
                if idx not in positions:
                    positions[idx] = {}
                positions[idx][attr] = val
    return positions


def _get_cluster_colorscale(clusters):
    """Scattergl用: 数値インデックスベースのcolorscale情報を返す。

    HEX文字列配列をmarker.colorに渡すとWebGL内部処理で色ミスマッチが
    生じるため、数値+colorscaleで確実に色を指定する。

    Returns:
        cluster_to_idx: dict[str, int] — クラスタ文字列→0-based数値インデックス
        discrete_colorscale: list — Plotly colorscale形式
    """
    str_cls = list(set(str(c) for c in clusters))
    str_cls.sort(key=_cluster_sort_key)
    n = max(len(str_cls), 1)
    cluster_to_idx = {cl: i for i, cl in enumerate(str_cls)}

    # discrete colorscale: 各色が均等な範囲を占める
    colorscale = []
    for i, cl in enumerate(str_cls):
        low = i / n
        high = (i + 1) / n
        color = DESI_COLORS_50[i % len(DESI_COLORS_50)]
        colorscale.append([low, color])
        colorscale.append([high, color])

    return cluster_to_idx, colorscale


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

    from app.services.data_manager import list_msi_files, list_tims_files
    samples = list_msi_files(folder_path)
    # .txtが見つからない場合はTIMS形式(.parquet等)を試行
    if not samples:
        samples = list_tims_files(folder_path)
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
     Output("deg_results_section", "style"),
     Output("spatial_exclude_cluster", "options"),
     Output("spatial_highlight_cluster", "options"),
     Output("umap_exclude_cluster", "options")],
    [Input("load_interactive_data", "n_clicks"),
     Input("interactive_integration_method", "value"),
     Input("interactive_rds_map", "data")],
    [State("interactive_result_folder", "value")],
    prevent_initial_call=True,
)
def load_interactive_data(n_clicks, integration_method, rds_map, result_folder):
    _n_out = 12
    if not integration_method or not rds_map:
        return (
            "統合手法を選択してください（結果フォルダをスキャンしてください）",
            {"display": "none"}, [], [], [], None, None, None,
            {"display": "none"}, [], [], [],
        )

    rds_path = rds_map.get(integration_method)
    if not rds_path or not Path(rds_path).exists():
        return (
            f"RDSファイルが見つかりません: {integration_method}",
            {"display": "none"}, [], [], [], None, None, None,
            {"display": "none"}, [], [], [],
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
            cluster_options,  # spatial_exclude_cluster用
            cluster_options,  # spatial_highlight_cluster用
            cluster_options,  # umap_exclude_cluster用
        )

    except Exception as e:
        return (
            f"読み込みエラー: {e}",
            {"display": "none"}, [], [], [], None, None, None,
            {"display": "none"}, [], [], [],
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
                            # Volcano Plot用に元の数値を保持
                            df["p_val_adj_raw"] = df[col].copy()
                            # テーブル表示用に科学記数法文字列へ変換
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
                                show_legend, show_labels, title=None,
                                marker_size=2, exclude_clusters=None,
                                label_size=14, saved_positions=None):
    """統合UMAPのgo.Figureを生成（メイン/フルスクリーン共用）"""
    fig = go.Figure()

    # 除外クラスタのフィルタリング
    if exclude_clusters:
        exclude_set = set(str(c) for c in exclude_clusters)
        df = df[~df["Cluster"].astype(str).isin(exclude_set)]
        if df.empty:
            fig.add_annotation(text="全クラスタが除外されています", showarrow=False,
                               xref="paper", yref="paper", x=0.5, y=0.5)
            return fig

    color_map = _get_cluster_color_map(df["Cluster"])

    if highlight_clusters and len(highlight_clusters) > 0:
        highlight_set = set(str(c) for c in highlight_clusters)
        mask_bg = ~df["Cluster"].astype(str).isin(highlight_set)
        if mask_bg.any():
            fig.add_trace(go.Scattergl(
                x=df.loc[mask_bg, "UMAP_1"],
                y=df.loc[mask_bg, "UMAP_2"],
                mode="markers",
                marker=dict(size=max(1, marker_size - 1), color=HIGHLIGHT_GRAY, opacity=0.1),
                name="Other", showlegend=False, hoverinfo="skip",
            ))
        for cl in highlight_clusters:
            mask = df["Cluster"].astype(str) == str(cl)
            if mask.any():
                fig.add_trace(go.Scattergl(
                    x=df.loc[mask, "UMAP_1"],
                    y=df.loc[mask, "UMAP_2"],
                    mode="markers",
                    marker=dict(size=marker_size + 1, color=color_map.get(str(cl), "#999999")),
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
            rank = _cluster_sort_key(cat)[0] if str(cat).isdigit() else 1000
            fig.add_trace(go.Scattergl(
                x=df.loc[mask, "UMAP_1"],
                y=df.loc[mask, "UMAP_2"],
                mode="markers",
                marker=dict(size=marker_size, color=cat_color_map.get(str(cat), "#999999")),
                name=f"Cluster {cat}",
                legendrank=rank,
                text=df.loc[mask, "CellID"],
                hovertemplate=f"{color_col}: {cat}<br>" + "%{text}<extra></extra>",
            ))

    if show_labels:
        centroids = df.groupby("Cluster").agg(
            cx=("UMAP_1", "mean"), cy=("UMAP_2", "mean"),
        ).reset_index()
        for _, row in centroids.iterrows():
            cl_str = str(row["Cluster"])
            pos = (saved_positions or {}).get(cl_str, {})
            fig.add_annotation(
                x=pos.get("x", row["cx"]),
                y=pos.get("y", row["cy"]),
                text=cl_str,
                showarrow=False,
                font=dict(size=label_size, color="black", family="Arial Black"),
                bgcolor="rgba(255,255,255,0.7)", borderpad=2,
            )

    layout_opts = dict(
        dragmode="select",
        showlegend=bool(show_legend),
        legend=dict(itemsizing="constant", font=dict(size=12), tracegroupgap=2),
        margin=dict(l=40, r=10, t=40 if title else 30, b=40),
        xaxis_title="UMAP_1", yaxis_title="UMAP_2",
        template="plotly_white",
    )
    if title:
        layout_opts["title"] = dict(text=title, font=dict(size=14), x=0.5)
    fig.update_layout(**layout_opts)
    return fig


def _build_umap_per_sample_graphs(df, color_map, highlight_clusters,
                                   show_labels, graph_height="300px",
                                   marker_size=2, exclude_clusters=None,
                                   label_size=11, saved_positions=None):
    """サンプル別UMAPのhtml.Divリストを生成（メイン/フルスクリーン共用）"""
    # 除外クラスタのフィルタリング
    if exclude_clusters:
        exclude_set = set(str(c) for c in exclude_clusters)
        df = df[~df["Cluster"].astype(str).isin(exclude_set)]
        if df.empty:
            return [html.Div("全クラスタが除外されています", className="text-muted small mt-2")]

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
                marker=dict(size=max(1, marker_size - 1), color=HIGHLIGHT_GRAY, opacity=0.1),
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
                    marker=dict(size=marker_size, color=HIGHLIGHT_GRAY, opacity=0.3),
                    name="Other", showlegend=False, hoverinfo="skip",
                ))
            for cl in highlight_clusters:
                mask_cl = df_s["Cluster"].astype(str) == str(cl)
                if mask_cl.any():
                    fig.add_trace(go.Scattergl(
                        x=df_s.loc[mask_cl, "UMAP_1"],
                        y=df_s.loc[mask_cl, "UMAP_2"],
                        mode="markers",
                        marker=dict(size=marker_size + 1, color=color_map.get(str(cl), "#999999")),
                        name=f"Cluster {cl}", showlegend=False,
                    ))
        else:
            for cl in sorted(df_s["Cluster"].unique(), key=_cluster_sort_key):
                mask_cl = df_s["Cluster"] == cl
                fig.add_trace(go.Scattergl(
                    x=df_s.loc[mask_cl, "UMAP_1"],
                    y=df_s.loc[mask_cl, "UMAP_2"],
                    mode="markers",
                    marker=dict(size=marker_size, color=color_map.get(str(cl), "#999999")),
                    name=str(cl), showlegend=False,
                ))

        if show_labels:
            sample_pos = (saved_positions or {}).get(s, {})
            centroids = df_s.groupby("Cluster").agg(
                cx=("UMAP_1", "mean"), cy=("UMAP_2", "mean"),
            ).reset_index()
            for _, row in centroids.iterrows():
                cl_str = str(row["Cluster"])
                pos = sample_pos.get(cl_str, {})
                fig.add_annotation(
                    x=pos.get("x", row["cx"]),
                    y=pos.get("y", row["cy"]),
                    text=cl_str,
                    showarrow=False,
                    font=dict(size=label_size, color="black", family="Arial Black"),
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
                    dcc.Graph(id={"type": "umap_per_sample_graph", "index": s},
                              figure=fig, style={"height": graph_height}, config=cfg),
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
     Input("umap_display_mode", "value"),
     Input("umap_marker_size", "value"),
     Input("umap_exclude_cluster", "value"),
     Input("umap_label_size", "value"),
     Input("seurat_rds_path_store", "data")],
)
def update_umap_plot(color_by, highlight_clusters, show_legend, show_labels,
                     display_mode, marker_size, exclude_clusters, label_size, rds_path):
    if display_mode == "per_sample":
        return go.Figure()
    df = _interactive_data.get("plot_data")
    if df is None:
        return go.Figure()
    all_pos = _load_label_positions()
    return _build_umap_integrated_fig(df, color_by, highlight_clusters,
                                       show_legend, show_labels,
                                       marker_size=marker_size or 2,
                                       exclude_clusters=exclude_clusters,
                                       label_size=label_size or 14,
                                       saved_positions=all_pos.get("umap_integrated"))


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
    "edits": {"annotationPosition": True},
    "toImageButtonOptions": {"format": "png", "scale": 3},
}


@callback(
    Output("umap_per_sample_container", "children"),
    [Input("umap_display_mode", "value"),
     Input("umap_highlight_cluster", "value"),
     Input("umap_show_labels", "value"),
     Input("umap_marker_size", "value"),
     Input("umap_exclude_cluster", "value"),
     Input("umap_label_size", "value"),
     Input("seurat_rds_path_store", "data")],
)
def update_umap_per_sample(display_mode, highlight_clusters, show_labels,
                            marker_size, exclude_clusters, label_size, rds_path):
    """表示モード「サンプル別」の場合、各サンプルのUMAPを並列表示"""
    if display_mode != "per_sample":
        return ""
    df = _interactive_data.get("plot_data")
    if df is None:
        return ""
    color_map = _get_cluster_color_map(df["Cluster"])
    all_pos = _load_label_positions()
    graphs = _build_umap_per_sample_graphs(df, color_map, highlight_clusters,
                                            show_labels, graph_height="300px",
                                            marker_size=marker_size or 2,
                                            exclude_clusters=exclude_clusters,
                                            label_size=label_size or 11,
                                            saved_positions=all_pos.get("umap_per_sample"))
    return html.Div(
        style={"display": "flex", "flexWrap": "wrap", "gap": "15px", "marginTop": "10px"},
        children=graphs,
    )


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
                               title=None, embed_legend=False,
                               cluster_to_idx=None, discrete_cscale=None,
                               marker_size=4, exclude_clusters=None,
                               label_size=10, saved_positions=None):
    """単一サンプルのSpatial Mapping figureを生成"""
    # 除外クラスタのフィルタリング
    if exclude_clusters:
        exclude_set = set(str(c) for c in exclude_clusters)
        df_sample = df_sample[~df_sample["Cluster"].astype(str).isin(exclude_set)]
        if df_sample.empty:
            fig = go.Figure()
            fig.add_annotation(text="全クラスタが除外されています", showarrow=False,
                               xref="paper", yref="paper", x=0.5, y=0.5)
            return fig
    fig = go.Figure()

    # 座標の取得と変換適用（反転+回転）
    raw_x = df_sample["SpatialX"].values
    raw_y = -df_sample["SpatialY"].values  # Y軸反転
    plot_x, plot_y = _transform_coords(raw_x, raw_y, rotation_deg,
                                        flip_h=flip_h, flip_v=flip_v)

    # marker_size=0 の場合、データ密度ベースで自動計算（点間距離ゼロ）
    if marker_size <= 0 and len(plot_x) > 1:
        sorted_ux = np.sort(np.unique(plot_x))
        if len(sorted_ux) > 1:
            min_spacing = float(np.min(np.diff(sorted_ux)))
            x_range = float(plot_x.max() - plot_x.min())
            y_range = float(plot_y.max() - plot_y.min()) if len(plot_y) > 1 else 1.0
            # scaleanchor="x" のため、描画幅は高さ×アスペクト比で決定
            # コンテナ高さ350px - margin(t=30+b=10)=40px → 有効高さ310px
            effective_h = 310
            if y_range > 0 and x_range > 0:
                effective_w = effective_h * (x_range / y_range)
                marker_size = max(2, round(min_spacing * effective_w / x_range * 1.3))
            else:
                marker_size = 4
        else:
            marker_size = 4
    elif marker_size <= 0:
        marker_size = 4

    if selected_cell_ids:
        mask_selected = df_sample["CellID"].isin(selected_cell_ids).values
        mask_bg = ~mask_selected
        if mask_bg.any():
            if "TotalCount" in df_sample.columns:
                tc_values = df_sample["TotalCount"].values[mask_bg]
                bg_marker = dict(size=marker_size, color=tc_values, colorscale="Greys",
                                 opacity=0.5, showscale=False)
                bg_name = "TIC"
            else:
                bg_marker = dict(size=marker_size, color=HIGHLIGHT_GRAY, opacity=0.2)
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
                marker=dict(size=marker_size + 1, color="red"),
                name=f"Selected ({mask_selected.sum()})",
            ))
    elif highlight_clusters and len(highlight_clusters) > 0:
        highlight_set = set(str(c) for c in highlight_clusters)
        # 非ハイライトクラスタをTIC or 灰色で描画
        mask_bg = ~df_sample["Cluster"].astype(str).isin(highlight_set)
        if mask_bg.values.any():
            if "TotalCount" in df_sample.columns:
                tc_values = df_sample["TotalCount"].values[mask_bg.values]
                bg_marker = dict(size=marker_size, color=tc_values, colorscale="Greys",
                                 opacity=0.5, showscale=False)
                bg_name = "TIC"
            else:
                bg_marker = dict(size=marker_size, color=HIGHLIGHT_GRAY, opacity=0.2)
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
                    marker=dict(size=marker_size + 1, color=color_map.get(str(cl), "#999999")),
                    name=f"Cluster {cl}",
                ))
    else:
        # 数値インデックス + discrete colorscale 方式
        # （HEX文字列配列のWebGL処理問題を回避）
        if cluster_to_idx is not None and discrete_cscale is not None:
            n_clusters = max(len(cluster_to_idx), 1)
            point_values = np.array(
                [cluster_to_idx.get(str(cl), 0) for cl in df_sample["Cluster"]]
            )
            point_normalized = (point_values + 0.5) / n_clusters
            fig.add_trace(go.Scattergl(
                x=plot_x, y=plot_y, mode="markers",
                marker=dict(
                    size=marker_size,
                    color=point_normalized,
                    colorscale=discrete_cscale,
                    cmin=0, cmax=1,
                    showscale=False,
                ),
                text=[f"Cluster {cl}" for cl in df_sample["Cluster"]],
                hovertemplate="%{text}<extra></extra>",
                showlegend=False,
            ))
        else:
            # フォールバック: HEX文字列配列方式
            point_colors = [color_map.get(str(cl), "#999999") for cl in df_sample["Cluster"]]
            fig.add_trace(go.Scattergl(
                x=plot_x, y=plot_y, mode="markers",
                marker=dict(size=marker_size, color=point_colors),
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
                cx_default = plot_x[mask].mean()
                cy_default = plot_y[mask].mean()
                cl_str = str(cl)
                pos = (saved_positions or {}).get(cl_str, {})
                fig.add_annotation(
                    x=pos.get("x", cx_default),
                    y=pos.get("y", cy_default),
                    text=cl_str,
                    showarrow=False,
                    font=dict(size=label_size, color="black"),
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
    "edits": {"annotationPosition": True},
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
     Input("spatial_highlight_cluster", "value"),
     Input("interactive_umap_plot", "selectedData"),
     Input("spatial_rotation_store", "data"),
     Input("spatial_show_labels", "value"),
     Input("spatial_marker_size", "value"),
     Input("spatial_exclude_cluster", "value"),
     Input("spatial_label_size", "value"),
     Input("seurat_rds_path_store", "data")],
)
def update_spatial_plots(sample, highlight_clusters, selected_data,
                         rotation_store, show_labels, marker_size,
                         exclude_clusters, label_size, rds_path):
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
    cluster_to_idx, discrete_cscale = _get_cluster_colorscale(df["Cluster"])
    all_pos = _load_label_positions()
    spatial_pos = all_pos.get("spatial", {})

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
                                         title=s, embed_legend=True,
                                         cluster_to_idx=cluster_to_idx,
                                         discrete_cscale=discrete_cscale,
                                         marker_size=marker_size or 0,
                                         exclude_clusters=exclude_clusters,
                                         label_size=label_size or 10,
                                         saved_positions=spatial_pos.get(s))
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
                    dcc.Graph(id={"type": "spatial_graph", "index": s},
                              figure=fig, style={"height": "350px"}, config=cfg),
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
        "edits": {"annotationPosition": True},
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
                dbc.Col(width=2, children=[
                    dcc.Dropdown(id="fs_umap_highlight_cluster",
                                 options=cluster_opts, multi=True,
                                 placeholder="ハイライト"),
                ]),
                dbc.Col(width=3, children=[
                    dcc.Dropdown(id="fs_umap_exclude_cluster",
                                 options=cluster_opts, multi=True,
                                 placeholder="除去するクラスタ"),
                ]),
                dbc.Col(width=1, children=[
                    dbc.Checkbox(id="fs_umap_show_labels", label="ラベル", value=False),
                ]),
                dbc.Col(width=1, children=[
                    dbc.Checkbox(id="fs_umap_show_legend", label="凡例", value=True),
                ]),
            ]),
            dbc.Row(className="mt-1 align-items-center", children=[
                dbc.Col(width=2, children=[
                    dbc.Label("点サイズ", className="small mb-0"),
                    dcc.Slider(
                        id="fs_umap_marker_size",
                        min=1, max=10, step=1, value=2,
                        marks={1: "1", 5: "5", 10: "10"},
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                ]),
                dbc.Col(width=2, children=[
                    dbc.Label("ラベルサイズ", className="small mb-0"),
                    dcc.Slider(
                        id="fs_umap_label_size",
                        min=6, max=24, step=1, value=14,
                        marks={6: "6", 14: "14", 24: "24"},
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                ]),
                dbc.Col(width=2, children=[
                    dbc.Label("高さ", className="small mb-0"),
                    dcc.Slider(
                        id="fs_umap_height_slider",
                        min=40, max=95, step=5, value=78,
                        marks={40: "40", 78: "78", 95: "95"},
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                ]),
                dbc.Col(width=2, children=[
                    dbc.Label("横幅", className="small mb-0"),
                    dcc.Slider(
                        id="fs_umap_width_slider",
                        min=40, max=100, step=5, value=95,
                        marks={40: "40", 70: "70", 95: "95"},
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                ]),
                dbc.Col(width=2, className="d-flex align-items-end", children=[
                    dbc.Button("ラベル位置保存", id="fs_save_label_pos_btn",
                               size="sm", color="secondary", className="mb-1"),
                ]),
            ]),
            html.Div(id="fs_umap_graph_container", children=[init_graph]),
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
        cluster_to_idx, discrete_cscale = _get_cluster_colorscale(df["Cluster"])

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
                                             title=s, embed_legend=True,
                                             cluster_to_idx=cluster_to_idx,
                                             discrete_cscale=discrete_cscale)
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
                dbc.Col(width=2, children=[
                    dcc.Dropdown(id="fs_spatial_highlight_cluster",
                                 options=cluster_opts, multi=True,
                                 placeholder="ハイライト"),
                ]),
                dbc.Col(width=2, children=[
                    dcc.Dropdown(id="fs_spatial_exclude_cluster",
                                 options=cluster_opts, multi=True,
                                 placeholder="除去"),
                ]),
                dbc.Col(width=1, children=[
                    dbc.Checkbox(id="fs_spatial_show_labels", label="番号", value=False),
                ]),
            ]),
            dbc.Row(className="mt-1 align-items-center", children=[
                dbc.Col(width=2, children=[
                    dbc.Label("マーカー", className="small mb-0"),
                    dcc.Slider(
                        id="fs_spatial_marker_size",
                        min=0, max=15, step=1, value=0,
                        marks={0: "自動", 8: "8", 15: "15"},
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                ]),
                dbc.Col(width=2, children=[
                    dbc.Label("ラベル", className="small mb-0"),
                    dcc.Slider(
                        id="fs_spatial_label_size",
                        min=6, max=24, step=1, value=10,
                        marks={6: "6", 14: "14", 24: "24"},
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                ]),
                dbc.Col(width=2, children=[
                    dbc.Label("高さ", className="small mb-0"),
                    dcc.Slider(
                        id="fs_spatial_height_slider",
                        min=30, max=85, step=5, value=60,
                        marks={30: "30", 60: "60", 85: "85"},
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                ]),
                dbc.Col(width=2, children=[
                    dbc.Label("横幅", className="small mb-0"),
                    dcc.Slider(
                        id="fs_spatial_width_slider",
                        min=40, max=100, step=5, value=95,
                        marks={40: "40", 70: "70", 95: "95"},
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                ]),
                dbc.Col(width=2, className="d-flex align-items-end", children=[
                    dbc.Button("ラベル位置保存", id="fs_save_spatial_label_pos_btn",
                               size="sm", color="secondary", className="mb-1"),
                ]),
            ]),
            dbc.Accordion(
                fs_accordion_items, start_collapsed=True,
                flush=True, always_open=True,
                style={"marginBottom": "8px"},
            ),
            html.Div(id="fs_spatial_graph_container", children=[init_container]),
        ])
        return True, "Spatial Mapping", body

    # ===== DEG テーブル + Volcano + Heatmap =====
    if trigger == "expand_deg_btn" and deg_data:
        # クラスタ選択肢
        deg_clusters = sorted(
            set(str(r.get("cluster", "")) for r in deg_data),
            key=_cluster_sort_key,
        )
        deg_cluster_opts = [{"label": f"Cluster {c}", "value": c} for c in deg_clusters]

        fs_deg_body = dbc.Tabs(active_tab="fs_deg_table_tab", children=[
            dbc.Tab(label="テーブル", tab_id="fs_deg_table_tab", children=[
                dash_table.DataTable(
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
                ),
            ]),
            dbc.Tab(label="Volcano Plot", tab_id="fs_deg_volcano_tab", children=[
                html.P("メイン画面のVolcano Plotタブで操作してください。",
                       className="text-muted small mt-2"),
            ]),
            dbc.Tab(label="Heatmap", tab_id="fs_deg_heatmap_tab", children=[
                html.P("メイン画面のHeatmapタブで操作してください。",
                       className="text-muted small mt-2"),
            ]),
        ])
        return True, "DEG マーカー", fs_deg_body

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
     Input("fs_umap_show_legend", "value"),
     Input("fs_umap_height_slider", "value"),
     Input("fs_umap_width_slider", "value"),
     Input("fs_umap_marker_size", "value"),
     Input("fs_umap_exclude_cluster", "value"),
     Input("fs_umap_label_size", "value")],
    prevent_initial_call=True,
)
def update_fs_umap(display_mode, color_by, highlight, show_labels, show_legend,
                   height_val, width_val, marker_size, exclude_clusters, label_size):
    height_val = height_val or 78
    width_val = width_val or 95
    df = _interactive_data.get("plot_data")
    if df is None:
        return ""
    color_map = _get_cluster_color_map(df["Cluster"])
    fs_config = {"scrollZoom": True, "edits": {"annotationPosition": True}, "toImageButtonOptions": {"format": "png", "scale": 3}}
    all_pos = _load_label_positions()

    # タイトル（RDSファイル名から生成）
    rds_path = _interactive_data.get("rds_path", "")
    umap_title = Path(rds_path).stem if rds_path else "UMAP"

    if display_mode == "integrated":
        fig = _build_umap_integrated_fig(df, color_by, highlight, show_legend, show_labels,
                                          title=umap_title,
                                          marker_size=marker_size or 2,
                                          exclude_clusters=exclude_clusters,
                                          label_size=label_size or 14,
                                          saved_positions=all_pos.get("umap_integrated"))
        fs_cfg = dict(fs_config)
        fs_cfg["toImageButtonOptions"] = dict(fs_cfg["toImageButtonOptions"],
                                               filename=f"UMAP_{umap_title}")
        return html.Div(
            style={"width": f"{width_val}vw", "margin": "0 auto"},
            children=[dcc.Graph(figure=fig, style={"height": f"{height_val}vh"}, config=fs_cfg)],
        )
    else:
        per_h = max(height_val // 2, 25)
        graphs = _build_umap_per_sample_graphs(df, color_map, highlight,
                                                show_labels, graph_height=f"{per_h}vh",
                                                marker_size=marker_size or 2,
                                                exclude_clusters=exclude_clusters,
                                                label_size=label_size or 11,
                                                saved_positions=all_pos.get("umap_per_sample"))
        return html.Div(
            style={"display": "flex", "flexWrap": "wrap", "gap": "15px",
                   "width": f"{width_val}vw", "margin": "0 auto"},
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
     Input("fs_spatial_highlight_cluster", "value"),
     Input("fs_spatial_exclude_cluster", "value"),
     Input("fs_spatial_marker_size", "value"),
     Input("fs_spatial_height_slider", "value"),
     Input("fs_spatial_width_slider", "value"),
     Input("fs_spatial_label_size", "value")],
    prevent_initial_call=True,
)
def update_fs_spatial(sample, rotation_store, show_labels, highlight,
                      exclude_clusters, marker_size, height_val, width_val,
                      label_size):
    height_val = height_val or 60
    width_val = width_val or 95
    df = _interactive_data.get("plot_data")
    if df is None or "SpatialX" not in df.columns:
        return ""
    color_map = _get_cluster_color_map(df["Cluster"])
    cluster_to_idx, discrete_cscale = _get_cluster_colorscale(df["Cluster"])
    all_pos = _load_label_positions()
    spatial_pos = all_pos.get("spatial", {})
    if not rotation_store:
        rotation_store = {}

    if sample:
        samples_to_show = [sample]
    else:
        samples_to_show = sorted(df["Sample"].unique())

    fs_config = {"scrollZoom": True, "edits": {"annotationPosition": True}, "toImageButtonOptions": {"format": "png", "scale": 3}}
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
                                         title=s, embed_legend=True,
                                         cluster_to_idx=cluster_to_idx,
                                         discrete_cscale=discrete_cscale,
                                         marker_size=marker_size or 0,
                                         exclude_clusters=exclude_clusters,
                                         label_size=label_size or 10,
                                         saved_positions=spatial_pos.get(s))
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
                    dcc.Graph(id={"type": "fs_spatial_graph", "index": s},
                              figure=fig, style={"height": f"{height_val}vh"}, config=fs_cfg),
                ],
            )
        )
    return html.Div(
        style={"display": "flex", "flexWrap": "wrap", "gap": "15px",
               "width": f"{width_val}vw", "margin": "0 auto"},
        children=graphs,
    )



# ---------------------------------------------------------------------------
# ラベル位置の永続保存
# ---------------------------------------------------------------------------

@callback(
    Output("label_pos_save_status", "data"),
    [Input("save_label_pos_btn", "n_clicks"),
     Input("save_spatial_label_pos_btn", "n_clicks"),
     Input("fs_save_label_pos_btn", "n_clicks"),
     Input("fs_save_spatial_label_pos_btn", "n_clicks")],
    [State("interactive_umap_plot", "relayoutData"),
     State({"type": "umap_per_sample_graph", "index": ALL}, "relayoutData"),
     State({"type": "spatial_graph", "index": ALL}, "relayoutData"),
     State({"type": "fs_spatial_graph", "index": ALL}, "relayoutData")],
    prevent_initial_call=True,
)
def save_label_positions(n1, n2, n3, n4, umap_relayout, umap_ps_relayouts,
                         spatial_relayouts, fs_spatial_relayouts):
    """全グラフのアノテーション位置をJSONファイルに永続保存する"""
    path = _get_label_positions_path()
    if not path:
        return no_update

    df = _interactive_data.get("plot_data")
    if df is None:
        return no_update

    clusters = sorted(df["Cluster"].unique(), key=_cluster_sort_key)

    # 既存データを読み込み（マージ）
    existing = _load_label_positions()

    # 1) UMAP統合
    umap_pos = _extract_annotation_positions(umap_relayout)
    if umap_pos:
        umap_saved = existing.get("umap_integrated", {})
        for idx_str, pos in umap_pos.items():
            idx = int(idx_str)
            if idx < len(clusters):
                cl = str(clusters[idx])
                if cl not in umap_saved:
                    umap_saved[cl] = {}
                umap_saved[cl].update(pos)
        existing["umap_integrated"] = umap_saved

    # 2) サンプル別UMAP
    if umap_ps_relayouts:
        umap_ps_saved = existing.get("umap_per_sample", {})
        inputs_list = ctx.inputs_list[5]  # State index 5 (Input 4つ + State[0]=umap, [1]=per_sample)
        for i, rd in enumerate(umap_ps_relayouts):
            if i < len(inputs_list):
                sample_name = inputs_list[i]["id"]["index"]
                ps_pos = _extract_annotation_positions(rd)
                if ps_pos:
                    sample_saved = umap_ps_saved.get(sample_name, {})
                    sample_clusters = sorted(
                        df[df["Sample"] == sample_name]["Cluster"].unique(),
                        key=_cluster_sort_key)
                    for idx_str, pos in ps_pos.items():
                        idx = int(idx_str)
                        if idx < len(sample_clusters):
                            cl = str(sample_clusters[idx])
                            if cl not in sample_saved:
                                sample_saved[cl] = {}
                            sample_saved[cl].update(pos)
                    umap_ps_saved[sample_name] = sample_saved
        existing["umap_per_sample"] = umap_ps_saved

    # 3) Spatial (通常 + FS)
    all_spatial_relayouts = (spatial_relayouts or []) + (fs_spatial_relayouts or [])
    spatial_inputs = (ctx.inputs_list[6] if len(ctx.inputs_list) > 6 else []) + \
                     (ctx.inputs_list[7] if len(ctx.inputs_list) > 7 else [])
    if all_spatial_relayouts:
        spatial_saved = existing.get("spatial", {})
        inputs_list = spatial_inputs
        for i, rd in enumerate(all_spatial_relayouts):
            if i < len(inputs_list):
                sample_name = inputs_list[i]["id"]["index"]
                sp_pos = _extract_annotation_positions(rd)
                if sp_pos:
                    sample_saved = spatial_saved.get(sample_name, {})
                    sample_clusters = sorted(
                        df[df["Sample"] == sample_name]["Cluster"].unique(),
                        key=_cluster_sort_key)
                    for idx_str, pos in sp_pos.items():
                        idx = int(idx_str)
                        if idx < len(sample_clusters):
                            cl = str(sample_clusters[idx])
                            if cl not in sample_saved:
                                sample_saved[cl] = {}
                            sample_saved[cl].update(pos)
                    spatial_saved[sample_name] = sample_saved
        existing["spatial"] = spatial_saved

    # JSON書き込み
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False),
                    encoding="utf-8")

    return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Volcano Plot（DEG インタラクティブ可視化）
# ---------------------------------------------------------------------------

@callback(
    Output("volcano_cluster_select", "options"),
    Input("deg_data_store", "data"),
    prevent_initial_call=True,
)
def update_volcano_cluster_options(deg_data):
    """DEGデータからVolcano Plotのクラスタ選択肢を生成"""
    if not deg_data:
        return []
    clusters = sorted(
        set(str(r.get("cluster", "")) for r in deg_data),
        key=_cluster_sort_key,
    )
    return [{"label": f"Cluster {c}", "value": c} for c in clusters]


@callback(
    Output("volcano_plot", "figure"),
    [Input("volcano_cluster_select", "value"),
     Input("volcano_fc_threshold", "value"),
     Input("volcano_p_threshold", "value"),
     Input("volcano_y_max", "value"),
     Input("volcano_marker_size", "value")],
    State("deg_data_store", "data"),
    prevent_initial_call=True,
)
def update_volcano_plot(cluster, fc_thresh, p_thresh, y_max, marker_size, deg_data):
    """DEGデータからVolcano Plotを生成"""
    if not deg_data:
        return go.Figure()

    df = pd.DataFrame(deg_data)
    # p_val_adj_raw があればそちらを使用（文字列変換前の精度を保持）
    if "p_val_adj_raw" in df.columns:
        df["p_num"] = pd.to_numeric(df["p_val_adj_raw"], errors="coerce")
    else:
        df["p_num"] = pd.to_numeric(df["p_val_adj"], errors="coerce")
    # p=0 は 1e-50 にclip（-log10 = 50 に集約、300集中を回避）
    df["neg_log10_p"] = -np.log10(df["p_num"].clip(lower=1e-50))
    df["avg_log2FC"] = pd.to_numeric(df["avg_log2FC"], errors="coerce")

    if cluster:
        df = df[df["cluster"].astype(str) == str(cluster)]

    fc_thresh = fc_thresh or 0.5
    p_thresh = p_thresh or 1.3
    marker_size = marker_size or 8

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
            mask = ~(
                (df["neg_log10_p"] >= p_thresh)
                & (df["avg_log2FC"].abs() >= fc_thresh)
            )
        sub = df[mask]
        if len(sub) > 0:
            fig.add_trace(go.Scattergl(
                x=sub["avg_log2FC"],
                y=sub["neg_log10_p"],
                mode="markers",
                marker=dict(size=marker_size, color=color, opacity=0.7),
                name=label,
                text=sub["gene"],
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "log2FC: %{x:.3f}<br>"
                    "-log10(p): %{y:.2f}<extra></extra>"
                ),
            ))

    # 閾値ライン
    fig.add_hline(y=p_thresh, line_dash="dash", line_color="gray", opacity=0.5)
    fig.add_vline(x=fc_thresh, line_dash="dash", line_color="gray", opacity=0.5)
    fig.add_vline(x=-fc_thresh, line_dash="dash", line_color="gray", opacity=0.5)

    title = (f"Volcano Plot - Cluster {cluster}" if cluster
             else "Volcano Plot (全クラスタ)")
    yaxis_opts = {}
    if y_max is not None and y_max > 0:
        yaxis_opts["range"] = [0, y_max]
    fig.update_layout(
        title=dict(text=title, font=dict(size=14), x=0.5),
        xaxis_title="avg_log2FC",
        yaxis_title="-log10(p_val_adj)",
        yaxis=yaxis_opts,
        template="plotly_white",
        margin=dict(l=50, r=20, t=40, b=40),
    )
    return fig


# ---------------------------------------------------------------------------
# Heatmap（DEG Top N マーカーのクラスタ別平均発現量）
# ---------------------------------------------------------------------------

def _build_mz_to_compound_map(mrm_path_str, tolerance=0.1):
    """MRMファイルから m/z値 → 化合物名 のマッピング辞書を構築する。

    Parent m/z と Daughter m/z の両方を対象にマッチングを行う。
    Returns:
        dict[float, str] — m/z数値 → 化合物名
    """
    if not mrm_path_str:
        return {}
    from app.services.data_manager import load_mrm_file
    mrm_df = load_mrm_file(mrm_path_str)
    if mrm_df is None or mrm_df.empty:
        return {}

    # カラム名の正規化（R側と同様のロジック）
    col_map = {}
    for col in mrm_df.columns:
        cl = col.lower().replace(" ", ".").replace("_", ".")
        if cl in ("compound", "name", "metabolite", "metabolite.name",
                  "analyte", "analyte.name"):
            col_map[col] = "Compound"
        elif cl in ("parent.m.z", "parent.mz", "parent", "precursor",
                    "q1", "q1.m.z", "precursor.m.z", "precursor.mz"):
            col_map[col] = "Parent_mz"
        elif cl in ("daughter.m.z", "daughter.mz", "daughter", "product",
                    "q3", "q3.m.z", "product.m.z", "product.mz"):
            col_map[col] = "Daughter_mz"
    mrm_df = mrm_df.rename(columns=col_map)

    if "Compound" not in mrm_df.columns:
        return {}

    # m/z → 化合物名 マッピング (Parent と Daughter 両方)
    mz_map = {}
    for _, row in mrm_df.iterrows():
        name = str(row.get("Compound", "")).strip()
        if not name:
            continue
        for mz_col in ("Parent_mz", "Daughter_mz"):
            if mz_col in mrm_df.columns:
                try:
                    mz_val = float(row[mz_col])
                    mz_map[mz_val] = name
                except (ValueError, TypeError):
                    continue
    return mz_map


def _annotate_gene_labels(gene_list, mz_to_compound, tolerance=0.1):
    """遺伝子/m/zラベルリストに化合物名を付与して返す。

    Returns:
        list[str] — アノテーション済みラベル（例: "mz_123.456 (Compound A)"）
    """
    if not mz_to_compound:
        return gene_list

    mrm_mz_values = np.array(sorted(mz_to_compound.keys()))
    annotated = []
    for g in gene_list:
        label = g
        # m/z数値を抽出（"mz_123.456" や "123.456" 形式に対応）
        import re
        match = re.search(r"(\d+\.\d+)", str(g))
        if match and len(mrm_mz_values) > 0:
            mz_val = float(match.group(1))
            # 最近傍マッチ
            idx = np.argmin(np.abs(mrm_mz_values - mz_val))
            if abs(mrm_mz_values[idx] - mz_val) <= tolerance:
                compound = mz_to_compound[mrm_mz_values[idx]]
                label = f"{g} ({compound})"
        annotated.append(label)
    return annotated


@callback(
    Output("heatmap_plot", "figure"),
    [Input("heatmap_top_n", "value"),
     Input("heatmap_scale", "value"),
     Input("heatmap_annotation_switch", "value")],
    [State("deg_data_store", "data"),
     State("seurat_cache_dir_store", "data"),
     State("mrm_path", "value")],
    prevent_initial_call=True,
)
def update_heatmap(top_n, scale, annotation_on, deg_data, cache_dir_str, mrm_path_str):
    """DEG Top N マーカーのクラスタ別平均発現量ヒートマップを生成"""
    if not deg_data or not cache_dir_str:
        return go.Figure()

    top_n = top_n or 5
    df_deg = pd.DataFrame(deg_data)
    df_deg["p_num"] = pd.to_numeric(df_deg["p_val_adj"], errors="coerce")

    # 各クラスタの Top N マーカーを抽出
    top_markers = df_deg.sort_values("p_num").groupby("cluster").head(top_n)
    genes = top_markers["gene"].unique().tolist()

    if not genes:
        fig = go.Figure()
        fig.add_annotation(
            text="マーカーが見つかりません", showarrow=False,
            xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    # expression_matrix.parquet から発現量取得
    cache_dir = Path(cache_dir_str)
    expr_path = cache_dir / "expression_matrix.parquet"
    if not expr_path.exists():
        fig = go.Figure()
        fig.add_annotation(
            text="発現量データがありません", showarrow=False,
            xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    # 利用可能な遺伝子のみ読み込み
    available = []
    for g in genes:
        try:
            pd.read_parquet(expr_path, columns=[g])
            available.append(g)
        except Exception:
            continue

    if not available:
        fig = go.Figure()
        fig.add_annotation(
            text="発現量カラムが見つかりません", showarrow=False,
            xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    expr_df = pd.read_parquet(expr_path, columns=["CellID"] + available)

    # クラスタ情報を結合
    plot_data = _interactive_data.get("plot_data")
    if plot_data is None:
        return go.Figure()
    merged = expr_df.merge(
        plot_data[["CellID", "Cluster"]], on="CellID", how="inner"
    )

    # クラスタ別平均発現量
    cluster_means = merged.groupby("Cluster")[available].mean()
    cluster_means = cluster_means.reindex(
        sorted(cluster_means.index, key=_cluster_sort_key)
    )

    # Z-score 変換（scipy なしで手動計算）
    z_data = cluster_means.values.copy()
    if scale == "zscore":
        col_mean = z_data.mean(axis=0)
        col_std = z_data.std(axis=0)
        col_std[col_std == 0] = 1
        z_data = (z_data - col_mean) / col_std

    # Y軸ラベル: アノテーションがONかつMRMファイルがある場合は化合物名を付与
    y_labels = available
    if annotation_on and mrm_path_str:
        mz_to_compound = _build_mz_to_compound_map(mrm_path_str, tolerance=0.1)
        y_labels = _annotate_gene_labels(available, mz_to_compound, tolerance=0.1)

    fig = go.Figure(go.Heatmap(
        z=z_data.T,
        x=[f"C{c}" for c in cluster_means.index],
        y=y_labels,
        colorscale="RdBu_r",
        zmid=0 if scale == "zscore" else None,
        hovertemplate=(
            "Cluster: %{x}<br>Gene: %{y}<br>"
            "Value: %{z:.3f}<extra></extra>"
        ),
    ))
    # Y軸ラベルの余白を動的に調整
    max_label_len = max(len(str(l)) for l in y_labels) if y_labels else 10
    left_margin = min(max(max_label_len * 7, 120), 350)
    fig.update_layout(
        title=dict(text=f"Top {top_n} DEG Heatmap", font=dict(size=14), x=0.5),
        xaxis_title="Cluster",
        yaxis_title="Gene / m/z",
        template="plotly_white",
        margin=dict(l=left_margin, r=20, t=40, b=40),
        yaxis=dict(autorange="reversed"),
    )
    return fig
