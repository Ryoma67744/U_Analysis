"""手法名の誤分類、継承クラスタ、段階完了と内容差し替えの回帰。"""
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from app.services.result_contract import build_result_descriptor, descriptor_label, require_capabilities
from app.services import result_catalog as catalog


def facts(space="pca", assay="Spatial", **overrides):
    result = {"reductions": {space: {"assay": assay, "n_dims": 30, "n_cells": 3}},
              "embedding": {"kind": "umap", "space": space},
              "clusters": {"space": space}, "has_clusters": True,
              "has_spatial": True, "has_expression": True, "cluster_commands_verified": True,
              "idents_match_seurat_clusters": True}
    result.update(overrides)
    return result


def descriptor(observed=None, records=None):
    return build_result_descriptor({"path": "Step2_HarmonyPCA_Result.rds", "sha256": "abc"},
                                   observed or facts(), records)


def test_filename_harmony_does_not_override_actual_raw_pca():
    result = descriptor()
    assert result["classification"]["method"] == "PCA"
    assert result["classification"]["state"] == "inferred"
    assert result["capabilities"]["independent_comparison"]


def test_integrated_pca_is_never_uncorrected_without_rpca_evidence():
    result = descriptor(facts(assay="integrated"))
    assert result["classification"]["method"] is None
    assert not result["capabilities"]["independent_comparison"]
    confirmed = descriptor(facts(assay="integrated", has_rpca_integration=True))
    assert confirmed["classification"]["method"] == "RPCA"


@pytest.mark.parametrize("embedding_kind", ["umap", "pca2d"])
def test_inherited_harmony_clusters_cannot_claim_independent_pca(embedding_kind):
    result = descriptor(facts(embedding={"kind": embedding_kind, "space": "pca"},
                              wrapper_reduction="pca", clusters={"space": "harmony"}))
    assert result["clusters"]["kind"] == "inherited"
    assert not result["capabilities"]["independent_comparison"]
    assert "既存クラスタ" in descriptor_label(result)
    assert result["capabilities"]["pc"] == (embedding_kind == "pca2d")
    with pytest.raises(ValueError):
        require_capabilities(result, "independent_comparison")


def test_conflicting_recorded_method_and_manifest_hash_fail_closed():
    result = descriptor(facts(result_provenance={"method": "Harmony"}))
    assert result["classification"]["state"] == "conflict"
    assert not result["capabilities"]["resume"]
    result = descriptor(records={"artifact_sha256": "different"})
    assert result["classification"]["state"] == "conflict"


def test_export_failure_does_not_erase_completed_clustering():
    result = descriptor(records={"status": "failed", "stages": {
        "cluster": {"status": "complete"}, "export": {"status": "failed"}}})
    assert result["capabilities"]["spatial"]
    assert result["capabilities"]["independent_comparison"]
    partial = descriptor(records={"stages": {"cluster": {"status": "pending"}}})
    assert partial["capabilities"]["resume"]
    assert not partial["capabilities"]["spatial"]


def test_old_commands_and_identity_labels_do_not_promote_reduction_only_artifact():
    observed = facts(result_provenance={"method": "PCA", "cluster_kind": "none"},
                     clusters={"space": "harmony"})
    result = descriptor(observed)
    assert result["clusters"]["kind"] == "none"
    assert not result["capabilities"]["spatial"]
    imported = descriptor(records={"stages": {"reduction": {"status": "complete"}}})
    assert not imported["capabilities"]["spatial"]
    assert imported["capabilities"]["resume"]


def test_new_computed_provenance_supersedes_legacy_projection_marker():
    result = descriptor(facts(misc={"pca_origin": "legacy_derived"},
                              result_provenance={"method": "PCA", "cluster_kind": "computed"}))
    assert result["clusters"]["kind"] == "computed"
    assert result["capabilities"]["independent_comparison"]


def test_harmony_reduction_before_umap_can_resume_despite_pc_diagnostic():
    observed = facts("harmony", wrapper_reduction="harmony", has_clusters=False,
                     embedding={"kind": "pca2d", "space": "pca"},
                     result_provenance={"method": "Harmony", "stage": "reduction"})
    observed["reductions"]["pca"] = {"assay": "Spatial", "n_dims": 30, "n_cells": 3}
    result = descriptor(observed)
    assert result["classification"]["method"] == "Harmony"
    assert result["classification"]["reduction"] == "harmony"
    assert result["capabilities"]["resume"]
    assert result["capabilities"]["pc"]
    assert not result["capabilities"]["spatial"]


