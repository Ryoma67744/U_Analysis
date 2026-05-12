# =============================================================================
# MSI Analysis Application - Seurat Bridge
# Seurat RDS → Parquet/CSV 変換管理
# =============================================================================

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import pandas as pd

from app.config import R_HELPERS_DIR, RSCRIPT_PATH, SEURAT_CACHE_DIR


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
        cache_dir.mkdir(parents=True, exist_ok=True)
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

    def extract_data(self, rds_path: str, with_expression: bool = False) -> dict:
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
        cache_dir = self._get_cache_dir(rds_path)

        if not self._is_cached(cache_dir):
            self._run_extraction(rds_path, cache_dir, with_expression=with_expression)

        try:
            result = self._load_extracted_data(cache_dir)
        except Exception:
            # キャッシュ破損の可能性 → 削除して再抽出
            shutil.rmtree(cache_dir, ignore_errors=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._run_extraction(rds_path, cache_dir, with_expression=with_expression)
            result = self._load_extracted_data(cache_dir)

        result["cache_dir"] = cache_dir
        return result

    def ensure_expression_matrix(self, rds_path: str) -> Path:
        """expression_matrix.parquet を必要時に生成して Path を返す。

        既に存在する（過去セッションで生成済み or 明示的に with_expression=True で
        生成済み）場合は即座に返す。不在の場合は R 抽出を再実行して生成する。

        Feature plot や m/z キャリブレーション callback の頭で呼び出すと、
        初回の重いコストを「ユーザーがその機能を使ったとき」に限定できる。
        """
        cache_dir = self._get_cache_dir(rds_path)
        parquet_path = cache_dir / "expression_matrix.parquet"
        if parquet_path.exists():
            return parquet_path
        # 不在 → R 抽出を with_expression=True で再実行
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
                        with_expression: bool = False):
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
        result = subprocess.run(
            cmd, capture_output=True, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            # 不完全なキャッシュファイルを削除
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
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
        result = subprocess.run(
            cmd, capture_output=True, timeout=300,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
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
