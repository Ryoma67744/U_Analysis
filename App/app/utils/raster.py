"""PPTX 静的出力向けラスター描画ヘルパー。

MSI の空間/feature 図は 1 枚あたり数万〜十万点の散布で、kaleido(SVG) 静的出力が非常に遅い。
これらを **``go.Heatmap``（データ座標つき）**に置き換えると、点数に依存しない一定コストで
描画でき、既存の軸設定（scaleanchor / reversed / range）をそのまま活かせるため向きズレも起きない。

対象は PPTX 専用ビルダー（`_build_feature_plot_fig` / `_build_cluster_slide_combined_fig`）のみ。
対話 UI 側の図は WebGL でブラウザ描画され速いので変更しない。

- 規則グリッド（回転が 90 度の倍数＋反転なら軸平行のまま）: `bin_to_grid` / `grid_index`。
  格子と判定できない（任意角回転・不規則座標）場合は ``None`` を返し散布経路へフォールバック。
- 非格子（UMAP 埋め込み）: `umap_hist_grid` で固定解像度に集約。

環境変数 ``PPTX_RASTER=0`` で無効化（従来の散布＋タイムアウト経路にフォールバック）。
"""

from __future__ import annotations

import logging
import os

import numpy as np
import plotly.graph_objects as go

logger = logging.getLogger(__name__)

RASTER_ENABLED = str(os.environ.get("PPTX_RASTER", "1")).strip().lower() not in (
    "0", "false", "no", "off", "",
)

# 間隔推定がノイズを拾って格子が爆発した場合の保険（格子ではないと判断）。
_MAX_CELLS = int(os.environ.get("PPTX_RASTER_MAX_CELLS", str(6_000_000)))
_MAX_CELLS_FACTOR = 40
# UMAP 等の非格子ラスターの解像度（長辺のビン数）。
_UMAP_MAX_DIM = int(os.environ.get("PPTX_RASTER_UMAP_DIM", "400"))


def raster_enabled() -> bool:
    """ラスター描画が有効か。"""
    return RASTER_ENABLED


# ---------------------------------------------------------------------------
# グリッド化
# ---------------------------------------------------------------------------

def _detect_step(vals: np.ndarray):
    """1 次元座標の最小正間隔を推定。格子でなければ None。"""
    u = np.unique(vals[np.isfinite(vals)])
    if u.size < 2:
        return None
    d = np.diff(u)
    d = d[d > 1e-9]
    if d.size == 0:
        return None
    return float(np.min(d))


def _grid_axes(px, py):
    """(px,py) の規則格子パラメータ (x0,y0,sx,sy,nx,ny) を返す。格子でなければ None。"""
    sx = _detect_step(px)
    sy = _detect_step(py)
    if not sx or not sy:
        return None
    x0, y0 = float(px.min()), float(py.min())
    nx = int(round((float(px.max()) - x0) / sx)) + 1
    ny = int(round((float(py.max()) - y0) / sy)) + 1
    if nx < 2 or ny < 2:
        return None
    if nx * ny > _MAX_CELLS or nx * ny > _MAX_CELLS_FACTOR * px.size:
        return None
    return x0, y0, sx, sy, nx, ny


def bin_to_grid(px, py, values, agg: str = "mean"):
    """変換後座標 (px,py) と値を規則グリッドに集約する（連続値向け）。

    Returns (z, xc, yc) または None。
      z (ny,nx) float, 空きビン=NaN。row=j は yc[j]（昇順）。
      xc/yc はセル中心のデータ座標（``go.Heatmap`` の x/y にそのまま渡す）。
    格子と判定できない/点数不足/過大な場合は None（呼び出し側は散布へフォールバック）。
    """
    px = np.asarray(px, dtype=float).ravel()
    py = np.asarray(py, dtype=float).ravel()
    v = np.asarray(values, dtype=float).ravel()
    if px.size < 4 or px.shape != py.shape or px.shape != v.shape:
        return None
    m = np.isfinite(px) & np.isfinite(py) & np.isfinite(v)
    if int(m.sum()) < 4:
        return None
    px, py, v = px[m], py[m], v[m]
    ax = _grid_axes(px, py)
    if ax is None:
        return None
    x0, y0, sx, sy, nx, ny = ax
    ix = np.clip(np.round((px - x0) / sx).astype(int), 0, nx - 1)
    iy = np.clip(np.round((py - y0) / sy).astype(int), 0, ny - 1)
    z = np.full((ny, nx), np.nan, dtype=float)
    if agg == "mean":
        s = np.zeros((ny, nx), dtype=float)
        c = np.zeros((ny, nx), dtype=float)
        np.add.at(s, (iy, ix), v)
        np.add.at(c, (iy, ix), 1.0)
        nz = c > 0
        z[nz] = s[nz] / c[nz]
    else:  # 'last'
        z[iy, ix] = v
    xc = x0 + np.arange(nx) * sx
    yc = y0 + np.arange(ny) * sy
    return z, xc, yc


