# =============================================================================
# MSI Analysis Application - 外部アノテーション根拠の取込・対応づけ・由来表示
# =============================================================================
# 方針（ユーザー確定）:
#   - アプリは代謝物名の良し悪しを「判定（診断）」しない。
#   - 各 m/z に対し、外部結果を取り込んで対応づけ、「何で付けた注釈か（由来）」を
#     表示する。出典が持つ指標（METASPACE の FDR 等）はそのまま併記する。
#   - 優先順位: ① LC/MS 照合 → ② METASPACE → ③ MS-DIAL → in-house → manual。
#   - 1 つの m/z に複数候補が付く場合は併記（断定しない）。
#
# 依存は pandas/numpy のみ。重い外部依存（metaspace2020 クライアント等）は持たない
# （将来 API 直結する場合も、本モジュールの正規化レコードに合わせて橋渡しする）。
#
# FUTURE(annot-provenance) — STATUS: 本モジュールは「エンジン」のみ実装＆単体テスト済みで、
#   現状どの UI／コールバック／アップロード経路にも未接続（＝アプリの挙動・画面は不変）。
#   取込の設計が固まり次第、build_feature_source_map() / format_annotation_label() を使って
#   各表示箇所に「由来(source)」を併記する想定。配線先は次を grep:  FUTURE(annot-provenance)
#     - app/utils/deg_utils.py（DEG 表に source 列）
#     - app/callbacks/interactive_calibration.py（外部由来の統合）
#     - app/callbacks/interactive_feature_lists.py（feature ピッカーのラベル）
#     - app/callbacks/interactive_deg.py（Volcano ラベル）
#     - app/callbacks/interactive_pptx.py（feature/Volcano タイトル）
#     - app/services/feature_lists.py（feature→由来サマリのサイドカー保存）
#     - app/layouts/interactive_tab.py（DEG 表 UI に 由来 列）
#   詳細・残課題: App/docs/MVP4_IMPLEMENTATION_STATUS.md
# =============================================================================

from __future__ import annotations

import math
from typing import Optional, Sequence, Union

import numpy as np
import pandas as pd

# 由来ラベルと優先順位（小さいほど高優先）
SOURCE_LCMS = "LC-MS/MS"
SOURCE_METASPACE = "METASPACE"
SOURCE_MSDIAL = "MS-DIAL"
SOURCE_INHOUSE = "in-house"
SOURCE_MANUAL = "manual"

SOURCE_PRIORITY = {
    SOURCE_LCMS: 1,
    SOURCE_METASPACE: 2,
    SOURCE_MSDIAL: 3,
    SOURCE_INHOUSE: 4,
    SOURCE_MANUAL: 5,
}


def source_priority(source: Optional[str]) -> int:
    return SOURCE_PRIORITY.get(source or "", 99)


# --------------------------------------------------------------------------
# 入出力ユーティリティ
# --------------------------------------------------------------------------
def _as_df(table: Union["pd.DataFrame", str], sep: Optional[str] = None) -> "pd.DataFrame":
    if isinstance(table, pd.DataFrame):
        return table
    path = str(table)
    if sep is None:
        sep = "\t" if path.lower().endswith((".tsv", ".txt", ".mztab")) else ","
    return pd.read_csv(path, sep=sep)


def _find_col(df: "pd.DataFrame", candidates: Sequence[str]) -> Optional[str]:
    """候補名（大文字小文字・空白無視）に一致する実列名を返す。"""
    norm = {str(c).strip().lower().replace(" ", ""): c for c in df.columns}
    for cand in candidates:
        key = cand.strip().lower().replace(" ", "")
        if key in norm:
            return norm[key]
    return None


def _to_float(v) -> Optional[float]:
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def make_candidate(name, mz, source, adduct=None, ppm=None, formula=None,
                   metrics: Optional[dict] = None) -> dict:
    """正規化された候補レコード（feature 未対応の段階）。"""
    return {
        "name": (str(name).strip() if name is not None else ""),
        "source_mz": _to_float(mz),
        "source": source,
        "adduct": (str(adduct).strip() if adduct not in (None, "") else None),
        "ppm": _to_float(ppm),
        "formula": (str(formula).strip() if formula not in (None, "") else None),
        "source_metrics": {k: v for k, v in (metrics or {}).items() if v not in (None, "")},
    }


