# =============================================================================
# MSI Analysis Application - H&E オーバーレイ／ポリゴン領域 純ロジック
# =============================================================================
# 解剖学的領域（H&E 上でポリゴン指定）を MSI の spot へ割り当て、
# 「領域 × クラスタ」単位で集計・MetaboAnalyst 用エクスポートを行うための
# 純粋計算ロジック（UI 非依存・テスト可能）。
#
# 設計（壁打ち確定）:
#   - 位置合わせ: H&E と TIC の対応点（ランドマーク）から最小二乗でアフィン推定。
#     ポリゴンは H&E 画素座標で描き、アフィンで MSI 座標へ変換して内包判定する。
#   - 内包判定: ベクトル化レイキャスト（matplotlib/shapely 不要、numpy のみ）。
#   - エクスポート: region_cluster（例 脳_cluster1）群ごとの平均強度。列は化合物名優先。
# 依存: numpy / pandas のみ。
# =============================================================================

from __future__ import annotations

import re

import numpy as np
import pandas as pd


def parse_plotly_path(path_str):
    """Plotly drawclosedpath の SVG パス文字列（例 "M10,20L30,40L15,60Z"）から
    頂点列 [(x,y), ...] を抽出する。"""
    if not path_str:
        return []
    nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(path_str))
    vals = [float(n) for n in nums]
    return [(vals[i], vals[i + 1]) for i in range(0, len(vals) - 1, 2)]


# ---------------------------------------------------------------------------
# アフィン（ランドマーク位置合わせ）
# ---------------------------------------------------------------------------
def estimate_affine(src, dst):
    """対応点から 2x3 アフィン行列 M を最小二乗推定（dst ≈ M @ [x, y, 1]）。

    Args:
        src: (N,2) 変換元（例: H&E 画素座標）, N>=3
        dst: (N,2) 変換先（例: MSI 空間座標）
    Returns:
        M: (2,3) ndarray
    """
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    if src.ndim != 2 or src.shape[1] != 2 or src.shape != dst.shape:
        raise ValueError("src/dst は同数・(N,2) が必要です")
    n = src.shape[0]
    if n < 3:
        raise ValueError("対応点は3点以上が必要です")
    # [x y 1] を 2 行ぶん（x',y'）に展開した設計行列
    A = np.zeros((2 * n, 6), dtype=float)
    b = np.zeros(2 * n, dtype=float)
    A[0::2, 0] = src[:, 0]; A[0::2, 1] = src[:, 1]; A[0::2, 2] = 1.0
    A[1::2, 3] = src[:, 0]; A[1::2, 4] = src[:, 1]; A[1::2, 5] = 1.0
    b[0::2] = dst[:, 0]
    b[1::2] = dst[:, 1]
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    return sol.reshape(2, 3)


def apply_affine(pts, M):
    """点列 (N,2) にアフィン M(2,3) を適用して (N,2) を返す。"""
    pts = np.asarray(pts, dtype=float)
    M = np.asarray(M, dtype=float)
    if pts.size == 0:
        return pts.reshape(-1, 2)
    return (M[:, :2] @ pts.T + M[:, 2:3]).T


def invert_affine(M):
    """アフィン M(2,3) の逆変換 (2,3) を返す（dst→src）。"""
    M = np.asarray(M, dtype=float)
    A = M[:, :2]
    t = M[:, 2]
    A_inv = np.linalg.inv(A)
    t_inv = -A_inv @ t
    return np.hstack([A_inv, t_inv.reshape(2, 1)])


def affine_residual(src, dst, M):
    """対応点の当てはまり残差（RMS, dst 座標系）。位置合わせ品質の目安に使う。"""
    pred = apply_affine(src, M)
    d = np.asarray(dst, float) - pred
    return float(np.sqrt(np.mean(np.sum(d * d, axis=1))))


