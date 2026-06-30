# =============================================================================
# MSI Analysis Application - SCiLS peak `Name` アノテーションのパース
# =============================================================================
# SCiLS peak-list の `Name` 欄（per-feature・パイプ区切りの 1 候補リッチメタ）を
# 構造化 dict に分解する。
#
# `Name` 例:
#   PI 38:4 (PI 18:0/20:4) | PI | Calibration_Priority_9AA_v1_7 | [M-H]- | 2.13ppm |
#   annotation_tol=10ppm | mz_window=10ppm | formula=C47H83O13P | SMILES=NA |
#   adduct_image=single_ion_or_no_family
#
# 確定ルール:
#   - `|` で分割（データ内に `|` は無い前提）。先頭 = 化合物名。
#     `No DB hit` は候補なし（is_db_hit=False、表示は m/z 数値のみ）。
#   - adduct（`[M…]`）を起点に、化合物名と adduct の「間」の非 key=value 数で判定:
#       2 個 -> 分類, DB ／ 1 個 -> DB のみ ／ 0 個 -> なし
#   - adduct の次の `<数値>ppm` -> ppm（数値）。
#   - `key=value` はキー名で格納（NA -> None、未知キーは extras）。
#   - 分類の補完はしない。adduct_family の値は raw 保持。raw 全文を必ず保持。
# =============================================================================

from __future__ import annotations

import re
from typing import Optional

import numpy as np
import pandas as pd

# adduct らしさ: `[M…]` で始まり閉じ括弧を持つ（[M-H]-, [M]-, [M+Na]+, [M-2H]2- 等）
_PPM_RE = re.compile(r"^-?\d+(\.\d+)?\s*ppm$", re.IGNORECASE)
_NO_DB_HIT = "no db hit"

# 構造化して持つ key=value の代表キー（小文字比較）
_TEXT_KEYS = ("annotation_tol", "mz_window", "adduct_image")


def _is_adduct(field: str) -> bool:
    f = field.strip()
    return f.startswith("[M") and "]" in f


