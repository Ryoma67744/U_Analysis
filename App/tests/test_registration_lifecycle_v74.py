"""★ ver74.0: 登録/選択/編集の実I/O状態遷移。原版のsubset停止・編集消失を検出する。"""
from copy import deepcopy
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services import input_preparation as prep
from app.services.section_metadata import build_section_manifest, validate_section_manifest


def _raw(tmp_path):
    from pyimzml.ImzMLWriter import ImzMLWriter
    from app.services.imzml_spatial_layout import inspect_imzml_spatial_layout, default_spatial_sections
    from app.services.section_metadata import stable_file_id
    path = tmp_path / "sample.imzML"
    with ImzMLWriter(str(path), mode="continuous", spec_type="centroid") as writer:
        for index, coordinate in enumerate([(1, 1, 1), (2, 1, 1), (10, 1, 1), (11, 1, 1)]):
            writer.addSpectrum(np.array([100., 200.]), np.array([index + 1., index + 2.]), coordinate)
    tree = ET.parse(path)
    for element in tree.iter():
        if element.get("accession") == "MS:1000511":
            element.set("value", "1")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    layout = inspect_imzml_spatial_layout(path)
    fid = stable_file_id(str(path))
    rows = default_spatial_sections(fid, layout)
    assert len(rows) == 2
    for index, row in enumerate(rows):
        row.update(subject_id=f"subject{index}", group="Control", metadata_confirmed=True)
    catalog = [{"path": str(path), "file_id": fid, "spatial_layout": layout, "spatial_sections": rows}]
    return catalog, build_section_manifest(catalog)


def _params(manifest):
    return {"section_manifest": manifest, "execution_policy": "section_auto_v1", "annotation_enable": False}


def test_subset_survives_real_conversion_cache_and_empty_selection(tmp_path):
    catalog, manifest = _raw(tmp_path)
    first_id = manifest["files"][0]["sections"][0]["section_id"]
    selected = build_section_manifest(catalog, {catalog[0]["path"]: [first_id]}, previous=manifest)
    params = prep.prepare_inputs(_params(selected), cache_root=tmp_path / "cache")
    entry = params["section_manifest"]["files"][0]
    assert [row["selected"] for row in entry["spatial_sections"]] == [True, False]
    assert [row["section_id"] for row in entry["sections"]] == [first_id]
    assert not validate_section_manifest(params["section_manifest"])
    assert pq.ParquetFile(entry["runtime_path"]).metadata.num_rows == 4
    cached = prep.prepare_inputs(params, cache_root=tmp_path / "cache")
    assert cached["section_manifest"]["files"][0]["selected_section_ids"] == [first_id]
    emptied = deepcopy(params)
    emptied["section_manifest"]["files"][0]["selected_section_ids"] = []
    with pytest.raises(ValueError, match="解析対象"):
        prep.prepare_inputs(emptied, cache_root=tmp_path / "cache")


def test_raw_metadata_revision_keeps_old_asset_and_reuses_spectral_core(tmp_path):
    catalog, manifest = _raw(tmp_path)
    initial = prep.prepare_inputs(_params(manifest), cache_root=tmp_path / "cache")
    first = initial["section_manifest"]["files"][0]
    old_bytes = Path(first["runtime_path"]).read_bytes()
    draft = deepcopy(first["spatial_sections"])
    draft[0].update(section_display_name="Renamed", group="Treatment", metadata_confirmed=True)
    changed_catalog = [{**catalog[0], "spatial_sections": draft}]
    changed = build_section_manifest(changed_catalog, previous=initial["section_manifest"])
    assert changed["files"][0]["registered_sections"][0]["group"] == "Control"
    assert changed["files"][0]["registration_revision_requested"]
    updated = prep.prepare_inputs(_params(changed), cache_root=tmp_path / "cache")
    second = updated["section_manifest"]["files"][0]
    assert second["sections"][0]["group"] == "Treatment"
    assert second["sections"][0]["section_display_name"] == "Renamed"
    assert second["conversion_key"] != first["conversion_key"]
    assert second["spectral_key"] == first["spectral_key"]
    receipt = json.loads(Path(second["conversion_receipt_path"]).read_text())
    assert receipt["derived_from_conversion_key"] == first["conversion_key"]
    assert Path(first["runtime_path"]).read_bytes() == old_bytes
    old_table, new_table = pq.read_table(first["runtime_path"]), pq.read_table(second["runtime_path"])
    assert old_table.select(["100.000000", "200.000000"]).equals(new_table.select(["100.000000", "200.000000"]))
    from app.services.execution_policy import prepare_execution_params
    prepare_execution_params(initial, tmp_path / "out1")
    prepare_execution_params(updated, tmp_path / "out2")
    assert initial["reduction_signature"] == updated["reduction_signature"]
    assert initial["analysis_signature"] != updated["analysis_signature"]
    assert initial["metadata_signature"] != updated["metadata_signature"]
    subset = deepcopy(updated)
    subset["section_manifest"]["files"][0]["sections"].pop()
    from app.services.execution_policy import reduction_signature
    assert reduction_signature(subset) != updated["reduction_signature"]
    ppm = {**updated, "mz_align_ppm": 1}
    assert reduction_signature(ppm) != updated["reduction_signature"]


