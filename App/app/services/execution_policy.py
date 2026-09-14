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
    return [str(Path(f["path"]).expanduser().resolve())
            for f in (manifest or {}).get("files", []) if f.get("selection_mode") != "none"]


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
                if "section_settings" in result:
                    result["section_settings"][s["section_id"]] = deepcopy(s)
    return result


def method_outcome(output_dir):
    """★ ver67.0: 完了記録だけでなく実在するRDSを確認し、未出力を成功扱いしない。"""
    path = Path(output_dir) / "analysis_methods.json"
    if not path.is_file():
        # ★ ver67.0: 新方式の状態記録欠落を旧形式の正常終了と混同しない。
        try:
            saved = json.loads((Path(output_dir) / "analysis_params.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, AttributeError):
            saved = {}
        policy = saved.get("execution_policy") or (saved.get("runtime_parameters") or {}).get("execution_policy")
        if policy == AUTO_POLICY:
            return {"available": [], "incomplete": ["手法別状態が未保存"], "reduction_only": False}
        return None
    invalid = {"available": [], "incomplete": ["手法別の完了状態が不正です"], "reduction_only": False}
    try:
        methods = json.loads(path.read_text(encoding="utf-8")).get("methods", {})
    except (OSError, ValueError, AttributeError):
        return invalid
    if not isinstance(methods, dict) or not methods:
        return invalid
    names = {"pca": "PCA", "harmony": "Harmony", "rpca": "RPCA"}
    available, incomplete, stages = [], [], []
    for name, row in methods.items():
        label = names.get(str(name).lower(), str(name))
        if not isinstance(row, dict):
            incomplete.append(label)
            continue
        status = row.get("status")
        if status == "complete":
            rds = Path(row["rds_path"]) if row.get("rds_path") else None
            if rds is not None and not rds.is_absolute():
                rds = Path(output_dir) / rds
            if rds is not None and rds.is_file():
                available.append(label)
                stages.append(row.get("stage"))
            else:
                incomplete.append(label)
        elif status in ("failed", "running") or status != "skipped":
            incomplete.append(label)
    if not available and not incomplete:
        incomplete.append("利用可能な解析結果がありません")
    return {"available": available, "incomplete": incomplete,
            "reduction_only": bool(available) and all(stage == "reduction" for stage in stages)}


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
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return None
    st = p.stat()
    return {"path": str(p), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def prepare_execution_params(params, output_dir):
    """注入する条件を確定し、R起動前に保存する（paramsを更新）。"""
    # ★ ver67.0: 続き実行で現在画面の条件を混ぜず、元実行の条件を引き継ぐ。
    source = params.get("resume_reanalysis_dir") if params.get("resume_reanalysis") else None
    if not source and params.get("resume_from_rds"):
        paths = params.get("resume_rds_paths") or []
        source = paths[0] if paths else None
    if source:
        # ★ ver67.0: 再開先に署名が無い旧記録へ、現在の別実行の署名を持ち込まない。
        params.pop("analysis_signature", None)
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
            else:
                # ★ ver67.0: 保存時に未指定だった値へ、現在画面の指定を混入させない。
                params.pop(key, None)
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
            if current is None:
                raise ValueError(f"保存済み入力が見つかりません: {Path(item['path']).name}。元の入力を復元してください。")
            if current["size"] != item["size"] or current["mtime_ns"] != item["mtime_ns"]:
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
    if not source or "input_fingerprints" not in params:
        paths = selected_manifest_paths(manifest) or params.get("input_paths") or params.get("original_input_paths") or []
        params["input_fingerprints"] = [item for p in paths if (item := _fingerprint(p))]
    if not source or "source_rds_fingerprint" not in params:
        if params.get("rds_path"):
            params["source_rds_fingerprint"] = _fingerprint(params["rds_path"])
        else:
            params.pop("source_rds_fingerprint", None)
    # ★ ver67.0: 通常実行で辞書が再利用されても古い署名を持ち越さない。
    # 保存条件を引き継いだ再開では、その実行の署名を維持する。
    if not source or not params.get("analysis_signature"):
        params["analysis_signature"] = analysis_signature(params)
    return params
