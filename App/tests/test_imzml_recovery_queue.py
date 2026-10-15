"""★ ver81.0: 復元専用queueの固定要求・認可・競合・取消・死亡時leaseの回帰検査。"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import psutil
import pytest

from app.services import imzml_recovery_queue as q
from app.services import process_control as pc
from app.services.input_preparation import InputPreparationError, PreparationCancelled


@pytest.fixture
def setup_queue(tmp_path, monkeypatch):
    root = tmp_path / "queue"
    result = tmp_path / "result"
    result.mkdir()
    rds = result / "old.rds"
    rds.write_bytes(b"old-RDS")
    params = result / "analysis_params.json"
    params.write_text('{"old":true}', encoding="utf-8")
    entries = [{"file_id": f"file-{index}", "path": str(tmp_path / f"raw{index}.imzML"),
                "conversion_key": str(index + 1) * 64, "runtime_path": str(tmp_path / f"asset{index}" / "a.parquet"),
                "conversion_spec": {"old": True}, "metadata_overlay": {"group": "edited"}}
               for index in range(2)]
    snapshot = {"project_id": "p1", "sub_project_id": "s1", "scope": str(result),
                "context_id": "view-a", "revision": q.capture_revision([rds, params]),
                "manifest": {"files": entries}, "file_ids": [item["file_id"] for item in entries]}
    lease = tmp_path / "heavy_job.json"
    monkeypatch.setattr(pc, "admission_path", lambda: lease)
    pc.record_lease(SimpleNamespace(pid=os.getpid(), poll=lambda: None), root, "tester")
    return SimpleNamespace(root=root, result=result, rds=rds, params=params, entries=entries,
                           snapshot=snapshot, lease=lease, owner="trusted-session-owner")


def _enqueue(s, **kwargs):
    return q.enqueue(s.snapshot, owner=s.owner, root=s.root, **kwargs)


def _state(s, identity):
    return q.get_job(identity, s.owner, scope=s.snapshot["scope"], root=s.root)


def test_admission_is_atomic_idempotent_and_keeps_old_manifest(setup_queue):
    s = setup_queue
    before = deepcopy(s.snapshot)
    identity = _enqueue(s)
    assert _enqueue(s) == identity
    reopened = deepcopy(s.snapshot)
    reopened["context_id"] = "new-view"
    assert q.enqueue(reopened, owner=s.owner, root=s.root) == identity
    assert s.snapshot == before
    s.snapshot["manifest"]["files"][0]["conversion_spec"]["old"] = False
    saved = q._read(s.root / "jobs" / identity / "request.json")
    assert saved["manifest"] == before["manifest"]
    assert _state(s, identity)["revision"] == before["revision"]
    assert _state(s, identity)["file_ids"] == before["file_ids"]
    assert len(list((s.root / "jobs").glob("*"))) == 1


def test_failed_admission_does_not_publish_partial_request(setup_queue, monkeypatch):
    s = setup_queue
    writer = q.write_json
    def fail(path, value):
        if Path(path).name == "state.json":
            raise OSError("disk full")
        return writer(path, value)
    monkeypatch.setattr(q, "write_json", fail)
    with pytest.raises(OSError):
        _enqueue(s)
    assert not list((s.root / "jobs").glob("*/request.json"))


def test_authorization_scope_and_identity_reject_tampering(setup_queue):
    s = setup_queue
    identity = _enqueue(s)
    with pytest.raises(PermissionError):
        q.get_job(identity, "other", root=s.root)
    with pytest.raises(PermissionError):
        q.cancel_job(identity, s.owner, scope="other-result", root=s.root)
    with pytest.raises(ValueError):
        q.get_job("../outside", s.owner, root=s.root)
    assert q.list_jobs("other", s.snapshot["scope"], root=s.root) == []
    assert len(q.list_jobs(s.owner, s.snapshot["scope"], root=s.root)) == 1


@pytest.mark.parametrize("change", ["empty", "duplicate", "unknown", "not-pinned"])
def test_only_saved_unique_pinned_entries_can_be_enqueued(setup_queue, change):
    s = setup_queue
    if change == "empty": s.snapshot["file_ids"] = []
    elif change == "duplicate": s.snapshot["file_ids"] *= 2
    elif change == "unknown": s.snapshot["file_ids"] = ["other"]
    else: s.snapshot["manifest"]["files"][0].pop("conversion_key")
    with pytest.raises(ValueError):
        _enqueue(s)


def test_revision_changed_before_admission_or_worker_never_converts(setup_queue):
    s = setup_queue
    identity = _enqueue(s)
    s.params.write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(InputPreparationError):
        _enqueue(s)
    state = q.run_job(identity, root=s.root, recoverer=lambda *a, **kw: pytest.fail("conversion must not run"))
    assert state["state"] == "failed"
    assert "変わりました" in state["error"]


def test_worker_snapshot_progress_and_success_preserve_original_files(setup_queue):
    s = setup_queue
    original = {str(path): path.read_bytes() for path in (s.rds, s.params)}
    frozen = deepcopy(s.snapshot)
    seen = []
    def recover(entry, **kwargs):
        seen.append(deepcopy(entry))
        assert not kwargs["cancel"]()
        kwargs["progress"]({"stage": "verify_source", "done": 1, "total": 1})
        kwargs["progress"]({"stage": "publish"})
        entry["changed_only_local_copy"] = True
        return entry
    identity = _enqueue(s)
    state = q.run_job(identity, root=s.root, recoverer=recover)
    assert state["state"] == "completed"
    assert all(item["state"] == "completed" for item in state["files"])
    assert seen == frozen["manifest"]["files"]
    assert s.snapshot == frozen
    assert all(Path(path).read_bytes() == data for path, data in original.items())


def test_one_file_failure_does_not_discard_another_success(setup_queue):
    s = setup_queue
    def recover(entry, **kwargs):
        if entry["file_id"] == "file-0":
            raise InputPreparationError("old dependency unavailable")
        return entry
    state = q.run_job(_enqueue(s), root=s.root, recoverer=recover)
    assert state["state"] == "completed_with_errors"
    assert [item["state"] for item in state["files"]] == ["failed", "completed"]


def test_cancel_before_dispatch_never_calls_recovery(setup_queue):
    s = setup_queue
    identity = _enqueue(s)
    assert q.cancel_job(identity, s.owner, root=s.root)["state"] == "cancelled"
    assert q.run_job(identity, root=s.root, recoverer=lambda *a, **k: pytest.fail("cancelled"))["state"] == "cancelled"


def test_cancel_after_publish_keeps_complete_asset_and_does_not_start_next(setup_queue):
    s = setup_queue
    identity = _enqueue(s)
    calls = []
    def recover(entry, **kwargs):
        calls.append(entry["file_id"])
        q.cancel_job(identity, s.owner, root=s.root)
        kwargs["progress"]({"stage": "complete"})
        return entry  # 公開直後に取消が到着した場合を再現
    state = q.run_job(identity, root=s.root, recoverer=recover)
    assert state["state"] == "cancelled"
    assert calls == ["file-0"]
    assert [item["state"] for item in state["files"]] == ["completed", "cancelled"]


def test_context_content_change_before_publish_blocks_it(setup_queue):
    s = setup_queue
    def recover(entry, **kwargs):
        s.rds.write_bytes(b"new-RDS")
        kwargs["progress"]({"stage": "publish"})
        pytest.fail("changed result must not publish")
    state = q.run_job(_enqueue(s), root=s.root, recoverer=recover)
    assert state["state"] == "failed"
    assert not any(item["state"] == "completed" for item in state["files"])


def test_retry_is_idempotent_and_revalidates_published_assets(setup_queue, monkeypatch):
    s = setup_queue
    identity = _enqueue(s)
    folder = s.root / "jobs" / identity
    state = q._read(folder / "state.json")
    state.update(state="running", pid=99999999, process_started_at=1)
    state["files"][0]["state"] = "publish"
    q.write_json(folder / "state.json", state)
    monkeypatch.setattr(q, "_live_processes", lambda state: [])
    assert _state(s, identity)["state"] == "interrupted"
    retry = q.retry_job(identity, s.owner, token="attempt-one", root=s.root)
    assert q.retry_job(identity, s.owner, token="attempt-one", root=s.root) == retry
    assert retry != identity
    verified = []
    def recover(entry, **kwargs):
        verified.append(entry["file_id"])
        kwargs["progress"]({"stage": "reuse"})
        return entry
    assert q.run_job(retry, root=s.root, recoverer=recover)["state"] == "completed"
    assert verified == ["file-0", "file-1"]


@pytest.mark.parametrize("job_child_saved", [False, True])
def test_orphan_child_holds_common_lease_and_prevents_retry(setup_queue, job_child_saved):
    s = setup_queue
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        created = psutil.Process(child.pid).create_time()
        supervisor = psutil.Process().create_time()
        pc.record_lease_child(os.getpid(), supervisor, child.pid, created)
        lease = q._read(s.lease)
        lease["process_started_at"] -= 100
        q.write_json(s.lease, lease)
        assert pc.active_lease() is not None
        identity = _enqueue(s)
        path = s.root / "jobs" / identity / "state.json"
        state = q._read(path)
        state.update(state="running", pid=os.getpid(), process_started_at=supervisor - 100,
                     children=[{"pid": child.pid, "process_started_at": created}] if job_child_saved else [])
        q.write_json(path, state)
        assert _state(s, identity)["state"] == "orphan_running"
        with pytest.raises(ValueError):
            q.retry_job(identity, s.owner, token="retry", root=s.root)
        q.cancel_job(identity, s.owner, root=s.root)
        child.wait(timeout=5)
        assert pc.active_lease() is None
        assert _state(s, identity)["state"] == "cancelled"
    finally:
        pc.terminate_tree(child.pid, timeout=.1)
        child.wait(timeout=5)


def test_child_callback_persists_both_records_before_runtime_gate(setup_queue):
    s = setup_queue
    identity = _enqueue(s)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        def recover(entry, **kwargs):
            started = psutil.Process(child.pid).create_time()
            kwargs["child_callback"](child.pid, started)
            job = q._read(s.root / "jobs" / identity / "state.json")
            lease = q._read(s.lease)
            expected = {"pid": child.pid, "process_started_at": started}
            assert expected in job["children"] and expected in lease["children"]
            return entry
        assert q.run_job(identity, root=s.root, recoverer=recover)["state"] == "completed"
    finally:
        pc.terminate_tree(child.pid, timeout=.1)
        child.wait(timeout=5)


def test_pid_reuse_and_permission_errors_do_not_release_other_process_lease(setup_queue, monkeypatch):
    s = setup_queue
    info = q._read(s.lease)
    info["process_started_at"] -= 100
    q.write_json(s.lease, info)
    assert pc.active_lease() is None
    monkeypatch.setattr(pc, "lease_process_alive", lambda *args: (_ for _ in ()).throw(psutil.AccessDenied()))
    with pytest.raises(InputPreparationError):
        pc.active_lease()


def test_direct_worker_without_common_admission_fails_closed(setup_queue):
    s = setup_queue
    s.lease.unlink()
    with pytest.raises(InputPreparationError):
        q.run_job(_enqueue(s), root=s.root, recoverer=lambda *a, **k: pytest.fail("unadmitted"))


def test_dispatch_uses_common_launcher_and_keeps_busy_reason(setup_queue):
    s = setup_queue
    identity = _enqueue(s)
    calls = []
    def launch(*args, **kwargs):
        calls.append((args, kwargs))
        return {"success": False, "message": "通常解析が実行中です。"}
    q.dispatch_once(root=s.root, launcher=launch)
    assert len(calls) == 1
    assert Path(calls[0][0][0]).name == "imzml_recovery_worker.py"
    assert calls[0][1]["interpreter"] == [sys.executable, "-u"]
    assert _state(s, identity)["state"] == "queued"
    assert "通常解析" in _state(s, identity)["wait_reason"]


def test_concurrent_double_click_creates_only_one_frozen_request(setup_queue):
    from concurrent.futures import ThreadPoolExecutor
    s = setup_queue
    with ThreadPoolExecutor(max_workers=4) as executor:
        identities = list(executor.map(lambda _: _enqueue(s), range(8)))
    assert len(set(identities)) == 1
    assert len(list((s.root / "jobs").glob("*/request.json"))) == 1


def test_completed_state_is_not_downgraded_during_poll_race(setup_queue, monkeypatch):
    s = setup_queue
    identity = _enqueue(s)
    running = q._read(s.root / "jobs" / identity / "state.json")
    running.update(state="running", pid=os.getpid(), process_started_at=psutil.Process().create_time())
    completed = dict(running, state="completed")
    reads = iter([running, completed])
    monkeypatch.setattr(q, "_read", lambda path: next(reads))
    monkeypatch.setattr(pc, "active_lease", lambda: pytest.fail("completed must not be recovered"))
    assert q._load_state(identity, s.root)["state"] == "completed"


def test_cancel_during_dispatch_does_not_start_heavy_work(setup_queue, monkeypatch):
    from filelock import FileLock, Timeout
    s = setup_queue
    identity = _enqueue(s)
    folder = s.root / "jobs" / identity
    monkeypatch.setattr(q, "_watch", lambda *args: None)
    def launch(*args, **kwargs):
        # 親のPID保存が済むまでworkerはrun.lockで待機する。
        with pytest.raises(Timeout):
            FileLock(str(folder / "run.lock"), timeout=0).acquire()
        q.cancel_job(identity, s.owner, root=s.root)
        return {"success": True, "process": SimpleNamespace(pid=os.getpid())}
    q.dispatch_once(root=s.root, launcher=launch)
    assert (folder / "cancel").exists()
    assert q.run_job(identity, root=s.root,
                     recoverer=lambda *a, **kw: pytest.fail("cancelled before start"))["state"] == "cancelled"


def test_immediate_worker_exit_is_interrupted_not_requeued(setup_queue, monkeypatch):
    s = setup_queue
    identity = _enqueue(s)
    monkeypatch.setattr(q, "_watch", lambda *args: None)
    q.dispatch_once(root=s.root, launcher=lambda *a, **kw: {
        "success": True, "process": SimpleNamespace(pid=99999999)})
    state = _state(s, identity)
    assert state["state"] == "interrupted"
    assert state["process_started_at"] is None


@pytest.mark.parametrize("state,code,status", [("completed", 0, "finished"),
                                             ("cancelled", 130, "stopped"),
                                             ("completed_with_errors", 2, "error")])
def test_worker_exit_records_are_compatible_with_common_launcher(setup_queue, monkeypatch, state, code, status):
    from tools import imzml_recovery_worker as worker
    s = setup_queue
    identity = _enqueue(s)
    monkeypatch.setattr(worker.signal, "signal", lambda *a: None)
    monkeypatch.setattr(worker, "run_job", lambda *a, **kw: {"state": state})
    assert worker.main([str(s.root), identity]) == code
    folder = s.root / "jobs" / identity
    assert (folder / "log" / "analysis_status.txt").read_text(encoding="utf-8") == status
    assert q._read(folder / "exit.json")["returncode"] == code


def test_real_worker_handles_interpreter_launcher_pid_and_records_completion(setup_queue):
    from filelock import FileLock
    s = setup_queue
    identity = _enqueue(s)
    folder = s.root / "jobs" / identity
    # 実venv起動を使う。変換本体だけを置き換え、受付・worker・保存を通す。
    script = """
import sys
from pathlib import Path
from app.services import process_control as pc, imzml_asset_recovery as core
from tools.imzml_recovery_worker import main
pc.admission_path = lambda: Path(sys.argv[3])
def reuse(entry, **kwargs):
    kwargs['progress']({'stage': 'reuse'})
    return entry
core.recover_entry = reuse
sys.exit(main(sys.argv[1:3]))
"""
    with FileLock(str(folder / "run.lock")):
        process = subprocess.Popen([sys.executable, "-c", script, str(s.root), identity, str(s.lease)],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        pc.record_lease(process, folder)
        state = q._read(folder / "state.json")
        state.update(pid=process.pid, process_started_at=psutil.Process(process.pid).create_time())
        q.write_json(folder / "state.json", state)
    try:
        output, _ = process.communicate(timeout=30)
        assert process.returncode == 0, output
        state = _state(s, identity)
        assert state["state"] == "completed"
        assert all(item["state"] == "completed" for item in state["files"])
        lease = q._read(s.lease)
        assert pc.lease_has_process(lease, state["pid"], state["process_started_at"])
        assert pc.active_lease() is None
    finally:
        pc.terminate_tree(process.pid, timeout=.1)
        process.wait(timeout=5)
