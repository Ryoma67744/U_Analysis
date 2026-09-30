"""Versioned numerical requests. A request hash is not an artifact/content hash.

R records effective arguments, feature/cell identities and numerical artifacts
after computing each stage; these Python hashes describe the requested work.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCHEMA_VERSION = 2
DOWNSTREAM_KEYS = frozenset({
    "umap_dims_n", "umap_n_neighbors", "umap_min_dist", "umap_metric", "umap_seed",
    "cluster_dims_n", "cluster_k_param", "cluster_metric", "cluster_algorithm",
    "cluster_resolution", "cluster_resolution_single", "cluster_resolution_harmony",
    "cluster_resolution_rpca", "cluster_seed", "p_thresh", "logfc_thresh",
})
UPSTREAM_KEYS = (
    "input_normalized", "norm_mode", "mz_align_ppm", "calibration_enable",
    "calibration_coefficients", "calibration_by_sample", "spatial_smooth_enable",
    "spatial_smooth_radius", "spatial_smooth_sigma", "filter_mode", "target_clusters",
    "cluster_source", "source_rds_fingerprint", "annotation_filter", "roi_filter",
    "use_roi_as_sample", "execution_policy", "pca_npcs", "pca_seed", "correction_seed",
    "n_var_features", "pca_retry_grid", "rpca_retry_grid", "min_cells_rpca",
    "batch_var", "batch_correction_enable", "annotation_role", "allow_condition_correction",
    "v13_batch_var", "v13_batch_correction_enable", "v13_annotation_role",
    "v13_allow_condition_correction",
)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validated_import_record(params):
    """Validate the explicit import, including every immutable RDS, before bypassing raw I/O."""
    entry = params.get("validated_legacy_import")
    if not entry and params.get("resume_from_rds"):
        paths = params.get("resume_rds_paths") or []
        if paths:
            source = Path(paths[0]).parent
            if source.name == "RDS_Files":
                source = source.parent
            record_path = source / "analysis_params.json"
            if record_path.is_file():
                record = json.loads(record_path.read_text(encoding="utf-8"))
                entry = (record.get("runtime_parameters") or record).get("validated_legacy_import")
    if not entry:
        return None
    manifest_path = Path(entry.get("manifest_path", ""))
    if not manifest_path.is_file() and params.get("resume_rds_paths"):
        source = Path(params["resume_rds_paths"][0]).parent
        if source.name == "RDS_Files":
            source = source.parent
        manifest_path = source / str(entry.get("manifest_path", "")).replace("\\", "/").rsplit("/",1)[-1]
        entry = {**entry, "manifest_path": str(manifest_path)}
    if not manifest_path.is_file() or file_sha256(manifest_path) != entry.get("manifest_sha256"):
        raise ValueError("旧結果インポートの検証記録が変更されています。")
    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = record.get("artifacts")
    if record.get("schema_version") != 1 or record.get("kind") != "validated_reduction_import" or not isinstance(artifacts,list) or not artifacts:
        raise ValueError("旧結果インポートの形式が不正です。")
    names = set()
    for artifact in artifacts:
        if (str(artifact.get("method", "")).lower() not in {"pca", "harmony", "rpca"}
                or not artifact.get("reduction") or type(artifact.get("n_cells")) is not int
                or type(artifact.get("n_dims")) is not int or artifact["n_cells"] < 1 or artifact["n_dims"] < 2):
            raise ValueError("インポートの手法・画素数・次元数の検証記録が不正です。")
        path = (manifest_path.parent / artifact["path"]).resolve()
        if not path.is_relative_to(manifest_path.parent.resolve()) or str(path) in names:
            raise ValueError("旧結果インポートの成果物パスが不正です。")
        names.add(str(path))
        if not path.is_file() or file_sha256(path) != artifact.get("sha256"):
            raise ValueError("インポートRDSの内容が検証時と異なります。")
    return {"entry": entry, "manifest": record}


def resolve_stage_defaults(params):
    """Resolve seeds/PC count once, before downstream overrides are applied."""
    seed = params.get("umap_seed") if params.get("umap_seed") is not None else 42
    for key in ("pca_seed", "correction_seed", "cluster_seed", "umap_seed"):
        if params.get(key) is None:
            params[key] = seed
    if params.get("pca_npcs") is None:
        params["pca_npcs"] = 30
    if params.get("cluster_dims_n") is None:
        # A resolved, explicit clustering choice; later UMAP edits cannot alter it.
        params["cluster_dims_n"] = 30


def build_stage_signatures(params, numerical_selection):
    scripts = Path(__file__).resolve().parents[2] / "Script"
    paths = [scripts / "helpers/analysis_contract.R", scripts / "helpers/feature_naming_policy.R"]
    if params.get("template_path"):
        paths.append(Path(params["template_path"]))
    code = {p.name: file_sha256(p) for p in paths if p.is_file()}
    entries = {str(Path(entry.get("path", "")).resolve()): entry
               for entry in (params.get("section_manifest") or {}).get("files", [])}
    inputs = []
    for item in params.get("input_fingerprints", []):
        entry = entries.get(str(Path(item.get("path", "")).resolve()), {})
        if entry.get("spectral_key") and entry.get("source_fingerprint"):
            # A metadata-only registration revision creates a new Parquet/cache
            # footer, but leaves the measured spectra and numeric selection intact.
            inputs.append({"file_id": entry.get("file_id"), "spectral_key": entry["spectral_key"],
                           "xml_sha256": entry["source_fingerprint"]["xml"]["sha256"],
                           "ibd_sha256": entry["source_fingerprint"]["ibd"]["sha256"]})
        else:
            inputs.append({k: v for k, v in item.items()
                           if k not in {"mtime_ns", "path"} or not item.get("sha256")})
    base = {"schema_version": SCHEMA_VERSION, "code": code}
    def stage(name, parent, values):
        return canonical_hash({**base, "stage": name, "parent": parent, "parameters": values})
    result = {}
    result["input"] = stage("input", None, {"files": inputs, "selection": numerical_selection})
    upstream = {key: params.get(key) for key in UPSTREAM_KEYS}
    if any(params.get(k) in {"group", "subject_id", "section_display_name"}
           for k in ("batch_var", "v13_batch_var", "annotation_role", "v13_annotation_role")):
        upstream["correction_metadata"] = params.get("section_manifest")
    result["upstream"] = stage("upstream", result["input"], upstream)
    # The R-side effective signature additionally binds actual features, retry,
    # dependency versions and matrices. Never claim these request hashes do so.
    for method in ("pca", "harmony", "rpca"):
        result[method] = stage(method, result["upstream"], {"method": method})
    for name, keys in (
        ("umap", ("umap_dims_n", "umap_n_neighbors", "umap_min_dist", "umap_metric", "umap_seed")),
        ("cluster", ("cluster_dims_n", "cluster_k_param", "cluster_metric", "cluster_algorithm",
                     "cluster_resolution", "cluster_resolution_single", "cluster_resolution_harmony",
                     "cluster_resolution_rpca", "cluster_seed")),
    ):
        result[name] = stage(name, result["upstream"], {k: params.get(k) for k in keys})
    result["deg"] = stage("deg", result["cluster"], {
        "test": "wilcox", "only_pos": False, "min_pct": 0, "logfc_threshold": 0,
        "return_thresh": 1, "adjust": "BH", "assay": "Spatial"})
    export_values = {
        k: params.get(k) for k in ("p_thresh", "logfc_thresh", "annotation_enable",
                                  "annotation_path", "annotation_csv_path", "ion_mode",
                                  "adduct_patterns", "tolerance_mz", "section_manifest")}
    export_values["annotation_enable"] = bool(params.get("annotation_enable",False))
    result["export"] = stage("export", [result["umap"], result["deg"]], export_values)
    return result
