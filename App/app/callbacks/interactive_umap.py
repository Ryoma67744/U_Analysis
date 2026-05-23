# =============================================================================
# MSI Analysis Application - Interactive UMAP Callbacks
# インタラクティブ解析 UMAP コールバック
#
# interactive_callbacks.py から分離された UMAP 可視化関連の
# ヘルパー関数・コールバックをまとめたモジュール。
# =============================================================================

import logging

import numpy as np
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import (Input, Output, State, callback, ctx, no_update, html, dcc,
                  ALL)
from dash.exceptions import PreventUpdate

from app.config import HIGHLIGHT_GRAY
from app.utils.color_utils import (
    cluster_sort_key as _cluster_sort_key,
    get_cluster_color_map as _get_cluster_color_map,
    get_merged_cluster_color_map as _get_merged_cluster_color_map,
    cluster_display_name as _cluster_display_name,
)
from app.utils.display_helpers import (
    display_name as _display_name,
    add_umap_arrows as _add_umap_arrows,
)
from app.utils.label_persistence import (
    merge_label_positions as _merge_label_positions,
)

logger = logging.getLogger("msi.interactive.umap")


# ---------------------------------------------------------------------------
# UMAP プロット — ヘルパー関数
# ---------------------------------------------------------------------------

def _build_umap_integrated_fig(df, color_by, highlight_clusters,
                                show_legend, show_labels, title=None,
                                marker_size=2, exclude_clusters=None,
                                label_size=14, saved_positions=None,
                                custom_colors=None, bg_opacity=0.1,
                                title_font_size=None, cluster_name_map=None):
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

    color_map = _get_cluster_color_map(df["Cluster"], custom_colors)

    if highlight_clusters and len(highlight_clusters) > 0:
        highlight_set = set(str(c) for c in highlight_clusters)
        mask_bg = ~df["Cluster"].astype(str).isin(highlight_set)
        if mask_bg.any():
            fig.add_trace(go.Scattergl(
                x=df.loc[mask_bg, "UMAP_1"],
                y=df.loc[mask_bg, "UMAP_2"],
                mode="markers",
                marker=dict(size=max(1, marker_size - 1), color=HIGHLIGHT_GRAY, opacity=bg_opacity),
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
                    name=_cluster_display_name(cl, cluster_name_map),
                    text=df.loc[mask, "CellID"],
                    hovertemplate="Cluster: %{meta}<br>%{text}<extra></extra>",
                    meta=[str(cl)] * mask.sum(),
                ))
    else:
        # 凡例ダブルクリック時に他クラスタを灰色で残すための背景 trace。
        # showlegend=False のため Plotly のダブルクリック操作対象外で、
        # 色付き trace が visible=False になっても下の灰色背景が残る。
        fig.add_trace(go.Scattergl(
            x=df["UMAP_1"], y=df["UMAP_2"],
            mode="markers",
            marker=dict(size=marker_size, color=HIGHLIGHT_GRAY, opacity=0.2),
            showlegend=False, hoverinfo="skip",
            name="_background_grey",
        ))
        color_col = color_by if color_by in df.columns else "Cluster"
        categories = sorted(df[color_col].unique(), key=_cluster_sort_key)
        cat_color_map = _get_cluster_color_map(categories, custom_colors)
        for cat in categories:
            mask = df[color_col] == cat
            rank = _cluster_sort_key(cat)[0] if str(cat).isdigit() else 1000
            fig.add_trace(go.Scattergl(
                x=df.loc[mask, "UMAP_1"],
                y=df.loc[mask, "UMAP_2"],
                mode="markers",
                marker=dict(size=marker_size, color=cat_color_map.get(str(cat), "#999999")),
                name=_cluster_display_name(cat, cluster_name_map),
                legendrank=rank,
                text=df.loc[mask, "CellID"],
                hovertemplate=f"{color_col}: {cat}<br>" + "%{text}<extra></extra>",
            ))

    if show_labels:
        centroids = df.groupby("Cluster").agg(
            cx=("UMAP_1", "mean"), cy=("UMAP_2", "mean"),
        ).reset_index()
        centroids = centroids.sort_values(
            "Cluster", key=lambda col: col.map(_cluster_sort_key)
        )
        for _, row in centroids.iterrows():
            cl_str = str(row["Cluster"])
            pos = (saved_positions or {}).get(cl_str, {})
            fig.add_annotation(
                x=pos.get("x", row["cx"]),
                y=pos.get("y", row["cy"]),
                text=_cluster_display_name(cl_str, cluster_name_map),
                showarrow=False,
                font=dict(size=label_size, color="black", family="Arial Black"),
            )

    layout_opts = dict(
        dragmode="select",
        showlegend=bool(show_legend),
        legend=dict(itemsizing="constant", font=dict(size=12), tracegroupgap=2),
        margin=dict(l=60, r=10,
                    t=max(40, (title_font_size or 14) + 15) if title else 30,
                    b=60),
        xaxis=dict(showgrid=False, showline=False, zeroline=False,
                   showticklabels=False, title=""),
        yaxis=dict(scaleanchor="x", showgrid=False, showline=False,
                   zeroline=False, showticklabels=False, title=""),
        plot_bgcolor="white",
    )
    if title:
        layout_opts["title"] = dict(
            text=title, font=dict(size=title_font_size or 14), x=0.5)
    fig.update_layout(**layout_opts)
    _add_umap_arrows(fig)
    return fig


