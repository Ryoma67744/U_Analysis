# =============================================================================
# MSI Analysis Application - SCiLS Lab Feature List Converter
# SCiLS Lab "Export → Feature list / Annotation" で出力される CSV を
# 本アプリが読み込む Parquet 形式 (id, x, y, mz_*, annotation) に変換する。
# =============================================================================
#
# 想定入力:
#   - Long 形式:  spot_id, x, y, mz, intensity, [region/annotation]
#   - Wide 形式:  spot_id, x, y, [annotation,] <m/z 列...>
#
# 出力: 1 サンプル = 1 Parquet
#   カラム: id, x, y, mz_<m/z値>..., [annotation]
# =============================================================================

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import pandas as pd

logger = logging.getLogger("msi.scils_converter")


# 列名エイリアス (case-insensitive 比較) — SCiLS のバージョン差異を吸収
_ALIAS_MAP = {
    "id": "id",
    "spot_id": "id",
    "spot index": "id",
    "spot": "id",
    "pixel": "id",
    "pixel_id": "id",
    "index": "id",
    "x": "x",
    "x_coord": "x",
    "x coordinate": "x",
    "x_position": "x",
    "y": "y",
    "y_coord": "y",
    "y coordinate": "y",
    "y_position": "y",
    "mz": "mz",
    "m/z": "mz",
    "m_z": "mz",
    "intensity": "intensity",
    "abundance": "intensity",
    "value": "intensity",
    "annotation": "annotation",
    "region": "annotation",
    "region name": "annotation",
    "roi": "annotation",
    "compound": "annotation",
}

# m/z 列名抽出パターン — "m/z 123.456", "mz 123.456", "123.456" に対応
_MZ_LABEL_RE = re.compile(r"^\s*(?:m\s*/?\s*z\s*[:_]?\s*)?(\d+(?:\.\d+)?)\s*$", re.I)


@dataclass
class ConversionResult:
    """変換結果の要約"""
    output_path: str = ""
    source_csv: str = ""
    n_rows: int = 0
    n_mz_features: int = 0
    has_annotation: bool = False
    shape: Literal["long", "wide", "unknown"] = "unknown"
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def detect_scils_csv(folder: Path) -> Optional[Path]:
    """フォルダ内で最有力の SCiLS CSV を 1 つ選ぶ。

    - .csv / .tsv / .txt を候補に
    - 最大サイズのものを採用 (Feature list は通常最大)
    """
    if not folder.is_dir():
        return None
    exts = {".csv", ".tsv", ".txt"}
    csvs = [p for p in folder.iterdir() if p.suffix.lower() in exts and p.is_file()]
    if not csvs:
        return None
    return max(csvs, key=lambda p: p.stat().st_size)


def _read_csv_auto(path: Path) -> pd.DataFrame:
    """区切り文字を自動判別して CSV を読む"""
    suffix = path.suffix.lower()
    if suffix in {".tsv", ".txt"}:
        return pd.read_csv(path, sep="\t", low_memory=False)
    try:
        # sep=None は python engine が自動判別
        return pd.read_csv(path, sep=None, engine="python")
    except pd.errors.ParserError:
        return pd.read_csv(path, sep=",", low_memory=False)


def normalize_column_aliases(df: pd.DataFrame) -> pd.DataFrame:
    """既知の列名エイリアスを本アプリ標準名 (id/x/y/mz/intensity/annotation) に置換"""
    renamed = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if key in _ALIAS_MAP:
            renamed[col] = _ALIAS_MAP[key]
    if renamed:
        df = df.rename(columns=renamed)
    return df


def _parse_mz_from_label(label: str) -> Optional[float]:
    """'m/z 123.456' / 'mz_123.456' / '123.456' のいずれかから数値を抽出"""
    match = _MZ_LABEL_RE.match(str(label))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def infer_csv_shape(df: pd.DataFrame) -> Literal["long", "wide", "unknown"]:
    """DataFrame 形状を判定 (エイリアス正規化済みを想定)"""
    cols_lower = {str(c).lower() for c in df.columns}
    if "mz" in cols_lower and "intensity" in cols_lower:
        return "long"
    # m/z 数値を名前に含む列が 2 つ以上あれば wide
    mz_cols = [c for c in df.columns if _parse_mz_from_label(c) is not None]
    if len(mz_cols) >= 2:
        return "wide"
    return "unknown"


