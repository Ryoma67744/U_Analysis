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
#   row group: 既定で「全行 1 つ」。1 列 (= 1 化合物) がファイル上で連続し、フッタが
#              桁違いに小さくなる (実データ規模で 735MB -> 約 2MB、open 3.3s -> 15ms)。
#              旧レイアウト (200 行/row group) のファイルもそのまま読める。
# =============================================================================

from __future__ import annotations

import csv
import logging
import math
import os
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
    # ★ ver55.0: 領域アノテーションの由来。"csv" = 実ファイルから解決、
    #   "none" = 一枚も渡されず全 spot が 'Unannotated'。
    annotation_source: str = "none"
    has_peak_list: bool = False
    peak_list_file: str = ""
    sidecar_path: str = ""
    n_annotated: int = 0
    organized: bool = False
    moved_files: list[str] = field(default_factory=list)
    duration_sec: float = 0.0
    warnings: list[str] = field(default_factory=list)
    # --- 出力 Parquet のレイアウト（書き込み後に実ファイルから読み取って記録）---
    row_group_rows: int = 0        # 実際に採用した 1 row group あたりの行数
    n_row_groups: int = 0
    footer_bytes: int = 0          # metadata.serialized_size（フッタ実サイズ）
    row_group_policy: str = ""     # "single" / "explicit" / "single-fallback"


# ---------------------------------------------------------------------------
# CSV ヘッダ読み込みユーティリティ
# ---------------------------------------------------------------------------

