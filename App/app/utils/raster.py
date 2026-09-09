"""ラスター描画ヘルパー（PPTX 静的出力と画面表示の共通基盤）。

MSI の空間/feature 図は 1 枚あたり数万〜十万点の散布で、kaleido(SVG) 静的出力が非常に遅い。
これらを **``go.Heatmap``（データ座標つき）**に置き換えると、点数に依存しない一定コストで
描画でき、既存の軸設定（scaleanchor / reversed / range）をそのまま活かせるため向きズレも起きない。

- 規則グリッド（回転が 90 度の倍数＋反転なら軸平行のまま）: `bin_to_grid` / `grid_index`。
  格子と判定できない（任意角回転・不規則座標）場合は ``None`` を返し散布経路へフォールバック。
- 非格子（UMAP 埋め込み）: `umap_hist_grid` で固定解像度に集約。

★ ver66.0: 従来このモジュールは PPTX 専用で、docstring にも「対話 UI 側の図は WebGL で
  ブラウザ描画され速いので変更しない」と書いていた。だが速度は問題の一部でしかなかった。

  散布のマーカーサイズは **画面ピクセル単位**なので、データ座標の拡大率が変わると
  「隣接スポットがちょうど接する大きさ」が崩れる。ズーム（scrollZoom 有効）すれば
  間隔だけが広がって**隙間が開き**、縮小すれば重なる。しかもズーム/パンは
  `assets/relayout_filter.js` でサーバへ送らない設計なので、サーバ側は追従しようがない。
  自動計算 (`_calc_zero_gap_marker_size`) が作画領域の高さを決め打ちしていたのも、
  「サーバはクライアントの画素寸法を知り得ない」という同じ原因の裏返しだった。

  セルの大きさをデータ座標で決めれば、この調整自体が不要になる。MSI は本来
  「決まった位置に四角い画素を隙間なく並べたもの」なので、そちらが実体に近い。

環境変数:
  ``PPTX_RASTER=0``    … PPTX 出力のラスターを無効化（従来の散布経路へ）
  ``SPATIAL_RASTER=0`` … 画面 (Spatial / Feature Plot) のラスターを無効化
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

# ★ ver66.0: 画面用の切替は PPTX とは別キーにする。同じ `PPTX_RASTER` を流用すると
#   「PPTX だけ従来の散布に戻したい」人が画面まで巻き添えで戻り、その逆も起きる。
SCREEN_RASTER_ENABLED = str(
    os.environ.get("SPATIAL_RASTER", "1")
).strip().lower() not in ("0", "false", "no", "off", "")

# 間隔推定がノイズを拾って格子が爆発した場合の保険（格子ではないと判断）。
_MAX_CELLS = int(os.environ.get("PPTX_RASTER_MAX_CELLS", str(6_000_000)))
_MAX_CELLS_FACTOR = 40
# UMAP 等の非格子ラスターの解像度（長辺のビン数）。
_UMAP_MAX_DIM = int(os.environ.get("PPTX_RASTER_UMAP_DIM", "400"))


def raster_enabled() -> bool:
    """PPTX 静的出力のラスター描画が有効か。"""
    return RASTER_ENABLED


def screen_raster_enabled() -> bool:
    """画面 (Spatial Mapping / Feature Plot) のラスター描画が有効か。"""
    return SCREEN_RASTER_ENABLED


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


def grid_index(px, py, min_unique_ratio: float = 0.99):
    """規則グリッド割当を返す（複数レイヤ合成用）。

    Returns (ix, iy, xc, yc) または None。ix/iy は各点のセル添字、xc/yc はセル中心座標。
    px,py は同一長・全要素有限であること（アラインメント保証のため）。

    ★ ver66.0: 「別々の点が同じセルに潰れていないか」を検査して、潰れていれば
      None（＝散布へフォールバック）を返すようにした。

      同じセルに 2 点以上入ると、ラスターでは **後から書いた点が前の点を上書きする**。
      例外も None も出ないため、画面には「それらしく見えるが実際は別物」の図が
      黙って出てしまう（ver52.5 の行順ずれと同じ、いちばん気づけない壊れ方）。
      散布なら両方が描かれるので、そちらへ落ちるほうが安全。

      MSI は 1 スポット = 1 座標なので、正しいデータなら衝突は起きない。実際に
      効くのは座標が重複しているとき（データの取り違え・結合ミス等）。
      なお `_detect_step` は「ユニーク値の**最小**の正の間隔」を採るので、
      間隔を過小評価することはあっても過大評価はしない（過小評価の側は
      `_MAX_CELLS` / `_MAX_CELLS_FACTOR` が受け止める）。
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
    if min_unique_ratio:
        keys = iy.astype(np.int64) * np.int64(nx) + ix.astype(np.int64)
        if np.unique(keys).size < min_unique_ratio * px.size:
            logger.info("grid_index: セルの衝突が多すぎるため格子とみなさない "
                        "(unique=%d / points=%d)", np.unique(keys).size, px.size)
            return None
    xc = x0 + np.arange(nx) * sx
    yc = y0 + np.arange(ny) * sy
    return ix, iy, xc, yc