def test_unwrapped_incomplete_rpca_is_not_classified_by_pc_diagnostic():
    # ★ ver77.0: 実 RDS の facts を最小化。PC1–PC2 は表示だけで、RPCA が保存済み。
    observed = {"wrapper_reduction": None, "result_provenance": None, "misc": {},
                "reductions": {"pca": {"assay": "Spatial", "n_cells": 3, "n_dims": 30},
                               "rpca": {"assay": "Spatial", "n_cells": 3, "n_dims": 30}},
                "embedding": {"kind": "pca2d", "space": "pca", "location": "primary",
                              "origin_state": "inferred", "parameters": {"dims": [1, 2]}},
                "clusters": {"space": None}, "has_clusters": False,
                "has_spatial": True, "cell_ids_valid": True, "cluster_commands_verified": False}
    result = descriptor(observed)
    assert result["classification"]["method"] == "RPCA"
    assert result["classification"]["reduction"] == "rpca"
    assert result["classification"]["state"] == "inferred"
    assert result["embedding"]["space"] == "pca"
    assert result["capabilities"]["resume"]
    assert result["capabilities"]["pc"]
    assert not result["capabilities"]["spatial"]
    assert not result["capabilities"]["independent_comparison"]
    observed["reductions"]["harmony"] = {"assay": "Spatial", "n_cells": 3, "n_dims": 30}
    ambiguous = descriptor(observed)
    assert ambiguous["classification"]["state"] == "unknown"
    assert ambiguous["classification"]["method"] is None
    assert not ambiguous["capabilities"]["resume"]
    assert ambiguous["capabilities"]["pc"]


def test_discovery_is_metadata_only_and_corrects_verified_alias(tmp_path, monkeypatch):
    path = tmp_path / "Step2_HarmonyPCA_Result.rds"
    path.write_bytes(b"not opened by discovery")
    monkeypatch.setattr(catalog, "file_sha256", lambda *a, **k: pytest.fail("scan hashed RDS"))
    assert catalog.discover_results(tmp_path)[0]["method_key"] == "Harmony"
    catalog.remember_descriptor(path, descriptor())
    assert catalog.discover_results(tmp_path)[0]["method_key"] == "PCA"
    assert catalog.discover_results(tmp_path)[0]["result_descriptor"]["artifact_state"] == "verified"


def test_catalog_does_not_merge_child_runs_and_marks_hints_unverified(tmp_path):
    for run in ("run1", "run2"):
        folder = tmp_path / run / "RDS_Files"
        folder.mkdir(parents=True)
        (folder / "Step2_HarmonyPCA_Result.rds").touch()
    assert catalog.discover_results(tmp_path) == []
    candidate = catalog.discover_results(tmp_path / "run1")[0]
    assert candidate["display_name"] == "未確認（Harmony候補）"


def test_catalog_ignores_preprocessing_lists_but_keeps_generic_result(tmp_path):
    for name in ("Step1_SeuratList_Preprocessed.rds", "DESI_SeuratList1_bgremoved.rds", "custom_result.rds"):
        (tmp_path / name).touch()
    assert [Path(c["rds_path"]).name for c in catalog.discover_results(tmp_path)] == ["custom_result.rds"]


def test_v2_resume_uses_reduction_artifact_not_last_failed_stage(tmp_path):
    reduction = tmp_path / "reduction.rds"
    reduction.write_bytes(b"reduction")
    (tmp_path / "analysis_methods.json").write_text(json.dumps({"schema_version": 2, "methods": {
        "pca": {"status": "failed", "stages": {"reduction": {"status": "complete", "rds_path": "reduction.rds"},
                                                   "cluster": {"status": "failed"}}}}}), encoding="utf-8")
    assert catalog.resume_result_paths(tmp_path) == [str(reduction.resolve())]


