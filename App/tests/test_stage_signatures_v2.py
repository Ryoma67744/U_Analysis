import json
import os
from copy import deepcopy

import pytest

from app.services.execution_policy import prepare_execution_params, method_outcome
from app.services.stage_signatures import build_stage_signatures, file_sha256, resolve_stage_defaults


def checkpoint(tmp_path):
    raw = tmp_path / "input.parquet"
    raw.write_bytes(b"fixture input")
    saved = dict(input_paths=[str(raw)], input_normalized=True, norm_mode="log1p",
                 umap_dims_n=10, umap_seed=17, cluster_resolution=.4)
    result = tmp_path / "source"
    result.mkdir()
    prepare_execution_params(saved, result)
    (result / "analysis_params.json").write_text(json.dumps({"runtime_parameters": saved}))
    rds = result / "RDS_Files" / "Step2_PCA_uncorrected.rds"
    rds.parent.mkdir()
    rds.write_bytes(b"rds fixture")
    return raw, saved, result, rds


def test_umap_edits_do_not_invalidate_reduction_or_cluster():
    params = dict(umap_seed=7, umap_dims_n=10)
    resolve_stage_defaults(params)
    before = build_stage_signatures(params, [])
    params.update(umap_seed=8, umap_dims_n=9, umap_n_neighbors=11)
    after = build_stage_signatures(params, [])
    assert before["upstream"] == after["upstream"]
    assert before["cluster"] == after["cluster"]
    assert before["deg"] == after["deg"]
    assert before["umap"] != after["umap"]
    assert before["export"] != after["export"]
    params["pca_seed"] = 8
    assert build_stage_signatures(params, [])["upstream"] != after["upstream"]


def test_new_downstream_keeps_upstream_but_adopts_explicit_values(tmp_path):
    _, saved, _, rds = checkpoint(tmp_path)
    new = dict(resume_from_rds=True, resume_rds_paths=[str(rds)], execution_mode="downstream_new",
               umap_seed=999, umap_dims_n=5, cluster_resolution=.8, norm_mode="sqrt")
    prepare_execution_params(new, tmp_path / "new")
    assert new["norm_mode"] == "log1p"
    assert new["pca_seed"] == new["correction_seed"] == 17
    assert new["cluster_seed"] == 17 and new["umap_seed"] == 999
    assert new["cluster_resolution"] == .8 and new["umap_dims_n"] == 5
    assert new["stage_signatures"]["upstream"] == saved["stage_signatures"]["upstream"]
    assert new["stage_signatures"]["cluster"] != saved["stage_signatures"]["cluster"]
    assert new["parent_run_id"] == saved["run_id"] and new["run_id"] != saved["run_id"]


def test_resume_same_keeps_saved_effective_values(tmp_path):
    _, saved, _, rds = checkpoint(tmp_path)
    new = dict(resume_from_rds=True, resume_rds_paths=[str(rds)], execution_mode="resume_same",
               umap_seed=999, umap_dims_n=5, cluster_resolution=.8)
    prepare_execution_params(new, tmp_path / "continued")
    assert new["stage_signatures"] == saved["stage_signatures"]
    assert new["umap_seed"] == 17 and new["umap_dims_n"] == 10


def test_old_unsigned_cannot_be_relabelled_as_new_contract(tmp_path):
    _, saved, result, rds = checkpoint(tmp_path)
    saved.pop("signature_schema_version")
    (result / "analysis_params.json").write_text(json.dumps({"runtime_parameters": saved}))
    with pytest.raises(ValueError, match="インポート"):
        prepare_execution_params(dict(resume_from_rds=True,resume_rds_paths=[str(rds)],
                                      execution_mode="downstream_new"),tmp_path / "new")


@pytest.mark.parametrize("field,value", [
    ("cluster_dims_n", 2.5), ("cluster_k_param", 0),
    ("cluster_seed", -1), ("cluster_resolution", float("nan")),
    ("cluster_resolution", float("inf")), ("cluster_algorithm", 7),
])
def test_backend_rejects_invalid_effective_parameters_before_publication(tmp_path, field, value):
    _, _, _, rds = checkpoint(tmp_path)
    output = tmp_path / "invalid"
    params = dict(resume_from_rds=True,resume_rds_paths=[str(rds)],execution_mode="downstream_new")
    params[field] = value
    with pytest.raises(ValueError):
        prepare_execution_params(params, output)
    assert not output.exists()


