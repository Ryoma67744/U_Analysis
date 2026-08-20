#!/usr/bin/env python3
# =============================================================================
# MSI Analysis Application - データ出力「UMAP cluster 列が空欄」の原因切り分け
#
# 「データ出力 (UMAP cluster)」で出したファイルのクラスタ列が空欄のとき、
# どこで突合が外れているのかをアプリの外から判定する。
#
# なぜ必要か:
#   エクスポートはクラスタ値を「完全一致の辞書引き」で埋めている。
#     キー = (サンプル名, round(x,4), round(y,4))
#     辞書側 = plot_data の Sample / SpatialX / SpatialY / Cluster
#     引く側 = TIMS は parquet の annotation 列、DESI はファイル名 stem
#   1 件も当たらなくても services/export_transform.py の fillna("") が
#   全部を空文字に潰すため、例外もログも出ず「✅ 生成しました」で終わる。
#   つまり画面上は成功と区別が付かず、出力を開くまで気づけない。
#   しかも原因は「クラスタに属さない spot」と見分けが付かない。
#
#   本スクリプトは突合の両側を実際に読み出し、_match_sample_name と同じ
#   判定を掛けて「どのファイルのどの値が当たらないのか」を名指しする。
#
# 使い方（コンテナを再ビルドせずに実行できる）:
#   docker compose exec -T msi-app python3 - < App/tools/diag_umap_cluster_export.py
#
#   イメージに取り込み済みなら直接でもよい:
#   docker exec msi-analysis-app python3 /app/App/tools/diag_umap_cluster_export.py
#
#   参照先はコンテナの既定パス。環境変数 SEURAT_CACHE_DIR / TIMS_DATA_DIR /
#   DESI_DATA_DIR が設定されていればそちらを優先する（compose と同じ変数）。
#
# 読み取り専用。ファイルは一切書き換えない。
#
# 終了コード:
#   0 = 突合が外れている箇所は見つからなかった
#   3 = 全行が空欄になる箇所を検出した（★★ の行）
#   4 = 一部だけ空欄になる箇所を検出した（★ の行）
#   1 = 診断自体の失敗
# =============================================================================

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pyarrow.parquet as pq

CACHE_DIR = Path(os.environ.get("SEURAT_CACHE_DIR", "/app/Data/Other/seurat_cache"))
TIMS_DIR = Path(os.environ.get("TIMS_DATA_DIR", "/app/Data/TIMS/Data"))
DESI_DIR = Path(os.environ.get("DESI_DATA_DIR", "/app/Data/DESI/Data"))

# 注釈サイドカーは「サンプル」ではない（data_manager._filter_tims_candidates と同じ除外）。
SIDECAR = "_feature_annotations.parquet"
# 解析の出力物は生データではない。結果フォルダは生データフォルダ配下に作られる
# 運用があり、混ざると診断が読めなくなるので走査から外す。
SKIP_DIRS = {"RDS_Files", "log", "logs", "provenance", "__pycache__"}
SKIP_FILES = {"plot_data.parquet", "expression_matrix.parquet",
              "feature_annotations.parquet", "cluster_stats.csv"}
MAXSHOW = 8

# 検出した重症度。3 = 全行空欄 / 4 = 一部空欄 / 0 = 異常なし。
worst = 0


def _mark(code: int) -> None:
    global worst
    # 3(全行空欄) を 4(一部空欄) より重く扱う。
    if code == 3 or (code == 4 and worst != 3):
        worst = code


# --- callbacks/interactive_data_export.py の _match_sample_name と同一ロジック ---
# ver51.8 で「曖昧なら None」に厳格化された版をそのまま写している。
# ここを本体と食い違わせると診断が嘘をつくので、本体を直すときは必ず両方直す。
def _safe_prefix(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "_", name)


