"""初回・再解析・段階実行に共通する切片情報と実行条件。"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid
from app.utils.file_locks import atomic_write_json
from app.services.stage_signatures import (
    DOWNSTREAM_KEYS, UPSTREAM_KEYS, build_stage_signatures, file_sha256, resolve_stage_defaults,
    validated_import_record,
)

AUTO_POLICY = "section_auto_v1"
_INHERITED = (
    "template_path", "data_folder", "original_data_folder", "input_paths", "original_input_paths", "sample_names",
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
    "cluster_resolution_rpca", "p_thresh", "logfc_thresh", "pca_npcs", "pca_seed",
    "correction_seed", "cluster_seed", "validated_legacy_import", "legacy_import_consistency",
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
    """実行直前の表を採用し、Store更新の遅れで名称・群を失わない。"""
    if manifest is None or rows is None:
        return manifest
    from app.services.section_metadata import apply_metadata_updates
    # ★ ver74.0: 実行直前の表も同じoverlay更新規則を使い固定registryを壊さない。
    return apply_metadata_updates(manifest, rows, confirmed=False, registration_revision=True)


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
    completed_reductions, completed_analyses = [], []
    artifact_digests = {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    for name, row in methods.items():
        label = names.get(str(name).lower(), str(name))
        if not isinstance(row, dict):
            incomplete.append(label)
            continue
        status = row.get("status")
        if manifest.get("schema_version") == 2 and isinstance(row.get("stages"), dict):
            stage_rows = row["stages"]
            def complete(name):
                item = stage_rows.get(name) or {}
                artifact = item.get("rds_path") or row.get("rds_path")
                if item.get("status") != "complete" or not artifact:
                    return False
                artifact_path = Path(artifact)
                if not artifact_path.is_absolute():
                    artifact_path = Path(output_dir) / artifact_path
                if not artifact_path.is_file():
                    return False
                expected = item.get("artifact_sha256")
                if not expected:
                    return False  # v2 completion is bound to the published bytes
                identity = str(artifact_path.resolve())
                if identity not in artifact_digests:
                    artifact_digests[identity] = file_sha256(artifact_path)
                return artifact_digests[identity] == expected
            reduced = complete("reduction")
            analyzed = complete("umap") and complete("cluster")
            if reduced:
                completed_reductions.append(label)
            if analyzed:
                completed_analyses.append(label)
            if reduced or analyzed:
                available.append(label)
                stages.append("downstream" if analyzed else "reduction")
            if any(s.get("status") in {"failed", "running"} for s in stage_rows.values() if isinstance(s, dict)):
                incomplete.append(label)
            continue
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
    result = {"available": available, "incomplete": incomplete,
              "reduction_only": bool(available) and all(stage == "reduction" for stage in stages)}
    if manifest.get("schema_version") == 2:
        result.update(completed_reductions=completed_reductions, completed_analyses=completed_analyses,
                      run_id=manifest.get("run_id"))
    return result


def _numeric_manifest(manifest):
    return [{"file_id": f.get("file_id"), "path": f.get("path"),
             "selection_mode": f.get("selection_mode"), "rois": [] if f.get("registered_sections") else sorted(f.get("rois") or []),
             "roi_role": f.get("roi_role"),
             "coordinate_hash": (f.get("spatial_layout") or {}).get("coordinate_hash"),
             # 同じ原本パスでも変換revisionまたは座標切片構成が異なれば別の数値入力。
             "conversion_key": f.get("spectral_key") or f.get("conversion_key"),
             "sections": [{
                 "section_id": s.get("section_id"),
                 "roi": None if f.get("registered_sections") else s.get("roi"),
                 "integration_unit_id": s.get("integration_unit_id"),
                 "component_ids": sorted(s.get("component_ids") or []),
             } for s in f.get("sections", [])]} for f in (manifest or {}).get("files", [])]


def reduction_signature(params):
    """★ ver74.0: reductionの数値条件だけを署名しmetadata revisionと分離する。"""
    if params.get("signature_schema_version") == 2:
        return build_stage_signatures(params, _numeric_manifest(params.get("section_manifest")))["upstream"]
    keys = ("input_normalized", "norm_mode", "mz_align_ppm", "calibration_enable",
            "calibration_coefficients", "calibration_by_sample", "umap_n_neighbors", "umap_min_dist",
            "umap_metric", "umap_dims_n", "umap_seed", "cluster_dims_n", "cluster_k_param",
            "cluster_metric", "cluster_algorithm", "cluster_resolution", "cluster_resolution_single",
            "cluster_resolution_harmony", "cluster_resolution_rpca", "p_thresh", "logfc_thresh",
            "execution_policy", "filter_mode", "target_clusters", "cluster_source", "source_rds_fingerprint",
            "batch_var", "batch_correction_enable", "annotation_role", "allow_condition_correction",
            "v13_batch_var", "v13_batch_correction_enable", "v13_annotation_role", "v13_allow_condition_correction",
            "annotation_filter", "roi_filter", "use_roi_as_sample")
    payload = {k: params.get(k) for k in keys}
    payload["selection"] = _numeric_manifest(params.get("section_manifest"))
    # ★ ver74.0: 群を補正変数とする旧手動設定ではmetadataも数値条件。AUTO_POLICYだけと混同しない。
    if any(params.get(key) in {"group", "subject_id", "section_display_name"}
           for key in ("batch_var", "v13_batch_var", "annotation_role", "v13_annotation_role")):
        payload["correction_metadata"] = [{key: row.get(key) for key in
            ("section_id", "section_display_name", "subject_id", "group")}
            for file_entry in (params.get("section_manifest") or {}).get("files", [])
            for row in file_entry.get("sections", [])]
    entries = {str(Path(f.get("path", "")).resolve()): f
               for f in (params.get("section_manifest") or {}).get("files", [])}
    numeric_inputs = []
    for item in params.get("input_fingerprints") or []:
        entry = entries.get(str(Path(item.get("path", "")).resolve()), {})
        if entry.get("spectral_key") and entry.get("source_fingerprint"):
            numeric_inputs.append({"file_id": entry.get("file_id"), "spectral_key": entry["spectral_key"],
                "xml_sha256": entry["source_fingerprint"]["xml"]["sha256"],
                "ibd_sha256": entry["source_fingerprint"]["ibd"]["sha256"]})
        else:
            numeric_inputs.append(item)
    payload["input_fingerprints"] = numeric_inputs
    # コード変更を数値cacheへ混入させない。metadataのみのPython修正は含めない。
    scripts = Path(__file__).resolve().parents[2] / "Script"
    paths = [scripts / "helpers" / "analysis_contract.R", scripts / "helpers" / "feature_naming_policy.R"]
    if params.get("template_path"):
        paths.append(Path(params["template_path"]))
    payload["numerical_code"] = {str(path.name): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in paths if path.is_file()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def metadata_signature(params):
    """群別統計・表示の無効化key。原登録/overlay/実行時有効値を全て記録する。"""
    payload = {"manifest": params.get("section_manifest"),
               "annotation_enable": params.get("annotation_enable"),
               "annotation_path": params.get("annotation_path"),
               "p_thresh": params.get("p_thresh"), "logfc_thresh": params.get("logfc_thresh")}
    payload["annotation_settings"] = {key: params.get(key) for key in
        ("annotation_csv_path", "reanalysis_annotation_path", "ion_mode", "tolerance_mz",
         "adduct_patterns", "use_embedded_annotation", "db_annotation_enabled")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()


def analysis_signature(params):
    """完全来歴key。数値reduction再利用判定にはreduction_signatureを用いる。"""
    payload = {"reduction": reduction_signature(params), "metadata": metadata_signature(params),
               "input_fingerprints": params.get("input_fingerprints") or []}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()


def _fingerprint(path):
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return None
    st = p.stat()
    return {"path": str(p), "size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": file_sha256(p)}


def prepare_execution_params(params, output_dir, *, preflight=False):
    """注入する条件を確定し、R起動前に保存する（paramsを更新）。"""
    # ★ ver67.0: 続き実行で現在画面の条件を混ぜず、元実行の条件を引き継ぐ。
    mode = params.get("execution_mode") or "resume_same"
    if mode not in {"resume_same", "downstream_new"}:
        raise ValueError("不明な再開モードです。")
    params["execution_mode"] = mode
    selected_template = params.get("template_path")
    edits = {key: deepcopy(params[key]) for key in DOWNSTREAM_KEYS if params.get(key) is not None}
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
        if mode == "downstream_new" and saved.get("signature_schema_version") != 2 and not saved.get("validated_legacy_import"):
            raise ValueError("旧結果は検証済みのインポート後に、新条件の下流解析を実行してください。")
        # Resolve the old shared seed before applying a new UMAP-only seed.
        if saved.get("signature_schema_version") == 2 or mode == "downstream_new":
            resolve_stage_defaults(saved)
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
        for key in UPSTREAM_KEYS:
            if key in saved:
                params[key] = deepcopy(saved[key])
            elif key not in _INHERITED:
                params.pop(key, None)
        if mode == "downstream_new":
            params.update(edits)
            params["source_template_path"] = saved.get("template_path")
            if selected_template:
                params["template_path"] = selected_template
            elif saved.get("validated_legacy_import"):
                # Imports use the current supported runtime even if the original
                # computer's script path no longer exists.
                from app.config import DESI_V8_TEMPLATE_PATH, TIMS_V8_TEMPLATE_PATH
                instrument = str(saved.get("instrument", "")) + str(saved.get("template_path", ""))
                params["template_path"] = str(DESI_V8_TEMPLATE_PATH if "desi" in instrument.lower()
                                              else TIMS_V8_TEMPLATE_PATH)
            if "cluster_resolution" in edits and "desi" in str(params.get("template_path", "")).lower():
                for key in ("cluster_resolution_single", "cluster_resolution_harmony", "cluster_resolution_rpca"):
                    params[key] = edits.get(key, edits["cluster_resolution"])
            params["pipeline_stage"] = "downstream_from_reduction"
        if saved.get("signature_schema_version") == 2:
            params["signature_schema_version"] = 2
        else:
            params.pop("signature_schema_version", None)
            params["legacy_signatures"] = {key: saved[key] for key in
                ("analysis_signature", "reduction_signature") if saved.get(key)} or deepcopy(saved.get("legacy_signatures", {}))
        params["source_result_dir"] = str(root)
        params["source_run"] = {k: record.get(k) for k in
                                ("timestamp", "pipeline_stage", "analysis_signature", "execution_policy")}
        if params.get("original_input_paths") and not params.get("input_paths"):
            params["input_paths"] = deepcopy(params["original_input_paths"])
        if "execution_policy" not in saved and saved.get("signature_schema_version") != 2:
            params["execution_policy"] = "legacy_saved"
        fingerprints = record.get("input_fingerprints") or saved.get("input_fingerprints") or []
        imported = saved.get("validated_legacy_import")
        if imported:
            validated = validated_import_record(params)
            params["validated_legacy_import"] = validated["entry"]
        to_check = [] if imported else fingerprints + ([saved["source_rds_fingerprint"]] if saved.get("source_rds_fingerprint") else [])
        for item in to_check:
            # ★ ver70.0: 旧imzML結果の入力はimmutable cache。原本の移動/更新を誤検出しない。
            pinned = [f for f in (params.get("section_manifest") or {}).get("files", [])
                      if f.get("conversion_key") and Path(f.get("path", "")).resolve() == Path(item["path"]).resolve()]
            if pinned:
                if not params.get("_imzml_defer_validation"):
                    from app.services.input_preparation import validate_asset
                    validate_asset(pinned[0])
                continue
            current = _fingerprint(item["path"])
            if current is None:
                raise ValueError(f"保存済み入力が見つかりません: {Path(item['path']).name}。元の入力を復元してください。")
            if current["size"] != item["size"] or current["mtime_ns"] != item["mtime_ns"]:
                raise ValueError(f"保存後に入力が変更されています: {Path(item['path']).name}。新規解析を実行してください。")
            if item.get("sha256") and current["sha256"] != item["sha256"]:
                raise ValueError(f"保存後に入力内容が変更されています: {Path(item['path']).name}。")
        if fingerprints:
            params["input_fingerprints"] = deepcopy(fingerprints)
        if imported:
            params["legacy_source_manifest"] = params.get("section_manifest")
            params["legacy_input_paths"] = params.get("input_paths")
            params["section_manifest"] = None
            params["section_manifest_path"] = ""
            params["input_paths"] = []
            params["data_folder"] = str(Path(params["validated_legacy_import"]["manifest_path"]).parent)
            params["sample_names"] = []
        if saved.get("analysis_signature") and not saved.get("reduction_signature"):
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
    if not source or params.get("signature_schema_version") == 2 or params.get("validated_legacy_import"):
        resolve_stage_defaults(params)
    from app.utils.validation import validate_downstream_parameters
    validate_downstream_parameters(params)
    if preflight:
        # ★ ver74.0: 保存runの復元後にも検証し、直接Parquet再開の入口漏れを防ぐ。
        from app.services.input_preparation import preflight_registered_inputs
        preflight_registered_inputs(params)
    manifest = params.get("section_manifest")
    if manifest is not None and not params.get("validated_legacy_import"):
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
        entries = {str(Path(f["path"]).resolve()): f for f in (manifest or {}).get("files", [])}
        fingerprints = []
        for p in paths:
            entry = entries.get(str(Path(p).resolve()), {})
            if entry.get("conversion_key"):
                # ★ ver70.0: XML/ibdの両方と固定runtimeのhashを記録する。
                fingerprints.append({**entry["source_fingerprint"]["xml"],
                    "ibd": entry["source_fingerprint"]["ibd"], "conversion_key": entry["conversion_key"],
                    "validation": entry["validation"]})
            elif (item := _fingerprint(p)):
                fingerprints.append(item)
        params["input_fingerprints"] = fingerprints
    if not source or "source_rds_fingerprint" not in params:
        if params.get("rds_path"):
            params["source_rds_fingerprint"] = _fingerprint(params["rds_path"])
        else:
            params.pop("source_rds_fingerprint", None)
    # ★ ver67.0: 通常実行で辞書が再利用されても古い署名を持ち越さない。
    # 保存条件を引き継いだ再開では、その実行の署名を維持する。
    if not source or params.get("signature_schema_version") == 2 or params.get("validated_legacy_import"):
        params["signature_schema_version"] = 2
        params["stage_signatures"] = build_stage_signatures(params, _numeric_manifest(params.get("section_manifest")))
    params["reduction_signature"] = reduction_signature(params)
    params["metadata_signature"] = metadata_signature(params)
    if not source or not params.get("analysis_signature") or saved.get("reduction_signature"):
        params["analysis_signature"] = analysis_signature(params)
    if params.get("run_output_dir") != str(output_dir):
        params["run_id"] = str(uuid.uuid4())
        params["run_output_dir"] = str(output_dir)
    if source:
        params["parent_run_id"] = saved.get("run_id") or record.get("run_id")
    return params
