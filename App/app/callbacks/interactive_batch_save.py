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


def _get_export_figures(kind, session_id, rds_path):
    """描画コールバックがサーバ側に置いた figure リストを取り出す (ver46.1)。

    以前は同じ内容を dcc.Store 経由でブラウザに持たせていたが、描画のたびに
    全タイルの点データが往復していたためサーバ保持に変更した。
    循環 import を避けるため関数内で遅延 import する。
    """
    from app.callbacks.interactive_callbacks import get_export_figures
    return get_export_figures(kind, session_id, rds_path)

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

# ver3.15: サムネ専用の小さい解像度。
# 最終的に thumbnail_service で 300x300 にリサイズされるので、
# kaleido で 600x600 を生成すれば十分。バッチ保存用の高解像度より
# 5-10x 高速 (1-3 秒 → 200-500ms)。
_THUMB_RENDER_W = 600
_THUMB_RENDER_H = 600
_THUMB_RENDER_SCALE = 1


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

    # ver28.0: 凡例(欠番空白化で常に全クラスタ分)が縦に溢れて PNG で見切れるのを防ぐため、
    # 凡例行数に応じて書き出し高さを自動拡張する。combined は高さ差を中央寄せで吸収する。
    def _legend_rows(fd):
        return sum(1 for t in (fd or {}).get("data", []) if t.get("showlegend"))
    max_rows = max((_legend_rows(fd) for _, fd in figures_list), default=0)
    eff_height = max(height, max_rows * 20 + 70)

    # 各 figure を PNG に変換
    png_list = []  # (name, png_bytes)
    for name, fig_dict in figures_list:
        try:
            png = fig_to_png_bytes(fig_dict, width=width, height=eff_height, scale=scale)
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
     State("session_id_store", "data"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def cb_batch_save_umap(n_clicks, umap_fig, display_mode, session_id, rds_path):
    if not n_clicks:
        raise PreventUpdate
    per_sample_figs = _get_export_figures("umap", session_id, rds_path)

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
    [State("session_id_store", "data"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def cb_batch_save_spatial(n_clicks, session_id, rds_path):
    if not n_clicks:
        raise PreventUpdate
    spatial_figs = _get_export_figures("spatial", session_id, rds_path)

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
    [State("session_id_store", "data"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def cb_batch_save_feature(n_clicks, session_id, rds_path):
    if not n_clicks:
        raise PreventUpdate
    feature_figs = _get_export_figures("feature", session_id, rds_path)

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


# =============================================================================
# ver3.10: 現在の UMAP / Spatial をプロジェクトサムネに登録
# 図を PNG 化してサーバーに保存し、project.thumbnail_source を更新する。
# =============================================================================

def _save_figure_as_thumbnail(figures_list, width, height, scale,
                                project_id, kind):
    """Plotly figure(s) を PNG 化してプロジェクトサムネとして登録。

    ver3.11: 複数切片 (per-sample) がある場合は **最初の 1 枚** のみ使用。
    横結合した wide なサムネは 50x50 square で見切れるため。

    Returns
    -------
    tuple[bool, str]
        (success, message)
    """
    from pathlib import Path
    from app.config import OTHER_DIR
    from app.services.project_manager import get_project, update_project

    if not project_id:
        return False, "プロジェクトが選択されていません"

    project = get_project(project_id)
    if not project:
        return False, f"プロジェクトが見つかりません: {project_id}"

    if not figures_list:
        return False, "保存対象のプロットがありません"

    # ver3.11: 複数 figure (per-sample 等) は **最初の 1 枚だけ** 使う。
    # 横結合 (concat) はサムネで見切れるため廃止
    first_entry = figures_list[0]
    if isinstance(first_entry, (list, tuple)) and len(first_entry) >= 2:
        first_name, first_fig = first_entry[0], first_entry[1]
    else:
        first_name, first_fig = "thumbnail", first_entry

    try:
        final_png = fig_to_png_bytes(
            first_fig, width=width, height=height, scale=scale,
        )
    except Exception as e:
        logger.warning("PNG conversion failed: %s", e, exc_info=True)
        return False, f"PNG 化に失敗しました: {e}"

    if not final_png:
        return False, "PNG 化に失敗しました"

    n_total = len(figures_list)
    if n_total > 1:
        logger.info(
            "thumbnail: %d figures available, using only first (%s)",
            n_total, first_name,
        )

    # 保存先: Data/Other/cache/project_thumbnails_src/<project_id>_<kind>.png
    save_dir = OTHER_DIR / "cache" / "project_thumbnails_src"
    save_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_id)
    save_path = save_dir / f"{safe_id}_{kind}.png"
    try:
        with open(save_path, "wb") as f:
            f.write(final_png)
    except Exception as e:
        logger.error("thumbnail PNG save failed: %s", e)
        return False, f"保存失敗: {e}"

    # project.thumbnail_source を更新
    updated = update_project(project_id, {
        "thumbnail_source": str(save_path),
    })
    if updated is None:
        return False, "プロジェクト更新に失敗しました"

    # ver3.15: cache pre-warm。次回 Flask route 呼出は即時 hit する。
    # 失敗してもユーザー操作には影響しないので例外は飲み込む。
    try:
        from app.services.thumbnail_service import get_thumbnail_path
        get_thumbnail_path(project_id, str(save_path))
    except Exception as e:
        logger.debug("thumbnail cache pre-warm failed: %s", e)

    logger.info("thumbnail set: project=%s kind=%s path=%s (used 1/%d figs)",
                project_id, kind, save_path, n_total)
    if n_total > 1:
        msg = f"サムネを {kind} で登録しました (複数切片は 1 枚目のみ使用)"
    else:
        msg = f"サムネを {kind} で登録しました"
    return True, msg


@callback(
    Output("notification_toast", "is_open", allow_duplicate=True),
    Output("notification_toast", "children", allow_duplicate=True),
    Output("notification_toast", "icon", allow_duplicate=True),
    Output("project_list_refresh", "data", allow_duplicate=True),
    Input("btn_set_thumbnail_spatial", "n_clicks"),
    [State("interactive_project_select", "value"),
     State("project_list_refresh", "data"),
     State("session_id_store", "data"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def cb_set_thumbnail_spatial(n_clicks, project_id, refresh, session_id, rds_path):
    if not n_clicks:
        raise PreventUpdate
    spatial_figs = _get_export_figures("spatial", session_id, rds_path)
    # ver3.15: サムネ用に小さい解像度で kaleido を呼ぶ (5-10× 高速化)
    ok, msg = _save_figure_as_thumbnail(
        spatial_figs or [],
        _THUMB_RENDER_W, _THUMB_RENDER_H, _THUMB_RENDER_SCALE,
        project_id, "spatial",
    )
    return True, msg, ("success" if ok else "danger"), (refresh or 0) + 1


@callback(
    Output("notification_toast", "is_open", allow_duplicate=True),
    Output("notification_toast", "children", allow_duplicate=True),
    Output("notification_toast", "icon", allow_duplicate=True),
    Output("project_list_refresh", "data", allow_duplicate=True),
    Input("btn_set_thumbnail_umap", "n_clicks"),
    [State("interactive_umap_plot", "figure"),
     State("umap_display_mode", "value"),
     State("interactive_project_select", "value"),
     State("project_list_refresh", "data"),
     State("session_id_store", "data"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def cb_set_thumbnail_umap(n_clicks, umap_fig,
                          display_mode, project_id, refresh,
                          session_id, rds_path):
    if not n_clicks:
        raise PreventUpdate
    per_sample_figs = _get_export_figures("umap", session_id, rds_path)
    # 表示モードに応じて選択: per_sample なら 1 枚目、それ以外は統合 UMAP
    if display_mode == "per_sample" and per_sample_figs:
        figs = per_sample_figs
    elif umap_fig:
        figs = [("UMAP_integrated", umap_fig)]
    else:
        return True, "UMAP プロットが見つかりません", "danger", no_update
    # ver3.15: サムネ用に小さい解像度で kaleido を呼ぶ (5-10× 高速化)
    ok, msg = _save_figure_as_thumbnail(
        figs,
        _THUMB_RENDER_W, _THUMB_RENDER_H, _THUMB_RENDER_SCALE,
        project_id, "umap",
    )
    return True, msg, ("success" if ok else "danger"), (refresh or 0) + 1
