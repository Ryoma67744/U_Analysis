"""SCiLS型processed-centroid imzMLのMS1推定とm/z軸診断。"""
import io
import json
import struct
import uuid
import xml.etree.ElementTree as ET

import pytest

from app.services.imzml_validation import inspect_binary_contract, inspect_spectral_preflight
from app.services.input_preparation import InputPreparationError
from app.services.section_metadata import build_section_manifest


def _scils_pair(tmp_path, lengths=(3, 3), *, explicit=False, ms_level=None,
                add_precursor=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    identity = uuid.UUID("11111111-2222-3333-4444-555555555555")
    binary = bytearray(identity.bytes)
    root = ET.Element("mzML")

    def cv(parent, accession, value="", name=""):
        return ET.SubElement(parent, "cvParam", accession=accession,
                             value=str(value), name=name)

    content = ET.SubElement(ET.SubElement(root, "fileDescription"), "fileContent")
    cv(content, "IMS:1000080", identity)
    cv(content, "IMS:1000031", name="processed")
    cv(content, "MS:1000294", name="mass spectrum")

    groups = ET.SubElement(root, "referenceableParamGroupList")
    mz_group = ET.SubElement(groups, "referenceableParamGroup", id="mzArray")
    cv(mz_group, "MS:1000514", name="m/z array")
    cv(mz_group, "MS:1000523", name="64-bit float")
    cv(mz_group, "MS:1000576", name="no compression")
    cv(mz_group, "IMS:1000101", "true", "external data")
    int_group = ET.SubElement(groups, "referenceableParamGroup", id="intensities")
    cv(int_group, "MS:1000515", name="intensity array")
    cv(int_group, "MS:1000521", name="32-bit float")
    cv(int_group, "MS:1000576", name="no compression")
    cv(int_group, "IMS:1000101", "true", "external data")
    spectrum_group = ET.SubElement(groups, "referenceableParamGroup", id="spectrum")
    cv(spectrum_group, "MS:1000294", name="mass spectrum")
    cv(spectrum_group, "MS:1000127", name="centroid spectrum")
    if explicit:
        cv(spectrum_group, "MS:1000579", name="MS1 spectrum")

    spectra = ET.SubElement(ET.SubElement(root, "run"), "spectrumList",
                            count=str(len(lengths)))
    for index, length in enumerate(lengths):
        spectrum = ET.SubElement(spectra, "spectrum", id=f"spectrum={index}",
                                 index=str(index), defaultArrayLength="0")
        ET.SubElement(spectrum, "referenceableParamGroupRef", ref="spectrum")
        cv(spectrum, "MS:1000130", name="positive scan")
        if ms_level is not None:
            cv(spectrum, "MS:1000511", ms_level, "ms level")
        if add_precursor:
            ET.SubElement(spectrum, "precursor")
        scan = ET.SubElement(ET.SubElement(spectrum, "scanList"), "scan")
        cv(scan, "IMS:1000050", index + 1, "position x")
        cv(scan, "IMS:1000051", 1, "position y")
        arrays = ET.SubElement(spectrum, "binaryDataArrayList")
        for ref, width, code, values in (
            ("mzArray", 8, "d", [100.0 + j for j in range(length)]),
            ("intensities", 4, "f", [float(index + j + 1) for j in range(length)]),
        ):
            array = ET.SubElement(arrays, "binaryDataArray")
            ET.SubElement(array, "referenceableParamGroupRef", ref=ref)
            offset = len(binary)
            encoded = struct.pack("<" + code * length, *values)
            binary.extend(encoded)
            cv(array, "IMS:1000103", length, "external array length")
            cv(array, "IMS:1000104", len(encoded), "external encoded length")
            cv(array, "IMS:1000102", offset, "external offset")
            ET.SubElement(array, "binary")

    xml = tmp_path / "scils.imzML"
    xml.with_suffix(".ibd").write_bytes(binary)
    ET.ElementTree(root).write(xml, encoding="utf-8", xml_declaration=True)
    return xml


def test_scils_full_scan_without_ms1_tag_is_inferred(tmp_path):
    path = _scils_pair(tmp_path, (3, 3))
    preflight = inspect_spectral_preflight(path)
    assert preflight["ms_level_interpretation"]["status"] == "inferred_ms1"
    assert preflight["ms_level_interpretation"]["confidence"] == "high"
    assert preflight["mz_axis_contract"]["status"] == "common_length_unverified_values"
    meta = inspect_binary_contract(path)
    assert meta["features"] == 3
    assert meta["representation"] == "processed"


def test_explicit_ms1_remains_explicit(tmp_path):
    path = _scils_pair(tmp_path, (3, 3), explicit=True)
    preflight = inspect_spectral_preflight(path)
    assert preflight["ms_level_interpretation"]["status"] == "explicit_ms1"


def test_msn_evidence_is_rejected(tmp_path):
    path = _scils_pair(tmp_path, (3, 3), ms_level=2)
    with pytest.raises(InputPreparationError, match="MS level=2"):
        inspect_spectral_preflight(path)
    path = _scils_pair(tmp_path / "precursor", (3, 3), add_precursor=True)
    with pytest.raises(InputPreparationError, match="前駆体"):
        inspect_spectral_preflight(path)



def test_variable_length_centroid_is_a_supported_sparse_candidate(tmp_path):
    path = _scils_pair(tmp_path, (4, 3, 5))
    preflight = inspect_spectral_preflight(path)
    axis = preflight["mz_axis_contract"]
    assert axis["status"] == "processed_sparse_candidate"
    assert (axis["feature_count_min"], axis["feature_count_max"],
            axis["feature_count_unique"]) == (3, 5, 3)
    assert axis["source_peak_count"] == 12
    meta = inspect_binary_contract(path)
    assert meta["features"] is None
    assert meta["source_peak_count"] == 12
    assert meta["ms_level_interpretation"]["status"] == "inferred_ms1"



def test_spectral_preflight_survives_manifest_rebuild(tmp_path):
    path = _scils_pair(tmp_path, (3, 3))
    preflight = inspect_spectral_preflight(path)
    catalog = [{"path": str(path), "available_rois": [],
                "spectral_preflight": preflight}]
    first = build_section_manifest(catalog)
    assert first["files"][0]["spectral_preflight"] == preflight
    second = build_section_manifest([{"path": str(path), "available_rois": []}],
                                    previous=first)
    assert second["files"][0]["spectral_preflight"] == preflight
