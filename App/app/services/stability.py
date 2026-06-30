# =============================================================================
# MSI Analysis Application - UMAP/クラスタ安定性メトリクス
# =============================================================================
# 「1 枚の UMAP の形」に意味を読み込みすぎないための品質保証メトリクス。
#   - ARI: 2 つのクラスタ割当の一致度（seed/subsample 再計算の安定性）
#   - per-cluster Jaccard: 各クラスタが再計算でどれだけ保たれるか（旗付けに使用）
#   - silhouette: クラスタの分離度（部分標本で計算）
#   - trustworthiness: 2D 埋め込みが元空間の近傍をどれだけ保つか（部分標本）
#
# 依存は numpy のみ（scikit-learn/scipy 不使用）。大規模 pixel は部分標本化する。
# 旗の目安（scclusteval/Hennig 由来）: Jaccard <0.6 不安定 / >=0.85 安定。
# =============================================================================

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

UNSTABLE_THRESHOLD = 0.6
STABLE_THRESHOLD = 0.85


# --------------------------------------------------------------------------
# クラスタ割当の一致度
# --------------------------------------------------------------------------
def adjusted_rand_index(labels_a, labels_b) -> float:
    """Adjusted Rand Index（範囲おおむね [-0.5, 1]、1=完全一致、~0=偶然）。"""
    a = np.asarray(labels_a)
    b = np.asarray(labels_b)
    if a.shape[0] != b.shape[0]:
        raise ValueError("ラベル長が一致しません")
    n = a.shape[0]
    if n == 0:
        return float("nan")
    _, a_idx = np.unique(a, return_inverse=True)
    _, b_idx = np.unique(b, return_inverse=True)
    cont = np.zeros((a_idx.max() + 1, b_idx.max() + 1), dtype=np.int64)
    np.add.at(cont, (a_idx, b_idx), 1)

    def _comb2(x):
        x = np.asarray(x, dtype=np.float64)
        return x * (x - 1.0) / 2.0

    sum_comb = _comb2(cont).sum()
    sum_comb_a = _comb2(cont.sum(axis=1)).sum()
    sum_comb_b = _comb2(cont.sum(axis=0)).sum()
    total = _comb2(np.array([n]))[0]
    expected = (sum_comb_a * sum_comb_b) / total if total > 0 else 0.0
    max_index = 0.5 * (sum_comb_a + sum_comb_b)
    if max_index == expected:
        return 1.0
    return float((sum_comb - expected) / (max_index - expected))


def cluster_jaccard(members_a: np.ndarray, members_b: np.ndarray) -> float:
    """2 つの boolean メンバーシップの Jaccard 係数。"""
    a = np.asarray(members_a, dtype=bool)
    b = np.asarray(members_b, dtype=bool)
    inter = int(np.count_nonzero(a & b))
    union = int(np.count_nonzero(a | b))
    return inter / union if union else 0.0


def match_clusters_jaccard(labels_ref, labels_alt) -> dict:
    """ref の各クラスタについて、alt の最も重なるクラスタとの最大 Jaccard を返す。

    Returns: {ref_label: best_jaccard}
    """
    ref = np.asarray(labels_ref)
    alt = np.asarray(labels_alt)
    out = {}
    alt_labels = np.unique(alt)
    alt_masks = {al: (alt == al) for al in alt_labels}
    for rc in np.unique(ref):
        rmask = ref == rc
        best = 0.0
        for al in alt_labels:
            j = cluster_jaccard(rmask, alt_masks[al])
            if j > best:
                best = j
        out[_key(rc)] = best
    return out


def _key(v):
    """numpy スカラーを素の Python 値（dict キー用）に変換。"""
    try:
        return v.item()
    except AttributeError:
        return v


def stability_flag(jaccard: float,
                   unstable: float = UNSTABLE_THRESHOLD,
                   stable: float = STABLE_THRESHOLD) -> str:
    """Jaccard を stable / borderline / unstable に分類。"""
    if not np.isfinite(jaccard):
        return "unknown"
    if jaccard >= stable:
        return "stable"
    if jaccard < unstable:
        return "unstable"
    return "borderline"


# --------------------------------------------------------------------------
# 埋め込み品質（部分標本で O(m^2)）
# --------------------------------------------------------------------------
def _subsample_idx(n: int, max_n: int, seed: int) -> Optional[np.ndarray]:
    if n <= max_n:
        return None
    rng = np.random.default_rng(seed)
    return rng.choice(n, size=max_n, replace=False)


