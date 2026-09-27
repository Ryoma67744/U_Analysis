"""★ ver74.0: 科学的な名称・手法・実行記録と潜在ヘルパー不具合の回帰試験。"""
import base64
import io
import hashlib
import json
import os
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.services import gpt_api, methods_text, molinfo_attach, naming_policy
from app.services import hne_persistence, pseudobulk, stability
from app.utils import deg_utils, raster


def _main(folder, name="sample"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.parquet"
    pd.DataFrame({"id": [1], "x": [1], "y": [1], "mz_100.0000": [5.0]}).to_parquet(path)
    return path


def _sidecar(source, name):
    path = source.with_name(source.stem + "_feature_annotations.parquet")
    pd.DataFrame({"mz": [100.], "compound": [name], "adduct": ["[M+H]+"]}).to_parquet(path)
    return path


def _peaklist(tmp_path):
    path = tmp_path / "new.sef"
    path.write_text(json.dumps({"version": "2", "peaklist": {"intervals": [
        {"lower": 99.999, "upper": 100.001, "name": "NewCompound | [M+H]+ | 0ppm"}
    ]}}))
    return path


@pytest.mark.parametrize("old_name", [None, "OldCompound"])
def test_posthoc_annotation_updates_selected_names_without_rewriting_snapshot(tmp_path, old_name):
    from app.services.seurat_bridge import SeuratBridge
    source = _main(tmp_path / "data")
    if old_name:
        _sidecar(source, old_name)
    out = tmp_path / "results"
    naming_policy.copy_selected_feature_annotations([source], out)
    original_files = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    original_input = source.read_bytes()
    rds = out / "RDS_Files" / "sample.rds"
    cache = tmp_path / "viewer_cache"
    SeuratBridge()._load_feature_annotations(cache, rds, ["mz_100.0000"])
    result = molinfo_attach.attach_molecular_info(
        {"data_folder": str(source.parent), "last_result_dir": str(out)}, _peaklist(tmp_path))
    assert result["status"] == "ok"
    records = naming_policy.resolve_feature_annotations(
        naming_policy.find_annotation_sidecars(rds), ["mz_100.0000"])
    assert records["mz_100.0000"]["compound"] == "NewCompound"
    assert source.read_bytes() == original_input
    assert all(p.read_bytes() == content for p, content in original_files.items())
    # 再openも同じ有効版を参照し、元の解析時名称を削除しない。
    reopened = SeuratBridge()._load_feature_annotations(cache, rds, ["mz_100.0000"])
    assert reopened["mz_100.0000"]["compound"] == "NewCompound"


def test_posthoc_annotation_does_not_update_unselected_same_basename(tmp_path):
    a = _main(tmp_path / "a")
    b = _main(tmp_path / "b")
    _sidecar(a, "A")
    _sidecar(b, "B")
    out = tmp_path / "results"
    naming_policy.copy_selected_feature_annotations([b], out)
    molinfo_attach.attach_molecular_info(
        {"data_folder": str(a.parent), "last_result_dir": str(out)}, _peaklist(tmp_path))
    records = naming_policy.resolve_feature_annotations(
        naming_policy.find_annotation_sidecars(out / "data.rds"), ["100.0000"])
    assert records["100.0000"]["compound"] == "B"


def test_posthoc_annotation_keeps_other_selected_input_and_source_history(tmp_path):
    a = _main(tmp_path / "a")
    b = _main(tmp_path / "b")
    previous = _sidecar(a, "A").read_bytes()
    _sidecar(b, "B")
    out = tmp_path / "results"
    naming_policy.copy_selected_feature_annotations([a, b], out)
    molinfo_attach.attach_molecular_info(
        {"data_folder": str(a.parent), "last_result_dir": str(out)}, _peaklist(tmp_path))
    records = naming_policy.resolve_feature_annotations(
        naming_policy.find_annotation_sidecars(out / "data.rds"), ["100.0000"])
    assert {r["compound"] for r in records["100.0000"]["candidates"]} == {"NewCompound", "B"}
    assert previous in [p.read_bytes() for p in (a.parent / "feature_annotation_history").glob("*.parquet")]
    overlay = json.loads((out / "feature_annotation_overlays.json").read_text())
    effective = pd.read_csv(out / overlay["effective_sources_csv"])
    assert set(effective["compound"]) == {"NewCompound", "B"}
    assert overlay["history"][0]["provenance"]["peaklist_sha256"]


def test_new_analysis_snapshot_does_not_inherit_old_annotation_overlay(tmp_path):
    source = _main(tmp_path / "data")
    _sidecar(source, "A")
    out = tmp_path / "results"
    naming_policy.copy_selected_feature_annotations([source], out)
    molinfo_attach.attach_molecular_info(
        {"data_folder": str(source.parent), "last_result_dir": str(out)}, _peaklist(tmp_path))
    _sidecar(source, "NextAnalysis")
    naming_policy.copy_selected_feature_annotations([source], out)
    records = naming_policy.resolve_feature_annotations(
        naming_policy.find_annotation_sidecars(out / "data.rds"), ["100.0000"])
    assert records["100.0000"]["compound"] == "NextAnalysis"


def test_failed_overlay_publication_keeps_previous_viewer_names_and_reports_failure(tmp_path, monkeypatch):
    from app.utils import file_locks
    source = _main(tmp_path / "data")
    _sidecar(source, "Before")
    out = tmp_path / "results"
    naming_policy.copy_selected_feature_annotations([source], out)
    snapshots = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    def fail_publish(*args, **kwargs):
        raise OSError("publication failed")
    monkeypatch.setattr(file_locks, "atomic_write_json", fail_publish)
    with pytest.raises(OSError, match="publication failed"):
        molinfo_attach.attach_molecular_info(
            {"data_folder": str(source.parent), "last_result_dir": str(out)}, _peaklist(tmp_path))
    assert all(p.read_bytes() == content for p, content in snapshots.items())
    records = naming_policy.resolve_feature_annotations(
        naming_policy.find_annotation_sidecars(out / "data.rds"), ["100.0000"])
    assert records["100.0000"]["compound"] == "Before"


@pytest.mark.parametrize("kind", ["csv", "rds"])
@pytest.mark.parametrize("requested,folder", [("RPCA", "RPCA"), ("pca (uncorrected)", "pca_uncorrected")])
def test_wrong_method_in_legacy_deg_index_is_rejected(tmp_path, monkeypatch, kind, requested, folder):
    (tmp_path / "Harmony").mkdir()
    (tmp_path / folder).mkdir()
    wrong = tmp_path / "Harmony" / f"deg_markers.{kind}"
    rows = [{"gene": "WRONG", "cluster": 0, "avg_log2FC": 1., "p_val_adj": .001}]
    if kind == "csv":
        pd.DataFrame(rows).to_csv(wrong, index=False)
    else:
        wrong.touch()
        monkeypatch.setattr(deg_utils, "read_deg_rds", lambda p: rows)
    pd.DataFrame([{**rows[0], "gene": "RIGHT"}]).to_csv(
        tmp_path / folder / "markers_annotated.csv", index=False)
    (tmp_path / "deg_index.json").write_text(json.dumps({"version": 1, "deg_results": {
        folder: {"type": kind, "path": str(wrong.relative_to(tmp_path))}}}))
    result = deg_utils.load_deg_results(tmp_path, requested)
    assert result and result[0]["gene"] == "RIGHT"


def test_hne_sample_images_do_not_collide_and_legacy_remains_readable(tmp_path):
    rds = tmp_path / "sample.rds"
    data = ["data:image/png;base64," + base64.b64encode(b).decode() for b in (b"image A", b"image B")]
    image_dir = hne_persistence.hne_image_dir(rds)
    image_dir.mkdir()
    (image_dir / "Slice_1.png").write_bytes(b"legacy")
    first = hne_persistence.save_hne_image(rds, "Slice 1", data[0])
    second = hne_persistence.save_hne_image(rds, "Slice_1", data[1])
    assert first != second
    assert hne_persistence.load_hne_image_b64(rds, first) == data[0]
    assert hne_persistence.load_hne_image_b64(rds, second) == data[1]
    assert (image_dir / "Slice_1.png").read_bytes() == b"legacy"
    replacement = hne_persistence.save_hne_image(rds, "Slice 1", data[1])
    assert replacement != first
    assert hne_persistence.load_hne_image_b64(rds, first) == data[0]


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_export_options_alone_never_claim_roi_export_or_qea_submission(lang):
    c = {"interactive": {"hne_export_options": {"intensity_repr": "linear", "unit": "compound", "include_qea": True}}}
    assert methods_text._sec_roi(c, lang) is None


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_de_options_alone_never_claim_a_completed_analysis(lang):
    c = {"rds_path": "/out/Harmony.rds", "interactive": {"onthefly_de": {"mode": "global", "target_clusters": []}}}
    assert methods_text._sec_onthefly(c, lang) is None


@pytest.mark.parametrize("with_qea", [False, True])
def test_roi_receipt_uses_saved_files_and_survives_settings_change(tmp_path, with_qea):
    from app.services.provenance import collect_conditions
    from app.utils.label_persistence import save_interactive_settings
    rds = tmp_path / "data.rds"
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as bundle:
        bundle.writestr("RPCA/intensity_matrix_compound.csv", "Group,X\nS1_ROI_cluster0,1\n")
        if with_qea:
            bundle.writestr("RPCA/exploratory_QEA_cluster_all_raw.csv", "Sample,Class,X\nS1,0,1\n")
    saved = hne_persistence.save_metaboanalyst_bytes(rds, "export.zip", content.getvalue())
    assert hne_persistence.record_hne_export_receipt(rds, saved, ["RPCA", "Harmony"], "linear", "compound")
    save_interactive_settings("hne_export_options", {"intensity_repr": "counts", "unit": "mz", "include_qea": not with_qea}, rds)
    c = collect_conditions(rds_path=rds, integration_method="RPCA")
    for lang in ("ja", "en"):
        text = methods_text.prose_to_text([methods_text._sec_roi(c, lang)], lang)
        assert "linearized" in text if lang == "en" else "対数変換を戻した線形強度" in text
        assert ("QEA" in text) == with_qea
        assert "submitted" not in text and "投入した" not in text
        assert "H&E" not in text
    c["integration_method"] = "Harmony"
    assert methods_text._sec_roi(c, "ja") is None
    assert not hne_persistence.record_hne_export_receipt(rds, tmp_path / "missing.zip", ["RPCA"], "counts", "mz")


@pytest.mark.parametrize("qea_available", [False, True])
def test_hne_export_callback_records_only_successful_saved_output(tmp_path, monkeypatch, qea_available):
    from app.callbacks import hne_overlay_callbacks as cb, interactive_callbacks as ic
    from app.services.provenance import collect_conditions
    rds = tmp_path / "data.rds"
    rds.touch()
    state = {"plot_data": pd.DataFrame({"Sample": ["S1", "S1"], "CellID": ["a", "b"],
        "SpatialX": [0., 1.], "SpatialY": [0., 1.], "Cluster": ["0", "1"]}),
        "feature_annotations": {"mz_100.0000": {"compound": "X", "mz": 100.}}}
    hne_persistence.save_hne_overlay_sample(rds, "S1", {"polygons": [{"name": "ROI", "coord_space": "msi",
        "vertices": [[-1., -1.], [2., -1.], [2., 2.], [-1., 2.]]}]})
    monkeypatch.setattr(cb, "_get_state", lambda p: state)
    monkeypatch.setattr(ic._bridge, "extract_data", lambda p: state)
    monkeypatch.setattr(ic._bridge, "export_region_cluster_means", lambda p, g, **kw:
        pd.DataFrame({"Group": g["Group"].tolist(), "mz_100.0000": [1., 2.]}))
    if not qea_available:
        from app.services import metaboanalyst_qea
        monkeypatch.setattr(metaboanalyst_qea, "build_qea_bundle", lambda *a: {"README.txt": "No eligible classes"})
    args = ({"n": 1}, str(rds), None, "linear", "compound", {"RPCA": str(rds)}, "RPCA", ["RPCA"], ["qea"])
    response = cb.hne_export_stage_b(*args)
    assert response[3] == "完了", response[1]
    conditions = collect_conditions(rds_path=rds, integration_method="RPCA")
    assert bool(conditions["interactive"]["hne_export_receipts"]["RPCA"]["qea_files"]) == qea_available
    assert ("QEA用CSV同梱" in response[1]) == qea_available
    # 同じZIPを再利用しても実施記録は同一の内容hashを持つ。
    digest = conditions["interactive"]["hne_export_receipts"]["RPCA"]["archive_sha256"]
    assert cb.hne_export_stage_b(*args)[3] == "完了"
    assert collect_conditions(rds_path=rds)["interactive"]["hne_export_receipts"]["RPCA"]["archive_sha256"] == digest


@pytest.mark.parametrize("legacy_cache", [False, True])
def test_hne_counts_failure_does_not_export_linear_values_as_raw_counts(tmp_path, monkeypatch, legacy_cache):
    from app.callbacks import hne_overlay_callbacks as cb, interactive_callbacks as ic
    from app.services.provenance import collect_conditions
    rds = tmp_path / "data.rds"
    rds.touch()
    cache = tmp_path / "cache"
    cache.mkdir()
    pd.DataFrame({"CellID": ["a", "b"], "mz_100.0000": [1., 2.]}).to_parquet(cache / "expression_matrix.parquet")
    state = {"plot_data": pd.DataFrame({"Sample": ["S1", "S1"], "CellID": ["a", "b"],
        "SpatialX": [0., 1.], "SpatialY": [0., 1.], "Cluster": ["0", "1"]}), "cache_dir": str(cache)}
    hne_persistence.save_hne_overlay_sample(rds, "S1", {"polygons": [{"name": "ROI", "coord_space": "msi",
        "vertices": [[-1., -1.], [2., -1.], [2., 2.], [-1., 2.]]}]})
    old_zip = None
    if legacy_cache:
        # ver73で実際に使われたkey規約を固定し、countsと誤記された旧ZIPを再現する。
        raw = "|".join([str(rds), str(os.path.getmtime(rds)),
            str(os.path.getmtime(hne_persistence.hne_state_path(rds))), "{}",
            "repr=counts", "unit=mz", "methods=RPCA", "fmt=zip", "lblfmt=cluster",
            "assaysrc=measured_v1", "roi=mixed_msi_v1", "qea=0"])
        key = hashlib.md5(raw.encode()).hexdigest()[:16]
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as bundle:
            bundle.writestr("RPCA/intensity_matrix_mz.csv", "Group,X\nS1,1.718281828\n")
        old_zip = hne_persistence.save_metaboanalyst_bytes(rds, "metaboanalyst_counts_mz.zip", content.getvalue())
        hne_persistence.save_export_cache_key(rds, "metaboanalyst_counts_mz.zip", key)
    monkeypatch.setattr(cb, "_get_state", lambda p: state)
    monkeypatch.setattr(ic._bridge, "extract_data", lambda p: state)
    def no_counts(*args, **kwargs):
        raise RuntimeError("counts unavailable")
    monkeypatch.setattr(ic._bridge, "export_region_cluster_means", no_counts)
    response = cb.hne_export_stage_b({"n": 1}, str(rds), str(cache), "counts", "mz",
        {"RPCA": str(rds)}, "RPCA", ["RPCA"], [])
    assert response[3] == "失敗", response[1]
    assert "生countsを取得できません" in response[1]
    assert not collect_conditions(rds_path=rds)["interactive"].get("hne_export_receipts")
    assert list(tmp_path.glob("metaboanalyst_exports/*.zip")) == ([Path(old_zip)] if old_zip else [])


def test_de_success_receipt_is_not_replaced_by_later_options(tmp_path, monkeypatch):
    from app.callbacks import interactive_de as cb, interactive_callbacks as ic
    from app.utils.label_persistence import save_interactive_settings
    from app.services.provenance import collect_conditions
    rds = str(tmp_path / "data.rds")
    monkeypatch.setattr(ic, "_set_active_key", lambda p: None)
    monkeypatch.setattr(ic._bridge, "run_differential_expression", lambda *a, **kw:
        pd.DataFrame({"gene": ["X"], "avg_log2FC": [1.], "p_val_adj": [.001]}))
    records, message = cb.run_onthefly_de(1, ["a", "b", "c"], "global", [], "exclude", rds)
    assert len(records) == 1
    save_interactive_settings("onthefly_de", {"mode": "local", "target_clusters": ["WRONG"]}, rds)
    c = collect_conditions(rds_path=rds)
    receipt = c["interactive"][f"onthefly_de_receipt::{rds}"]
    assert receipt["status"] == "complete" and receipt["mode"] == "global"
    text = methods_text.prose_to_text([methods_text._sec_onthefly(c, "ja")], "ja")
    assert "選択領域以外の全測定点" in text and "WRONG" not in text
    other = collect_conditions(rds_path=str(tmp_path / "other_method.rds"))
    assert methods_text._sec_onthefly(other, "ja") is None


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("lang", ["ja", "en"])
def test_methods_use_only_requested_method_cluster_names(reverse, lang):
    entries = [("cluster_name_map::RPCA", {"0": "RPCA Brain"}),
               ("cluster_name_map::Harmony", {"0": "Harmony Liver"})]
    c = {"integration_method": "RPCA", "interactive": dict(reversed(entries) if reverse else entries)}
    text = methods_text.prose_to_text([methods_text._sec_cluster(c, lang)], lang)
    assert "RPCA Brain" in text and "Harmony Liver" not in text


def test_all_openapi_parameters_have_valid_locations_types_and_defaults():
    spec = gpt_api.build_openapi_spec("https://example.invalid")
    for path, operations in spec["paths"].items():
        for operation in operations.values():
            for parameter in operation.get("parameters", []):
                assert parameter["in"] in {"query", "path", "header", "cookie"}, (path, parameter)
                schema = parameter["schema"]
                if isinstance(schema.get("default"), bool):
                    assert schema["type"] == "boolean", (path, parameter)
    params = spec["paths"]["/api/gpt/projects/{pid}/sub/{sid}/exports/interactive"]["post"]["parameters"]
    p = next(p for p in params if p["name"] == "exclude_unused")
    assert p["in"] == "query" and p["schema"] == {"type": "boolean", "default": True}


def test_full_openapi_schema_validates():
    # 契約CIではvalidator欠落をskipにせず依存エラーとして止める。
    from openapi_spec_validator import validate
    validate(gpt_api.build_openapi_spec("https://example.invalid"))


def test_irregular_rotated_coordinates_do_not_snap_to_grid():
    points = np.array([[0, 0], [1, 0], [0, 1], [1, 1]])
    theta = np.pi / 6
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    points = np.round(points @ rotation.T, 5)
    assert raster.grid_index(points[:, 0], points[:, 1]) is None
    assert raster.bin_to_grid(points[:, 0], points[:, 1], np.arange(4)) is None


@pytest.mark.parametrize("theta", [0, np.pi / 2, np.pi, 3 * np.pi / 2])
def test_valid_axis_aligned_grid_keeps_coordinates(theta):
    points = np.array([[0, 0], [1, 0], [0, 1], [1, 1]])
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    points = np.round(points @ rotation.T, 5)
    ix, iy, xc, yc = raster.grid_index(points[:, 0], points[:, 1])
    np.testing.assert_allclose(np.column_stack([xc[ix], yc[iy]]), points, atol=1e-10)


def test_pseudobulk_rejects_multiple_rows_for_one_independent_sample():
    meta = pd.DataFrame({"Sample": ["a", "a", "b", "b"], "Cluster": ["0", "1", "0", "1"]})
    pb = pseudobulk.aggregate_pseudobulk(meta, np.array([[1.], [2.], [9.], [10.]]), ["Sample", "Cluster"])
    with pytest.raises(ValueError, match="1.*sample|1.*サンプル|重複"):
        pseudobulk.sample_level_test(pb, {"a": "control", "b": "case"})


@pytest.mark.parametrize("n,k", [(8, 5), (8, 4), (3, 2), (2, 1), (1, 1), (10, 0)])
def test_trustworthiness_invalid_neighborhood_is_nan(n, k):
    x = np.arange(n * 2, dtype=float).reshape(n, 2)
    assert np.isnan(stability.trustworthiness(x, x, n_neighbors=k))


def test_trustworthiness_duplicate_coordinates_excludes_self():
    x = np.array([[0., 0.], [0., 0.], [1., 1.], [2., 2.], [4., 4.], [8., 8.]])
    assert stability.trustworthiness(x, x, n_neighbors=2) == 1.
