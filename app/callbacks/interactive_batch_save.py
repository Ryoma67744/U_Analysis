"""セクション別 一括保存コールバック。

各アコーディオンセクション（UMAP, Spatial, Feature, DEG）の
「📷 一括保存」ボタンで、セクション内の全プロットを
PNG にまとめた ZIP をダウンロードする。

ZIP には **個別 PNG** に加え、2枚以上ある場合は
**横一列に結合した combined PNG** も同梱する。

**重要**: PNG 変換時の width/height は画面表示と同じサイズを指定し、
scale で高解像度化する。画面と異なるサイズを指定すると、
margin/marker/font の比率が変わり見た目が異なってしまう。
"""

import logging
import zipfile
from datetime import datetime
from io import BytesIO

from dash import Input, Output, State, callback, dcc, no_update
from dash.exceptions import PreventUpdate

from app.utils.pptx_helpers import fig_to_png_bytes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 表示サイズ定数（CSS で指定している値に合わせる）
# ---------------------------------------------------------------------------
# Per-sample UMAP / Spatial / Feature: 小パネル (flex レイアウト)
_PANEL_W = 400        # 各パネルの概算表示幅 (px)
_PANEL_H_UMAP = 300   # dcc.Graph style={"height": "300px"}
_PANEL_H_SPATIAL = 350 # dcc.Graph style={"height": "350px"}
_PANEL_H_FEATURE = 350 # dcc.Graph style={"height": "350px"}
_PANEL_SCALE = 4       # scale=4 → 実解像度 1600×1200 等

# 統合 UMAP: 大きく表示
_INTEGRATED_W = 800
_INTEGRATED_H = 600
_INTEGRATED_SCALE = 3  # 実解像度 2400×1800

# DEG (Volcano / Heatmap): 大きめプロット
_DEG_W = 700
_DEG_H = 500
_DEG_SCALE = 3         # 実解像度 2100×1500


# ---------------------------------------------------------------------------
# 共通ユーティリティ
# ---------------------------------------------------------------------------

def _concat_pngs_horizontal(png_bytes_list, gap=20, bg_color=(255, 255, 255)):
    """複数の PNG bytes を横一列に結合して PNG bytes を返す。

    Parameters
    ----------
    png_bytes_list : list[bytes]
        各画像の PNG バイト列
    gap : int
        画像間の余白ピクセル数
    bg_color : tuple
        背景色 (R, G, B)
    """
    from PIL import Image

    if not png_bytes_list:
        return None

    images = [Image.open(BytesIO(b)) for b in png_bytes_list]
    total_w = sum(img.width for img in images) + gap * (len(images) - 1)
    max_h = max(img.height for img in images)
    combined = Image.new("RGB", (total_w, max_h), bg_color)
    x = 0
    for img in images:
        # 縦方向は中央揃え
        y = (max_h - img.height) // 2
        combined.paste(img, (x, y))
        x += img.width + gap
    buf = BytesIO()
    combined.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


def _create_zip_from_figures(figures_list, width, height, scale, section_name=""):
    """[(name, fig_dict), ...] → ZIP bytes を返す。

    個別 PNG に加え、2枚以上の場合は横一列結合画像も同梱する。

    Parameters
    ----------
    figures_list : list[tuple[str, dict]]
        (ファイル名stem, plotly figure dict) のリスト
    width, height : int
        画面表示と同じピクセルサイズ
    scale : int
        解像度倍率（実PNG = width*scale × height*scale）
    section_name : str
        セクション名（結合画像のファイル名に使用）
    """
    if not figures_list:
        return None

    # 各 figure を PNG に変換
    png_list = []  # (name, png_bytes)
    for name, fig_dict in figures_list:
        try:
            png = fig_to_png_bytes(fig_dict, width=width, height=height, scale=scale)
            if png:
                png_list.append((name, png))
        except Exception:
            logger.warning("PNG conversion failed for %s", name, exc_info=True)

    if not png_list:
        return None

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 個別 PNG
        for name, png in png_list:
            zf.writestr(f"{name}.png", png)

        # 横結合画像（2枚以上の場合のみ）
        if len(png_list) >= 2:
            try:
                combined = _concat_pngs_horizontal([p for _, p in png_list])
                if combined:
                    combined_name = f"{section_name}_combined" if section_name else "combined"
                    zf.writestr(f"{combined_name}.png", combined)
            except Exception:
                logger.warning("Combined image creation failed", exc_info=True)

    buf.seek(0)
    data = buf.getvalue()
    # 空ZIP（ヘッダーのみ）の場合は None
    if len(data) <= 22:
        return None
    return data


