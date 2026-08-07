# =============================================================================
# MSI Analysis Application - Interactive UMAP Callbacks
# インタラクティブ解析 UMAP コールバック
#
# interactive_callbacks.py から分離された UMAP 可視化関連の
# ヘルパー関数・コールバックをまとめたモジュール。
# =============================================================================

import logging
import math

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
    facet_block as _facet_block,
    geom_uirevision as _geom_uirevision,
)
from app.utils.label_persistence import (
    merge_label_positions as _merge_label_positions,
)

logger = logging.getLogger("msi.interactive.umap")


# ---------------------------------------------------------------------------
# UMAP プロット — ヘルパー関数
# ---------------------------------------------------------------------------

def _rounded_umap(df):
    """表示用に UMAP 座標の桁を落とした df を返す (ver51.4)。

    Spatial / Feature は ver46.1 / ver51.3 で座標を丸めていたが、UMAP だけは
    `_round_for_display` を import すらしておらず、埋め込み座標が float64 の
    17 桁表記のまま流れていた (実測 5 万点で gzip 後 0.901MB → 0.308MB)。
    とくに Split View は**タイルごとに全点を灰色背景として再送する**設計なので、
    クラスタ数ぶん倍率がかかる。

    ★ 図の組み立て前に **1 回だけ** 丸める。トレースごとに部分集合で丸めると
      量子化幅がトレースごとに変わり、同じ図の中で点がずれる。

    ★ 元の df は変更しない (`assign` は他の列を共有する浅いコピー)。
      `interactive_loupe.umap_polygon_commit` は **生の** UMAP 座標で点内外判定を
      しており、そちらに丸めが漏れてはいけない。量子化幅は範囲の 1/100000 で、
      手でクリックする精度 (範囲の 1/500 程度) の約 200 倍細かいため、
      表示座標との食い違いが選択結果を変えることは無い。
    """
    if df is None or len(df) == 0 or "UMAP_1" not in df.columns:
        return df
    from app.callbacks.interactive_spatial import _round_for_display
    rx, ry = _round_for_display(df["UMAP_1"].to_numpy(dtype=float),
                                df["UMAP_2"].to_numpy(dtype=float))
    return df.assign(UMAP_1=rx, UMAP_2=ry)


