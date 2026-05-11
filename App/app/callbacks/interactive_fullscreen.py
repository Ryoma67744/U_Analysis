# =============================================================================
# MSI Analysis Application - Interactive Fullscreen & Label Callbacks
# インタラクティブ解析 フルスクリーン拡大・ラベル蓄積 コールバック
#
# interactive_callbacks.py から分離されたフルスクリーンモーダル関連の
# コールバック・ヘルパー関数をまとめたモジュール。
# =============================================================================

import json
import logging
from datetime import datetime
from pathlib import Path

import dash_bootstrap_components as dbc
from dash import (Input, Output, State, callback, ctx, no_update, html, dcc, ALL)
from dash.exceptions import PreventUpdate

from app.utils.color_utils import (
    cluster_sort_key as _cluster_sort_key,
    get_cluster_color_map as _get_cluster_color_map,
    cluster_display_name as _cluster_display_name,
    get_cluster_colorscale as _get_cluster_colorscale,
)
from app.utils.display_helpers import (
    display_name as _display_name,
)
from app.utils.label_persistence import (
    extract_annotation_positions_by_name as _extract_annotation_positions_by_name,
    merge_label_positions as _merge_label_positions,
    save_label_positions as _save_label_positions,
)

# 共有状態・ヘルパーを interactive_callbacks / interactive_umap から参照
from app.callbacks.interactive_callbacks import (
    _interactive_data,
    _load_label_positions,
    _get_label_positions_path,
)
from app.callbacks.interactive_umap import (
    _get_merged_label_positions,
)

