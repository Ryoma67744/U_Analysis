# =============================================================================
# MSI Analysis Application - H&E background overlay (Phase 4 / ver30.0 統合)
# 登録済みの組織像 (H&E) を背景に、MSI クラスタのスポットを重ねて表示する。
#
# 画像のワープは行わず、MSI スポット座標を「登録済みアフィンの逆」で H&E 画素座標へ
# 射影し、ネイティブ H&E 画像 (go.Image) の上にスポットを散布する（堅牢・検証容易）。
# 使用するアフィン/回転は本番の領域割当 (hne_overlay.regions_from_overlay) と同一規約。
#
# ver30.0: 独立グラフ (hne_overlay_graph) を廃止し、本ロジックを純関数
# build_hne_overlay_fig に切り出して **Spatial Mapping 本体の各タイル**から呼ぶ
# （interactive_spatial.update_spatial_plots）。スポット透明度で H&E を透過させる。
# =============================================================================

import base64
import logging
from io import BytesIO

import numpy as np
import plotly.graph_objects as go
from dash import Input, Output, callback, html

from app.services import hne_persistence as hp
from app.services.hne_overlay import msi_to_hne_px
from app.utils.color_utils import cluster_display_name as _cluster_display_name
from app.utils.selection_utils import natural_cluster_key

logger = logging.getLogger("msi.interactive.hne_bg")

# デコード済み H&E 配列のキャッシュ（複数サンプルを毎回デコードしないため）
_HNE_ARR_CACHE = {}


def _decode_png_to_array(data_uri):
    from PIL import Image
    raw = base64.b64decode(str(data_uri).split(",", 1)[1])
    img = Image.open(BytesIO(raw)).convert("RGB")
    return np.asarray(img)


def _load_hne_array(rds_path, img_file):
    """rds_path+img_file をキーにデコード済み H&E 配列をキャッシュして返す。失敗時 None。"""
    key = (str(rds_path), str(img_file))
    if key in _HNE_ARR_CACHE:
        return _HNE_ARR_CACHE[key]
    data_uri = hp.load_hne_image_b64(rds_path, img_file)
    if not data_uri:
        return None
    try:
        arr = _decode_png_to_array(data_uri)
    except Exception as e:  # noqa: BLE001
        logger.warning("H&E 画像デコード失敗: %s", e)
        return None
    _HNE_ARR_CACHE[key] = arr
    return arr


def _msg(text, cls="text-muted small"):
    return html.Span(text, className=cls)


