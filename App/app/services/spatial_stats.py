# =============================================================================
# MSI Analysis Application - 空間自己相関（Moran's I）
# =============================================================================
# MSI の各 feature（m/z 画像）が「空間的にまとまっているか」を 1 つのスコアで
# 定量する。pixel-level の探索的ランキング/QC に使う指標であり、
# 群間差の統計的推論（p 値主張）には使わない（=本モジュールの設計意図）。
#
# 設計:
#   - pixel はグリッド座標（SpatialX/SpatialY）。近傍は rook(4) か queen(8)。
#   - 重み w_ij ∈ {0,1}（隣接=1）。Moran's I = (N/W)·(Σ_ij w_ij z_i z_j)/(Σ_i z_i^2)。
#   - 依存は numpy のみ（scipy 不使用）。座標は整数格子として扱う。
#   - permutation p 値は shortlist 用に任意提供（既定では計算しない）。
# =============================================================================

from __future__ import annotations

from typing import Optional

import numpy as np

_ROOK = ((1, 0), (-1, 0), (0, 1), (0, -1))
_QUEEN = _ROOK + ((1, 1), (1, -1), (-1, 1), (-1, -1))


def _grid_keys(ix: np.ndarray, iy: np.ndarray):
    """整数格子座標を一意な int64 キーへ符号化（負・大値も安全に扱える）。"""
    ix = ix.astype(np.int64)
    iy = iy.astype(np.int64)
    y0 = int(iy.min())
    x0 = int(ix.min())
    height = int(iy.max()) - y0 + 1
    # offset で隣接探索するため両側に余白を持たせる
    stride = height + 2
    keys = (ix - x0 + 1) * stride + (iy - y0 + 1)
    return keys, x0, y0, stride


def build_edges(coords, connectivity: str = "rook"):
    """格子座標から隣接エッジ（有向）を返す。

    Args:
        coords: (N,2) の (x,y)。整数格子前提（float でも round して扱う）。
        connectivity: "rook"（4近傍）or "queen"（8近傍）。
    Returns:
        (edge_i, edge_j): それぞれ (E,) の int 配列。各無向隣接は両方向に現れる。
    """
    coords = np.asarray(coords, dtype=float)
    if coords.ndim != 2 or coords.shape[1] != 2:
        raise ValueError("coords は (N,2) が必要です")
    ix = np.rint(coords[:, 0]).astype(np.int64)
    iy = np.rint(coords[:, 1]).astype(np.int64)
    keys, x0, y0, stride = _grid_keys(ix, iy)

    order = np.argsort(keys, kind="mergesort")
    keys_sorted = keys[order]

    offsets = _QUEEN if connectivity == "queen" else _ROOK
    src_list = []
    dst_list = []
    for dx, dy in offsets:
        nbr = (ix - x0 + 1 + dx) * stride + (iy - y0 + 1 + dy)
        pos = np.searchsorted(keys_sorted, nbr)
        pos_clipped = np.clip(pos, 0, keys_sorted.size - 1)
        hit = keys_sorted[pos_clipped] == nbr
        src = np.nonzero(hit)[0]
        dst = order[pos_clipped[hit]]
        src_list.append(src)
        dst_list.append(dst)
    if not src_list:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    return np.concatenate(src_list), np.concatenate(dst_list)


def morans_i(coords, values, connectivity: str = "rook", edges=None) -> float:
    """単一 feature の Moran's I を返す（空間構造が無い/定数/近傍ゼロは nan）。"""
    values = np.asarray(values, dtype=float)
    n = values.shape[0]
    if n < 3:
        return float("nan")
    if edges is None:
        edge_i, edge_j = build_edges(coords, connectivity)
    else:
        edge_i, edge_j = edges
    w = edge_i.size
    if w == 0:
        return float("nan")
    z = values - np.nanmean(values)
    denom = float(np.nansum(z * z))
    if denom <= 0:
        return float("nan")
    num = float(np.nansum(z[edge_i] * z[edge_j]))
    return (n / w) * (num / denom)


def morans_i_batch(coords, value_matrix, connectivity: str = "rook") -> np.ndarray:
    """複数 feature（value_matrix: (N,F)）の Moran's I を返す（エッジは1回だけ構築）。"""
    value_matrix = np.asarray(value_matrix, dtype=float)
    if value_matrix.ndim == 1:
        value_matrix = value_matrix[:, None]
    n, f = value_matrix.shape
    edge_i, edge_j = build_edges(coords, connectivity)
    w = edge_i.size
    out = np.full(f, np.nan, dtype=float)
    if w == 0 or n < 3:
        return out
    z = value_matrix - np.nanmean(value_matrix, axis=0, keepdims=True)
    denom = np.nansum(z * z, axis=0)
    num = np.nansum(z[edge_i, :] * z[edge_j, :], axis=0)
    good = denom > 0
    out[good] = (n / w) * (num[good] / denom[good])
    return out


def morans_i_permutation(
    coords,
    values,
    n_perm: int = 199,
    connectivity: str = "rook",
    seed: int = 0,
):
    """shortlist 用の permutation 検定。観測 I と片側 p 値（I が大きいほど有意）を返す。

    注意: 計算コストが高いので、Moran's I で上位に来た feature にのみ適用すること。
    """
    values = np.asarray(values, dtype=float)
    edges = build_edges(coords, connectivity)
    obs = morans_i(coords, values, connectivity, edges=edges)
    if not np.isfinite(obs):
        return obs, float("nan")
    rng = np.random.default_rng(seed)
    ge = 1  # +1 で観測自身を数える（保守的）
    for _ in range(n_perm):
        perm = rng.permutation(values)
        if morans_i(coords, perm, connectivity, edges=edges) >= obs:
            ge += 1
    return obs, ge / (n_perm + 1)


def spatial_autocorr_table(coords, value_matrix, feature_names=None,
                           connectivity: str = "rook"):
    """feature ごとの Moran's I を降順で返す（順位付け/QC 用の軽量サマリ）。

    Returns: list[dict(feature, morans_i)]（pandas 非依存）。
    """
    scores = morans_i_batch(coords, value_matrix, connectivity)
    f = scores.shape[0]
    names = list(feature_names) if feature_names is not None else [str(i) for i in range(f)]
    rows = [{"feature": names[i], "morans_i": float(scores[i])} for i in range(f)]
    rows.sort(key=lambda r: (np.isnan(r["morans_i"]), -(r["morans_i"] if np.isfinite(r["morans_i"]) else 0)))
    return rows