def _build_umap_integrated_fig(df, color_by, highlight_clusters,
                                show_legend, show_labels, title=None,
                                marker_size=2, exclude_clusters=None,
                                label_size=14, saved_positions=None,
                                custom_colors=None, bg_opacity=0.1,
                                title_font_size=None, cluster_name_map=None,
                                uirevision=None):
    """統合UMAPのgo.Figureを生成（メイン/フルスクリーン共用）

    uirevision: 同値なら Plotly がズーム/パンを保持する (ver46.1)。埋め込み座標が
        変わる要素 (表示モード・マージ切替・除外クラスタ) のみを含めること。
    """
    fig = go.Figure()

    # 除外クラスタのフィルタリング
    if exclude_clusters:
        exclude_set = set(str(c) for c in exclude_clusters)
        df = df[~df["Cluster"].astype(str).isin(exclude_set)]
        if df.empty:
            fig.add_annotation(text="全クラスタが除外されています", showarrow=False,
                               xref="paper", yref="paper", x=0.5, y=0.5)
            return fig

    df = _rounded_umap(df)   # ver51.4: 表示用に座標の桁を落とす
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
                    # ver46.1: 同一文字列を点数ぶん並べた配列だった。Plotly は
                    # スカラーを全点にブロードキャストするので表示は変わらない。
                    meta=str(cl),
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
                # ver46.3: cat（クラスタ値 / サンプル名）はデータ由来なので
                # テンプレートに直接埋めず meta で渡す。color_col は
                # "Cluster"/"Sample" の固定値なので埋め込みのままで安全。
                meta=str(cat),
                hovertemplate=f"{color_col}: " + "%{meta}<br>%{text}<extra></extra>",
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
        # ver27.0: 範囲選択はクリックで頂点を置くポリゴン方式に統一。
        # ドラッグ=パン / ホイール=ズーム / クリック=頂点追加 とするため "pan"。
        # 選択は interactive_loupe の umap_polygon_* コールバックが担う。
        dragmode="pan",
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
        # ver46.1: マーカーサイズ・色・ラベル・凡例の変更でズーム/パンを保持する。
        uirevision=uirevision,
    )
    if title:
        layout_opts["title"] = dict(
            text=title, font=dict(size=title_font_size or 14), x=0.5)
    fig.update_layout(**layout_opts)
    # ver27.0: ポリゴン下書きの専用オーバーレイ trace（常に最後＝data[-1]）。
    # 空で追加し、interactive_loupe の umap_polygon_overlay が Patch で頂点を流し込む。
    fig.add_trace(go.Scattergl(
        x=[], y=[], mode="lines+markers", name="_umap_poly_draft",
        line=dict(color="#d6336c", width=2),
        marker=dict(color="#d6336c", size=7, symbol="circle"),
        showlegend=False, hoverinfo="skip", uid="umap_poly_draft",
    ))
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
                                   rows=0, cluster_name_map=None,
                                   collect_figures=None, legend_hidden=None,
                                   uirevision=None):
    """サンプル別UMAPのhtml.Divリストを生成（メイン/フルスクリーン共用）

    collect_figures: リストを渡すと (display_name, fig_dict) を追加する（一括保存用）
    legend_hidden: 共有凡例で「灰色化」したクラスタ。色付き trace を描かず灰色背景を残す
        (exclude と異なりセルは消さない, ver29.1)。
    """
    # 除外クラスタのフィルタリング（完全除去。灰色背景も消える）
    if exclude_clusters:
        exclude_set = set(str(c) for c in exclude_clusters)
        df = df[~df["Cluster"].astype(str).isin(exclude_set)]
        if df.empty:
            return [html.Div("全クラスタが除外されています", className="text-muted small mt-2")]
    df = _rounded_umap(df)   # ver51.4: 表示用に座標の桁を落とす
    # 灰色化クラスタ（色付き trace のみ非表示。灰色背景は残す）
    legend_hidden_set = {str(c) for c in (legend_hidden or [])}

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
                if str(cl) in legend_hidden_set:
                    continue  # 凡例で灰色化 → 色付き trace を描かない（灰色背景は残る）
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
                if str(cl) in legend_hidden_set:
                    continue  # 凡例で灰色化 → 色付き trace を描かない（灰色背景は残る）
                mask_cl = df_s["Cluster"] == cl
                fig.add_trace(go.Scattergl(
                    x=df_s.loc[mask_cl, "UMAP_1"],
                    y=df_s.loc[mask_cl, "UMAP_2"],
                    mode="markers",
                    marker=dict(size=marker_size, color=color_map.get(str(cl), "#999999")),
                    name=_cluster_display_name(cl, cluster_name_map), showlegend=False,
                    legendgroup=_cluster_display_name(cl, cluster_name_map),
                ))

        # 凡例用ダミートレース（出力PNG用）。この図に存在するクラスタは色付き、
        # 欠番は「空白スロット」(透明マーカー＋空白名)で位置を保持＝全図で番号の縦位置がそろう。
        # 画面表示では下流で showlegend=False にし、上部の共有凡例(1つ)に集約する。
        if show_legend:
            present = set(df_s["Cluster"].astype(str).unique())
            for cl in sorted(df["Cluster"].unique(), key=_cluster_sort_key):
                rank = _cluster_sort_key(cl)[0] if str(cl).isdigit() else 1000
                if str(cl) in present:
                    fig.add_trace(go.Scattergl(
                        x=[None], y=[None], mode="markers",
                        marker=dict(size=10, color=color_map.get(str(cl), "#999999")),
                        name=_cluster_display_name(cl, cluster_name_map),
                        showlegend=True, legendrank=rank,
                        legendgroup=_cluster_display_name(cl, cluster_name_map),
                    ))
                else:
                    fig.add_trace(go.Scattergl(
                        x=[None], y=[None], mode="markers",
                        marker=dict(size=10, color="rgba(0,0,0,0)"),
                        name=" ", showlegend=True, legendrank=rank,
                        legendgroup=f"_blank_{cl}",
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
            # ver46.1: 見た目だけの変更ではズーム/パンを保持する。
            uirevision=(f"{uirevision}|{s}" if uirevision else None),
        )

        # 出力(一括保存/サムネ)は各図に凡例を残す → 先にスナップショット。
        if collect_figures is not None:
            collect_figures.append((f"UMAP_{display_s}", fig.to_dict()))
        # 画面表示は per-tile 凡例オフ（上部の共有凡例に集約）。
        fig.update_layout(showlegend=False)

        cfg = dict(_UMAP_PER_SAMPLE_CONFIG)
        cfg["toImageButtonOptions"] = dict(cfg["toImageButtonOptions"],
                                           filename=f"UMAP_{display_s}")
        if rows:
            # 行数指定: 1 行あたりの列数 = ceil(サンプル数 / 行数)（最大 rows 行）
            n_cols = max(1, math.ceil(len(samples) / rows))
            gap_total = (n_cols - 1) * 15
            flex_basis = f"calc({100 / n_cols:.2f}% - {gap_total / n_cols:.1f}px)"
            min_w = "0"
        else:
            n_cols = len(samples)
            flex_basis = f"{max(20, 90 // n_cols)}%"
            min_w = "300px"
        graphs.append(
            html.Div(
                className="facet-tile",
                style={"flex": f"1 1 {flex_basis}", "minWidth": min_w},
                children=[
                    dcc.Graph(id={"type": "umap_per_sample_graph", "index": s},
                              figure=fig, style={"height": graph_height}, config=cfg),
                ],
            )
        )
    return graphs


def _build_umap_facet_graphs(df, facets, color_map, marker_size=2,
                             rows=0, cluster_name_map=None,
                             graph_height="300px", collect_figures=None,
                             legend_hidden=None):
    """汎用 Split View: facets=[(label, mask_or_cellids), ...]。

    各タイルは「全細胞を淡灰の背景」+「当該ファセットの細胞を Cluster 色」で描き、
    全タイルで軸範囲を共有 (synchronized small multiples)。Loupe の Split View 相当。
    facet 要素: ブール mask (Cluster 分割) もしくは CellID 集合 (選択グループ分割)。
    legend_hidden: 共有凡例で灰色化したクラスタ。色付き trace を描かず灰色背景は残す。
    """
    if df is None or len(df) == 0 or not facets:
        return [html.Div("表示できるデータがありません", className="text-muted small mt-2")]
    legend_hidden_set = {str(c) for c in (legend_hidden or [])}

    df = _rounded_umap(df)   # ver51.4: 表示用に座標の桁を落とす
    x_all, y_all = df["UMAP_1"], df["UMAP_2"]
    pad_x = (float(x_all.max()) - float(x_all.min())) * 0.03 or 1.0
    pad_y = (float(y_all.max()) - float(y_all.min())) * 0.03 or 1.0
    xr = [float(x_all.min()) - pad_x, float(x_all.max()) + pad_x]
    yr = [float(y_all.min()) - pad_y, float(y_all.max()) + pad_y]

    n = len(facets)
    graphs = []
    for label, sel in facets:
        if isinstance(sel, (set, list)):
            mask = df["CellID"].astype(str).isin({str(s) for s in sel}).to_numpy()
        else:
            mask = np.asarray(sel)
        fig = go.Figure()
        # 全体を淡灰で背景表示（位置の文脈を保つ）
        fig.add_trace(go.Scattergl(
            x=x_all, y=y_all, mode="markers",
            marker=dict(size=marker_size, color=HIGHLIGHT_GRAY, opacity=0.15),
            showlegend=False, hoverinfo="skip", name="_bg"))
        sub = df[mask]
        for cl in sorted(sub["Cluster"].unique(), key=_cluster_sort_key):
            if str(cl) in legend_hidden_set:
                continue  # 凡例で灰色化 → 色付き trace を描かない（灰色背景は残る）
            m2 = (sub["Cluster"] == cl)
            fig.add_trace(go.Scattergl(
                x=sub.loc[m2, "UMAP_1"], y=sub.loc[m2, "UMAP_2"], mode="markers",
                marker=dict(size=marker_size + 1, color=color_map.get(str(cl), "#999999")),
                name=_cluster_display_name(cl, cluster_name_map), showlegend=False,
                legendgroup=_cluster_display_name(cl, cluster_name_map)))
        fig.update_layout(
            margin=dict(l=40, r=10, t=28, b=40),
            title=dict(text=f"{label} ({int(mask.sum())})", font=dict(size=12), x=0.5),
            xaxis=dict(showgrid=False, showline=False, zeroline=False,
                       showticklabels=False, title="", range=xr),
            yaxis=dict(scaleanchor="x", showgrid=False, showline=False,
                       zeroline=False, showticklabels=False, title="", range=yr),
            plot_bgcolor="white", showlegend=False)
        if collect_figures is not None:
            collect_figures.append((f"UMAP_facet_{label}", fig.to_dict()))
        cfg = dict(_UMAP_PER_SAMPLE_CONFIG)
        cfg["toImageButtonOptions"] = dict(cfg["toImageButtonOptions"],
                                           filename=f"UMAP_facet_{label}")
        if rows:
            # 行数指定: 1 行あたりの列数 = ceil(facet 数 / 行数)（最大 rows 行）
            n_cols = max(1, math.ceil(n / rows))
            gap_total = (n_cols - 1) * 15
            flex_basis = f"calc({100 / n_cols:.2f}% - {gap_total / n_cols:.1f}px)"
            min_w = "0"
        else:
            n_cols = min(n, 4)
            flex_basis = f"{max(20, 90 // max(1, n_cols))}%"
            min_w = "250px"
        graphs.append(html.Div(
            className="facet-tile",
            style={"flex": f"1 1 {flex_basis}", "minWidth": min_w},
            children=[dcc.Graph(figure=fig, style={"height": graph_height}, config=cfg)]))
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
    from app.callbacks.interactive_callbacks import (
        _interactive_data, _set_active_key, accordion_toggle_is_noop)
    # ver46.1: 他セクションの開閉だけで統合 UMAP を作り直さない
    if accordion_toggle_is_noop("acc_umap_integrated", None, rds_path,
                                active_items, ctx.triggered_id):
        return no_update
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
        # ver46.1: 全列コピーをやめ、UMAP 描画が使う列だけを組み立てる。
        _cols = [c for c in ("Sample", "CellID") if c in df.columns]
        plot_df = df[_cols].copy()
        plot_df["Cluster"] = df["Cluster_merged"].to_numpy()
        plot_df["UMAP_1"] = df["UMAP_1_merged"].to_numpy()
        plot_df["UMAP_2"] = df["UMAP_2_merged"].to_numpy()
        effective_custom_colors = _get_merged_cluster_color_map(
            plot_df["Cluster"], mode=merge_color_mode or "shade"
        )

    # rds_path / method を引数で明示することで、_interactive_data が
    # ContextVar 切替直後で未初期化の場合にも JSON を正しく読込む。
    method = _interactive_data.get("method")
    all_pos = _get_merged_label_positions(accumulated_positions,
                                          rds_path=rds_path, method=method)
    # ver46.1: 座標(埋め込み)が変わる要素のみを uirevision に含める。除外クラスタは
    # 点集合が変わり autorange も変わるためリセット対象に含める。
    uirev = _geom_uirevision("umap", merge_toggle,
                             ",".join(sorted(str(c) for c in (exclude_clusters or []))))
    return _build_umap_integrated_fig(plot_df, color_by, highlight_clusters,
                                       show_legend, show_labels,
                                       marker_size=marker_size or 2,
                                       exclude_clusters=exclude_clusters,
                                       label_size=label_size or 14,
                                       saved_positions=all_pos.get("umap_integrated"),
                                       custom_colors=effective_custom_colors,
                                       uirevision=uirev,
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
    Output("umap_per_sample_container", "children"),
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
     Input("umap_rows_per_view", "value"),
     Input("cluster_name_map_store", "data"),
     Input("interactive_accordion", "active_item"),
     Input("umap_facet_by", "value"),
     Input("umap_legend_hidden_store", "data")],
    [State("accumulated_label_positions", "data"),
     State("selection_groups_store", "data"),
     State("session_id_store", "data")],
)
def update_umap_per_sample(display_mode, highlight_clusters, show_labels,
                            marker_size, exclude_clusters, label_size, rds_path,
                            show_legend, name_map, _fs_trigger, custom_colors,
                            rows, cluster_name_map, active_items,
                            facet_by, legend_hidden, accumulated_positions,
                            selection_groups, session_id=None):
    """表示モード「サンプル別」(=分割表示) の場合、facet_by 基準で分割表示する。"""
    active_list = active_items if isinstance(active_items, list) else ([active_items] if active_items else [])
    if "acc_umap" not in active_list:
        return no_update
    from app.callbacks.interactive_callbacks import (
        _interactive_data, _set_active_key, accordion_toggle_is_noop,
        set_export_figures)
    # ver46.1: 他セクションの開閉だけで全図を作り直さない
    if accordion_toggle_is_noop("acc_umap_facet", session_id, rds_path,
                                active_items, ctx.triggered_id):
        return no_update
    _set_active_key(rds_path)

    def _finish(children, fig_dicts):
        """ver46.1: 一括保存/サムネ用 figure はサーバ側に保持し、ブラウザへは送らない。"""
        set_export_figures("umap", session_id, rds_path, fig_dicts)
        return children

    if display_mode != "per_sample":
        return _finish("", [])
    df = _interactive_data.get("plot_data")
    if df is None:
        return _finish("", [])
    color_map = _get_cluster_color_map(df["Cluster"], custom_colors)
    # ver46.1: 埋め込み座標/点集合が変わる要素のみ uirevision に含める。
    uirev = _geom_uirevision(
        "umap_facet", facet_by,
        ",".join(sorted(str(c) for c in (exclude_clusters or []))))

    # --- Split View: facet_by が Cluster / 選択グループ なら汎用ファセット描画 ---
    facet_by = facet_by or "Sample"
    if facet_by == "Cluster":
        if exclude_clusters:
            ex = {str(c) for c in exclude_clusters}
            df = df[~df["Cluster"].astype(str).isin(ex)]
        cats = sorted(df["Cluster"].unique(), key=_cluster_sort_key)
        facets = [(_cluster_display_name(c, cluster_name_map),
                   (df["Cluster"].astype(str) == str(c)).to_numpy()) for c in cats]
        fig_dicts = []
        graphs = _build_umap_facet_graphs(
            df, facets, color_map, marker_size=marker_size or 2,
            rows=rows or 0, cluster_name_map=cluster_name_map,
            collect_figures=fig_dicts, legend_hidden=legend_hidden)
        return _finish(_facet_block(
            graphs, color_map, cluster_name_map=cluster_name_map,
            show_legend=bool(show_legend), legend_id="umap_shared_legend",
            hidden=legend_hidden, outer_style={"marginTop": "10px"}), fig_dicts)
    if facet_by == "group":
        groups = (selection_groups or {}).get("groups", [])
        if not groups:
            return _finish(html.Div(
                "選択グループがありません（UMAP の「選択グループ」で保存してください）。",
                className="text-muted small mt-2"), [])
        facets = [(g.get("name", ""), set(g.get("cell_ids", []))) for g in groups]
        fig_dicts = []
        graphs = _build_umap_facet_graphs(
            df, facets, color_map, marker_size=marker_size or 2,
            rows=rows or 0, cluster_name_map=cluster_name_map,
            collect_figures=fig_dicts, legend_hidden=legend_hidden)
        return _finish(_facet_block(
            graphs, color_map, cluster_name_map=cluster_name_map,
            show_legend=bool(show_legend), legend_id="umap_shared_legend",
            hidden=legend_hidden, outer_style={"marginTop": "10px"}), fig_dicts)

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
                                            rows=rows or 0,
                                            cluster_name_map=cluster_name_map,
                                            collect_figures=fig_dicts,
                                            legend_hidden=legend_hidden,
                                            uirevision=uirev)
    return _finish(_facet_block(
        graphs, color_map, cluster_name_map=cluster_name_map,
        show_legend=bool(show_legend), legend_id="umap_shared_legend",
        hidden=legend_hidden,
        outer_style={"marginTop": "10px"},
    ), fig_dicts)