logger = logging.getLogger("msi.interactive.fullscreen")


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
     State("feature_plot_container", "children"),
     State("last_spatial_figure_store", "data"),
     State("deg_data_store", "data"),
     State("spatial_rotation_store", "data"),
     State("custom_color_map_store", "data"),
     State("spatial_columns_per_row", "value"),
     State("cluster_name_map_store", "data")],
    prevent_initial_call=True,
)
def toggle_fullscreen(umap_n, feat_n, spatial_n, deg_n,
                      umap_fig, feat_container_children, spatial_fig_data, deg_data,
                      rotation_store, custom_colors, spatial_columns_per_row,
                      cluster_name_map=None):
    # 遅延 import（循環参照回避）
    from app.callbacks.interactive_umap import _build_umap_integrated_fig
    from app.callbacks.interactive_spatial import _create_single_spatial_fig

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
        cluster_opts = [{"label": _cluster_display_name(c, cluster_name_map), "value": str(c)} for c in clusters]
        color_map = _get_cluster_color_map(df["Cluster"], custom_colors)

        # タイトル（RDSファイル名から生成）
        rds_path = _interactive_data.get("rds_path", "")
        umap_title = Path(rds_path).stem if rds_path else "UMAP"

        # 初期グラフ（統合モード）
        init_fig = _build_umap_integrated_fig(df, "Cluster", None, True, False,
                                               title=umap_title,
                                               custom_colors=custom_colors,
                                               cluster_name_map=cluster_name_map)
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
            ]),
            html.Div(id="fs_umap_graph_container", children=[init_graph]),
        ])
        return True, "UMAP", body

    # ===== Feature Plot (コンテナごと拡大) =====
    if trigger == "expand_feature_btn" and feat_container_children:
        return (
            True, "Feature Plot",
            html.Div(feat_container_children),
        )

    # ===== Spatial Mapping (インタラクティブ) =====
    if trigger == "expand_spatial_btn":
        df = _interactive_data.get("plot_data")
        if df is None or "SpatialX" not in df.columns:
            return False, "", ""

        samples = sorted(df["Sample"].unique())
        name_map = _interactive_data.get("_name_map") or {}
        sample_opts = [{"label": _display_name(s, name_map), "value": s} for s in samples]
        clusters = sorted(df["Cluster"].unique(), key=_cluster_sort_key)
        cluster_opts = [{"label": _cluster_display_name(c, cluster_name_map), "value": str(c)} for c in clusters]
        color_map = _get_cluster_color_map(df["Cluster"], custom_colors)
        cluster_to_idx, discrete_cscale = _get_cluster_colorscale(df["Cluster"], custom_colors)

        # 初期グラフ（全サンプル、rotation_store適用）
        if not rotation_store:
            rotation_store = {}
        init_graphs = []
        for s in samples:
            display_s = _display_name(s, name_map)
            df_s = df[df["Sample"] == s]
            transform = rotation_store.get(
                s, rotation_store.get("__all__", {"angle": 0, "flip_h": False, "flip_v": False}))
            if isinstance(transform, (int, float)):
                transform = {"angle": int(transform), "flip_h": False, "flip_v": False}
            fig = _create_single_spatial_fig(df_s, color_map, None, set(),
                                             rotation_deg=transform.get("angle", 0),
                                             flip_h=transform.get("flip_h", False),
                                             flip_v=transform.get("flip_v", False),
                                             title=display_s, embed_legend=True,
                                             cluster_to_idx=cluster_to_idx,
                                             discrete_cscale=discrete_cscale,
                                             cluster_name_map=cluster_name_map)
            if spatial_columns_per_row:
                n_cols = spatial_columns_per_row
                gap_total = (n_cols - 1) * 15
                flex_basis = f"calc({100 / n_cols:.2f}% - {gap_total / n_cols:.1f}px)"
                min_w = "0"
            else:
                n_cols = len(samples)
                flex_basis = f"{max(20, 90 // n_cols)}%"
                min_w = "350px"
            init_cfg = dict(fs_config)
            init_cfg["toImageButtonOptions"] = dict(init_cfg["toImageButtonOptions"],
                                                     filename=f"Spatial_{display_s}")
            init_graphs.append(
                html.Div(
                    style={"flex": f"1 1 {flex_basis}", "minWidth": min_w,
                            "border": "1px solid #dee2e6", "borderRadius": "6px",
                            "padding": "5px", "backgroundColor": "#fff"},
                    children=[
                        dcc.Graph(figure=fig, style={"height": "60vh"}, config=init_cfg),
                    ],
                )
            )
        init_container = html.Div(
            style={"display": "flex", "flexWrap": "wrap", "gap": "15px"},
            children=init_graphs,
        )

        # フルスクリーン用サンプル別コントロール（1つのAccordionItemに統合）
        name_map = _interactive_data.get("_name_map") or {}
        fs_all_controls = []
        for i, s in enumerate(samples):
            t = rotation_store.get(
                s, rotation_store.get("__all__", {"angle": 0, "flip_h": False, "flip_v": False}))
            if isinstance(t, (int, float)):
                t = {"angle": int(t), "flip_h": False, "flip_v": False}
            display_s = _display_name(s, name_map)
            fs_all_controls.append(
                html.Div(
                    style={"padding": "4px 8px"},
                    children=[
                        html.Label(display_s or s, className="fw-bold small mb-1"),
                        dcc.Slider(
                            id={"type": "per_sample_rotation", "index": s},
                            min=0, max=270, step=90,
                            value=t.get("angle", 0),
                            marks={0: "0\u00b0", 90: "90\u00b0", 180: "180\u00b0", 270: "270\u00b0"},
                        ),
                        html.Div(className="d-flex gap-2 justify-content-center", children=[
                            dbc.Checkbox(
                                id={"type": "per_sample_flip_h", "index": s},
                                label="\u2194 左右", value=t.get("flip_h", False),
                            ),
                            dbc.Checkbox(
                                id={"type": "per_sample_flip_v", "index": s},
                                label="\u2195 上下", value=t.get("flip_v", False),
                            ),
                        ]),
                        html.Hr(className="my-1") if i < len(samples) - 1 else html.Div(),
                    ],
                )
            )
        fs_accordion_items = [dbc.AccordionItem(title="回転/反転", children=fs_all_controls)]

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
                    html.Div(style={"display": "flex", "alignItems": "center", "gap": "4px"}, children=[
                        dbc.Label("マーカー", className="small mb-0"),
                        dbc.Button("Auto", id="fs_spatial_marker_auto_btn",
                                   size="sm", outline=True, color="info",
                                   style={"padding": "0 5px", "fontSize": "10px",
                                          "lineHeight": "1.2"}),
                    ]),
                    dcc.Slider(
                        id="fs_spatial_marker_size",
                        min=0, max=30, step=1, value=0,
                        marks={0: "自動", 10: "10", 20: "20", 30: "30"},
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
        deg_cluster_opts = [{"label": _cluster_display_name(c, cluster_name_map), "value": c} for c in deg_clusters]

        fs_deg_body = dbc.Tabs(active_tab="fs_deg_volcano_tab", children=[
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
# フルスクリーン閉鎖 → メインプロット再描画トリガー
# ---------------------------------------------------------------------------

@callback(
    Output("fullscreen_closed_trigger", "data"),
    Input("fullscreen_plot_modal", "is_open"),
    State("fullscreen_closed_trigger", "data"),
    prevent_initial_call=True,
)
def on_fullscreen_close(is_open, current_val):
    """フルスクリーンモーダルが閉じた時にトリガー値をインクリメントし、
    メインプロットの再描画をトリガーする"""
    if not is_open:
        return (current_val or 0) + 1
    return no_update


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
    [State("custom_color_map_store", "data"),
     State("umap_columns_per_row", "value"),
     State("accumulated_label_positions", "data"),
     State("cluster_name_map_store", "data")],
    prevent_initial_call=True,
)
def update_fs_umap(display_mode, color_by, highlight, show_labels, show_legend,
                   height_val, width_val, marker_size, exclude_clusters, label_size,
                   custom_color_map, columns_per_row, accumulated_positions,
                   cluster_name_map=None):
    # 遅延 import（循環参照回避）
    from app.callbacks.interactive_umap import (
        _build_umap_integrated_fig,
        _build_umap_per_sample_graphs,
    )

    height_val = height_val or 78
    width_val = width_val or 95
    df = _interactive_data.get("plot_data")
    if df is None:
        return ""
    custom_colors = custom_color_map if custom_color_map else None
    color_map = _get_cluster_color_map(df["Cluster"], custom_colors)
    fs_config = {"scrollZoom": True, "edits": {"annotationPosition": True}, "toImageButtonOptions": {"format": "png", "scale": 3}}
    all_pos = _get_merged_label_positions(accumulated_positions)

    # タイトル（RDSファイル名から生成）
    rds_path = _interactive_data.get("rds_path", "")
    umap_title = Path(rds_path).stem if rds_path else "UMAP"

    if display_mode == "integrated":
        fig = _build_umap_integrated_fig(df, color_by, highlight, show_legend, show_labels,
                                          title=umap_title,
                                          marker_size=marker_size or 2,
                                          exclude_clusters=exclude_clusters,
                                          label_size=label_size or 14,
                                          saved_positions=all_pos.get("umap_integrated"),
                                          custom_colors=custom_colors,
                                          cluster_name_map=cluster_name_map)
        fs_cfg = dict(fs_config)
        fs_cfg["toImageButtonOptions"] = dict(fs_cfg["toImageButtonOptions"],
                                               filename=f"UMAP_{umap_title}")
        return html.Div(
            style={"width": f"{width_val}vw", "margin": "0 auto"},
            children=[dcc.Graph(id="fs_umap_integrated_graph", figure=fig, style={"height": f"{height_val}vh"}, config=fs_cfg)],
        )
    else:
        per_h = max(height_val // 2, 25)
        name_map = _interactive_data.get("_name_map") or {}
        graphs = _build_umap_per_sample_graphs(df, color_map, highlight,
                                                show_labels, graph_height=f"{per_h}vh",
                                                marker_size=marker_size or 2,
                                                exclude_clusters=exclude_clusters,
                                                label_size=label_size or 11,
                                                saved_positions=all_pos.get("umap_per_sample"),
                                                show_legend=bool(show_legend),
                                                name_map=name_map,
                                                columns_per_row=columns_per_row or 0)
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
    [State("custom_color_map_store", "data"),
     State("spatial_columns_per_row", "value"),
     State("accumulated_label_positions", "data"),
     State("cluster_name_map_store", "data")],
    prevent_initial_call=True,
)
def update_fs_spatial(sample, rotation_store, show_labels, highlight,
                      exclude_clusters, marker_size, height_val, width_val,
                      label_size, custom_colors, columns_per_row,
                      accumulated_positions, cluster_name_map=None):
    # 遅延 import（循環参照回避）
    from app.callbacks.interactive_spatial import _create_single_spatial_fig

    height_val = height_val or 60
    width_val = width_val or 95
    df = _interactive_data.get("plot_data")
    if df is None or "SpatialX" not in df.columns:
        return ""
    color_map = _get_cluster_color_map(df["Cluster"], custom_colors)
    cluster_to_idx, discrete_cscale = _get_cluster_colorscale(df["Cluster"], custom_colors)
    all_pos = _get_merged_label_positions(accumulated_positions)
    spatial_pos = all_pos.get("spatial", {})
    if not rotation_store:
        rotation_store = {}

    if sample:
        samples_to_show = [sample]
    else:
        samples_to_show = sorted(df["Sample"].unique())

    fs_config = {"scrollZoom": True, "edits": {"annotationPosition": True}, "toImageButtonOptions": {"format": "png", "scale": 3}}
    name_map = _interactive_data.get("_name_map") or {}
    graphs = []
    for s in samples_to_show:
        display_s = _display_name(s, name_map)
        df_s = df[df["Sample"] == s]
        transform = rotation_store.get(
            s, rotation_store.get("__all__", {"angle": 0, "flip_h": False, "flip_v": False}))
        if isinstance(transform, (int, float)):
            transform = {"angle": int(transform), "flip_h": False, "flip_v": False}
        render_h = round(height_val * 10.8)  # vh → px概算
        fig = _create_single_spatial_fig(df_s, color_map, highlight, set(),
                                         rotation_deg=transform.get("angle", 0),
                                         show_labels=show_labels,
                                         flip_h=transform.get("flip_h", False),
                                         flip_v=transform.get("flip_v", False),
                                         title=display_s, embed_legend=True,
                                         cluster_to_idx=cluster_to_idx,
                                         discrete_cscale=discrete_cscale,
                                         marker_size=marker_size or 0,
                                         exclude_clusters=exclude_clusters,
                                         label_size=label_size or 10,
                                         saved_positions=spatial_pos.get(s),
                                         render_height=render_h,
                                         cluster_name_map=cluster_name_map)
        if columns_per_row:
            n_cols = columns_per_row
            gap_total = (n_cols - 1) * 15
            flex_basis = f"calc({100 / n_cols:.2f}% - {gap_total / n_cols:.1f}px)"
            min_w = "0"
        else:
            n_cols = len(samples_to_show)
            flex_basis = f"{max(20, 90 // n_cols)}%"
            min_w = "350px"
        fs_cfg = dict(fs_config)
        fs_cfg["toImageButtonOptions"] = dict(fs_cfg["toImageButtonOptions"],
                                               filename=f"Spatial_{display_s}")
        graphs.append(
            html.Div(
                style={"flex": f"1 1 {flex_basis}", "minWidth": min_w,
                        "border": "1px solid #dee2e6", "borderRadius": "6px",
                        "padding": "5px", "backgroundColor": "#fff"},
                children=[
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
# ラベル位置の永続保存（v2: relayoutData 蓄積 + DOM スナップショット二重方式）
# ---------------------------------------------------------------------------
# Primary: relayoutData からアノテーション位置をリアルタイム蓄積（サーバーサイド）
# Backup: 保存ボタン押下時に Plotly.js DOM を直接読み取り（クライアントサイド）


# --- メカニズム1: relayoutData 蓄積 ---
# v4: 通常モード / FS モードに分割（動的 Input がコールバック全体をブロックする問題の対策）


def _accumulate_core(triggered_id, existing, excl_fn):
    """蓄積コールバックの共通ロジック。

    Args:
        triggered_id: ctx.triggered_id
        existing: accumulated_label_positions Store の現在値
        excl_fn: triggered_id に応じた除外セットを返す関数
    Returns:
        更新された existing dict、または PreventUpdate
    """
    # triggered[0]["value"] から relayoutData を取得
    rd = None
    for t in ctx.triggered:
        if t.get("value") and isinstance(t["value"], dict):
            rd = t["value"]
            break
    if not rd:
        raise PreventUpdate

    # annotation 位置変更のみ処理（zoom/pan はスキップ）
    if not any(k.startswith("annotations[") for k in rd):
        raise PreventUpdate

    df = _interactive_data.get("plot_data")
    if df is None:
        raise PreventUpdate

    existing = dict(existing) if existing else {}
    excl = excl_fn(triggered_id)

    # 文字列 ID → UMAP 統合
    if isinstance(triggered_id, str):
        clusters = [c for c in sorted(df["Cluster"].unique(), key=_cluster_sort_key)
                    if str(c) not in excl]
        pos = _extract_annotation_positions_by_name(rd, clusters)
        if pos:
            umap_saved = dict(existing.get("umap_integrated", {}))
            _merge_label_positions(umap_saved, pos)
            existing["umap_integrated"] = umap_saved

    # dict ID → パターンマッチ（per_sample / spatial）
    elif isinstance(triggered_id, dict):
        graph_type = triggered_id.get("type")
        sample_name = str(triggered_id.get("index", ""))

        if graph_type in ("umap_per_sample_graph",):
            section = "umap_per_sample"
        elif graph_type in ("spatial_graph", "fs_spatial_graph"):
            section = "spatial"
        else:
            raise PreventUpdate

        sample_df = df[df["Sample"] == sample_name]
        if sample_df.empty:
            raise PreventUpdate
        clusters = [c for c in sorted(sample_df["Cluster"].unique(), key=_cluster_sort_key)
                    if str(c) not in excl]
        pos = _extract_annotation_positions_by_name(rd, clusters)
        if pos:
            section_saved = dict(existing.get(section, {}))
            sample_saved = dict(section_saved.get(sample_name, {}))
            _merge_label_positions(sample_saved, pos)
            section_saved[sample_name] = sample_saved
            existing[section] = section_saved
    else:
        raise PreventUpdate

    return existing


def _auto_save_label_positions(accumulated):
    """ラベル位置をJSONファイルへ自動保存する（relayoutDataベースのみ）。

    filelock + 原子的書き込みで、同一プロジェクト同時編集時のロストアップデートを防ぐ。
    """
    try:
        path = _get_label_positions_path()
        if not path:
            return
        # filelock 内で読込→マージ→atomic write を一貫実行
        _save_label_positions(
            accumulated or {},
            _interactive_data.get("rds_path"),
            _interactive_data.get("method"),
            merge=True,
        )
    except Exception as e:
        print(f"[LABEL] ラベル位置自動保存エラー: {e}")


def _excl_set(val):
    """exclude dropdown の値から除外セットを返す"""
    if not val:
        return set()
    return set(str(c) for c in val)


# 1a: 通常モード蓄積（静的 Input のみ → 常に発火可能）
@callback(
    Output("accumulated_label_positions", "data", allow_duplicate=True),
    [Input("interactive_umap_plot", "relayoutData"),
     Input({"type": "umap_per_sample_graph", "index": ALL}, "relayoutData"),
     Input({"type": "spatial_graph", "index": ALL}, "relayoutData")],
    [State("accumulated_label_positions", "data"),
     State("umap_exclude_cluster", "value"),
     State("spatial_exclude_cluster", "value")],
    prevent_initial_call=True,
)
def accumulate_annotation_positions_normal(umap_rd, umap_ps_rds,
                                            spatial_rds, existing,
                                            umap_exclude, spatial_exclude):
    """通常モード: relayoutData のアノテーション位置変更をリアルタイムで蓄積。"""
    triggered_id = ctx.triggered_id
    if not triggered_id:
        raise PreventUpdate

    def _get_excl(tid):
        if isinstance(tid, dict):
            gtype = tid.get("type")
            if gtype == "spatial_graph":
                return _excl_set(spatial_exclude)
            else:
                return _excl_set(umap_exclude)
        return _excl_set(umap_exclude)

    result = _accumulate_core(triggered_id, existing, _get_excl)
    _auto_save_label_positions(result)
    return result


# 1b: FS UMAP 蓄積（Input 1個: 文字列 ID → UMAP FS 時のみ存在）
@callback(
    Output("accumulated_label_positions", "data", allow_duplicate=True),
    Input("fs_umap_integrated_graph", "relayoutData"),
    [State("accumulated_label_positions", "data"),
     State("fs_umap_exclude_cluster", "value")],
    prevent_initial_call=True,
)
def accumulate_annotation_positions_fs_umap(fs_umap_rd, existing, fs_umap_exclude):
    """FS UMAP: relayoutData のアノテーション位置変更を蓄積。"""
    triggered_id = ctx.triggered_id
    if not triggered_id:
        raise PreventUpdate
    result = _accumulate_core(triggered_id, existing, lambda _: _excl_set(fs_umap_exclude))
    _auto_save_label_positions(result)
    return result


# 1c: FS Spatial 蓄積（Input 1個: パターンマッチ → Spatial FS 時のみ存在）
@callback(
    Output("accumulated_label_positions", "data", allow_duplicate=True),
    Input({"type": "fs_spatial_graph", "index": ALL}, "relayoutData"),
    [State("accumulated_label_positions", "data"),
     State("fs_spatial_exclude_cluster", "value")],
    prevent_initial_call=True,
)
def accumulate_annotation_positions_fs_spatial(fs_spatial_rds, existing, fs_spatial_exclude):
    """FS Spatial: relayoutData のアノテーション位置変更を蓄積。"""
    triggered_id = ctx.triggered_id
    if not triggered_id:
        raise PreventUpdate
    result = _accumulate_core(triggered_id, existing, lambda _: _excl_set(fs_spatial_exclude))
    _auto_save_label_positions(result)
    return result


# --- 保存コールバック: ヘルパー関数（PPT出力等で使用） ---


def _do_save_label_positions(accumulated, snapshot):
    """ラベル位置保存の共通ロジック。蓄積データ + DOM スナップショット → JSON。

    accumulated と snapshot から「patch dict」を構築し、save_label_positions
    （filelock + 原子的書き込み）にマージ保存させる。これにより同一プロジェクト
    複数タブ同時編集時のロストアップデートを防ぐ。
    """
    try:
        path = _get_label_positions_path()
        if not path:
            return no_update, "ラベル位置の保存に失敗しました（データ未読込）", True

        # patch を組み立てる（既存ファイル状態に依存せずに新規分のみまとめる）
        patch: dict = {}
        acc = accumulated or {}
        for section in ("umap_integrated", "umap_per_sample", "spatial"):
            acc_section = acc.get(section)
            if acc_section:
                patch[section] = dict(acc_section)

        # --- DOM スナップショットからの追加分 ---
        if snapshot and snapshot.get("timestamp"):
            def _anns_to_dict(anns_list):
                d = {}
                for a in (anns_list or []):
                    txt = (a.get("text") or "").strip()
                    if txt and a.get("x") is not None and a.get("y") is not None:
                        d[txt] = {"x": a["x"], "y": a["y"]}
                return d

            # UMAP 統合（FS 優先）
            umap_anns = snapshot.get("fs_umap_integrated") or []
            if not umap_anns:
                umap_anns = snapshot.get("umap_integrated") or []
            umap_dict = _anns_to_dict(umap_anns)
            if umap_dict:
                base = patch.get("umap_integrated", {})
                _merge_label_positions(base, umap_dict)
                patch["umap_integrated"] = base

            # サンプル別 UMAP
            for sample_name, anns in (snapshot.get("umap_per_sample") or {}).items():
                sd = _anns_to_dict(anns)
                if sd:
                    sect = patch.get("umap_per_sample", {})
                    ss = sect.get(sample_name, {})
                    _merge_label_positions(ss, sd)
                    sect[sample_name] = ss
                    patch["umap_per_sample"] = sect

            # Spatial（FS 優先）
            for src_key in ("spatial", "fs_spatial"):
                for sample_name, anns in (snapshot.get(src_key) or {}).items():
                    sd = _anns_to_dict(anns)
                    if sd:
                        sect = patch.get("spatial", {})
                        ss = sect.get(sample_name, {})
                        _merge_label_positions(ss, sd)
                        sect[sample_name] = ss
                        patch["spatial"] = sect

        # filelock + 原子的書き込みでマージ保存
        _save_label_positions(
            patch,
            _interactive_data.get("rds_path"),
            _interactive_data.get("method"),
            merge=True,
        )

        print(f"[LABEL] ラベル位置を保存しました: {path}")
        return datetime.now().isoformat(), "ラベル位置を保存しました", True
    except Exception as e:
        print(f"[LABEL] ラベル位置保存エラー: {e}")
        import traceback; traceback.print_exc()
        return no_update, f"ラベル位置の保存エラー: {e}", True