def _pairwise_sq_dists(X: np.ndarray) -> np.ndarray:
    """二乗ユークリッド距離行列（数値誤差で負になった分は 0 にクリップ）。"""
    sq = np.sum(X * X, axis=1)
    d2 = sq[:, None] + sq[None, :] - 2.0 * (X @ X.T)
    return np.maximum(d2, 0.0)


def silhouette_score(X, labels, max_n: int = 2000, seed: int = 0) -> float:
    """平均シルエット幅（[-1,1]）。大規模時は部分標本化。クラスタ<2 は nan。"""
    X = np.asarray(X, dtype=float)
    labels = np.asarray(labels)
    n = X.shape[0]
    idx = _subsample_idx(n, max_n, seed)
    if idx is not None:
        X = X[idx]
        labels = labels[idx]
    m = X.shape[0]
    uniq = np.unique(labels)
    if uniq.size < 2 or m < 3:
        return float("nan")
    D = np.sqrt(_pairwise_sq_dists(X))
    masks = {u: (labels == u) for u in uniq}
    sizes = {u: int(masks[u].sum()) for u in uniq}
    sil = np.zeros(m, dtype=float)
    for i in range(m):
        li = labels[i]
        if sizes[li] <= 1:
            sil[i] = 0.0
            continue
        same = masks[li].copy()
        same[i] = False
        a = D[i, same].mean()
        b = np.inf
        for u in uniq:
            if u == li:
                continue
            b = min(b, D[i, masks[u]].mean())
        denom = max(a, b)
        sil[i] = 0.0 if denom == 0 else (b - a) / denom
    return float(sil.mean())


def trustworthiness(X_high, X_low, n_neighbors: int = 5,
                    max_n: int = 2000, seed: int = 0) -> float:
    """低次元埋め込みが元空間の局所近傍をどれだけ保つか（[0,1]、1=完全）。

    sklearn.manifold.trustworthiness と同等の定義。大規模時は部分標本化。
    """
    X_high = np.asarray(X_high, dtype=float)
    X_low = np.asarray(X_low, dtype=float)
    n = X_high.shape[0]
    idx = _subsample_idx(n, max_n, seed)
    if idx is not None:
        X_high = X_high[idx]
        X_low = X_low[idx]
    m = X_high.shape[0]
    k = int(n_neighbors)
    if m - 1 <= k or k < 1:
        return float("nan")

    d_high = _pairwise_sq_dists(X_high)
    d_low = _pairwise_sq_dists(X_low)

    # 元空間での順位（自分自身を除く）
    rank_high = np.argsort(np.argsort(d_high, axis=1), axis=1)
    # 低次元での k 近傍（自分自身を除いた先頭 k）
    nn_low = np.argsort(d_low, axis=1)[:, 1:k + 1]

    t = 0.0
    for i in range(m):
        for j in nn_low[i]:
            r = rank_high[i, j]
            if r > k:
                t += (r - k)
    norm = 2.0 / (m * k * (2.0 * m - 3.0 * k - 1.0))
    return float(1.0 - norm * t)


# --------------------------------------------------------------------------
# 集約（複数 seed/subsample の再計算結果をまとめて旗付け）
# --------------------------------------------------------------------------
def aggregate_seed_stability(labels_ref, labels_alts: Sequence,
                             unstable: float = UNSTABLE_THRESHOLD,
                             stable: float = STABLE_THRESHOLD) -> dict:
    """基準クラスタリングと複数の再計算結果から、ARI とクラスタ別安定性をまとめる。

    Args:
        labels_ref: 基準のクラスタラベル（長さ N）
        labels_alts: 同じ N に対する再計算ラベルの列（seed/subsample 違い）
    Returns:
        dict(ari_list, mean_ari, cluster_jaccard_mean, cluster_flags)
    """
    ref = np.asarray(labels_ref)
    alts = [np.asarray(a) for a in labels_alts]
    ari_list = [adjusted_rand_index(ref, a) for a in alts]

    per_cluster: dict = {}
    for a in alts:
        m = match_clusters_jaccard(ref, a)
        for rc, j in m.items():
            per_cluster.setdefault(rc, []).append(j)

    cluster_mean = {rc: float(np.mean(js)) for rc, js in per_cluster.items()}
    flags = {rc: stability_flag(mj, unstable, stable) for rc, mj in cluster_mean.items()}
    return {
        "ari_list": [float(x) for x in ari_list],
        "mean_ari": float(np.mean(ari_list)) if ari_list else float("nan"),
        "cluster_jaccard_mean": cluster_mean,
        "cluster_flags": flags,
        "thresholds": {"unstable": unstable, "stable": stable},
    }