_UMAP_PER_SAMPLE_CONFIG = {
    "scrollZoom": True,
    "edits": {"annotationPosition": True},
    "toImageButtonOptions": {"format": "png", "scale": 3},
}


def _build_umap_per_sample_graphs(df, color_map, highlight_clusters,
                                   show_labels, graph_height="300px",
                                   marker_size=2, exclude_clusters=None,
                                   label_size=11, saved_positions=None,
                                   show_legend=True, name_map=None,
                                   columns_per_row=0, cluster_name_map=None,
                                   collect_figures=None):
    """サンプル別UMAPのhtml.Divリストを生成（メイン/フルスクリーン共用）

    collect_figures: リストを渡すと (display_name, fig_dict) を追加する（一括保存用）
    """
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
                        name=_cluster_display_name(cl, cluster_name_map), showlegend=False,
                        legendgroup=_cluster_display_name(cl, cluster_name_map),
                    ))
        else:
            # 凡例ダブルクリック時に他クラスタを灰色で残すための背景 trace
            fig.add_trace(go.Scattergl(
                x=df_s["UMAP_1"], y=df_s["UMAP_2"],
                mode="markers",
                marker=dict(size=marker_size, color=HIGHLIGHT_GRAY, opacity=0.2),
                showlegend=False, hoverinfo="skip",
                name="_background_grey",
            ))
            for cl in sorted(df_s["Cluster"].unique(), key=_cluster_sort_key):
                mask_cl = df_s["Cluster"] == cl
                fig.add_trace(go.Scattergl(
                    x=df_s.loc[mask_cl, "UMAP_1"],
                    y=df_s.loc[mask_cl, "UMAP_2"],
                    mode="markers",
                    marker=dict(size=marker_size, color=color_map.get(str(cl), "#999999")),
                    name=_cluster_display_name(cl, cluster_name_map), showlegend=False,
                    legendgroup=_cluster_display_name(cl, cluster_name_map),
                ))

        # 凡例用ダミートレース（全クラスタ共通で統一凡例を表示）
        if show_legend:
            for cl in sorted(df["Cluster"].unique(), key=_cluster_sort_key):
                rank = _cluster_sort_key(cl)[0] if str(cl).isdigit() else 1000
                fig.add_trace(go.Scattergl(
                    x=[None], y=[None], mode="markers",
                    marker=dict(size=10, color=color_map.get(str(cl), "#999999")),
                    name=_cluster_display_name(cl, cluster_name_map), showlegend=True, legendrank=rank,
                    legendgroup=_cluster_display_name(cl, cluster_name_map),
                ))

        if show_labels:
            sample_pos = (saved_positions or {}).get(s, {})
            centroids = df_s.groupby("Cluster").agg(
                cx=("UMAP_1", "mean"), cy=("UMAP_2", "mean"),
            ).reset_index()
            centroids = centroids.sort_values(
                "Cluster", key=lambda col: col.map(_cluster_sort_key)
            )
            for _, row in centroids.iterrows():
                cl_str = str(row["Cluster"])
                pos = sample_pos.get(cl_str, {})
                fig.add_annotation(
                    x=pos.get("x", row["cx"]),
                    y=pos.get("y", row["cy"]),
                    text=_cluster_display_name(cl_str, cluster_name_map),
                    showarrow=False,
                    font=dict(size=label_size, color="black", family="Arial Black"),
                )

        display_s = _display_name(s, name_map)
        fig.update_layout(
            margin=dict(l=50, r=10, t=30, b=50),
            title=dict(text=display_s, font=dict(size=12), x=0.5),
            xaxis=dict(showgrid=False, showline=False, zeroline=False,
                       showticklabels=False, title=""),
            yaxis=dict(scaleanchor="x", showgrid=False, showline=False,
                       zeroline=False, showticklabels=False, title=""),
            plot_bgcolor="white",
            showlegend=bool(show_legend),
            legend=dict(itemsizing="constant", font=dict(size=9), tracegroupgap=1),
        )
        _add_umap_arrows(fig)

        if collect_figures is not None:
            collect_figures.append((f"UMAP_{display_s}", fig.to_dict()))

        cfg = dict(_UMAP_PER_SAMPLE_CONFIG)
        cfg["toImageButtonOptions"] = dict(cfg["toImageButtonOptions"],
                                           filename=f"UMAP_{display_s}")
        if columns_per_row:
            n_cols = columns_per_row
            gap_total = (n_cols - 1) * 15
            flex_basis = f"calc({100 / n_cols:.2f}% - {gap_total / n_cols:.1f}px)"
            min_w = "0"
        else:
            n_cols = len(samples)
            flex_basis = f"{max(20, 90 // n_cols)}%"
            min_w = "300px"
        graphs.append(
            html.Div(
                style={"flex": f"1 1 {flex_basis}", "minWidth": min_w,
                        "border": "1px solid #dee2e6", "borderRadius": "6px",
                        "padding": "5px", "backgroundColor": "#fff"},
                children=[
                    dcc.Graph(id={"type": "umap_per_sample_graph", "index": s},
                              figure=fig, style={"height": graph_height}, config=cfg),
                ],
            )
        )
    return graphs


