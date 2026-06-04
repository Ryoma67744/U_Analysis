# =============================================================================
# MSI Analysis Application - Interactive Spatial Mapping Callbacks
# インタラクティブ解析 Spatial Mapping コールバック
#
# interactive_callbacks.py から分離された Spatial Mapping 関連の
# ヘルパー関数・コールバックをまとめたモジュール。
# =============================================================================

import logging

import numpy as np
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import (Input, Output, State, callback, ctx, no_update, html, dcc,
                  ALL, MATCH)
from dash.exceptions import PreventUpdate

from app.config import CLUSTER_PRESET_COLORS, HIGHLIGHT_GRAY
from app.utils.color_utils import (
    cluster_sort_key as _cluster_sort_key,
    get_cluster_color_map as _get_cluster_color_map,
    get_merged_cluster_color_map as _get_merged_cluster_color_map,
    cluster_display_name as _cluster_display_name,
    get_cluster_colorscale as _get_cluster_colorscale,
)
from app.utils.display_helpers import (
    display_name as _display_name,
)

logger = logging.getLogger("msi.interactive.spatial")


# ---------------------------------------------------------------------------
# Spatial Mapping ヘルパー関数
# ---------------------------------------------------------------------------

def _transform_coords(x, y, angle_deg, flip_h=False, flip_v=False):
    """中心基準で座標を反転+2D回転"""
    cx, cy = np.nanmean(x), np.nanmean(y)
    # 反転（回転の前に適用）
    if flip_h:
        x = 2 * cx - x
    if flip_v:
        y = 2 * cy - y
    # 回転
    if angle_deg == 0:
        return x, y
    # 反転後の中心を再計算
    cx, cy = np.nanmean(x), np.nanmean(y)
    rad = np.radians(angle_deg)
    cos_a, sin_a = np.cos(rad), np.sin(rad)
    x_rot = cos_a * (x - cx) - sin_a * (y - cy) + cx
    y_rot = sin_a * (x - cx) + cos_a * (y - cy) + cy
    return x_rot, y_rot


def _calc_zero_gap_marker_size(plot_x, plot_y, render_height=310, scale_factor=1.0):
    """点間距離ベースのマーカーサイズを計算。

    scale_factor=1.0 で隣接点が接するサイズ（隙間ゼロ）。
    float を返す（Plotly は小数マーカーサイズに対応）。
    """
    if len(plot_x) <= 1:
        return 4
    sorted_ux = np.sort(np.unique(plot_x))
    if len(sorted_ux) <= 1:
        return 4
    min_spacing = float(np.min(np.diff(sorted_ux)))
    x_range = float(plot_x.max() - plot_x.min())
    y_range = float(plot_y.max() - plot_y.min()) if len(plot_y) > 1 else 1.0
    if y_range > 0 and x_range > 0:
        effective_w = render_height * (x_range / y_range)
        return max(2.0, min_spacing * effective_w / x_range * scale_factor)
    return 4


