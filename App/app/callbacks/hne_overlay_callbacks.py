# =============================================================================
# MSI Analysis Application - 解剖×クラスタ（H&E オーバーレイ）コールバック
# =============================================================================
# フェーズ1: 個体選択 / TIC 表示 / H&E アップロード・表示 / 不透明度。
# 読込済みインタラクティブ解析の plot_data（_interactive_data）を再利用する。
# =============================================================================

import base64
import io
import logging

import numpy as np
import plotly.graph_objects as go
from dash import callback, Input, Output, State, no_update

logger = logging.getLogger("msi.hne_overlay")


def _empty_fig(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=msg, showarrow=False, font=dict(color="#888", size=13))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), template="plotly_white")
    return fig


def _get_plot_data(rds_path):
    """読込済みインタラクティブ解析の plot_data を取得（active key を合わせる）。"""
    from app.callbacks.interactive_callbacks import _interactive_data, _set_active_key
    if rds_path:
        _set_active_key(rds_path)
    return _interactive_data.get("plot_data")


# ---------------------------------------------------------------------------
# 個体(Sample)ドロップダウンの populate（タブ表示・解析読込で更新）
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
    df = _get_plot_data(rds_path)
    if df is None or "Sample" not in df.columns:
        return [], None, "インタラクティブ解析で解析を読み込んでください。"
    samples = sorted(str(s) for s in df["Sample"].dropna().unique())
    opts = [{"label": s, "value": s} for s in samples]
    val = current if current in samples else (samples[0] if samples else None)
    has_sp = "SpatialX" in df.columns and "SpatialY" in df.columns
    status = f"{len(df):,} spot / {len(samples)} 個体" + ("" if has_sp else "  ※空間座標なし")
    return opts, val, status


# ---------------------------------------------------------------------------
# TIC 図（選択した個体の spot を TotalCount で濃淡表示。空間ビューと同じ Y 反転）
# ---------------------------------------------------------------------------
@callback(
    Output("hne_tic_graph", "figure"),
    Input("hne_sample_select", "value"),
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def hne_tic_figure(sample, rds_path):
    df = _get_plot_data(rds_path)
    if df is None or not sample:
        return _empty_fig("解析と個体を選択してください")
    if "SpatialX" not in df.columns or "SpatialY" not in df.columns:
        return _empty_fig("空間座標がありません")
    d = df[df["Sample"].astype(str) == str(sample)]
    if d.empty:
        return _empty_fig("該当 spot がありません")
    x = d["SpatialX"].to_numpy(dtype=float)
    y = -d["SpatialY"].to_numpy(dtype=float)  # 空間ビューと同じ向き
    if "TotalCount" in d.columns:
        tic = d["TotalCount"].to_numpy(dtype=float)
        marker = dict(size=3, symbol="square", color=tic, colorscale="Greys",
                      showscale=True, colorbar=dict(title="TIC", thickness=10))
    else:
        marker = dict(size=3, symbol="square", color="#444")
    fig = go.Figure(go.Scattergl(x=x, y=y, mode="markers", marker=marker, hoverinfo="skip"))
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), template="plotly_white", dragmode="pan")
    fig.update_yaxes(scaleanchor="x", scaleratio=1)
    return fig


# ---------------------------------------------------------------------------
# H&E アップロード → Store（base64 + 寸法）
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
        raw = base64.b64decode(b64)
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        w, h = img.size
        return ({"src": contents, "width": int(w), "height": int(h),
                 "name": filename or "H&E"},
                f"{filename}（{w}×{h}px）")
    except Exception as e:  # noqa: BLE001
        logger.warning("H&E 画像の読込に失敗: %s", e)
        return no_update, f"画像の読込に失敗: {e}"


# ---------------------------------------------------------------------------
# H&E 図（アップロード画像を背景表示。座標は画素＝左上原点）
# ---------------------------------------------------------------------------
@callback(
    Output("hne_image_graph", "figure"),
    Input("hne_image_store", "data"),
    Input("hne_opacity", "value"),
    prevent_initial_call=True,
)
def hne_image_figure(img, opacity):
    if not img:
        return _empty_fig("H&E をアップロードしてください")
    w, h = img["width"], img["height"]
    fig = go.Figure()
    fig.add_layout_image(dict(
        source=img["src"], xref="x", yref="y",
        x=0, y=0, sizex=w, sizey=h, sizing="stretch",
        opacity=float(opacity) if opacity is not None else 1.0, layer="below",
    ))
    # 画像座標（行=上が0）。y 範囲を [h,0] にして上下を画像通りに。
    fig.update_xaxes(visible=False, range=[0, w], constrain="domain")
    fig.update_yaxes(visible=False, range=[h, 0], scaleanchor="x", scaleratio=1)
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), template="plotly_white", dragmode="pan")
    return fig