def apply_rotation(x, y, rotation):
    """MSI 回転設定 {angle, flip_h, flip_v} で (x, y) を重心基準で反転+回転する。

    `interactive_spatial._transform_coords` と同一結果の純関数（Dash 非依存）。
    spot 表示・領域割当の双方に同じ個体の全 (x, y) を渡すこと（重心一致＝一貫）。
    """
    rotation = rotation or {}
    angle = float(rotation.get("angle", 0) or 0)
    flip_h = bool(rotation.get("flip_h", False))
    flip_v = bool(rotation.get("flip_v", False))
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if angle == 0 and not flip_h and not flip_v:
        return x, y
    cx, cy = np.nanmean(x), np.nanmean(y)
    if flip_h:
        x = 2 * cx - x
    if flip_v:
        y = 2 * cy - y
    if angle == 0:
        return x, y
    cx, cy = np.nanmean(x), np.nanmean(y)  # 反転後の中心で回転
    rad = np.radians(angle)
    cos_a, sin_a = np.cos(rad), np.sin(rad)
    x_rot = cos_a * (x - cx) - sin_a * (y - cy) + cx
    y_rot = sin_a * (x - cx) + cos_a * (y - cy) + cy
    return x_rot, y_rot


# ---------------------------------------------------------------------------
# 点-内包判定（ベクトル化レイキャスト）
# ---------------------------------------------------------------------------
def points_in_polygon(xs, ys, polygon):
    """点(xs,ys)が多角形 polygon=[(x,y),...] の内部かを判定（even-odd 規則）。

    Returns: bool ndarray（xs と同形）。頂点 < 3 なら全 False。
    """
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    poly = np.asarray(polygon, dtype=float)
    inside = np.zeros(xs.shape, dtype=bool)
    n = poly.shape[0]
    if n < 3:
        return inside
    j = n - 1
    for i in range(n):
        xi, yi = poly[i, 0], poly[i, 1]
        xj, yj = poly[j, 0], poly[j, 1]
        cond = (yi > ys) != (yj > ys)
        denom = (yj - yi)
        denom = np.where(denom == 0.0, 1e-12, denom)
        x_cross = (xj - xi) * (ys - yi) / denom + xi
        inside ^= cond & (xs < x_cross)
        j = i
    return inside


# ---------------------------------------------------------------------------
# 領域割当（spot → 領域名）
# ---------------------------------------------------------------------------
def assign_regions(df, polygons, x_col="SpatialX", y_col="SpatialY"):
    """各 spot に最初にマッチした領域名を割り当てる。

    Args:
        df: SpatialX/Y を持つ DataFrame
        polygons: [{"name": str, "vertices": [(x,y),...]} ...]（MSI 座標）
    Returns:
        pd.Series（df.index に揃う。未割当は None）
    """
    xs = pd.to_numeric(df[x_col], errors="coerce").to_numpy(dtype=float)
    ys = pd.to_numeric(df[y_col], errors="coerce").to_numpy(dtype=float)
    valid = ~(np.isnan(xs) | np.isnan(ys))  # NA 空間は除外（堅牢化）
    region = np.array([None] * len(df), dtype=object)
    for poly in polygons or []:
        name = poly.get("name")
        verts = poly.get("vertices") or []
        if not name or len(verts) < 3:
            continue
        unassigned = region == None  # noqa: E711 (object 配列の None 比較)
        inside = points_in_polygon(xs, ys, verts) & valid & unassigned
        region[inside] = name
    return pd.Series(region, index=df.index)


def transform_polygons(polygons_px, M):
    """H&E 画素座標のポリゴン群をアフィン M で MSI 座標へ変換して返す。"""
    out = []
    for poly in polygons_px or []:
        verts = poly.get("vertices") or []
        if len(verts) >= 1:
            verts_msi = apply_affine(verts, M).tolist()
        else:
            verts_msi = []
        out.append({**poly, "vertices": verts_msi})
    return out