def grid_shape(gi):
    """`grid_index` の戻り値から z の形 (ny, nx) を返す。"""
    _ix, _iy, xc, yc = gi
    return (len(yc), len(xc))


def fill_grid(shape, ix, iy, values, fill=np.nan):
    """格子添字 (ix, iy) の位置に `values` を置いた 2D 配列を返す（空きセル = NaN）。

    `grid_index` で添字を **1 回だけ**取り、背景・前景・選択…と層ごとにこれで z を作る。

    ★ ver66.0: 従来この 3 行 (`np.full` → 代入) は PPTX 側の層ごとに直書きされていた
      (`interactive_pptx._build_cluster_spatial_panel_fig`)。画面側でも同じ層合成を
      するようになったので、書き写して 2 箇所に増やすのではなく共通化する。

    shape: (ny, nx)。ix/iy/values は同じ長さか、values はスカラーでもよい。
    """
    z = np.full(shape, fill, dtype=float)
    if len(ix):
        z[iy, ix] = values
    return z


def fill_grid_labels(shape, ix, iy, labels):
    """hover 用に、格子の各セルへ文字列を置いた 2D の object 配列を返す（空きは None）。

    `go.Heatmap` の `text` / `customdata` にそのまま渡せる。

    ★ ver66.0: 散布では「1 トレース = 1 クラスタ」だったのでホバー文字列は
      スカラー 1 個で済んでいた (ver46.2)。ラスターは全クラスタが 1 枚の z に
      入るため、セルごとに文字列を持たせないとどのクラスタか出せない。
      実測では 250x200 格子・20 クラスタで gzip 後 34KB と小さい。
    """
    a = np.full(shape, None, dtype=object)
    if len(ix):
        a[iy, ix] = labels
    return a


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
                  colorbar=None, name=None, *, text=None, customdata=None,
                  hovertemplate=None, hoverinfo=None, opacity=None, meta=None):
    """データ座標つき ``go.Heatmap`` トレースを返す（空きビン=NaN は透明）。

    ★ ver66.0: `hoverinfo="skip"` 固定をやめ、ホバーを出す層を作れるようにした。
      PPTX 出力にはホバーが無いので固定で足りていたが、画面ではクラスタ名や強度を
      出す必要がある。`hovertemplate` を渡したのに `hoverinfo="skip"` のままだと
      **何も出ない**（skip が優先される）ので、渡されたときは既定値を付けない。

    ホバー文字列は `text` / `customdata` に入れて `%{text}` / `%{customdata}` で
    参照すること。テンプレートへ f-string で直接埋めてはいけない（クラスタ名や
    化合物名は利用者が付けるもので、"%{x}" のような記法を含みうる。ver46.3 の教訓）。
    """
    kwargs = dict(
        z=z, x=xc, y=yc, colorscale=colorscale, zmin=zmin, zmax=zmax,
        zsmooth=False, showscale=showscale,
        colorbar=colorbar if colorbar is not None else None,
        name=name or "",
        # ★ ver66.0: 既定値と同じだが**明示する**。ここが 0 でなくなった瞬間に
        #   「隣接セルの間に隙間が出る」＝本改修が解決した問題そのものが戻る。
        #   テーマや将来の plotly の既定変更に委ねない。
        xgap=0, ygap=0,
        # ★ ver66.0: 空セル (z=NaN) をホバー対象にしない。散布では点が無い場所に
        #   ホバーは出なかったので、これが無いと組織の外側でも吹き出しが出て退行する。
        hoverongaps=False,
    )
    if hovertemplate is not None:
        kwargs["hovertemplate"] = hovertemplate
    if hoverinfo is not None:
        kwargs["hoverinfo"] = hoverinfo
    elif hovertemplate is None:
        kwargs["hoverinfo"] = "skip"     # 従来どおり（背景層など）
    if text is not None:
        kwargs["text"] = text
    if customdata is not None:
        kwargs["customdata"] = customdata
    if opacity is not None:
        kwargs["opacity"] = opacity
    if meta is not None:
        kwargs["meta"] = meta
    return go.Heatmap(**kwargs)