def _na_to_none(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return None if (s == "" or s.upper() == "NA") else s


def _empty_record(raw: str = "", is_hit: bool = False) -> dict:
    return {
        "compound": "",
        "lipid_class": None,
        "database": None,
        "adduct": None,
        "ppm": None,
        "formula": None,
        "smiles": None,
        "annotation_tol": None,
        "mz_window": None,
        "adduct_image": None,
        "adduct_family": None,
        "extras": {},
        "raw": raw,
        "is_db_hit": is_hit,
        # 由来（何で付けた注釈か）。SCiLS peak-list 由来はラボ内 "in-house"。
        "source": "in-house" if is_hit else None,
        "source_metrics": {},
    }


def parse_scils_name(name) -> dict:
    """SCiLS `Name` 文字列を構造化 dict に分解する。

    Returns:
        dict: compound / lipid_class / database / adduct / ppm / formula / smiles /
              annotation_tol / mz_window / adduct_image / adduct_family / extras /
              raw / is_db_hit
    """
    raw = "" if name is None else str(name).strip()
    rec = _empty_record(raw=raw, is_hit=bool(raw))
    if not raw:
        return rec

    parts = [p.strip() for p in raw.split("|")]
    rec["compound"] = parts[0]
    if parts[0].lower() == _NO_DB_HIT:
        rec["is_db_hit"] = False
        rec["source"] = None

    rest = parts[1:]

    # --- adduct の位置を探す（最初の非 key=value で [M…] にマッチ） ---
    adduct_idx = None
    for i, fld in enumerate(rest):
        if "=" in fld:
            continue
        if _is_adduct(fld):
            adduct_idx = i
            break

    if adduct_idx is not None:
        rec["adduct"] = rest[adduct_idx]
        # 化合物名と adduct の「間」の非 key=value（= 分類/DB 候補）
        leading = [f for f in rest[:adduct_idx] if "=" not in f and f]
        if len(leading) >= 2:
            rec["lipid_class"] = leading[0] or None
            rec["database"] = leading[1] or None
            if len(leading) > 2:
                rec["extras"]["unparsed_leading"] = leading[2:]
        elif len(leading) == 1:
            rec["database"] = leading[0] or None
        # adduct の後ろの最初の <num>ppm
        for fld in rest[adduct_idx + 1:]:
            if "=" not in fld and _PPM_RE.match(fld):
                try:
                    rec["ppm"] = float(re.sub(r"(?i)ppm", "", fld).strip())
                except ValueError:
                    pass
                break
    else:
        # adduct 無し（No DB hit 等）：先頭側の非 key=value は extras 退避
        leading = [f for f in rest if "=" not in f and f]
        if leading:
            rec["extras"]["unparsed_leading"] = leading

    # --- key=value を回収（最初の `=` で分割し、値は raw 保持） ---
    for fld in rest:
        if "=" not in fld:
            continue
        key, _, val = fld.partition("=")
        key = key.strip()
        val = val.strip()
        kl = key.lower()
        if kl in _TEXT_KEYS:
            rec[kl] = val or None
        elif kl == "formula":
            rec["formula"] = _na_to_none(val)
        elif kl == "smiles":
            rec["smiles"] = _na_to_none(val)
        elif kl == "adduct_family":
            rec["adduct_family"] = val or None  # raw 保持（; , = を含む）
        elif kl in ("m/z", "mz"):
            rec["extras"]["mz_field"] = val
        else:
            rec["extras"][key] = val

    return rec


def display_label(record: dict, mz: float, mz_decimals: int = 4) -> str:
    """画面表示名（先頭フィールド）= `化合物名_<m/z>`。No DB hit は m/z 数値のみ。"""
    mz_str = f"{mz:.{mz_decimals}f}"
    if not record.get("is_db_hit", True):
        return mz_str
    comp = (record.get("compound") or "").strip()
    return f"{comp}_{mz_str}" if comp else mz_str


def make_column_name(raw_name, mz: float, mz_decimals: int = 4) -> str:
    """埋め込み用の列名（= パイプ全文の先頭フィールドに `_<m/z>` を付与）。

    Name が空なら従来どおり m/z 数値の列名にフォールバック。
    """
    raw = "" if raw_name is None else str(raw_name).strip()
    mz_str = f"{mz:.{mz_decimals}f}"
    if not raw:
        return f"{mz:.6f}"
    head, sep, tail = raw.partition("|")
    new_head = f"{head.strip()}_{mz_str}"
    return f"{new_head} | {tail.strip()}" if sep else new_head


_TABLE_COLUMNS = [
    "mz", "compound", "lipid_class", "database", "adduct", "ppm",
    "formula", "smiles", "adduct_image", "adduct_family", "raw", "display_name",
]


def build_feature_annotation_table(
    mz_values,
    peaklist_mz,
    peaklist_names,
    tol_da: float = 0.01,
) -> "pd.DataFrame":
    """変換の m/z 列（mz_values）に peak-list の `Name` を数値 m/z 最近傍で割り当て、
    per-feature の注釈テーブル（1 feature 1 行）を返す。

    Args:
        mz_values: 変換後 parquet の m/z（昇順）配列
        peaklist_mz: peak-list の m/z 配列
        peaklist_names: peak-list の Name 配列（peaklist_mz と同順）
        tol_da: 最近傍マッチの許容（Da）
    """
    mz_values = np.asarray(mz_values, dtype=float)
    pk_mz = np.asarray(peaklist_mz, dtype=float)
    pk_names = list(peaklist_names)

    # 最近傍探索を O(F log P) にするため peak-list を m/z でソートしておく
    have_peaklist = pk_mz.size > 0
    if have_peaklist:
        order = np.argsort(pk_mz, kind="mergesort")
        pk_sorted = pk_mz[order]

    rows = []
    for mz in mz_values:
        name = ""
        if have_peaklist:
            pos = int(np.searchsorted(pk_sorted, mz))
            best = -1
            best_d = None
            for cand in (pos - 1, pos):
                if 0 <= cand < pk_sorted.size:
                    d = abs(pk_sorted[cand] - mz)
                    if best_d is None or d < best_d:
                        best_d, best = d, cand
            if best >= 0 and best_d <= tol_da:
                nm = pk_names[order[best]]
                name = nm if nm is not None else ""
        rec = parse_scils_name(name)
        rows.append({
            "mz": float(mz),
            "compound": rec["compound"],
            "lipid_class": rec["lipid_class"],
            "database": rec["database"],
            "adduct": rec["adduct"],
            "ppm": rec["ppm"],
            "formula": rec["formula"],
            "smiles": rec["smiles"],
            "adduct_image": rec["adduct_image"],
            "adduct_family": rec["adduct_family"],
            "raw": rec["raw"],
            "display_name": display_label(rec, float(mz)),
        })
    return pd.DataFrame(rows, columns=_TABLE_COLUMNS)
