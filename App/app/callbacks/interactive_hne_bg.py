# =============================================================================
# MSI Analysis Application - H&E background overlay in the Interactive tab (Phase 4)
# 登録済みの組織像 (H&E) を背景に、MSI クラスタのスポットを重ねて表示する。
#
# 画像のワープは行わず、MSI スポット座標を「登録済みアフィンの逆」で H&E 画素座標へ
# 射影し、ネイティブ H&E 画像 (go.Image) の上にスポットを散布する（堅牢・検証容易）。
# 使用するアフィン/回転は本番の領域割当 (hne_overlay.regions_from_overlay) と同一規約。
#
# スポット透明度スライダー (Loupe の「組織像に対するスポット不透明度」) を備える。
# =============================================================================

import base64
import logging
from io import BytesIO

import numpy as np
import plotly.graph_objects as go
from dash import Input, Output, State, callback, no_update, html

from app.services import hne_persistence as hp
from app.services.hne_overlay import (
    msi_to_hne_px, estimate_affine, affine_residual)
from app.utils.color_utils import (
    get_cluster_color_map as _get_cluster_color_map,
    cluster_display_name as _cluster_display_name,
)
from app.utils.selection_utils import natural_cluster_key

logger = logging.getLogger("msi.interactive.hne_bg")


def _decode_png_to_array(data_uri):
    from PIL import Image
    raw = base64.b64decode(str(data_uri).split(",", 1)[1])
    img = Image.open(BytesIO(raw)).convert("RGB")
    return np.asarray(img)


def _msg(text, cls="text-muted small"):
    return html.Span(text, className=cls)


@callback(
    [Output("hne_overlay_graph", "figure"),
     Output("hne_overlay_status", "children")],
    [Input("hne_overlay_show", "value"),
     Input("interactive_sample", "value"),
     Input("hne_overlay_opacity", "value"),
     Input("hne_overlay_marker_size", "value"),
     Input("interactive_accordion", "active_item"),
     Input("custom_color_map_store", "data"),
     Input("cluster_name_map_store", "data")],
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def update_hne_overlay(show, sample, opacity, marker_size, active_items,
                       custom_colors, cluster_name_map, rds_path):
    active = active_items if isinstance(active_items, list) else (
        [active_items] if active_items else [])
    if "acc_spatial" not in active:
        return no_update, no_update
    if not show:
        return go.Figure(), _msg("「組織像オーバーレイを表示」をオンにしてください。")
    if not rds_path:
        return go.Figure(), _msg("データが読み込まれていません")
    if not sample:
        return go.Figure(), _msg(
            "単一サンプルを選択してください（Spatial の「サンプル」ドロップダウン）。",
            "text-warning small")

    from app.callbacks.interactive_callbacks import (
        _interactive_data, _set_active_key)
    _set_active_key(rds_path)
    df = _interactive_data.get("plot_data")
    if df is None or "SpatialX" not in df.columns:
        return go.Figure(), _msg("空間座標がありません")

    entry = hp.load_hne_sample(rds_path, sample) or {}
    lm = entry.get("landmarks") or {}
    tic = lm.get("tic") or []
    hne = lm.get("hne") or []
    npair = min(len(tic), len(hne))
    img_info = entry.get("image") or {}
    img_file = img_info.get("file")
    if npair < 3 or not img_file:
        return go.Figure(), _msg(
            f"サンプル「{sample}」は H&E 登録（画像 + 3 点以上のランドマーク）が"
            "未完了です。「H&E オーバーレイ」タブで登録してください。",
            "text-warning small")

    data_uri = hp.load_hne_image_b64(rds_path, img_file)
    if not data_uri:
        return go.Figure(), _msg("H&E 画像の読込に失敗しました。", "text-danger small")
    try:
        arr = _decode_png_to_array(data_uri)
    except Exception as e:  # noqa: BLE001
        return go.Figure(), _msg(f"画像デコード失敗: {e}", "text-danger small")

    sub = df[df["Sample"].astype(str) == str(sample)]
    if sub.empty:
        return go.Figure(), _msg("該当サンプルのピクセルがありません")

    proj = msi_to_hne_px(
        sub["SpatialX"].to_numpy(dtype=float),
        sub["SpatialY"].to_numpy(dtype=float),
        entry.get("rotation"), hne, tic)
    if proj is None:
        return go.Figure(), _msg("ランドマークが不足しています（3 点以上必要）。",
                                 "text-warning small")
    px_x, px_y = proj

    fig = go.Figure()
    fig.add_trace(go.Image(z=arr, hoverinfo="skip"))

    op = (opacity if opacity is not None else 70) / 100.0
    ms = marker_size if (marker_size and marker_size > 0) else 5
    cats = sorted(sub["Cluster"].astype(str).unique(), key=natural_cluster_key)
    color_map = _get_cluster_color_map(cats, custom_colors)
    cell_ids = sub["CellID"].to_numpy()
    for cl in cats:
        mask = (sub["Cluster"].astype(str) == cl).to_numpy()
        if not mask.any():
            continue
        name = _cluster_display_name(cl, cluster_name_map)
        fig.add_trace(go.Scattergl(
            x=px_x[mask], y=px_y[mask], mode="markers",
            marker=dict(size=ms, color=color_map.get(cl, "#999999"), opacity=op),
            name=name, text=cell_ids[mask],
            hovertemplate=f"Cluster: {name}<br>%{{text}}<extra></extra>",
        ))

    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10), plot_bgcolor="white",
        legend=dict(font=dict(size=10)),
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)

    try:
        M = estimate_affine(hne[:npair], tic[:npair])
        res = affine_residual(hne[:npair], tic[:npair], M)
        status = _msg(
            f"H&E オーバーレイ表示中（位置合わせ残差 RMS: {res:.1f} MSI単位 / "
            f"ランドマーク {npair} 点）", "text-success small")
    except Exception:  # noqa: BLE001
        status = _msg("H&E オーバーレイ表示中", "text-success small")
    return fig, status