def _get_merged_label_positions(accumulated_positions=None,
                                rds_path=None, method=None):
    """JSON ファイル + 蓄積 Store からマージしたラベル位置を返す。

    蓄積データは JSON より新しいため、蓄積データで JSON をオーバーライドする。

    rds_path / method を引数で渡せば、_interactive_data が未初期化でも
    JSON ファイルを正しく解決して読込できる (race condition 回避)。
    両方 None なら従来通り _interactive_data から解決。
    """
    from app.callbacks.interactive_callbacks import _load_label_positions
    if rds_path is not None:
        # 引数版で確実に読込 (_interactive_data 未初期化対策)
        from app.utils.label_persistence import (
            load_label_positions as _load_label_positions_util,
        )
        all_pos = _load_label_positions_util(rds_path, method) or {}
    else:
        all_pos = _load_label_positions()
    acc = accumulated_positions or {}
    for section in ("umap_integrated", "umap_per_sample", "spatial"):
        acc_section = acc.get(section)
        if not acc_section:
            continue
        saved_section = all_pos.get(section, {})
        if section == "umap_integrated":
            _merge_label_positions(saved_section, acc_section)
        else:
            for sample_name, pos_dict in acc_section.items():
                sample_saved = saved_section.get(sample_name, {})
                _merge_label_positions(sample_saved, pos_dict)
                saved_section[sample_name] = sample_saved
        all_pos[section] = saved_section
    return all_pos


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
     Input("seurat_rds_path_store", "data"),
     Input("fullscreen_closed_trigger", "data"),
     Input("custom_color_map_store", "data"),
     Input("cluster_name_map_store", "data"),
     Input("umap_merge_toggle", "value"),
     Input("umap_merge_color_mode", "value"),
     Input("interactive_accordion", "active_item")],
    State("accumulated_label_positions", "data"),
)
def update_umap_plot(color_by, highlight_clusters, show_legend, show_labels,
                     display_mode, marker_size, exclude_clusters, label_size,
                     rds_path, _fs_trigger, custom_colors, cluster_name_map,
                     merge_toggle, merge_color_mode, active_items,
                     accumulated_positions):
    active_list = active_items if isinstance(active_items, list) else ([active_items] if active_items else [])
    if "acc_umap" not in active_list:
        return no_update
    from app.callbacks.interactive_callbacks import _interactive_data, _set_active_key
    _set_active_key(rds_path)
    if display_mode == "per_sample":
        return go.Figure()
    df = _interactive_data.get("plot_data")
    if df is None:
        return go.Figure()

    # マージ表示切替
    plot_df = df
    effective_custom_colors = custom_colors
    if merge_toggle == "merged" and df is not None and "Cluster_merged" in df.columns:
        plot_df = df.copy()
        plot_df["Cluster"] = plot_df["Cluster_merged"]
        plot_df["UMAP_1"] = plot_df["UMAP_1_merged"]
        plot_df["UMAP_2"] = plot_df["UMAP_2_merged"]
        effective_custom_colors = _get_merged_cluster_color_map(
            plot_df["Cluster"], mode=merge_color_mode or "shade"
        )

    # rds_path / method を引数で明示することで、_interactive_data が
    # ContextVar 切替直後で未初期化の場合にも JSON を正しく読込む。
    method = _interactive_data.get("method")
    all_pos = _get_merged_label_positions(accumulated_positions,
                                          rds_path=rds_path, method=method)
    return _build_umap_integrated_fig(plot_df, color_by, highlight_clusters,
                                       show_legend, show_labels,
                                       marker_size=marker_size or 2,
                                       exclude_clusters=exclude_clusters,
                                       label_size=label_size or 14,
                                       saved_positions=all_pos.get("umap_integrated"),
                                       custom_colors=effective_custom_colors,
                                       cluster_name_map=cluster_name_map)


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
# マージ統合コントロールの表示/非表示
# ---------------------------------------------------------------------------

