"""Registration queue boundaries: frozen input, ownership and durable terminal state."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import uuid

import pytest

from app.services import imzml_registration_queue as queue
from app.services.imzml_spatial_layout import build_spatial_layout, strip_runtime_layout
from app.services.input_preparation import InputPreparationError


@pytest.fixture
def setup_queue(tmp_path, monkeypatch):
    from app.services import imzml_spatial_layout, imzml_validation
    layout = strip_runtime_layout(build_spatial_layout(
        [(1, 1, 1), (2, 1, 1)], include_preview=False))
    monkeypatch.setattr(imzml_spatial_layout, "inspect_imzml_spatial_layout",
                        lambda path: deepcopy(layout))
    monkeypatch.setattr(imzml_validation, "inspect_spectral_preflight",
                        lambda path: {"status": "ok", "pixels": 2})
    root = tmp_path / "queue"
    paths = []
    for name in ("first", "second"):
        path = tmp_path / f"{name}.imzML"
        path.write_text("synthetic XML", encoding="utf-8")
        path.with_suffix(".ibd").write_bytes(b"synthetic binary")
        paths.append(path)
    draft = queue.inspect_paths(paths, uuid.uuid4().hex, "Owner", "project", root=root)
    rows = queue.table_rows(draft)
    for row in rows:
        row.update(subject_id="Mouse_01", group="Control")
    snapshot = queue.confirm_snapshot(draft, rows, 0, generation=draft["id"],
        revision=draft["revision"], owner="Owner", project_id="project")
    return SimpleNamespace(root=root, paths=paths, draft=draft, rows=rows, snapshot=snapshot)


def _job(setup):
    return queue.enqueue(setup.snapshot, root=setup.root)


def _completed(entry, suffix=""):
    return {**entry, "conversion_key": "asset-" + entry["file_id"] + suffix,
            "runtime_path": str(entry["path"]) + ".parquet"}


def test_confirm_rejects_stale_generation_revision_project_and_missing_sections(setup_queue):
    s = setup_queue
    base = dict(generation=s.draft["id"], revision=s.draft["revision"], owner="Owner",
                project_id="project")
    for changed in ({"generation": uuid.uuid4().hex}, {"revision": "old"}, {"project_id": "other"}):
        with pytest.raises(ValueError):
            queue.confirm_snapshot(s.draft, s.rows, 0, **{**base, **changed})
    with pytest.raises(ValueError):
        queue.confirm_snapshot(s.draft, s.rows[:-1], 0, **base)
    with pytest.raises(ValueError):
        queue.confirm_snapshot(s.draft, s.rows + s.rows[:1], 0, **base)


@pytest.mark.parametrize("ppm", [None, True, -1, float("nan"), float("inf")])
def test_invalid_ppm_never_enters_queue(setup_queue, ppm):
    s = setup_queue
    with pytest.raises(ValueError):
        queue.confirm_snapshot(s.draft, s.rows, ppm, generation=s.draft["id"],
            revision=s.draft["revision"], owner="Owner", project_id="project")


def test_source_change_before_confirm_requires_reinspection(setup_queue):
    s = setup_queue
    s.paths[0].with_suffix(".ibd").write_bytes(b"different source")
    with pytest.raises(ValueError, match="原本"):
        queue.confirm_snapshot(s.draft, s.rows, 0, generation=s.draft["id"],
            revision=s.draft["revision"], owner="Owner", project_id="project")


def test_spatial_edit_preserves_other_files_table_metadata(setup_queue):
    s = setup_queue
    rows = deepcopy(s.rows)
    rows[1].update(section_display_name="Other slice", subject_id="Mouse_02", group="Treatment")
    first = s.draft["entries"][0]
    changed = deepcopy(s.snapshot["entries"][0]["registered_sections"])
    changed[0].update(section_display_name="Edited slice", subject_id="Mouse_03")
    revised = queue.revise_draft(s.draft["id"], "Owner", s.draft["revision"], {
        first["path"]: {"coordinate_hash": first["spatial_layout"]["coordinate_hash"],
                        "registered_sections": changed}}, rows=rows, root=s.root)
    table = queue.table_rows(queue.get_draft(s.draft["id"], "Owner", root=s.root))
    assert table[0]["section_display_name"] == "Edited slice"
    assert table[0]["subject_id"] == "Mouse_03"
    assert {key: table[1][key] for key in queue.METADATA} == {
        "section_display_name": "Other slice", "subject_id": "Mouse_02", "group": "Treatment"}
    assert revised["revision"] != s.draft["revision"]
    accepted = queue.confirm_snapshot(revised, table, 0, generation=revised["id"],
        revision=revised["revision"], owner="Owner", project_id="project")
    assert accepted["entries"][1]["registered_sections"][0]["subject_id"] == "Mouse_02"


def test_admission_is_idempotent_and_snapshot_is_frozen(setup_queue):
    s = setup_queue
    identity = _job(s)
    assert queue.enqueue(deepcopy(s.snapshot), root=s.root) == identity
    s.snapshot["entries"][0]["registered_sections"][0]["group"] = "Changed later"
    request = json.loads((s.root / "jobs" / identity / "request.json").read_text(encoding="utf-8"))
    assert request["entries"][0]["registered_sections"][0]["group"] == "Control"
    assert len(queue.list_jobs("Owner", "project", root=s.root)) == 1


def test_poll_read_finishes_before_atomic_replacement(tmp_path, monkeypatch):
    """An active Windows reader must not make a worker's publication fail."""
    path = tmp_path / "state.json"
    queue.write_json(path, {"state": "queued"})
    reading, release, writer_started, replacing = (threading.Event() for _ in range(4))
    original_read = Path.read_text
    original_write = queue._atomic_write_json

    def held_read(current, *args, **kwargs):
        if current != path:
            return original_read(current, *args, **kwargs)
        with current.open(encoding="utf-8") as stream:
            reading.set()
            assert release.wait(5), "test did not release the active reader"
            return stream.read()

    def observed_write(current, value):
        replacing.set()
        return original_write(current, value)

    def publish():
        writer_started.set()
        queue.write_json(path, {"state": "running"})

    monkeypatch.setattr(Path, "read_text", held_read)
    monkeypatch.setattr(queue, "_atomic_write_json", observed_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(queue._read, path)
        try:
            assert reading.wait(5)
            writer = pool.submit(publish)
            assert writer_started.wait(5)
            assert not replacing.wait(0.1), "replacement raced an open polling reader"
        finally:
            release.set()
        assert reader.result(timeout=5) == {"state": "queued"}
        writer.result(timeout=5)
    assert queue._read(path) == {"state": "running"}


def test_admission_recovers_after_state_write_failure(setup_queue, monkeypatch):
    s = setup_queue
    original = queue.write_json
    failed = []
    def write(path, data):
        if path.name == "state.json" and not failed:
            failed.append(True)
            raise OSError("simulated state write failure")
        return original(path, data)
    monkeypatch.setattr(queue, "write_json", write)
    with pytest.raises(OSError):
        _job(s)
    identity = _job(s)
    assert queue.get_job(identity, "Owner", root=s.root)["state"] == "queued"
    assert (s.root / "jobs" / identity / "request.json").is_file()


def test_legacy_request_without_state_can_be_recovered(setup_queue):
    s = setup_queue
    identity = _job(s)
    (s.root / "jobs" / identity / "state.json").unlink()
    assert _job(s) == identity
    assert queue.get_job(identity, "Owner", root=s.root)["state"] == "queued"


def test_failed_file_does_not_discard_completed_sibling_and_retry_is_new_attempt(setup_queue):
    s = setup_queue
    identity = _job(s)
    calls = []
    def prepare(entry, **kwargs):
        calls.append(entry["file_id"])
        assert kwargs["alignment_ppm"] == 0
        assert kwargs["spec"] == entry["conversion_spec"]
        if len(calls) == 1:
            raise ValueError("first file failure")
        kwargs["progress"]({"stage": "convert", "done": 1, "total": 2})
        return _completed(entry)
    state = queue.run_job(identity, root=s.root, preparer=prepare)
    assert state["state"] == "completed_with_errors"
    assert [f["state"] for f in state["files"]] == ["failed", "completed"]
    retry = queue.retry_job(identity, "Owner", token="one-click", root=s.root)
    assert retry != identity
    assert queue.retry_job(identity, "Owner", token="one-click", root=s.root) == retry
    retry_state = queue.run_job(retry, root=s.root, preparer=lambda entry, **kwargs: _completed(entry))
    assert retry_state["state"] == "completed" and len(retry_state["files"]) == 1
    assert len(queue.completed_assets("Owner", "project", root=s.root)) == 2
    with pytest.raises(ValueError):
        queue.retry_job(retry, "Owner", token="second-click", root=s.root)


def test_verified_source_identity_survives_progress_and_file_failure(setup_queue):
    s = setup_queue
    identity = _job(s)
    verified = {}
    def prepare(entry, **kwargs):
        evidence = {"stage": "source_verified", "source_path": entry["path"],
                    "source_ibd": str(Path(entry["path"]).with_suffix(".ibd")),
                    "source_xml_sha256": "a" * 64, "source_ibd_sha256": "b" * 64}
        verified[entry["file_id"]] = deepcopy(evidence)
        kwargs["progress"](evidence)
        evidence["source_xml_sha256"] = "caller changed this object"
        kwargs["progress"]({"stage": "convert", "done": 1, "total": 2})
        persisted = queue.get_job(identity, "Owner", root=s.root, recover=False)
        item = next(f for f in persisted["files"] if f["file_id"] == entry["file_id"])
        assert item["progress"]["stage"] == "convert"
        assert item["source_identity"] == verified[entry["file_id"]]
        if entry["file_id"] == s.snapshot["entries"][0]["file_id"]:
            raise ValueError("conversion failed after hashing")
        return _completed(entry)
    state = queue.run_job(identity, root=s.root, preparer=prepare)
    assert state["state"] == "completed_with_errors"
    assert [item["state"] for item in state["files"]] == ["failed", "completed"]
    for item in queue.get_job(identity, "Owner", root=s.root)["files"]:
        assert item["source_identity"] == verified[item["file_id"]]


def test_queued_cancel_never_calls_preparer(setup_queue):
    s = setup_queue
    identity = _job(s)
    queue.cancel_job(identity, "Owner", root=s.root)
    state = queue.run_job(identity, root=s.root,
                          preparer=lambda *a, **k: pytest.fail("cancelled job started"))
    assert state["state"] == "cancelled"
    assert all(item["state"] == "cancelled" for item in state["files"])


def test_cancel_after_publish_keeps_asset_and_does_not_start_next_file(setup_queue):
    s = setup_queue
    identity = _job(s)
    calls = []
    def prepare(entry, **kwargs):
        calls.append(entry["file_id"])
        queue.cancel_job(identity, "Owner", root=s.root)
        return _completed(entry)  # atomic publication has already succeeded
    state = queue.run_job(identity, root=s.root, preparer=prepare)
    assert len(calls) == 1
    assert state["state"] == "cancelled"
    assert [f["state"] for f in state["files"]] == ["completed", "cancelled"]
    assert len(queue.completed_assets("Owner", "project", root=s.root)) == 1


def test_cooperative_cancel_interrupts_current_file_and_remaining_files(setup_queue):
    s = setup_queue
    identity = _job(s)
    calls = []
    def prepare(entry, **kwargs):
        calls.append(entry["file_id"])
        queue.cancel_job(identity, "Owner", root=s.root)
        assert kwargs["cancel"]()
        kwargs["progress"]({"stage": "convert", "done": 1, "total": 2})
        pytest.fail("cancelled progress should stop conversion")
    state = queue.run_job(identity, root=s.root, preparer=prepare)
    assert len(calls) == 1 and state["state"] == "cancelled"
    assert all(f["state"] == "cancelled" for f in state["files"])
    assert not queue.completed_assets("Owner", "project", root=s.root)


def test_source_change_before_worker_fails_only_changed_file(setup_queue):
    s = setup_queue
    identity = _job(s)
    s.paths[0].write_text("modified since admission", encoding="utf-8")
    calls = []
    def prepare(entry, **kwargs):
        calls.append(entry["file_id"])
        return _completed(entry)
    state = queue.run_job(identity, root=s.root, preparer=prepare)
    assert len(calls) == 1
    assert [f["state"] for f in state["files"]] == ["failed", "completed"]
    assert "原本" in state["files"][0]["error"]


def test_changed_converter_spec_never_prepares_accepted_snapshot(setup_queue, monkeypatch):
    s = setup_queue
    identity = _job(s)
    monkeypatch.setattr(queue, "conversion_spec", lambda **kwargs: {"contract": "new"})
    state = queue.run_job(identity, root=s.root,
                         preparer=lambda *a, **k: pytest.fail("stale spec executed"))
    assert all(item["state"] == "failed" for item in state["files"])
    assert "変換仕様" in state["files"][0]["error"]


def test_job_owner_and_project_scope_are_enforced(setup_queue):
    s = setup_queue
    identity = _job(s)
    for operation in (lambda: queue.get_job(identity, "Other", root=s.root),
                      lambda: queue.cancel_job(identity, "Other", root=s.root),
                      lambda: queue.get_draft(s.draft["id"], "Other", root=s.root)):
        with pytest.raises(PermissionError):
            operation()
    assert queue.list_jobs("Other", "project", root=s.root) == []
    assert queue.list_jobs("Owner", "OtherProject", root=s.root) == []
    assert queue.cancel_job(identity, "Admin", admin=True, root=s.root)["state"] == "cancelled"


def test_recovery_marks_lost_process_interrupted_and_preserves_completed_file(setup_queue, monkeypatch):
    from app.services import process_control
    s = setup_queue
    identity = _job(s)
    state = queue.get_job(identity, root=s.root)
    state.update(state="running", pid=999999, process_started_at=1)
    state["files"][0].update(state="completed", result=_completed(s.snapshot["entries"][0]))
    state["files"][1]["state"] = "converting"
    queue.write_json(s.root / "jobs" / identity / "state.json", state)
    monkeypatch.setattr(process_control, "matching_process", lambda *args: False)
    recovered = queue.get_job(identity, "Owner", root=s.root)
    assert recovered["state"] == "interrupted"
    assert [f["state"] for f in recovered["files"]] == ["completed", "interrupted"]
    assert len(queue.completed_assets("Owner", "project", root=s.root)) == 1


def test_handoff_accepts_only_completed_verified_exact_asset(setup_queue, monkeypatch):
    from app.services import tims_parquet_contract
    s = setup_queue
    identity = _job(s)
    with pytest.raises(ValueError):
        queue.handoff_assets(["not-complete"], "Owner", "project", root=s.root)
    queue.run_job(identity, root=s.root, preparer=lambda entry, **kwargs: _completed(entry))
    assets = queue.completed_assets("Owner", "project", root=s.root)
    key = next(iter(assets))
    verified = []
    monkeypatch.setattr(queue, "validate_asset", lambda entry: verified.append(entry["conversion_key"]))
    registry = deepcopy(s.snapshot["entries"][0]["registered_sections"])
    monkeypatch.setattr(tims_parquet_contract, "read_registered_sections", lambda path: registry)
    result = queue.handoff_assets([key], "Owner", "project", root=s.root)
    assert verified == [key]
    assert result["conversion_keys"] == [key]
    assert result["paths"] == [assets[key]["entry"]["runtime_path"]]
    with pytest.raises(ValueError):
        queue.handoff_assets([key, key], "Owner", "project", root=s.root)
    monkeypatch.setattr(queue, "validate_asset", lambda entry: (_ for _ in ()).throw(
        InputPreparationError("asset changed")))
    with pytest.raises(InputPreparationError, match="asset changed"):
        queue.handoff_assets([key], "Owner", "project", root=s.root)


def _isolate_launcher(monkeypatch):
    monkeypatch.setitem(sys.modules, "app.services.analysis_runner", SimpleNamespace(
        start_analysis_process=lambda *a, **k: pytest.fail("real worker started"),
        _cancel_watchdog=lambda pid: None))
    monkeypatch.setattr(queue.threading, "Thread", lambda *a, **k: SimpleNamespace(start=lambda: None))


def test_dispatch_preserves_cancellation_during_launch(setup_queue, monkeypatch):
    s = setup_queue
    identity = _job(s)
    _isolate_launcher(monkeypatch)
    def launch(*args, **kwargs):
        queue.cancel_job(identity, "Owner", root=s.root)
        return {"success": True, "process": SimpleNamespace(pid=os.getpid()),
                "log_file_handle": None}
    queue.dispatch_once(root=s.root, launcher=launch)
    state = queue.get_job(identity, root=s.root, recover=False)
    assert state["state"] == "cancelling"
    assert (s.root / "jobs" / identity / "cancel").exists()
    finished = queue.run_job(identity, root=s.root,
                            preparer=lambda *a, **k: pytest.fail("cancelled launch converted"))
    assert finished["state"] == "cancelled"


def test_dispatch_immediate_process_exit_is_interrupted_not_relaunched(setup_queue, monkeypatch):
    import psutil
    s = setup_queue
    identity = _job(s)
    _isolate_launcher(monkeypatch)
    monkeypatch.setattr(psutil, "Process", lambda pid: (_ for _ in ()).throw(psutil.NoSuchProcess(pid)))
    calls = []
    def launch(*args, **kwargs):
        calls.append(True)
        return {"success": True, "process": SimpleNamespace(pid=999999, poll=lambda: 2, wait=lambda: 2),
                "log_file_handle": None}
    queue.dispatch_once(root=s.root, launcher=launch)
    state = queue.get_job(identity, root=s.root, recover=False)
    assert state["state"] == "interrupted"
    assert all(item["state"] == "interrupted" for item in state["files"])
    queue.dispatch_once(root=s.root, launcher=launch)
    assert len(calls) == 1