# --------------------------------------------------------------------------
# 各ソースの取込（→ 候補レコードのリスト）
# --------------------------------------------------------------------------
def import_lcms_table(table, mz_col=None, name_col=None, adduct_col=None,
                      rt_col=None, msms_col=None) -> list:
    """LC-MS/MS 結果表（CSV/TSV）を取込む。MSI 側は m/z 対応、RT/MS-MS は支持情報。"""
    df = _as_df(table)
    c_mz = mz_col or _find_col(df, ["mz", "m/z", "average mz", "precursor m/z", "exp_mass_to_charge"])
    c_name = name_col or _find_col(df, ["name", "metabolite name", "compound", "compound_name", "chemical_name"])
    c_add = adduct_col or _find_col(df, ["adduct", "adduct type", "adduct_type"])
    c_rt = rt_col or _find_col(df, ["rt", "retention time", "average rt(min)", "rt(min)"])
    c_msms = msms_col or _find_col(df, ["ms/ms score", "msms", "dot product", "ms/ms matched", "total score"])
    if c_mz is None:
        raise ValueError("LC-MS 表に m/z 列が見つかりません")
    out = []
    for _, r in df.iterrows():
        metrics = {}
        if c_rt is not None:
            metrics["rt"] = _to_float(r[c_rt])
        if c_msms is not None:
            metrics["msms"] = r[c_msms]
        out.append(make_candidate(
            name=r[c_name] if c_name else "",
            mz=r[c_mz], source=SOURCE_LCMS,
            adduct=r[c_add] if c_add else None,
            metrics=metrics,
        ))
    return out


def import_metaspace_table(table, fdr_col=None) -> list:
    """METASPACE 注釈表（CSV）。FDR/MSM/候補分子は source_metrics にそのまま保持。"""
    df = _as_df(table)
    c_mz = _find_col(df, ["mz", "m/z"])
    c_formula = _find_col(df, ["formula", "ionformula", "ion_formula"])
    c_add = _find_col(df, ["adduct"])
    c_fdr = fdr_col or _find_col(df, ["fdr"])
    c_msm = _find_col(df, ["msm"])
    c_mol = _find_col(df, ["moleculenames", "molecule_names", "moleculename"])
    c_ids = _find_col(df, ["moleculeids", "molecule_ids"])
    if c_mz is None and c_formula is None:
        raise ValueError("METASPACE 表に mz/formula 列が見つかりません")
    out = []
    for _, r in df.iterrows():
        names = r[c_mol] if c_mol else (r[c_formula] if c_formula else "")
        # moleculeNames は "A, B, C" 形式のことが多い → 先頭を代表名に、全体は metrics へ
        primary = str(names).split(",")[0].strip() if names is not None else ""
        metrics = {}
        if c_fdr is not None:
            metrics["fdr"] = _to_float(r[c_fdr])
        if c_msm is not None:
            metrics["msm"] = _to_float(r[c_msm])
        if c_mol is not None:
            metrics["moleculeNames"] = r[c_mol]
        if c_ids is not None:
            metrics["moleculeIds"] = r[c_ids]
        out.append(make_candidate(
            name=primary,
            mz=r[c_mz] if c_mz else None,
            source=SOURCE_METASPACE,
            adduct=r[c_add] if c_add else None,
            formula=r[c_formula] if c_formula else None,
            metrics=metrics,
        ))
    return out


def import_msdial_table(table) -> list:
    """MS-DIAL のアライメント結果表（タブ区切り、~32 メタ列）を取込む。"""
    df = _as_df(table)
    c_mz = _find_col(df, ["average mz", "mz", "m/z", "average m/z"])
    c_name = _find_col(df, ["metabolite name", "name", "compound"])
    c_add = _find_col(df, ["adduct type", "adduct"])
    c_formula = _find_col(df, ["formula"])
    c_score = _find_col(df, ["total score", "dot product", "score"])
    if c_mz is None:
        raise ValueError("MS-DIAL 表に Average Mz 列が見つかりません")
    out = []
    for _, r in df.iterrows():
        nm = str(r[c_name]).strip() if c_name else ""
        # MS-DIAL の未同定は "Unknown" / "w/o MS2" 等 → 由来は残すが名前は空扱い
        if nm.lower() in ("unknown", "", "null", "w/o ms2:unknown"):
            nm = ""
        metrics = {}
        if c_score is not None:
            metrics["score"] = _to_float(r[c_score])
        out.append(make_candidate(
            name=nm, mz=r[c_mz], source=SOURCE_MSDIAL,
            adduct=r[c_add] if c_add else None,
            formula=r[c_formula] if c_formula else None,
            metrics=metrics,
        ))
    return out


def import_generic_table(table, mz_col, name_col, source, adduct_col=None,
                         extra_metric_cols: Optional[Sequence[str]] = None) -> list:
    """列名を明示して任意の表を取り込む（手動ラベル/独自 DB 等）。"""
    df = _as_df(table)
    out = []
    for _, r in df.iterrows():
        metrics = {}
        for mc in (extra_metric_cols or []):
            if mc in df.columns:
                metrics[mc] = r[mc]
        out.append(make_candidate(
            name=r[name_col], mz=r[mz_col], source=source,
            adduct=r[adduct_col] if adduct_col else None,
            metrics=metrics,
        ))
    return out


