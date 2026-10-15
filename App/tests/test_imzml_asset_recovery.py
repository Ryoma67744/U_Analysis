"""★ ver81.0: 復元失敗・競合・途中停止でも旧解析と既存資産を保持する。"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import input_preparation as ip
from app.services import imzml_asset_recovery as recovery
from tests.test_restore_imzml_assets import saved, _lose_assets, _result_bytes, _fake_convert


def _forbid(*args, **kwargs):
    pytest.fail("軽量診断は原本hash・XML解析・変換を実行しない")


def test_light_inspection_missing_and_present_never_hash_raw(saved, monkeypatch):
    entry = saved["entry"]
    monkeypatch.setattr(ip, "fingerprint", _forbid)
    monkeypatch.setattr(ip, "validate_asset", _forbid)
    manifest = {"files": [entry]}
    report = recovery.inspect_requirements(manifest, cache_root=saved["cache"])
    assert not report["needs_recovery"] and report["files"][0]["status"] == "present"
    _lose_assets(saved)
    before = _result_bytes(saved["rds"])
    report = recovery.inspect_requirements(manifest, cache_root=saved["cache"])
    assert report["can_restore"] and report["files"][0]["status"] == "missing"
    assert set(report["files"][0]["missing"]) == {"parquet", "manifest", "receipt"}
    assert not Path(entry["runtime_path"]).exists()
    assert _result_bytes(saved["rds"]) == before


def test_explicit_empty_selection_does_not_expand_to_all(saved):
    report = recovery.inspect_requirements({"files": [saved["entry"]]}, file_ids=[], cache_root=saved["cache"])
    assert report["files"] == [] and not report["can_restore"]
    with pytest.raises(ip.InputPreparationError, match="登録されていません"):
        recovery.inspect_requirements({"files": [saved["entry"]]}, file_ids=["unknown"])


def test_receipt_only_restored_even_without_raw_and_converter(saved, monkeypatch):
    from app.services import imzml_recovery_runtime as runtime
    entry = saved["entry"]
    target = Path(entry["runtime_path"]).parent
    (target / ip.RECEIPT).unlink()
    saved["raw"].unlink()
    saved["raw"].with_suffix(".ibd").unlink()
    monkeypatch.setattr(runtime, "generate", _forbid)
    monkeypatch.setattr(ip, "fingerprint", _forbid)
    before = _result_bytes(saved["rds"])
    report = recovery.inspect_requirements({"files": [entry]}, cache_root=saved["cache"])
    assert report["can_restore"] and report["files"][0]["status"] == "receipt_missing"
    from tools import restore_imzml_assets as cli
    diagnosed = cli.run(saved["rds"], cache_root=saved["cache"])
    assert diagnosed["files"][0]["status"] == "needs_restore"
    result = recovery.recover_entry(entry, cache_root=saved["cache"])
    assert result == entry
    receipt = ip.validate_asset(result)
    assert receipt["restored"] and receipt["recovery"]["receipt_only"]
    assert _result_bytes(saved["rds"]) == before
    assert list(target.parent.glob(".corrupt-*"))


def test_partial_target_stays_in_place_until_replacement_validated(saved, monkeypatch):
    from app.services import imzml_validation
    entry = saved["entry"]
    target = Path(entry["runtime_path"]).parent
    sidecar = Path(entry["conversion_manifest_path"])
    original_parquet = Path(entry["runtime_path"]).read_bytes()
    sidecar.unlink()
    def fail_after_reading_old(xml, output, **kwargs):
        assert target.is_dir()
        assert Path(entry["runtime_path"]).read_bytes() == original_parquet
        assert not sidecar.exists()
        output.write_bytes(b"incomplete")
        raise OSError("conversion failed")
    monkeypatch.setattr(imzml_validation, "checked_import", fail_after_reading_old)
    before = _result_bytes(saved["rds"])
    with pytest.raises(OSError, match="conversion failed"):
        recovery.recover_entry(entry, cache_root=saved["cache"])
    assert Path(entry["runtime_path"]).read_bytes() == original_parquet
    assert not sidecar.exists() and not list(target.parent.glob(".corrupt-*"))
    assert _result_bytes(saved["rds"]) == before
    assert not list(target.parent.glob(".stage-recovery-*"))


def test_source_change_during_conversion_never_published(saved, monkeypatch):
    from app.services import imzml_validation
    _lose_assets(saved)
    def changed(xml, output, **kwargs):
        result = _fake_convert(xml, output, **kwargs)
        xml.write_bytes(xml.read_bytes() + b"new")
        return result
    monkeypatch.setattr(imzml_validation, "checked_import", changed)
    with pytest.raises(ip.InputPreparationError, match="原本内容"):
        recovery.recover_entry(saved["entry"], cache_root=saved["cache"])
    assert not Path(saved["entry"]["runtime_path"]).exists()


def test_cancel_before_publish_preserves_partial_target(saved):
    entry = saved["entry"]
    Path(entry["conversion_manifest_path"]).unlink()
    before = Path(entry["runtime_path"]).read_bytes()
    cancelled = False
    def progress(event):
        nonlocal cancelled
        if event["stage"] == "publish":
            cancelled = True
    with pytest.raises(ip.PreparationCancelled):
        recovery.recover_entry(entry, cache_root=saved["cache"], cancel=lambda: cancelled, progress=progress)
    assert Path(entry["runtime_path"]).read_bytes() == before
    assert not Path(entry["conversion_manifest_path"]).exists()


def test_same_target_two_workers_generate_once(saved, monkeypatch):
    from app.services import imzml_validation
    _lose_assets(saved)
    calls = []
    def convert(*args, **kwargs):
        calls.append(1)
        return _fake_convert(*args, **kwargs)
    monkeypatch.setattr(imzml_validation, "checked_import", convert)
    barrier = threading.Barrier(2)
    def run():
        barrier.wait(timeout=5)
        return recovery.recover_entry(saved["entry"], cache_root=saved["cache"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        assert all(f.result(timeout=20) == saved["entry"] for f in futures)
    assert calls == [1]
    ip.validate_asset(saved["entry"])


def test_publish_error_rolls_back_and_next_attempt_recovers(saved, monkeypatch):
    entry = saved["entry"]
    Path(entry["conversion_manifest_path"]).unlink()
    target = Path(entry["runtime_path"]).parent
    before = Path(entry["runtime_path"]).read_bytes()
    rename = recovery.os.rename
    def fail_stage(source, destination):
        if Path(source).name.startswith(".stage-recovery-"):
            raise OSError("publish interruption")
        return rename(source, destination)
    with monkeypatch.context() as patch:
        patch.setattr(recovery.os, "rename", fail_stage)
        with pytest.raises(OSError, match="publish interruption"):
            recovery.recover_entry(entry, cache_root=saved["cache"])
    assert Path(entry["runtime_path"]).read_bytes() == before
    assert not Path(entry["conversion_manifest_path"]).exists()
    recovery.recover_entry(entry, cache_root=saved["cache"])
    ip.validate_asset(entry)
    assert json.loads(recovery._journal_path(target).read_text())["state"] == "committed"


def test_abrupt_stop_between_renames_reconciles_before_retry(saved):
    entry = saved["entry"]
    target = Path(entry["runtime_path"]).parent
    Path(entry["conversion_manifest_path"]).unlink()
    backup = target.with_name(f".corrupt-{target.name[:12]}-test")
    stage = target.with_name(f".stage-recovery-{target.name[:12]}-test")
    stage.mkdir()
    (stage / "unfinished").write_bytes(b"temp")
    target.rename(backup)
    ip.write_json(recovery._journal_path(target), {
        "conversion_key": entry["conversion_key"], "target": str(target),
        "stage": str(stage), "backup": str(backup), "state": "prepared"})
    recovery.recover_entry(entry, cache_root=saved["cache"])
    ip.validate_asset(entry)
    assert not stage.exists()


def test_published_asset_reconciles_pending_journal_before_healthy_fast_path(saved):
    entry = saved["entry"]
    target = Path(entry["runtime_path"]).parent
    stage = target.with_name(f".stage-recovery-{target.name[:12]}-test")
    backup = target.with_name(f".corrupt-{target.name[:12]}-test")
    backup.mkdir()
    (backup / "old").write_bytes(b"keep")
    ip.write_json(recovery._journal_path(target), {
        "conversion_key": entry["conversion_key"], "target": str(target),
        "stage": str(stage), "backup": str(backup), "state": "prepared"})
    recovery.recover_entry(entry, cache_root=saved["cache"])
    assert json.loads(recovery._journal_path(target).read_text())["state"] == "committed"
    assert (backup / "old").read_bytes() == b"keep"


def test_journal_path_tampering_is_rejected_without_deleting_foreign_dir(saved):
    entry = saved["entry"]
    target = Path(entry["runtime_path"]).parent
    Path(entry["conversion_manifest_path"]).unlink()
    foreign = saved["cache"].parent / "unrelated"
    foreign.mkdir()
    keep = foreign / "keep"
    keep.write_bytes(b"keep")
    ip.write_json(recovery._journal_path(target), {
        "conversion_key": entry["conversion_key"], "target": str(target),
        "stage": str(foreign), "backup": str(foreign), "state": "prepared"})
    with pytest.raises(ip.InputPreparationError, match="journal"):
        recovery.recover_entry(entry, cache_root=saved["cache"])
    assert keep.read_bytes() == b"keep"


@pytest.mark.parametrize("field", ["conversion_key", "runtime_path", "source_fingerprint"])
def test_inconsistent_descriptor_does_not_create_target(saved, field):
    entry = deepcopy(saved["entry"])
    _lose_assets(saved)
    if field == "conversion_key":
        entry[field] = "e" * 64
    elif field == "runtime_path":
        entry[field] = str(saved["cache"].parent / "evil.parquet")
    else:
        entry[field]["xml"]["path"] = str(saved["raw"].with_name("other.imzML"))
    with pytest.raises(ip.InputPreparationError):
        recovery.recover_entry(entry, cache_root=saved["cache"])
    assert not Path(saved["entry"]["runtime_path"]).exists()


def test_disk_full_does_not_remove_partial_target(saved, monkeypatch):
    entry = saved["entry"]
    Path(entry["conversion_manifest_path"]).unlink()
    usage = shutil.disk_usage(saved["cache"])
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage._replace(free=0))
    with pytest.raises(ip.InputPreparationError, match="空き容量"):
        recovery.recover_entry(entry, cache_root=saved["cache"])
    assert Path(entry["runtime_path"]).is_file()
