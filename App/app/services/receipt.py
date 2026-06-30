# =============================================================================
# MSI Analysis Application - 解析レシート（1 ファイルに集約）
# =============================================================================
# 「目で見て分かる」かつ「それ 1 つで再現できる」控えを 1 解析につき 1 つ作る。
#   - 母体は既存 analysis_params.json（解析開始時に書かれる入力パラメータ）。
#   - これに不足分（ソフト/パッケージ版・seed 値・cluster/正規化/補正・入力 checksum・
#     出力一覧・終了時刻・アノテーション由来）を足して 1 つの receipt にまとめる。
#   - キー名は schema.org / Process Run Crate 形（object/result/instrument/agent/
#     startTime/softwareVersion）に寄せ、将来の RO-Crate 書出を容易にする。
#   - 機械可読 JSON（receipt.json）と人可読 Markdown（RECEIPT.md）の両方を出す。
#
# 依存は標準ライブラリのみ。
# =============================================================================

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

RECEIPT_VERSION = "1"
RECEIPT_JSON = "receipt.json"
RECEIPT_MD = "RECEIPT.md"

# レシートに版を記録したい代表 Python パッケージ
_PY_PACKAGES = ("dash", "plotly", "pandas", "numpy", "pyarrow", "polars-lts-cpu",
                "python-pptx", "kaleido", "Pillow", "scipy", "scikit-learn")


def sha256_file(path, chunk: int = 1 << 20) -> Optional[str]:
    """ファイルの SHA-256（存在しなければ None）。大きい入力も逐次読みで対応。"""
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def collect_python_versions(packages: Sequence[str] = _PY_PACKAGES) -> dict:
    """主要 Python パッケージの版を集める（取得不可は省略）。"""
    try:
        from importlib import metadata as ilm
    except ImportError:  # pragma: no cover
        return {}
    out = {}
    for name in packages:
        try:
            out[name] = ilm.version(name)
        except Exception:
            continue
    import platform
    out["python"] = platform.python_version()
    return out


def file_entry(path) -> dict:
    p = Path(path)
    ent = {"path": str(path)}
    if p.is_file():
        ent["bytes"] = p.stat().st_size
        ent["sha256"] = sha256_file(p)
    return ent