def build_hne_overlay_fig(df_sample, rds_path, sample, *, title=None, opacity=70,
                          marker_size=5, color_map=None, cluster_name_map=None,
                          show_labels=False, exclude_clusters=None,
                          legend_hidden=None, mono=False):
    """登録済み H&E を背景に、当該サンプルの MSI スポットを射影して重ねた figure を返す。

    Spatial Mapping 本体の per-sample タイルから呼ぶ。登録未完了（画像が無い / ランドマーク
    3 点未満 / 射影不能）なら **None** を返し、呼び出し側は従来の MSI 空間タイルにフォールバックする。

    - opacity: スポット不透明度 (0–100)。下げると H&E が透ける。
    - exclude_clusters: 完全除去（描かない）。legend_hidden: 灰色化（色トレースを描かず H&E を透かす）。
    座標系は登録フレーム（H&E 画素）。共有凡例連動用に欠番空白のダミー凡例 trace を付ける。
    """
    if df_sample is None or getattr(df_sample, "empty", True):
        return None
    if "SpatialX" not in df_sample.columns or "SpatialY" not in df_sample.columns:
        return None
    entry = hp.load_hne_sample(rds_path, sample) or {}
    lm = entry.get("landmarks") or {}
    tic = lm.get("tic") or []
    hne = lm.get("hne") or []
    npair = min(len(tic), len(hne))
    img_info = entry.get("image") or {}
    img_file = img_info.get("file")
    if npair < 3 or not img_file:
        return None
    arr = _load_hne_array(rds_path, img_file)
    if arr is None:
        return None

    # exclude（完全除去）
    sub = df_sample
    if exclude_clusters:
        ex = {str(c) for c in exclude_clusters}
        sub = sub[~sub["Cluster"].astype(str).isin(ex)]
    if sub.empty:
        return None

    proj = msi_to_hne_px(
        sub["SpatialX"].to_numpy(dtype=float),
        sub["SpatialY"].to_numpy(dtype=float),
        entry.get("rotation"), hne, tic)
    if proj is None:
        return None
    px_x, px_y = proj

    op = (opacity if opacity is not None else 70) / 100.0
    ms = marker_size if (marker_size and marker_size > 0) else 5
    color_map = color_map or {}
    hidden = {str(c) for c in (legend_hidden or [])}

    # ver31.0: モノクロ表示。キャッシュ配列を破壊せず新規配列で輝度グレースケール化。
    arr_show = arr
    if mono:
        lum = arr[..., :3].astype(float) @ np.array([0.299, 0.587, 0.114])
        arr_show = np.repeat(lum[..., None], 3, axis=2).astype(np.uint8)

    fig = go.Figure()
    fig.add_trace(go.Image(z=arr_show, hoverinfo="skip"))

    cl_series = sub["Cluster"].astype(str).to_numpy()
    cats_present = sorted(set(cl_series), key=natural_cluster_key)
    cell_ids = sub["CellID"].to_numpy() if "CellID" in sub.columns else None
    for cl in cats_present:
        if cl in hidden:
            continue  # 灰色化＝色トレースを描かない → H&E が透ける
        mask = (cl_series == cl)
        if not mask.any():
            continue
        name = _cluster_display_name(cl, cluster_name_map)
        kw = dict(x=px_x[mask], y=px_y[mask], mode="markers",
                  marker=dict(size=ms, color=color_map.get(cl, "#999999"), opacity=op),
                  name=name, showlegend=False, legendgroup=name)
        if cell_ids is not None:
            kw["text"] = cell_ids[mask]
            kw["hovertemplate"] = f"Cluster: {name}<br>%{{text}}<extra></extra>"
        fig.add_trace(go.Scattergl(**kw))
        if show_labels:
            fig.add_annotation(x=float(px_x[mask].mean()), y=float(px_y[mask].mean()),
                               text=name, showarrow=False,
                               font=dict(size=10, color="black"))

    # 共有凡例連動用ダミー（存在＝色付き / 欠番＝空白スロットで縦位置を保持）
    all_clusters = sorted({str(c) for c in color_map.keys()} | set(cats_present),
                          key=natural_cluster_key)
    present = set(cats_present)
    for cl in all_clusters:
        rank = int(cl) if str(cl).isdigit() else 1000
        if cl in present and cl not in hidden:
            fig.add_trace(go.Scattergl(
                x=[None], y=[None], mode="markers",
                marker=dict(size=10, color=color_map.get(cl, "#999999")),
                name=_cluster_display_name(cl, cluster_name_map),
                showlegend=True, legendrank=rank,
                legendgroup=_cluster_display_name(cl, cluster_name_map)))
        else:
            fig.add_trace(go.Scattergl(
                x=[None], y=[None], mode="markers",
                marker=dict(size=10, color="rgba(0,0,0,0)"),
                name=" ", showlegend=True, legendrank=rank,
                legendgroup=f"_blank_{cl}"))

    fig.update_layout(
        margin=dict(l=10, r=10, t=max(24, 10), b=10), plot_bgcolor="white",
        showlegend=True, legend=dict(itemsizing="constant", font=dict(size=10)),
    )
    if title:
        fig.update_layout(title=dict(text=str(title), font=dict(size=12), x=0.5))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


# ---------------------------------------------------------------------------
# 状態テキスト（コントロール脇）。トグル ON/OFF を案内する軽量コールバック。
# ---------------------------------------------------------------------------
@callback(
    Output("hne_overlay_status", "children"),
    Input("hne_overlay_show", "value"),
    prevent_initial_call=True,
)
def hne_overlay_status_msg(show):
    if show:
        return _msg(
            "登録済みサンプルの背景に組織像を重畳中（未登録サンプルは通常表示）。"
            "スポット透明度を下げると組織像が透けます。", "text-success small")
    return _msg("オフ：通常の Spatial Mapping 表示。", "text-muted small")
