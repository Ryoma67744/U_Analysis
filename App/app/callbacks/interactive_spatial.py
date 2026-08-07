# =============================================================================
# MSI Analysis Application - Interactive Spatial Mapping Callbacks
# インタラクティブ解析 Spatial Mapping コールバック
#
# interactive_callbacks.py から分離された Spatial Mapping 関連の
# ヘルパー関数・コールバックをまとめたモジュール。
# =============================================================================

import logging
import math

import numpy as np
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import (Input, Output, State, callback, ctx, no_update, html, dcc,
                  ALL, MATCH, ClientsideFunction, clientside_callback)
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
    facet_block as _facet_block,
    transform_uirevision as _transform_uirevision,
)
from app.callbacks.interactive_hne_bg import build_hne_overlay_fig as _build_hne_overlay_fig

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


# ---------------------------------------------------------------------------
# トレースの「見た目の役割」タグ (ver46.1)
# ---------------------------------------------------------------------------
# マーカーサイズ / スポット不透明度のスライダーは図のデータを変えないので、
# サーバで全図を作り直さず clientside の Plotly.restyle で更新する
# (assets/spatial_restyle.js)。JS 側がトレース構成を仮定しないで済むよう、
# 各トレースに「基準サイズからの差分」と「スポット不透明度を適用するか」を
# meta として持たせる。凡例ダミーなど触ってほしくないトレースには付けない。
#
#   dsz : 基準マーカーサイズからの差分 (0 or 1)
#   op  : True なら spot_opacity スライダーの対象
_MSZ_BG = {"dsz": 0, "op": False}          # TIC / 灰色の背景
_MSZ_SPOT = {"dsz": 0, "op": True}          # クラスタ色スポット
_MSZ_SPOT_PLUS1 = {"dsz": 1, "op": True}    # ハイライト時のクラスタ色スポット
_MSZ_PLUS1 = {"dsz": 1, "op": False}        # 選択セルの赤ハイライト


def _cluster_names_for(cluster_str, cluster_name_map):
    """クラスタ列 (str) を表示名の list に変換する（ver46.1）。

    従来は点ごとに `_cluster_display_name()` を呼ぶ内包表記だった（10 万点なら
    10 万回の Python 関数呼び出し）。ユニーク値ぶんだけ解決して map するので、
    出力は同一のままクラスタ数ぶんの呼び出しで済む。
    """
    lookup = {c: _cluster_display_name(c, cluster_name_map)
              for c in cluster_str.unique()}
    return cluster_str.map(lookup).tolist()


def _round_for_display(x, y):
    """表示用に座標の有効桁を落とす（JSON 転送量の削減、ver46.1）。

    回転/反転をかけると座標は float64 の 17 桁表記になり、JSON では 1 点あたり
    約 19 バイトになる。データ範囲の約 1/100000 の量子化まで丸めれば表示上の差は
    視認できず、転送量は 2〜3 倍小さくなる。

    範囲に対する相対量で丸めるため、座標の単位（画素 / µm / mm）に依存しない。

    figure を作る経路（画面表示・一括保存 PNG・PPTX）では一貫してこれを通す。
    画面と出力で同じ figure になる方が「一括保存が画面と一致する」既存の前提に
    合うため。量子化は範囲の 1/100000 なので、PPTX の 3 倍解像度 (約 3000px)
    でも 0.1 画素未満であり見た目に影響しない。

    注意: **幾何計算には使わないこと**。H&E 射影 (`msi_to_hne_px`) や
    `hne_overlay.apply_rotation` との一致検証には `_transform_coords` の生値を使う。
    """
    finite_x = x[np.isfinite(x)] if getattr(x, "size", 0) else x
    finite_y = y[np.isfinite(y)] if getattr(y, "size", 0) else y
    if getattr(finite_x, "size", 0) == 0 or getattr(finite_y, "size", 0) == 0:
        return x, y
    span = max(float(finite_x.max() - finite_x.min()),
               float(finite_y.max() - finite_y.min()))
    if not np.isfinite(span) or span <= 0:
        return x, y
    decimals = int(np.clip(5 - np.floor(np.log10(span)), 0, 12))
    return np.round(x, decimals), np.round(y, decimals)


