# =============================================================================
# MSI Analysis Application - Share Callbacks
# 共有ページ コールバック（URL共有・読み取り専用ビュー）
# =============================================================================

import base64
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import (
    Input, Output, State, callback, ctx, no_update,
    html, dcc, dash_table, ALL,
)

logger = logging.getLogger(__name__)
from app.services.share_manager import get_share
from app.services.persistent_share_manager import (
    get_persistent_share, increment_view_count as _persistent_increment_view,
)
from app.services.seurat_bridge import SeuratBridge
from app.services.results_viewer import (
    categorize_image, get_available_clusters,
    sort_images_by_time, filter_images_by_cluster,
)

# interactive_callbacks.py のヘルパー関数を直接import
from app.callbacks.interactive_callbacks import (
    _cluster_sort_key,
    _get_cluster_color_map,
    _get_cluster_colorscale,
    _load_deg_results,
)
from app.callbacks.interactive_umap import _build_umap_integrated_fig
from app.callbacks.interactive_spatial import _create_single_spatial_fig
from app.utils.deg_utils import is_meaningful_annotation as _is_meaningful_annotation

# Seuratブリッジ
_sv_bridge = SeuratBridge()

# 共有データキャッシュ: { token: { plot_data, cluster_stats, features_list, meta, ... } }
_shared_data: dict[str, dict] = {}


# =========================================================================
# URL ルーティング
# =========================================================================

@callback(
    [Output("current_page", "data", allow_duplicate=True),
     Output("share_token", "data")],
    Input("url_bar", "pathname"),
    prevent_initial_call=True,
)
def route_share_url(pathname):
    """URL パスが /share/<token> または /view/<token> なら共有ページに遷移。

    /share/<token> は期間付き共有 (Tier B 必須)、/view/<token> は無期限共有
    (認証バイパス)。どちらも内部的には同じ shared_view を使い、トークンの
    解決先 (shares.json vs persistent_shares.json) で挙動を切り替える。
    """
    if not pathname:
        return no_update, no_update
    if pathname.startswith("/share/"):
        token = pathname.split("/share/", 1)[1].split("/")[0].split("?")[0]
        if token:
            return "shared", token
    if pathname.startswith("/view/"):
        token = pathname.split("/view/", 1)[1].split("/")[0].split("?")[0]
        if token:
            # ビュー数をインクリメント (失敗しても致命的でないので無視)
            _persistent_increment_view(token)
            # token 先頭に "v:" prefix を付けて initialize_shared_view 側で
            # 期間付き vs 無期限を判別する
            return "shared", f"v:{token}"
    return no_update, no_update


# =========================================================================
# 共有ページ初期化（トークン検証 → データ読込）
# =========================================================================

