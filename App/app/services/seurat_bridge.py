# =============================================================================
# MSI Analysis Application - Seurat Bridge
# Seurat RDS → Parquet/CSV 変換管理
# =============================================================================

import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from app.config import R_HELPERS_DIR, RSCRIPT_PATH, SEURAT_CACHE_DIR

logger = logging.getLogger("msi.seurat_bridge")


class ExtractionCancelled(Exception):
    """ユーザーが RDS 抽出をキャンセルしたときに送出される。"""


def _none_str(v):
    """NaN / None / 空 / "None" を None に正規化、それ以外は str を返す。"""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return None if (s == "" or s.lower() == "none") else s


def _popen_with_cancel(cmd, cancel_event, timeout=600, creationflags=0):
    """cmd を Popen で起動し、0.3 秒ごとに cancel_event を監視する。

    cancel_event がセットされたらサブプロセスを kill し ExtractionCancelled を
    送出する。timeout 超過時は RuntimeError("__TIMEOUT__") を送出。
    Returns: (returncode, stderr_bytes)
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    start = time.monotonic()
    while True:
        try:
            _out, stderr_bytes = proc.communicate(timeout=0.3)
            return proc.returncode, stderr_bytes
        except subprocess.TimeoutExpired:
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                raise ExtractionCancelled()
            if time.monotonic() - start > timeout:
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                raise RuntimeError("__TIMEOUT__")

# Seurat キャッシュの LRU 上限。新規エントリ追加時に超過分は古い順に削除。
# /tmp/msi_seurat_cache が無制限に膨らむのを防ぐ。
SEURAT_CACHE_MAX_ENTRIES = int(os.environ.get("SEURAT_CACHE_MAX_ENTRIES", 12))


def _evict_seurat_cache_lru(cache_base: Path, max_entries: int) -> int:
    """SEURAT_CACHE_DIR 内のサブディレクトリを mtime 降順で max_entries 件まで残し、
    それより古い (mtime 小) サブディレクトリを物理削除する。

    Returns: 削除したサブディレクトリ数
    """
    try:
        if not cache_base.exists():
            return 0
        sub_dirs = []
        for child in cache_base.iterdir():
            if child.is_dir():
                try:
                    mt = child.stat().st_mtime
                except OSError:
                    mt = 0
                sub_dirs.append((mt, child))
        if len(sub_dirs) <= max_entries:
            return 0
        # mtime 昇順 (古いものから) で並べる
        sub_dirs.sort(key=lambda t: t[0])
        to_evict = sub_dirs[: len(sub_dirs) - max_entries]
        removed = 0
        for mt, path in to_evict:
            try:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
                logger.info("Evicted stale Seurat cache: %s (age=%.0fs)",
                            path.name, time.time() - mt)
            except Exception as e:
                logger.warning("Failed to evict %s: %s", path.name, e)
        return removed
    except Exception as e:
        logger.warning("LRU eviction error: %s", e)
        return 0


class SeuratBridge:
    """Seurat RDS ファイルを R ヘルパースクリプト経由で
    Parquet/CSV に変換し、pandas で読み込む。
    """

    def __init__(self):
        self._cache_base = SEURAT_CACHE_DIR

    def _get_cache_key(self, rds_path: str) -> str:
        """RDSファイルパス + 更新日時 + Rスクリプト更新日時からキャッシュキーを生成"""
        p = Path(rds_path)
        mtime = p.stat().st_mtime if p.exists() else 0
        # Rスクリプト更新時にもキャッシュを再生成するため、スクリプトのmtimeも含める
        r_script = R_HELPERS_DIR / "extract_seurat_data.R"
        r_mtime = r_script.stat().st_mtime if r_script.exists() else 0
        raw = f"{rds_path}|{mtime}|{r_mtime}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    def _get_cache_dir(self, rds_path: str) -> Path:
        key = self._get_cache_key(rds_path)
        cache_dir = self._cache_base / key
        is_new = not cache_dir.exists()
        cache_dir.mkdir(parents=True, exist_ok=True)
        # 新規キャッシュ作成時に LRU evict をトリガー (頻繁すぎず適度)
        if is_new and SEURAT_CACHE_MAX_ENTRIES > 0:
            try:
                _evict_seurat_cache_lru(self._cache_base, SEURAT_CACHE_MAX_ENTRIES)
            except Exception as e:
                logger.debug("LRU eviction failed (non-critical): %s", e)
        return cache_dir

    def get_cache_dir(self, rds_path: str) -> Path:
        """外部からキャッシュディレクトリを参照（Parquet直接読み込み用）"""
        return self._get_cache_dir(rds_path)

    def _is_cached(self, cache_dir: Path) -> bool:
        """キャッシュ済みかチェック（必須ファイルが全て揃っているか）"""
        required = ["extraction_meta.json", "cluster_stats.csv"]
        # plot_data は parquet or csv のどちらか
        has_plot = (cache_dir / "plot_data.parquet").exists() or (cache_dir / "plot_data.csv").exists()
        return has_plot and all((cache_dir / f).exists() for f in required)

    def extract_data(self, rds_path: str, with_expression: bool = False,
                     cancel_event=None) -> dict:
        """Seurat RDS からデータを抽出。キャッシュがあればそれを使用。

        Args:
            rds_path: RDSファイルパス
            with_expression: True なら expression_matrix.parquet も生成
                （dense 100k×18k で 14GB 級になり 20-60 秒追加。
                初回データロードでは省略推奨、Feature plot/m/z キャリブで必要時のみ True）

        Returns:
            {
                "plot_data": pd.DataFrame,
                "cluster_stats": pd.DataFrame,
                "features_list": list[str],
                "meta": dict,
                "cache_dir": Path,
            }
        """
        from app.utils.file_locks import get_or_create_lock
        cache_dir = self._get_cache_dir(rds_path)

        # ver4.4: 同一 RDS への同時初回アクセス (受信者オープン + 共有生成時の
        # プリウォーム等) で R 抽出が二重に走らないよう排他。ロック取得後に
        # 再チェックし、先行プロセスが既に抽出済みならスキップする。
        if not self._is_cached(cache_dir):
            lock = get_or_create_lock(cache_dir / "extract", timeout=600)
            with lock:
                if not self._is_cached(cache_dir):
                    self._run_extraction(rds_path, cache_dir, with_expression=with_expression,
                                         cancel_event=cancel_event)

        try:
            result = self._load_extracted_data(cache_dir)
        except Exception:
            # キャッシュ破損の可能性 → 削除して再抽出
            shutil.rmtree(cache_dir, ignore_errors=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._run_extraction(rds_path, cache_dir, with_expression=with_expression,
                                 cancel_event=cancel_event)
            result = self._load_extracted_data(cache_dir)

        result["cache_dir"] = cache_dir
        result["feature_annotations"] = self._load_feature_annotations(
            cache_dir, rds_path, result.get("features_list") or []
        )
        return result

    def ensure_expression_matrix(self, rds_path: str) -> Path:
        """expression_matrix.parquet を必要時に生成して Path を返す。

        既に存在する（過去セッションで生成済み or 明示的に with_expression=True で
        生成済み）場合は即座に返す。不在の場合は R 抽出を再実行して生成する。

        Feature plot や m/z キャリブレーション callback の頭で呼び出すと、
        初回の重いコストを「ユーザーがその機能を使ったとき」に限定できる。

        複数ユーザーが同一 RDS の Feature plot を同時に初めて開いても、
        FileLock により R 抽出は 1 回のみ実行され、後発プロセスは生成完了を待つ。
        """
        from app.utils.file_locks import get_or_create_lock
        cache_dir = self._get_cache_dir(rds_path)
        parquet_path = cache_dir / "expression_matrix.parquet"
        if parquet_path.exists():
            return parquet_path
        # 不在 → 排他取得して生成（R 抽出最大 10 分 → timeout=900）
        lock = get_or_create_lock(parquet_path, timeout=900)
        with lock:
            # ロック取得後に再チェック（先行プロセスが既に生成完了している可能性）
            if parquet_path.exists():
                return parquet_path
            self._run_extraction(rds_path, cache_dir, with_expression=True)
        if not parquet_path.exists():
            raise RuntimeError(
                f"expression_matrix.parquet の生成に失敗しました: {parquet_path}"
            )
        return parquet_path

    def get_feature_expression(
        self, rds_path: str, feature_name: str
    ) -> pd.Series:
        """単一 Feature の発現量を取得（R subprocess fallback）"""
        cache_dir = self._get_cache_dir(rds_path)
        feature_file = cache_dir / f"feature_{feature_name}.csv"

        if not feature_file.exists():
            self._run_feature_extraction(rds_path, feature_name, feature_file)

        df = pd.read_csv(feature_file, header=None)
        return df.iloc[:, 0]

    def get_feature_expression_fast(
        self, cache_dir: Path, feature_name: str
    ) -> Optional[pd.Series]:
        """Parquet 発現量マトリクスから単一 Feature を高速取得。

        expression_matrix.parquet が存在する場合、指定カラムのみ読み込む。
        存在しない場合は None を返す（呼び出し元で R fallback を使用）。
        """
        expr_path = cache_dir / "expression_matrix.parquet"
        if not expr_path.exists():
            return None

        try:
            df = pd.read_parquet(expr_path, columns=[feature_name])
            return df[feature_name]
        except (KeyError, Exception):
            return None

    def _run_extraction(self, rds_path: str, output_dir: Path,
                        with_expression: bool = False, cancel_event=None):
        """R ヘルパースクリプトで Seurat データを抽出

        with_expression=True で expression_matrix.parquet も生成（重い処理）。
        """
        script = R_HELPERS_DIR / "extract_seurat_data.R"
        rscript = str(RSCRIPT_PATH)
        if not Path(rscript).exists():
            rscript = "Rscript"

        cmd = [
            rscript, "--vanilla",
            str(script), rds_path, str(output_dir),
        ]
        if with_expression:
            cmd.append("--with-expression")
        # ver3.7: subprocess.run は timeout 時に内部で kill するため zombie の
        # 心配は無いが、TimeoutExpired を捕まえてユーザー向けエラーに整形
        if cancel_event is None:
            # 通常パス（キャンセル不要）: 既存どおり subprocess.run
            try:
                result = subprocess.run(
                    cmd, capture_output=True, timeout=600,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as e:
                if output_dir.exists():
                    shutil.rmtree(output_dir, ignore_errors=True)
                raise RuntimeError(
                    f"Seurat extraction timed out (10min): rds={rds_path}"
                ) from e
            returncode, stderr_bytes = result.returncode, result.stderr
        else:
            # キャンセル可能パス: Popen + cancel_event 監視。kill 時は部分キャッシュを掃除。
            try:
                returncode, stderr_bytes = _popen_with_cancel(
                    cmd, cancel_event, timeout=600,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except ExtractionCancelled:
                if output_dir.exists():
                    shutil.rmtree(output_dir, ignore_errors=True)
                raise
            except RuntimeError as e:
                if str(e) == "__TIMEOUT__":
                    if output_dir.exists():
                        shutil.rmtree(output_dir, ignore_errors=True)
                    raise RuntimeError(
                        f"Seurat extraction timed out (10min): rds={rds_path}"
                    ) from e
                raise
        if returncode != 0:
            # 不完全なキャッシュファイルを削除
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            raise RuntimeError(
                f"Seurat extraction failed:\n{stderr_text[:2000]}"
            )

    def _run_feature_extraction(
        self, rds_path: str, feature_name: str, output_path: Path
    ):
        """R ヘルパースクリプトで単一 Feature を抽出"""
        script = R_HELPERS_DIR / "extract_features.R"
        rscript = str(RSCRIPT_PATH)
        if not Path(rscript).exists():
            rscript = "Rscript"

        cmd = [
            rscript, "--vanilla",
            str(script), rds_path, feature_name, str(output_path),
        ]
        # ver3.7: TimeoutExpired を捕まえユーザー向けエラーに整形
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"Feature extraction timed out (5min): "
                f"feature={feature_name}"
            ) from e
        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            raise RuntimeError(
                f"Feature extraction failed:\n{stderr_text[:2000]}"
            )

    def _load_extracted_data(self, cache_dir: Path) -> dict:
        """キャッシュディレクトリからデータを読み込み"""
        # plot_data: Parquet優先、CSV fallback
        plot_parquet = cache_dir / "plot_data.parquet"
        plot_csv = cache_dir / "plot_data.csv"
        if plot_parquet.exists():
            plot_data = pd.read_parquet(plot_parquet)
        elif plot_csv.exists():
            plot_data = pd.read_csv(plot_csv)
        else:
            raise FileNotFoundError(f"plot_data が見つかりません: {cache_dir}")

        # cluster_stats
        cs_path = cache_dir / "cluster_stats.csv"
        if not cs_path.exists():
            raise FileNotFoundError(f"cluster_stats.csv が見つかりません: {cache_dir}")
        cluster_stats = pd.read_csv(cs_path)

        # features_list（任意 — なくても空リストで続行）
        features_file = cache_dir / "features_list.txt"
        features_list = []
        if features_file.exists():
            features_list = features_file.read_text(encoding="utf-8").strip().splitlines()

        # meta
        meta_file = cache_dir / "extraction_meta.json"
        if not meta_file.exists():
            raise FileNotFoundError(f"extraction_meta.json が見つかりません: {cache_dir}")
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)

        return {
            "plot_data": plot_data,
            "cluster_stats": cluster_stats,
            "features_list": features_list,
            "meta": meta,
        }

    # --- 外部アノテーション（SCiLS peak Name 由来）のサイドカー結合 (Q2) ---
    def _find_feature_annotation_sidecar(self, rds_path) -> Optional[Path]:
        """rds_path 近傍から `*_feature_annotations.parquet` を探す。"""
        p = Path(rds_path).resolve()
        bases = [p.parent, p.parent.parent, p.parent.parent.parent]
        seen = set()
        for base in bases:
            if base is None or str(base) in seen or not base.is_dir():
                continue
            seen.add(str(base))
            hits = sorted(base.glob("*_feature_annotations.parquet"))
            if hits:
                return hits[0]
            sub_hits = sorted(base.glob("*/*_feature_annotations.parquet"))
            if sub_hits:
                return sub_hits[0]
        return None

    def _load_feature_annotations(self, cache_dir: Path, rds_path,
                                  features_list: list) -> dict:
        """サイドカーを features_list に数値 m/z で join し {feature_str: record} を返す。

        キャッシュ済み（cache_dir/feature_annotations.json）があれば再利用。
        サイドカー無し / 候補なし feature はキーに含めない（= m/z 表示のまま）。
        """
        cache_file = cache_dir / "feature_annotations.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        if not features_list:
            return {}
        sidecar = self._find_feature_annotation_sidecar(rds_path)
        if sidecar is None:
            return {}
        try:
            from app.utils.deg_utils import _extract_mz_numeric
            side = pd.read_parquet(sidecar)
            side_mz = side["mz"].to_numpy(dtype=float)
            out: dict = {}
            tol = 0.005
            for feat in features_list:
                mz = _extract_mz_numeric(feat)
                if mz is None or mz == float("inf"):
                    continue
                j = int(np.argmin(np.abs(side_mz - mz)))
                if abs(side_mz[j] - mz) > tol:
                    continue
                row = side.iloc[j]
                comp = _none_str(row.get("compound"))
                if not comp:
                    continue  # No DB hit 等は m/z 表示のまま
                out[feat] = {
                    "display_name": _none_str(row.get("display_name")) or comp,
                    "compound": comp,
                    "lipid_class": _none_str(row.get("lipid_class")),
                    "database": _none_str(row.get("database")),
                    "adduct": _none_str(row.get("adduct")),
                    "ppm": (float(row["ppm"]) if pd.notna(row.get("ppm")) else None),
                    "formula": _none_str(row.get("formula")),
                    "smiles": _none_str(row.get("smiles")),
                    "adduct_image": _none_str(row.get("adduct_image")),
                    "adduct_family": _none_str(row.get("adduct_family")),
                    "mz": float(row["mz"]),
                }
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False)
            except Exception:
                pass
            return out
        except Exception as e:
            logger.warning("feature annotation の join に失敗: %s", e)
            return {}
