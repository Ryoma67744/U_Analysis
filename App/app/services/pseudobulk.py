# =============================================================================
# MSI Analysis Application - ROI/サンプル pseudobulk と sample-level 比較
# =============================================================================
# pixel-level 検定は「探索的ランキング」に留め、群間主張は ROI/サンプル単位に
# 集約（pseudobulk）してから比較する。隣接 pixel の擬似多重（pseudoreplication）
# による見かけの有意差を避けるための層（Cassese 2016 / Lee & Han 2024 の考え方）。
#
#   - aggregate_pseudobulk: pixel を {sample}/{ROI}/{cluster} 単位に平均集約。
#   - sample_level_test: サンプル単位の値で 2 群比較（Welch t）。p 値は自前実装。
#   - 反復（群あたりサンプル数）が不足する場合は記述統計のみ（p を出さない）。
#
# 依存は numpy/pandas のみ（scipy 不使用、t 分布の p 値は betai で算出）。
# =============================================================================

from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np
import pandas as pd

_EPS = 1e-9


# --------------------------------------------------------------------------
# 自前の正則化不完全ベータ関数（t 分布の両側 p 値に使用）
# --------------------------------------------------------------------------
def _betacf(a: float, b: float, x: float, itmax: int = 200, eps: float = 3e-12) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    bt = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t: float, df: float) -> float:
    """自由度 df の Student-t における両側 p 値。"""
    if df <= 0 or not np.isfinite(t):
        return float("nan")
    x = df / (df + t * t)
    return _betai(df / 2.0, 0.5, x)


def welch_ttest(a, b):
    """Welch の t 検定（等分散を仮定しない）。(t, df, p) を返す。"""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    na, nb = a.size, b.size
    if na < 2 or nb < 2:
        return float("nan"), float("nan"), float("nan")
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se2 = va / na + vb / nb
    if se2 <= 0:
        return float("nan"), float("nan"), float("nan")
    t = (a.mean() - b.mean()) / math.sqrt(se2)
    df = se2 ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    return t, df, t_two_sided_p(t, df)


def bh_adjust(pvals) -> np.ndarray:
    """Benjamini-Hochberg 補正（NaN は NaN のまま、有効値のみで補正）。"""
    p = np.asarray(pvals, dtype=float)
    out = np.full(p.shape, np.nan)
    finite = np.isfinite(p)
    if not finite.any():
        return out
    vals = p[finite]
    n = vals.size
    order = np.argsort(vals)
    ranked = vals[order] * n / (np.arange(1, n + 1))
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    adj = np.empty(n)
    adj[order] = np.clip(ranked, 0, 1)
    out[finite] = adj
    return out


# --------------------------------------------------------------------------
# 集約と sample-level 比較
# --------------------------------------------------------------------------
def aggregate_pseudobulk(meta: "pd.DataFrame", expr,
                         group_cols: Sequence[str],
                         feature_names: Optional[Sequence[str]] = None,
                         agg: str = "mean") -> "pd.DataFrame":
    """pixel を group_cols（例 ['Sample','Cluster'] や ['Sample','ROI','Cluster']）
    単位に集約する。meta の行と expr の行は同順（同じ pixel 並び）であること。

    Returns: 行=group、列=feature（+ n_pixels）の DataFrame。
    """
    meta = meta.reset_index(drop=True)
    if isinstance(expr, pd.DataFrame):
        feats = list(expr.columns) if feature_names is None else list(feature_names)
        E = expr.reset_index(drop=True)
    else:
        arr = np.asarray(expr)
        feats = list(feature_names) if feature_names is not None else [f"f{i}" for i in range(arr.shape[1])]
        E = pd.DataFrame(arr, columns=feats)
    if len(E) != len(meta):
        raise ValueError("meta と expr の行数が一致しません")

    key = meta[list(group_cols)].astype(str).agg("|".join, axis=1)
    full = E.copy()
    full["__group__"] = key.values
    gb = full.groupby("__group__", sort=True)
    if agg == "median":
        pb = gb[feats].median()
    elif agg == "sum":
        pb = gb[feats].sum()
    else:
        pb = gb[feats].mean()
    pb.insert(0, "n_pixels", gb.size())
    # group キーを列に展開（Sample/ROI/Cluster を後段で使えるように）
    parts = pb.index.to_series().str.split("|", expand=True)
    parts.columns = list(group_cols)
    for c in group_cols:
        pb[c] = parts[c].values
    return pb.reset_index(drop=True)


def sample_level_test(pb: "pd.DataFrame",
                      condition_map: dict,
                      sample_col: str = "Sample",
                      feature_cols: Optional[Sequence[str]] = None,
                      min_per_group: int = 2) -> dict:
    """サンプル単位に集約した pseudobulk から 2 群比較（Welch t + BH）。

    Args:
        pb: aggregate_pseudobulk の出力（sample_col を含む）。
        condition_map: {sample: condition}（2 群）。
    Returns:
        dict(result=DataFrame, descriptive_only=bool, n_a, n_b, levels, note)
    """
    meta_cols = {"n_pixels", sample_col, "Cluster", "ROI", "__group__"}
    feats = list(feature_cols) if feature_cols is not None else [
        c for c in pb.columns if c not in meta_cols]

    cond = pb[sample_col].map(condition_map)
    levels = [c for c in pd.unique(cond.dropna())]
    if len(levels) != 2:
        raise ValueError(f"2 群が必要ですが {len(levels)} 群です: {levels}")
    a_mask = (cond == levels[0]).values
    b_mask = (cond == levels[1]).values
    n_a, n_b = int(a_mask.sum()), int(b_mask.sum())
    descriptive = n_a < min_per_group or n_b < min_per_group

    rows = []
    for f in feats:
        a = pb.loc[a_mask, f].to_numpy(dtype=float)
        b = pb.loc[b_mask, f].to_numpy(dtype=float)
        ma, mb = (a.mean() if a.size else np.nan), (b.mean() if b.size else np.nan)
        l2fc = math.log2((ma + _EPS) / (mb + _EPS)) if (np.isfinite(ma) and np.isfinite(mb)) else np.nan
        if descriptive:
            t = df = p = np.nan
        else:
            t, df, p = welch_ttest(a, b)
        rows.append({"feature": f, f"mean_{levels[0]}": ma, f"mean_{levels[1]}": mb,
                     "log2fc": l2fc, "t": t, "df": df, "p_val": p})
    res = pd.DataFrame(rows)
    if not descriptive:
        res["p_val_adj"] = bh_adjust(res["p_val"].to_numpy())
    note = ("サンプル反復が不足（群あたり <%d）のため記述統計のみ。p 値は出していません。"
            % min_per_group) if descriptive else (
        "サンプル単位（pseudobulk）の群間比較。pixel-level ランキングとは別物です。")
    return {"result": res, "descriptive_only": descriptive,
            "n_a": n_a, "n_b": n_b, "levels": list(levels), "note": note}