def test_parquet_group_overlay_survives_rebuild_hydrate_and_result_save(tmp_path, monkeypatch):
    catalog, manifest = _raw(tmp_path)
    prepared = prep.prepare_inputs(_params(manifest), cache_root=tmp_path / "cache")
    raw_entry = prepared["section_manifest"]["files"][0]
    path = raw_entry["runtime_path"]
    direct_catalog = prep._catalog_from_paths_for_backend([path])
    direct = build_section_manifest(direct_catalog)
    original_registry = deepcopy(direct["files"][0]["registered_sections"])
    from app.services.section_metadata import manifest_group_rows
    rows = manifest_group_rows(direct)
    rows[0].update(section_display_name="Alias", group="Changed")
    changed = build_section_manifest(direct_catalog, group_rows=rows, previous=direct)
    from app.services.execution_policy import apply_group_rows
    immediate = apply_group_rows(direct, rows)
    assert validate_section_manifest(immediate) == validate_section_manifest(changed) == []
    assert immediate["files"][0]["metadata_overlay"] == changed["files"][0]["metadata_overlay"]
    hydrated = prep.prepare_inputs(_params(changed))["section_manifest"]
    assert hydrated["files"][0]["registered_sections"] == original_registry
    assert hydrated["files"][0]["sections"][0]["section_display_name"] == "Alias"
    assert hydrated["files"][0]["sections"][0]["roi"] == original_registry[0]["section_display_name"]
    reopened = build_section_manifest(direct_catalog, previous=hydrated)
    assert reopened["files"][0]["sections"][0]["group"] == "Changed"
    from app.services import section_group_metadata as gm
    result = tmp_path / "result"; result.mkdir()
    (result / "analysis_params.json").write_text(json.dumps({"section_manifest": hydrated}))
    monkeypatch.setattr(gm, "_result_dir", lambda _: result)
    rows[0]["group"] = "Postanalysis"
    saved = gm.save_group_edits("x.rds", rows)
    assert saved["files"][0]["registered_sections"] == original_registry
    hydrated_again = prep.prepare_inputs(_params(saved))["section_manifest"]
    assert hydrated_again["files"][0]["sections"][0]["group"] == "Postanalysis"
    assert (result / "analysis_params.json").read_text().find("Postanalysis") == -1


def test_direct_config_rejects_corrupt_standard_footer_before_template_read(tmp_path):
    catalog, manifest = _raw(tmp_path)
    prepared = prep.prepare_inputs(_params(manifest), cache_root=tmp_path / "cache")
    source = prepared["section_manifest"]["files"][0]["runtime_path"]
    table = pq.read_table(source)
    md = dict(table.schema.metadata); md.pop(b"ua_section_registry")
    broken = tmp_path / "broken.parquet"
    pq.write_table(table.replace_schema_metadata(md), broken)
    from app.services.analysis_runner import generate_v8_config, generate_cluster_filter_config
    for generate in (generate_v8_config, generate_cluster_filter_config):
        with pytest.raises(ValueError, match="registry|登録"):
            generate({"input_paths": [str(broken)], "template_path": str(tmp_path / "absent.R")}, str(tmp_path / "out"))


def test_explicit_empty_and_duplicate_ids_do_not_become_all_selected(tmp_path, monkeypatch):
    _, manifest = _raw(tmp_path)
    entry = deepcopy(manifest["files"][0])
    runtime = tmp_path / "fixture.parquet"; runtime.touch()
    entry["runtime_path"] = str(runtime)
    registry = deepcopy(entry["registered_sections"])
    monkeypatch.setattr("app.services.data_manager.read_parquet_section_registry", lambda _: registry)
    def hydrate(value):
        return prep._hydrate_parquet_registries({"files": [value]})["files"][0]
    entry["selected_section_ids"] = []
    assert hydrate(entry)["sections"] == []
    entry["selected_section_ids"] = ["unknown"]
    with pytest.raises(ValueError, match="未登録"):
        hydrate(entry)
    sid = entry["registered_sections"][0]["section_id"]
    entry["selected_section_ids"] = [sid, sid]
    with pytest.raises(ValueError, match="重複"):
        hydrate(entry)