def match_sample_name(file_stem: str, sample_names: list[str]):
    """(一致したサンプル名 | None, 判定理由) を返す。"""
    if file_stem in sample_names:
        return file_stem, "完全一致"
    safe = _safe_prefix(file_stem)
    hits = [s for s in sample_names if _safe_prefix(s) == safe]
    if len(hits) == 1:
        return hits[0], "safe_prefix 一致"
    if len(hits) > 1:
        return None, f"safe_prefix 一致が曖昧 {hits[:3]}"
    partial = [s for s in sample_names if file_stem in s or s in file_stem]
    if len(partial) == 1:
        return partial[0], "部分一致"
    if len(partial) > 1:
        return None, f"部分一致が曖昧 {partial[:3]}"
    return None, "候補なし"


def show(vals) -> str:
    v = sorted(vals)
    return f"{v[:MAXSHOW]}{' …' if len(v) > MAXSHOW else ''}  (計 {len(v)} 種)"


def uniques(path: Path, col: str):
    """parquet の 1 列だけ読んでユニーク値を返す（列が無ければ None）。"""
    f = pq.ParquetFile(str(path))
    if col not in f.schema_arrow.names:
        return None
    return {str(x) for x in f.read(columns=[col]).column(0).to_pylist()}


def _usable(p: Path) -> bool:
    if not p.is_file() or p.name in SKIP_FILES or p.name.endswith(SIDECAR):
        return False
    return not (SKIP_DIRS & set(p.parts))


def data_files(root: Path) -> dict:
    """フォルダごとの入力ファイル一覧。

    data_manager._filter_tims_candidates と同じ優先度
    （parquet が 1 本でもあれば parquet のみ、無ければ CSV/TSV/TXT）。
    """
    out: dict = {}
    if not root.is_dir():
        return out
    exts = (".parquet", ".pq", ".csv", ".tsv", ".txt")
    for folder in {p.parent for p in root.rglob("*")
                   if p.suffix.lower() in exts and _usable(p)}:
        here = [p for p in folder.iterdir() if _usable(p)]
        pqs = sorted(p for p in here if p.suffix.lower() in (".parquet", ".pq"))
        out[folder] = pqs or sorted(
            p for p in here if p.suffix.lower() in (".csv", ".tsv", ".txt"))
    return out


def is_data_line(ln: str) -> bool:
    """DESI .txt のデータ開始行判定（services/desi_header.py と同基準）。"""
    sp = ln.rstrip("\r\n").split("\t")
    if len(sp) < 3:
        return False
    a, b, c = sp[0].strip(), sp[1].strip(), sp[2].strip()
    if not a or "." in a:      # PixelID は整数の想定
        return False
    try:
        int(a), float(b), float(c)
    except ValueError:
        return False
    return True


# --- 1. 突合先（解析結果側） ----------------------------------------------
print("=" * 78)
print("【1】解析結果 (plot_data) のサンプル名 — エクスポートの突合先")
print("=" * 78)

plot_samples: dict = {}
if not CACHE_DIR.is_dir():
    print(f"  抽出キャッシュがありません: {CACHE_DIR}")
    print("  アプリで対象プロジェクトを一度開いてから実行してください。")
for pd_path in sorted(CACHE_DIR.glob("*/plot_data.parquet")):
    key = pd_path.parent.name
    cols = pq.ParquetFile(str(pd_path)).schema_arrow.names
    samples = uniques(pd_path, "Sample") or set()
    plot_samples[key] = samples
    print(f"\n  [{key}]")
    print(f"    Sample   : {show(samples)}")
    print(f"    Cluster  : {show(uniques(pd_path, 'Cluster') or set())}")
    if "SpatialX" in cols and "SpatialY" in cols:
        print("    SpatialX/Y 列: あり")
    else:
        print("    SpatialX/Y 列: ★★ 無し（座標が無いのでこの手法は全行空欄）")
        _mark(3)

all_samples = sorted(set().union(*plot_samples.values())) if plot_samples else []

# --- 2. TIMS（突合キー = annotation 列） ----------------------------------
print()
print("=" * 78)
print("【2】TIMS 生データ — 突合キーは annotation 列")
print("=" * 78)

tims = data_files(TIMS_DIR)
if not tims:
    print(f"  TIMS データが見つかりません: {TIMS_DIR}")
