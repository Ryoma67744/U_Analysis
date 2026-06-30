# =============================================================================
# MSI Analysis Application - クラスタ安定性ランナー（R 再計算 + Python 集約）
# =============================================================================
# UMAP/クラスタを複数 seed・部分標本で再計算し、stability.py で集約する。
#   - 重い再計算（Seurat FindNeighbors/FindClusters の seed 違い）は R 側
#     stability_diagnostics.R に委譲し、CellID × seed のラベル行列 CSV を得る。
#   - 本モジュールはコマンド組立・CSV 解析・集約（ARI/Jaccard/旗）を担う。
#   - ラベル解析と集約は純 Python（テスト可能）。subprocess 実行は run_stability()。
# =============================================================================

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from app.services import stability as _st

DEFAULT_SEEDS = (42, 101, 202, 303, 404)
LABELS_CSV = "stability_labels.csv"


def build_rscript_cmd(rds_path: str, out_dir: str, helpers_dir: str,
                      seeds: Sequence[int] = DEFAULT_SEEDS,
                      subsample_frac: float = 1.0,
                      reduction: str = "auto") -> list:
    """stability_diagnostics.R を呼ぶ Rscript コマンドを組み立てる（実行はしない）。"""
    script = str(Path(helpers_dir) / "stability_diagnostics.R")
    return [
        "Rscript", "--vanilla", script,
        "--rds", str(rds_path),
        "--out", str(out_dir),
        "--seeds", ",".join(str(int(s)) for s in seeds),
        "--subsample", str(subsample_frac),
        "--reduction", reduction,
    ]


def parse_labels_csv(path: str) -> "pd.DataFrame":
    """CellID, ref, seed_<n>... のラベル行列を読む。

    クラスタラベルは数値ではなくカテゴリとして扱う（"10" と 10 を混同しない）ため
    全列を文字列で読む。
    """
    df = pd.read_csv(path, dtype=str)
    if "ref" not in df.columns:
        raise ValueError("labels CSV に ref 列がありません")
    return df


def aggregate_from_labels(labels: "pd.DataFrame",
                          unstable: float = _st.UNSTABLE_THRESHOLD,
                          stable: float = _st.STABLE_THRESHOLD) -> dict:
    """ラベル行列（ref + seed_*）から ARI/クラスタ別 Jaccard/旗を集約する。"""
    seed_cols = [c for c in labels.columns if c.startswith("seed_")]
    if not seed_cols:
        raise ValueError("seed_* 列がありません")
    ref = labels["ref"].to_numpy()
    alts = [labels[c].to_numpy() for c in seed_cols]
    summary = _st.aggregate_seed_stability(ref, alts, unstable=unstable, stable=stable)
    summary["n_runs"] = len(seed_cols)
    summary["n_cells"] = int(len(labels))
    return summary


def run_stability(rds_path: str, out_dir: str, helpers_dir: str,
                  seeds: Sequence[int] = DEFAULT_SEEDS,
                  subsample_frac: float = 1.0,
                  reduction: str = "auto",
                  timeout: int = 3600,
                  env: Optional[dict] = None) -> dict:
    """R 再計算を実行し、ラベル行列を集約して安定性サマリを返す。

    R が無い/失敗した場合は {"error": ...} を返す（呼び出し側で UI 表示）。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cmd = build_rscript_cmd(rds_path, out_dir, helpers_dir, seeds, subsample_frac, reduction)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, env=env)
    except FileNotFoundError:
        return {"error": "Rscript が見つかりません（R 未インストール）。"}
    except subprocess.TimeoutExpired:
        return {"error": f"安定性解析がタイムアウトしました（{timeout}s）。"}
    labels_path = out / LABELS_CSV
    if proc.returncode != 0 or not labels_path.is_file():
        return {"error": f"R 安定性解析に失敗しました: {proc.stderr[-800:] if proc.stderr else proc.returncode}"}
    summary = aggregate_from_labels(parse_labels_csv(str(labels_path)))
    summary["labels_csv"] = str(labels_path)
    return summary