def normalize_long_to_wide(df: pd.DataFrame) -> pd.DataFrame:
    """long (id/x/y/mz/intensity) → wide (id/x/y/mz_<値>...) に pivot"""
    required = {"id", "x", "y", "mz", "intensity"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"long 形式に必要な列が不足: {sorted(missing)}")

    # annotation があれば id 単位で先頭値を保持
    ann_series: Optional[pd.Series] = None
    if "annotation" in df.columns:
        ann_series = df.groupby("id")["annotation"].first()

    # mz を 5 桁に丸めて列名の揺れを抑制 (本アプリ R スクリプトの表記と一致)
    df = df.copy()
    df["mz"] = pd.to_numeric(df["mz"], errors="coerce").round(5)
    df = df.dropna(subset=["mz"])

    pivot = df.pivot_table(
        index=["id", "x", "y"],
        columns="mz",
        values="intensity",
        aggfunc="mean",  # 同一 (id, mz) が重複する場合は平均
    ).reset_index()

    pivot.columns = [
        c if not isinstance(c, float) else f"mz_{c:.5f}"
        for c in pivot.columns
    ]

    if ann_series is not None:
        pivot = pivot.merge(
            ann_series.rename("annotation"), left_on="id", right_index=True, how="left",
        )

    return pivot


def normalize_wide(df: pd.DataFrame) -> pd.DataFrame:
    """wide 形式の m/z 列名を `mz_<値>` に統一 (id 欠落は呼出側が補完)"""
    if not {"x", "y"}.issubset(df.columns):
        missing = {"x", "y"} - set(df.columns)
        raise ValueError(f"wide 形式に必要な列が不足: {sorted(missing)}")

    renamed = {}
    for col in df.columns:
        if col in {"id", "x", "y", "annotation"}:
            continue
        mz = _parse_mz_from_label(col)
        if mz is not None:
            renamed[col] = f"mz_{mz:.5f}"
    if renamed:
        df = df.rename(columns=renamed)
    return df


def _finalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """列順を id, x, y, mz_*..., annotation に整える + 数値化"""
    mz_cols = sorted(
        [c for c in df.columns if str(c).startswith("mz_")],
        key=lambda c: float(str(c).split("_", 1)[1]),
    )
    order = ["id", "x", "y"] + mz_cols
    if "annotation" in df.columns:
        order.append("annotation")
    df = df[order]

    # 数値列を数値型に
    for c in ("x", "y", *mz_cols):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df[mz_cols] = df[mz_cols].fillna(0)
    return df


# ---------------------------------------------------------------------------
# メイン変換関数
# ---------------------------------------------------------------------------

def convert_scils_to_parquet(
    input_folder: str,
    output_path: str,
) -> ConversionResult:
    """SCiLS Feature list フォルダを 1 つの Parquet に変換する。

    Parameters
    ----------
    input_folder : str
        SCiLS Lab の Export 出力フォルダ (CSV を含む)
    output_path : str
        書き出す .parquet のフルパス

    Returns
    -------
    ConversionResult
    """
    result = ConversionResult()
    folder = Path(input_folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"入力フォルダが存在しません: {input_folder}")

    csv_path = detect_scils_csv(folder)
    if csv_path is None:
        raise FileNotFoundError(
            f"CSV ファイルが見つかりません: {input_folder}\n"
            "SCiLS Lab で Export → Feature list を CSV 形式で実行してください。"
        )
    result.source_csv = str(csv_path)
    logger.info("SCiLS CSV を読込: %s", csv_path)

    df = _read_csv_auto(csv_path)
    if df.empty:
        raise ValueError(f"CSV が空です: {csv_path}")

    df = normalize_column_aliases(df)
    shape = infer_csv_shape(df)
    result.shape = shape

    if shape == "long":
        wide = normalize_long_to_wide(df)
    elif shape == "wide":
        wide = normalize_wide(df)
    else:
        raise ValueError(
            "CSV 形式を判定できませんでした。"
            f"列名: {list(df.columns)[:10]}... (long: mz+intensity 列, "
            "wide: m/z 値を名前に持つ列を 2 つ以上 期待)"
        )

    # id 列が無ければ行番号で補完
    if "id" not in wide.columns:
        wide.insert(0, "id", range(1, len(wide) + 1))
        result.warnings.append("id 列が無かったため行番号を採番しました")

    wide = _finalize_dataframe(wide)

    result.n_rows = len(wide)
    result.n_mz_features = sum(1 for c in wide.columns if str(c).startswith("mz_"))
    result.has_annotation = "annotation" in wide.columns

    if result.n_mz_features == 0:
        raise ValueError("m/z 列が 1 つも検出されませんでした")

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wide.to_parquet(out_path, index=False)
    result.output_path = str(out_path)
    logger.info(
        "変換完了: %s (%d 行 × %d m/z 列, shape=%s)",
        out_path, result.n_rows, result.n_mz_features, shape,
    )
    return result