def _round_values_for_display(v):
    """表示用に強度値の有効桁を落とす（JSON 転送量の削減、ver51.3）。

    `_round_for_display` と同じ「範囲に対する相対量で丸める」考え方を
    marker.color に適用する。座標だけ丸めて色を丸めていなかったため、
    強度は float64 の 17 桁表記のまま送られていた（実測 50,000 点で
    gzip 後 0.45MB。丸めると 0.13MB）。

    画面上の色は colorscale の段階に落ちるので、範囲の 1/100000 まで丸めても
    色は 1 段も動かない。

    強度は桁が大きく振れる（1e-3 〜 1e6）ため、固定小数での丸めは使わない。
    範囲基準にすることで単位にも強度スケールにも依存しなくなる。

    ★ ただし **小数 4 桁より粗くはしない**。hover は `%{marker.color:.4f}` で
      4 桁を出すので、範囲だけで決めると強度が大きいデータ（範囲 2e4 なら
      小数 1 桁）で「1234.5000」のように**存在しない桁をゼロで捏造して表示する**
      ことになる。値の誤差自体は範囲の 1/400000 で無視できるが、画面に出す数字が
      実測値と違うのは別問題。転送量より表示の正しさを優先する。

    注意: **統計・DEG・エクスポートの数値には使わないこと**。これは
    「画面へ送る figure の色配列」専用。
    """
    arr = np.asarray(v)
    # ★ 整数列（TotalCount など）はそのまま返す。float に上げて丸めると
    #   12345 が 12345.0 になり、JSON では**むしろ長くなる**。
    if arr.dtype.kind in ("i", "u", "b"):
        return arr
    arr = np.asarray(arr, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return arr
    span = float(finite.max() - finite.min())
    if not np.isfinite(span) or span <= 0:
        return arr
    # hover の表示桁 (.4f) を下限にする。詳細は docstring の ★。
    decimals = int(np.clip(max(4.0, 5 - np.floor(np.log10(span))), 0, 12))
    return np.round(arr, decimals)


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
                               cluster_name_map=None, scale_factor=1.0,
                               legend_hidden=None, spot_opacity=1.0,
                               uirevision=None):
    """単一サンプルのSpatial Mapping figureを生成。

    legend_hidden: 共有凡例で灰色化したクラスタ。色付き trace を描かず灰色背景は残す
        (exclude と異なりセルは消さない, ver29.1)。
    spot_opacity: クラスタ色スポットの不透明度 (0–1, ver31.0)。下げると背後の TIC 背景が透ける
        (既定 1.0＝従来どおり不透明)。TIC 背景・選択ハイライトには適用しない。
    uirevision: 同値なら Plotly がズーム/パンを保持する (ver46.1)。座標そのものが
        変わる要素 (サンプル・回転・反転) だけを含めた文字列を渡すこと。
    """
    # 除外クラスタのフィルタリング（完全除去。灰色背景も消える）
    if exclude_clusters:
        exclude_set = set(str(c) for c in exclude_clusters)
        df_sample = df_sample[~df_sample["Cluster"].astype(str).isin(exclude_set)]
        if df_sample.empty:
            fig = go.Figure()
            fig.add_annotation(text="全クラスタが除外されています", showarrow=False,
                               xref="paper", yref="paper", x=0.5, y=0.5)
            return fig
    # 灰色化クラスタ（色付き trace のみ非表示。灰色背景は残す）
    legend_hidden_set = {str(c) for c in (legend_hidden or [])}
    fig = go.Figure()

    # 座標の取得と変換適用（反転+回転）
    raw_x = df_sample["SpatialX"].values
    raw_y = -df_sample["SpatialY"].values  # Y軸反転
    plot_x, plot_y = _transform_coords(raw_x, raw_y, rotation_deg,
                                        flip_h=flip_h, flip_v=flip_v)
    plot_x, plot_y = _round_for_display(plot_x, plot_y)
    # ver46.1: クラスタ列の文字列化はクラスタ数ぶんのループ内で毎回行われていた
    # （30 クラスタ × 5 万行 = 150 万回の変換）。1 回だけ作って使い回す。
    cluster_str = df_sample["Cluster"].astype(str)
    cluster_str_values = cluster_str.values

    # 「自動」時のマーカーサイズ（データ密度ベース＝隣接点が接するサイズ）。
    # ver46.1: 従来は marker_size<=0 のときだけ計算していたが、常に計算して
    # layout.meta に載せる。マーカーサイズスライダーは clientside の
    # Plotly.restyle で更新するため、「自動」に戻されたときの基準値を
    # サーバに問い合わせずブラウザ側で復元できる必要があるため。
    # （計算内容は従来のインライン実装と同一。重複を解消して helper に一本化した）
    auto_msz = _calc_zero_gap_marker_size(
        plot_x, plot_y,
        render_height=render_height or 310,
        scale_factor=scale_factor,
    )
    if marker_size <= 0:
        marker_size = auto_msz

    if selected_cell_ids:
        mask_selected = df_sample["CellID"].isin(selected_cell_ids).values
        mask_bg = ~mask_selected
        if mask_bg.any():
            if "TotalCount" in df_sample.columns:
                tc_values = df_sample["TotalCount"].values[mask_bg]
                bg_marker = dict(size=marker_size, symbol="square",
                                 # ver51.4: TIC も丸める。hoverinfo="skip" なので
                                 # 表示桁の心配は無い (Feature 側と違う点)。
                                 color=_round_values_for_display(tc_values),
                                 colorscale="Greys",
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
                meta=_MSZ_BG,
            ))
        if mask_selected.any():
            fig.add_trace(go.Scattergl(
                x=plot_x[mask_selected],
                y=plot_y[mask_selected],
                mode="markers",
                marker=dict(size=marker_size + 1, symbol="square", color="red"),
                name=f"Selected ({mask_selected.sum()})",
                meta=_MSZ_PLUS1,
            ))
    elif highlight_clusters and len(highlight_clusters) > 0:
        highlight_set = set(str(c) for c in highlight_clusters)
        # 非ハイライトクラスタをTIC or 灰色で描画
        mask_bg = ~cluster_str.isin(highlight_set)
        if mask_bg.values.any():
            if "TotalCount" in df_sample.columns:
                tc_values = df_sample["TotalCount"].values[mask_bg.values]
                bg_marker = dict(size=marker_size, symbol="square",
                                 # ver51.4: TIC も丸める。hoverinfo="skip" なので
                                 # 表示桁の心配は無い (Feature 側と違う点)。
                                 color=_round_values_for_display(tc_values),
                                 colorscale="Greys",
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
                meta=_MSZ_BG,
            ))
        # ハイライトクラスタを色付きで描画
        for cl in sorted(highlight_clusters, key=lambda x: _cluster_sort_key(x), reverse=True):
            mask = (cluster_str_values == str(cl))
            if mask.any():
                fig.add_trace(go.Scattergl(
                    x=plot_x[mask],
                    y=plot_y[mask],
                    mode="markers",
                    marker=dict(size=marker_size + 1, symbol="square",
                                color=color_map.get(str(cl), "#999999"),
                                opacity=spot_opacity),
                    name=_cluster_display_name(cl, cluster_name_map),
                    legendgroup=_cluster_display_name(cl, cluster_name_map),
                    meta=_MSZ_SPOT_PLUS1,
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
                    # ver51.4: 同上。ここが update_spatial_plots の本経路。
                    color=_round_values_for_display(
                        df_sample["TotalCount"].values),
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
                meta=_MSZ_BG,
            ))
            # 凡例リンク用: クラスタ別個別トレース（legendgroup でダミーと連動）
            for cl in sorted(df_sample["Cluster"].unique(), key=_cluster_sort_key):
                if str(cl) in legend_hidden_set:
                    continue  # 凡例で灰色化 → 色付き trace を描かない（灰色背景は残る）
                mask = (cluster_str_values == str(cl))
                if mask.any():
                    fig.add_trace(go.Scattergl(
                        x=plot_x[mask], y=plot_y[mask], mode="markers",
                        marker=dict(size=marker_size, symbol="square",
                                    color=color_map.get(str(cl), "#999999"),
                                    opacity=spot_opacity),
                        # ver46.1: 同一文字列を点数ぶん並べた配列だった（5 万点で
                        # 約 0.7MB の純粋な無駄）。
                        #
                        # ver46.2: `text=<スカラー>` + `hovertemplate="%{text}"` は
                        # **動かない**。plotly.py はスカラーをそのまま直列化するが、
                        # plotly.js は scattergl の `%{text}` をスカラーから解決できず、
                        # ツールチップに文字列 "%{text}" がそのまま出ていた。
                        # `hovertext`(スカラー可) + `hoverinfo="text"` なら
                        # 全点に同じ文字列が出る（配列を作らずに済む点は同じ）。
                        # テンプレート解釈が入らないので、ユーザーが変更できる
                        # クラスタ名に "%{...}" が含まれていても安全。
                        hovertext=_cluster_display_name(cl, cluster_name_map),
                        hoverinfo="text",
                        name=_cluster_display_name(cl, cluster_name_map), showlegend=False,
                        legendgroup=_cluster_display_name(cl, cluster_name_map),
                        meta=_MSZ_SPOT,
                    ))
        elif cluster_to_idx is not None and discrete_cscale is not None:
            # 数値インデックス + discrete colorscale 方式
            n_clusters = max(len(cluster_to_idx), 1)
            point_values = cluster_str.map(lambda c: cluster_to_idx.get(c, 0)).to_numpy()
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
                    opacity=spot_opacity,
                ),
                text=_cluster_names_for(cluster_str, cluster_name_map),
                hovertemplate="%{text}<extra></extra>",
                showlegend=False,
                meta=_MSZ_SPOT,
            ))
        else:
            # フォールバック: HEX文字列配列方式
            point_colors = cluster_str.map(lambda c: color_map.get(c, "#999999")).tolist()
            fig.add_trace(go.Scattergl(
                x=plot_x, y=plot_y, mode="markers",
                marker=dict(size=marker_size, symbol="square", color=point_colors,
                            opacity=spot_opacity),
                text=_cluster_names_for(cluster_str, cluster_name_map),
                hovertemplate="%{text}<extra></extra>",
                showlegend=False,
                meta=_MSZ_SPOT,
            ))
        # 凡例用ダミートレース。全クラスタ分のスロットを作り、この図に存在するクラスタは
        # 色付き、欠番は「空白スロット」(透明＋空白名)で位置を保持＝全図で番号の縦位置がそろう。
        if embed_legend:
            present = set(cluster_str.unique())
            for cl in sorted(color_map.keys(), key=_cluster_sort_key):
                rank = _cluster_sort_key(cl)[0] if str(cl).isdigit() else 1000
                if str(cl) in present:
                    fig.add_trace(go.Scattergl(
                        x=[None], y=[None], mode="markers",
                        marker=dict(size=10, symbol="square",
                                    color=color_map.get(str(cl), "#999999")),
                        name=_cluster_display_name(cl, cluster_name_map),
                        showlegend=True, legendrank=rank,
                        legendgroup=_cluster_display_name(cl, cluster_name_map),
                    ))
                else:
                    fig.add_trace(go.Scattergl(
                        x=[None], y=[None], mode="markers",
                        marker=dict(size=10, symbol="square", color="rgba(0,0,0,0)"),
                        name=" ", showlegend=True, legendrank=rank,
                        legendgroup=f"_blank_{cl}",
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
        # ver46.1: マーカーサイズ・色・ラベル・凡例の変更ではユーザーのズーム/パンを
        # 保持する。座標が変わる操作 (サンプル切替・回転・反転) では呼び出し側が
        # 別の値を渡すのでリセットされる。
        uirevision=uirevision,
        # ver46.1: clientside restyle 用のタイル情報。
        # kind でどのスライダーの対象かを判別し、auto_msz を「自動」時の基準サイズにする。
        meta=dict(kind="msi", auto_msz=float(auto_msz),
                  label_size=float(label_size or 10)),
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
     Output("last_spatial_figure_store", "data")],
    # ver46.1: マーカーサイズ / ラベルサイズ / スポット不透明度 / H&E スポットサイズは
    # figure の **データ** を変えないので Input から外し State にした。これらは
    # assets/spatial_restyle.js の clientside callback が Plotly.restyle で直接
    # 反映するため、スライダー操作ではサーバ往復も再描画も発生しない。
    # State に残しているのは、別の理由で作り直すとき (サンプル切替など) に
    # 現在値で図を組み立てるため。
    [Input("interactive_sample", "value"),
     Input("spatial_highlight_cluster", "value"),
     Input("selected_cell_ids_store", "data"),
     Input("spatial_rotation_store", "data"),
     Input("spatial_show_labels", "value"),
     Input("spatial_exclude_cluster", "value"),
     Input("seurat_rds_path_store", "data"),
     Input("sample_name_map_store", "data"),
     Input("fullscreen_closed_trigger", "data"),
     Input("custom_color_map_store", "data"),
     Input("spatial_rows_per_view", "value"),
     Input("cluster_name_map_store", "data"),
     Input("umap_merge_toggle", "value"),
     Input("umap_merge_color_mode", "value"),
     Input("interactive_accordion", "active_item"),
     Input("spatial_legend_hidden_store", "data"),
     Input("hne_overlay_show", "value"),
     Input("hne_overlay_mono", "value")],
    [State("accumulated_label_positions", "data"),
     State("session_id_store", "data"),
     State("spatial_marker_size", "value"),
     State("spatial_label_size", "value"),
     State("hne_overlay_opacity", "value"),
     State("hne_overlay_marker_size", "value")],
)
def update_spatial_plots(sample, highlight_clusters, selected_ids,
                         rotation_store, show_labels,
                         exclude_clusters, rds_path, name_map,
                         _fs_trigger, custom_colors, rows,
                         cluster_name_map, merge_toggle, merge_color_mode,
                         active_items, legend_hidden, hne_show,
                         hne_mono, accumulated_positions,
                         session_id=None, marker_size=0, label_size=10,
                         hne_opacity=100, hne_marker_size=5):
    active_list = active_items if isinstance(active_items, list) else ([active_items] if active_items else [])
    if "acc_spatial" not in active_list:
        return no_update, no_update
    from app.callbacks.interactive_callbacks import (
        _set_active_key, accordion_toggle_is_noop, set_export_figures)
    # ver46.1: 他セクションの開閉だけで全タイルを作り直さない
    # （Feature Plot を開いただけで Spatial 全図が再構築されていた）。
    if accordion_toggle_is_noop("acc_spatial", session_id, rds_path,
                                active_items, ctx.triggered_id):
        return no_update, no_update
    _set_active_key(rds_path)
    from app.callbacks.interactive_callbacks import _interactive_data
    from app.callbacks.interactive_umap import _get_merged_label_positions
    df = _interactive_data.get("plot_data")
    if df is None or "SpatialX" not in df.columns:
        set_export_figures("spatial", session_id, rds_path, [])
        return html.Div("空間座標データがありません", className="text-muted p-3"), None

    if not rotation_store:
        rotation_store = {}
    if not name_map:
        name_map = {}

    # 選択セルID（ver27.0: UMAP ポリゴン選択由来の共有 Store から直接受け取る）
    selected_cell_ids = {str(c) for c in (selected_ids or [])}

    # マージ表示切替 (Spatial Mapping)
    plot_df = df
    effective_custom_colors = custom_colors
    if merge_toggle == "merged" and "Cluster_merged" in df.columns:
        # ver46.1: 従来は 10 万行の全列コピーだった。Spatial 描画が使う列だけを
        # 取り出せば足りる（UMAP 座標やマージ前後の別列は使わない）。
        _cols = [c for c in ("Sample", "SpatialX", "SpatialY", "CellID", "TotalCount")
                 if c in df.columns]
        plot_df = df[_cols].copy()
        plot_df["Cluster"] = df["Cluster_merged"].to_numpy()
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
        # ver46.1: 座標そのものが変わる要素だけを uirevision に含める。
        # これでマーカーサイズ/色/ラベル/凡例の変更ではズーム・パンが保たれ、
        # サンプル切替・回転・反転・H&E 座標系への切替では正しくリセットされる。
        tile_uirev = _transform_uirevision(
            s, transform, extra=("hne" if hne_show else "msi"))
        # ver30.0: 組織像オーバーレイ ON かつ当該サンプルが H&E 登録済みなら、
        # H&E 背景＋射影スポットのタイルを使う（未登録/OFF は従来の MSI 空間タイル）。
        # ver31.0: スポット透明度を通常タイルにも適用（下げると背後の TIC が透ける）。0 も有効値。
        _spot_op = (hne_opacity if hne_opacity is not None else 100) / 100.0
        fig = None
        if hne_show:
            try:
                fig = _build_hne_overlay_fig(
                    df_s, rds_path, s, title=display_s,
                    opacity=hne_opacity, marker_size=hne_marker_size,
                    color_map=color_map, cluster_name_map=cluster_name_map,
                    show_labels=show_labels, exclude_clusters=exclude_clusters,
                    legend_hidden=legend_hidden, mono=hne_mono)
            except Exception as e:  # noqa: BLE001
                logger.warning("[H&E overlay] %s: 生成失敗 -> 通常表示にフォールバック: %s", s, e)
                fig = None
        if fig is None:
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
                                         cluster_name_map=cluster_name_map,
                                         legend_hidden=legend_hidden,
                                         spot_opacity=_spot_op,
                                         uirevision=tile_uirev)
        # 出力(一括保存/HTML)は各図に凡例を残す → 先に凡例ありでスナップショット。
        # ver46.1: to_dict() は 1 タイルにつき 1 回だけ。従来は先頭タイルで 2 回
        # 呼んでおり、同じ点データを 2 度ディープコピーしていた。
        fig_dict = fig.to_dict()
        if representative_fig is None:
            representative_fig = fig_dict
        batch_fig_dicts.append((f"Spatial_{display_s}", fig_dict))
        # 画面表示は per-tile 凡例オフ（上部の共有凡例に集約）。
        fig.update_layout(showlegend=False)
        cfg = dict(_SPATIAL_IMG_CONFIG)
        cfg["toImageButtonOptions"] = dict(cfg["toImageButtonOptions"],
                                           filename=f"Spatial_{display_s}")
        if rows:
            # 行数指定: 1 行あたりの列数 = ceil(サンプル数 / 行数)（最大 rows 行）
            n_cols = max(1, math.ceil(len(samples_to_show) / rows))
            gap_total = (n_cols - 1) * 15
            flex_basis = f"calc({100 / n_cols:.2f}% - {gap_total / n_cols:.1f}px)"
            min_w = "0"
        else:
            n_cols = len(samples_to_show)
            flex_basis = f"{max(20, 90 // n_cols)}%"
            min_w = "300px"
        graphs.append(
            html.Div(
                className="facet-tile",
                style={"flex": f"1 1 {flex_basis}", "minWidth": min_w},
                children=[
                    dcc.Graph(id={"type": "spatial_graph", "index": s},
                              figure=fig, style={"height": "350px"}, config=cfg),
                ],
            )
        )

    # 共有クラスタ凡例(上部に1つ) + 縦線区切りタイル。
    container = _facet_block(graphs, color_map, cluster_name_map=cluster_name_map,
                             show_legend=True, legend_id="spatial_shared_legend",
                             hidden=legend_hidden)
    # ver46.1: 一括保存/サムネ用の全タイル figure はサーバ側に保持する。従来は
    # dcc.Store 経由でブラウザへ送っていたため、描画のたびに表示用と同じ点データが
    # もう 1 セット（非圧縮で数 MB〜数十 MB）流れていた。読み出すのは
    # 「一括保存」「サムネ登録」ボタンだけなので、往復させる必要が無い。
    set_export_figures("spatial", session_id, rds_path, batch_fig_dicts)
    # 代表figureはPPTX出力(background=True の別プロセス)から参照されるため Store のまま。
    store_data = representative_fig if representative_fig else None
    return container, store_data


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
     Input("spatial_rows_per_view", "value"),
     Input("spatial_exclude_cluster", "value")],
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def save_spatial_display_settings(marker_size, label_size, show_labels,
                                  rows, exclude_cluster, rds_path):
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
        "rows_per_view": rows if rows is not None else 0,
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


# ---------------------------------------------------------------------------
# 見た目だけのコントロール → clientside restyle (ver46.1)
# ---------------------------------------------------------------------------
# これらのスライダーは figure のデータを変えないため、サーバで作り直さず
# ブラウザ側で Plotly.restyle する（assets/spatial_restyle.js）。
# Output はダミー Store。実際の更新は JS が DOM 上のグラフに直接行う。

for _slider_id, _fn in (
    ("spatial_marker_size", "marker_size"),
    ("spatial_label_size", "label_size"),
    ("hne_overlay_opacity", "spot_opacity"),
    ("hne_overlay_marker_size", "hne_marker_size"),
):
    clientside_callback(
        ClientsideFunction(namespace="spatial_restyle", function_name=_fn),
        Output("spatial_restyle_dummy", "data", allow_duplicate=True),
        Input(_slider_id, "value"),
        prevent_initial_call=True,
    )