def test_unique_pca_sidecar_does_not_select_rpca(tmp_path):
    source = tmp_path / "Step2_PCA_uncorrected.rds"
    source.touch()
    pca = tmp_path / "UMAP_pca_uncorrected_umap_embedding.rds"
    pca.touch()
    (tmp_path / "UMAP_rpca_umap_embedding.rds").touch()
    assert catalog.find_embedding_sidecar(source) == str(pca)


def test_manifest_selects_sidecar_even_when_primary_filename_claims_wrong_method(tmp_path):
    source = tmp_path / "Step2_HarmonyPCA_Result.rds"
    source.touch()
    sidecar = tmp_path / "UMAP_pca_umap_embedding.rds"
    sidecar.touch()
    (tmp_path / "UMAP_harmony_umap_embedding.rds").touch()
    (tmp_path / "analysis_methods.json").write_text(json.dumps({"schema_version": 2, "methods": {
        "pca": {"rds_path": source.name, "stages": {"umap": {"status": "complete", "rds_path": sidecar.name}}}}}), encoding="utf-8")
    assert catalog.find_embedding_sidecar(source) == str(sidecar)


def test_sidecar_cell_ids_alone_cannot_establish_raw_pca_origin(tmp_path):
    source, sidecar = tmp_path / "PCA.rds", tmp_path / "UMAP_pca_umap_embedding.rds"
    observed = facts(wrapper_reduction="pca", cell_ids_r_hash="same-cells",
                     embedding={"kind": "umap", "location": "sidecar", "space": "pca"})
    observed["embedding"] = catalog.verify_embedding_origin(source, sidecar, "sidecar-sha", observed, {})
    result = descriptor(observed)
    assert result["embedding"]["space"] is None
    assert result["embedding"]["origin_state"] == "unknown"
    assert not result["capabilities"]["independent_comparison"]
    assert "埋め込み由来未確認" in descriptor_label(result)
    records = {"stages": {"umap": {"status": "complete", "rds_path": sidecar.name,
        "artifact_sha256": "sidecar-sha", "numerical": {"reduction": "pca", "source_assay": "Spatial", "cell_ids_hash": "same-cells"}}}}
    verified = catalog.verify_embedding_origin(source, sidecar, "sidecar-sha", observed, records)
    assert verified["space"] == "pca"
    assert verified["origin_state"] == "recorded"
    records["stages"]["umap"]["artifact_sha256"] = "wrong-method-sidecar-sha"
    assert catalog.verify_embedding_origin(source, sidecar, "sidecar-sha", observed, records)["origin_state"] == "unknown"


def test_content_verification_invalidates_same_stat_and_uses_snapshot(tmp_path, monkeypatch):
    from app.services import seurat_bridge as module
    bridge = module.SeuratBridge()
    bridge._cache_base = tmp_path / "cache"
    source = tmp_path / "Step2_PCA_uncorrected.rds"
    source.write_bytes(b"AAAA")
    signature = module.file_signature(source)
    original_signature = module.file_signature
    monkeypatch.setattr(module, "file_signature", lambda p: signature if Path(p) == source else original_signature(p))
    monkeypatch.setattr(bridge, "_load_feature_annotations", lambda *a: {})
    extractions = []

    def extract(path, out, **kwargs):
        assert Path(path) != source
        extractions.append(Path(path).read_bytes())
        pd.DataFrame({"CellID": ["c", "a", "b"], "Cluster": ["1", "0", "1"],
                      "UMAP_1": [1, 2, 3], "UMAP_2": [0, 0, 0], "Sample": ["s"] * 3}).to_csv(out / "plot_data.csv", index=False)
        pd.DataFrame({"Cluster": ["0", "1"], "Count": [1, 2]}).to_csv(out / "cluster_stats.csv", index=False)
        (out / "extraction_meta.json").write_text(json.dumps({"result_facts": facts()}), encoding="utf-8")
    monkeypatch.setattr(bridge, "_run_extraction", extract)
    first = bridge.extract_data(str(source), force_verify=True)
    source.write_bytes(b"BBBB")
    second = bridge.extract_data(str(source), force_verify=True)
    assert extractions == [b"AAAA", b"BBBB"]
    assert first["cache_dir"] != second["cache_dir"]
    assert second["result_descriptor"]["source"]["sha256"] == hashlib.sha256(b"BBBB").hexdigest()
    assert second["result_descriptor"]["source"]["path"] == str(source.resolve())
    assert bridge.extract_data(str(source), force_verify=True)["cache_dir"] == second["cache_dir"]
    assert len(extractions) == 2


