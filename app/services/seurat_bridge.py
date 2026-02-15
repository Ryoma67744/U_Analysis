# =============================================================================
# MSI Analysis Application - Seurat Bridge
# Seurat RDS → Parquet/CSV 変換管理
# =============================================================================

import hashlib
import json
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
        """RDSファイルパス + 更新日時からキャッシュキーを生成"""
        p = Path(rds_path)
        mtime = p.stat().st_mtime if p.exists() else 0
        raw = f"{rds_path}|{mtime}"
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
        """キャッシュ済みかチェック"""
        return (cache_dir / "extraction_meta.json").exists()

    def extract_data(self, rds_path: str) -> dict:
        """Seurat RDS からデータを抽出。キャッシュがあればそれを使用。

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
            self._run_extraction(rds_path, cache_dir)

        result = self._load_extracted_data(cache_dir)
        result["cache_dir"] = cache_dir
        return result

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

    def _run_extraction(self, rds_path: str, output_dir: Path):
        """R ヘルパースクリプトで Seurat データを抽出"""
        script = R_HELPERS_DIR / "extract_seurat_data.R"
        rscript = str(RSCRIPT_PATH)
        if not Path(rscript).exists():
            rscript = "Rscript"

        cmd = [
            rscript, "--vanilla",
            str(script), rds_path, str(output_dir),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Seurat extraction failed:\n{result.stderr[:2000]}"
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
            cmd, capture_output=True, text=True, timeout=300,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Feature extraction failed:\n{result.stderr[:2000]}"
            )

    def _load_extracted_data(self, cache_dir: Path) -> dict:
        """キャッシュディレクトリからデータを読み込み"""
        # plot_data: Parquet優先、CSV fallback
        plot_data_path = cache_dir / "plot_data.parquet"
        if plot_data_path.exists():
            plot_data = pd.read_parquet(plot_data_path)
        else:
            plot_data = pd.read_csv(cache_dir / "plot_data.csv")

        # cluster_stats
        cluster_stats = pd.read_csv(cache_dir / "cluster_stats.csv")

        # features_list
        features_file = cache_dir / "features_list.txt"
        features_list = []
        if features_file.exists():
            features_list = features_file.read_text(encoding="utf-8").strip().splitlines()

        # meta
        meta_file = cache_dir / "extraction_meta.json"
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)

        return {
            "plot_data": plot_data,
            "cluster_stats": cluster_stats,
            "features_list": features_list,
            "meta": meta,
        }
