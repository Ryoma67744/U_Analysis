# =============================================================================
# MSI Analysis Application - 解剖×クラスタ（H&E オーバーレイ）コールバック
# =============================================================================
# 読込済みインタラクティブ解析の plot_data（_interactive_data）を再利用し、
# H&E アップロード → 対応点で位置合わせ → ポリゴンで領域指定 → 領域×クラスタ集計・
# MetaboAnalyst 用エクスポートを行う。
#
# 位置合わせ: H&E を go.Image トレースとして描画 → clickData で画素座標を取得。
#   TIC（散布）の clickData で MSI 座標を取得 → 対応点からアフィン推定（hne_overlay）。
# ポリゴン: H&E 上をクリックして頂点を順に配置（下書き）→「領域を確定」で閉じて登録 →
#   アフィンで MSI 座標へ変換 → 点-内包判定で spot に領域割当。
# =============================================================================

import base64
import io
import logging

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import (callback, Input, Output, State, no_update, ctx, html, dcc,
                  dash_table, clientside_callback)

from app.services import hne_overlay as hn
from app.services import hne_persistence as hp
from app.config import CLUSTER_PRESET_COLORS
from app.utils.color_utils import get_cluster_color_map

logger = logging.getLogger("msi.hne_overlay")

_NONMETA = {"id", "x", "y", "annotation"}


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------
def _empty_fig(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, showarrow=False, font=dict(color="#888", size=13))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), template="plotly_white")
    return fig


def _alert(msg, color="warning"):
    return dbc.Alert(msg, color=color, className="mb-0")


def _get_state(rds_path):
    from app.callbacks.interactive_callbacks import _interactive_data, _set_active_key
    if rds_path:
        _set_active_key(rds_path)
    return _interactive_data


def _dragmode(mode):
    # polygon もクリックで頂点を置くため pan（クリック=頂点 / ドラッグ=パン / ホイール=ズーム）
    return {"landmark": "pan", "polygon": "pan", "pan": "pan"}.get(mode, "pan")


# 位置合わせの十字ガイド（カーソル追従スパイク）。TIC/H&E 両図の軸に適用する。
# spikesnap="cursor" でデータ点でなくカーソル実位置に追従＝クリック位置の予測線になる。
_SPIKE_AXIS = dict(showspikes=True, spikemode="across", spikesnap="cursor",
                   spikethickness=1, spikedash="solid", spikecolor="#00b3b3")


def _roi_color_map(polys):
    """ポリゴン群の表示名 → 色 のマップ（同名ROIは同色）。クラスタ配色を流用。"""
    names = [(p.get("name") or f"領域{i + 1}") for i, p in enumerate(polys or [])]
    return get_cluster_color_map(names)


def _hex_to_rgba(hex_color, alpha):
    """'#RRGGBB' → 'rgba(r,g,b,alpha)'（塗りの半透明用）。"""
    h = str(hex_color).lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _centroid(verts):
    """頂点列の重心 (x, y)。空なら (None, None)。領域名ラベルの配置に使う。"""
    pts = list(verts) if verts is not None else []
    if len(pts) == 0:
        return None, None
    xs = [float(v[0]) for v in pts]; ys = [float(v[1]) for v in pts]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _sample_df(state, sample):
    df = state.get("plot_data")
    if df is None or not sample or "SpatialX" not in df.columns:
        return None
    d = df[df["Sample"].astype(str) == str(sample)]
    return d if not d.empty else None


def _apply_rotation(x, y, rotation):
    """MSI 回転設定（hne_rotation_store）で (x, y) を中心基準で反転+回転する。

    `interactive_spatial._transform_coords` を再利用（重心基準の反転＋任意角回転）。
    表示・割当の双方に同じ個体の全 (SpatialX, SpatialY) を渡すこと（重心一致＝一貫）。
    """
    rotation = rotation or {}
    angle = float(rotation.get("angle", 0) or 0)
    flip_h = bool(rotation.get("flip_h", False))
    flip_v = bool(rotation.get("flip_v", False))
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if angle == 0 and not flip_h and not flip_v:
        return x, y
    from app.callbacks.interactive_spatial import _transform_coords  # 遅延 import で循環回避
    return _transform_coords(x, y, angle, flip_h, flip_v)