def apply_region_groups(polygons):
    """同じ `group` のポリゴンを1つの ROI に束ねるため、各ポリゴンの実効 `name` を
    その group の代表名に置換した新リストを返す（vertices 等は保持）。

    - 代表名: その group 内で最初に現れた非空 `name`。無ければ `f"領域{group}"`。
    - `group` が未設定（None/空文字）のポリゴンは従来どおり自分の `name` のまま
      （＝同名 ROI は引き続き name 単位で統合される）。
    これを `assign_regions` の直前に適用すると、集計・色分け・エクスポートは既存ロジックの
    まま「同 group → 同 ROI」に集約される。
    """
    polys = polygons or []

    def _norm_group(p):
        g = p.get("group")
        if g is None:
            return None
        s = str(g).strip()
        return s or None

    rep: dict = {}
    for p in polys:
        g = _norm_group(p)
        if g is None:
            continue
        nm = (p.get("name") or "").strip()
        if nm and not rep.get(g):
            rep[g] = nm

    out = []
    for i, p in enumerate(polys):
        g = _norm_group(p)
        if g is not None:
            eff = rep.get(g) or f"領域{g}"
        else:
            eff = p.get("name") or f"領域{i + 1}"
        out.append({**p, "name": eff})
    return out


def regions_from_overlay(sub_df, entry, x_col="SpatialX", y_col="SpatialY"):
    """1切片の H&E オーバーレイ保存状態 `entry` から、sub_df 各 spot の領域名 Series を返す。

    Args:
        sub_df: 当該切片の plot_data 部分（SpatialX/SpatialY を持つ）
        entry: {"polygons": [...], "landmarks": {"tic":[...], "hne":[...]},
                "rotation": {...}}（hne_overlay_state.json の個体 entry）
    Returns:
        pd.Series（sub_df.index に揃う。割当不可は None）

    affine は保存されないため対応点(landmarks)から再推定する。group 統合
    (`apply_region_groups`) と MSI 回転(`apply_rotation`) を内部で適用。対応点が3対未満／
    polygon が無ければ全 None を返す。
    """
    none_series = pd.Series([None] * len(sub_df), index=sub_df.index, dtype=object)
    entry = entry or {}
    polys = entry.get("polygons") or []
    lm = entry.get("landmarks") or {}
    tic = lm.get("tic") or []
    hne = lm.get("hne") or []
    npair = min(len(tic), len(hne))
    if not polys or npair < 3:
        return none_series
    try:
        M = estimate_affine(hne[:npair], tic[:npair])
    except Exception:
        return none_series
    polys_msi = transform_polygons(apply_region_groups(polys), M)
    rx, ry = apply_rotation(
        pd.to_numeric(sub_df[x_col], errors="coerce").to_numpy(dtype=float),
        pd.to_numeric(sub_df[y_col], errors="coerce").to_numpy(dtype=float),
        entry.get("rotation"))
    tmp = pd.DataFrame({x_col: rx, y_col: ry}, index=sub_df.index)
    return assign_regions(tmp, polys_msi, x_col=x_col, y_col=y_col)


# ---------------------------------------------------------------------------
# 領域 × クラスタ 集計
# ---------------------------------------------------------------------------
def region_cluster_counts(df, region_col="region", cluster_col="Cluster"):
    """領域×クラスタの spot 数・領域内比率（領域が付いた行のみ）。

    Returns: DataFrame[region, Cluster, count, pct_in_region]
    """
    cols = [region_col, cluster_col, "count", "pct_in_region"]
    if region_col not in df.columns:
        return pd.DataFrame(columns=cols)
    sub = df[df[region_col].notna()]
    if sub.empty:
        return pd.DataFrame(columns=cols)
    g = (sub.groupby([region_col, cluster_col]).size()
         .rename("count").reset_index())
    tot = g.groupby(region_col)["count"].transform("sum")
    g["pct_in_region"] = (g["count"] / tot * 100.0).round(2)
    return g[cols]


def region_cluster_label(region, cluster):
    """`脳_cluster1` のような領域×クラスタの結合ラベル。"""
    return f"{region}_cluster{cluster}"