def _create_single_spatial_fig(df_sample, color_map, highlight_clusters,
                               selected_cell_ids, rotation_deg=0,
                               show_labels=False, flip_h=False, flip_v=False,
                               title=None, embed_legend=False,
                               cluster_to_idx=None, discrete_cscale=None,
                               marker_size=4, exclude_clusters=None,
                               label_size=10, saved_positions=None,
                               title_font_size=None, render_height=None,
                               cluster_name_map=None, scale_factor=1.0):
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

    # marker_size=0 の場合、データ密度ベースで自動計算（隙間ゼロ）
    if marker_size <= 0 and len(plot_x) > 1:
        sorted_ux = np.sort(np.unique(plot_x))
        if len(sorted_ux) > 1:
            min_spacing = float(np.min(np.diff(sorted_ux)))
            x_range = float(plot_x.max() - plot_x.min())
            y_range = float(plot_y.max() - plot_y.min()) if len(plot_y) > 1 else 1.0
            # scaleanchor="x" のため、描画幅は高さ×アスペクト比で決定
            # render_height 指定時はそれを使用、未指定時はWebデフォルト310px
            effective_h = render_height or 310
            if y_range > 0 and x_range > 0:
                effective_w = effective_h * (x_range / y_range)
                marker_size = max(2.0, min_spacing * effective_w / x_range * scale_factor)
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
                bg_marker = dict(size=marker_size, symbol="square", color=tc_values, colorscale="Greys",
                                 opacity=0.5, showscale=False)
                bg_name = "TIC"
            else:
                bg_marker = dict(size=marker_size, symbol="square", color=HIGHLIGHT_GRAY, opacity=0.2)
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
                marker=dict(size=marker_size + 1, symbol="square", color="red"),
                name=f"Selected ({mask_selected.sum()})",
            ))
    elif highlight_clusters and len(highlight_clusters) > 0:
        highlight_set = set(str(c) for c in highlight_clusters)
        # 非ハイライトクラスタをTIC or 灰色で描画
        mask_bg = ~df_sample["Cluster"].astype(str).isin(highlight_set)
        if mask_bg.values.any():
            if "TotalCount" in df_sample.columns:
                tc_values = df_sample["TotalCount"].values[mask_bg.values]
                bg_marker = dict(size=marker_size, symbol="square", color=tc_values, colorscale="Greys",
                                 opacity=0.5, showscale=False)
                bg_name = "TIC"
            else:
                bg_marker = dict(size=marker_size, symbol="square", color=HIGHLIGHT_GRAY, opacity=0.2)
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
                    marker=dict(size=marker_size + 1, symbol="square", color=color_map.get(str(cl), "#999999")),
                    name=_cluster_display_name(cl, cluster_name_map),
                    legendgroup=_cluster_display_name(cl, cluster_name_map),
                ))
    else:
        if embed_legend:
            # 凡例ダブルクリック時に他クラスタを「TIC (白黒)」or 灰色で
            # 残すための背景 trace。showlegend=False のため Plotly の
            # ダブルクリック操作対象外で、色付き trace が visible=False に
            # なっても下の背景が残る。
            # ※ highlight_clusters / selected_cell_ids 時の既存ロジック
            #   (line 128-135 / 156-160) と同じく TIC が利用可能なら
            #   Greys colorscale で MSI 画像の TIC を白黒表示する。
            if "TotalCount" in df_sample.columns:
                bg_marker = dict(
                    size=marker_size, symbol="square",
                    color=df_sample["TotalCount"].values,
                    colorscale="Greys", opacity=0.5, showscale=False,
                )
            else:
                bg_marker = dict(
                    size=marker_size, symbol="square",
                    color=HIGHLIGHT_GRAY, opacity=0.2,
                )
            fig.add_trace(go.Scattergl(
                x=plot_x, y=plot_y,
                mode="markers",
                marker=bg_marker,
                showlegend=False, hoverinfo="skip",
                name="_background_tic",
            ))
            # 凡例リンク用: クラスタ別個別トレース（legendgroup でダミーと連動）
            for cl in sorted(df_sample["Cluster"].unique(), key=_cluster_sort_key):
                mask = (df_sample["Cluster"].astype(str) == str(cl)).values
                if mask.any():
                    fig.add_trace(go.Scattergl(
                        x=plot_x[mask], y=plot_y[mask], mode="markers",
                        marker=dict(size=marker_size, symbol="square",
                                    color=color_map.get(str(cl), "#999999")),
                        text=[_cluster_display_name(cl, cluster_name_map)] * int(mask.sum()),
                        hovertemplate="%{text}<extra></extra>",
                        name=_cluster_display_name(cl, cluster_name_map), showlegend=False,
                        legendgroup=_cluster_display_name(cl, cluster_name_map),
                    ))
        elif cluster_to_idx is not None and discrete_cscale is not None:
            # 数値インデックス + discrete colorscale 方式
            n_clusters = max(len(cluster_to_idx), 1)
            point_values = np.array(
                [cluster_to_idx.get(str(cl), 0) for cl in df_sample["Cluster"]]
            )
            point_normalized = (point_values + 0.5) / n_clusters
            fig.add_trace(go.Scattergl(
                x=plot_x, y=plot_y, mode="markers",
                marker=dict(
                    size=marker_size,
                    symbol="square",
                    color=point_normalized,
                    colorscale=discrete_cscale,
                    cmin=0, cmax=1,
                    showscale=False,
                ),
                text=[_cluster_display_name(cl, cluster_name_map) for cl in df_sample["Cluster"]],
                hovertemplate="%{text}<extra></extra>",
                showlegend=False,
            ))
        else:
            # フォールバック: HEX文字列配列方式
            point_colors = [color_map.get(str(cl), "#999999") for cl in df_sample["Cluster"]]
            fig.add_trace(go.Scattergl(
                x=plot_x, y=plot_y, mode="markers",
                marker=dict(size=marker_size, symbol="square", color=point_colors),
                text=[_cluster_display_name(cl, cluster_name_map) for cl in df_sample["Cluster"]],
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
                    marker=dict(size=10, symbol="square", color=color_map.get(str(cl), "#999999")),
                    name=_cluster_display_name(cl, cluster_name_map),
                    showlegend=True,
                    legendrank=rank,
                    legendgroup=_cluster_display_name(cl, cluster_name_map),
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
                    text=_cluster_display_name(cl_str, cluster_name_map),
                    showarrow=False,
                    font=dict(size=label_size, color="black"),
                )

    layout_opts = dict(
        xaxis=dict(showgrid=False, showline=False, zeroline=False,
                   showticklabels=False, title="", visible=False),
        yaxis=dict(scaleanchor="x", showgrid=False, showline=False, zeroline=False,
                   showticklabels=False, title="", visible=False),
        margin=dict(l=10, r=10, t=max(30, (title_font_size or 14) + 15) if title else 10, b=10),
        plot_bgcolor="white",
        showlegend=embed_legend,
        legend=dict(itemsizing="constant", font=dict(size=12), tracegroupgap=2),
    )
    if title:
        layout_opts["title"] = dict(text=title, font=dict(size=title_font_size or 14), x=0.5)
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
    [State("spatial_rotation_store", "data"),
     State("sample_name_map_store", "data"),
     State("custom_color_map_store", "data"),
     State("cluster_name_map_store", "data")],
)
def create_spatial_controls(rds_path, rotation_store, name_map, custom_color_map, cluster_name_map=None):
    """データ読み込み後、サンプル別の回転/反転 + サンプル名変更コントロールを生成"""
    from app.callbacks.interactive_callbacks import _interactive_data, _save_interactive_settings, _set_active_key
    _set_active_key(rds_path)
    df = _interactive_data.get("plot_data")
    if df is None or "SpatialX" not in df.columns:
        return ""
    if not rotation_store:
        rotation_store = {}
    if not name_map:
        name_map = {}
    if not custom_color_map:
        custom_color_map = {}

    # ==================== 回転/反転 ====================
    samples = sorted(df["Sample"].unique())
    sample_options = [{"label": s, "value": s} for s in samples]
    first_sample = samples[0] if samples else None

    sample_blocks = []
    for i, s in enumerate(samples):
        transform = rotation_store.get(
            s, rotation_store.get("__all__", {"angle": 0, "flip_h": False, "flip_v": False}))
        if isinstance(transform, (int, float)):
            transform = {"angle": int(transform), "flip_h": False, "flip_v": False}

        display_s = _display_name(s, name_map)
        is_first = (i == 0)
        block = html.Div(
            id={"type": "sample_block", "index": s},
            style={"padding": "4px 8px",
                   "display": "block" if is_first else "none"},
            children=[
                dbc.Row(className="align-items-center mb-1", children=[
                    dbc.Col(width=3, children=[
                        html.Label(s, className="fw-bold small mb-0",
                                   style={"whiteSpace": "nowrap", "overflow": "hidden",
                                          "textOverflow": "ellipsis"}),
                    ]),
                    dbc.Col(width=4, children=[
                        dbc.Input(
                            id={"type": "sample_rename_input", "index": s},
                            value=display_s if display_s != s else "",
                            placeholder=s,
                            size="sm", debounce=True,
                        ),
                        html.Small(
                            id={"type": "sample_rename_lock_indicator", "index": s},
                            className="text-warning",
                            children="",
                        ),
                    ]),
                    dbc.Col(width=5, children=[
                        html.Div(className="d-flex gap-2 align-items-center", children=[
                            dbc.Checkbox(
                                id={"type": "per_sample_flip_h", "index": s},
                                label="<-> 左右", value=transform.get("flip_h", False),
                            ),
                            dbc.Checkbox(
                                id={"type": "per_sample_flip_v", "index": s},
                                label="<-> 上下", value=transform.get("flip_v", False),
                            ),
                            html.Small(
                                id={"type": "sample_rotation_lock_indicator", "index": s},
                                className="text-warning ms-2",
                                children="",
                            ),
                        ]),
                    ]),
                ]),
                dcc.Slider(
                    id={"type": "per_sample_rotation", "index": s},
                    min=0, max=270, step=90,
                    value=transform.get("angle", 0),
                    marks={0: "0\u00b0", 90: "90\u00b0", 180: "180\u00b0", 270: "270\u00b0"},
                ),
            ],
        )
        sample_blocks.append(block)

    rotation_section = [
        dbc.Select(
            id="spatial_sample_selector",
            options=sample_options,
            value=first_sample,
            size="sm",
            className="mb-2",
        ),
        *sample_blocks,
    ]

    # ==================== クラスタ色変更 ====================
    clusters = sorted(df["Cluster"].unique(), key=_cluster_sort_key)
    cluster_options = [{"label": _cluster_display_name(c, cluster_name_map), "value": str(c)} for c in clusters]
    first_cluster = str(clusters[0]) if clusters else None

    # 現在使用中の色マップを構築（デフォルト + カスタム）
    current_cmap = _get_cluster_color_map(df["Cluster"], custom_color_map)
    # 各クラスタが使用中の色 -> {色: クラスタ} のマップ
    color_usage = {}
    for cl_key, col_val in current_cmap.items():
        upper_col = col_val.upper()
        if upper_col not in color_usage:
            color_usage[upper_col] = cl_key

    cluster_blocks = []
    for idx, cl in enumerate(clusters):
        cl_str = str(cl)
        default_color = current_cmap.get(cl_str, "#999999")
        is_first = (idx == 0)

        swatches = []
        for pc in CLUSTER_PRESET_COLORS:
            # 他のクラスタで使用中ならグレーアウト
            owner = color_usage.get(pc.upper())
            used_by_other = (owner is not None and owner != cl_str)
            swatch_style = {
                "width": "18px", "height": "18px",
                "backgroundColor": pc,
                "border": "2px solid #aaa",
                "borderRadius": "3px",
                "display": "inline-block",
            }
            if used_by_other:
                swatch_style.update({
                    "opacity": "0.25",
                    "cursor": "not-allowed",
                    "pointerEvents": "none",
                })
            else:
                swatch_style["cursor"] = "pointer"

            swatches.append(
                html.Div(
                    style=swatch_style,
                    id={"type": "cluster_color_swatch",
                        "index": cl_str, "color": pc},
                    n_clicks=0,
                )
            )

        block = html.Div(
            id={"type": "cluster_block", "index": cl_str},
            style={"display": "block" if is_first else "none"},
            className="mb-2",
            children=[
                html.Label(_cluster_display_name(cl, cluster_name_map), className="small mb-1 fw-bold"),
                html.Div(
                    style={"display": "flex", "alignItems": "center", "gap": "6px"},
                    children=[
                        html.Div(
                            style={"display": "flex", "flexWrap": "wrap", "gap": "3px"},
                            children=swatches,
                        ),
                        dbc.Input(
                            type="color",
                            id={"type": "cluster_color_picker", "index": cl_str},
                            value=default_color,
                            style={"width": "32px", "height": "24px", "padding": "1px",
                                   "border": "1px solid #ccc", "cursor": "pointer",
                                   "flexShrink": "0"},
                        ),
                        html.Small(
                            id={"type": "cluster_color_lock_indicator", "index": cl_str},
                            className="text-warning ms-1",
                            children="",
                        ),
                    ],
                ),
            ],
        )
        cluster_blocks.append(block)

    color_section = [
        dbc.Select(
            id="spatial_cluster_selector",
            options=cluster_options,
            value=first_cluster,
            size="sm",
            className="mb-2",
        ),
        *cluster_blocks,
    ]

    return dbc.Accordion(
        [
            dbc.AccordionItem(title="回転/反転", children=rotation_section),
            dbc.AccordionItem(title="クラスタ色変更", children=color_section),
        ],
        start_collapsed=True,
        flush=True,
        always_open=True,
        style={"marginBottom": "8px"},
    )