def grid_index(px, py):
    """規則グリッド割当を返す（複数レイヤ合成用）。

    Returns (ix, iy, xc, yc) または None。ix/iy は各点のセル添字、xc/yc はセル中心座標。
    px,py は同一長・全要素有限であること（アラインメント保証のため）。
    """
    px = np.asarray(px, dtype=float).ravel()
    py = np.asarray(py, dtype=float).ravel()
    if px.size < 4 or px.shape != py.shape:
        return None
    if not np.isfinite(px).all() or not np.isfinite(py).all():
        return None
    ax = _grid_axes(px, py)
    if ax is None:
        return None
    x0, y0, sx, sy, nx, ny = ax
    ix = np.clip(np.round((px - x0) / sx).astype(int), 0, nx - 1)
    iy = np.clip(np.round((py - y0) / sy).astype(int), 0, ny - 1)
    xc = x0 + np.arange(nx) * sx
    yc = y0 + np.arange(ny) * sy
    return ix, iy, xc, yc


def umap_hist_grid(x, y, cat, x_range, y_range, max_dim: int = None):
    """非格子点 (UMAP 等) を固定解像度グリッドへ集約し、カテゴリ index の z を返す。

    cat: 各点の整数カテゴリ（大きい値ほど後で上書き＝highlight を前面に）。
    Returns (z, xc, yc) または None。z[j,i]=そのビンの最大 cat、空きは NaN。
    """
    if max_dim is None:
        max_dim = _UMAP_MAX_DIM
    x = np.asarray(x, dtype=float).ravel()
    y = np.asarray(y, dtype=float).ravel()
    cat = np.asarray(cat, dtype=float).ravel()
    if x.size == 0 or x.shape != y.shape or x.shape != cat.shape:
        return None
    m = np.isfinite(x) & np.isfinite(y) & np.isfinite(cat)
    if not m.any():
        return None
    x, y, cat = x[m], y[m], cat[m]
    xmin, xmax = float(x_range[0]), float(x_range[1])
    ymin, ymax = float(y_range[0]), float(y_range[1])
    xspan, yspan = xmax - xmin, ymax - ymin
    if not (xspan > 0 and yspan > 0):
        return None
    if xspan >= yspan:
        w = int(max_dim)
        h = max(2, int(round(max_dim * yspan / xspan)))
    else:
        h = int(max_dim)
        w = max(2, int(round(max_dim * xspan / yspan)))
    ix = np.clip(np.round((x - xmin) / xspan * (w - 1)).astype(int), 0, w - 1)
    iy = np.clip(np.round((y - ymin) / yspan * (h - 1)).astype(int), 0, h - 1)
    z = np.full((h, w), np.nan, dtype=float)
    order = np.argsort(cat, kind="stable")  # 小さい cat から書き、大きい cat で上書き
    z[iy[order], ix[order]] = cat[order]
    xc = xmin + np.arange(w) / (w - 1) * xspan
    yc = ymin + np.arange(h) / (h - 1) * yspan
    return z, xc, yc


# ---------------------------------------------------------------------------
# Plotly トレース
# ---------------------------------------------------------------------------

def build_discrete_colorscale(hex_list):
    """離散色（index 0..k-1）用のステップ状カラースケールを作る。

    Returns (colorscale, zmin, zmax)。z に整数 index、空きは NaN を与えると
    index i がちょうど hex_list[i] に対応する。hex_list が空なら (None, 0, 1)。
    """
    k = len(hex_list)
    if k == 0:
        return None, 0.0, 1.0
    cs = []
    for i, hx in enumerate(hex_list):
        cs.append([i / k, hx])
        cs.append([(i + 1) / k, hx])
    return cs, -0.5, k - 0.5


def heatmap_trace(z, xc, yc, colorscale, zmin, zmax, showscale=False,
                  colorbar=None, name=None):
    """データ座標つき ``go.Heatmap`` トレースを返す（空きビン=NaN は透明）。"""
    return go.Heatmap(
        z=z, x=xc, y=yc, colorscale=colorscale, zmin=zmin, zmax=zmax,
        zsmooth=False, showscale=showscale,
        colorbar=colorbar if colorbar is not None else None,
        hoverinfo="skip", name=name or "",
    )