def test_missing_completion_marker_is_not_a_valid_cache(tmp_path):
    from app.services.seurat_bridge import SeuratBridge
    for name in ("plot_data.csv", "cluster_stats.csv", "extraction_meta.json"):
        (tmp_path / name).touch()
    assert not SeuratBridge()._is_cached(tmp_path)


def test_invalid_folder_invalidates_only_its_browser_scope(tmp_path):
    from app.callbacks import interactive_callbacks as ic
    from dash.exceptions import PreventUpdate
    ic._replace_load_scope("tab-a", "old")
    ic._replace_load_scope("tab-b", "other")
    assert ic.invalidate_result_folder(str(tmp_path / "missing"), "tab-a") == ({"display": "none"}, None, None, None)
    assert ic._load_is_superseded({"scope": "tab-a", "token": "old"})
    assert not ic._load_is_superseded({"scope": "tab-b", "token": "other"})
    with pytest.raises(PreventUpdate):
        ic.load_stage_b_extract({"scope": "tab-a", "token": "old"})
    assert ic.auto_scan_rds_files(str(tmp_path / "missing"), None) == ([], None, {})


def test_pc_diagnostic_builder_labels_axes_without_changing_coordinates():
    from app.callbacks.interactive_umap import _build_umap_integrated_fig
    frame = pd.DataFrame({"CellID": ["a", "b"], "Cluster": ["0", "1"], "Sample": ["s", "s"],
                          "UMAP_1": [3., 5.], "UMAP_2": [7., 11.]})
    frame.attrs["result_descriptor"] = descriptor(facts(embedding={"kind": "pca2d", "space": "pca"}))
    figure = _build_umap_integrated_fig(frame, "Cluster", None, True, False)
    assert figure.layout.xaxis.title.text == "PC1"
    assert figure.layout.yaxis.title.text == "PC2"
    assert "PC1" in figure.layout.title.text
    assert {point for trace in figure.data[:-1] for point in trace.x} == {3., 5.}


def test_scope_waits_for_browser_and_stale_folder_response_does_not_cancel_new_load(tmp_path):
    from app.callbacks import interactive_callbacks as ic
    from dash.exceptions import PreventUpdate
    source = tmp_path / "PCA.rds"
    source.touch()
    with pytest.raises(PreventUpdate):
        ic.load_stage_a_show_progress(1, "PCA", {"PCA": str(source)}, str(tmp_path), scope=None)
    first = ic.load_stage_a_show_progress(1, "PCA", {"PCA": str(source)}, str(tmp_path), scope="window-one")
    second = ic.load_stage_a_show_progress(1, "PCA", {"PCA": str(source)}, str(tmp_path), scope="window-two")
    assert first[-1] != second[-1]
    assert not ic._load_is_superseded(first[-2])
    assert not ic._load_is_superseded(second[-2])
    with pytest.raises(PreventUpdate):
        ic.invalidate_result_folder(str(tmp_path), "window-one")
    assert not ic._load_is_superseded(first[-2])


def test_conditions_use_selected_rds_parameters_and_preserve_old_record_separately(tmp_path):
    from app.services.provenance import collect_conditions
    (tmp_path / "analysis_params.json").write_text(json.dumps({"UMAP_N_NEIGHBORS": 99}), encoding="utf-8")
    observed = facts(embedding={"kind": "umap", "space": "pca", "parameters": {"n.neighbors": 10, "min.dist": .3}},
                     clusters={"space": "pca", "parameters": {"k.param": 20, "resolution": .5}})
    result = collect_conditions(result_folder=str(tmp_path), integration_method="Harmony", result_descriptor=descriptor(observed))
    assert result["integration_method"] == "PCA"
    assert result["requested_method"] == "Harmony"
    assert result["analysis"]["umap"]["n_neighbors"] == 10
    assert result["analysis"]["clustering"]["k_param"] == 20
    assert result["analysis"]["umap"]["seed"] is None
    assert "recorded_run_analysis" in result