# ---------------------------------------------------------------------------
# 個体ドロップダウン populate
# ---------------------------------------------------------------------------
@callback(
    Output("hne_sample_select", "options"),
    Output("hne_sample_select", "value"),
    Output("hne_data_status", "children"),
    Input("main_tabs", "active_tab"),
    Input("seurat_rds_path_store", "data"),
    State("hne_sample_select", "value"),
    prevent_initial_call=True,
)
def hne_populate_samples(active_tab, rds_path, current):
    if active_tab != "hne":
        return no_update, no_update, no_update
    state = _get_state(rds_path)
    df = state.get("plot_data")
    if df is None or "Sample" not in df.columns:
        return [], None, "インタラクティブ解析で解析を読み込んでください。"
    samples = sorted(str(s) for s in df["Sample"].dropna().unique())
    opts = [{"label": s, "value": s} for s in samples]
    val = current if current in samples else (samples[0] if samples else None)
    has_sp = "SpatialX" in df.columns and "SpatialY" in df.columns
    status = f"{len(df):,} spot / {len(samples)} 個体" + ("" if has_sp else "  ※空間座標なし")
    return opts, val, status


# ---------------------------------------------------------------------------
# H&E アップロード → Store
# ---------------------------------------------------------------------------
@callback(
    Output("hne_image_store", "data"),
    Output("hne_upload_info", "children"),
    Input("hne_image_upload", "contents"),
    State("hne_image_upload", "filename"),
    State("hne_sample_select", "value"),
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def hne_store_image(contents, filename, sample, rds_path):
    if not contents:
        return no_update, no_update
    try:
        _, b64 = contents.split(",", 1)
        from PIL import Image
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        ow, oh = img.size  # 元サイズ
        # 大きい画像は最大辺 ~2000px に縮小（表示を軽く。座標系は対応点アフィンが吸収）。
        img.thumbnail((2000, 2000), Image.LANCZOS)
        # 宣言 MIME に依存せず、PIL がデコードした画素から 8bit RGB PNG に作り直す。
        # （TIFF・拡張子偽装・16bit/CMYK/パレット PNG いずれもブラウザ描画可能になる）
        img = img.convert("RGB")
        w, h = img.size
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        src = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        note = f"{filename}（{w}×{h}px）" + (
            f"  ※元 {ow}×{oh}px を縮小" if (w, h) != (ow, oh) else "")
        store = {"src": src, "width": int(w), "height": int(h), "name": filename or "H&E"}
        # 個体別に PNG を永続保存（State の個体・RDSパスへ）。JSON には画像メタを記録。
        if sample and rds_path:
            fn = hp.save_hne_image(rds_path, sample, src)
            if fn:
                hp.save_hne_overlay_sample(rds_path, sample, {"image": {
                    "file": fn, "width": int(w), "height": int(h),
                    "name": filename or "H&E"}})
        return (store, note)
    except Exception as e:  # noqa: BLE001
        logger.warning("H&E 画像の読込に失敗: %s", e)
        return no_update, f"画像の読込に失敗: {e}"


# ---------------------------------------------------------------------------
# MSI 回転（粗い向き合わせ）：スライダ/反転 → Store 更新 ＋ 対応点クリア
# ---------------------------------------------------------------------------
@callback(
    Output("hne_rotation_store", "data"),
    Output("hne_landmarks_store", "data", allow_duplicate=True),
    Input("hne_rotation_angle", "value"),
    Input("hne_rotation_flip", "value"),
    State("hne_rotation_store", "data"),
    prevent_initial_call=True,
)
def hne_update_rotation(angle, flips, prev):
    flips = flips or []
    rot = {"angle": float(angle or 0),
           "flip_h": "flip_h" in flips, "flip_v": "flip_v" in flips}
    prev = prev or {}
    # 実質変化なし（個体復元で rotation_store を直接戻した等）なら対応点を消さない。
    if (float(prev.get("angle", 0) or 0) == rot["angle"]
            and bool(prev.get("flip_h", False)) == rot["flip_h"]
            and bool(prev.get("flip_v", False)) == rot["flip_v"]):
        return no_update, no_update
    # 旧回転でクリックした対応点は無効 → クリア（→ アフィンも自動的に未設定に戻る）。
    return rot, {"tic": [], "hne": []}


# ---------------------------------------------------------------------------
# 対応点（ランドマーク）取得：TIC / H&E の clickData ＋ クリアボタン
# ---------------------------------------------------------------------------
@callback(
    Output("hne_landmarks_store", "data"),
    Input("hne_tic_graph", "clickData"),
    Input("hne_image_graph", "clickData"),
    Input("hne_landmark_clear", "n_clicks"),
    State("hne_mode", "value"),
    State("hne_landmarks_store", "data"),
    prevent_initial_call=True,
)
def hne_capture_landmark(tic_click, hne_click, clear_n, mode, store):
    trig = ctx.triggered_id
    store = store or {"tic": [], "hne": []}
    if trig == "hne_landmark_clear":
        return {"tic": [], "hne": []}
    if mode != "landmark":
        return no_update
    if trig == "hne_tic_graph" and tic_click and tic_click.get("points"):
        p = tic_click["points"][0]
        store = {**store, "tic": list(store.get("tic", [])) + [[p["x"], p["y"]]]}
        return store
    if trig == "hne_image_graph" and hne_click and hne_click.get("points"):
        p = hne_click["points"][0]
        store = {**store, "hne": list(store.get("hne", [])) + [[p["x"], p["y"]]]}
        return store
    return no_update


# ---------------------------------------------------------------------------
# アフィン推定（対応点 >=3 ペア）＋情報表示
# ---------------------------------------------------------------------------
@callback(
    Output("hne_affine_store", "data"),
    Output("hne_landmark_info", "children"),
    Input("hne_landmarks_store", "data"),
    prevent_initial_call=True,
)
def hne_estimate_affine(lm):
    lm = lm or {"tic": [], "hne": []}
    tic = lm.get("tic", [])
    hne = lm.get("hne", [])
    npair = min(len(tic), len(hne))
    info = f"対応点: TIC {len(tic)} / H&E {len(hne)}（有効ペア {npair}）"
    if npair < 3:
        return None, info + " … 3ペア以上で位置合わせ"
    try:
        # src = H&E 画素, dst = MSI(TIC) 座標
        M = hn.estimate_affine(hne[:npair], tic[:npair])
        rms = hn.affine_residual(hne[:npair], tic[:npair], M)
        return ({"M": M.tolist(), "rms": rms},
                info + f" → 位置合わせ完了（残差 RMS={rms:.1f}）")
    except Exception as e:  # noqa: BLE001
        logger.warning("アフィン推定失敗: %s", e)
        return None, info + f" … 推定失敗: {e}"


# ---------------------------------------------------------------------------
# ポリゴン下書き：H&E クリックで頂点を追加 / 取り消し（最後の1点）/ 下書きクリア
# ---------------------------------------------------------------------------
@callback(
    Output("hne_polygon_draft_store", "data"),
    Input("hne_image_graph", "clickData"),
    Input("hne_polygon_undo", "n_clicks"),
    Input("hne_polygon_clear_draft", "n_clicks"),
    State("hne_mode", "value"),
    State("hne_polygon_draft_store", "data"),
    prevent_initial_call=True,
)
def hne_polygon_draft(click, undo_n, clear_n, mode, draft):
    trig = ctx.triggered_id
    draft = list(draft or [])
    if trig == "hne_polygon_clear_draft":
        return []
    if trig == "hne_polygon_undo":
        return draft[:-1]
    if trig == "hne_image_graph":
        # ポリゴンモードの H&E クリックのみ頂点として採用
        if mode != "polygon" or not (click and click.get("points")):
            return no_update
        p = click["points"][0]
        return draft + [[p["x"], p["y"]]]
    return no_update


# ---------------------------------------------------------------------------
# ポリゴン確定：下書き(>=3頂点) → 確定ポリゴンに追加（既定名）＋ 下書きを空に
# ---------------------------------------------------------------------------
@callback(
    Output("hne_polygons_store", "data", allow_duplicate=True),
    Output("hne_polygon_draft_store", "data", allow_duplicate=True),
    Input("hne_polygon_commit", "n_clicks"),
    State("hne_polygon_draft_store", "data"),
    State("hne_polygons_store", "data"),
    prevent_initial_call=True,
)
def hne_polygon_commit(n, draft, polys):
    draft = list(draft or [])
    if not n or len(draft) < 3:
        return no_update, no_update
    polys = list(polys or [])
    polys.append({"name": f"領域{len(polys) + 1}",
                  "vertices": [[float(v[0]), float(v[1])] for v in draft]})
    return polys, []


# ---------------------------------------------------------------------------
# 下書き状態の表示
# ---------------------------------------------------------------------------
@callback(
    Output("hne_polygon_draft_info", "children"),
    Input("hne_polygon_draft_store", "data"),
    prevent_initial_call=True,
)
def hne_polygon_draft_info(draft):
    n = len(draft or [])
    if n == 0:
        return "下書き: 0 頂点（H&E をクリックして頂点を追加）"
    if n < 3:
        return f"下書き: {n} 頂点（あと {3 - n} 点で確定可）"
    return f"下書き: {n} 頂点（「領域を確定」で閉じられます）"


# ---------------------------------------------------------------------------
# ポリゴン名テーブル同期（store → table。名前は store に保持）
# ---------------------------------------------------------------------------
@callback(
    Output("hne_polygon_table", "data"),
    Input("hne_polygons_store", "data"),
    prevent_initial_call=True,
)
def hne_sync_polygon_table(polys):
    polys = polys or []
    return [{"idx": i, "name": p.get("name") or f"領域{i + 1}",
             "nv": len(p.get("vertices") or [])} for i, p in enumerate(polys)]


# ---------------------------------------------------------------------------
# 表編集（改名・行削除）→ store へ反映（store→table と循環するためループ防止に等価比較）
# ---------------------------------------------------------------------------
@callback(
    Output("hne_polygons_store", "data"),
    Input("hne_polygon_table", "data"),
    State("hne_polygons_store", "data"),
    prevent_initial_call=True,
)
def hne_polygon_table_to_store(rows, polys):
    polys = polys or []
    rows = rows or []
    idxs = [int(r.get("idx", -1)) for r in rows]
    # 前個体の古い行で発火した過渡状態（範囲外idx・重複idx・store超過）は取りこぼし防止に
    # 何もしない。改名・1行削除・全削除（rows=[]）は通す。
    if (any(not (0 <= i < len(polys)) for i in idxs)
            or len(set(idxs)) != len(idxs) or len(rows) > len(polys)):
        return no_update
    new = []
    for r in rows:
        i = int(r["idx"])                # 行の idx は現在の store 位置を指す
        p = dict(polys[i])               # 頂点はそのまま、名前のみ表の値で更新
        nm = (r.get("name") or "").strip()
        p["name"] = nm or p.get("name") or f"領域{len(new) + 1}"
        new.append(p)
    if new == polys:                     # 変化なし（store→table 由来の再発火）→ 何もしない
        return no_update
    return new


# ---------------------------------------------------------------------------
# TIC 図（spot + 対応点 + 変換ポリゴン）
# ---------------------------------------------------------------------------
@callback(
    Output("hne_tic_graph", "figure"),
    Input("hne_sample_select", "value"),
    Input("hne_landmarks_store", "data"),
    Input("hne_affine_store", "data"),
    Input("hne_polygons_store", "data"),
    Input("hne_mode", "value"),
    Input("hne_rotation_store", "data"),
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def hne_tic_figure(sample, lm, affine, polys, mode, rotation, rds_path):
    state = _get_state(rds_path)
    d = _sample_df(state, sample)
    if d is None:
        return _empty_fig("解析と個体を選択してください（空間座標が必要）")
    # SpatialX/SpatialY に MSI 回転（粗い向き合わせ）を適用。対応点クリック・割当も
    # この回転後フレームで一致する（H&E との残りの向き差は対応点アフィンが吸収）。
    x, y = _apply_rotation(d["SpatialX"].to_numpy(dtype=float),
                           d["SpatialY"].to_numpy(dtype=float), rotation)
    if "TotalCount" in d.columns:
        marker = dict(size=3, symbol="square", color=d["TotalCount"].to_numpy(float),
                      colorscale="Greys", showscale=False)
    else:
        marker = dict(size=3, symbol="square", color="#555")
    # 主トレース（TIC）。hoverinfo="skip" は Plotly でクリック/ホバーイベントを
    # 抑止してしまう（対応点クリックが拾えなくなる）ため "none" にする
    # （ラベルは出さずイベントだけ発火。インタラクティブ解析の clickable トレースと同方針）。
    fig = go.Figure(go.Scattergl(x=x, y=y, mode="markers", marker=marker, hoverinfo="none",
                                 name="TIC"))
    # 対応点（TIC 側）
    tic_pts = (lm or {}).get("tic", [])
    if tic_pts:
        tx = [p[0] for p in tic_pts]; ty = [p[1] for p in tic_pts]
        # WebGL ベース(Scattergl)の spot 層と同じ canvas に載せて前面に描く
        # （go.Scatter(SVG) だと WebGL 層の下に隠れて TIC 側で見えなくなる）。
        fig.add_trace(go.Scattergl(x=tx, y=ty, mode="markers+text",
                                   marker=dict(size=10, color="red", symbol="x"),
                                   text=[str(i + 1) for i in range(len(tx))],
                                   textposition="top center", name="対応点", hoverinfo="skip"))
    # 変換済みポリゴン（アフィンがあれば）。ROIごとに色分け＋重心に領域名ラベル。
    if affine and affine.get("M") and polys:
        M = np.array(affine["M"], dtype=float)
        cmap = _roi_color_map(polys)
        for i, p in enumerate(polys):
            v = p.get("vertices") or []
            if len(v) >= 3:
                nm = p.get("name") or f"領域{i + 1}"
                col = cmap.get(str(nm), CLUSTER_PRESET_COLORS[0])
                msi = hn.apply_affine(v, M)
                xs = list(msi[:, 0]) + [msi[0, 0]]
                ys = list(msi[:, 1]) + [msi[0, 1]]
                # 対応点と同じく Scattergl で WebGL canvas 前面に描く
                fig.add_trace(go.Scattergl(x=xs, y=ys, mode="lines", fill="toself",
                                           line=dict(color=col),
                                           fillcolor=_hex_to_rgba(col, 0.25),
                                           name=nm, hoverinfo="skip"))
                cx, cy = _centroid(msi)
                if cx is not None:
                    fig.add_annotation(x=cx, y=cy, text=nm, showarrow=False,
                                       font=dict(size=11, color=col),
                                       bgcolor="rgba(255,255,255,0.6)")
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), template="plotly_white",
                      dragmode=_dragmode(mode), showlegend=False, hovermode="closest",
                      hoverlabel=dict(font_size=13, bgcolor="white"),
                      uirevision=sample or "tic")
    fig.update_xaxes(**_SPIKE_AXIS)
    fig.update_yaxes(scaleanchor="x", scaleratio=1, **_SPIKE_AXIS)
    return fig