@callback(
    [Output("sv_content", "style"),
     Output("sv_share_error", "style"),
     Output("sv_share_error", "children"),
     Output("sv_share_info", "children"),
     Output("sv_metadata_card", "children"),
     Output("sv_result_dir_store", "data"),
     Output("sv_rds_path_store", "data"),
     Output("sv_integration_method_store", "data"),
     Output("sv_data_info", "children"),
     Output("sv_umap_highlight_cluster", "options"),
     Output("sv_spatial_highlight_cluster", "options"),
     Output("sv_spatial_sample", "options"),
     Output("sv_feature_select", "options"),
     Output("sv_cluster_stats_table", "data"),
     Output("sv_cluster_stats_table", "columns"),
     Output("sv_deg_section", "style"),
     Output("sv_deg_data_store", "data"),
     Output("sv_subfolder_selector", "options"),
     Output("sv_cluster_selector", "options")],
    Input("share_token", "data"),
    prevent_initial_call=True,
)
def initialize_shared_view(token):
    """共有トークン変更時にデータをロードし、全UIを初期化"""
    # デフォルト（非表示）
    hide = {"display": "none"}
    show = {}

    if not token:
        return (hide, hide, "", "", "", "", "", "", "",
                [], [], [], [], [], [], hide, None, [], [])

    # トークン検証 (route_share_url が "v:" prefix を付けてくる無期限共有を判別)
    is_persistent = token.startswith("v:")
    actual_token = token[2:] if is_persistent else token

    if is_persistent:
        share = get_persistent_share(actual_token)
        not_found_msg = (
            "このリンクは無効です (無期限共有が失効しているか、"
            "URL が間違っています)。"
        )
    else:
        share = get_share(actual_token)
        not_found_msg = "このリンクは無効か、有効期限が切れています。"

    if not share:
        return (hide, show, not_found_msg, "", "",
                "", "", "", "", [], [], [], [], [], [], hide, None, [], [])

    result_dir = share.get("result_dir", "")
    rds_path = share.get("rds_path", "")
    integration_method = share.get("integration_method", "")
    if is_persistent:
        info_text = (
            f"{share.get('project_name', '')} / "
            f"{share.get('sub_project_name', '')} — 無期限共有"
        )
    else:
        info_text = (
            f"{share.get('project_name', '')} / "
            f"{share.get('sub_project_name', '')} — "
            f"有効期限: {share.get('expires_at', '不明')}"
        )
    share_kind_label = "無期限共有" if is_persistent else "期間付き共有"
    metadata_card = dbc.Card(
        dbc.CardBody(
            dbc.Row([
                dbc.Col([html.Strong("プロジェクト: "),
                         html.Span(share.get("project_name", "—"))], width="auto"),
                dbc.Col([html.Strong("サブプロジェクト: "),
                         html.Span(share.get("sub_project_name", "—"))], width="auto"),
                dbc.Col([html.Strong("統合手法: "),
                         html.Span(integration_method or "—")], width="auto"),
                dbc.Col([html.Strong("共有日時: "),
                         html.Span(share.get("created_at", "—"))], width="auto"),
                dbc.Col([html.Strong("種別: "),
                         html.Span(share_kind_label)], width="auto"),
            ], className="g-3"),
            className="py-2",
        ),
        className="bg-light border-0",
    )

    # --- ギャラリー用: サブフォルダ・クラスタ ---
    subfolder_opts = [{"label": "(ルート)", "value": ""}]
    cluster_opts = [{"label": "すべて", "value": ""}]
    if result_dir and Path(result_dir).is_dir():
        root = Path(result_dir)
        for d in sorted(root.rglob("*")):
            if d.is_dir():
                rel = d.relative_to(root)
                if len(rel.parts) <= 2:
                    subfolder_opts.append({"label": str(rel), "value": str(rel)})
        for c in get_available_clusters(result_dir):
            cluster_opts.append({"label": f"Cluster {c}", "value": str(c)})

    # --- インタラクティブ用: Seurat RDS データ読込 ---
    cluster_options = []
    sample_options = []
    feature_options = []
    stats_data = []
    stats_columns = []
    deg_style = hide
    deg_data = None
    data_info = ""

    if rds_path and Path(rds_path).exists():
        try:
            extracted = _sv_bridge.extract_data(rds_path)
            df_plot = extracted["plot_data"]
            df_stats = extracted["cluster_stats"]
            features = extracted["features_list"]
            meta = extracted["meta"]

            # キャッシュに保存
            _shared_data[token] = {
                "plot_data": df_plot,
                "cluster_stats": df_stats,
                "features_list": features,
                "meta": meta,
                "rds_path": rds_path,
                "cache_dir": str(extracted["cache_dir"]),
            }

            # クラスタオプション
            if df_plot is not None and "Cluster" in df_plot.columns:
                clusters = sorted(df_plot["Cluster"].unique(), key=_cluster_sort_key)
                cluster_options = [{"label": f"Cluster {c}", "value": str(c)} for c in clusters]

            # サンプルオプション
            if df_plot is not None and "Sample" in df_plot.columns:
                samples = sorted(df_plot["Sample"].unique())
                sample_options = [{"label": s, "value": s} for s in samples]

            # フィーチャーオプション（上位100件のみ表示用）
            if features:
                feature_options = [{"label": f, "value": f} for f in features[:100]]

            # クラスタ統計
            if df_stats is not None:
                stats_data = df_stats.to_dict("records")
                stats_columns = [{"name": col, "id": col} for col in df_stats.columns]

            # DEG データ
            if result_dir and Path(result_dir).is_dir():
                deg_records = _load_deg_results(Path(result_dir), integration_method)
                if deg_records:
                    deg_style = show
                    deg_data = deg_records

            # データ情報
            n_cells = len(df_plot) if df_plot is not None else 0
            n_features = len(features) if features else 0
            data_info = f"データ読込完了: {n_cells:,} cells, {n_features:,} features"

        except Exception as e:
            data_info = f"RDS読込エラー: {e}"

    return (
        show,                    # sv_content style
        hide,                    # sv_share_error style
        "",                      # sv_share_error children
        info_text,               # sv_share_info children
        metadata_card,           # sv_metadata_card children
        result_dir,              # sv_result_dir_store
        rds_path,                # sv_rds_path_store
        integration_method,      # sv_integration_method_store
        data_info,               # sv_data_info
        cluster_options,         # sv_umap_highlight_cluster options
        cluster_options,         # sv_spatial_highlight_cluster options
        sample_options,          # sv_spatial_sample options
        feature_options,         # sv_feature_select options
        stats_data,              # sv_cluster_stats_table data
        stats_columns,           # sv_cluster_stats_table columns
        deg_style,               # sv_deg_section style
        deg_data,                # sv_deg_data_store
        subfolder_opts,          # sv_subfolder_selector options
        cluster_opts,            # sv_cluster_selector options
    )


