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
import os
import threading
from collections import OrderedDict
from io import BytesIO

import plotly.graph_objects as go
from dash import Input, Output, callback, html

from app.services import hne_persistence as hp
from app.services.hne_overlay import msi_to_hne_px
from app.utils.color_utils import cluster_display_name as _cluster_display_name
from app.utils.display_helpers import transform_uirevision as _transform_uirevision
from app.utils.selection_utils import natural_cluster_key

logger = logging.getLogger("msi.interactive.hne_bg")

# ---------------------------------------------------------------------------
# 表示用 H&E 画像キャッシュ (ver46.1)
# ---------------------------------------------------------------------------
# 旧実装は go.Image(z=<H,W,3 の uint8 配列>) で図を作っていた。plotly は数値配列を
# JSON の数値リストとして直列化するため、2000x2000 の H&E 1 枚で **約 62MB** の
# JSON になっていた（実測）。しかも同じ figure が一括保存用 Store にも複製されるため、
# スポット透明度スライダーを 1 目盛り動かすだけでその数倍が非圧縮で流れていた。
#
# go.Image(source=<PNG/JPEG data URI>) なら圧縮済みバイト列がそのまま渡るので、
# 同じ絵が数百 KB で済む。この形式は H&E 位置合わせタブ (hne_overlay_callbacks.py)
# で既に使われている実績のある書き方。
#
# あわせて、
#  - 表示用に長辺を縮小する（位置合わせ用の原寸は登録側でそのまま保持）
#  - モノクロ変換の結果もキャッシュする（旧実装は毎描画で float64 の行列積 +
#    np.repeat を実行しており、1 枚あたり数十 MB の一時確保が発生していた）
#  - キャッシュを件数上限つき LRU + ロック付きにする（旧 _HNE_ARR_CACHE は
#    無制限・ロック無しで、開いたプロジェクトの枚数だけメモリを保持し続けていた）
# ---------------------------------------------------------------------------

# 表示用の長辺上限 (px)。登録時の上限は 2000 (hne_overlay_callbacks._read_image)。
HNE_DISPLAY_MAX_DIM = int(os.environ.get("HNE_DISPLAY_MAX_DIM", 1400))
# JPEG 品質。組織像は写真なので JPEG の方が PNG より遥かに小さい。
HNE_DISPLAY_JPEG_QUALITY = int(os.environ.get("HNE_DISPLAY_JPEG_QUALITY", 85))
_HNE_CACHE_MAX_ENTRIES = int(os.environ.get("HNE_CACHE_MAX_ENTRIES", 12))

# key -> (data_uri, scale, width, height)。scale = 表示画像 / 原寸 の倍率。
_HNE_IMG_CACHE: "OrderedDict[tuple, tuple]" = OrderedDict()
_HNE_IMG_CACHE_LOCK = threading.Lock()


def _build_display_image(data_uri, mono):
    """原寸の data URI から「表示用に縮小(+モノクロ)した data URI」を作る。

    Returns (data_uri, scale, width, height)。scale は原寸に対する倍率
    (例: 2000px -> 1400px なら 0.7)。失敗時 None。
    """
    from PIL import Image
    raw = base64.b64decode(str(data_uri).split(",", 1)[1])
    img = Image.open(BytesIO(raw)).convert("RGB")
    orig_w = img.width
    if orig_w <= 0 or img.height <= 0:
        return None
    if max(img.width, img.height) > HNE_DISPLAY_MAX_DIM:
        img.thumbnail((HNE_DISPLAY_MAX_DIM, HNE_DISPLAY_MAX_DIM), Image.LANCZOS)
    scale = img.width / orig_w
    if mono:
        # 輝度グレースケール化。PIL の "L" は ITU-R 601-2 luma (0.299/0.587/0.114) で
        # 旧実装の重み付けと同一。RGB に戻して go.Image の期待する 3ch を保つ。
        img = img.convert("L").convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=HNE_DISPLAY_JPEG_QUALITY, optimize=True)
    uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    return uri, scale, img.width, img.height


def _load_hne_display_image(rds_path, img_file, mono=False):
    """表示用 H&E 画像を (data_uri, scale, width, height) で返す。失敗時 None。

    mono の有無ごとに別エントリでキャッシュする。
    """
    key = (str(rds_path), str(img_file), bool(mono))
    with _HNE_IMG_CACHE_LOCK:
        hit = _HNE_IMG_CACHE.get(key)
        if hit is not None:
            _HNE_IMG_CACHE.move_to_end(key)
            return hit
    data_uri = hp.load_hne_image_b64(rds_path, img_file)
    if not data_uri:
        return None
    try:
        built = _build_display_image(data_uri, mono)
    except Exception as e:  # noqa: BLE001
        logger.warning("H&E 表示画像の生成に失敗: %s", e)
        return None
    if built is None:
        return None
    with _HNE_IMG_CACHE_LOCK:
        _HNE_IMG_CACHE[key] = built
        _HNE_IMG_CACHE.move_to_end(key)
        while len(_HNE_IMG_CACHE) > _HNE_CACHE_MAX_ENTRIES:
            _HNE_IMG_CACHE.popitem(last=False)
    return built


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
    disp = _load_hne_display_image(rds_path, img_file, mono=bool(mono))
    if disp is None:
        return None
    img_uri, img_scale, img_w, img_h = disp

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
    # 表示画像は縮小してあるため、原寸 H&E 画素で計算されたスポット座標も同じ倍率で
    # 縮める。go.Image(source=...) は x0=0, dx=1（1 画素 = 1 データ単位）で置かれるので、
    # これで背景とスポットの位置関係は原寸時と完全に一致する。
    if img_scale != 1.0:
        px_x = px_x * img_scale
        px_y = px_y * img_scale

    fig = go.Figure()
    fig.add_trace(go.Image(source=img_uri, hoverinfo="skip"))

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
                  name=name, showlegend=False, legendgroup=name,
                  # ver46.1: clientside restyle 対象（サイズ・不透明度スライダー）
                  meta={"dsz": 0, "op": True})
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
        # ver46.1: H&E タイルであることと基準マーカーサイズを clientside に伝える。
        # kind="hne" なので H&E 用スライダーだけが対象になる（通常タイルとは分離）。
        meta=dict(kind="hne", auto_msz=float(ms), label_size=10.0),
        # ver46.1: 透明度・マーカーサイズ・モノクロ切替ではズーム/パンを保持し、
        # 座標系が変わる要素 (サンプル / 表示倍率) が変わったときだけリセットする。
        uirevision=_transform_uirevision(sample, None, extra=f"hne:{img_scale:.4f}"),
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
