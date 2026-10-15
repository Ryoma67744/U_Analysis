"""★ ver81.0: 復元受付の権限・保存結果・画面世代を信頼境界で検証する。"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from dash import no_update
from flask import Flask, g, session
import pytest


@pytest.fixture
def ui(tmp_path, monkeypatch):
    from app.callbacks import imzml_recovery_callbacks as cb
    from app.callbacks import interactive_callbacks as interactive
    from app.services import auth_service, imzml_recovery_queue as queue
    root = tmp_path / "result"
    rds = root / "RDS_Files/PCA.rds"
    rds.parent.mkdir(parents=True)
    rds.write_bytes(b"saved RDS boundary")
    entry = {"file_id": "imzml-one", "path": str(tmp_path / "one.imzML"),
             "conversion_key": "a" * 64, "selection_mode": "all",
             "sections": [{"section_id": "selected"}],
             "registered_sections": [{"section_id": str(i)} for i in range(5)]}
    other = {"file_id": "parquet", "path": str(tmp_path / "direct.parquet"),
             "sections": [{"section_id": "parquet-section"}]}
    manifest = {"files": [entry, other]}
    record = root / "analysis_params.json"
    record.write_text(json.dumps({"section_manifest": manifest}), encoding="utf-8")
    project = {"id": "project", "created_by": "Alice"}
    sub = {"id": "sub", "last_result_dir": str(root)}
    monkeypatch.setattr(cb.project_manager, "get_project", lambda value: project if value == "project" else None)
    monkeypatch.setattr(cb.project_manager, "get_sub_project", lambda pid, sid: sub if (pid, sid) == ("project", "sub") else None)
    monkeypatch.setattr(interactive, "_detect_integration_methods", lambda folder: {"PCA": str(rds)})
    monkeypatch.setattr(auth_service, "get_password_version", lambda: 7)
    monkeypatch.setattr(queue, "queue_root", lambda: tmp_path / "jobs")
    monkeypatch.setattr(queue, "ensure_dispatcher", lambda: None)
    monkeypatch.setattr(cb, "_GRANTS", {})
    monkeypatch.setattr(cb, "_inspect", lambda snapshot: {
        "files": [{"file_id": entry["file_id"], "source_path": entry["path"], "status": "missing",
                   "reason": "変換ファイルがありません"}] if snapshot["file_ids"] else [],
        "can_restore": bool(snapshot["file_ids"]), "needs_recovery": bool(snapshot["file_ids"])})
    context = {"id": "generation-one", "project_id": "project", "sub_project_id": "sub",
               "folder": str(root), "rds": str(rds), "method": "PCA", "methods": ["PCA"],
               "selection": {"mode": "all"}, "loading": False, "shared": False}
    app = Flask(__name__)
    app.secret_key = "isolated-test-secret"
    with app.test_request_context("/"):
        session.update(analyst_name="Alice", access_tier="A", pw_version=7,
                       imzml_recovery_principal="signed-session-identity")
        yield SimpleNamespace(cb=cb, queue=queue, context=context, manifest=manifest, root=root,
                              rds=rds, record=record, project=project, sub=sub)


def _selected(ui, file_id="imzml-one", section="selected"):
    return {"mode": "selected", "scope": str(ui.root),
            "ids": ["source:" + ui.cb._digest(["source", file_id, section])]}


def test_all_and_selected_filter_only_imzml_but_keep_whole_registration(ui):
    owner, snapshot = ui.cb._authorize_context(ui.context)
    assert owner and owner != "Alice"
    assert snapshot["file_ids"] == ["imzml-one"]
    assert len(snapshot["manifest"]["files"][0]["registered_sections"]) == 5
    ui.context["selection"] = _selected(ui)
    assert ui.cb._authorize_context(ui.context)[1]["file_ids"] == ["imzml-one"]
    ui.context["selection"] = _selected(ui, "parquet", "parquet-section")
    assert ui.cb._authorize_context(ui.context)[1]["file_ids"] == []
    report, job = ui.cb.diagnose_recovery(ui.context, 0)
    assert not report["visible"] and not report["can_restore"] and job is None


@pytest.mark.parametrize("selection", [[], {"mode": "selected", "scope": "wrong", "ids": ["x"]},
                                          {"mode": "selected", "ids": []}, {"mode": "bad"}])
def test_invalid_selection_never_expands_to_all(ui, selection):
    ui.context["selection"] = selection
    with pytest.raises(ValueError):
        ui.cb._authorize_context(ui.context)


@pytest.mark.parametrize("alter", ["no-session", "tier-b", "revoked", "different-owner", "shared-request", "shared-store"])
def test_signed_session_and_modify_permission_required(ui, alter):
    if alter == "no-session":
        session.clear()
    elif alter == "tier-b":
        session["access_tier"] = "B"
    elif alter == "revoked":
        session["pw_version"] = 6
    elif alter == "different-owner":
        session["analyst_name"] = "Mallory"
    elif alter == "shared-request":
        g.ua_shared_readonly = True
    else:
        ui.context["shared"] = True
    report, job = ui.cb.diagnose_recovery(ui.context, 0)
    assert not report["can_restore"] and "nonce" not in report and job is None
    assert not ui.cb._GRANTS


@pytest.mark.parametrize("field,value", [("project_id", "other"), ("sub_project_id", "other"),
                                          ("folder", "/unregistered"), ("rds", "/other.rds"),
                                          ("methods", ["other"]), ("methods", [{}]), ("method", {})])
def test_client_path_and_method_forgery_is_rejected(ui, field, value):
    ui.context[field] = value
    report, job = ui.cb.diagnose_recovery(ui.context, 0)
    assert not report["can_restore"] and job is None and "nonce" not in report


def test_missing_priority_sidecar_is_part_of_revision_and_nonce_expires_on_creation(ui):
    report, _ = ui.cb.diagnose_recovery(ui.context, 0)
    owner, snapshot, _ = ui.cb._grant(ui.context, report["nonce"])
    sidecar = ui.root / "section_manifest.json"
    assert next(x for x in snapshot["revision"]["files"] if x["path"] == str(sidecar))["exists"] is False
    sidecar.write_text(json.dumps(ui.manifest), encoding="utf-8")
    with pytest.raises(PermissionError, match="変わりました"):
        ui.cb._grant(ui.context, report["nonce"])


def test_manifest_mutation_during_diagnosis_is_rejected(ui, monkeypatch):
    original = ui.cb.load_result_manifest
    def changing(path):
        manifest = original(path)
        ui.record.write_bytes(ui.record.read_bytes() + b" ")
        return manifest
    monkeypatch.setattr(ui.cb, "load_result_manifest", changing)
    report, _ = ui.cb.diagnose_recovery(ui.context, 0)
    assert not report["can_restore"] and "確認中" in report["summary"]


def test_nonce_cannot_be_reused_by_other_session_or_generation(ui):
    report, _ = ui.cb.diagnose_recovery(ui.context, 0)
    assert ui.cb._grant(ui.context, report["nonce"])
    with pytest.raises(PermissionError):
        ui.cb._grant({**ui.context, "id": "generation-two"}, report["nonce"])
    session["imzml_recovery_principal"] = "different-authenticated-session"
    with pytest.raises(PermissionError):
        ui.cb._grant(ui.context, report["nonce"])


def test_enqueue_poll_cancel_retry_use_fixed_server_snapshot_and_do_not_change_result(ui, monkeypatch):
    before = {p: p.read_bytes() for p in ui.root.rglob("*") if p.is_file()}
    report, _ = ui.cb.diagnose_recovery(ui.context, 0)
    monkeypatch.setattr(ui.cb, "ctx", SimpleNamespace(triggered_id="imzml_recovery_start"))
    job = ui.cb.recovery_action(1, 0, 0, ui.context, report, None)
    assert job["state"] == "queued"
    assert ui.cb.recovery_action(2, 0, 0, ui.context, report, None)["job_id"] == job["job_id"]
    owner, snapshot = ui.cb._authorize_context(ui.context)
    request = ui.queue._read(ui.queue.queue_root() / "jobs" / job["job_id"] / "request.json")
    assert request["file_ids"] == ["imzml-one"]
    assert request["manifest"] == ui.manifest
    view, fresh = ui.cb.poll_recovery(1, ui.context, report, job)
    assert view["state"] == "queued" and fresh is no_update
    monkeypatch.setattr(ui.cb, "ctx", SimpleNamespace(triggered_id="imzml_recovery_cancel"))
    cancelled = ui.cb.recovery_action(2, 1, 0, ui.context, report, job)
    assert cancelled["state"] == "cancelled"
    monkeypatch.setattr(ui.cb, "ctx", SimpleNamespace(triggered_id="imzml_recovery_retry"))
    retried = ui.cb.recovery_action(2, 1, 1, ui.context, report, cancelled)
    assert retried["state"] == "queued" and retried["job_id"] != job["job_id"]
    double_click = ui.cb.recovery_action(2, 1, 2, ui.context, report, cancelled)
    assert double_click["job_id"] == retried["job_id"]
    assert all(p.read_bytes() == data for p, data in before.items())
    assert ui.cb.poll_recovery(2, {**ui.context, "id": "new"}, report, job) == (no_update, no_update)


def test_forged_job_from_same_scope_other_project_cannot_be_cancelled(ui, monkeypatch):
    report, _ = ui.cb.diagnose_recovery(ui.context, 0)
    owner, snapshot = ui.cb._authorize_context(ui.context)
    snapshot["project_id"] = "different-project"
    identity = ui.queue.enqueue(snapshot, owner=owner)
    monkeypatch.setattr(ui.cb, "ctx", SimpleNamespace(triggered_id="imzml_recovery_cancel"))
    job = {"context_id": ui.context["id"], "job_id": identity}
    response = ui.cb.recovery_action(0, 1, 0, ui.context, report, job)
    assert response["state"] == "error"
    assert ui.queue.get_job(identity, owner)["state"] == "queued"


def test_poll_does_not_hash_or_inspect_source_during_active_job(ui, monkeypatch):
    report, _ = ui.cb.diagnose_recovery(ui.context, 0)
    owner, snapshot = ui.cb._authorize_context(ui.context)
    identity = ui.queue.enqueue(snapshot, owner=owner)
    def forbidden(*args, **kwargs):
        pytest.fail("poll must not hash inputs or run source inspection")
    monkeypatch.setattr(ui.queue, "sha256_file", forbidden)
    monkeypatch.setattr(ui.cb, "_inspect", forbidden)
    view, fresh = ui.cb.poll_recovery(1, ui.context, report, {"context_id": ui.context["id"], "job_id": identity})
    assert view["state"] == "queued" and fresh is no_update


def test_file_progress_and_failure_are_visible_without_internal_stage_names(ui):
    state = {"request_id": "job", "state": "running", "files": [
        {"state": "completed"}, {"path": "/source/two.imzML", "state": "convert",
        "progress": {"stage": "convert", "done": 2, "total": 4}}]}
    view = ui.cb._job_view(ui.context, state)
    assert view["progress"] == 75 and "変換中" in view["label"] and "1/2" in view["label"]
    state["state"] = "completed_with_errors"
    state["files"][1].update(state="failed", error="原本内容が変わりました")
    view = ui.cb._job_view(ui.context, state)
    assert "原本内容" in view["details"] and view["progress"] == 50


@pytest.mark.parametrize("tier,password", [("A", "test-master"), ("B", "test-readonly")])
def test_successful_login_assigns_fresh_signed_recovery_identity(monkeypatch, tier, password):
    from app.services import auth_middleware as auth
    monkeypatch.setattr(auth.auth_service, "verify_master", lambda value: value == "test-master")
    monkeypatch.setattr(auth.auth_service, "verify_password_b", lambda value: value == "test-readonly")
    monkeypatch.setattr(auth.auth_service, "get_password_version", lambda: 7)
    monkeypatch.setattr(auth, "_render_login", lambda **kwargs: "test login")
    app = Flask(__name__)
    app.secret_key = "isolated-test-secret"
    app.add_url_rule("/login", view_func=auth._login_view, methods=["GET", "POST"])
    client = app.test_client()
    values = []
    for _ in range(2):
        assert client.post("/login", data={"analyst_name": "Alice", "password": password}).status_code == 302
        with client.session_transaction() as authenticated:
            assert authenticated["access_tier"] == tier
            assert authenticated["analyst_name"] == "Alice" and authenticated["pw_version"] == 7
            values.append(authenticated["imzml_recovery_principal"])
    assert values[0] != values[1] and all(len(value) >= 40 for value in values)
    assert client.post("/login", data={"analyst_name": "Alice", "password": "incorrect"}).status_code == 401
    with client.session_transaction() as authenticated:
        assert authenticated["imzml_recovery_principal"] == values[-1]