# =========================================================================
# 結果ギャラリー
# =========================================================================

@callback(
    [Output("sv_image_gallery", "children"),
     Output("sv_page_info", "children")],
    [Input("sv_subfolder_selector", "value"),
     Input("sv_image_category", "value"),
     Input("sv_cluster_selector", "value"),
     Input("sv_gallery_page_store", "data")],
    State("sv_result_dir_store", "data"),
)
def sv_render_gallery(subfolder, category, cluster, page, result_dir):
    if not result_dir or not Path(result_dir).is_dir():
        return [html.Div("結果フォルダが設定されていません", className="text-muted p-4")], ""

    target = Path(result_dir)
    if subfolder:
        target = target / subfolder

    extensions = {".png", ".jpg", ".jpeg"}
    images = [str(f) for f in target.rglob("*") if f.suffix.lower() in extensions]

    if category and category != "all":
        images = [img for img in images if categorize_image(img) == category]
    if cluster:
        images = filter_images_by_cluster(images, int(cluster))

    images = sort_images_by_time(images)
    if not images:
        return [html.Div("画像が見つかりません", className="text-muted p-4")], ""

    per_page = 20
    total_pages = max(1, -(-len(images) // per_page))
    current_page = min(page or 1, total_pages)
    start = (current_page - 1) * per_page
    page_images = images[start:start + per_page]

    cards = []
    for img_path in page_images:
        p = Path(img_path)
        try:
            with open(img_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()
            src = f"data:image/png;base64,{img_data}"
        except Exception as e:
            # ver3.7: silent failure を解消。ユーザー画面では空表示のままだが、
            # サーバーログで原因究明できるようにする
            logger.error("image load failed (gallery) path=%s: %s", img_path, e)
            src = ""

        cards.append(
            html.Div(
                className="image-item",
                children=[
                    html.Img(src=src, style={
                        "width": "100%", "height": "180px",
                        "objectFit": "contain", "background": "#f8f9fa"}),
                    html.Div(className="caption", children=p.name, title=str(p)),
                ],
                id={"type": "sv_gallery_image", "path": str(p)},
                n_clicks=0,
            )
        )

    page_info = f"{current_page} / {total_pages} ({len(images)} 枚)"
    return cards, page_info


# ページネーション
@callback(
    Output("sv_gallery_page_store", "data"),
    [Input("sv_prev_page", "n_clicks"),
     Input("sv_next_page", "n_clicks")],
    State("sv_gallery_page_store", "data"),
    prevent_initial_call=True,
)
def sv_handle_pagination(prev_clicks, next_clicks, current_page):
    triggered = ctx.triggered_id
    page = current_page or 1
    if triggered == "sv_prev_page":
        return max(1, page - 1)
    elif triggered == "sv_next_page":
        return page + 1
    return page


# 画像クリック → モーダル
@callback(
    [Output("sv_image_modal", "is_open"),
     Output("sv_modal_body", "children"),
     Output("sv_clicked_image_store", "data")],
    Input({"type": "sv_gallery_image", "path": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def sv_open_image_modal(clicks):
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update, no_update, no_update

    img_path = ctx.triggered_id["path"]
    try:
        with open(img_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode()
        src = f"data:image/png;base64,{img_data}"
    except Exception as e:
        # ver3.7: モーダル画像読込失敗を logger.error に記録
        logger.error("image load failed (modal) path=%s: %s", img_path, e)
        return True, html.Div("画像を読み込めません"), img_path

    return (
        True,
        html.Img(src=src, className="modal-image",
                  style={"maxWidth": "90vw", "maxHeight": "80vh", "objectFit": "contain"}),
        img_path,
    )


# =========================================================================
# UMAP プロット
# =========================================================================

@callback(
    Output("sv_umap_plot", "figure"),
    [Input("sv_umap_color_by", "value"),
     Input("sv_umap_highlight_cluster", "value"),
     Input("sv_umap_show_legend", "value"),
     Input("sv_umap_show_labels", "value"),
     Input("sv_umap_marker_size", "value")],
    State("share_token", "data"),
)
def sv_update_umap(color_by, highlight_clusters, show_legend, show_labels,
                   marker_size, token):
    empty = go.Figure()
    # ver3.7: token check と dict アクセスを 1 段階化し競合状態 (token 削除
    # との race) で KeyError が出ないように修正
    data = _shared_data.get(token) if token else None
    if data is None:
        empty.add_annotation(text="データなし", showarrow=False,
                             xref="paper", yref="paper", x=0.5, y=0.5)
        return empty

    df = data.get("plot_data")
    if df is None or df.empty:
        return empty

    return _build_umap_integrated_fig(
        df, color_by, highlight_clusters or [],
        show_legend, show_labels,
        marker_size=marker_size or 2,
    )


# =========================================================================
# Spatial Mapping
# =========================================================================

@callback(
    Output("sv_spatial_container", "children"),
    [Input("sv_spatial_highlight_cluster", "value"),
     Input("sv_spatial_sample", "value")],
    State("share_token", "data"),
)
def sv_update_spatial(highlight_clusters, sample_filter, token):
    # ver3.7: race 防止のため .get() で 1 段階化
    data = _shared_data.get(token) if token else None
    if data is None:
        return html.Div("データなし", className="text-muted")

    df = data.get("plot_data")
    if df is None or df.empty or "SpatialX" not in df.columns:
        return html.Div("Spatial データなし", className="text-muted")

    color_map = _get_cluster_color_map(df["Cluster"])
    cluster_to_idx, discrete_cscale = _get_cluster_colorscale(df["Cluster"])

    if sample_filter:
        samples = [sample_filter]
    else:
        samples = sorted(df["Sample"].unique())

    graphs = []
    for s in samples:
        df_s = df[df["Sample"] == s]
        if df_s.empty:
            continue
        fig = _create_single_spatial_fig(
            df_s, color_map, highlight_clusters or [],
            selected_cell_ids=None,
            title=s, embed_legend=True,
            cluster_to_idx=cluster_to_idx,
            discrete_cscale=discrete_cscale,
        )
        graphs.append(
            html.Div(
                style={"flex": "1 1 46%", "minWidth": "300px", "maxWidth": "50%",
                        "border": "1px solid #dee2e6", "borderRadius": "6px",
                        "padding": "5px", "backgroundColor": "#fff"},
                children=[
                    dcc.Graph(figure=fig, style={"height": "350px"},
                              config={"scrollZoom": True}),
                ],
            )
        )

    if not graphs:
        return html.Div("表示するサンプルがありません", className="text-muted")

    return html.Div(
        style={"display": "flex", "flexWrap": "wrap", "gap": "10px"},
        children=graphs,
    )


# =========================================================================
# Feature Plot
# =========================================================================

@callback(
    Output("sv_feature_plot", "figure"),
    Input("sv_feature_select", "value"),
    State("share_token", "data"),
)
def sv_update_feature_plot(feature, token):
    empty = go.Figure()
    # ver3.7: race 防止のため .get() で 1 段階化
    data = _shared_data.get(token) if (feature and token) else None
    if data is None:
        empty.add_annotation(text="Feature を選択してください", showarrow=False,
                             xref="paper", yref="paper", x=0.5, y=0.5)
        return empty

    rds_path = data.get("rds_path")
    cache_dir_str = data.get("cache_dir")
    df = data.get("plot_data")
    if not rds_path or df is None:
        return empty

    try:
        # 必要時に expression_matrix.parquet を生成（初回 feature plot で 20-60 秒、以降は即座）
        try:
            _sv_bridge.ensure_expression_matrix(rds_path)
        except Exception:
            pass  # 失敗時は R subprocess fallback に委ねる
        # Parquet からの高速読み込みを優先
        expression = None
        if cache_dir_str:
            expression = _sv_bridge.get_feature_expression_fast(
                Path(cache_dir_str), feature
            )
        # Parquet にない場合は R subprocess で取得
        if expression is None:
            expression = _sv_bridge.get_feature_expression(rds_path, feature)

        fig = go.Figure(go.Scatter(
            x=df["UMAP_1"], y=df["UMAP_2"],
            mode="markers",
            marker=dict(
                size=2,
                color=expression,
                colorscale="Plasma",
                colorbar=dict(title=feature),
                showscale=True,
            ),
            text=df["CellID"],
            hovertemplate=f"{feature}: " + "%{marker.color:.4f}<br>%{text}<extra></extra>",
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


# フィーチャー検索（サーバーサイドフィルタ）
@callback(
    Output("sv_feature_select", "options", allow_duplicate=True),
    Input("sv_feature_select", "search_value"),
    State("share_token", "data"),
    prevent_initial_call=True,
)
def sv_filter_features(search_value, token):
    if not search_value or len(search_value) < 2 or not token:
        return no_update
    data = _shared_data.get(token, {})
    features = data.get("features_list", [])
    if not features:
        return no_update
    q = search_value.lower()
    filtered = [f for f in features if q in f.lower()]
    return [{"label": f, "value": f} for f in filtered[:100]]


# =========================================================================
# ダウンロード
# =========================================================================

# =========================================================================
# DEG テーブル
# =========================================================================

@callback(
    Output("sv_deg_table", "data"),
    Input("sv_deg_data_store", "data"),
    prevent_initial_call=True,
)
def sv_update_deg_table(deg_data):
    """DEGデータストアの変更時にテーブルを更新"""
    if not deg_data:
        return []
    return deg_data


# =========================================================================
# Volcano Plot（軽量・読み取り専用、ホバーで化合物名表示）
# =========================================================================

@callback(
    [Output("sv_volcano_plot", "figure"),
     Output("sv_volcano_cluster_select", "options")],
    [Input("sv_volcano_cluster_select", "value"),
     Input("sv_deg_data_store", "data")],
)
def sv_update_volcano_plot(cluster, deg_data):
    """共有ページ用 Volcano Plot

    - クラスタ選択ドロップダウンの選択肢を deg_data から動的生成
    - ホバーで化合物名（annotation）を表示
    - 標準的な閾値線・配色（編集UIは省略）
    """
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

    # ホバー表示テキスト：annotation があれば化合物名を含める
    if "annotation" in df.columns:
        df["display_text"] = df.apply(
            lambda r: f"{r['gene']}<br>({r['annotation']})"
            if _is_meaningful_annotation(r.get('annotation', ''), r.get('gene', ''))
            else r['gene'],
            axis=1,
        )
    else:
        df["display_text"] = df["gene"]

    # クラスタ選択肢を deg_data から生成
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
