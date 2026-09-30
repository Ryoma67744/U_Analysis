from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.services.legacy_result_import import audit_result_folder, import_reductions
from app.services.result_catalog import file_sha256


class Bridge:
    def __init__(self, *, conflict=False, changed=False):
        self.conflict = conflict
        self.changed = changed

    def extract_data(self, path, **kwargs):
        assert kwargs["force_verify"]
        sha = file_sha256(path)
        result = {"result_descriptor": {
            "result_id": "source-pca", "classification": {"method": "PCA", "reduction": "pca",
                "state": "conflict" if self.conflict else "inferred"},
            "source": {"sha256": sha, "n_cells": 12},
            "reductions": {"pca": {"n_dims": 5, "assay": "Spatial", "n_cells": 12}},
            "capabilities": {"resume": not self.conflict, "independent_comparison": False}}}
        if self.changed:
            Path(path).write_bytes(b"changed-after-audit")
        return result


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "original"
    (root / "RDS_Files").mkdir(parents=True)
    (root / "RDS_Files" / "Step2_HarmonyPCA_Result.rds").write_bytes(b"legacy-pca")
    (root / "analysis_params.json").write_text(json.dumps({"runtime_parameters": {
        "data_folder": "/old/Linux/input", "input_normalized": True,
        "reduction_signature": "old", "umap_dims_n": 5}}))
    return root


def test_read_only_audit_uses_contents_not_filename(source):
    before = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    result = audit_result_folder(source, bridge=Bridge())
    assert result["artifacts"][0]["descriptor"]["classification"]["method"] == "PCA"
    assert result["artifacts"][0]["recommended_action"] == "downstream"
    assert before == {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}


def test_import_retains_bytes_without_promoting_old_signature(source, tmp_path):
    target = tmp_path / "new"
    result = import_reductions(source, target, bridge=Bridge())
    assert (target / "RDS_Files" / "Step2_PCA_uncorrected.rds").read_bytes() == b"legacy-pca"
    assert result["manifest"]["artifacts"][0]["n_dims"] == 5
    assert result["manifest"]["consistency"]["state"] == "single_origin_unconfirmed"
    assert result["manifest"]["consistency"]["input_run_binding"] == "unknown"
    assert result["manifest"]["consistency"]["limitations"]
    runtime = json.loads((target / "analysis_params.json").read_text(encoding="utf-8"))["runtime_parameters"]
    assert runtime["legacy_signatures"]["reduction_signature"] == "old"
    assert "reduction_signature" not in runtime
    assert runtime["validated_legacy_import"]["manifest_sha256"] == file_sha256(target / "legacy_import.json")
    from app.services.project_result_refs import describe_result_reference
    assert describe_result_reference(target)["purposes"] == ["reduction"]


@pytest.mark.parametrize("mode", ["conflict", "changed", "missing_method"])
def test_invalid_import_does_not_publish_partial_folder(source, tmp_path, mode):
    target = tmp_path / "new"
    bridge = Bridge(conflict=mode == "conflict", changed=mode == "changed")
    with pytest.raises(ValueError):
        import_reductions(source, target, bridge=bridge,
                          methods=["Harmony"] if mode == "missing_method" else None)
    assert not target.exists()
    assert not list(tmp_path.glob(".ua-import-*"))


def test_refuses_existing_or_nested_destination(source, tmp_path):
    for target in (source, source / "copy"):
        with pytest.raises(ValueError):
            import_reductions(source, target, bridge=Bridge())


class MultipleBridge:
    def __init__(self, cells=None, runs=None, requests=None):
        self.cells = cells or {"PCA": "c" * 64, "Harmony": "c" * 64}
        self.runs = runs or {"PCA": "run-one", "Harmony": "run-one"}
        self.requests = requests or {"PCA": "a" * 64, "Harmony": "b" * 64}

    def extract_data(self, path, **kwargs):
        method = "Harmony" if "Harmony" in Path(path).name else "PCA"
        reduction = method.lower()
        descriptor = {
            "result_id": "result-" + method,
            "classification": {"method": method, "reduction": reduction, "state": "recorded"},
            "source": {"sha256": file_sha256(path), "n_cells": 12,
                       "cell_set_sha256": self.cells.get(method),
                       "cell_order_sha256": ("d" if method == "PCA" else "e") * 64},
            "reductions": {reduction: {"n_dims": 5, "assay": "Spatial", "n_cells": 12}},
            "capabilities": {"resume": True}}
        return {"result_descriptor": descriptor, "meta": {"result_facts": {"result_provenance": {
            "run_id": self.runs.get(method), "stages": {"reduction": {
                "request_signature": self.requests.get(method)}}}}}}