def build_receipt(params: dict,
                  r_sidecar: Optional[dict] = None,
                  inputs: Optional[Sequence[str]] = None,
                  outputs: Optional[Sequence[str]] = None,
                  app_version: Optional[str] = None,
                  annotation_sources: Optional[Sequence[str]] = None,
                  started_at: Optional[str] = None,
                  ended_at: Optional[str] = None) -> dict:
    """既存 analysis_params dict（＋R サイドカー）から集約レシートを組み立てる。

    params は analysis_params.json の内容（フラットな dict）。r_sidecar は R 側で出した
    analysis_receipt_r.json（sessionInfo/seed/cluster 設定等）。
    """
    params = dict(params or {})
    r_sidecar = dict(r_sidecar or {})

    started = started_at or params.get("timestamp")
    ended = ended_at or params.get("execution_end_time")
    elapsed = None
    if started and ended:
        try:
            elapsed = (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds()
        except ValueError:
            elapsed = None

    instrument = {
        "app_version": app_version or params.get("app_version"),
        "r_version": r_sidecar.get("r_version"),
        "packages": {
            "python": collect_python_versions(),
            "r": r_sidecar.get("package_versions") or {},
        },
    }

    preprocessing = {
        "input_normalized": r_sidecar.get("input_normalized"),
        "norm_mode": r_sidecar.get("norm_mode"),
        "batch_correction": r_sidecar.get("batch_correction"),
        "calibration_enable": params.get("calibration_enable"),
        "calibration_regression_mode": params.get("calibration_regression_mode"),
    }
    umap = {
        "seed": r_sidecar.get("seed", params.get("umap_seed")),
        "n_neighbors": params.get("umap_n_neighbors"),
        "min_dist": params.get("umap_min_dist"),
        "metric": params.get("umap_metric"),
        "dims": params.get("umap_dims_n"),
        "threads": r_sidecar.get("threads", params.get("omp_num_threads")),
    }
    clustering = {
        "algorithm": r_sidecar.get("clustering_algorithm"),
        "resolution": r_sidecar.get("clustering_resolution"),
        "k_param": r_sidecar.get("clustering_k"),
    }
    annotation = {
        "sources": list(annotation_sources or params.get("annotation_sources") or []),
        "annotation_csv": params.get("annotation_csv") or params.get("annotation_path") or "",
        "ion_mode": params.get("ion_mode") or "",
        "tolerance_mz": params.get("tolerance_mz"),
        "adduct_filter": params.get("adduct_filter") or [],
    }

    return {
        "receipt_version": RECEIPT_VERSION,
        "schema": "schema.org/CreateAction (Process Run Crate-aligned)",
        "startTime": started,
        "endTime": ended,
        "elapsed_seconds": elapsed,
        "agent": {"operator": params.get("operator") or params.get("created_by")},
        "instrument": instrument,
        "object": {
            "analysis_type": params.get("analysis_type"),
            "data_folder": params.get("data_folder"),
            "inputs": [file_entry(p) for p in (inputs or [])],
            "preprocessing": preprocessing,
            "umap": umap,
            "clustering": clustering,
            "annotation": annotation,
            "thresholds": {"p": params.get("p_thresh"), "logfc": params.get("logfc_thresh")},
            "filter_mode": params.get("filter_mode") or "",
            "target_clusters": params.get("target_clusters") or "",
        },
        "result": {
            "output_dir": params.get("output_dir"),
            "outputs": [file_entry(p) for p in (outputs or [])],
        },
        "raw_params": params,
    }


def _md_kv(title: str, d: dict) -> str:
    lines = [f"### {title}", ""]
    for k, v in d.items():
        if v in (None, "", [], {}):
            continue
        lines.append(f"- **{k}**: {v}")
    lines.append("")
    return "\n".join(lines)


def render_receipt_markdown(receipt: dict) -> str:
    """レシートを人が読める Markdown に整形する。"""
    obj = receipt.get("object", {})
    instr = receipt.get("instrument", {})
    lines = [
        "# 解析レシート (Analysis Receipt)", "",
        f"- **analysis_type**: {obj.get('analysis_type')}",
        f"- **開始**: {receipt.get('startTime')}　**終了**: {receipt.get('endTime')}"
        f"　**経過(s)**: {receipt.get('elapsed_seconds')}",
        f"- **operator**: {receipt.get('agent', {}).get('operator')}",
        f"- **data_folder**: {obj.get('data_folder')}",
        f"- **output_dir**: {receipt.get('result', {}).get('output_dir')}",
        "",
    ]
    lines.append(_md_kv("ソフトウェア / パッケージ版", {
        "app_version": instr.get("app_version"),
        "r_version": instr.get("r_version"),
        "python": (instr.get("packages", {}).get("python") or {}).get("python"),
    }))
    lines.append(_md_kv("UMAP 設定", obj.get("umap", {})))
    lines.append(_md_kv("クラスタリング設定", obj.get("clustering", {})))
    lines.append(_md_kv("前処理 / 正規化 / 補正", obj.get("preprocessing", {})))
    ann = obj.get("annotation", {})
    lines.append(_md_kv("アノテーション由来", {
        "sources": ", ".join(ann.get("sources", [])) or "(なし)",
        "annotation_csv": ann.get("annotation_csv"),
        "ion_mode": ann.get("ion_mode"),
        "tolerance_mz": ann.get("tolerance_mz"),
    }))
    inputs = obj.get("inputs", [])
    if inputs:
        lines.append("### 入力ファイル (checksum)")
        lines.append("")
        for e in inputs:
            lines.append(f"- `{e.get('path')}`  sha256=`{(e.get('sha256') or '')[:12]}…`")
        lines.append("")
    lines.append("> このレシート 1 つで、同じ入力・設定からの再現を意図しています。")
    return "\n".join(lines)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_receipt(output_dir, receipt: dict) -> dict:
    """receipt.json と RECEIPT.md を結果フォルダにアトミック書き込みする。"""
    out = Path(output_dir)
    json_path = out / RECEIPT_JSON
    md_path = out / RECEIPT_MD
    _atomic_write(json_path, json.dumps(receipt, indent=2, ensure_ascii=False))
    _atomic_write(md_path, render_receipt_markdown(receipt))
    return {"json": str(json_path), "md": str(md_path)}


def load_r_sidecar(output_dir) -> dict:
    """R 側サイドカー（analysis_receipt_r.json）を読む（無ければ空）。"""
    p = Path(output_dir) / "analysis_receipt_r.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def finalize_receipt(output_dir, app_version: Optional[str] = None,
                     inputs: Optional[Sequence[str]] = None,
                     outputs: Optional[Sequence[str]] = None,
                     annotation_sources: Optional[Sequence[str]] = None,
                     ended_at: Optional[str] = None) -> Optional[dict]:
    """結果フォルダの analysis_params.json と R サイドカーから receipt を確定・書出。

    解析完了ハンドラから 1 行で呼べる入口。失敗しても解析本体は壊さないよう、
    呼び出し側で try/except して使うこと（本関数はファイルが無ければ None を返す）。
    """
    out = Path(output_dir)
    params_path = out / "analysis_params.json"
    if not params_path.is_file():
        return None
    try:
        params = json.loads(params_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if ended_at and not params.get("execution_end_time"):
        params["execution_end_time"] = ended_at
    # 入力 checksum は既定で annotation_csv のみ（大きい raw のハッシュは避ける）
    if inputs is None:
        acsv = params.get("annotation_csv") or params.get("annotation_path")
        inputs = [acsv] if acsv else []
    receipt = build_receipt(
        params,
        r_sidecar=load_r_sidecar(output_dir),
        inputs=inputs,
        outputs=outputs,
        app_version=app_version,
        annotation_sources=annotation_sources,
        ended_at=ended_at,
    )
    write_receipt(output_dir, receipt)
    return receipt