def _timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# UMAP 一括保存
# ---------------------------------------------------------------------------

@callback(
    Output("dl_batch_zip", "data", allow_duplicate=True),
    Input("btn_batch_save_umap", "n_clicks"),
    [State("interactive_umap_plot", "figure"),
     State("umap_display_mode", "value"),
     State("batch_umap_figures_store", "data")],
    prevent_initial_call=True,
)
def cb_batch_save_umap(n_clicks, umap_fig, display_mode, per_sample_figs):
    if not n_clicks:
        raise PreventUpdate

    figures = []
    if display_mode == "per_sample" and per_sample_figs:
        figures = per_sample_figs
        w, h, s = _PANEL_W, _PANEL_H_UMAP, _PANEL_SCALE
    elif umap_fig:
        figures = [("UMAP_integrated", umap_fig)]
        w, h, s = _INTEGRATED_W, _INTEGRATED_H, _INTEGRATED_SCALE
    else:
        raise PreventUpdate

    if not figures:
        raise PreventUpdate

    zip_bytes = _create_zip_from_figures(figures, width=w, height=h, scale=s,
                                         section_name="UMAP")
    if zip_bytes is None:
        raise PreventUpdate

    return dcc.send_bytes(zip_bytes, f"UMAP_{_timestamp()}.zip")


# ---------------------------------------------------------------------------
# Spatial Mapping 一括保存
# ---------------------------------------------------------------------------

@callback(
    Output("dl_batch_zip", "data", allow_duplicate=True),
    Input("btn_batch_save_spatial", "n_clicks"),
    State("batch_spatial_figures_store", "data"),
    prevent_initial_call=True,
)
def cb_batch_save_spatial(n_clicks, spatial_figs):
    if not n_clicks:
        raise PreventUpdate

    if not spatial_figs:
        raise PreventUpdate

    zip_bytes = _create_zip_from_figures(
        spatial_figs,
        width=_PANEL_W, height=_PANEL_H_SPATIAL, scale=_PANEL_SCALE,
        section_name="SpatialMapping",
    )
    if zip_bytes is None:
        raise PreventUpdate

    return dcc.send_bytes(zip_bytes, f"SpatialMapping_{_timestamp()}.zip")


# ---------------------------------------------------------------------------
# Feature Plot 一括保存
# ---------------------------------------------------------------------------

@callback(
    Output("dl_batch_zip", "data", allow_duplicate=True),
    Input("btn_batch_save_feature", "n_clicks"),
    State("batch_feature_figures_store", "data"),
    prevent_initial_call=True,
)
def cb_batch_save_feature(n_clicks, feature_figs):
    if not n_clicks:
        raise PreventUpdate

    if not feature_figs:
        raise PreventUpdate

    zip_bytes = _create_zip_from_figures(
        feature_figs,
        width=_PANEL_W, height=_PANEL_H_FEATURE, scale=_PANEL_SCALE,
        section_name="FeaturePlot",
    )
    if zip_bytes is None:
        raise PreventUpdate

    return dcc.send_bytes(zip_bytes, f"FeaturePlot_{_timestamp()}.zip")


# ---------------------------------------------------------------------------
# DEG マーカー 一括保存
# ---------------------------------------------------------------------------

@callback(
    Output("dl_batch_zip", "data", allow_duplicate=True),
    Input("btn_batch_save_deg", "n_clicks"),
    [State("volcano_plot", "figure"),
     State("heatmap_plot", "figure"),
     State("volcano_cluster_select", "value")],
    prevent_initial_call=True,
)
def cb_batch_save_deg(n_clicks, volcano_fig, heatmap_fig, cluster_select):
    if not n_clicks:
        raise PreventUpdate

    figures = []
    suffix = f"_Cluster{cluster_select}" if cluster_select else ""
    if volcano_fig:
        figures.append((f"Volcano{suffix}", volcano_fig))
    if heatmap_fig:
        figures.append((f"Heatmap{suffix}", heatmap_fig))

    if not figures:
        raise PreventUpdate

    zip_bytes = _create_zip_from_figures(
        figures, width=_DEG_W, height=_DEG_H, scale=_DEG_SCALE,
        section_name="DEG",
    )
    if zip_bytes is None:
        raise PreventUpdate

    return dcc.send_bytes(zip_bytes, f"DEG_{_timestamp()}.zip")
