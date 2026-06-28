# =============================================================================
# MSI Analysis Application - Selection / Colorscale pure helpers
# インタラクティブ解析の「選択統計」「Top-N 抽出」「カラースケール変換」など
# Dash に依存しない純ロジック。単体テスト可能 (pandas/numpy のみ依存)。
#
# ここに置く関数は副作用なし・I/O なし。Dash コールバックからは
# interactive_selection.py 経由で利用する。
# =============================================================================

from __future__ import annotations

import re

import numpy as np
import pandas as pd

__all__ = [
    "extract_selected_cell_ids",
    "natural_cluster_key",
    "compute_selection_summary",
    "log_transform_intensities",
    "top_n_markers",
]


def natural_cluster_key(value) -> tuple:
    """クラスタ ID を自然順ソートするためのキー。
    数値クラスタは数値順、"3-a" のようなサブクラスタや文字列は後置。
    color_utils.cluster_sort_key と同等の並びだが、config(dotenv) 依存を
    避けるためここに最小実装を持つ。"""
    s = str(value)
    m = re.match(r"^(\d+)", s)
    if m:
        return (0, int(m.group(1)), s)
    return (1, 0, s)


def extract_selected_cell_ids(selected_data) -> list[str]:
    """Plotly の selectedData (lasso/box 共通) から CellID 一覧を抽出。

    UMAP/feature/spatial の各 trace は point の ``text`` に CellID を持つため
    そこから取り出す。重複は順序を保って除去する。"""
    if not selected_data:
        return []
    points = selected_data.get("points") or []
    out: list[str] = []
    seen: set[str] = set()
    for p in points:
        t = p.get("text")
        if t is None:
            continue
        t = str(t)
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def compute_selection_summary(df, selected_ids, expr=None,
                              feature_name=None) -> dict:
    """選択された CellID 集合について plot_data を集計したサマリを返す。

    Parameters
    ----------
    df : DataFrame  (CellID / Cluster / Sample 列を想定)
    selected_ids : 選択された CellID のリスト
    expr : df の行と同じ並び・長さの強度配列 (任意)。表示中 feature の平均算出用。
    feature_name : expr に対応する feature 名 (任意)

    Returns
    -------
    dict : {n_selected, n_total, pct, by_cluster[], by_sample[],
            feature_name, mean_intensity}
    """
    n_total = int(len(df)) if df is not None else 0
    result = {
        "n_selected": 0,
        "n_total": n_total,
        "pct": 0.0,
        "by_cluster": [],
        "by_sample": [],
        "feature_name": feature_name,
        "mean_intensity": None,
    }
    if df is None or not selected_ids:
        return result

    sel = {str(x) for x in selected_ids}
    mask = df["CellID"].astype(str).isin(sel)
    n = int(mask.sum())
    result["n_selected"] = n
    if n_total:
        result["pct"] = round(100.0 * n / n_total, 1)
    if n == 0:
        return result

    sub = df[mask]

    def _composition(series) -> list[dict]:
        vc = series.astype(str).value_counts()
        rows = [
            {"key": k, "count": int(v), "pct": round(100.0 * int(v) / n, 1)}
            for k, v in vc.items()
        ]
        rows.sort(key=lambda r: natural_cluster_key(r["key"]))
        return rows

    if "Cluster" in sub.columns:
        result["by_cluster"] = _composition(sub["Cluster"])
    if "Sample" in sub.columns:
        # サンプルは件数降順 (自然順より件数が見たい)
        rows = _composition(sub["Sample"])
        rows.sort(key=lambda r: r["count"], reverse=True)
        result["by_sample"] = rows

    if expr is not None and feature_name:
        try:
            arr = np.asarray(expr, dtype=float)
            if len(arr) == len(df):
                vals = arr[mask.to_numpy()]
                vals = vals[~np.isnan(vals)]
                if len(vals):
                    result["mean_intensity"] = float(np.mean(vals))
        except Exception:
            pass

    return result


def log_transform_intensities(values):
    """強度配列を log1p 変換して返す (表示用)。

    MSI の強度はダイナミックレンジが広く、線形表示では少数の高強度点に
    飽和して大半の構造が潰れる。負値があれば最小値分シフトしてから log1p。
    NaN は保持する。"""
    arr = np.asarray(values, dtype=float)
    finite = arr[np.isfinite(arr)]
    shift = 0.0
    if finite.size and float(finite.min()) < 0.0:
        shift = -float(finite.min())
    return np.log1p(arr + shift)


def top_n_markers(records, n, sort_col="p_val_adj_raw",
                  ascending=True) -> list[dict]:
    """マーカー records を sort_col で並べ替え、上位 n 件を返す。

    n が None / 0 / 負なら全件 (並べ替えのみ)。Loupe の
    "Top 10/20/50/100/All" 出力に対応する。"""
    if not records:
        return []
    df = pd.DataFrame(records)
    if sort_col in df.columns:
        df = df.sort_values(sort_col, ascending=ascending, na_position="last")
    if n and int(n) > 0:
        df = df.head(int(n))
    return df.to_dict("records")