@pytest.mark.parametrize("location", ["selection", "saved", "catalog"])
@pytest.mark.parametrize("duplicate", [False, True])
def test_manifest_rebuild_rejects_invalid_ids_before_normalization(tmp_path, location, duplicate):
    catalog, manifest = _raw(tmp_path)
    sid = manifest["files"][0]["sections"][0]["section_id"]
    bad = [sid, sid] if duplicate else ["unknown"]
    selections = None
    if location == "selection":
        selections = {catalog[0]["path"]: bad}
    elif location == "saved":
        manifest["files"][0]["selected_section_ids"] = bad
    else:
        catalog[0]["selected_section_ids"] = bad
    with pytest.raises(ValueError, match="未登録|重複"):
        build_section_manifest(catalog, selections, previous=manifest)


def test_numeric_stage_key_ignores_only_metadata_asset_hashes(tmp_path):
    from app.services import execution_policy as ep
    _, manifest = _raw(tmp_path)
    entry = manifest["files"][0]
    entry.update(conversion_key="registration1", spectral_key="same-spectra", source_fingerprint={
        "xml": {"sha256": "raw-xml"}, "ibd": {"sha256": "raw-ibd"}})
    first = _params(manifest)
    first["input_fingerprints"] = [{"path": entry["path"], "conversion_key": "registration1", "validation": {"parquet": "hash1"}}]
    changed = deepcopy(first)
    changed["section_manifest"]["files"][0]["conversion_key"] = "registration2"
    changed["input_fingerprints"][0].update(conversion_key="registration2", validation={"parquet": "hash2"})
    changed["section_manifest"]["files"][0]["sections"][0]["group"] = "New group"
    signature = getattr(ep, "reduction_signature", ep.analysis_signature)
    assert signature(first) == signature(changed)
    assert ep.analysis_signature(first) != ep.analysis_signature(changed)
    assert signature({**first, "batch_var": "group"}) != signature({**changed, "batch_var": "group"})
    for key in ("xml", "ibd"):
        source_changed = deepcopy(changed)
        source_changed["section_manifest"]["files"][0]["source_fingerprint"][key]["sha256"] += "changed"
        assert signature(first) != signature(source_changed)


def test_rds_reanalysis_cannot_add_unselected_source_sections(tmp_path):
    catalog, manifest = _raw(tmp_path)
    sid = manifest["files"][0]["sections"][0]["section_id"]
    catalog[0]["source_selected_section_ids"] = [sid]
    rebuilt = build_section_manifest(catalog, previous=manifest)
    assert rebuilt["files"][0]["source_selected_section_ids"] == [sid]
    assert any("元のRDS" in message for message in validate_section_manifest(rebuilt))
    with pytest.raises(ValueError, match="元のRDS"):
        prep.prepare_inputs(_params(rebuilt), cache_root=tmp_path / "cache")
    assert not (tmp_path / "cache").exists()
    allowed = build_section_manifest(catalog, {catalog[0]["path"]: [sid]}, previous=rebuilt)
    assert not validate_section_manifest(allowed)


def test_result_metadata_overlay_uses_pinned_asset_after_raw_move(tmp_path):
    catalog, manifest = _raw(tmp_path)
    prepared = prep.prepare_inputs(_params(manifest), cache_root=tmp_path / "cache")
    saved = prepared["section_manifest"]
    saved["source_rds_path"] = str(tmp_path / "saved.rds")
    entry = saved["files"][0]
    entry["source_selected_section_ids"] = list(entry["selected_section_ids"])
    from app.services.execution_policy import apply_group_rows
    from app.services.section_metadata import manifest_group_rows
    rows = manifest_group_rows(saved)
    rows[0].update(group="NewGroup", metadata_confirmed=True)
    edited = apply_group_rows(saved, rows)
    assert not edited["files"][0].get("registration_revision_requested")
    raw = Path(catalog[0]["path"]); raw.unlink(); raw.with_suffix(".ibd").unlink()
    restored = prep.prepare_inputs(_params(edited), cache_root=tmp_path / "cache")
    new = restored["section_manifest"]["files"][0]
    assert new["sections"][0]["group"] == "NewGroup"
    assert new["conversion_key"] == entry["conversion_key"]
    assert new["registered_sections"][0]["group"] == "Control"