# ---------------------------------------------------------------------------
# UMAP 表示設定の永続化（簡易ビューアーとの共有用）
# ---------------------------------------------------------------------------

@callback(
    Output("umap_display_save_trigger", "data"),
    [Input("umap_marker_size", "value"),
     Input("umap_label_size", "value"),
     Input("umap_show_labels", "value"),
     Input("umap_rows_per_view", "value"),
     Input("umap_exclude_cluster", "value"),
     Input("umap_show_legend", "value"),
     Input("umap_color_by", "value")],
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def save_umap_display_settings(marker_size, label_size, show_labels,
                                rows, exclude_cluster,
                                show_legend, color_by, rds_path):
    """UMAP表示パラメータの変更を interactive_settings.json に保存。

    簡易ビューアー (/lite/...) はこの値を読み出して同じ表示を再現する。
    ver3.5: exclude_cluster / show_legend / color_by も保存対象に追加。
    """
    if not rds_path:
        raise PreventUpdate
    from app.callbacks.interactive_callbacks import _save_interactive_settings
    _save_interactive_settings("umap_display", {
        "marker_size": marker_size if marker_size is not None else 2,
        "label_size": label_size if label_size is not None else 14,
        "show_labels": bool(show_labels),
        "rows_per_view": rows if rows is not None else 0,
        "exclude_cluster": list(exclude_cluster) if exclude_cluster else [],
        "show_legend": bool(show_legend) if show_legend is not None else True,
        "color_by": color_by or "Cluster",
    })
    return no_update