def first_header_and_skipcount(path: Path) -> tuple[list[str], str, int]:
    """`#` 行をスキップして最初のヘッダ行を返す。

    Returns
    -------
    (headers, delimiter, skip_lines)
    """
    # ★ ver51.9: `utf-8-sig` で開く。Windows の Excel / SCiLS が出す CSV は
    #   **UTF-8 BOM 付き**が既定で、素の `utf-8` だと先頭セルが `m/z` ではなく
    #   `﻿m/z` になる。影響は 2 つ:
    #     1. ver51.8 で m/z 列を必須にしたので、BOM 付きピークリストが
    #        「m/z 列がありません」で落ちるようになった
    #        (それ以前は「見つからなければ列 0」で偶然正しかった)
    #     2. BOM が `#` コメント行に付くと `startswith("#")` が False になり、
    #        **コメント行をヘッダとして採用**する。例外は出ず列が丸ごとずれる
    #   BOM が無いファイルには影響しない。
    skip = 0
    header_line: Optional[str] = None
    with path.open("r", newline="", encoding="utf-8-sig", errors="replace") as f:
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

    # サイズ降順に並べる。★ ver55.0: 以前はここで「先頭 = マスター Spot、残り =
    # Annotation」と**確定**させていたが、切片ごとに座標を Export したフォルダには
    # 全体を覆うマスターが存在せず、一番大きい切片 1 枚がマスターに昇格していた。
    # 役割の確定は spot 集合を見る `build_master_spot_table` に委ね、ここでは
    # 候補を順序付けて渡すだけにする（`spot` / `annotations` は従来の呼び出し元と
    # プレビュー用に残す）。
    spot_likes.sort(key=lambda p: p.stat().st_size, reverse=True)
    return {
        "intensity": intensities[0],
        "spot": spot_likes[0],
        "annotations": spot_likes[1:],
        "spot_likes": spot_likes,
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
    *,
    drop_uncovered: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str], np.ndarray, list[str]]:
    """Intensity ヘッダと Spot テーブルを厳格整合して (y, x) ソートする。

    完全一致を要求。±1 グローバルシフトのみ自動補正 (データ不動, ラベルのみ調整)。

    drop_uncovered=True なら、座標が無い spot 列を**除外して**変換を続ける
    (切片だけを Export し、測定全体の Intensity と突き合わせる運用向け)。
    除外件数は必ず warnings に出す。この経路では ±1 の自動補正は行わない
    — 部分カバーでは shift の有無を集合の一致では判定できず、誤って補正すると
    全 spot の座標が 1 つずれた画像を黙って出力してしまうため。

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
        elif drop_uncovered:
            # 座標のある spot だけを残す。数の食い違いを黙って埋めないよう、
            # 除外件数は必ず申告する。
            keep = np.fromiter(
                (n in set_spot for n in spot_nums_int.tolist()), dtype=bool,
                count=len(spot_nums_int),
            )
            n_keep = int(keep.sum())
            if n_keep == 0:
                raise ValueError(
                    "座標 CSV に載っている spot が Intensity に 1 つもありません。\n"
                    f"- Intensity の spot 番号: {sorted(set_int)[:5]} ...\n"
                    f"- 座標 CSV の spot 番号: {sorted(set_spot)[:5]} ...\n"
                    "別の測定の Intensity と座標を突き合わせていないかご確認ください。"
                )
            if extra:
                warnings.append(
                    f"座標 CSV に無い {len(extra)} spot を除外しました"
                    f"（Intensity の {len(set_int)} spot 中 {n_keep} spot を変換）。"
                )
                logger.warning("座標なし spot を除外: %d 個 (例: %s)", len(extra), extra[:10])
            if missing:
                warnings.append(
                    f"座標だけがあり Intensity に列が無い spot が {len(missing)} 個"
                    f"（例: {missing[:10]}）。これらは出力に含まれません。"
                )
            # ★ 部分カバーでは ±1 シフトを集合の一致で判定できない。自動補正は
            #   しないが、疑わしいときは黙らずに申告する（1 spot ずれた画像は
            #   一見それらしく見えてしまい、後から気付けない）。
            n0 = len(set_int & set_spot)
            n_minus = len({n - 1 for n in set_int} & set_spot)
            n_plus = len({n + 1 for n in set_int} & set_spot)
            if max(n_minus, n_plus) > n0:
                warnings.append(
                    "spot 番号が ±1 ずれている可能性があります"
                    f"（一致数: そのまま {n0} / -1 補正で {n_minus} / +1 補正で {n_plus}）。"
                    "部分カバーでは自動補正しないため、変換後の画像で位置が正しいか"
                    "必ずご確認ください。"
                )
                logger.warning(
                    "±1 シフトの疑い: 一致数 そのまま=%d, -1=%d, +1=%d", n0, n_minus, n_plus
                )
            spot_labels = [spot_labels[i] for i in np.nonzero(keep)[0]]
            spot_nums_int = spot_nums_int[keep]
        else:
            msg = [
                "Intensity と Spot テーブルの spot 番号が一致しません。",
                f"- Intensity にのみ存在: {len(extra)} 個 (例: {extra[:10]})",
                f"- Spot にのみ存在: {len(missing)} 個 (例: {missing[:10]})",
                "SCiLS Lab で両ファイルの spot 番号が一致するよう再エクスポートしてください。",
                "(±1 のグローバルシフトのみ自動補正対象)",
            ]
            if extra:
                msg.append(
                    "測定全体の Intensity に対して切片ごとの座標 CSV しか無い場合は、"
                    "詳細設定の「座標 CSV に無い spot を除外して変換する」を有効にすると"
                    "座標のある spot だけを変換できます。",
                )
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


def build_master_spot_table(
    spot_like_files: list[Path],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, Optional[dict[int, str]], list[str]]:
    """SpotIndex/X/Y を持つ CSV 群からマスター座標表を組み立てる。

    レイアウトを **ファイル名やサイズではなく spot 集合で** 判別する。

    1. マスター + 領域注釈 (従来)
       全 spot の座標を持つ 1 本があり、残りはその部分集合。
       -> そのファイルをマスターとして返し ``region_map=None``。
          ラベル解決は従来どおり :func:`build_annotation_map` が行う。
    2. 切片ごとの座標ファイル (ver55.0 で追加)
       全体を覆う 1 本が無く、各ファイルが切片単位の座標を持つ。
       -> 全ファイルの和集合をマスターにし、ファイル名をそのまま領域ラベルに
          した ``region_map`` を返す。

    ★ ver55.0: 以前は「一番大きい spot_like ファイル = マスター」と決め打ちして
      いたため、切片ごとに Export したフォルダでは**一番大きい切片 1 枚だけが
      マスターに昇格**し、他の切片はその部分集合であることを要求されて
      「spot 番号が一致しません」で必ず落ちていた。しかも仮に数が合っても、
      マスターに吸収された切片は領域ラベルを失っていた。

    Returns
    -------
    (spot_index, x, y, region_map, warnings)
    """
    if not spot_like_files:
        raise ValueError("SpotIndex/X/Y を持つ CSV が 1 本もありません。")

    warnings: list[str] = []
    tables = [(fp, *read_spot_table(fp)) for fp in spot_like_files]
    sets = [{int(v) for v in si.tolist()} for _, si, _, _ in tables]
    union: set[int] = set().union(*sets)

    # レイアウト 1: 先頭 (最大サイズ) が全体を覆う → 従来どおり
    if sets[0] == union:
        _, si, x, y = tables[0]
        return si, x, y, None, warnings

    # レイアウト 2: 切片ごとの座標ファイルを統合する
    labels: list[str] = []
    owner: dict[int, str] = {}
    coords: dict[int, tuple[float, float]] = {}
    for (fp, si, x, y), s in zip(tables, sets):
        label = annotation_label_from_filename(fp)
        if label in labels:
            raise ValueError(f"領域ラベル名が重複しています: '{label}' ({fp.name})")
        labels.append(label)

        dup = sorted(s & owner.keys())
        if dup:
            prev = sorted({owner[d] for d in dup})
            raise ValueError(
                f"座標 CSV 間で spot が重複しています: '{label}' ({fp.name}) の "
                f"{len(dup)} spot が既存の領域 {prev} と重複しています。"
                f"例: {dup[:5]}\n"
                "切片ごとに Export したファイルは重複しない想定です。同じ領域を"
                "二重に Export していないか、測定全体のマスター Spot CSV が"
                "混ざっていないかをご確認ください。"
            )
        for i, sv in enumerate(si.tolist()):
            owner[int(sv)] = label
            coords[int(sv)] = (float(x[i]), float(y[i]))

    order = sorted(coords)
    spot_index = np.asarray(order, dtype=np.int64)
    x_arr = np.asarray([coords[s][0] for s in order], dtype=np.float64)
    y_arr = np.asarray([coords[s][1] for s in order], dtype=np.float64)

    breakdown = " / ".join(f"{lab} ({len(s)} spot)" for lab, s in zip(labels, sets))
    warnings.append(
        "測定全体を覆うマスター Spot CSV が無いため、座標 CSV を統合して座標表を"
        f"作りました: {breakdown} — 計 {len(order)} spot。"
        "ファイル名がそのまま領域ラベルになります。"
    )
    logger.info("座標 CSV %d 本を統合 → %d spot (%s)", len(tables), len(order), breakdown)
    return spot_index, x_arr, y_arr, owner, warnings


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
            prev_labels = sorted({mapping[s] for s in overlap})
            n, n_this = len(overlap), len(set_ann)
            dup_hint = (
                f"（'{label}' の全 {n_this} spot が重複＝重複/複製された領域の可能性が高い）"
                if n == n_this else ""
            )
            raise ValueError(
                f"領域アノテーションが重複しています: '{label}' ({fp.name}) の {n} spot が "
                f"既存領域 {prev_labels} と同一です{dup_hint}。"
                f"例: spot {sorted(overlap)[:5]}。"
                "同じ領域を二重にエクスポートしていないか、SCiLS の ROI 定義をご確認ください。"
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
    # SCILS_NO_POLARS=1 で polars を使わず pyarrow 経路を強制（CPU 非互換などの保険）。
    _use_polars = False
    if os.environ.get("SCILS_NO_POLARS"):
        logger.info("SCILS_NO_POLARS 指定 → pyarrow batched を使用")
    else:
        try:
            import polars as pl
            _use_polars = True
        except ImportError:
            logger.info("polars 未インストール → pyarrow batched にフォールバック")

    if _use_polars:
        logger.info("Phase A エンジン: polars (streaming sink)")
        # ★ ver55.0: **列名キーの dict**で渡す。ver51.9 が「BOM で 1 列目のキーが
        #   一致せず dtype 指定が黙って無視される」ことを恐れて位置指定の list に
        #   変えたが、`pl.scan_csv` は `new_columns=` 併用時以外 list を受け付けず
        #   `TypeError: expected 'schema_overrides' dict, found 'list'` で即死する
        #   （polars 0.20〜1.33 の全バージョンで同じ）。恐れていた BOM 事故は
        #   そもそも起きない:
        #     1. polars はスキーマ推論・本文パースの両経路で BOM を剥ぐ
        #     2. 剥がなかったとしても、**列数と同数の dict** は polars 側で index
        #        適用にフォールバックし、列名も int_headers[i] に書き換えられる
        #        (schema_inference の `schema_overwrite.len() == headers.len()` 分岐)
        #   つまり dict は位置指定の上位互換で、Phase B が名前で spot 列を読む
        #   (`pf.read(columns=...)`) ための名前正規化まで担保する。
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
        # ★ ver55.0: ここに `except TypeError: lf.collect(streaming=True)` のシムが
        #   あったが、compression/row_group_size は polars 1.x 全域で有効なので何も
        #   守っておらず、sink_parquet 内部の任意の TypeError を拾って数 GB を
        #   非ストリーミングで全メモリ展開する経路に落とすだけだった (12 GB
        #   コンテナでは OOM)。握り潰さずそのまま投げる。
        lf.sink_parquet(str(temp_parquet), compression=_TEMP_PARQUET_COMPRESSION, row_group_size=512)
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


def _read_peaklist(path: Path, *, return_skipped: bool = False):
    """peak-list (Feature list) CSV から (m/z 配列, Name 配列) を返す。

    SCiLS の `Name` 欄は `adduct_family=mass_only;n=2;adducts=[M-H]-,[M]-;peaks=12,47` のように
    区切り文字 (`;`) をフィールド内部に含むことがある（未クオート）。素朴な `pd.read_csv` では
    列数が合わず壊れるため、ヘッダ列数を基準に「Name より後ろの列数」を固定し、超過トークンを
    Name へ再結合して原文を復元する。区切りを内部に含み得るのは Name 列のみ（m/z・Interval・
    Color は数値/16進、Name 以降は強度数値）という構造に依拠するため、超過分は必ず Name に属する。

    ★ ver52.3 (T5): 壊れ行は 3 通りの理由で **無言で捨てられていた**。
      落ちた行の化合物名は変換後 Parquet の**列名に焼き込まれない**ので、
      あとから復元できない（サイドカーを作り直しても元 CSV が要る）。
      `return_skipped=True` で理由別の内訳を受け取れるようにし、
      呼び出し 2 箇所（変換 / 分子情報の後付け）から利用者へ見せる。
      既定は 2-tuple のままなので既存の呼び出しは壊れない。

    Args:
        return_skipped: True なら `(m/z 配列, Name 配列, skipped)` の 3-tuple を返す。
            `skipped` は `{"short_row": n, "non_numeric_mz": n, "non_finite_mz": n}`。
    """
    headers, delim, skip = first_header_and_skipcount(path)
    norm = [str(h).strip().lower().replace(" ", "") for h in headers]
    ncol = len(headers)
    # ★ ver51.8: m/z 列が見つからないとき **黙って列 0 にフォールバック**していた。
    #   Name 列は同じ状況で例外を投げるのに非対称で、正当化できない。
    #   ヘッダが認識できない書式（"Center m/z" / "Mass" / 先頭に Index 列がある等）
    #   だと `float(tok[0])` がインデックス値を読んで成功してしまうため、
    #   例外も出ずに **全行の m/z が行番号**になり、以降の近傍マッチが
    #   「全部 No DB hit」か「低 m/z だけ無関係な化合物に一致」になる。
    #   気づける手段が無いので、ここで止める。
    mz_idx = next((i for i, c in enumerate(norm) if c in ("m/z", "mz", "m_z")), None)
    if mz_idx is None:
        raise ValueError(
            f"peak-list に m/z 列がありません: {path.name} (ヘッダ: {headers})")
    name_idx = next((i for i, c in enumerate(norm) if c == "name"), None)
    if name_idx is None:
        raise ValueError(f"peak-list に Name 列がありません: {path.name}")
    n_after_name = ncol - name_idx - 1   # Name より後ろの列数（各1トークン・区切り無し）

    mz_list: list[float] = []
    name_list: list[str] = []
    skipped = {"short_row": 0, "non_numeric_mz": 0, "non_finite_mz": 0}
    with path.open("r", newline="", encoding="utf-8", errors="replace") as f:
        for _ in range(skip + 1):        # `#` 行 + ヘッダ行を読み飛ばす
            next(f, None)
        for line in f:
            line = line.rstrip("\r\n")
            if not line:
                continue
            tok = line.split(delim)
            if len(tok) < ncol:          # 列不足の壊れ行はスキップ
                skipped["short_row"] += 1
                continue
            try:
                mz = float(tok[mz_idx])
            except ValueError:
                skipped["non_numeric_mz"] += 1
                continue                 # m/z が数値でない行は除外（従来の NaN マスク相当）
            # ★ ver52.3: `float("nan")` / `float("inf")` は **例外を出さない**ので
            #   従来はここを素通りし、注釈テーブルの最近傍探索で
            #   （比較が常に偽になるため）どの feature にも当たらず消えていた。
            #   「読めなかった」と同じ扱いにして数える。
            if not math.isfinite(mz):
                skipped["non_finite_mz"] += 1
                continue
            # Name は name_idx から「後ろの固定列数」を残して再結合（内部の区切りを原文復元）
            name = delim.join(tok[name_idx:len(tok) - n_after_name])
            mz_list.append(mz)
            name_list.append(name)
    arr = np.asarray(mz_list, dtype=float)
    if return_skipped:
        return arr, name_list, skipped
    return arr, name_list


def peaklist_skip_message(skipped: dict) -> str:
    """`_read_peaklist(..., return_skipped=True)` の内訳を利用者向け 1 行にする。

    捨てた行が無ければ空文字。呼び出し 2 箇所で同じ文言にするためここに置く
    （文言を 2 か所で書くと、片方だけ直す T3 を自分で作ることになる）。
    """
    labels = (("short_row", "列数が足りない行"),
              ("non_numeric_mz", "m/z が数値でない行"),
              ("non_finite_mz", "m/z が NaN/Inf の行"))
    parts = [f"{label} {skipped.get(key, 0)} 件"
             for key, label in labels if skipped.get(key, 0)]
    if not parts:
        return ""
    return ("peak-list の " + " / ".join(parts)
            + " を除外しました（この行の化合物名は付与されません）。")


def _check_conversion_memory(
    intensity_path: Path,
    *,
    n_spots_hint: int = 0,
    itemsize: int = 4,
) -> None:
    """変換前の簡易メモリチェック。空きメモリが Intensity CSV に対して著しく不足する
    場合は明示的に RuntimeError を送出し、無言の OOM（途中終了＋0byte 一時ファイル
    残留）を避ける。psutil 不在環境ではスキップ。

    Phase A は数分かかるため、全行 1 row group の出力バッファが明らかに載らない場合も
    ここで落とす。ただし n_mz は Phase A 後まで確定しないので CSV サイズからの粗い概算に
    留め、「絶望的な場合の早期却下」にのみ使う（レイアウト決定には使わない）。

    Parameters
    ----------
    n_spots_hint : int
        spot 数の上限見積り（`len(int_headers) - 1`）。0 なら出力バッファの概算をスキップ。
    itemsize : int
        出力 m/z 列 1 要素のバイト数（float32 なら 4）。
    """
    try:
        csv_bytes = intensity_path.stat().st_size
    except Exception:
        return
    csv_gb = csv_bytes / (1024 ** 3)
    if csv_gb < 0.5:
        return  # 小さいデータはメモリチェック不要

    avail_gb = _available_memory_gb()
    if avail_gb is None:
        return

    need_gb = csv_gb * 1.5 + 1.0

    # 全行 1 row group の出力バッファ概算。n_mz は Phase A 後まで不明なので
    # 「CSV 1 セルあたり平均バイト数」から逆算する（誤差は大きい）。
    buf_gb = 0.0
    if n_spots_hint > 0:
        n_cols = n_spots_hint + 1                       # m/z 列 + spot 列
        n_mz_est = max(1, int(csv_bytes / (n_cols * _AVG_CSV_CELL_BYTES)))
        buf_gb = n_mz_est * n_spots_hint * itemsize / (1024 ** 3)
        # Phase A / Phase B は同時に走らないので、必要量は両者の最大値
        need_gb = max(need_gb, buf_gb + _PHASE_A_FOOTER_MARGIN_GB)

    logger.info(
        "変換メモリ確認: Intensity CSV %.2f GB / 空き %.2f GB（目安 %.1f GB 以上"
        "%s）",
        csv_gb, avail_gb, need_gb,
        f" / 出力バッファ概算 {buf_gb:.1f} GB" if buf_gb else "",
    )
    if avail_gb < need_gb:
        raise RuntimeError(
            f"空きメモリが不足しています（Intensity CSV {csv_gb:.1f} GB に対し空き "
            f"{avail_gb:.1f} GB）。約 {need_gb:.1f} GB 以上の空きが必要です。"
            "他の解析・変換の完了を待つか、サーバのメモリを増やしてください。"
        )


# ---------------------------------------------------------------------------
# 出力 row group のサイズ決定
# ---------------------------------------------------------------------------
#
# ParquetWriter は「1 回の write_table = 1 row group」で、row group ごとの column chunk
# メタデータを close() まで RAM に保持しフッタをまとめて Thrift 直列化する。実測で
# 約 6.4MB/row group（2,700 列・注釈付き列名）。したがってメモリコストは
#
#     total(rg) = n_mz * rg * itemsize            # 出力バッファ
#               + _RG_METADATA_BYTES * ceil(n_spots / rg)   # ライタが抱えるメタデータ
#
# という U 字になり、rg を小さくすれば安全とは限らない（むしろ悪化する）。
# 実データ規模（203,078 spot × 2,700 m/z）では 200 行/row group が約 3.8GB、
# 全行 1 つが約 2.2GB で、全行 1 つのほうが軽い。
_RG_METADATA_BYTES = 6.4 * 1024 ** 2
# Phase A の一時 parquet のフッタは Phase B の間ずっと常駐する（pf を保持するため）。
# n_mz 行 × n_spots 列 ÷ row_group_size=512 で column chunk が百万単位になり、
# 実データ規模で約 1.15GB。計上しないと 1GB 以上見積りを外す。
_PHASE_A_FOOTER_MARGIN_GB = 1.5
# 空きメモリのうち変換に使ってよい割合（残りは他ユーザーの解析・アプリ本体のため）
_RG_AVAIL_FRACTION = 0.6
# これ以上小さく割っても意味がない下限（メタデータ側が支配的になる）
_RG_MIN_ROWS = 1024
# CSV 1 セルあたりの平均バイト数（早期チェックの粗い概算用）
_AVG_CSV_CELL_BYTES = 11


def _available_memory_gb() -> Optional[float]:
    """変換に使える空きメモリ (GB)。cgroup 上限を優先し、無ければ psutil にフォールバック。

    psutil.virtual_memory().available はホストの /proc/meminfo をそのまま返し cgroup を
    見ないため、大きなホスト上のコンテナでは「コンテナはほぼ満杯なのにチェックを通る」。
    analysis_runner に既にある cgroup 読み取りを再利用する。
    """
    limit_gb = None
    try:
        from app.services.analysis_runner import _container_memory_limit_gb
        limit_gb = _container_memory_limit_gb()
    except Exception:
        limit_gb = None

    if limit_gb is not None:
        # cgroup v2 の現在使用量が読めれば「上限 - 使用中」を空きとする
        for path in ("/sys/fs/cgroup/memory.current",
                     "/sys/fs/cgroup/memory/memory.usage_in_bytes"):
            try:
                used_gb = int(Path(path).read_text(encoding="utf-8").strip()) / (1024 ** 3)
            except (OSError, ValueError):
                continue
            return max(0.0, limit_gb - used_gb)

    try:
        import psutil
        return psutil.virtual_memory().available / (1024 ** 3)
    except Exception:
        return None


def _row_group_cost_bytes(rg_rows: int, *, n_spots: int, n_mz: int, itemsize: int) -> float:
    """1 row group を rg_rows 行にしたときの Phase B ピークメモリ概算 (bytes)"""
    n_groups = -(-n_spots // max(1, rg_rows))
    return n_mz * rg_rows * itemsize + _RG_METADATA_BYTES * n_groups


def _plan_row_groups(
    *,
    n_spots: int,
    n_mz: int,
    itemsize: int,
    requested: Optional[int] = None,
) -> tuple[int, str, list[str]]:
    """出力 row group の行数を決める。戻り値 = (rg_rows, policy, warnings)

    既定 (requested=None) は「全行 1 row group」。予算に収まらない場合のみ、
    U 字コストを最小化する行数へ落として警告を返す（単純に小さくすると row group 数が
    増えてメタデータが膨らみ、かえって悪化するため）。
    """
    warns: list[str] = []
    n_spots = max(1, int(n_spots))
    n_mz = max(1, int(n_mz))

    if requested is None:
        want, policy = n_spots, "single"
    else:
        want, policy = max(1, int(requested)), "explicit"
    want = min(want, n_spots)

    avail_gb = _available_memory_gb()
    if avail_gb is None:
        logger.info("row group 計画: 空きメモリ不明のため %s (%d 行) をそのまま採用", policy, want)
        return want, policy, warns

    budget = max(0.0, (avail_gb - _PHASE_A_FOOTER_MARGIN_GB)) * _RG_AVAIL_FRACTION * 1024 ** 3
    want_cost = _row_group_cost_bytes(want, n_spots=n_spots, n_mz=n_mz, itemsize=itemsize)

    if want_cost <= budget:
        logger.info(
            "row group 計画: %s → %d 行 × %d group（想定 %.2f GB / 予算 %.2f GB）",
            policy, want, -(-n_spots // want), want_cost / 1024 ** 3, budget / 1024 ** 3,
        )
        return want, policy, warns

    # 予算超過 → U 字の最小点を探す。理論最小は rg* = sqrt(meta * n_spots / (itemsize * n_mz))
    ideal = int((_RG_METADATA_BYTES * n_spots / (itemsize * n_mz)) ** 0.5) or 1
    candidates = {1, n_spots, ideal, max(1, ideal // 2), min(n_spots, ideal * 2), _RG_MIN_ROWS}
    candidates = {min(n_spots, max(1, c)) for c in candidates}
    best = min(candidates, key=lambda c: _row_group_cost_bytes(
        c, n_spots=n_spots, n_mz=n_mz, itemsize=itemsize))
    best_cost = _row_group_cost_bytes(best, n_spots=n_spots, n_mz=n_mz, itemsize=itemsize)

    if best_cost > budget:
        raise RuntimeError(
            f"メモリが不足しています（{n_spots:,} spot × {n_mz:,} m/z）。"
            f"row group をどう分割しても約 {best_cost / 1024 ** 3:.1f} GB 必要ですが、"
            f"変換に使える空きは約 {budget / 1024 ** 3:.1f} GB です。"
            "float32 保存を有効にするか、他の解析の完了を待つか、サーバのメモリを増やしてください。"
        )

    # 端数 row group を避けて等分割する（最後だけ極端に小さい group を作らない）
    n_groups = max(1, -(-n_spots // best))
    rg_rows = -(-n_spots // n_groups)
    warns.append(
        f"全行 1 row group には約 {want_cost / 1024 ** 3:.1f} GB 必要ですが空きが "
        f"約 {budget / 1024 ** 3:.1f} GB のため、{rg_rows:,} 行 × {n_groups} row group に"
        f"分割しました（想定 {best_cost / 1024 ** 3:.1f} GB）。"
        "出力内容は同一で、Parquet のフッタが少し大きくなるだけです。"
    )
    logger.warning("row group 計画: 予算超過のため %d 行 × %d group へ分割", rg_rows, n_groups)
    return rg_rows, policy + "-fallback", warns


def convert_scils_to_parquet(
    input_folder: str,
    output_path: str,
    *,
    spot_block: int = 200,
    row_group_rows: Optional[int] = None,
    store_float32: bool = True,
    organize: bool = True,
    annotation_tol: float = 1e-6,
    drop_uncovered: bool = False,
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
        Phase B で 1 回に一時 Parquet から読み込む spot 列数 (既定 200)。
        **読み取り粒度のみを決める**。出力の row group サイズとは無関係。
    row_group_rows : int | None
        出力 Parquet の 1 row group あたりの行数。
        None (既定) なら **全行を 1 row group** にする。正の整数ならその行数で分割する。
        いずれの場合も、メモリ予算に収まらなければ自動的に分割し `warnings` に記録する。
    store_float32 : bool
        Parquet の m/z 列を float32 で格納 (容量半減)
    organize : bool
        True なら出力フォルダを `<BASE>_Transform` サブフォルダに変更し、
        元 CSV を同サブフォルダに移動
    annotation_tol : float
        Annotation CSV の座標がマスターと一致するかの許容誤差
    drop_uncovered : bool
        True なら、座標 CSV に載っていない spot 列を除外して変換する。
        既定 False = 数が合わなければエラーで止める（黙って捨てない）。
        ただし**切片ごとの座標 CSV を統合したレイアウトでは常に有効**になる。
        一部の切片だけ座標を Export しない運用が通常なので、そこでの
        「座標が無い spot」は不整合ではなく前提のため。除外件数は必ず報告する。
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
    spot_likes: list[Path] = detected["spot_likes"]
    peaklist_path: Optional[Path] = detected.get("peak_list")

    # 1.5) マスター座標表を組み立てる。★ ver55.0: 「一番大きい座標 CSV = マスター」
    #      という決め打ちをやめ、spot 集合を見てレイアウトを判定する。切片ごとに
    #      Export したフォルダでは全ファイルを統合し、ファイル名を領域ラベルにする。
    spot_index, x_arr, y_arr, region_map, master_warnings = build_master_spot_table(spot_likes)
    result.warnings.extend(master_warnings)
    spots_path: Path = spot_likes[0]
    annotation_files: list[Path] = list(spot_likes) if region_map is not None else spot_likes[1:]

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
    if region_map is None:
        logger.info("Spot     : %s", spots_path)
    else:
        logger.info("Spot     : (座標 CSV %d 本を統合)", len(spot_likes))
    if annotation_files:
        logger.info("Annotation: %s", [p.name for p in annotation_files])
    if peaklist_path is not None:
        logger.info("Peak-list : %s", peaklist_path.name)
    logger.info("出力      : %s", out_path)

    # 3) Phase A: Intensity CSV → 一時 Parquet
    temp_parquet = intensity_path.parent / f"{base}_temp.parquet"
    int_headers, delim, skip = first_header_and_skipcount(intensity_path)
    # 大規模データのメモリ事前チェック（巨大／超ワイドな Intensity CSV による OOM で
    # 「途中終了＋0byte 一時ファイル残留」になるのを避け、不足時は明示エラーにする）。
    # Phase A は数分かかるので、出力バッファが明らかに載らない場合もここで落とす。
    _check_conversion_memory(
        intensity_path,
        n_spots_hint=max(0, len(int_headers) - 1),
        itemsize=4 if store_float32 else 8,
    )

    logger.info("Phase A 開始: CSV→一時 Parquet (%s)", temp_parquet.name)
    phase_a_start = time.perf_counter()
    _report(2, "CSV を読み込み中…（Phase A）")

    pf = None
    try:
        # Phase A も try 内で実行し、失敗時も finally で一時ファイルを確実に削除する
        _csv_to_temp_parquet(intensity_path, int_headers, delim, skip, temp_parquet)
        logger.info("Phase A 完了: %.1f 秒", time.perf_counter() - phase_a_start)

        # 4) m/z 列読込 + ソート
        pf = pq.ParquetFile(str(temp_parquet))
        # ★ ver51.9: m/z 列は **位置**で解決する。`int_headers[0]` で引くと、
        #   CSV 本体を読むエンジンが BOM を剥がなかったとき一時 Parquet の
        #   1 列目が `﻿m/z` のままで名前が一致せず、BOM 付き CSV の変換が丸ごと落ちる。
        #   m/z 列が先頭であることは SCiLS Intensity CSV の構造上の前提
        #   (`compute_spot_mapping` も int_headers[1:] を spot 列として扱う)。
        mz_col_name = pf.schema_arrow.names[0]
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
                pk_mz, pk_names, pk_skipped = _read_peaklist(
                    peaklist_path, return_skipped=True)
                # ★ ver52.3 (T5): 壊れ行は捨てるだけで、変換は「成功」と表示されていた。
                #   落ちた行の化合物名はサイドカーに載らないので、変換後の成果物からは
                #   復元できない（元 CSV を直して再登録するしかない）ため必ず報告する。
                pk_skip_msg = peaklist_skip_message(pk_skipped)
                if pk_skip_msg:
                    logger.warning("%s", pk_skip_msg)
                    result.warnings.append(pk_skip_msg)
                feat_ann_df = _pann.build_feature_annotation_table(
                    mz_sorted, pk_mz, pk_names, tol_da=_PEAKLIST_MZ_TOL_DA
                )
                # ★ ver51.8: 以前は `compound != ""` で数えていたが、
                #   `parse_scils_name` は DB 未ヒットでも compound に
                #   **"No DB hit" という文字列**を入れる（is_db_hit=False にするだけ）。
                #   そのため未同定ピークまで「付与済み」に数えられ、変換画面が
                #   「2,700 / 2,700 に付与」、注釈プレビューが「60 / 2,700」と
                #   矛盾した数字を出していた。プレビュー側と同じ判定に揃える。
                from app.services.annotation_inspect import _is_real_compound
                result.n_annotated = int(
                    feat_ann_df["compound"].map(_is_real_compound).sum())
                logger.info("Peak-list 注釈: %d / %d feature にマッチ", result.n_annotated, n_mz)
            except Exception as e:
                logger.warning("peak-list 解析に失敗（注釈なしで継続）: %s", e)
                result.warnings.append(f"peak-list の解析に失敗（注釈なしで継続）: {e}")
                feat_ann_df = None

        # 5) Spot マッピング (座標表は 1.5 で組み立て済み)
        # ★ ver55.1: 切片ごとの座標 CSV を統合したレイアウトでは、**欲しい切片の
        #   座標だけを Export する**のが通常の運用（例: 04 だけ座標を出さない）。
        #   この構成で「座標が無い spot がある」のは異常ではなく前提なので、
        #   既定で除外して変換を続ける（件数は必ず warnings に出す）。
        #   測定全体のマスター Spot CSV があるレイアウトでは、数が合わないことは
        #   本物の不整合なので従来どおり厳格に突き合わせる。
        effective_drop = drop_uncovered or (region_map is not None)
        sort_idx, x_sorted, y_sorted, spot_labels_sorted, spot_index_sorted, mapping_warnings = (
            compute_spot_mapping(
                int_headers, spot_index, x_arr, y_arr, drop_uncovered=effective_drop,
            )
        )
        result.warnings.extend(mapping_warnings)
        n_spots = len(spot_labels_sorted)

        # 6) Annotation 解決
        if region_map is not None:
            # ★ ver55.0: 切片ごとの座標 CSV を統合したレイアウト。座標表を作った
            #   時点で spot → 領域ラベルが確定しているので、部分集合であることを
            #   要求する build_annotation_map は通さない（全ファイルが領域なので
            #   「マスターの部分集合」という前提自体が成り立たない）。
            ann_map = dict(region_map)
            annotation_source = "csv"
        elif annotation_files:
            ann_map, ann_warnings = build_annotation_map(
                annotation_files, spot_index, x_arr, y_arr, tol=annotation_tol
            )
            result.warnings.extend(ann_warnings)
            annotation_source = "csv"
        else:
            # ★ ver55.0: ここは Spot ファイル名からラベルを導出して全 spot に割り当てて
            #   いた。領域アノテーションを一枚も渡していない利用者に対して、ファイル名が
            #   そのまま領域ラベルとして出るため、**Annotation CSV: (なし) と
            #   annotation ラベル: <ファイル名> が同時に表示される**という矛盾を生む。
            #   区別が付かないので「無い」ことを 'Unannotated' で明示する。
            #   列そのものは残す — R が slice_id → condition の組み立てに使っており
            #   (ver6.R の DEG グループ列)、消すと解析側が壊れる。
            logger.info("Annotation ファイルなし → 全 spot に 'Unannotated' を割当")
            ann_map = {int(si): "Unannotated" for si in spot_index}
            annotation_source = "none"
        annotation_sorted = [ann_map[int(si)] for si in spot_index_sorted]
        result.annotation_labels = sorted(set(annotation_sorted))

        # 7) Intensity ヘッダ整合チェック (全 spot 列が一時 Parquet に存在するか)
        # ★ ver55.0: `set(int_headers)` と突き合わせていたが spot_labels_sorted は
        #   int_headers[1:] 由来なので missing_cols は**常に空**＝恒真だった。
        #   実際に読む相手 (一時 Parquet) のスキーマと突き合わせる。
        header_set = set(pf.schema_arrow.names)
        missing_cols = [c for c in spot_labels_sorted if c not in header_set]
        if missing_cols:
            raise ValueError(
                f"Intensity ヘッダから一部 spot 列が欠落: {missing_cols[:10]}"
            )

        # 8) Phase B: 一時 Parquet から chunk 読み → 最終 Parquet 書き込み
        logger.info("Phase B 開始: chunk 読込 + 最終 Parquet 書き込み")
        phase_b_start = time.perf_counter()

        # ★ ver55.0: 化合物名を**列名に焼き込むのをやめた**。列名は常に m/z の数値。
        #   焼き込みは不可逆で (下の 'column_names' コメント参照)、しかも列名は
        #   「注釈」ではなく feature の識別子として R の rowname → deg$gene →
        #   画面・CSV・PPTX・PNG ファイル名まで伝播するため、後から表示を切ることも
        #   できなかった。化合物名は同じ変換の最後に生成されるサイドカー
        #   `<BASE>_feature_annotations.parquet` が持ち、そちらは可逆に付け外しできる
        #   (「分子情報を後から登録」と同じ仕組み)。
        mz_colnames = [f"{v:.6f}" for v in mz_sorted]
        # スキーマにメタデータを直接付与（ParquetWriter のスキーマに含めることで
        # 全バッチ＝ファイルへ確実に永続化される）。mz_sorted はフル桁の m/z 一覧で、
        # 列名が丸められていても m/z を確実に復元できる正となる。
        schema_md = {
            b"mz_sorted": ",".join(f"{v:.10g}" for v in mz_sorted).encode("utf-8"),
            b"annotation_files": ";".join(p.name for p in annotation_files).encode("utf-8"),
            # ★ ver55.0: 領域アノテーションの由来。'csv' = 実ファイルから解決、
            #   'none' = 一枚も無く全 spot が 'Unannotated'。読み手 (data_manager /
            #   file_handlers) が「領域の選択肢を出してよいか」を判断できるようにする。
            b"annotation_source": annotation_source.encode("utf-8"),
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

        rg_rows, rg_policy, rg_warns = _plan_row_groups(
            n_spots=n_spots,
            n_mz=n_mz,
            itemsize=np.dtype(intensity_dtype).itemsize,
            requested=row_group_rows,
        )
        result.warnings.extend(rg_warns)
        rg_rows = max(1, min(rg_rows, n_spots))

        # 出力バッファ: 形状 (n_mz, rg_rows) の C 連続配列。
        #   buf[j, :k] は連続ビューになるため pa.array がゼロコピーで包む（= 列 1 本ぶん）。
        #   軸を逆順 (rg_rows, n_mz) にすると buf[:, j] が非連続になり pyarrow が黙って
        #   コピーし、ピークメモリが 2 倍になる。**軸順は絶対に変えないこと。**
        buf = np.empty((n_mz, rg_rows), dtype=intensity_dtype)

        # 失敗時に壊れた出力を残さないよう、いったん同じフォルダの一時パスへ書いてから
        # 検証して os.replace で差し替える。ParquetWriter は close() 時に「完了した
        # row group だけで有効なフッタ」を書いてしまうため、直接 out_path に書くと
        # 途中終了で「有効だが行数が足りない parquet」が既存の正常ファイルを上書きする。
        tmp_out = _unique_path(out_path.parent / f"{out_path.stem}.writing{out_path.suffix}")
        try:
            with pq.ParquetWriter(str(tmp_out), schema, compression="zstd") as pq_writer:
                for rg_start in range(0, n_spots, rg_rows):
                    rg_end = min(n_spots, rg_start + rg_rows)
                    n_this = rg_end - rg_start

                    # --- 読み取り: spot_block 単位でバッファを埋める ---
                    for start in range(rg_start, rg_end, spot_block):
                        end = min(rg_end, start + spot_block)
                        spot_cols_block = spot_labels_sorted[start:end]
                        table_block = pf.read(columns=spot_cols_block)
                        vals = np.column_stack([
                            table_block.column(c).to_numpy(zero_copy_only=False)
                            for c in spot_cols_block
                        ])
                        if vals.shape[0] != n_mz:
                            raise RuntimeError(f"行数不一致: 期待 {n_mz}, 実際 {vals.shape[0]}")
                        # m/z 並べ替えと目的 dtype へのキャストを「代入 1 回」で同時に行う。
                        # numpy の代入キャストは casting='unsafe'（= astype の既定）と同一
                        # 挙動なので、オーバーフローが inf になる点まで従来と一致する。
                        buf[:, start - rg_start:end - rg_start] = vals[order_mz, :]
                        del vals, table_block
                        _report(15 + int(78 * end / n_spots),
                                f"読込中… {end:,}/{n_spots:,} spot")

                    # --- 書き込み: この row group を 1 回で書く ---
                    # 全行 1 row group だとここが数十秒止まるので、別ラベルで通知する。
                    _report(15 + int(78 * rg_end / n_spots),
                            f"row group 書き込み中… {rg_end:,}/{n_spots:,} spot")
                    arrays = [
                        pa.array(np.arange(rg_start + 1, rg_end + 1, dtype=np.int64)),
                        pa.array(x_sorted[rg_start:rg_end].astype(np.float64, copy=False)),
                        pa.array(y_sorted[rg_start:rg_end].astype(np.float64, copy=False)),
                    ]
                    arrays.extend(pa.array(buf[j, :n_this]) for j in range(n_mz))
                    arrays.append(
                        pa.array(annotation_sorted[rg_start:rg_end], type=pa.string()))
                    table = pa.Table.from_arrays(arrays, schema=schema)
                    # row_group_size は必ず明示する。None 既定は 1,048,576 行で無言分割する。
                    pq_writer.write_table(table, row_group_size=n_this)
                    del arrays, table

            # 書き切れたことを行数で検証してから差し替える
            written = pq.ParquetFile(str(tmp_out)).metadata
            if written.num_rows != n_spots:
                raise RuntimeError(
                    f"書き込み行数が一致しません（期待 {n_spots:,} / 実際 {written.num_rows:,}）。"
                    "出力は破棄しました。"
                )
            result.row_group_rows = rg_rows
            result.n_row_groups = written.num_row_groups
            result.footer_bytes = written.serialized_size
            result.row_group_policy = rg_policy
            del written
            os.replace(str(tmp_out), str(out_path))
        finally:
            # 例外時にトレースバックが巨大バッファを掴んだままにしない
            buf = None
            if tmp_out.exists():
                try:
                    tmp_out.unlink()
                except Exception as e:
                    logger.warning("書き込み中の一時ファイル削除に失敗: %s", e)

        logger.info(
            "Parquet レイアウト: %d row group × %d 行 / フッタ %.2f MB (%s)",
            result.n_row_groups, result.row_group_rows,
            result.footer_bytes / (1024 ** 2), result.row_group_policy,
        )
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
        result.annotation_source = annotation_source
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