# ---------------------------------------------------------------------------
# MetaboAnalyst 用エクスポート（region×cluster 群ごとの平均強度）
# ---------------------------------------------------------------------------
def build_region_cluster_export(df, expr_df, region_col="region",
                                cluster_col="Cluster", cellid_col="CellID",
                                sample_col=None, feature_name_map=None, min_spots=1):
    """region×cluster 群ごとの平均強度（行=群, 列=feature）を返す。

    Args:
        df: region 列 + Cluster + CellID を持つ DataFrame（領域割当済み）
        expr_df: CellID + feature(m/z) 列の強度行列（元 Spatial 強度を推奨）
        sample_col: 指定すると群ラベルを `{sample}_{region}_{cluster}`（例 `E15_Brain_23`）
            にして全切片を1ファイルに統合する。None なら `{region}_cluster{cluster}`（後方互換）。
        feature_name_map: {feature列名 -> 化合物名} があれば列名を化合物名へ。
        min_spots: この spot 数未満の群は出力しない。
    Returns:
        DataFrame（先頭列 "Group" = 群ラベル、以降 feature 平均）。ROI 未割当（region=NA）は除外。
    """
    if region_col not in df.columns:
        return pd.DataFrame()
    sub = df[df[region_col].notna()].copy()
    if sub.empty or expr_df is None or expr_df.empty:
        return pd.DataFrame()
    if sample_col and sample_col in sub.columns:
        sub["__rc__"] = [f"{s}_{r}_{c}" for s, r, c in zip(
            sub[sample_col].astype(str), sub[region_col].astype(str),
            sub[cluster_col].astype(str))]
    else:
        sub["__rc__"] = [region_cluster_label(r, c)
                         for r, c in zip(sub[region_col].astype(str),
                                         sub[cluster_col].astype(str))]
    expr = expr_df.set_index(cellid_col)
    feat_cols = list(expr.columns)

    labels, rows = [], []
    for rc, grp in sub.groupby("__rc__"):
        ids = [cid for cid in grp[cellid_col].tolist() if cid in expr.index]
        if len(ids) < max(1, int(min_spots)):
            continue
        labels.append(rc)
        rows.append(expr.loc[ids, feat_cols].mean(axis=0).to_numpy())

    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows, columns=feat_cols)
    if feature_name_map:
        out = out.rename(columns={c: feature_name_map.get(c, c) for c in out.columns})
    out.insert(0, "Group", labels)
    return out


def build_groups_table(df, sample_col="Sample", region_col="region",
                       cluster_col="Cluster", cellid_col="CellID"):
    """region 付き行から `{CellID, Group}` の小さな表を返す（B経路で R に渡す入力）。

    Group ラベルは `build_region_cluster_export`（sample_col 指定時）と**同一規約**
    `{sample}_{region}_{cluster}`（例 `E15_Brain_23`）。ROI 未割当（region=NA）は除外。
    巨大な強度行列を作らず、R 側で対象 cell の群平均だけ計算させるための入力になる。
    """
    cols = [cellid_col, "Group"]
    if region_col not in df.columns or cellid_col not in df.columns:
        return pd.DataFrame(columns=cols)
    sub = df[df[region_col].notna()]
    if sub.empty:
        return pd.DataFrame(columns=cols)
    groups = [f"{s}_{r}_{c}" for s, r, c in zip(
        sub[sample_col].astype(str), sub[region_col].astype(str),
        sub[cluster_col].astype(str))]
    return pd.DataFrame({cellid_col: sub[cellid_col].astype(str).to_numpy(),
                        "Group": groups})


def rename_export_columns(df, feature_name_map):
    """先頭 `Group` 列を保ったまま feature 列名（m/z）を化合物名へ置換して返す。

    `build_region_cluster_export` と同一の置換ルール（map に無い列はそのまま）。
    R 集計（B経路）が返す m/z 列名 CSV に Python 側で適用する。
    """
    if df is None or getattr(df, "empty", True) or not feature_name_map:
        return df
    return df.rename(columns={c: feature_name_map.get(c, c) for c in df.columns})