# ---------------------------------------------------------------------------
# サンプル/クラスタ ブロック表示切替
# ---------------------------------------------------------------------------

@callback(
    Output({"type": "sample_block", "index": ALL}, "style"),
    Input("spatial_sample_selector", "value"),
    prevent_initial_call=True,
)
def toggle_sample_rotation_visibility(selected):
    """ドロップダウンで選択されたサンプルのみ表示"""
    styles = []
    for item in ctx.outputs_list:
        idx = item["id"]["index"]
        vis = "block" if idx == selected else "none"
        styles.append({"padding": "4px 8px", "display": vis})
    return styles


@callback(
    Output({"type": "cluster_block", "index": ALL}, "style"),
    Input("spatial_cluster_selector", "value"),
    prevent_initial_call=True,
)
def toggle_cluster_color_visibility(selected):
    """ドロップダウンで選択されたクラスタのみ表示"""
    styles = []
    for item in ctx.outputs_list:
        idx = item["id"]["index"]
        vis = "block" if idx == selected else "none"
        styles.append({"display": vis})
    return styles


# ---------------------------------------------------------------------------
# スウォッチの使用済み色グレーアウト
# ---------------------------------------------------------------------------

@callback(
    Output({"type": "cluster_color_swatch", "index": ALL, "color": ALL}, "style"),
    Input("custom_color_map_store", "data"),
    prevent_initial_call=True,
)
def update_swatch_disabled_state(custom_colors):
    """色マップ変更時に、他クラスタで使用中のスウォッチをグレーアウトする"""
    from app.callbacks.interactive_callbacks import _interactive_data
    if not custom_colors:
        custom_colors = {}
    df = _interactive_data.get("plot_data")
    if df is None:
        raise PreventUpdate

    # 現在の全色マップ（デフォルト＋カスタム）
    current_cmap = _get_cluster_color_map(df["Cluster"], custom_colors)
    # {色(大文字) -> クラスタID} の逆引き
    color_usage = {}
    for cl_key, col_val in current_cmap.items():
        upper_col = col_val.upper()
        if upper_col not in color_usage:
            color_usage[upper_col] = cl_key

    styles = []
    for item in ctx.outputs_list:
        swatch_cluster = item["id"]["index"]
        swatch_color = item["id"]["color"]

        base_style = {
            "width": "18px", "height": "18px",
            "backgroundColor": swatch_color,
            "border": "2px solid #aaa",
            "borderRadius": "3px",
            "display": "inline-block",
        }

        owner = color_usage.get(swatch_color.upper())
        if owner is not None and owner != swatch_cluster:
            base_style.update({
                "opacity": "0.25",
                "cursor": "not-allowed",
                "pointerEvents": "none",
            })
        else:
            base_style["cursor"] = "pointer"

        styles.append(base_style)
    return styles