@pytest.fixture
def multi_source(source):
    rds = source / "RDS_Files"
    (rds / "Step2_HarmonyPCA_Result.rds").write_bytes(b"harmony")
    (rds / "Step2_PCA_uncorrected.rds").write_bytes(b"pca")
    runtime = {"run_id": "run-one", "signature_schema_version": 2,
               "stage_signatures": {"input": "f" * 64, "pca": "a" * 64, "harmony": "b" * 64}}
    (source / "analysis_params.json").write_text(json.dumps({"runtime_parameters": runtime}))
    methods = {}
    for method, name in (("pca", "Step2_PCA_uncorrected.rds"), ("harmony", "Step2_HarmonyPCA_Result.rds")):
        path = "RDS_Files/" + name
        methods[method] = {"rds_path": path, "run_id": "run-one", "stages": {"reduction": {
            "status": "complete", "rds_path": path, "artifact_sha256": file_sha256(source / path)}}}
    (source / "analysis_methods.json").write_text(json.dumps({"schema_version": 2, "run_id": "run-one", "methods": methods}))
    return source


def test_multiple_import_requires_recorded_common_input_run_and_same_cell_set(multi_source, tmp_path):
    result = import_reductions(multi_source, tmp_path / "coherent", bridge=MultipleBridge())
    consistency = result["manifest"]["consistency"]
    assert consistency["state"] == "recorded_coherent"
    assert consistency["input_run_binding"] == "recorded"
    assert consistency["input_signature"] == "f" * 64
    assert consistency["cell_relation"] == "equal"
    assert consistency["limitations"] == []
    assert len(result["manifest"]["artifacts"]) == 2


@pytest.mark.parametrize("change", ["different_cells", "different_run", "missing_run",
                                  "missing_cell_hash", "wrong_request", "unsigned_manifest"])
def test_unrelated_or_unproven_multi_import_does_not_publish(multi_source, tmp_path, change):
    bridge = MultipleBridge()
    if change == "different_cells":
        bridge.cells["Harmony"] = "d" * 64
    elif change == "different_run":
        bridge.runs["Harmony"] = "another-run"
    elif change == "missing_run":
        bridge.runs["Harmony"] = None
    elif change == "missing_cell_hash":
        bridge.cells["Harmony"] = None
    elif change == "wrong_request":
        bridge.requests["Harmony"] = "z" * 64
    elif change == "unsigned_manifest":
        (multi_source / "analysis_methods.json").write_text('{}')
    target = tmp_path / "rejected"
    with pytest.raises(ValueError):
        import_reductions(multi_source, target, bridge=bridge)
    assert not target.exists()
    assert not list(tmp_path.glob(".ua-import-*"))


def test_unproven_legacy_multi_can_be_selected_one_method_at_a_time(multi_source, tmp_path):
    bridge = MultipleBridge(runs={"PCA": None, "Harmony": None})
    with pytest.raises(ValueError, match="--method"):
        import_reductions(multi_source, tmp_path / "all", bridge=bridge)
    result = import_reductions(multi_source, tmp_path / "one", methods=["PCA"], bridge=bridge)
    assert [a["method"] for a in result["manifest"]["artifacts"]] == ["PCA"]
    assert result["manifest"]["consistency"]["state"] == "single_origin_unconfirmed"


def test_changed_saved_parameters_cannot_be_published_as_audited(multi_source, tmp_path):
    class ChangedBridge(MultipleBridge):
        def extract_data(self, path, **kwargs):
            result = super().extract_data(path, **kwargs)
            with (multi_source / "analysis_params.json").open("a") as stream:
                stream.write(" ")
            return result
    target = tmp_path / "changed"
    with pytest.raises(ValueError, match="監査後"):
        import_reductions(multi_source, target, bridge=ChangedBridge())
    assert not target.exists()
    assert not list(tmp_path.glob(".ua-import-*"))
