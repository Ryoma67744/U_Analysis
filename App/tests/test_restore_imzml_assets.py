"""★ ver75.2: 既存解析を変えず、失われた固定変換資産だけを安全に復元する。"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from app.services import input_preparation as ip
from app.services import imzml_validation
from app.services.section_metadata import build_section_manifest
from tools import restore_imzml_assets as cli


def _fake_convert(xml, output, **kwargs):
    # 管理層の試験用。実Parquetではなく、原本から決定的なバイトを生成する。
    output.write_bytes(b"test-double:" + xml.read_bytes() + xml.with_suffix(".ibd").read_bytes())
    output.with_suffix(".imzml.json").write_text(json.dumps({"source": str(xml)}), encoding="utf-8")
    return {"parquet": str(output)}


def _fake_validate(path, **kwargs):
    assert path.read_bytes().startswith(b"test-double:")
    return {"test_double": True, "pixels": 4}


def _save_result(tmp_path, entry):
    root = tmp_path / "Result"
    rds = root / "RDS_Files" / "PCA.rds"
    rds.parent.mkdir(parents=True)
    rds.write_bytes(b"existing-analysis-not-to-be-opened-by-R")
    manifest = {"files": [entry]}
    (root / "analysis_params.json").write_text(json.dumps({"section_manifest": manifest}), encoding="utf-8")
    (root / "section_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "receipt.json").write_text('{"existing": true}', encoding="utf-8")
    return rds


def _result_bytes(rds):
    root = rds.parent.parent
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _assert_no_process(*args, **kwargs):
    pytest.fail("復元でR/UMAPまたは外部プロセスを開始してはいけない")


@pytest.fixture
def saved(tmp_path, monkeypatch):
    raw = tmp_path / "sample.imzML"
    raw.write_bytes(b"<test-double>not actual imzML</test-double>")
    raw.with_suffix(".ibd").write_bytes(b"original spectra")
    entry = build_section_manifest([{"path": str(raw), "available_rois": []}])["files"][0]
    entry["sections"][0].update(subject_id="Mouse01", group="Ctrl", metadata_confirmed=True)
    entry["registered_sections"] = deepcopy(entry["sections"])
    cache = tmp_path / "cache"
    monkeypatch.setattr(imzml_validation, "checked_import", _fake_convert)
    monkeypatch.setattr(imzml_validation, "validate_converted_table", _fake_validate)
    prepared = ip.prepare_imzml(entry, cache_root=cache, alignment_ppm=3.0)
    rds = _save_result(tmp_path, prepared)
    monkeypatch.setattr(subprocess, "Popen", _assert_no_process)
    return {"entry": prepared, "rds": rds, "cache": cache, "raw": raw}


def _lose_assets(saved):
    shutil.rmtree(Path(saved["entry"]["runtime_path"]).parent)


def _run(saved, **kwargs):
    return cli.run(saved["rds"], cache_root=saved["cache"], **kwargs)


def _save_entry(saved, entry):
    (saved["rds"].parent.parent / "section_manifest.json").write_text(
        json.dumps({"files": [entry]}), encoding="utf-8"
    )


def test_diagnosis_is_read_only_and_explicit_restore_recovers_exact_bytes(saved, monkeypatch):
    entry = saved["entry"]
    before = _result_bytes(saved["rds"])
    output_bytes = Path(entry["runtime_path"]).read_bytes()
    sidecar_bytes = Path(entry["conversion_manifest_path"]).read_bytes()
    _lose_assets(saved)
    calls = []
    convert = imzml_validation.checked_import

    def observed(*args, **kwargs):
        calls.append(1)
        return convert(*args, **kwargs)

    monkeypatch.setattr(imzml_validation, "checked_import", observed)
    diagnosed = _run(saved)
    assert not diagnosed["ok"]
    assert diagnosed["files"][0]["status"] == "needs_restore"
    assert diagnosed["files"][0]["restorable"]
    assert not calls
    assert not Path(entry["runtime_path"]).exists()
    assert _result_bytes(saved["rds"]) == before

    restored = _run(saved, restore=True)
    assert restored["ok"]
    assert restored["files"][0]["status"] == "restored"
    assert calls == [1]
    assert Path(entry["runtime_path"]).read_bytes() == output_bytes
    assert Path(entry["conversion_manifest_path"]).read_bytes() == sidecar_bytes
    assert ip.validate_asset(entry)["restored"] is True
    assert _result_bytes(saved["rds"]) == before


@pytest.mark.parametrize("restore", [False, True])
def test_healthy_asset_survives_deleted_raw_without_running_converter(saved, monkeypatch, restore):
    saved["raw"].unlink()
    saved["raw"].with_suffix(".ibd").unlink()
    monkeypatch.setattr(ip, "fingerprint", _assert_no_process)
    monkeypatch.setattr(ip, "conversion_spec", _assert_no_process)
    monkeypatch.setattr(ip, "prepare_imzml", _assert_no_process)
    before = _result_bytes(saved["rds"])
    report = _run(saved, restore=restore)
    assert report["ok"] and report["files"][0]["status"] == "healthy"
    assert _result_bytes(saved["rds"]) == before


@pytest.mark.parametrize("kind", ["xml", "ibd"])
@pytest.mark.parametrize("restore", [False, True])
def test_missing_assets_changed_original_is_rejected(saved, monkeypatch, kind, restore):
    _lose_assets(saved)
    path = saved["raw"] if kind == "xml" else saved["raw"].with_suffix(".ibd")
    path.write_bytes(path.read_bytes() + b"changed")
    monkeypatch.setattr(imzml_validation, "checked_import", _assert_no_process)
    before = _result_bytes(saved["rds"])
    report = _run(saved, restore=restore)
    assert not report["ok"] and report["files"][0]["status"] == "blocked"
    assert "原本内容" in report["files"][0]["error"]
    assert not Path(saved["entry"]["runtime_path"]).exists()
    assert _result_bytes(saved["rds"]) == before


@pytest.mark.parametrize("restore", [False, True])
def test_missing_assets_current_converter_drift_is_rejected(saved, monkeypatch, restore):
    _lose_assets(saved)
    original = ip.conversion_spec

    def drift(**kwargs):
        result = original(**kwargs)
        result["converter_sha256"] = "f" * 64
        return result

    monkeypatch.setattr(ip, "conversion_spec", drift)
    monkeypatch.setattr(imzml_validation, "checked_import", _assert_no_process)
    before = _result_bytes(saved["rds"])
    report = _run(saved, restore=restore)
    assert not report["ok"] and "変換仕様" in report["files"][0]["error"]
    assert not Path(saved["entry"]["runtime_path"]).exists()
    assert _result_bytes(saved["rds"]) == before


def test_converter_different_output_is_not_published(saved, monkeypatch):
    _lose_assets(saved)

    def changed(xml, output, **kwargs):
        result = _fake_convert(xml, output, **kwargs)
        output.write_bytes(output.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(imzml_validation, "checked_import", changed)
    report = _run(saved, restore=True)
    assert not report["ok"] and "hash" in report["files"][0]["error"]
    assert not Path(saved["entry"]["runtime_path"]).exists()


@pytest.mark.parametrize("key", [
    "file_id", "runtime_path", "conversion_key", "conversion_manifest_path", "validation",
    "conversion_spec", "source_fingerprint", "registered_sections",
])
def test_incomplete_pinned_descriptor_never_starts_conversion(saved, monkeypatch, key):
    _lose_assets(saved)
    entry = deepcopy(saved["entry"])
    entry.pop(key)
    _save_entry(saved, entry)
    monkeypatch.setattr(ip, "prepare_imzml", _assert_no_process)
    report = _run(saved, restore=True)
    assert not report["ok"] and report["files"][0]["status"] == "blocked"


def test_registration_revision_request_is_not_a_recovery(saved, monkeypatch):
    _lose_assets(saved)
    entry = deepcopy(saved["entry"])
    entry["registration_revision_requested"] = True
    _save_entry(saved, entry)
    monkeypatch.setattr(ip, "prepare_imzml", _assert_no_process)
    report = _run(saved, restore=True)
    assert not report["ok"] and "revision" in report["files"][0]["error"]


def test_edited_group_overlay_is_preserved_while_original_registry_is_restored(saved):
    entry = deepcopy(saved["entry"])
    entry["metadata_overlay"] = {entry["registered_sections"][0]["section_id"]: {"group": "UpdatedCtrl"}}
    _save_entry(saved, entry)
    before = _result_bytes(saved["rds"])
    _lose_assets(saved)
    assert _run(saved, restore=True)["ok"]
    assert _result_bytes(saved["rds"]) == before
    assert ip.validate_asset(entry)["validation"] == entry["validation"]


def test_file_filter_ignores_unselected_input_and_unknown_id_never_mutates(saved):
    bad = deepcopy(saved["entry"])
    bad.update(file_id="other-file", path=str(saved["raw"].with_name("missing.imzML")))
    bad.pop("conversion_key")
    (saved["rds"].parent.parent / "section_manifest.json").write_text(
        json.dumps({"files": [bad, saved["entry"]]}), encoding="utf-8"
    )
    _lose_assets(saved)
    with pytest.raises(ip.InputPreparationError, match="登録されていません"):
        _run(saved, file_ids=["unknown"], restore=True)
    assert not Path(saved["entry"]["runtime_path"]).exists()
    report = _run(saved, file_ids=[saved["entry"]["file_id"]], restore=True)
    assert report["ok"] and len(report["files"]) == 1


def test_missing_raw_and_wrong_cache_root_provide_action_without_new_directory(saved):
    _lose_assets(saved)
    other = saved["cache"].parent / "different-cache"
    report = cli.run(saved["rds"], cache_root=other, restore=True)
    assert not report["ok"] and "管理領域外" in report["files"][0]["error"]
    assert not other.exists()
    saved["raw"].with_suffix(".ibd").unlink()
    report = _run(saved, restore=True)
    assert not report["ok"] and ".ibd" in report["files"][0]["error"]
    assert "バックアップ" in report["files"][0]["action"]


def test_cli_json_and_exit_status_require_real_rds_and_explicit_restore(saved, capsys):
    assert cli.main(["--rds", str(saved["rds"].with_name("missing.rds"))]) == 2
    assert "既存" in json.loads(capsys.readouterr().out)["error"]
    _lose_assets(saved)
    args = ["--rds", str(saved["rds"]), "--cache-root", str(saved["cache"])]
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out)["mode"] == "diagnose"
    assert not Path(saved["entry"]["runtime_path"]).exists()
    assert cli.main(args + ["--restore"]) == 0
    assert json.loads(capsys.readouterr().out)["files"][0]["status"] == "restored"


def test_real_imzml_lost_asset_restored_without_analysis(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    pq = pytest.importorskip("pyarrow.parquet")
    writer_module = pytest.importorskip("pyimzml.ImzMLWriter")
    from app.services.imzml_spatial_layout import inspect_imzml_spatial_layout, default_spatial_sections
    from app.services.section_metadata import stable_file_id

    raw = tmp_path / "real.imzML"
    values = np.array([[1, 2], [3, 4]], dtype=np.float32)
    with writer_module.ImzMLWriter(str(raw), mode="continuous", spec_type="profile",
                                  mz_dtype=np.float64, intensity_dtype=np.float32) as writer:
        for xy, intensity in zip([(1, 1, 1), (2, 1, 1)], values):
            writer.addSpectrum(np.array([100.123, 200.456]), intensity, xy)
    tree = ET.parse(raw)
    for level in tree.iter():
        if level.get("accession") == "MS:1000511":
            level.set("value", "1")
    ET.register_namespace("", "http://psi.hupo.org/ms/mzml")
    tree.write(raw, encoding="utf-8", xml_declaration=True)
    layout = inspect_imzml_spatial_layout(raw)
    fid = stable_file_id(str(raw))
    sections = default_spatial_sections(fid, layout)
    sections[0].update(subject_id="Mouse1", group="Ctrl", metadata_confirmed=True, selected=True)
    entry = build_section_manifest([{
        "path": str(raw), "file_id": fid, "available_rois": [], "roi_role": "spatial",
        "spatial_layout": layout, "spatial_sections": sections, "registered_sections": sections,
    }])["files"][0]
    cache = tmp_path / "cache"
    prepared = ip.prepare_imzml(entry, cache_root=cache)
    rds = _save_result(tmp_path, prepared)
    before = _result_bytes(rds)
    expected = pq.read_table(prepared["runtime_path"]).to_pandas()
    shutil.rmtree(Path(prepared["runtime_path"]).parent)
    monkeypatch.setattr(subprocess, "Popen", _assert_no_process)
    report = cli.run(rds, cache_root=cache, restore=True)
    assert report["ok"], report
    restored = pq.read_table(prepared["runtime_path"]).to_pandas()
    assert restored.equals(expected)
    np.testing.assert_array_equal(restored[["100.123000", "200.456000"]].values, values)
    assert ip.validate_asset(prepared)["validation"] == prepared["validation"]
    assert _result_bytes(rds) == before
