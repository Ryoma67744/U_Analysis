# =============================================================================
# MSI Analysis Application - SCiLS Lab Intensity/Spot/Annotation Converter
# SCiLS Lab で Export した Intensity CSV + Spot CSV (+ Annotation CSV) を
# ストリーミング処理で Parquet に変換する。
# =============================================================================
#
# 入力 (フォルダ内に同居):
#   - <BASE>_Intensity.csv   : 先頭列 = m/z 値, それ以降の列 = "Spot NNNNN"
#   - <BASE>_Spot.csv        : SpotIndex, X, Y (マスター座標)
#   - <任意>_Annotation.csv  : SpotIndex, X, Y (任意, 複数可)
#     -> ファイル名 '<LABEL>_Annotation.csv' から LABEL を取得
#
# 出力: 1 Parquet ファイル
#   カラム: id (int64), x (float64), y (float64),
#           <mz を小数 6 桁で文字列化した列名> (float32/64), annotation (string)
#   行: (y, x) ソート順の spot, m/z 列は昇順
# =============================================================================

from __future__ import annotations

import csv
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("msi.scils_converter")


# ---------------------------------------------------------------------------
# 戻り値型
# ---------------------------------------------------------------------------

@dataclass
class ConversionResult:
    """変換結果の要約"""
    output_path: str = ""
    source_intensity: str = ""
    source_spot: str = ""
    annotation_files: list[str] = field(default_factory=list)
    n_spots: int = 0
    n_mz_features: int = 0
    has_annotation: bool = False
    annotation_labels: list[str] = field(default_factory=list)
    has_peak_list: bool = False
    peak_list_file: str = ""
    sidecar_path: str = ""
    n_annotated: int = 0
    organized: bool = False
    moved_files: list[str] = field(default_factory=list)
    duration_sec: float = 0.0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CSV ヘッダ読み込みユーティリティ
# ---------------------------------------------------------------------------

