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


def extract_mz_numeric(f: str) -> float:
    """フィーチャー名から数値部分(m/z値)を抽出してソート用floatを返す"""
    match = re.search(r"(\d+\.?\d*)", f)
    return float(match.group(1)) if match else float("inf")


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
    """
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
    if integration_method and integration_method in _METHOD_DIR_MAP:
        method_dir = _METHOD_DIR_MAP[integration_method]
    else:
        method_dir = "Harmony"

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

    # --- 1. CSV ファイル検索 ---
    csv_patterns = [
        f"{method_dir}/*deg*markers*.csv",
        f"{method_dir}/*top*markers*.csv",
        f"{method_dir}/markers_annotated*.csv",
        f"{method_dir}/markers_mz_only*.csv",
        "Harmony/*deg*markers*.csv",
        "Harmony/*top*markers*.csv",
        "RPCA/*deg*markers*.csv",
        "RPCA/*top*markers*.csv",
        "PCA/*deg*markers*.csv",
        "PCA/*top*markers*.csv",
        "*deg*markers*.csv",
        "*top*markers*.csv",
        "markers_annotated*.csv",
        "markers_mz_only*.csv",
    ]

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
    rds_patterns = [
        f"{method_dir}/deg_FindAllMarkers_raw_*.rds",
        "RDS_Files/deg_FindAllMarkers_raw_*.rds",
        "deg_FindAllMarkers_raw_*.rds",
    ]
    for pattern in rds_patterns:
        matches = sorted(result_base.glob(pattern))
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
    rds_rglob_matches = sorted(result_base.rglob("deg_FindAllMarkers_raw_*.rds"))
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

    logger.info(
        f"result_base={result_base}, method_dir={method_dir} -- DEGファイル見つからず"
    )
    return None


# ---------------------------------------------------------------------------
# クラスタ別 Top N Feature 取得
# ---------------------------------------------------------------------------

def get_top_n_features_for_cluster(
    deg_data: list[dict], cluster, n: int = 5
) -> tuple[list[str], list[str]]:
    """指定クラスタの DEG データから Top N up/down regulated feature を取得。

    Returns:
        (up_features: list[str], down_features: list[str])
    """
    if not deg_data:
        return [], []
    cluster_records = [
        r for r in deg_data
        if str(r.get("cluster", "")) == str(cluster)
    ]
    if not cluster_records:
        return [], []

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

    up_records = []
    down_records = []
    for r in cluster_records:
        try:
            fc = float(r.get("avg_log2FC", 0) or 0)
        except (ValueError, TypeError):
            fc = 0.0
        if fc > 0:
            up_records.append(r)
        elif fc < 0:
            down_records.append(r)

    return _extract_top_n(up_records, n), _extract_top_n(down_records, n)


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
    for cl in clusters:
        cl_str = str(cl)
        cl_label = str(name_map.get(cl_str, cl_str))
        up_f, down_f = get_top_n_features_for_cluster(deg_data, cl_str, n=top_n)
        for direction, feats in (("▲Up", up_f), ("▼Down", down_f)):
            for feat in feats:
                rec = rec_by.get((cl_str, str(feat)), {})
                mz = _mz(feat)
                mz_s = f"{mz:.4f}" if mz is not None else str(feat)
                rows.append([cl_label, mz_s, _compound(feat), direction,
                             _fmt(rec.get("avg_log2FC", "")),
                             str(rec.get("p_val_adj", ""))])
    return headers, rows