# ---------------------------------------------------------------------------
# H&E 図（go.Image + 対応点 + ポリゴン shape 再注入）
# ---------------------------------------------------------------------------
@callback(
    Output("hne_image_graph", "figure"),
    Input("hne_image_store", "data"),
    Input("hne_landmarks_store", "data"),
    Input("hne_polygons_store", "data"),
    Input("hne_opacity", "value"),
    Input("hne_mode", "value"),
    State("hne_polygon_draft_store", "data"),
    State("hne_sample_select", "value"),
    prevent_initial_call=True,
)
def hne_image_figure(img, lm, polys, opacity, mode, draft, sample):
    if not img:
        return _empty_fig("H&E をアップロードしてください")
    w, h = img["width"], img["height"]
    # hoverinfo="none": 既定の「画素 x/y ＋ RGB配列」ラベルを消す。ただしホバー
    # イベント自体は生かす（"skip" だとイベントごと止まり、スパイクと座標表示も死ぬ）。
    fig = go.Figure(go.Image(source=img["src"],
                             opacity=float(opacity) if opacity is not None else 1.0,
                             hoverinfo="none"))
    # 対応点（H&E 側）
    hne_pts = (lm or {}).get("hne", [])
    if hne_pts:
        hx = [p[0] for p in hne_pts]; hy = [p[1] for p in hne_pts]
        fig.add_trace(go.Scatter(x=hx, y=hy, mode="markers+text",
                                 marker=dict(size=10, color="red", symbol="x"),
                                 text=[str(i + 1) for i in range(len(hx))],
                                 textposition="top center", hoverinfo="skip"))
    # 下書きポリゴン（クリック中の頂点列）。clientside で部分更新するため常にトレースを
    # 置く（頂点追加で図全体を作り直さない＝go.Image 再描画・Loading スピナーを避ける）。
    draft = draft or []
    dx = [v[0] for v in draft]; dy = [v[1] for v in draft]
    fig.add_trace(go.Scatter(x=dx, y=dy, mode="lines+markers",
                             line=dict(color="orange", width=2),
                             marker=dict(size=7, color="orange"),
                             hoverinfo="skip", name="下書き"))
    # 確定ポリゴンを shape として再注入（ROIごとに色分け＋重心に領域名ラベル）。
    shapes = []
    cmap = _roi_color_map(polys)
    for i, p in enumerate(polys or []):
        v = p.get("vertices") or []
        if len(v) >= 3:
            nm = p.get("name") or f"領域{i + 1}"
            col = cmap.get(str(nm), CLUSTER_PRESET_COLORS[0])
            path = "M" + "L".join(f"{vx},{vy}" for vx, vy in v) + "Z"
            shapes.append(dict(type="path", path=path, line=dict(color=col),
                               fillcolor=_hex_to_rgba(col, 0.2)))
            cx, cy = _centroid(v)
            if cx is not None:
                fig.add_annotation(x=cx, y=cy, text=nm,
                                   showarrow=False, font=dict(size=11, color=col),
                                   bgcolor="rgba(255,255,255,0.6)")
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), template="plotly_white",
                      dragmode=_dragmode(mode), shapes=shapes, showlegend=False,
                      hovermode="closest", hoverlabel=dict(font_size=13, bgcolor="white"),
                      uirevision=sample or "hne")
    fig.update_xaxes(visible=False, range=[0, w], **_SPIKE_AXIS)
    fig.update_yaxes(visible=False, range=[h, 0], scaleanchor="x", scaleratio=1, **_SPIKE_AXIS)
    return fig


