# =============================================================================
# MSI Analysis Application - 解剖×クラスタ（H&E オーバーレイ）コールバック
# =============================================================================
# 読込済みインタラクティブ解析の plot_data（_interactive_data）を再利用し、
# H&E アップロード → 対応点で位置合わせ → ポリゴンで領域指定 → 領域×クラスタ集計・
# MetaboAnalyst 用エクスポートを行う。
#
# 位置合わせ: H&E を go.Image トレースとして描画 → clickData で画素座標を取得。
#   TIC（散布）の clickData で MSI 座標を取得 → 対応点からアフィン推定（hne_overlay）。
# ポリゴン: H&E 上で drawclosedpath → relayoutData['shapes'] を取り込み → アフィンで
#   MSI 座標へ変換 → 点-内包判定で spot に領域割当。
# =============================================================================

import base64
import io
import logging

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import callback, Input, Output, State, no_update, ctx, html, dcc, dash_table

from app.services import hne_overlay as hn

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
    return {"landmark": "pan", "polygon": "drawclosedpath", "pan": "pan"}.get(mode, "pan")


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
    prevent_initial_call=True,
)
def hne_store_image(contents, filename):
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
        return ({"src": src, "width": int(w), "height": int(h), "name": filename or "H&E"},
                note)
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
    prevent_initial_call=True,
)
def hne_update_rotation(angle, flips):
    flips = flips or []
    rot = {"angle": float(angle or 0),
           "flip_h": "flip_h" in flips, "flip_v": "flip_v" in flips}
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
# ポリゴン取り込み（H&E 上の drawclosedpath → relayoutData['shapes']）
# ---------------------------------------------------------------------------
@callback(
    Output("hne_polygons_store", "data"),
    Input("hne_image_graph", "relayoutData"),
    State("hne_polygons_store", "data"),
    prevent_initial_call=True,
)
def hne_capture_polygons(relayout, store):
    if not relayout or "shapes" not in relayout:
        return no_update
    polys = []
    for sh in relayout["shapes"] or []:
        if sh.get("type") == "path" and sh.get("path"):
            verts = hn.parse_plotly_path(sh["path"])
            if len(verts) >= 3:
                polys.append({"vertices": verts})
    return polys


# ---------------------------------------------------------------------------
# ポリゴン名テーブル同期（store → table、既存の編集名は idx で保持）
# ---------------------------------------------------------------------------
@callback(
    Output("hne_polygon_table", "data"),
    Input("hne_polygons_store", "data"),
    State("hne_polygon_table", "data"),
    prevent_initial_call=True,
)
def hne_sync_polygon_table(polys, table):
    polys = polys or []
    prev = {int(r.get("idx", i)): r.get("name") for i, r in enumerate(table or [])}
    rows = []
    for i, p in enumerate(polys):
        name = prev.get(i) or f"領域{i + 1}"
        rows.append({"idx": i, "name": name, "nv": len(p.get("vertices", []))})
    return rows


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
    fig = go.Figure(go.Scattergl(x=x, y=y, mode="markers", marker=marker, hoverinfo="skip",
                                 name="TIC"))
    # 対応点（TIC 側）
    tic_pts = (lm or {}).get("tic", [])
    if tic_pts:
        tx = [p[0] for p in tic_pts]; ty = [p[1] for p in tic_pts]
        fig.add_trace(go.Scatter(x=tx, y=ty, mode="markers+text",
                                 marker=dict(size=10, color="red", symbol="x"),
                                 text=[str(i + 1) for i in range(len(tx))],
                                 textposition="top center", name="対応点", hoverinfo="skip"))
    # 変換済みポリゴン（アフィンがあれば）
    if affine and affine.get("M") and polys:
        M = np.array(affine["M"], dtype=float)
        for i, p in enumerate(polys):
            v = p.get("vertices") or []
            if len(v) >= 3:
                msi = hn.apply_affine(v, M)
                xs = list(msi[:, 0]) + [msi[0, 0]]
                ys = list(msi[:, 1]) + [msi[0, 1]]
                fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", fill="toself",
                                         line=dict(color="royalblue"), opacity=0.35,
                                         name=f"領域{i + 1}", hoverinfo="skip"))
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), template="plotly_white",
                      dragmode=_dragmode(mode), showlegend=False)
    fig.update_yaxes(scaleanchor="x", scaleratio=1)
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
    prevent_initial_call=True,
)
def hne_image_figure(img, lm, polys, opacity, mode):
    if not img:
        return _empty_fig("H&E をアップロードしてください")
    w, h = img["width"], img["height"]
    fig = go.Figure(go.Image(source=img["src"],
                             opacity=float(opacity) if opacity is not None else 1.0))
    # 対応点（H&E 側）
    hne_pts = (lm or {}).get("hne", [])
    if hne_pts:
        hx = [p[0] for p in hne_pts]; hy = [p[1] for p in hne_pts]
        fig.add_trace(go.Scatter(x=hx, y=hy, mode="markers+text",
                                 marker=dict(size=10, color="red", symbol="x"),
                                 text=[str(i + 1) for i in range(len(hx))],
                                 textposition="top center", hoverinfo="skip"))
    # ポリゴンを shape として再注入（rebuild 後も保持）
    shapes = []
    for p in polys or []:
        v = p.get("vertices") or []
        if len(v) >= 3:
            path = "M" + "L".join(f"{vx},{vy}" for vx, vy in v) + "Z"
            shapes.append(dict(type="path", path=path, line=dict(color="royalblue"),
                               fillcolor="rgba(65,105,225,0.2)"))
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), template="plotly_white",
                      dragmode=_dragmode(mode), shapes=shapes, showlegend=False,
                      newshape=dict(line=dict(color="royalblue")))
    fig.update_xaxes(visible=False, range=[0, w])
    fig.update_yaxes(visible=False, range=[h, 0], scaleanchor="x", scaleratio=1)
    return fig


# ---------------------------------------------------------------------------
# 領域割当 → 領域×クラスタ集計表
# ---------------------------------------------------------------------------
def _named_polygons(polys, table):
    """geometry(store) と name(table) を idx で結合した polygon リスト（MSI 変換前）。"""
    names = {int(r.get("idx", i)): (r.get("name") or f"領域{i+1}")
             for i, r in enumerate(table or [])}
    out = []
    for i, p in enumerate(polys or []):
        out.append({"name": names.get(i, f"領域{i+1}"), "vertices": p.get("vertices") or []})
    return out


@callback(
    Output("hne_result_area", "children"),
    Input("hne_assign_btn", "n_clicks"),
    State("hne_sample_select", "value"),
    State("hne_polygons_store", "data"),
    State("hne_polygon_table", "data"),
    State("hne_affine_store", "data"),
    State("hne_rotation_store", "data"),
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def hne_assign_and_summarize(n, sample, polys, table, affine, rotation, rds_path):
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
    polys_msi = hn.transform_polygons(_named_polygons(polys, table), M)
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
    State("hne_polygon_table", "data"),
    State("hne_affine_store", "data"),
    State("hne_rotation_store", "data"),
    State("seurat_rds_path_store", "data"),
    State("seurat_cache_dir_store", "data"),
    prevent_initial_call=True,
)
def hne_export_csv(n, sample, polys, table, affine, rotation, rds_path, cache_dir_str):
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
        polys_msi = hn.transform_polygons(_named_polygons(polys, table), M)
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
