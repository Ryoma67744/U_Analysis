"""★ ver78.0: queue worker終端と従来ログのrunning残留を防ぐ。"""
import importlib.util
import json
from pathlib import Path

import pytest

from app.services import imzml_registration_queue as queue
from app.services.input_preparation import PreparationCancelled


@pytest.fixture
def worker(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("registration_worker_test",
        Path(__file__).resolve().parents[1] / "tools" / "imzml_registration_worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)
    identity = "a" * 32
    folder = tmp_path / "jobs" / identity
    (folder / "log").mkdir(parents=True)
    (folder / "log" / "analysis_status.txt").write_text("running")
    (folder / "log" / "analysis_pid.txt").write_text("123")
    queue.write_json(folder / "state.json", {"state": "running", "files": [
        {"state": "completed", "path": "complete.imzML"},
        {"state": "converting", "path": "active.imzML"}]})
    return module, tmp_path, folder, identity


@pytest.mark.parametrize("state,code,status", [
    ("completed", 0, "finished"), ("completed_with_errors", 2, "error"),
    ("cancelled", 130, "stopped"), ("interrupted", 2, "error")])
def test_terminal_state_matches_compatibility_files(worker, monkeypatch, capsys, state, code, status):
    module, root, folder, identity = worker
    monkeypatch.setattr(module, "run_job", lambda *args, **kwargs: {"state": state})
    assert module.main([str(root), identity]) == code
    assert (folder / "log" / "analysis_status.txt").read_text() == status
    assert json.loads((folder / "exit.json").read_text()) == {"returncode": code}
    assert json.loads((folder / "state.json").read_text())["returncode"] == code
    assert (folder / "log" / "analysis_pid.txt").read_text() == "123"
    assert f"終了コード {code}" in capsys.readouterr().out


@pytest.mark.parametrize("exception,code,status,file_state", [
    (RuntimeError("worker failure"), 2, "error", "failed"),
    (PreparationCancelled("stopped"), 130, "stopped", "cancelled")])
def test_exception_is_terminal_and_keeps_completed_assets(worker, monkeypatch, exception, code, status, file_state):
    module, root, folder, identity = worker
    def fail(*args, **kwargs):
        raise exception
    monkeypatch.setattr(module, "run_job", fail)
    assert module.main([str(root), identity]) == code
    state = json.loads((folder / "state.json").read_text())
    assert state["state"] in queue.TERMINAL
    assert [item["state"] for item in state["files"]] == ["completed", file_state]
    assert (folder / "log" / "analysis_status.txt").read_text() == status
    assert json.loads((folder / "exit.json").read_text())["returncode"] == code


def test_previously_recorded_stop_is_preserved(worker, monkeypatch):
    module, root, folder, identity = worker
    (folder / "log" / "analysis_status.txt").write_text("stopped")
    monkeypatch.setattr(module, "run_job", lambda *args, **kwargs: {"state": "completed_with_errors"})
    assert module.main([str(root), identity]) == 2
    assert (folder / "log" / "analysis_status.txt").read_text() == "stopped"