# ---------------------------------------------------------------------------
# 個体別 永続復元: 個体切替・解析ロードで、保存済み状態を各 store へ流し込む
# ---------------------------------------------------------------------------
# 回転スライダ(value)は出力しない（出力すると hne_update_rotation が発火し復元直後の
# 対応点を消す恐れがあるため）。rotation_store を直接復元すれば図・割当は正しい
# （スライダ表示のみ前の値が残る軽微な制限）。affine は landmarks 復元で自動再計算。
@callback(
    Output("hne_image_store", "data", allow_duplicate=True),
    Output("hne_landmarks_store", "data", allow_duplicate=True),
    Output("hne_polygons_store", "data", allow_duplicate=True),
    Output("hne_rotation_store", "data", allow_duplicate=True),
    Output("hne_polygon_draft_store", "data", allow_duplicate=True),
    Output("hne_polygon_table", "data", allow_duplicate=True),
    Input("hne_sample_select", "value"),
    Input("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def hne_restore_sample(sample, rds_path):
    if not sample or not rds_path:
        return (no_update,) * 6
    entry = hp.load_hne_sample(rds_path, sample)
    lm = entry.get("landmarks") or {"tic": [], "hne": []}
    rot = entry.get("rotation") or {"angle": 0, "flip_h": False, "flip_v": False}
    polys = entry.get("polygons") or []
    image_store = None
    img_meta = entry.get("image") or None
    if img_meta and img_meta.get("file"):
        src = hp.load_hne_image_b64(rds_path, img_meta.get("file"))
        if src:
            image_store = {"src": src, "width": img_meta.get("width"),
                           "height": img_meta.get("height"),
                           "name": img_meta.get("name") or "H&E"}
    # 表データも同時に復元（store と表を同一ラウンドで整合させ、前個体の古い行で
    # hne_polygon_table_to_store が復元ポリゴンを上書きするのを防ぐ）。
    rows = [{"idx": i, "name": p.get("name") or f"領域{i + 1}",
             "nv": len(p.get("vertices") or [])} for i, p in enumerate(polys)]
    return image_store, lm, polys, rot, [], rows


# ---------------------------------------------------------------------------
# 個体別 永続保存: 編集のたびに現個体の軽量状態（対応点/ポリゴン/回転）を保存
# ---------------------------------------------------------------------------
# 画像は hne_store_image（アップロード時）で別途 PNG 保存済み。ここでは軽量メタのみ。
@callback(
    Output("hne_save_dummy", "data"),
    Input("hne_landmarks_store", "data"),
    Input("hne_polygons_store", "data"),
    Input("hne_rotation_store", "data"),
    State("hne_sample_select", "value"),
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def hne_autosave(lm, polys, rotation, sample, rds_path):
    if sample and rds_path:
        hp.save_hne_overlay_sample(rds_path, sample, {
            "landmarks": lm or {"tic": [], "hne": []},
            "polygons": polys or [],
            "rotation": rotation or {"angle": 0, "flip_h": False, "flip_v": False},
        })
    return no_update


# ---------------------------------------------------------------------------
# 領域割当 → 領域×クラスタ集計表
# ---------------------------------------------------------------------------
@callback(
    Output("hne_result_area", "children"),
    Input("hne_assign_btn", "n_clicks"),
    State("hne_sample_select", "value"),
    State("hne_polygons_store", "data"),
    State("hne_affine_store", "data"),
    State("hne_rotation_store", "data"),
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def hne_assign_and_summarize(n, sample, polys, affine, rotation, rds_path):
    if not n:
        return no_update
    if not affine or not affine.get("M"):
        return _alert("先に「対応点」で位置合わせをしてください。", "warning")
    if not polys:
        return _alert("先に H&E 上で領域（ポリゴン）を描いてください。", "warning")
    state = _get_state(rds_path)
    d = _sample_df(state, sample)
    if d is None:
        return _alert("空間座標つきの個体を選択してください。", "warning")
    M = np.array(affine["M"], dtype=float)
    polys_msi = hn.transform_polygons(polys, M)
    # ポリゴン（アフィン後）は回転後フレーム → spot 座標も同じ回転をかけてから割当。
    dd = d.copy()
    dd["SpatialX"], dd["SpatialY"] = _apply_rotation(
        dd["SpatialX"].to_numpy(float), dd["SpatialY"].to_numpy(float), rotation)
    region = hn.assign_regions(dd, polys_msi)
    dd["region"] = region.values
    g = hn.region_cluster_counts(dd)
    n_assigned = int(region.notna().sum())
    if g.empty:
        return _alert(f"領域に含まれる spot がありませんでした（割当 {n_assigned}）。", "warning")
    # 表示用テーブル
    g2 = g.rename(columns={"region": "領域", "Cluster": "クラスタ",
                           "count": "spot数", "pct_in_region": "領域内%"})
    return html.Div([
        html.Div(f"割当 spot 数: {n_assigned:,} / {len(d):,}（個体 {sample}）",
                 className="small text-muted mb-1"),
        dash_table.DataTable(
            data=g2.to_dict("records"),
            columns=[{"name": c, "id": c} for c in g2.columns],
            sort_action="native", page_size=20,
            style_cell={"fontSize": "0.82rem", "padding": "3px"},
            style_header={"fontWeight": "bold"},
        ),
    ])


# ---------------------------------------------------------------------------
# MetaboAnalyst 用 CSV エクスポート（region×cluster 群平均・化合物名優先）
# ---------------------------------------------------------------------------
@callback(
    Output("hne_export_download", "data"),
    Output("hne_export_info", "children"),
    Input("hne_export_btn", "n_clicks"),
    State("hne_sample_select", "value"),
    State("hne_polygons_store", "data"),
    State("hne_affine_store", "data"),
    State("hne_rotation_store", "data"),
    State("seurat_rds_path_store", "data"),
    State("seurat_cache_dir_store", "data"),
    prevent_initial_call=True,
)
def hne_export_csv(n, sample, polys, affine, rotation, rds_path, cache_dir_str):
    if not n:
        return no_update, no_update
    if not (affine and affine.get("M") and polys):
        return no_update, "位置合わせとポリゴンを先に行ってください。"
    state = _get_state(rds_path)
    d = _sample_df(state, sample)
    if d is None:
        return no_update, "個体を選択してください。"
    try:
        M = np.array(affine["M"], dtype=float)
        polys_msi = hn.transform_polygons(polys, M)
        dd = d.copy()
        dd["SpatialX"], dd["SpatialY"] = _apply_rotation(
            dd["SpatialX"].to_numpy(float), dd["SpatialY"].to_numpy(float), rotation)
        dd["region"] = hn.assign_regions(dd, polys_msi).values

        # 強度行列（元 Spatial 強度を推奨。RPCA の integrated は補正値の点に注意）
        from app.callbacks.interactive_callbacks import _bridge
        try:
            _bridge.ensure_expression_matrix(rds_path)
        except Exception:
            pass
        from pathlib import Path
        expr_path = None
        if cache_dir_str:
            cand = Path(cache_dir_str) / "expression_matrix.parquet"
            if cand.exists():
                expr_path = cand
        if expr_path is None:
            return no_update, "強度行列が見つかりません（Feature plot を一度開くと生成されます）。"
        expr_df = pd.read_parquet(expr_path)

        # 化合物名マップ（ver4.21 アノテーション）
        fa = state.get("feature_annotations") or {}
        name_map = {k: (v.get("compound") or v.get("display_name"))
                    for k, v in fa.items() if (v.get("compound") or v.get("display_name"))}

        out = hn.build_region_cluster_export(dd, expr_df, feature_name_map=name_map)
        if out.empty:
            return no_update, "出力対象（領域内 spot）がありませんでした。"
        fname = f"metaboanalyst_{sample}_region_cluster.csv".replace(" ", "_")
        return (dcc.send_data_frame(out.to_csv, fname, index=False),
                f"{len(out)} 群 × {out.shape[1]-1} feature を出力しました。")
    except Exception as e:  # noqa: BLE001
        logger.exception("H&E エクスポート失敗")
        return no_update, f"エクスポート失敗: {e}"


# ---------------------------------------------------------------------------
# 位置合わせ補助（clientside）: モード連動の十字カーソル ＋ カーソル座標リードアウト
# ---------------------------------------------------------------------------
# 対応点/ポリゴンモードのときだけ、グラフのラッパ Div に .hne-crosshair を付け、
# ドラッグ面のカーソルを十字にする（CSS は assets/styles.css）。pan では外す。
clientside_callback(
    """
    function(mode) {
        var c = (mode === 'landmark' || mode === 'polygon') ? 'hne-crosshair' : '';
        return [c, c];
    }
    """,
    Output("hne_tic_graph_wrap", "className"),
    Output("hne_image_graph_wrap", "className"),
    Input("hne_mode", "value"),
)

# H&E 上のカーソル座標（画素）を大きく見やすく表示。go.Image は hoverinfo="none"
# でラベルを消しつつイベントは生きているので hoverData が届く。
clientside_callback(
    """
    function(hData) {
        if (!hData || !hData.points || !hData.points.length) { return ''; }
        var p = hData.points[0];
        return 'X: ' + Math.round(p.x) + '  /  Y: ' + Math.round(p.y);
    }
    """,
    Output("hne_coord_readout", "children"),
    Input("hne_image_graph", "hoverData"),
)

# TIC（MSI 空間）側のカーソル近傍点の座標。クリックで採用される点と一致する。
clientside_callback(
    """
    function(hData) {
        if (!hData || !hData.points || !hData.points.length) { return ''; }
        var p = hData.points[0];
        return 'X: ' + Number(p.x).toFixed(1) + '  /  Y: ' + Number(p.y).toFixed(1);
    }
    """,
    Output("hne_tic_coord_readout", "children"),
    Input("hne_tic_graph", "hoverData"),
)

# 下書きポリゴンの部分更新（clientside）: 頂点クリックで figure 全体を作り直さず
# 「下書き」トレースだけ Plotly.restyle で更新 → go.Image 再描画・Loading を回避。
clientside_callback(
    """
    function(draft) {
        try {
            var root = document.getElementById('hne_image_graph');
            if (!root || !window.Plotly) { return window.dash_clientside.no_update; }
            var gd = root.querySelector('.js-plotly-plot') || root;
            if (!gd || !gd.data) { return window.dash_clientside.no_update; }
            var idx = -1;
            for (var i = 0; i < gd.data.length; i++) {
                if (gd.data[i].name === '下書き') { idx = i; break; }
            }
            if (idx < 0) { return window.dash_clientside.no_update; }
            var xs = [], ys = [];
            (draft || []).forEach(function(v) { xs.push(v[0]); ys.push(v[1]); });
            window.Plotly.restyle(gd, {x: [xs], y: [ys]}, [idx]);
        } catch (e) { /* no-op */ }
        return window.dash_clientside.no_update;
    }
    """,
    Output("hne_draft_dummy", "data"),
    Input("hne_polygon_draft_store", "data"),
)