for folder, files in sorted(tims.items()):
    print(f"\n  [{folder}]")
    for p in files:
        if p.suffix.lower() not in (".parquet", ".pq"):
            print(f"    {p.name}")
            print("      ★★ parquet ではない: _read_tims_file は pd.read_csv で読むため"
                  " x/y 列が無く、全行空欄になります")
            _mark(3)
            continue
        f = pq.ParquetFile(str(p))
        cols = f.schema_arrow.names
        md = f.schema_arrow.metadata or {}
        raw_src = md.get(b"annotation_source")
        src = raw_src.decode() if raw_src else "(記録なし)"
        print(f"    {p.name}")
        if "x" in cols and "y" in cols:
            print("      x/y 列        : あり")
        else:
            print("      x/y 列        : ★★ 無し（キーを作れず全行空欄）")
            _mark(3)
        print(f"      annotation_source: {src}")
        ann = uniques(p, "annotation")
        if ann is None:
            print("      annotation 列 : 無し（この場合はファイル名 stem で突合される）")
            m, why = match_sample_name(p.stem, all_samples) if all_samples else (None, "-")
            print(f"      stem 突合     : {m!r} ({why})")
            if all_samples and m is None:
                _mark(3)
            continue
        print(f"      annotation    : {show(ann)}")
        if not all_samples:
            continue
        bad = [(a, why) for a in sorted(ann)
               for m, why in [match_sample_name(a, all_samples)] if m is None]
        if not bad:
            print("      → 突合 OK")
        elif len(bad) == len(ann):
            print("      → ★★ 全 annotation が突合失敗 = このファイルの行は全部空欄")
            for a, why in bad[:3]:
                print(f"           '{a}' → {why}")
            _mark(3)
        else:
            print(f"      → ★ 一部が突合失敗: {[a for a, _ in bad[:5]]}")
            _mark(4)

# --- 3. DESI（突合キー = ファイル名 stem） --------------------------------
print()
print("=" * 78)
print("【3】DESI 生データ — 突合キーはファイル名 stem / ヘッダ行数")
print("=" * 78)

desi = data_files(DESI_DIR)
if not desi:
    print(f"  DESI データが見つかりません: {DESI_DIR}")
for folder, files in sorted(desi.items()):
    txts = [p for p in files if p.suffix.lower() == ".txt"]
    if not txts:
        continue
    print(f"\n  [{folder}]")
    for p in txts:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            probe = [next(fh, "") for _ in range(12)]
        hit = [i for i, ln in enumerate(probe) if is_data_line(ln)]
        n_header = hit[0] if hit else None
        print(f"    {p.name}")
        if n_header == 5:
            print("      ヘッダ行数    : 5")
        else:
            # _export_desi は rows[5:] を決め打ちしている（ver55.2 の自動判定漏れ）。
            print(f"      ヘッダ行数    : {n_header}  ★ エクスポータは 5 行決め打ちのため"
                  f" 先頭 {5 - n_header if n_header else '?'} 画素が空欄になります")
            _mark(4)
        if all_samples:
            m, why = match_sample_name(p.stem, all_samples)
            print(f"      サンプル名突合: {m!r} ({why})")
            if m is None:
                print("      → ★★ このシートは全行空欄")
                _mark(3)

# --- 4. まとめ -------------------------------------------------------------
print()
print("=" * 78)
print("判定")
print("=" * 78)
if worst == 3:
    print("  ★★ 全行が空欄になる原因を検出しました。よくある形:")
    print("    - TIMS の annotation が ['Unannotated'] だけ")
    print("        → 変換時に領域アノテーション CSV を渡していない (ver55.0 の回帰)")
    print("    - TIMS の annotation が plot_data の Sample と食い違う")
    print("        → extract_seurat_data.R の Sample 列選択が同数のとき"
          "ファイル名側を採るため")
    print("    - DESI の突合が None（候補が曖昧）")
    print("        → 「ROI 列があれば各 ROI を別サンプルとして解析」ON のとき")
elif worst == 4:
    print("  ★ 一部の行だけ空欄になる原因を検出しました（上記 ★ の行）。")
else:
    print("  突合が外れている箇所は見つかりませんでした。")
    print("  それでも空欄なら、出力対象の手法・結果フォルダが"
          "意図したものか確認してください。")

sys.exit(worst)