# --------------------------------------------------------------------------
# feature への対応づけ（m/z 許容）と由来優先表示
# --------------------------------------------------------------------------
def _within_tol(feat_mz: float, cand_mz: float, tol_da: float, tol_ppm: Optional[float]):
    d = abs(feat_mz - cand_mz)
    if tol_ppm is not None:
        return d <= (tol_ppm * 1e-6 * feat_mz), d
    return d <= tol_da, d


def match_candidates_to_features(feature_mz, candidates, tol_da: float = 0.01,
                                 tol_ppm: Optional[float] = None) -> dict:
    """各 feature m/z に、許容内の候補を対応づける。

    候補は (source 優先 → |Δm/z| 小) 順に並べ、ppm（実測差→ppm）を補完する。
    Returns: {feature_mz: [candidate(+ppm), ...]}
    """
    feats = [float(m) for m in feature_mz]
    cands = [c for c in candidates if c.get("source_mz") is not None]
    cand_mz = np.array([c["source_mz"] for c in cands], dtype=float)
    order = np.argsort(cand_mz) if cand_mz.size else np.array([], dtype=int)
    cand_mz_sorted = cand_mz[order] if cand_mz.size else cand_mz

    result = {}
    for fmz in feats:
        matched = []
        if cand_mz.size:
            # 近傍のみ走査（許容幅で二分探索）
            half = (tol_ppm * 1e-6 * fmz) if tol_ppm is not None else tol_da
            lo = int(np.searchsorted(cand_mz_sorted, fmz - half, side="left"))
            hi = int(np.searchsorted(cand_mz_sorted, fmz + half, side="right"))
            for k in range(lo, hi):
                c = cands[order[k]]
                ok, d = _within_tol(fmz, c["source_mz"], tol_da, tol_ppm)
                if not ok:
                    continue
                cc = dict(c)
                if cc.get("ppm") is None and fmz > 0:
                    cc["ppm"] = (c["source_mz"] - fmz) / fmz * 1e6
                cc["delta_mz"] = c["source_mz"] - fmz
                matched.append(cc)
        matched.sort(key=lambda c: (source_priority(c["source"]), abs(c.get("delta_mz") or 0.0)))
        if matched:
            result[fmz] = matched
    return result


def _fmt_metric(metrics: dict) -> str:
    """出典が持つ指標を簡潔に併記（採点はしない・そのまま表示）。"""
    if not metrics:
        return ""
    bits = []
    fdr = metrics.get("fdr")
    if isinstance(fdr, (int, float)) and not (isinstance(fdr, float) and math.isnan(fdr)):
        bits.append(f"FDR={fdr*100:.0f}%" if fdr <= 1 else f"FDR={fdr:g}")
    if metrics.get("msm") not in (None, ""):
        try:
            bits.append(f"MSM={float(metrics['msm']):.2f}")
        except (TypeError, ValueError):
            pass
    if metrics.get("score") not in (None, ""):
        try:
            bits.append(f"score={float(metrics['score']):.2f}")
        except (TypeError, ValueError):
            pass
    if metrics.get("rt") not in (None, ""):
        try:
            bits.append(f"RT={float(metrics['rt']):.2f}")
        except (TypeError, ValueError):
            pass
    return ", ".join(bits)


def format_annotation_label(candidate: dict, with_metrics: bool = True) -> str:
    """1 候補の由来付き表示名。例: 'ATP (METASPACE, FDR=10%)'。"""
    name = candidate.get("name") or (f"m/z {candidate['source_mz']:.4f}"
                                     if candidate.get("source_mz") else "?")
    src = candidate.get("source") or "?"
    inside = src
    if with_metrics:
        m = _fmt_metric(candidate.get("source_metrics") or {})
        if m:
            inside = f"{src}, {m}"
    return f"{name} ({inside})"


def summarize_feature(matched: list) -> dict:
    """1 feature に対応づいた候補リストを要約する。

    Returns: {primary_label, primary_source, n_candidates, sources, all_labels}
    """
    if not matched:
        return {"primary_label": "", "primary_source": None, "n_candidates": 0,
                "sources": [], "all_labels": []}
    primary = matched[0]
    sources = []
    for c in matched:
        if c["source"] not in sources:
            sources.append(c["source"])
    return {
        "primary_label": format_annotation_label(primary),
        "primary_source": primary["source"],
        "n_candidates": len(matched),
        "sources": sources,
        "all_labels": [format_annotation_label(c) for c in matched],
    }


def build_feature_source_map(feature_mz, candidates, tol_da: float = 0.01,
                             tol_ppm: Optional[float] = None) -> dict:
    """feature m/z → 由来サマリ（永続化/表示用、JSON 化可能な素の dict）。"""
    matches = match_candidates_to_features(feature_mz, candidates, tol_da, tol_ppm)
    return {f"{fmz:.4f}": summarize_feature(m) for fmz, m in matches.items()}