def first_header_and_skipcount(path: Path) -> tuple[list[str], str, int]:
    """`#` 行をスキップして最初のヘッダ行を返す。

    Returns
    -------
    (headers, delimiter, skip_lines)
    """
    skip = 0
    header_line: Optional[str] = None
    with path.open("r", newline="", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("#"):
                skip += 1
                continue
            header_line = line.rstrip("\n")
            break
    if header_line is None:
        raise ValueError(f"ヘッダ行が見つかりません: {path}")

    comma = header_line.count(",")
    semi = header_line.count(";")
    tab = header_line.count("\t")
    if tab >= max(comma, semi):
        delim = "\t"
    elif semi >= max(comma, tab):
        delim = ";"
    else:
        delim = ","

    headers = next(csv.reader([header_line], delimiter=delim))
    return headers, delim, skip


def _find_col(candidates: set[str], headers: list[str]) -> int:
    """正規化ヘッダ名に対して候補を探し、インデックスを返す (該当なしは 0)"""
    norm = [str(h).strip().lower().replace(" ", "") for h in headers]
    for cand in candidates:
        if cand in norm:
            return norm.index(cand)
    return 0


# ---------------------------------------------------------------------------
# CSV 役割判定 / 自動検出
# ---------------------------------------------------------------------------

_SPOT_COL_PATTERN = re.compile(r"^Spot\s+\d+$", re.IGNORECASE)


def classify_csv_role(path: Path) -> str:
    """CSV のヘッダから役割を判定する。

    Returns
    -------
    "intensity" | "spot_like" | "unknown"
    """
    try:
        headers, _, _ = first_header_and_skipcount(path)
    except Exception:
        return "unknown"

    spot_col_count = sum(1 for h in headers[1:] if _SPOT_COL_PATTERN.match(str(h).strip()))
    if spot_col_count >= 5:
        return "intensity"

    norm = [str(h).strip().lower().replace(" ", "") for h in headers]
    has_index = any(c in norm for c in ("spotindex", "spot_index", "index", "spot"))
    has_x = any(c in norm for c in ("x", "xcoord", "xcoordinate"))
    has_y = any(c in norm for c in ("y", "ycoord", "ycoordinate"))
    if has_index and has_x and has_y:
        return "spot_like"

    # peak-list (Feature list): m/z 列 + Name 列を持つ（Spot 行列でも座標でもない）
    has_name = "name" in norm
    has_mz = any(c in ("m/z", "mz", "m_z") for c in norm)
    if has_name and has_mz:
        return "peak_list"

    return "unknown"


def auto_detect_file_roles(data_dir: Path) -> dict:
    """フォルダ内の CSV を分類して役割を確定する。

    Returns
    -------
    dict: {"intensity": Path, "spot": Path, "annotations": list[Path], "unknown": list[Path]}
    """
    if not data_dir.is_dir():
        raise FileNotFoundError(f"入力フォルダが存在しません: {data_dir}")

    csv_files = sorted(p for p in data_dir.iterdir() if p.is_file() and p.suffix.lower() == ".csv")
    if not csv_files:
        raise FileNotFoundError(f"フォルダ内に CSV がありません: {data_dir}")

    intensities: list[Path] = []
    spot_likes: list[Path] = []
    peaklists: list[Path] = []
    unknowns: list[Path] = []
    for p in csv_files:
        role = classify_csv_role(p)
        if role == "intensity":
            intensities.append(p)
        elif role == "spot_like":
            spot_likes.append(p)
        elif role == "peak_list":
            peaklists.append(p)
        else:
            unknowns.append(p)

    if not intensities:
        raise ValueError(
            "Intensity CSV が見つかりません (`Spot NNNNN` カラムを持つファイル)。\n"
            f"検索フォルダ: {data_dir}"
        )
    if len(intensities) > 1:
        names = "\n".join(f"  - {p.name}" for p in intensities)
        raise ValueError(f"Intensity CSV が複数あります (曖昧):\n{names}")

    if not spot_likes:
        raise ValueError(
            "Spot/Annotation CSV が見つかりません (SpotIndex/X/Y カラムを持つファイル)。\n"
            f"検索フォルダ: {data_dir}"
        )

    # 最大サイズ = Spot、残り = Annotation
    spot_likes.sort(key=lambda p: p.stat().st_size, reverse=True)
    return {
        "intensity": intensities[0],
        "spot": spot_likes[0],
        "annotations": spot_likes[1:],
        "peak_list": peaklists[0] if peaklists else None,
        "unknown": unknowns,
    }


# ---------------------------------------------------------------------------
# Spot テーブル / Intensity ヘッダ解析
# ---------------------------------------------------------------------------

def _extract_spot_numbers(labels: list[str]) -> np.ndarray:
    """`Spot 32011` のような列名から末尾数字を抽出 (NaN は未解析)"""
    out = np.full(len(labels), np.nan, dtype=float)
    for i, s in enumerate(labels):
        s = str(s).strip()
        parts = s.split()
        if parts and parts[-1].isdigit():
            out[i] = float(parts[-1])
            continue
        m = re.search(r"(\d+)$", s)
        if m:
            out[i] = float(m.group(1))
    return out


def read_spot_table(spots_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Spot テーブルを読み込んで (spot_index, x, y) を返す"""
    import pandas as pd

    headers, delim, skip = first_header_and_skipcount(spots_path)
    df = pd.read_csv(
        spots_path, sep=delim, skiprows=skip, header=0,
        engine="c", encoding="utf-8", na_filter=True,
    )

    idx_i = _find_col({"spotindex", "spot_index", "index", "spot"}, headers)
    x_i = _find_col({"x", "xcoord", "xcoordinate"}, headers)
    y_i = _find_col({"y", "ycoord", "ycoordinate"}, headers)

    spot_index = pd.to_numeric(df.iloc[:, idx_i], errors="coerce").astype("Int64")
    x_arr = pd.to_numeric(df.iloc[:, x_i], errors="coerce").astype(float).to_numpy()
    y_arr = pd.to_numeric(df.iloc[:, y_i], errors="coerce").astype(float).to_numpy()

    if spot_index.isna().any():
        raise ValueError(f"Spot index 列に欠損値があります: {spots_path.name}")

    return spot_index.astype(int).to_numpy(), x_arr, y_arr


def compute_spot_mapping(
    intensity_headers: list[str],
    spot_index: np.ndarray,
    x_arr: np.ndarray,
    y_arr: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str], np.ndarray, list[str]]:
    """Intensity ヘッダと Spot テーブルを厳格整合して (y, x) ソートする。

    完全一致を要求。±1 グローバルシフトのみ自動補正 (データ不動, ラベルのみ調整)。

    Returns
    -------
    sort_idx, x_sorted, y_sorted, spot_labels_sorted, spot_index_sorted, warnings
    """
    warnings: list[str] = []
    spot_labels = intensity_headers[1:]
    spot_nums = _extract_spot_numbers(spot_labels)

    nan_mask = ~np.isfinite(spot_nums)
    if nan_mask.any():
        bad = [spot_labels[i] for i in np.where(nan_mask)[0][:10]]
        raise ValueError(
            "Intensity ヘッダから spot 番号を解釈できません。\n"
            f"例: {bad}\n"
            "`Spot 32011` のように末尾が数字の列名を期待しています。"
        )

    spot_nums_int = spot_nums.astype(np.int64)
    if len(np.unique(spot_nums_int)) != len(spot_nums_int):
        from collections import Counter
        dup = [k for k, v in Counter(spot_nums_int.tolist()).items() if v > 1]
        raise ValueError(f"Intensity ヘッダに spot 番号の重複: {dup[:10]}")

    spot_index_int = spot_index.astype(np.int64, copy=False)
    set_int = set(spot_nums_int.tolist())
    set_spot = set(spot_index_int.tolist())

    extra = sorted(set_int - set_spot)
    missing = sorted(set_spot - set_int)

    if extra or missing:
        set_int_minus1 = {n - 1 for n in set_int}
        set_int_plus1 = {n + 1 for n in set_int}
        if set_int_minus1 == set_spot:
            warnings.append("Intensity ヘッダの spot 番号に +1 グローバルシフトを検出。ラベルを自動補正しました。")
            logger.info("Intensity ヘッダ +1 シフト検出 → ラベル -1 補正")
            spot_nums_int = spot_nums_int - 1
        elif set_int_plus1 == set_spot:
            warnings.append("Intensity ヘッダの spot 番号に -1 グローバルシフトを検出。ラベルを自動補正しました。")
            logger.info("Intensity ヘッダ -1 シフト検出 → ラベル +1 補正")
            spot_nums_int = spot_nums_int + 1
        else:
            msg = [
                "Intensity と Spot テーブルの spot 番号が一致しません。",
                f"- Intensity にのみ存在: {len(extra)} 個 (例: {extra[:10]})",
                f"- Spot にのみ存在: {len(missing)} 個 (例: {missing[:10]})",
                "SCiLS Lab で両ファイルの spot 番号が一致するよう再エクスポートしてください。",
                "(±1 のグローバルシフトのみ自動補正対象)",
            ]
            raise ValueError("\n".join(msg))

    coord = {int(si): (float(x_arr[i]), float(y_arr[i])) for i, si in enumerate(spot_index_int)}
    x_map = np.array([coord[int(n)][0] for n in spot_nums_int], dtype=np.float64)
    y_map = np.array([coord[int(n)][1] for n in spot_nums_int], dtype=np.float64)

    sort_idx = np.lexsort((x_map, y_map))
    x_sorted = x_map[sort_idx]
    y_sorted = y_map[sort_idx]
    spot_labels_sorted = [spot_labels[i] for i in sort_idx]
    spot_index_sorted = spot_nums_int[sort_idx]

    return sort_idx, x_sorted, y_sorted, spot_labels_sorted, spot_index_sorted, warnings


# ---------------------------------------------------------------------------
# Annotation 解決
# ---------------------------------------------------------------------------

def annotation_label_from_filename(p: Path) -> str:
    """ファイル名から注釈ラベルを抽出する。
    '<LABEL>_Annotation.csv' → '<LABEL>' (case-insensitive)
    """
    stem = p.stem
    label = re.sub(r"(?i)_annotation$", "", stem)
    label = re.sub(r"(?i)_spot$", "", label)
    label = re.sub(r"(?i)_intensity$", "", label)
    label = label.strip()
    if not label:
        raise ValueError(f"ファイル名からラベル名を抽出できません: {p.name}")
    return label


def build_annotation_map(
    annotation_files: list[Path],
    spot_index_all: np.ndarray,
    x_all: np.ndarray,
    y_all: np.ndarray,
    tol: float = 1e-6,
) -> tuple[Optional[dict[int, str]], list[str]]:
    """複数 annotation CSV から spot_index → label マップを構築する。

    Returns
    -------
    (mapping, warnings): annotation_files が空なら mapping=None
    """
    warnings: list[str] = []
    if not annotation_files:
        return None, warnings

    master_set = {int(v) for v in spot_index_all.tolist()}
    master_xy = {
        int(spot_index_all[i]): (float(x_all[i]), float(y_all[i]))
        for i in range(len(spot_index_all))
    }

    mapping: dict[int, str] = {}
    used_labels: set[str] = set()
    for fp in annotation_files:
        label = annotation_label_from_filename(fp)
        if label in used_labels:
            raise ValueError(f"Annotation ラベル名が重複しています: '{label}'")
        used_labels.add(label)

        si, x, y = read_spot_table(fp)
        set_ann = {int(v) for v in si.tolist()}
        unknown = sorted(set_ann - master_set)
        if unknown:
            raise ValueError(
                f"Annotation にマスター Spot テーブル非存在の spot: {fp.name}\n"
                f"件数: {len(unknown)} (例: {unknown[:10]})"
            )

        mism = []
        for i, s in enumerate(si):
            mx, my = master_xy[int(s)]
            if (abs(float(x[i]) - mx) > tol) or (abs(float(y[i]) - my) > tol):
                mism.append((int(s), float(x[i]), float(y[i]), mx, my))
                if len(mism) >= 10:
                    break
        if mism:
            ex = mism[0]
            raise ValueError(
                f"Annotation 座標がマスターと一致しません: {fp.name}\n"
                f"例: Spot {ex[0]} (ann=({ex[1]}, {ex[2]}), master=({ex[3]}, {ex[4]}))\n"
                f"許容誤差: {tol}"
            )

        overlap = [s for s in set_ann if s in mapping]
        if overlap:
            raise ValueError(
                f"同一 spot が複数 annotation に: {sorted(overlap)[:10]}"
            )

        for s in set_ann:
            mapping[int(s)] = label

    unlabeled = sorted(master_set - set(mapping.keys()))
    if unlabeled:
        warnings.append(
            f"{len(unlabeled)} spots が annotation に無いため 'Unannotated' を割当"
        )
        logger.warning("未アノテーション spot: %d 個 (例: %s)", len(unlabeled), unlabeled[:10])
        for s in unlabeled:
            mapping[s] = "Unannotated"

    return mapping, warnings


# ---------------------------------------------------------------------------
# パス整理
# ---------------------------------------------------------------------------

def derive_base_name(intensity_path: Path, spots_path: Path) -> str:
    """intensity/spot の共通ベース名を導出する"""
    def strip_kind(s: str) -> str:
        return re.sub(r"(?i)([_\-\s]*)(intensity|spot)s?$", "", s).strip(" _-")

    b1 = strip_kind(intensity_path.stem)
    b2 = strip_kind(spots_path.stem)
    return b1 if b1 and (b1 == b2 or not b2) else b1


def _unique_path(p: Path) -> Path:
    """既存なら (1), (2) ... を拡張子前に付与してユニーク化"""
    if not p.exists():
        return p
    for i in range(1, 1000):
        cand = p.parent / f"{p.stem}({i}){p.suffix}"
        if not cand.exists():
            return cand
    raise RuntimeError(f"ユニークなファイル名を生成できません: {p}")


def _move_into_folder(src: Path, dst_folder: Path) -> Path:
    """src を dst_folder に移動 (衝突時は (1) ... 付与)"""
    dst_folder.mkdir(parents=True, exist_ok=True)
    dst = _unique_path(dst_folder / src.name)
    shutil.move(str(src), str(dst))
    return dst


# ---------------------------------------------------------------------------
# メイン変換
# ---------------------------------------------------------------------------

# 一時 Parquet は変換後に必ず削除される使い捨て。保存コストが無いので zstd ではなく
# 高速・相互運用性の高い snappy を使い、Phase A 書込／Phase B 読込の CPU を削減する。
_TEMP_PARQUET_COMPRESSION = "snappy"


def _csv_to_temp_parquet(
    intensity_path: Path,
    int_headers: list[str],
    delim: str,
    skip: int,
    temp_parquet: Path,
) -> None:
    """Phase A: Intensity CSV を一時 Parquet にストリーミング変換"""
    try:
        import polars as pl
        _use_polars = True
    except ImportError:
        _use_polars = False
        logger.info("polars 未インストール → pyarrow batched にフォールバック")

    if _use_polars:
        logger.info("Phase A エンジン: polars (streaming sink)")
        schema_overrides = {h: pl.Float64 for h in int_headers}
        lf = pl.scan_csv(
            str(intensity_path),
            separator=delim,
            skip_rows=skip,
            has_header=True,
            try_parse_dates=False,
            infer_schema_length=0,
            schema_overrides=schema_overrides,
            low_memory=True,
        )
        try:
            lf.sink_parquet(str(temp_parquet), compression=_TEMP_PARQUET_COMPRESSION, row_group_size=512)
        except TypeError:
            lf.collect(streaming=True).write_parquet(str(temp_parquet), compression=_TEMP_PARQUET_COMPRESSION)
    else:
        logger.info("Phase A エンジン: pyarrow (batched fallback)")
        import pyarrow as pa
        import pyarrow.csv as pacsv
        import pyarrow.parquet as pq

        column_types = {h: pa.float64() for h in int_headers}
        read_opts = pacsv.ReadOptions(block_size=32 << 20, skip_rows=skip)
        parse_opts = pacsv.ParseOptions(delimiter=delim)
        convert_opts = pacsv.ConvertOptions(column_types=column_types)
        reader = pacsv.open_csv(str(intensity_path), read_opts, parse_opts, convert_opts)
        first_batch = reader.read_next_batch()
        arrow_schema = pa.schema([(n, pa.float64()) for n in first_batch.schema.names])
        with pq.ParquetWriter(str(temp_parquet), arrow_schema, compression=_TEMP_PARQUET_COMPRESSION) as w:
            w.write_batch(first_batch)
            while True:
                try:
                    w.write_batch(reader.read_next_batch())
                except StopIteration:
                    break


# peak-list の m/z と Intensity の m/z を突き合わせる許容（Da）
_PEAKLIST_MZ_TOL_DA = 0.01


def _read_peaklist(path: Path):
    """peak-list (Feature list) CSV から (m/z 配列, Name 配列) を返す。"""
    import pandas as pd
    headers, delim, skip = first_header_and_skipcount(path)
    df = pd.read_csv(
        path, sep=delim, skiprows=skip, header=0,
        engine="c", encoding="utf-8", dtype=str, keep_default_na=False,
    )
    norm = [str(h).strip().lower().replace(" ", "") for h in df.columns]
    mz_idx = next((i for i, c in enumerate(norm) if c in ("m/z", "mz", "m_z")), 0)
    name_idx = next((i for i, c in enumerate(norm) if c == "name"), None)
    if name_idx is None:
        raise ValueError(f"peak-list に Name 列がありません: {path.name}")
    mz_vals = pd.to_numeric(df.iloc[:, mz_idx], errors="coerce")
    mask = mz_vals.notna()
    return mz_vals[mask].to_numpy(dtype=float), df.iloc[:, name_idx][mask].astype(str).tolist()


def _ensure_unique_colnames(names: list[str]) -> list[str]:
    """列名の重複を避ける（万一 4 桁 m/z＋化合物名が衝突した場合の保険）。"""
    seen: dict[str, int] = {}
    out: list[str] = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n} #{seen[n]}")
        else:
            seen[n] = 0
            out.append(n)
    return out


def convert_scils_to_parquet(
    input_folder: str,
    output_path: str,
    *,
    spot_block: int = 200,
    store_float32: bool = True,
    organize: bool = True,
    annotation_tol: float = 1e-6,
    progress_cb=None,
) -> ConversionResult:
    """SCiLS Intensity+Spot(+Annotation) CSV フォルダを Parquet に変換する。

    Parameters
    ----------
    input_folder : str
        Intensity/Spot/Annotation CSV が同居するフォルダ
    output_path : str
        書き出す .parquet のフルパス
    spot_block : int
        Phase B で 1 回に読み込む spot 列数 (既定 200)
    store_float32 : bool
        Parquet の m/z 列を float32 で格納 (容量半減)
    organize : bool
        True なら出力フォルダを `<BASE>_Transform` サブフォルダに変更し、
        元 CSV を同サブフォルダに移動
    annotation_tol : float
        Annotation CSV の座標がマスターと一致するかの許容誤差
    progress_cb : callable | None
        `progress_cb(value:int, maximum:int, label:str)` 形式の進捗通知（0-100）。
        既定 None なら何もしない（UI のバックグラウンド実行から渡す）。
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    t_start = time.perf_counter()
    result = ConversionResult()

    def _report(value, label):
        """進捗を呼び出し元へ通知（失敗は無視）。value は 0-100。"""
        if progress_cb is None:
            return
        try:
            progress_cb(int(value), 100, label)
        except Exception:
            pass

    folder = Path(input_folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"入力フォルダが存在しません: {input_folder}")

    # 1) 役割自動検出
    detected = auto_detect_file_roles(folder)
    intensity_path: Path = detected["intensity"]
    spots_path: Path = detected["spot"]
    annotation_files: list[Path] = detected["annotations"]
    peaklist_path: Optional[Path] = detected.get("peak_list")

    result.source_intensity = str(intensity_path)
    result.source_spot = str(spots_path)
    result.annotation_files = [str(p) for p in annotation_files]
    result.has_peak_list = peaklist_path is not None
    result.peak_list_file = str(peaklist_path) if peaklist_path else ""

    # 2) 出力先確定 (organize の場合はサブフォルダに変更)
    base = derive_base_name(intensity_path, spots_path)
    out_path = Path(output_path)
    if organize:
        sub = intensity_path.parent / f"{base}_Transform"
        sub.mkdir(parents=True, exist_ok=True)
        out_path = sub / out_path.name
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("=== SCiLS 変換開始 ===")
    logger.info("Intensity: %s", intensity_path)
    logger.info("Spot     : %s", spots_path)
    if annotation_files:
        logger.info("Annotation: %s", [p.name for p in annotation_files])
    if peaklist_path is not None:
        logger.info("Peak-list : %s", peaklist_path.name)
    logger.info("出力      : %s", out_path)

    # 3) Phase A: Intensity CSV → 一時 Parquet
    temp_parquet = intensity_path.parent / f"{base}_temp.parquet"
    int_headers, delim, skip = first_header_and_skipcount(intensity_path)
    logger.info("Phase A 開始: CSV→一時 Parquet (%s)", temp_parquet.name)
    phase_a_start = time.perf_counter()
    _report(2, "CSV を読み込み中…（Phase A）")
    _csv_to_temp_parquet(intensity_path, int_headers, delim, skip, temp_parquet)
    logger.info("Phase A 完了: %.1f 秒", time.perf_counter() - phase_a_start)

    pf = None
    try:
        # 4) m/z 列読込 + ソート
        pf = pq.ParquetFile(str(temp_parquet))
        mz_col_name = int_headers[0]
        mz_num = pf.read(columns=[mz_col_name]).column(0).to_numpy(zero_copy_only=False).astype(np.float64)
        if np.isnan(mz_num).any():
            raise ValueError("Intensity の m/z 列に欠損値があります")
        order_mz = np.argsort(mz_num, kind="mergesort")
        mz_sorted = mz_num[order_mz]
        n_mz = len(mz_sorted)
        _report(8, "m/z を読込・ソート中…")

        # 4.5) peak-list（化合物アノテーション）→ per-feature 注釈テーブル
        feat_ann_df = None
        if peaklist_path is not None:
            try:
                _report(12, "化合物アノテーションを付与中…")
                from app.services import peak_annotation as _pann
                pk_mz, pk_names = _read_peaklist(peaklist_path)
                feat_ann_df = _pann.build_feature_annotation_table(
                    mz_sorted, pk_mz, pk_names, tol_da=_PEAKLIST_MZ_TOL_DA
                )
                result.n_annotated = int((feat_ann_df["compound"].astype(str) != "").sum())
                logger.info("Peak-list 注釈: %d / %d feature にマッチ", result.n_annotated, n_mz)
            except Exception as e:
                logger.warning("peak-list 解析に失敗（注釈なしで継続）: %s", e)
                result.warnings.append(f"peak-list の解析に失敗（注釈なしで継続）: {e}")
                feat_ann_df = None

        # 5) Spot テーブル読込 + マッピング
        spot_index, x_arr, y_arr = read_spot_table(spots_path)
        sort_idx, x_sorted, y_sorted, spot_labels_sorted, spot_index_sorted, mapping_warnings = (
            compute_spot_mapping(int_headers, spot_index, x_arr, y_arr)
        )
        result.warnings.extend(mapping_warnings)
        n_spots = len(spot_labels_sorted)

        # 6) Annotation 解決
        if annotation_files:
            ann_map, ann_warnings = build_annotation_map(
                annotation_files, spot_index, x_arr, y_arr, tol=annotation_tol
            )
            result.warnings.extend(ann_warnings)
        else:
            # Spot ファイル名からラベル導出 (全 spot に単一ラベル)
            single_label = annotation_label_from_filename(spots_path)
            logger.info("Annotation ファイルなし → 全 spot に '%s' を割当", single_label)
            ann_map = {int(si): single_label for si in spot_index}
        annotation_sorted = [ann_map[int(si)] for si in spot_index_sorted]
        result.annotation_labels = sorted(set(annotation_sorted))

        # 7) Intensity ヘッダ整合チェック (全 spot 列が一時 Parquet に存在するか)
        header_set = set(int_headers)
        missing_cols = [c for c in spot_labels_sorted if c not in header_set]
        if missing_cols:
            raise ValueError(
                f"Intensity ヘッダから一部 spot 列が欠落: {missing_cols[:10]}"
            )

        # 8) Phase B: 一時 Parquet から chunk 読み → 最終 Parquet 書き込み
        logger.info("Phase B 開始: chunk 読込 + 最終 Parquet 書き込み")
        phase_b_start = time.perf_counter()

        if feat_ann_df is not None:
            from app.services import peak_annotation as _pann
            mz_colnames = _ensure_unique_colnames([
                _pann.make_column_name(feat_ann_df["raw"].iloc[i], float(mz_sorted[i]))
                for i in range(n_mz)
            ])
        else:
            mz_colnames = [f"{v:.6f}" for v in mz_sorted]
        # スキーマにメタデータを直接付与（ParquetWriter のスキーマに含めることで
        # 全バッチ＝ファイルへ確実に永続化される）。mz_sorted はフル桁の m/z 一覧で、
        # 列名がパイプ全文になっても m/z を確実に復元できる正となる。
        schema_md = {
            b"mz_sorted": ",".join(f"{v:.10g}" for v in mz_sorted).encode("utf-8"),
            b"annotation_files": ";".join(p.name for p in annotation_files).encode("utf-8"),
        }
        if peaklist_path is not None:
            schema_md[b"peak_list"] = peaklist_path.name.encode("utf-8")
        schema = pa.schema(
            [("id", pa.int64()), ("x", pa.float64()), ("y", pa.float64())]
            + [(name, pa.float32() if store_float32 else pa.float64()) for name in mz_colnames]
            + [("annotation", pa.string())],
            metadata=schema_md,
        )
        intensity_dtype = np.float32 if store_float32 else np.float64

        with pq.ParquetWriter(str(out_path), schema, compression="zstd") as pq_writer:
            for start in range(0, n_spots, spot_block):
                end = min(n_spots, start + spot_block)
                spot_cols_block = spot_labels_sorted[start:end]
                table_block = pf.read(columns=spot_cols_block)
                vals = np.column_stack([
                    table_block.column(c).to_numpy(zero_copy_only=False) for c in spot_cols_block
                ])
                if vals.shape[0] != n_mz:
                    raise RuntimeError(f"行数不一致: 期待 {n_mz}, 実際 {vals.shape[0]}")
                # m/z 並べ替え＋目的 dtype へ「ブロックごとに 1 回だけ」キャスト。
                # 結果は C 連続なので vals_T[:, j]（= vals の行 j）は連続ビューとなり、
                # pa.array がゼロコピー化する（従来の列ごと astype × n_mz 回を撤廃）。
                vals = vals[order_mz, :].astype(intensity_dtype, copy=False)
                vals_T = vals.T  # (n_block_spots, n_mz)

                arrays = [
                    pa.array(np.arange(start + 1, end + 1, dtype=np.int64)),
                    pa.array(x_sorted[start:end].astype(np.float64, copy=False)),
                    pa.array(y_sorted[start:end].astype(np.float64, copy=False)),
                ]
                arrays.extend(pa.array(vals_T[:, j]) for j in range(n_mz))
                arrays.append(pa.array(annotation_sorted[start:end], type=pa.string()))
                table = pa.Table.from_arrays(arrays, schema=schema)
                pq_writer.write_table(table)
                _report(15 + int(80 * end / n_spots), f"書き込み中… {end:,}/{n_spots:,} spot")

        logger.info("Phase B 完了: %.1f 秒", time.perf_counter() - phase_b_start)
        _report(96, "サイドカー出力・ファイル整理中…")

        # 8.5) サイドカー（per-feature 注釈テーブル）を出力
        if feat_ann_df is not None:
            sidecar = out_path.parent / f"{base}_feature_annotations.parquet"
            try:
                feat_ann_df.to_parquet(str(sidecar), index=False)
                result.sidecar_path = str(sidecar)
                logger.info("サイドカー出力: %s", sidecar.name)
            except Exception as e:
                logger.warning("サイドカー出力に失敗: %s", e)
                result.warnings.append(f"サイドカー出力に失敗: {e}")

        result.output_path = str(out_path)
        result.n_spots = n_spots
        result.n_mz_features = n_mz
        result.has_annotation = bool(annotation_files)
    finally:
        # 一時 Parquet を削除 (Windows ファイルロック対策で pf を先に解放)
        pf = None
        if temp_parquet.exists():
            try:
                temp_parquet.unlink()
                logger.info("一時ファイル削除: %s", temp_parquet.name)
            except Exception as e:
                logger.warning("一時ファイル削除失敗: %s", e)

    # 9) organize: 元 CSV をサブフォルダに移動
    if organize:
        moved: list[str] = []
        try:
            move_srcs = [intensity_path, spots_path, *annotation_files]
            if peaklist_path is not None:
                move_srcs.append(peaklist_path)
            for src in move_srcs:
                dst = _move_into_folder(src, out_path.parent)
                moved.append(str(dst))
            result.moved_files = moved
            result.organized = True
        except Exception as e:
            raise RuntimeError(f"出力は作成済みですが元 CSV の移動に失敗: {e}")

    result.duration_sec = time.perf_counter() - t_start
    _report(100, "完了")
    logger.info(
        "変換完了: %s (%d spots × %d m/z, %.1f 秒)",
        out_path, result.n_spots, result.n_mz_features, result.duration_sec,
    )
    return result
