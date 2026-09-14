"""初回・再解析・段階実行に共通する切片情報と実行条件。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from app.utils.file_locks import atomic_write_json

AUTO_POLICY = "section_auto_v1"
_INHERITED = (
    "data_folder", "original_data_folder", "input_paths", "original_input_paths", "sample_names",
    "filter_mode", "target_clusters", "cluster_source", "rds_path", "source_rds_fingerprint",
    "section_manifest", "execution_policy", "input_normalized", "norm_mode", "mz_align_ppm",
    "annotation_enable", "db_annotation_enabled", "use_embedded_annotation", "annotation_path",
    "annotation_csv_path", "reanalysis_annotation_path", "ion_mode", "tolerance_mz", "adduct_patterns",
    "calibration_enable", "calibration_coefficients", "calibration_by_sample", "batch_var",
    "batch_correction_enable", "annotation_role", "allow_condition_correction", "v13_batch_var",
    "v13_annotation_role", "v13_batch_correction_enable", "v13_allow_condition_correction",
    "annotation_filter", "roi_filter", "use_roi_as_sample", "umap_n_neighbors", "umap_min_dist",
    "umap_metric", "umap_dims_n", "umap_seed", "cluster_dims_n", "cluster_k_param", "cluster_metric",
    "cluster_algorithm", "cluster_resolution", "cluster_resolution_single", "cluster_resolution_harmony",
    "cluster_resolution_rpca", "p_thresh", "logfc_thresh",
)


def result_root(path):
    p = Path(path)
    if p.is_file() or p.suffix.lower() == ".rds":
        p = p.parent
    return p.parent if p.name == "RDS_Files" else p


def selected_manifest_paths(manifest):
    return [str(f["path"]) for f in (manifest or {}).get("files", []) if f.get("selection_mode") != "none"]


def apply_group_rows(manifest, rows):
    """実行直前の表を採用し、Store更新の遅れで編集値を失わない。"""
    if manifest is None or rows is None:
        return manifest
    result = deepcopy(manifest)
    lookup = {r.get("section_id"): r for r in rows if r.get("section_id")}
    for f in result.get("files", []):
        for s in f.get("sections", []):
            row = lookup.get(s.get("section_id"))
            if row is not None:
                for key in ("subject_id", "group"):
                    s[key] = str(row.get(key) or "").strip()
    return result


def method_outcome(output_dir):
    """プロセスの正常終了と全手法の解析完了を区別する。"""
    path = Path(output_dir) / "analysis_methods.json"
    if not path.is_file():
        return None
    try:
        methods = json.loads(path.read_text(encoding="utf-8")).get("methods", {})
    except (OSError, ValueError, AttributeError):
        return {"available": [], "incomplete": ["状態を読み込めません"], "reduction_only": False}
    names = {"pca": "PCA", "harmony": "Harmony", "rpca": "RPCA"}
    complete = [(name, row) for name, row in methods.items() if row.get("status") == "complete"]
    return {"available": [names.get(name.lower(), name) for name, row in complete
                          if row.get("rds_path") and Path(row["rds_path"]).is_file()],
            "incomplete": [names.get(name.lower(), name) for name, row in methods.items()
                           if row.get("status") in ("failed", "running")],
            "reduction_only": bool(complete) and all(row.get("stage") == "reduction" for _, row in complete)}


def _numeric_manifest(manifest):
    return [{"file_id": f.get("file_id"), "path": f.get("path"),
             "selection_mode": f.get("selection_mode"), "rois": sorted(f.get("rois") or []),
             "roi_role": f.get("roi_role"),
             "sections": [{k: s.get(k) for k in ("section_id", "roi", "integration_unit_id")}
                          for s in f.get("sections", [])]} for f in (manifest or {}).get("files", [])]


def analysis_signature(params):
    """群名・個体名だけの変更では数値チェックポイントを無効にしない。"""
    keys = ("input_normalized", "norm_mode", "mz_align_ppm", "calibration_enable",
            "calibration_coefficients", "calibration_by_sample", "umap_n_neighbors", "umap_min_dist",
            "umap_metric", "umap_dims_n", "umap_seed", "cluster_dims_n", "cluster_k_param",
            "cluster_metric", "cluster_algorithm", "cluster_resolution", "cluster_resolution_single",
            "cluster_resolution_harmony", "cluster_resolution_rpca", "p_thresh", "logfc_thresh",
            "execution_policy", "filter_mode", "target_clusters", "cluster_source", "source_rds_fingerprint")
    payload = {k: params.get(k) for k in keys}
    payload["selection"] = _numeric_manifest(params.get("section_manifest"))
    payload["input_fingerprints"] = params.get("input_fingerprints") or []
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _fingerprint(path):
    p = Path(path)
    if not p.is_file():
        return None
    st = p.stat()
    return {"path": str(p), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def prepare_execution_params(params, output_dir):
    """注入する条件を確定し、R起動前に保存する（paramsを更新）。"""
    # ★ verNEXT: 続き実行で現在画面の条件を混ぜず、元実行の条件を引き継ぐ。
    source = params.get("resume_reanalysis_dir") if params.get("resume_reanalysis") else None
    if not source and params.get("resume_from_rds"):
        paths = params.get("resume_rds_paths") or []
        source = paths[0] if paths else None
    if source:
        root = result_root(source)
        record_path = root / "analysis_params.json"
        if not record_path.is_file():
            raise ValueError("保存済みの解析条件が見つかりません。条件を確認して新規解析を実行してください。")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        saved = deepcopy(record.get("runtime_parameters") or record)
        for canonical, old in (("annotation_csv_path", "annotation_csv"), ("adduct_patterns", "adduct_filter")):
            if canonical not in saved and old in saved:
                saved[canonical] = saved[old]
        from app.services.naming_policy import db_annotation_enabled
        saved["annotation_enable"] = db_annotation_enabled(saved)
        latest = root / "section_manifest.json"
        if latest.is_file() and saved.get("section_manifest"):
            manifest = json.loads(latest.read_text(encoding="utf-8"))
            if _numeric_manifest(manifest) == _numeric_manifest(saved["section_manifest"]):
                saved["section_manifest"] = manifest
        for key in _INHERITED:
            if key in saved:
                params[key] = deepcopy(saved[key])
        params["source_result_dir"] = str(root)
        params["source_run"] = {k: record.get(k) for k in
                                ("timestamp", "pipeline_stage", "analysis_signature", "execution_policy")}
        if params.get("original_input_paths") and not params.get("input_paths"):
            params["input_paths"] = deepcopy(params["original_input_paths"])
        if "execution_policy" not in saved:
            params["execution_policy"] = "legacy_saved"
        fingerprints = record.get("input_fingerprints") or saved.get("input_fingerprints") or []
        for item in fingerprints + ([saved["source_rds_fingerprint"]] if saved.get("source_rds_fingerprint") else []):
            current = _fingerprint(item["path"])
            if current and (current["size"] != item["size"] or current["mtime_ns"] != item["mtime_ns"]):
                raise ValueError(f"保存後に入力が変更されています: {Path(item['path']).name}。新規解析を実行してください。")
        if fingerprints:
            params["input_fingerprints"] = deepcopy(fingerprints)
        if saved.get("analysis_signature"):
            params["analysis_signature"] = saved["analysis_signature"]
    if params.get("execution_policy") == AUTO_POLICY:
        params.update(batch_var="integration_unit_id", batch_correction_enable=True, annotation_role="section_id",
                      allow_condition_correction=False, v13_batch_var="integration_unit_id",
                      v13_batch_correction_enable=True, v13_annotation_role="section_id",
                      v13_allow_condition_correction=False, use_embedded_annotation=True, use_roi_as_sample=False)
        params.pop("annotation_filter", None)
        params.pop("roi_filter", None)
    params["db_annotation_enabled"] = bool(params.get("annotation_enable", False))
    if params["db_annotation_enabled"]:
        database = (params.get("reanalysis_annotation_path") or params.get("annotation_csv_path")
                    or params.get("annotation_path") or "")
        if not database or not Path(database).is_file():
            raise ValueError("分子情報の追加がONですが、照合するDBファイルが見つかりません。")
    manifest = params.get("section_manifest")
    if manifest is not None:
        from app.services.section_metadata import validate_section_manifest
        errors = validate_section_manifest(manifest)
        if errors:
            raise ValueError(" / ".join(errors))
        params["section_manifest"] = deepcopy(manifest)
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / "section_manifest.json"
        atomic_write_json(params["section_manifest"], path)
        params["section_manifest_path"] = str(path)
    if "input_fingerprints" not in params:
        paths = selected_manifest_paths(manifest) or params.get("input_paths") or params.get("original_input_paths") or []
        params["input_fingerprints"] = [item for p in paths if (item := _fingerprint(p))]
    if "source_rds_fingerprint" not in params and params.get("rds_path"):
        params["source_rds_fingerprint"] = _fingerprint(params["rds_path"])
    if not params.get("analysis_signature"):
        params["analysis_signature"] = analysis_signature(params)
    return params
