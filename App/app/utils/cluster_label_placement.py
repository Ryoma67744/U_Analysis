"""クラスタ番号を最大の連続領域の内側へ配置する、描画から独立した幾何処理。

★ ver76.0: 全点の平均では、離れた島の間やリングの穴へ番号が集まる。
Spatial は観測格子、UMAP は共通の距離尺度で推定した占有領域を使う。
返す位置は必ず元の観測点で、クラスタ割当や埋め込み自体は変更しない。
UMAP の面積は表示ピクセルの面積ではないため、ズームや点サイズに依存しない。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from threading import Lock

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

# ★ ver76.0: 実データでの精度保証値ではなく、計算量を制限する初期設定。
# 長辺512は合成の複数島・リング・密度差テストで検証する。点径との連動はしない。
_UMAP_GRID_DIM = 512
_SPACING_QUANTILE = 0.90
_SPACING_MEDIAN_CAP = 3.0
_SUPPORT_RADIUS_FACTOR = 1.25
_MAX_GRID_CELLS = 1_048_576
_MAX_MASK_WORK = 32_000_000
_MAX_POINTS = 1_000_000
_QUERY_CHUNK = 65_536
_CROSS = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)
# 20万点UMAPの初期実測は約0.9秒。色・文字サイズだけの再描画で繰り返さない。
# 保持するのは結果のみだが、点数・注釈数・件数すべてに上限を付ける。
_CACHE_MAX_ENTRIES = 32
_CACHE_MAX_POINTS = 2_000_000
_CACHE_MAX_ANCHORS = 10_000
_ANCHOR_CACHE = OrderedDict()
_CACHE_LOCK = Lock()
_CACHE_POINTS = 0
_CACHE_ANCHORS = 0


@dataclass(frozen=True)
class LabelAnchor:
    """index は呼び出し元が渡した配列の位置（有限点の除去前）。"""

    index: int
    x: float
    y: float
    area: float
    reason: str


@dataclass(frozen=True)
class _Grid:
    x0: float
    y0: float
    sx: float
    sy: float
    nx: int
    ny: int

    def indices(self, x, y):
        ix = np.rint((x - self.x0) / self.sx).astype(np.int64)
        iy = np.rint((y - self.y0) / self.sy).astype(np.int64)
        return ix, iy


def _tie_min(values):
    """丸め誤差程度の同値候補を残す。入力順は最終的な座標比較で排除する。"""
    best = float(np.min(values))
    tolerance = 1e-10 * max(abs(best), float(np.max(np.abs(values))), 1e-12)
    return np.flatnonzero(values <= best + tolerance)


def _fallback(x, y, indices, reason):
    """中央値に最も近い実在点。領域推定不能時も島の間の架空の点を返さない。"""
    xx, yy = x[indices], y[indices]
    # 座標の原点が遠くても小さい差を同距離と誤認しないよう、範囲で正規化する。
    with np.errstate(over="ignore"):
        scale = max(float(xx.max() - xx.min()), float(yy.max() - yy.min()))
    if np.isfinite(scale) and scale > 0:
        xx, yy = (xx - xx.min()) / scale, (yy - yy.min()) / scale
    else:
        scale = max(float(np.max(np.abs(xx))), float(np.max(np.abs(yy))), 1.0)
        xx, yy = xx / scale, yy / scale
    d2 = (xx - np.median(xx)) ** 2 + (yy - np.median(yy)) ** 2
    candidates = _tie_min(d2)
    chosen = candidates[np.lexsort((indices[candidates], xx[candidates], yy[candidates]))[0]]
    i = int(indices[chosen])
    return LabelAnchor(i, float(x[i]), float(y[i]), 0.0, reason)


def _axis(values):
    unique = np.unique(values)
    if unique.size < 2:
        return None
    differences = np.diff(unique)
    step = float(differences.min())
    span = float(unique[-1] - unique[0])
    if not np.isfinite(span) or not np.isfinite(step) or step <= 0:
        return None
    cells = span / step
    if not np.isfinite(cells) or cells > _MAX_GRID_CELLS:
        return None
    size = int(round(cells)) + 1
    reconstructed = unique[0] + np.rint((unique - unique[0]) / step) * step
    tolerance = step * 1e-6 + np.finfo(float).eps * max(1.0, float(np.max(np.abs(unique)))) * 16
    if np.any(np.abs(unique - reconstructed) > tolerance):
        return None
    return float(unique[0]), step, size


def _spatial_grid(x, y, reference):
    rx, ry = reference if reference is not None else (x, y)
    rx, ry = np.asarray(rx, dtype=float).ravel(), np.asarray(ry, dtype=float).ravel()
    if rx.shape != ry.shape:
        raise ValueError("grid_reference の x/y は同じ長さである必要があります")
    if rx.size > _MAX_POINTS:
        return None
    finite = np.isfinite(rx) & np.isfinite(ry)
    ax, ay = _axis(rx[finite]), _axis(ry[finite])
    if ax is None or ay is None:
        return None
    x0, sx, nx = ax
    y0, sy, ny = ay
    if nx * ny > _MAX_GRID_CELLS:
        return None
    grid = _Grid(x0, y0, sx, sy, nx, ny)
    # 参照範囲外・別座標系の入力を端のセルへ押し込めない。
    ix, iy = grid.indices(x, y)
    if np.any((ix < 0) | (ix >= nx) | (iy < 0) | (iy >= ny)):
        return None
    for values, reconstructed, step in (
        (x, x0 + ix * sx, sx), (y, y0 + iy * sy, sy),
    ):
        tolerance = step * 1e-6 + np.finfo(float).eps * max(1.0, float(np.max(np.abs(values)))) * 16
        if np.any(np.abs(values - reconstructed) > tolerance):
            return None
    return grid


def _unique_points(x, y, codes):
    """座標順で重複を整理し、異なるクラスタの同座標を曖昧値(-1)にする。"""
    points, first, inverse = np.unique(
        np.column_stack((x, y)), axis=0, return_index=True, return_inverse=True,
    )
    low = np.full(len(points), np.iinfo(np.int32).max, dtype=np.int32)
    high = np.full(len(points), -1, dtype=np.int32)
    np.minimum.at(low, inverse, codes)
    np.maximum.at(high, inverse, codes)
    conflict = low != high
    owners = codes[first].copy()
    owners[conflict] = -1
    conflict_codes = np.unique(codes[conflict[inverse]])
    return points, owners, conflict_codes


def _umap_grid(x, y, codes):
    """共通支持半径で制限した最近傍領域。クラスタごとの膨張で他領域を跨がない。"""
    xmin, ymin = float(x.min()), float(y.min())
    span = max(float(x.max() - xmin), float(y.max() - ymin))
    if not np.isfinite(span) or span <= 0:
        return None
    # 平行移動・単位変更と無関係に同じグリッドを作る。X/Y別々の正規化はしない。
    ux, uy = (x - xmin) / span, (y - ymin) / span
    points, owners, conflicts = _unique_points(ux, uy, codes)
    if len(points) < 3:
        return None
    centered = points - points.mean(axis=0)
    eigenvalues = np.linalg.eigvalsh(centered.T @ centered)
    if eigenvalues[0] <= eigenvalues[-1] * 1e-12:
        return None
    # np.unique の座標順で木を構築するため、等距離候補も入力行順に依存しない。
    tree = cKDTree(points)
    nearest = np.empty(len(points), dtype=float)
    for start in range(0, len(points), _QUERY_CHUNK):
        stop = min(start + _QUERY_CHUNK, len(points))
        nearest[start:stop] = tree.query(points[start:stop], k=[2], eps=0)[0][:, 0]
    # 疎な島にも同じ半径を使う一方、孤立外れ値だけで半径が膨張するのを制限。
    # 3倍も合成テスト用の初期値であり、極端な密度差の面積を保証する値ではない。
    spacing = min(float(np.quantile(nearest, _SPACING_QUANTILE)),
                  float(np.median(nearest)) * _SPACING_MEDIAN_CAP)
    radius = spacing * _SUPPORT_RADIUS_FACTOR
    if not np.isfinite(radius) or radius <= 0:
        return None
    xspan, yspan = float(ux.max()), float(uy.max())
    step = (max(xspan, yspan) + 2 * radius) / (_UMAP_GRID_DIM - 1)
    nx = min(_UMAP_GRID_DIM, int(np.ceil((xspan + 2 * radius) / step)) + 1)
    ny = min(_UMAP_GRID_DIM, int(np.ceil((yspan + 2 * radius) / step)) + 1)
    if nx * ny > _MAX_GRID_CELLS:
        return None
    grid = _Grid(-radius, -radius, step, step, nx, ny)
    owner = np.full(nx * ny, -1, dtype=np.int32)
    for start in range(0, owner.size, _QUERY_CHUNK):
        stop = min(start + _QUERY_CHUNK, owner.size)
        flat = np.arange(start, stop)
        query = np.column_stack((grid.x0 + (flat % nx) * step,
                                 grid.y0 + (flat // nx) * step))
        distances, point_indices = tree.query(query, k=1, eps=0)
        inside = distances <= radius
        owner[start:stop][inside] = owners[point_indices[inside]]
    return grid, owner.reshape(ny, nx), ux, uy, span, conflicts


def _choose_component(components, counts, sx, sy):
    """同面積なら全領域重心に近い島、なお同じなら走査順（y, x）で決める。"""
    candidates = np.flatnonzero(counts == counts[1:].max())
    candidates = candidates[candidates != 0]
    if len(candidates) == 1:
        return int(candidates[0])
    rows, cols = np.nonzero(components)
    ids = components[rows, cols]
    sum_x = np.bincount(ids, weights=cols, minlength=len(counts))
    sum_y = np.bincount(ids, weights=rows, minlength=len(counts))
    dx = (sum_x[candidates] / counts[candidates] - cols.mean()) * sx
    dy = (sum_y[candidates] / counts[candidates] - rows.mean()) * sy
    return int(candidates[_tie_min(dx * dx + dy * dy)[0]])


def _place_on_grid(grid, owner, gx, gy, codes, names, original, x, y,
                   reason, area_scale=1.0, conflicts=()):
    """各クラスタを逐次処理し、全クラスタ分の距離画像を同時に保持しない。"""
    ix, iy = grid.indices(gx, gy)
    ix = np.clip(ix, 0, grid.nx - 1)
    iy = np.clip(iy, 0, grid.ny - 1)
    work = 0
    anchors = {}
    # グリッド全体をクラスタ数回走査せず、bboxは一度に集計する。
    rows, cols = np.nonzero(owner >= 0)
    occupied_codes = owner[rows, cols]
    lo_x = np.full(len(names), grid.nx, dtype=np.int64)
    lo_y = np.full(len(names), grid.ny, dtype=np.int64)
    hi_x = np.full(len(names), -1, dtype=np.int64)
    hi_y = np.full(len(names), -1, dtype=np.int64)
    for output, positions, operation in (
        (lo_x, cols, np.minimum), (lo_y, rows, np.minimum),
        (hi_x, cols, np.maximum), (hi_y, rows, np.maximum),
    ):
        operation.at(output, occupied_codes, positions)
    conflict_set = set(conflicts)
    for code, name in enumerate(names):
        point_positions = np.flatnonzero(codes == code)
        indices = original[point_positions]
        fallback_reason = "fallback_unresolved_region"
        if code in conflict_set:
            anchors[name] = _fallback(x, y, indices, "fallback_conflicting_coordinates")
            continue
        if hi_x[code] < 0:
            anchors[name] = _fallback(x, y, indices, fallback_reason)
            continue
        x0, x1 = int(lo_x[code]), int(hi_x[code]) + 1
        y0, y1 = int(lo_y[code]), int(hi_y[code]) + 1
        work += (x1 - x0) * (y1 - y0)
        if work > _MAX_MASK_WORK:
            anchors[name] = _fallback(x, y, indices, "fallback_work_limit")
            continue
        mask = owner[y0:y1, x0:x1] == code
        components, _ = ndimage.label(mask, structure=_CROSS)
        counts = np.bincount(components.ravel())
        selected = _choose_component(components, counts, grid.sx, grid.sy)
        local_x, local_y = ix[point_positions] - x0, iy[point_positions] - y0
        within = ((local_x >= 0) & (local_x < x1 - x0)
                  & (local_y >= 0) & (local_y < y1 - y0))
        candidates = np.flatnonzero(within)
        candidates = candidates[components[local_y[candidates], local_x[candidates]] == selected]
        if not len(candidates):
            anchors[name] = _fallback(x, y, indices, fallback_reason)
            continue
        selected_mask = components == selected
        # ★ ver76.0: sampling を省くと、異方ピッチの組織で内側の距離を誤る。
        # 背景セル中心までの離散距離であり、矩形の厳密な内接半径とは区別する。
        distance = ndimage.distance_transform_edt(
            np.pad(selected_mask, 1), sampling=(grid.sy, grid.sx),
        )[1:-1, 1:-1]
        clearance = distance[local_y[candidates], local_x[candidates]]
        candidates = candidates[_tie_min(-clearance)]
        rr, cc = np.nonzero(selected_mask)
        cx = grid.x0 + (x0 + cc.mean()) * grid.sx
        cy = grid.y0 + (y0 + rr.mean()) * grid.sy
        positions = point_positions[candidates]
        d2 = (gx[positions] - cx) ** 2 + (gy[positions] - cy) ** 2
        candidates = candidates[_tie_min(d2)]
        positions = point_positions[candidates]
        order = np.lexsort((original[positions], gx[positions], gy[positions]))
        i = int(original[positions[order[0]]])
        area = float(counts[selected]) * grid.sx * grid.sy * area_scale ** 2
        anchors[name] = LabelAnchor(i, float(x[i]), float(y[i]), area, reason)
    return anchors


def _compute_label_anchors_uncached(x, y, clusters, *, kind="umap", grid_reference=None):
    """最大領域内の観測点を返す。表示スタイル・永続化・キャッシュは扱わない。

    grid_reference は Spatial の除外前サンプル座標 (x, y)。返す index は常に
    引数 x/y の行位置なので、呼び出し元の回転済み配列にもそのまま使える。
    入力不整合は ValueError、通常の幾何退化や資源超過は reason 付き実在点へ戻す。
    キャッシュ用の公開ラッパーからも、この純粋な計算部分を呼ぶ。
    """
    if kind not in ("spatial", "umap"):
        raise ValueError("kind は 'spatial' または 'umap' です")
    x, y = np.asarray(x, dtype=float).ravel(), np.asarray(y, dtype=float).ravel()
    labels = np.asarray(clusters).astype(str, copy=False).ravel()
    if x.shape != y.shape or x.shape != labels.shape:
        raise ValueError("x, y, clusters は同じ長さである必要があります")
    original = np.flatnonzero(np.isfinite(x) & np.isfinite(y))
    if original.size == 0:
        return {}
    px, py = x[original], y[original]
    names, codes = np.unique(labels[original], return_inverse=True)
    codes = codes.astype(np.int32)

    def fallback_all(reason):
        return {str(name): _fallback(x, y, original[codes == code], reason)
                for code, name in enumerate(names)}

    if original.size > _MAX_POINTS:
        return fallback_all("fallback_point_limit")
    if kind == "spatial":
        grid = _spatial_grid(px, py, grid_reference)
        if grid is not None:
            ix, iy = grid.indices(px, py)
            keys = iy * grid.nx + ix
            low = np.full(grid.nx * grid.ny, np.iinfo(np.int32).max, dtype=np.int32)
            high = np.full(grid.nx * grid.ny, -1, dtype=np.int32)
            np.minimum.at(low, keys, codes)
            np.maximum.at(high, keys, codes)
            conflicts = (high >= 0) & (low != high)
            conflict_codes = np.unique(codes[conflicts[keys]])
            high[conflicts] = -1
            return _place_on_grid(
                grid, high.reshape(grid.ny, grid.nx), px, py, codes, names,
                original, x, y, "spatial_region", conflicts=conflict_codes,
            )
    inferred = _umap_grid(px, py, codes)
    if inferred is None:
        return fallback_all("fallback_degenerate_geometry")
    grid, owner, ux, uy, scale, conflicts = inferred
    reason = "umap_region" if kind == "umap" else "spatial_inferred_region"
    return _place_on_grid(grid, owner, ux, uy, codes, names, original, x, y,
                          reason, area_scale=scale, conflicts=conflicts)


def _fingerprint(x, y, labels, kind, reference):
    """配列の行順・文字列内容・格子参照・アルゴリズム設定を省略せずキーに含む。"""
    digest = hashlib.blake2b(digest_size=32)
    settings = (kind, _UMAP_GRID_DIM, _SPACING_QUANTILE, _SPACING_MEDIAN_CAP,
                _SUPPORT_RADIUS_FACTOR, _MAX_GRID_CELLS, _MAX_MASK_WORK, _MAX_POINTS,
                reference is not None)
    digest.update(repr(settings).encode("ascii"))
    for values in (x, y, labels, *(reference or ())):
        digest.update(repr((values.shape, values.dtype.str)).encode("ascii"))
        digest.update(memoryview(values).cast("B"))
    return digest.digest()


def _clear_anchor_cache():
    """テスト・診断用。データ配列そのものはキャッシュしていない。"""
    global _CACHE_POINTS, _CACHE_ANCHORS
    with _CACHE_LOCK:
        _ANCHOR_CACHE.clear()
        _CACHE_POINTS = 0
        _CACHE_ANCHORS = 0


def compute_label_anchors(x, y, clusters, *, kind="umap", grid_reference=None):
    """最大領域内の元観測点を返す。内容ハッシュ付きの有限LRUで結果だけ再利用。

    入力の所有コピーを取るので、計算中・計算後に呼び出し側が配列を変更しても
    キャッシュ内容とキーの対応は壊れない。返り値のdictも呼び出しごとに独立する。
    grid_reference=(除外前x, 除外前y)。indexは常に引数x/yの行位置。
    """
    if kind not in ("spatial", "umap"):
        raise ValueError("kind は 'spatial' または 'umap' です")
    xx = np.array(x, dtype=float, copy=True).ravel()
    yy = np.array(y, dtype=float, copy=True).ravel()
    labels = np.asarray(clusters).astype(str, copy=True).ravel()
    if xx.shape != yy.shape or xx.shape != labels.shape:
        raise ValueError("x, y, clusters は同じ長さである必要があります")
    reference = None
    if grid_reference is not None:
        rx, ry = grid_reference
        reference = (np.array(rx, dtype=float, copy=True).ravel(),
                     np.array(ry, dtype=float, copy=True).ravel())
        if reference[0].shape != reference[1].shape:
            raise ValueError("grid_reference の x/y は同じ長さである必要があります")
    if len(xx) > _MAX_POINTS:
        return _compute_label_anchors_uncached(xx, yy, labels, kind=kind,
                                                grid_reference=reference)
    key = _fingerprint(xx, yy, labels, kind, reference)
    with _CACHE_LOCK:
        entry = _ANCHOR_CACHE.get(key)
        if entry is not None:
            _ANCHOR_CACHE.move_to_end(key)
            return dict(entry[1])
    # SciPyの計算中はロックを解放し、他サンプルの描画を直列化しない。
    result = _compute_label_anchors_uncached(xx, yy, labels, kind=kind,
                                             grid_reference=reference)
    weight = len(xx) + (len(reference[0]) if reference is not None else 0)
    if weight <= _CACHE_MAX_POINTS and len(result) <= _CACHE_MAX_ANCHORS:
        global _CACHE_POINTS, _CACHE_ANCHORS
        with _CACHE_LOCK:
            if key not in _ANCHOR_CACHE:
                _ANCHOR_CACHE[key] = (weight, tuple(result.items()))
                _CACHE_POINTS += weight
                _CACHE_ANCHORS += len(result)
            _ANCHOR_CACHE.move_to_end(key)
            while (len(_ANCHOR_CACHE) > _CACHE_MAX_ENTRIES
                   or _CACHE_POINTS > _CACHE_MAX_POINTS
                   or _CACHE_ANCHORS > _CACHE_MAX_ANCHORS):
                _, (old_weight, old_result) = _ANCHOR_CACHE.popitem(last=False)
                _CACHE_POINTS -= old_weight
                _CACHE_ANCHORS -= len(old_result)
    return result