@callback(
    Output("umap_merge_controls_wrapper", "style"),
    [Input("seurat_rds_path_store", "data"),
     Input("fullscreen_closed_trigger", "data")],
)
def toggle_merge_controls(_rds_path, _fs_trigger):
    """マージデータの有無で UI 表示を制御"""
    from app.callbacks.interactive_callbacks import _interactive_data, _set_active_key
    _set_active_key(_rds_path)
    df = _interactive_data.get("plot_data")
    if df is not None and "Cluster_merged" in df.columns:
        return {"display": "block"}
    return {"display": "none"}


# ---------------------------------------------------------------------------
# サンプル別 UMAP 表示
# ---------------------------------------------------------------------------

@callback(
    [Output("umap_per_sample_container", "children"),
     Output("batch_umap_figures_store", "data")],
    [Input("umap_display_mode", "value"),
     Input("umap_highlight_cluster", "value"),
     Input("umap_show_labels", "value"),
     Input("umap_marker_size", "value"),
     Input("umap_exclude_cluster", "value"),
     Input("umap_label_size", "value"),
     Input("seurat_rds_path_store", "data"),
     Input("umap_show_legend", "value"),
     Input("sample_name_map_store", "data"),
     Input("fullscreen_closed_trigger", "data"),
     Input("custom_color_map_store", "data"),
     Input("umap_columns_per_row", "value"),
     Input("cluster_name_map_store", "data"),
     Input("interactive_accordion", "active_item")],
    State("accumulated_label_positions", "data"),
)
def update_umap_per_sample(display_mode, highlight_clusters, show_labels,
                            marker_size, exclude_clusters, label_size, rds_path,
                            show_legend, name_map, _fs_trigger, custom_colors,
                            columns_per_row, cluster_name_map, active_items,
                            accumulated_positions):
    """表示モード「サンプル別」の場合、各サンプルのUMAPを並列表示"""
    active_list = active_items if isinstance(active_items, list) else ([active_items] if active_items else [])
    if "acc_umap" not in active_list:
        return no_update, no_update
    from app.callbacks.interactive_callbacks import _interactive_data, _set_active_key
    _set_active_key(rds_path)
    if display_mode != "per_sample":
        return "", []
    df = _interactive_data.get("plot_data")
    if df is None:
        return "", []
    color_map = _get_cluster_color_map(df["Cluster"], custom_colors)
    method = _interactive_data.get("method")
    all_pos = _get_merged_label_positions(accumulated_positions,
                                          rds_path=rds_path, method=method)
    fig_dicts = []
    graphs = _build_umap_per_sample_graphs(df, color_map, highlight_clusters,
                                            show_labels, graph_height="300px",
                                            marker_size=marker_size or 2,
                                            exclude_clusters=exclude_clusters,
                                            label_size=label_size or 11,
                                            saved_positions=all_pos.get("umap_per_sample"),
                                            show_legend=bool(show_legend),
                                            name_map=name_map,
                                            columns_per_row=columns_per_row or 0,
                                            cluster_name_map=cluster_name_map,
                                            collect_figures=fig_dicts)
    return html.Div(
        style={"display": "flex", "flexWrap": "wrap", "gap": "15px", "marginTop": "10px"},
        children=graphs,
    ), fig_dicts


# ---------------------------------------------------------------------------
# UMAP 表示設定の永続化（簡易ビューアーとの共有用）
# ---------------------------------------------------------------------------

@callback(
    Output("umap_display_save_trigger", "data"),
    [Input("umap_marker_size", "value"),
     Input("umap_label_size", "value"),
     Input("umap_show_labels", "value"),
     Input("umap_columns_per_row", "value")],
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def save_umap_display_settings(marker_size, label_size, show_labels,
                                columns_per_row, rds_path):
    """UMAP表示パラメータの変更を interactive_settings.json に保存。

    簡易ビューアー (/lite/...) はこの値を読み出して同じ表示を再現する。
    """
    if not rds_path:
        raise PreventUpdate
    from app.callbacks.interactive_callbacks import _save_interactive_settings
    _save_interactive_settings("umap_display", {
        "marker_size": marker_size if marker_size is not None else 2,
        "label_size": label_size if label_size is not None else 14,
        "show_labels": bool(show_labels),
        "columns_per_row": columns_per_row if columns_per_row is not None else 0,
    })
    return no_update