def test_same_size_same_mtime_input_replacement_is_rejected(tmp_path):
    raw, _, _, rds = checkpoint(tmp_path)
    stamp = raw.stat()
    raw.write_bytes(b"changed input")
    os.utime(raw,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
    with pytest.raises(ValueError, match="入力内容"):
        prepare_execution_params(dict(resume_from_rds=True,resume_rds_paths=[str(rds)]),tmp_path / "new")


def test_completed_cluster_survives_failed_figures_and_missing_rds_does_not(tmp_path):
    rds = tmp_path / "result.rds"
    rds.write_bytes(b"published fixture")
    complete = dict(status="complete",rds_path=rds.name,artifact_sha256=file_sha256(rds))
    data = dict(schema_version=2,run_id="run",methods={"pca": dict(status="failed",
        rds_path=rds.name,stages={"reduction":deepcopy(complete),"umap":deepcopy(complete),
                                "cluster":deepcopy(complete),"export":{"status":"failed"}})})
    (tmp_path / "analysis_methods.json").write_text(json.dumps(data))
    outcome = method_outcome(tmp_path)
    assert outcome["completed_reductions"] == outcome["completed_analyses"] == ["PCA"]
    assert outcome["incomplete"] == ["PCA"] and not outcome["reduction_only"]
    rds.write_bytes(b"replacement")
    assert method_outcome(tmp_path)["completed_analyses"] == []


def test_validated_import_can_prepare_downstream_without_original_input(tmp_path):
    from app.services.input_preparation import prepare_inputs, preflight_registered_inputs
    original, saved, result, rds = checkpoint(tmp_path)
    original.unlink()
    manifest = result / "legacy_import.json"
    manifest.write_text(json.dumps(dict(schema_version=1,kind="validated_reduction_import",artifacts=[
        dict(path="RDS_Files/Step2_PCA_uncorrected.rds",sha256=file_sha256(rds),
             method="pca",reduction="pca",n_cells=80,n_dims=20)])))
    saved.pop("signature_schema_version")
    saved["template_path"] = "/old-machine/TIMS/v5.R"
    saved["validated_legacy_import"] = dict(manifest_path=str(manifest),manifest_sha256=file_sha256(manifest))
    (result / "analysis_params.json").write_text(json.dumps({"runtime_parameters":saved}))
    params = dict(resume_from_rds=True,resume_rds_paths=[str(rds)],execution_mode="downstream_new",
                  pipeline_stage="downstream_from_reduction",umap_dims_n=20,cluster_dims_n=20)
    preflight_registered_inputs(params)
    prepare_execution_params(params,tmp_path / "new")
    prepared = prepare_inputs(params)
    assert prepared["input_paths"] == [] and prepared["section_manifest"] is None
    assert prepared["validated_legacy_import"] == saved["validated_legacy_import"]
    assert prepared["legacy_signatures"]["analysis_signature"] == saved["analysis_signature"]
    assert prepared["source_template_path"] == saved["template_path"]
    assert prepared["template_path"].endswith("260623_DBSCAN_With_cluster_ver6_no-png_slim.R")
    from app.services.analysis_runner import generate_v8_config
    prepared.update(data_folder=str(tmp_path / "moved-original-folder"),sample_names=[],output_dir_var="OUTPUT_DIR")
    runtime = generate_v8_config(prepared,str(tmp_path / "new"))
    from pathlib import Path
    script = Path(runtime).read_text(encoding="utf-8")
    assert 'LEGACY_IMPORT_MANIFEST_PATH <-' in script
    assert 'downstream_from_reduction' in script
    assert 'moved-original-folder' not in script
    contract = json.loads((tmp_path / "new" / "stage_contract.json").read_text(encoding="utf-8"))
    assert contract["signature_schema_version"] == 2
    assert contract["validated_legacy_import"]["manifest_sha256"] == file_sha256(manifest)
    rds.write_bytes(b"changed")
    with pytest.raises(ValueError,match="内容"):
        preflight_registered_inputs(prepared)