# ---------------------------------------------------------------------------
# クラスタ色 Store 管理 (パターンマッチング)
# ---------------------------------------------------------------------------

@callback(
    [Output("custom_color_map_store", "data"),
     Output({"type": "cluster_color_picker", "index": ALL}, "value")],
    [Input({"type": "cluster_color_picker", "index": ALL}, "value"),
     Input({"type": "cluster_color_swatch", "index": ALL, "color": ALL}, "n_clicks")],
    State("custom_color_map_store", "data"),
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def update_custom_color_map(picker_values, swatch_clicks, current_store, rds_path=None):
    """カラーピッカーまたはスウォッチクリックでカスタム色マップStoreを更新する"""
    from app.callbacks.interactive_callbacks import _save_interactive_settings, _set_active_key
    _set_active_key(rds_path)
    current_store = current_store or {}

    # トリガーされたコンポーネントの判定
    triggered = ctx.triggered_id
    if not triggered:
        raise PreventUpdate

    # カラーピッカーのIDリストを取得（Output用の順序と一致させる）
    picker_ids = ctx.inputs_list[0]
    picker_cluster_order = [item["id"]["index"] for item in picker_ids]

    if isinstance(triggered, dict) and triggered.get("type") == "cluster_color_swatch":
        # --- スウォッチがクリックされた場合 ---
        cl = str(triggered["index"])
        color = triggered["color"]
        current_store[cl] = color
        # カラーピッカーの表示色も同期
        updated_picker_values = []
        for c_id in picker_cluster_order:
            if c_id == cl:
                updated_picker_values.append(color)
            else:
                # 既存のピッカー値を維持
                idx = picker_cluster_order.index(c_id)
                updated_picker_values.append(picker_values[idx])
        _save_interactive_settings("custom_color_map", current_store)
        return current_store, updated_picker_values
    else:
        # --- カラーピッカーが変更された場合 ---
        custom = {}
        for item in picker_ids:
            cl = item["id"]["index"]
            val = item.get("value")
            if val:
                custom[str(cl)] = val
        _save_interactive_settings("custom_color_map", custom)
        return custom, picker_values


# ---------------------------------------------------------------------------
# Spatial 回転 Store 管理 (パターンマッチング)
# ---------------------------------------------------------------------------

@callback(
    Output("spatial_rotation_store", "data"),
    [Input({"type": "per_sample_rotation", "index": ALL}, "value"),
     Input({"type": "per_sample_flip_h", "index": ALL}, "value"),
     Input({"type": "per_sample_flip_v", "index": ALL}, "value")],
    State("spatial_rotation_store", "data"),
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def update_rotation_store_from_per_sample(rotations, flip_hs, flip_vs, current_store, rds_path=None):
    """各プロットのコントロール変更時に Store を更新"""
    from app.callbacks.interactive_callbacks import _save_interactive_settings, _set_active_key
    _set_active_key(rds_path)
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
    _save_interactive_settings("spatial_rotation", current_store)
    return current_store


# ---------------------------------------------------------------------------
# サンプル名マップ管理 (パターンマッチング)
# ---------------------------------------------------------------------------

@callback(
    Output("sample_name_map_store", "data"),
    [Input({"type": "sample_rename_input", "index": ALL}, "value"),
     Input({"type": "umap_sample_rename_input", "index": ALL}, "value")],
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def update_sample_name_map(spatial_values, umap_values, rds_path=None):
    """サンプル名変更入力（Spatial側 + UMAP側）から表示名マッピングを更新。
    両側の値をマージし、トリガーされた側の値を高優先とする。"""
    from app.callbacks.interactive_callbacks import _interactive_data, _save_interactive_settings, _set_active_key
    _set_active_key(rds_path)
    triggered = ctx.triggered_id
    triggered_type = triggered.get("type", "") if isinstance(triggered, dict) else ""

    name_map = {}

    def _collect(inputs_list_idx):
        result = {}
        for inp in ctx.inputs_list[inputs_list_idx]:
            original = inp.get("id", {}).get("index", "")
            display_val = inp.get("value", "") or ""
            display_val = display_val.strip()
            if display_val and display_val != original:
                result[original] = display_val
        return result

    if triggered_type == "umap_sample_rename_input":
        # 非トリガー側（Spatial）を先に読み込み（低優先）
        name_map.update(_collect(0))
        # トリガー側（UMAP）で上書き（高優先）
        name_map.update(_collect(1))
    else:
        # 非トリガー側（UMAP）を先に読み込み（低優先）
        name_map.update(_collect(1))
        # トリガー側（Spatial）で上書き（高優先）
        name_map.update(_collect(0))

    # フルスクリーン用にも参照できるようモジュール変数にも保持
    _interactive_data["_name_map"] = name_map
    _save_interactive_settings("sample_name_map", name_map)
    return name_map


@callback(
    [Output("interactive_sample", "options", allow_duplicate=True),
     Output("feature_sample_select", "options", allow_duplicate=True)],
    Input("sample_name_map_store", "data"),
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def update_sample_dropdown_labels(name_map, rds_path=None):
    """サンプル名変更時にSpatial Mapping & Feature Plotサンプルドロップダウンのラベルを更新"""
    from app.callbacks.interactive_callbacks import _interactive_data, _set_active_key
    _set_active_key(rds_path)
    df = _interactive_data.get("plot_data")
    if df is None:
        return no_update, no_update
    if not name_map:
        name_map = {}
    samples = sorted(df["Sample"].unique())
    opts = [{"label": _display_name(s, name_map), "value": s} for s in samples]
    return opts, opts


# ---------------------------------------------------------------------------
# UMAP側サンプル名変更コントロール生成
# ---------------------------------------------------------------------------

@callback(
    Output("umap_name_controls_container", "children"),
    Input("seurat_rds_path_store", "data"),
    State("sample_name_map_store", "data"),
)
def create_umap_name_controls(rds_path, name_map):
    """データ読み込み後、UMAP側にサンプル名変更UIを生成"""
    from app.callbacks.interactive_callbacks import _interactive_data, _set_active_key
    _set_active_key(rds_path)
    df = _interactive_data.get("plot_data")
    if df is None:
        return ""
    if not name_map:
        name_map = {}

    samples = sorted(df["Sample"].unique())
    if len(samples) <= 1:
        return ""

    controls = []
    for i, s in enumerate(samples):
        display_s = _display_name(s, name_map)
        controls.append(
            html.Div(
                style={"padding": "2px 8px"},
                children=[
                    dbc.Row(className="align-items-center", children=[
                        dbc.Col(width=4, children=[
                            html.Label(s, className="fw-bold small mb-0",
                                       style={"whiteSpace": "nowrap", "overflow": "hidden",
                                              "textOverflow": "ellipsis"}),
                        ]),
                        dbc.Col(width=8, children=[
                            dbc.Input(
                                id={"type": "umap_sample_rename_input", "index": s},
                                value=display_s if display_s != s else "",
                                placeholder=s,
                                size="sm", debounce=True,
                            ),
                            html.Small(
                                id={"type": "umap_sample_rename_lock_indicator", "index": s},
                                className="text-warning",
                                children="",
                            ),
                        ]),
                    ]),
                    html.Hr(className="my-1") if i < len(samples) - 1 else html.Div(),
                ],
            )
        )

    return dbc.Accordion(
        [dbc.AccordionItem(title="サンプル名変更", children=controls)],
        start_collapsed=True,
        flush=True,
        always_open=True,
        style={"marginBottom": "8px"},
    )


# ---------------------------------------------------------------------------
# Spatial マーカーサイズ Auto ボタン
# ---------------------------------------------------------------------------

@callback(
    Output("spatial_marker_size", "value"),
    Input("spatial_marker_auto_btn", "n_clicks"),
    [State("spatial_rotation_store", "data"),
     State("interactive_sample", "value")],
    prevent_initial_call=True,
)
def auto_spatial_marker(n_clicks, rotation_store, sample):
    """通常ビュー: スライダーを自動モード(0)にリセット -> 各サンプル個別に自動計算"""
    return 0


@callback(
    Output("fs_spatial_marker_size", "value"),
    Input("fs_spatial_marker_auto_btn", "n_clicks"),
    [State("spatial_rotation_store", "data"),
     State("fs_spatial_sample", "value"),
     State("fs_spatial_height_slider", "value")],
    prevent_initial_call=True,
)
def auto_fs_spatial_marker(n_clicks, rotation_store, sample, height_val):
    """フルスクリーン: スライダーを自動モード(0)にリセット -> 各サンプル個別に自動計算"""
    return 0


# ---------------------------------------------------------------------------
# Feature Plot マーカーサイズ Auto ボタン
# ---------------------------------------------------------------------------

@callback(
    Output("feature_marker_size", "value"),
    Input("feature_marker_auto_btn", "n_clicks"),
    [State("spatial_rotation_store", "data"),
     State("feature_sample_select", "value")],
    prevent_initial_call=True,
)
def auto_feature_marker(n_clicks, rotation_store, sample):
    """Feature Plot: スライダーを自動モード(0)にリセット -> 各サンプル個別に自動計算"""
    return 0


# ---------------------------------------------------------------------------
# Spatial Mapping メインプロット
# ---------------------------------------------------------------------------

@callback(
    [Output("spatial_plots_container", "children"),
     Output("last_spatial_figure_store", "data"),
     Output("batch_spatial_figures_store", "data")],
    [Input("interactive_sample", "value"),
     Input("spatial_highlight_cluster", "value"),
     Input("interactive_umap_plot", "selectedData"),
     Input("spatial_rotation_store", "data"),
     Input("spatial_show_labels", "value"),
     Input("spatial_marker_size", "value"),
     Input("spatial_exclude_cluster", "value"),
     Input("spatial_label_size", "value"),
     Input("seurat_rds_path_store", "data"),
     Input("sample_name_map_store", "data"),
     Input("fullscreen_closed_trigger", "data"),
     Input("custom_color_map_store", "data"),
     Input("spatial_columns_per_row", "value"),
     Input("cluster_name_map_store", "data"),
     Input("umap_merge_toggle", "value"),
     Input("umap_merge_color_mode", "value"),
     Input("interactive_accordion", "active_item")],
    State("accumulated_label_positions", "data"),
)
def update_spatial_plots(sample, highlight_clusters, selected_data,
                         rotation_store, show_labels, marker_size,
                         exclude_clusters, label_size, rds_path, name_map,
                         _fs_trigger, custom_colors, columns_per_row,
                         cluster_name_map, merge_toggle, merge_color_mode,
                         active_items, accumulated_positions):
    active_list = active_items if isinstance(active_items, list) else ([active_items] if active_items else [])
    if "acc_spatial" not in active_list:
        return no_update, no_update, no_update
    from app.callbacks.interactive_callbacks import _set_active_key
    _set_active_key(rds_path)
    from app.callbacks.interactive_callbacks import _interactive_data
    from app.callbacks.interactive_umap import _get_merged_label_positions
    df = _interactive_data.get("plot_data")
    if df is None or "SpatialX" not in df.columns:
        return html.Div("空間座標データがありません", className="text-muted p-3"), None, []

    if not rotation_store:
        rotation_store = {}
    if not name_map:
        name_map = {}

    # UMAP選択セルID
    selected_cell_ids = set()
    if selected_data and selected_data.get("points"):
        for pt in selected_data["points"]:
            if pt.get("text"):
                selected_cell_ids.add(pt["text"])

    # マージ表示切替 (Spatial Mapping)
    plot_df = df
    effective_custom_colors = custom_colors
    if merge_toggle == "merged" and "Cluster_merged" in df.columns:
        plot_df = df.copy()
        plot_df["Cluster"] = plot_df["Cluster_merged"]
        effective_custom_colors = _get_merged_cluster_color_map(
            plot_df["Cluster"], mode=merge_color_mode or "shade"
        )

    color_map = _get_cluster_color_map(plot_df["Cluster"], effective_custom_colors)
    cluster_to_idx, discrete_cscale = _get_cluster_colorscale(plot_df["Cluster"], effective_custom_colors)
    # rds_path / method を引数で明示し _interactive_data 未初期化 race を回避
    method = _interactive_data.get("method")
    all_pos = _get_merged_label_positions(accumulated_positions,
                                          rds_path=rds_path, method=method)
    spatial_pos = all_pos.get("spatial", {})

    # 表示対象サンプル
    if sample:
        samples_to_show = [sample]
    else:
        samples_to_show = sorted(plot_df["Sample"].unique())

    graphs = []
    batch_fig_dicts = []
    representative_fig = None
    for s in samples_to_show:
        df_s = plot_df[plot_df["Sample"] == s]
        # サンプル別の変換設定を取得
        transform = rotation_store.get(s, rotation_store.get("__all__", {"angle": 0, "flip_h": False, "flip_v": False}))
        # 後方互換: 旧形式(int)の場合
        if isinstance(transform, (int, float)):
            transform = {"angle": int(transform), "flip_h": False, "flip_v": False}
        rotation_deg = transform.get("angle", 0)
        flip_h = transform.get("flip_h", False)
        flip_v = transform.get("flip_v", False)
        display_s = _display_name(s, name_map)
        fig = _create_single_spatial_fig(df_s, color_map, highlight_clusters,
                                         selected_cell_ids,
                                         rotation_deg=rotation_deg,
                                         show_labels=show_labels,
                                         flip_h=flip_h, flip_v=flip_v,
                                         title=display_s, embed_legend=True,
                                         cluster_to_idx=cluster_to_idx,
                                         discrete_cscale=discrete_cscale,
                                         marker_size=marker_size or 0,
                                         exclude_clusters=exclude_clusters,
                                         label_size=label_size or 10,
                                         saved_positions=spatial_pos.get(s),
                                         cluster_name_map=cluster_name_map)
        if representative_fig is None:
            representative_fig = fig
        batch_fig_dicts.append((f"Spatial_{display_s}", fig.to_dict()))
        cfg = dict(_SPATIAL_IMG_CONFIG)
        cfg["toImageButtonOptions"] = dict(cfg["toImageButtonOptions"],
                                           filename=f"Spatial_{display_s}")
        if columns_per_row:
            n_cols = columns_per_row
            gap_total = (n_cols - 1) * 15
            flex_basis = f"calc({100 / n_cols:.2f}% - {gap_total / n_cols:.1f}px)"
            min_w = "0"
        else:
            n_cols = len(samples_to_show)
            flex_basis = f"{max(20, 90 // n_cols)}%"
            min_w = "300px"
        graphs.append(
            html.Div(
                style={"flex": f"1 1 {flex_basis}", "minWidth": min_w,
                        "border": "1px solid #dee2e6", "borderRadius": "6px",
                        "padding": "5px", "backgroundColor": "#fff"},
                children=[
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
    return container, store_data, batch_fig_dicts


# ---------------------------------------------------------------------------
# Spatial 表示パラメータの永続化 (label_size 等)
# 軽量ビューア (/lite/...) はこの値を読み出して同じ表示を再現する。
# interactive_umap.save_umap_display_settings の Spatial 版。
# ---------------------------------------------------------------------------

@callback(
    Output("spatial_display_save_trigger", "data"),
    [Input("spatial_marker_size", "value"),
     Input("spatial_label_size", "value"),
     Input("spatial_show_labels", "value"),
     Input("spatial_columns_per_row", "value"),
     Input("spatial_exclude_cluster", "value")],
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def save_spatial_display_settings(marker_size, label_size, show_labels,
                                  columns_per_row, exclude_cluster, rds_path):
    """Spatial 表示パラメータの変更を interactive_settings.json に保存。

    簡易ビューアー (/lite/...) はこの値を読み出して同じ表示を再現する。
    ver3.5: exclude_cluster も保存対象に追加。
    """
    if not rds_path:
        raise PreventUpdate
    from app.callbacks.interactive_callbacks import _save_interactive_settings, _set_active_key
    _set_active_key(rds_path)
    _save_interactive_settings("spatial_display", {
        "marker_size": marker_size if marker_size is not None else 0,
        "label_size": label_size if label_size is not None else 10,
        "show_labels": bool(show_labels),
        "columns_per_row": columns_per_row if columns_per_row is not None else 0,
        "exclude_cluster": list(exclude_cluster) if exclude_cluster else [],
    })
    return no_update


# ---------------------------------------------------------------------------
# PR-G2: sample_rename_input / umap_sample_rename_input の UI ロック
# ---------------------------------------------------------------------------

@callback(
    Output({"type": "sample_rename_lock_indicator", "index": ALL}, "id"),
    Input({"type": "sample_rename_input", "index": ALL}, "value"),
    [State("seurat_rds_path_store", "data"),
     State("session_id_store", "data"),
     State({"type": "sample_rename_input", "index": ALL}, "id")],
    prevent_initial_call=True,
)
def acquire_sample_rename_lock(values, rds_path, session_id, ids):
    """Spatial サイドのサンプル名変更入力でロック取得。"""
    from app.callbacks.edit_lock_callbacks import acquire_lock_for_callback
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict) or not rds_path or not session_id:
        return [no_update] * len(ids)
    target_index = triggered.get("index")
    if target_index is None:
        return [no_update] * len(ids)
    # sample_rename / umap_sample_rename は同じサンプルに対する別 UI なので
    # 同一 field_id "sample_rename:{s}" で 1 ロックを共有する。
    field_id = f"sample_rename:{target_index}"
    acquire_lock_for_callback(rds_path, field_id, session_id)
    return [no_update] * len(ids)


@callback(
    Output({"type": "umap_sample_rename_lock_indicator", "index": ALL}, "id"),
    Input({"type": "umap_sample_rename_input", "index": ALL}, "value"),
    [State("seurat_rds_path_store", "data"),
     State("session_id_store", "data"),
     State({"type": "umap_sample_rename_input", "index": ALL}, "id")],
    prevent_initial_call=True,
)
def acquire_umap_sample_rename_lock(values, rds_path, session_id, ids):
    """UMAP サイドのサンプル名変更入力でロック取得。
    Spatial 側と同じ field_id を共有 (同一サンプルへの編集は 1 ロック)。"""
    from app.callbacks.edit_lock_callbacks import acquire_lock_for_callback
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict) or not rds_path or not session_id:
        return [no_update] * len(ids)
    target_index = triggered.get("index")
    if target_index is None:
        return [no_update] * len(ids)
    field_id = f"sample_rename:{target_index}"  # Spatial 側と共有
    acquire_lock_for_callback(rds_path, field_id, session_id)
    return [no_update] * len(ids)


@callback(
    [Output({"type": "sample_rename_input", "index": MATCH}, "disabled"),
     Output({"type": "sample_rename_lock_indicator", "index": MATCH}, "children")],
    Input("edit_lock_state", "data"),
    [State({"type": "sample_rename_input", "index": MATCH}, "id"),
     State("session_id_store", "data")],
)
def reflect_sample_rename_lock(lock_state, comp_id, my_session_id):
    if not lock_state or not comp_id:
        return False, ""
    field_id = f"sample_rename:{comp_id.get('index')}"
    owner = lock_state.get(field_id)
    if owner and owner.get("user_id") != my_session_id:
        return True, f"編集中: {owner.get('user_display', '?')}"
    return False, ""


@callback(
    [Output({"type": "umap_sample_rename_input", "index": MATCH}, "disabled"),
     Output({"type": "umap_sample_rename_lock_indicator", "index": MATCH}, "children")],
    Input("edit_lock_state", "data"),
    [State({"type": "umap_sample_rename_input", "index": MATCH}, "id"),
     State("session_id_store", "data")],
)
def reflect_umap_sample_rename_lock(lock_state, comp_id, my_session_id):
    """UMAP 側も Spatial 側と同じ field_id (sample_rename:{s}) を見る。"""
    if not lock_state or not comp_id:
        return False, ""
    field_id = f"sample_rename:{comp_id.get('index')}"
    owner = lock_state.get(field_id)
    if owner and owner.get("user_id") != my_session_id:
        return True, f"編集中: {owner.get('user_display', '?')}"
    return False, ""


# ---------------------------------------------------------------------------
# PR-G2: per_sample_rotation / flip_h / flip_v (1 ロックで 3 コンポーネント共有)
# ---------------------------------------------------------------------------

@callback(
    Output({"type": "sample_rotation_lock_indicator", "index": ALL}, "id"),
    [Input({"type": "per_sample_rotation", "index": ALL}, "value"),
     Input({"type": "per_sample_flip_h", "index": ALL}, "value"),
     Input({"type": "per_sample_flip_v", "index": ALL}, "value")],
    [State("seurat_rds_path_store", "data"),
     State("session_id_store", "data"),
     State({"type": "sample_rotation_lock_indicator", "index": ALL}, "id")],
    prevent_initial_call=True,
)
def acquire_sample_rotation_lock(_rotvals, _hvals, _vvals, rds_path, session_id, ids):
    """回転/反転コンポーネントいずれかの変更でロック取得。
    field_id = "sample_rotation:{s}" でサンプル単位に 3 コンポーネント共有。"""
    from app.callbacks.edit_lock_callbacks import acquire_lock_for_callback
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict) or not rds_path or not session_id:
        return [no_update] * len(ids)
    target_index = triggered.get("index")
    if target_index is None:
        return [no_update] * len(ids)
    field_id = f"sample_rotation:{target_index}"
    acquire_lock_for_callback(rds_path, field_id, session_id)
    return [no_update] * len(ids)


@callback(
    [Output({"type": "per_sample_rotation", "index": MATCH}, "disabled"),
     Output({"type": "per_sample_flip_h", "index": MATCH}, "disabled"),
     Output({"type": "per_sample_flip_v", "index": MATCH}, "disabled"),
     Output({"type": "sample_rotation_lock_indicator", "index": MATCH}, "children")],
    Input("edit_lock_state", "data"),
    [State({"type": "per_sample_rotation", "index": MATCH}, "id"),
     State("session_id_store", "data")],
)
def reflect_sample_rotation_lock(lock_state, comp_id, my_session_id):
    """回転/反転 3 コンポーネントを 1 ロックで一括 disabled。"""
    if not lock_state or not comp_id:
        return False, False, False, ""
    field_id = f"sample_rotation:{comp_id.get('index')}"
    owner = lock_state.get(field_id)
    if owner and owner.get("user_id") != my_session_id:
        msg = f"編集中: {owner.get('user_display', '?')}"
        return True, True, True, msg
    return False, False, False, ""


# ---------------------------------------------------------------------------
# PR-G2: cluster_color_picker
# ---------------------------------------------------------------------------

@callback(
    Output({"type": "cluster_color_lock_indicator", "index": ALL}, "id"),
    Input({"type": "cluster_color_picker", "index": ALL}, "value"),
    [State("seurat_rds_path_store", "data"),
     State("session_id_store", "data"),
     State({"type": "cluster_color_lock_indicator", "index": ALL}, "id")],
    prevent_initial_call=True,
)
def acquire_cluster_color_lock(_vals, rds_path, session_id, ids):
    from app.callbacks.edit_lock_callbacks import acquire_lock_for_callback
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict) or not rds_path or not session_id:
        return [no_update] * len(ids)
    target_index = triggered.get("index")
    if target_index is None:
        return [no_update] * len(ids)
    field_id = f"cluster_color:{target_index}"
    acquire_lock_for_callback(rds_path, field_id, session_id)
    return [no_update] * len(ids)


@callback(
    [Output({"type": "cluster_color_picker", "index": MATCH}, "disabled"),
     Output({"type": "cluster_color_lock_indicator", "index": MATCH}, "children")],
    Input("edit_lock_state", "data"),
    [State({"type": "cluster_color_picker", "index": MATCH}, "id"),
     State("session_id_store", "data")],
)
def reflect_cluster_color_lock(lock_state, comp_id, my_session_id):
    if not lock_state or not comp_id:
        return False, ""
    field_id = f"cluster_color:{comp_id.get('index')}"
    owner = lock_state.get(field_id)
    if owner and owner.get("user_id") != my_session_id:
        return True, f"編集中: {owner.get('user_display', '?')}"
    return False, ""
