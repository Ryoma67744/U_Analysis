# =============================================================================
# DEG (Differentially Expressed Genes) Utility Functions
# interactive_callbacks.py から抽出した DEG 関連ユーティリティ
# =============================================================================

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("msi.deg_utils")


# ---------------------------------------------------------------------------
# 小さなヘルパー関数
# ---------------------------------------------------------------------------

def is_meaningful_annotation(ann: str, gene: str = "") -> bool:
    """annotation値が表示価値のある化合物名かどうかを判定。
    数値のみのアノテーション(例: "240.984")や空文字は除外する。"""
    if not ann or not isinstance(ann, str):
        return False
    ann = ann.strip()
    if not ann:
        return False
    # 数値のみ（小数点含む）は無意味なアノテーション
    if re.match(r'^[\d.]+$', ann):
        return False
    # geneと同一テキストも除外
    if ann == gene:
        return False
    return True


# feature 名から m/z を取り出す規則 (ver51.8)
# ---------------------------------------------------------------------------
# ★ 従来は「文字列中の最初の数字」を m/z としていた。annotated な feature 名は
#   `<化合物名>_<m/z> | <DB> | <アダクト>` 形式（peak_annotation.make_column_name が
#   作り、scils_converter が **列名として** 使い、R がそれを Seurat の rowname に
#   採用する）なので、化合物名に数字があると化合物名側を拾ってしまう:
#       "PI 38:4 (PI 18:0/20:4)_760.5851" -> 38.0
#       "2-Hydroxybutyric acid_105.0546 | HMDB | M+H" -> 2.0
#   同梱 DB (App/DB/TIMS/4500_endogenous_metabolites_mod.csv) は 4,546 化合物中
#   2,409 件 (53%) が名前に数字を含む。
#
#   これは calibration の窓判定だけでなく、サイドカーとの突き合わせ
#   (seurat_bridge._load_feature_annotations) も壊しており、**化合物名アノテーションを
#   持つデータセットに限って化合物名表示が丸ごと死ぬ** 状態になっていた。
#
# ★ R 側は同じバグを `.feature_mz()` で既に直している (CHANGELOG ver46 系)。
#   ここはその規則を Python へ揃えるもの。正しい実装は Python にも
#   data_manager._mz_from_embedded_name として既にあったが、生入力 parquet に
#   しか使われていなかった。
#
# ★ 認識できない形式は inf を返す (「m/z が無い」を意味する)。
#   従来の「最初の数字」だと DESI の 1 行ヘッダ形式 ("Vitamin B12" など純粋な
#   化合物名) が 12.0 になり、**m/z 12 の calibration 窓に紛れ込む**。
#   意味のない数値より「m/z 無し」の方が安全で、呼び出し側は既に inf を
#   扱える (seurat_bridge.py の `mz == float("inf"): continue` など)。
_MZ_TRAILING_RE = re.compile(r"_(\d+(?:\.\d+)?)\s*$")
_MZ_PREFIX_RE = re.compile(r"^m/z\s+(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)
_MZ_BARE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*$")
# DESI の MRM トランジション名 "Q1-Q3" (例 "146.1-102.0")。
# R が `paste(pre_masses, post_masses, sep="-")` で作り
# (DESI テンプレート v16)、そのまま Seurat の rowname になる。
# **プリカーサ (Q1) が m/z** なので先頭側を採る。
_MZ_MRM_RE = re.compile(r"^(\d+(?:\.\d+)?)-\d+(?:\.\d+)?\s*$")
# R の `make.unique()` が重複名に付ける ".1" ".2" … のサフィックス。
_MAKE_UNIQUE_SUFFIX_RE = re.compile(r"\.\d+$")


def _parse_mz_head(head: str):
    """パイプより前の部分から m/z を取り出す。取れなければ None。"""
    # 1. 末尾の _<数値>。annotated 名 (`<化合物名>_<m/z>`) と `mz_<m/z>` の両方。
    m = _MZ_TRAILING_RE.search(head)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    # 2. "m/z 760.58510" (R の非 annotated 経路)
    m = _MZ_PREFIX_RE.match(head)
    if m:
        return float(m.group(1))
    # 3. "419.257200" (素の数値列名)
    m = _MZ_BARE_RE.match(head)
    if m:
        return float(m.group(1))
    # 4. "146.1-102.0" (DESI の MRM トランジション)
    m = _MZ_MRM_RE.match(head)
    if m:
        return float(m.group(1))
    return None


def extract_mz_numeric(f: str) -> float:
    """フィーチャー名から m/z 値を抽出する。認識できなければ float("inf")。

    対応する形式:
      - `<化合物名>_<m/z> | <残り>` / `mz_<m/z>` … `|` より前の **末尾** の `_<数値>`
      - `m/z <m/z>`                              … R の非 annotated 経路
      - `<m/z>`                                   … 素の数値列名
      - `<Q1>-<Q3>`                               … DESI の MRM トランジション → Q1
      - 上記に R の `make.unique()` が付ける `.1` `.2` サフィックスが付いた形
    """
    s = str(f).strip()
    head = s.split("|", 1)[0].strip()

    v = _parse_mz_head(head)
    if v is not None:
        return v

    # ★ 重複名に make.unique が付ける ".N" を落として再試行する。
    #   例: "m/z 419.25720.1" / "Glucose_180.0634.1" / "146.1-102.0.1"。
    #   先に素のまま試しているので、"419.2572" のような **本物の小数**を
    #   誤って削ることはない (そちらは上の bare で既に拾えている)。
    stripped = _MAKE_UNIQUE_SUFFIX_RE.sub("", head)
    if stripped != head:
        v = _parse_mz_head(stripped)
        if v is not None:
            return v

    return float("inf")


def backfill_annotations(deg_data, annotation_map):
    """deg_data の空/無意味な annotation を annotation_map(feat→compound) から補完する。

    DEG 系の表示面（Volcano/Heatmap/クラスタTop5/マーカー表/PPTX）は deg レコードの
    ``annotation`` を読むが、これは R が markers_annotated.csv を出した場合のみ埋まる
    （TIMS: あり／DESI: なし）。一方 annotation_map は SCiLS サイドカーや CSV 由来の
    化合物名を含むスーパーセットなので、ここで空欄だけ補完すると上記が一括で点灯する。

    - 既存の「意味ある」annotation は上書きしない（R 出力を正とする）。
    - deg_data / annotation_map が空なら no-op。
    - レコードを in-place 更新し、同じ deg_data を返す。キーは gene(=feature 文字列)で直接一致。
    """
    if not deg_data or not annotation_map:
        return deg_data
    for r in deg_data:
        if not isinstance(r, dict):
            continue
        g = str(r.get("gene", ""))
        if not g:
            continue
        cur = r.get("annotation", "")
        if isinstance(cur, str) and is_meaningful_annotation(cur, g):
            continue
        cand = annotation_map.get(g)
        if isinstance(cand, str) and is_meaningful_annotation(cand, g):
            r["annotation"] = cand.strip()
    return deg_data


# ---------------------------------------------------------------------------
# DEG DataFrame 標準化・読み込み
# ---------------------------------------------------------------------------

def standardize_deg_df(df: pd.DataFrame) -> list[dict] | None:
    """DEG DataFrame の列名を標準化し、dict のリストとして返す。
    CSV / RDS 両方の読み込みから共通で使用する。"""
    try:
        # 列名を標準化
        col_map = {}
        for col in df.columns:
            cl = col.lower().strip()
            if cl in ("gene", "row.names", "x", "...1"):
                col_map[col] = "gene"
            elif "cluster" in cl:
                col_map[col] = "cluster"
            elif "avg_log2fc" in cl or "avg_logfc" in cl:
                col_map[col] = "avg_log2FC"
            elif "p_val_adj" in cl:
                col_map[col] = "p_val_adj"
            elif cl == "pct.1":
                col_map[col] = "pct.1"
            elif cl == "pct.2":
                col_map[col] = "pct.2"

        df = df.rename(columns=col_map)
        # gene列がない場合、最初の列をgeneとする
        if "gene" not in df.columns and len(df.columns) > 0:
            df = df.rename(columns={df.columns[0]: "gene"})

        # 必要な列のみ抽出
        # FUTURE(annot-provenance): 将来「由来表示」を足す場合、この keep に "source" を
        #   加え、app/services/annotation_sources.build_feature_source_map() の結果（feature→由来）
        #   を gene/m/z で突き合わせて source 列を埋める想定。取込設計が未確定のため現状は変更なし。
        keep = [c for c in ["gene", "cluster", "avg_log2FC", "p_val_adj",
                             "pct.1", "pct.2", "annotation"] if c in df.columns]
        df = df[keep]

        # 数値列を丸める
        for col in ["avg_log2FC", "p_val_adj", "pct.1", "pct.2"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
                if col == "p_val_adj":
                    # Volcano Plot用に元の数値を保持
                    df["p_val_adj_raw"] = df[col].copy()
                    # テーブル表示用に科学記数法文字列へ変換
                    df[col] = df[col].map(
                        lambda x: f"{x:.2e}" if pd.notna(x) else ""
                    )
                else:
                    df[col] = df[col].round(4)

        return df.to_dict("records")
    except Exception as e:
        logger.error(f"standardize_deg_df エラー: {e}", exc_info=True)
        return None


def _r_escape_path(p: Path) -> str:
    """R 文字列リテラル内に埋め込むための簡易エスケープ。

    バックスラッシュとダブルクォートをエスケープし、`f'"...{escaped}..."'`
    のように埋め込んでも R パーサーが破損しないようにする。
    内部生成のパスでも防衛的に通すための関数。
    """
    s = p.as_posix()
    return s.replace("\\", "\\\\").replace('"', '\\"')


def read_deg_rds(rds_path: Path) -> list[dict] | None:
    """TIMS ver13 が出力する deg_FindAllMarkers_raw_*.rds を読み込む。
    R subprocess で RDS -> 一時CSV -> pandas DataFrame に変換。"""
    # mkstemp は TOCTOU 安全（mktemp と異なり原子的にファイルを作成）
    fd, tmp_csv_str = tempfile.mkstemp(suffix=".csv")
    os.close(fd)  # subprocess 側で書き直すため fd は即 close
    tmp_csv = Path(tmp_csv_str)
    try:
        rds_escaped = _r_escape_path(rds_path)
        tmp_csv_escaped = _r_escape_path(tmp_csv)
        r_cmd = (
            f'deg <- readRDS("{rds_escaped}");\n'
            f'write.csv(deg, "{tmp_csv_escaped}", row.names=TRUE)'
        )
        result = subprocess.run(
            ["Rscript", "-e", r_cmd],
            capture_output=True, timeout=30,
        )
        if not tmp_csv.exists():
            return None
        df = pd.read_csv(tmp_csv)
        return standardize_deg_df(df)
    except Exception as e:
        logger.error(f"read_deg_rds error: {e}")
        return None
    finally:
        tmp_csv.unlink(missing_ok=True)


def _write_deg_index(
    result_base: Path,
    method: str,
    file_path: Path,
    file_type: str,
) -> None:
    """発見した DEG ファイル情報を deg_index.json にマージ書き込み。

    結果フォルダ直下に deg_index.json を作成 / 更新し、次回ロード時に
    glob 22 パターンを走査せず直接ファイルを開けるようにする。
    書き込み失敗（read-only フォルダ等）時は silent skip。

    ★ ver51.8: **別の統合手法のフォルダにあるファイルは絶対に記録しない**。
      以前は要求した手法に DEG が無いと別手法のファイルへフォールバックし、
      その対応をここに書き込んでいた。一度書かれると高速パスが先に読むので
      間違いが固着し、再起動しても消えなかった。探索側でも弾いているが、
      「間違いをディスクに焼き付ける」のは被害が桁違いなので二重に守る。
    """
    _other = {"harmony", "rpca", "pca", "pca_uncorrected"} - {str(method).lower()}
    try:
        _parts = [p.lower() for p in Path(file_path).relative_to(result_base).parts[:-1]]
    except ValueError:
        _parts = [p.lower() for p in Path(file_path).parts[:-1]]
    if any(p in _other for p in _parts):
        logger.warning(
            "deg_index.json への記録を拒否: method=%s に対し別手法のファイル %s",
            method, file_path)
        return

    meta_path = result_base / "deg_index.json"
    try:
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if not isinstance(meta, dict):
                    meta = {}
            except Exception:
                meta = {}
        else:
            meta = {}

        meta.setdefault("version", 1)
        meta["generated_at"] = datetime.now().isoformat()
        meta["generated_by"] = "umap-webapp"
        meta.setdefault("deg_results", {})
        try:
            rel = str(file_path.relative_to(result_base))
        except ValueError:
            rel = str(file_path)
        meta["deg_results"][method] = {"type": file_type, "path": rel}

        # 原子的書き込み（temp → rename）
        tmp = meta_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(meta_path)
    except Exception as e:
        # read-only フォルダ等の書き込み失敗は無視（fallback 動作に影響なし）
        logger.debug(f"deg_index.json 書き込みスキップ: {e}")


def load_deg_results(
    result_base: Path,
    integration_method: str | None = None,
    *,
    cache: dict | None = None,
) -> list[dict] | None:
    """解析結果フォルダ内の DEG CSV / RDS を読み込む（キャッシュ付き）。

    Parameters
    ----------
    result_base : Path
        解析結果のベースフォルダ。
    integration_method : str | None
        統合手法名 ("Harmony", "RPCA", "PCA", "PCA (uncorrected)")。
    cache : dict | None
        キャッシュ用 dict。呼び出し元が管理する辞書を渡す。
        ``deg_cache_key`` / ``deg_cache_data`` キーを使用する。
        None の場合はキャッシュを使用しない。
    """
    # キャッシュチェック
    cache_key = (str(result_base), integration_method)
    if cache is not None:
        if (cache.get("deg_cache_key") == cache_key
                and cache.get("deg_cache_data") is not None):
            logger.debug(f"キャッシュヒット: {cache_key}")
            return cache["deg_cache_data"]

    # 選択した統合手法のフォルダを優先検索
    # 手法名 -> 出力サブフォルダ名（R: run_downstream_analysis の prefix）。
    # "PCA (uncorrected)" は R 側 prefix "pca_uncorrected" のフォルダに対応（ver4）。
    _METHOD_DIR_MAP = {
        "Harmony": "Harmony",
        "RPCA": "RPCA",
        "PCA": "PCA",
        "PCA (uncorrected)": "pca_uncorrected",
    }
    # ★ ver51.9: 大文字小文字を無視して引く。
    #   呼び出し側は表記が揃っていない:
    #     - gpt_api.py は URL クエリをそのまま渡す (`?method=rpca` は小文字)
    #     - interactive_calibration.py は `integration_method or ""` を渡す
    #   ver51.8 で「別手法フォルダを候補から外す」ようにしたため、表記が一致しないと
    #   `method_dir="Harmony"` に落ちたうえで RPCA フォルダが除外され、
    #   **RPCA しか無いプロジェクトで DEG が見つからなくなっていた**
    #   (従来は無条件パターンで拾えていた = 別の意味で間違っていた)。
    _norm = {k.lower(): v for k, v in _METHOD_DIR_MAP.items()}
    method_dir = _norm.get(str(integration_method or "").strip().lower())

    # 手法を特定できない場合。「黙って Harmony 扱い」にすると別手法の表を返しうるので
    # しない。まず直下だけを見て、それでも無ければ **一致する手法フォルダが 1 つに
    # 定まるときだけ** 採用する (複数あるなら曖昧なので返さない)。
    method_unknown = method_dir is None
    if method_unknown:
        method_dir = ""      # 直下パターンだけが有効になる
        logger.info(
            "統合手法を特定できません (%r)。結果フォルダ直下を探し、"
            "手法フォルダは一意に定まるときだけ採用します。", integration_method)

    def _cache_and_return(data):
        """DEG結果をキャッシュして返す（meta.json 経路 / glob fallback 共通）"""
        if cache is not None:
            cache["deg_cache_key"] = cache_key
            cache["deg_cache_data"] = data
        return data

    # NEW: deg_index.json があれば優先使用（glob 22 パターンを 1 ファイル読込に削減）
    meta_path = result_base / "deg_index.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("version") == 1:
                entry = meta.get("deg_results", {}).get(method_dir)
                if entry and entry.get("path"):
                    abs_path = result_base / entry["path"]
                    if abs_path.exists():
                        if entry.get("type") == "csv":
                            try:
                                df = pd.read_csv(abs_path, encoding="utf-8")
                                result = standardize_deg_df(df)
                                if result:
                                    logger.info(f"deg_index.json 経路 (CSV) ヒット: {abs_path}")
                                    return _cache_and_return(result)
                            except Exception as e:
                                logger.warning(f"deg_index.json CSV 読込失敗、glob fallback: {e}")
                        elif entry.get("type") == "rds":
                            try:
                                result = read_deg_rds(abs_path)
                                if result:
                                    logger.info(f"deg_index.json 経路 (RDS) ヒット: {abs_path}")
                                    return _cache_and_return(result)
                            except Exception as e:
                                logger.warning(f"deg_index.json RDS 読込失敗、glob fallback: {e}")
        except Exception as e:
            logger.warning(f"deg_index.json パース失敗、glob fallback: {e}")

    # 既知の統合手法フォルダ名。要求された手法**以外**のフォルダにあるファイルは
    # 採用してはいけない (ver51.8)。
    _ALL_METHOD_DIRS = {"harmony", "rpca", "pca", "pca_uncorrected"}
    _other_method_dirs = _ALL_METHOD_DIRS - {method_dir.lower()}

    def _is_other_method(path) -> bool:
        """path が **別の統合手法** のフォルダの中にあるか。"""
        try:
            parts = [p.lower() for p in Path(path).relative_to(result_base).parts[:-1]]
        except ValueError:
            parts = [p.lower() for p in Path(path).parts[:-1]]
        return any(p in _other_method_dirs for p in parts)

    # --- 1. CSV ファイル検索 ---
    # ★ ver51.8: 以前はここに `Harmony/*` `RPCA/*` `PCA/*` が **無条件で** 並んでいた。
    #   要求した手法の DEG ファイルが無いと別手法の結果へ黙ってフォールバックし、
    #   しかも `_write_deg_index` でその対応を **ディスクに記録** していたため、
    #   間違いが固着して再起動しても消えなかった (RPCA を要求 → Harmony の表)。
    #   手法を比較しているつもりで同じ表を見ることになるので、パターンを削除した。
    #   直下 (手法フォルダを作らない単一手法の出力) は従来どおり許容する。
    _CSV_NAMES = [
        "*deg*markers*.csv",
        "*top*markers*.csv",
        "markers_annotated*.csv",
        "markers_mz_only*.csv",
    ]
    # 手法が特定できているときだけ、その手法のフォルダを優先して見る。
    # 特定できないときは直下だけ (method_dir == "")。
    csv_patterns = ([f"{method_dir}/{n}" for n in _CSV_NAMES] if method_dir else [])
    csv_patterns += _CSV_NAMES

    def _try_load_csv(matches):
        """マッチしたCSVファイルの読み込みを試行。成功時は (result, csv_path) を返す。"""
        for csv_path in matches:
            try:
                df = pd.read_csv(csv_path, encoding="utf-8")
                logger.info(
                    f"CSV発見: {csv_path} (列: {list(df.columns)}, 行数: {len(df)})"
                )
                result = standardize_deg_df(df)
                if result:
                    logger.info(f"読み込み成功: {len(result)} レコード")
                    return result, csv_path
                else:
                    logger.warning(
                        f"standardize_deg_df が None を返しました: {csv_path}"
                    )
            except Exception as e:
                logger.error(f"CSV読み込みエラー: {csv_path} -- {e}")
        return None, None

    # 第1段階: result_base 直下を glob で検索
    for pattern in csv_patterns:
        matches = sorted(result_base.glob(pattern))
        if matches:
            result, found = _try_load_csv(matches)
            if result:
                _write_deg_index(result_base, method_dir, found, "csv")
                return _cache_and_return(result)

    # 第2段階: result_base 以下を rglob で再帰検索
    # （TIMS ver13 は日付サブフォルダ内に出力するため）
    rglob_csv_names = [
        "markers_annotated*.csv",
        "markers_mz_only*.csv",
        "*deg*markers*.csv",
        "*top*markers*.csv",
    ]
    for name_pattern in rglob_csv_names:
        matches = sorted(result_base.rglob(name_pattern))
        # ★ ver51.8: 別手法のフォルダにあるものは候補から外す。
        #   従来は `prioritized + 残り全部` だったので、要求した手法に無ければ
        #   結局よその手法のファイルを掴んでいた。
        matches = [m for m in matches if not _is_other_method(m)]
        if matches:
            # 選択した統合手法のフォルダ内を優先
            method_lower = method_dir.lower()
            prioritized = [m for m in matches if method_lower in m.parent.name.lower()]
            ordered = prioritized + [m for m in matches if m not in prioritized]
            result, found = _try_load_csv(ordered)
            if result:
                _write_deg_index(result_base, method_dir, found, "csv")
                return _cache_and_return(result)

    # --- 2. DEG RDS ファイル検索（TIMS ver13 が出力する deg_FindAllMarkers_raw_*.rds） ---
    rds_patterns = ([f"{method_dir}/deg_FindAllMarkers_raw_*.rds"] if method_dir else [])
    rds_patterns += [
        "RDS_Files/deg_FindAllMarkers_raw_*.rds",
        "deg_FindAllMarkers_raw_*.rds",
    ]
    for pattern in rds_patterns:
        matches = sorted(result_base.glob(pattern))
        # ver51.8: CSV 段と同じく別手法フォルダのものは採らない
        matches = [m for m in matches if not _is_other_method(m)]
        if matches:
            try:
                logger.info(f"RDS発見: {matches[0]}")
                result = read_deg_rds(matches[0])
                if result:
                    logger.info(f"RDS読み込み成功: {len(result)} レコード")
                    _write_deg_index(result_base, method_dir, matches[0], "rds")
                    return _cache_and_return(result)
                else:
                    logger.warning(
                        f"read_deg_rds が None を返しました: {matches[0]}"
                    )
            except Exception as e:
                logger.error(f"RDS読み込みエラー: {matches[0]} -- {e}")
                continue

    # 第2段階(RDS): rglob でサブフォルダも再帰検索
    # ★ ver51.9: ここだけ _is_other_method の適用が漏れていた。CSV の rglob 段と
    #   RDS の glob 段は ver51.8 で塞いだのに最後のこの経路が素通りで、
    #   要求した手法に DEG が無いと **今も別手法の表を返していた**
    #   (deg_index.json への記録は拒否されるので固着はしないが、返る値は間違い)。
    rds_rglob_matches = [
        m for m in sorted(result_base.rglob("deg_FindAllMarkers_raw_*.rds"))
        if not _is_other_method(m)
    ]
    if rds_rglob_matches:
        try:
            logger.info(f"RDS発見(rglob): {rds_rglob_matches[0]}")
            result = read_deg_rds(rds_rglob_matches[0])
            if result:
                _write_deg_index(result_base, method_dir, rds_rglob_matches[0], "rds")
                logger.info(f"RDS読み込み成功: {len(result)} レコード")
                return _cache_and_return(result)
        except Exception as e:
            logger.error(
                f"RDS読み込みエラー(rglob): {rds_rglob_matches[0]} -- {e}"
            )

    # ★ ver51.9: 手法を特定できなかった場合の最後の手段。
    #   ここまでは直下しか見ていないので、手法フォルダに DEG があっても見つからない。
    #   ただし「黙って先頭の手法」を採ると ver51.8 で潰した不具合に逆戻りするので、
    #   **候補が 1 つの手法フォルダに定まるときだけ**採用する。複数あるなら
    #   どれが正しいか決められないので返さない (曖昧なら選ばない)。
    if method_unknown:
        by_dir: dict = {}
        for name_pattern in rglob_csv_names:
            for m in sorted(result_base.rglob(name_pattern)):
                try:
                    parts = [p.lower() for p in m.relative_to(result_base).parts[:-1]]
                except ValueError:
                    continue
                for p in parts:
                    if p in _ALL_METHOD_DIRS:
                        by_dir.setdefault(p, []).append(m)
                        break
        if len(by_dir) == 1:
            only_dir, matches = next(iter(by_dir.items()))
            result, found = _try_load_csv(matches)
            if result:
                logger.info(
                    "統合手法は不明でしたが、DEG を持つ手法フォルダが %s の 1 つに"
                    "定まったので採用します: %s", only_dir, found)
                # ★ 手法名を特定できていないので deg_index.json には記録しない
                #   (間違った対応をディスクに焼き付けない)
                return _cache_and_return(result)
        elif len(by_dir) > 1:
            logger.warning(
                "統合手法が不明で、DEG を持つ手法フォルダが複数あります (%s)。"
                "どれを返すべきか決められないため返しません。", sorted(by_dir))

    logger.info(
        f"result_base={result_base}, method_dir={method_dir} -- DEGファイル見つからず"
    )
    return None


# ---------------------------------------------------------------------------
# クラスタ別 Top N Feature 取得
# ---------------------------------------------------------------------------

def get_top_n_features_for_cluster(
    deg_data: list[dict], cluster, n: int = 5, return_dropped: bool = False
):
    """指定クラスタの DEG データから Top N up/down regulated feature を取得。

    Args:
        return_dropped: True なら `avg_log2FC` を数値化できず除外した件数も返す
            (ver52.3)。既定 False は従来どおりの 2-tuple なので既存の
            呼び出しを壊さない。

    Returns:
        (up_features, down_features) — `return_dropped=True` なら
        (up_features, down_features, dropped_count)
    """
    # ★ 早期 return も `return_dropped` の戻り値の形に従うこと。
    #   ver52.3 で 3-tuple を足したとき、ここを直し忘れて
    #   「not enough values to unpack」で落ちた（テストが即座に捕まえた）。
    _empty = ([], [], 0) if return_dropped else ([], [])
    if not deg_data:
        return _empty
    cluster_records = [
        r for r in deg_data
        if str(r.get("cluster", "")) == str(cluster)
    ]
    if not cluster_records:
        return _empty

    def _sort_key(r):
        p = r.get("p_val_adj_raw")
        if p is None:
            p = r.get("p_val_adj", 1.0)
        try:
            p = float(p)
            if np.isnan(p):
                p = 1.0
        except (ValueError, TypeError):
            p = 1.0
        fc = r.get("avg_log2FC", 0)
        try:
            fc = float(fc)
            if np.isnan(fc):
                fc = 0.0
        except (ValueError, TypeError):
            fc = 0.0
        return (p, -abs(fc))

    def _extract_top_n(records, n_items):
        sorted_recs = sorted(records, key=_sort_key)
        seen = set()
        result = []
        for r in sorted_recs:
            g = str(r.get("gene", ""))
            if g and g not in seen:
                seen.add(g)
                result.append(g)
                if len(result) >= n_items:
                    break
        return result

    # ★ ver52.3: `avg_log2FC` が読めない record は `fc = 0.0` になり、
    #   `> 0` にも `< 0` にも入らないため **Up / Down の両方の Top-N から消える**。
    #   件数の報告も無いので、切り詰められた一覧が「上位マーカーの全部」として
    #   表・スライドに出ていた。落とした件数を数えて呼び出し側へ渡せるようにする。
    up_records = []
    down_records = []
    dropped = 0
    for r in cluster_records:
        raw = r.get("avg_log2FC", 0)
        try:
            fc = float(raw if raw not in (None, "") else 0)
        except (ValueError, TypeError):
            dropped += 1
            continue
        if fc != fc:                      # NaN も「読めなかった」扱いにする
            dropped += 1
            continue
        if fc > 0:
            up_records.append(r)
        elif fc < 0:
            down_records.append(r)
        # fc == 0.0 は「変動なし」という**正当な測定値**なので落とさない。
        # Up でも Down でもないだけで、読めなかったわけではない。

    if dropped:
        logger.warning(
            "クラスタ %s: avg_log2FC を数値化できない record を %d 件除外した",
            cluster, dropped)

    up, down = _extract_top_n(up_records, n), _extract_top_n(down_records, n)
    if return_dropped:
        return up, down, dropped
    return up, down


def build_marker_rows(clusters, deg_data, top_n: int = 5,
                      mz_to_compound: dict | None = None,
                      cluster_name_map: dict | None = None):
    """DEG 非選択時の marker 集約表の (headers, rows) を返す（描画非依存の純ロジック）。

    列: クラスタ / m/z / 化合物名 / 方向(▲Up/▼Down) / log2FC / 調整p値。
    化合物名は annotation（意味あり）→ mz_to_compound の近傍一致(±0.1)→ 空欄 の順。
    """
    headers = ["クラスタ", "m/z", "化合物名", "方向", "log2FC", "調整p値"]
    deg_data = deg_data or []
    name_map = cluster_name_map or {}
    mz_to_compound = mz_to_compound or {}

    gene_ann, rec_by = {}, {}
    for r in deg_data:
        g = str(r.get("gene", ""))
        a = r.get("annotation", "")
        if g and is_meaningful_annotation(a, g):
            gene_ann[g] = a
        rec_by[(str(r.get("cluster", "")), g)] = r

    def _mz(feat):
        try:
            v = extract_mz_numeric(feat)
            if v is None or v != v or v == float("inf"):
                return None
            return float(v)
        except Exception:
            return None

    def _compound(gene):
        if gene in gene_ann:
            return gene_ann[gene]
        mz = _mz(gene)
        if mz is not None and mz_to_compound:
            best, bestd = "", 0.1
            for k, nm in mz_to_compound.items():
                try:
                    d = abs(float(k) - mz)
                except (ValueError, TypeError):
                    continue
                if d <= bestd:
                    bestd, best = d, nm
            return best
        return ""

    def _fmt(v):
        try:
            return f"{float(v):.3g}"
        except (ValueError, TypeError):
            return "" if v in (None, "") else str(v)

    rows = []
    dropped_total = 0
    for cl in clusters:
        cl_str = str(cl)
        cl_label = str(name_map.get(cl_str, cl_str))
        up_f, down_f, dropped = get_top_n_features_for_cluster(
            deg_data, cl_str, n=top_n, return_dropped=True)
        dropped_total += dropped
        for direction, feats in (("▲Up", up_f), ("▼Down", down_f)):
            for feat in feats:
                rec = rec_by.get((cl_str, str(feat)), {})
                mz = _mz(feat)
                mz_s = f"{mz:.4f}" if mz is not None else str(feat)
                rows.append([cl_label, mz_s, _compound(feat), direction,
                             _fmt(rec.get("avg_log2FC", "")),
                             str(rec.get("p_val_adj", ""))])

    # ★ ver52.3: 読めなかった record を黙って消さない。
    #   `avg_log2FC` が数値化できない record は Up にも Down にも入らないため、
    #   従来は**両方の Top-N から消え、件数の報告も無かった**。
    #   表そのものが利用者に届く成果物なので、注記行として同じ表に載せる
    #   （リポジトリ内の正解例: interactive_data_export の "Skipped" シート、
    #    interactive_pptx の skipped_methods → 最終ステータス文）。
    if dropped_total:
        rows.append(["—", "—",
                     f"※ avg_log2FC を読み取れず除外した記録 {dropped_total} 件",
                     "—", "—", "—"])
    return headers, rows
