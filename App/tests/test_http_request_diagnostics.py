"""HTTP diagnostics retain errors, omit payloads, and survive Dash's first request."""
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import UUID

import flask
import pytest

from app.services import access_logger as diagnostics


def _registered_callback():
    pass


@pytest.fixture
def client_factory(monkeypatch, caplog):
    monkeypatch.delenv("MSI_HTTP_TRACE_CALLBACKS", raising=False)
    monkeypatch.delenv("MSI_HTTP_SLOW_REQUEST_SEC", raising=False)
    monkeypatch.setattr(logging.getLogger("msi.access"), "propagate", True)
    caplog.set_level(logging.INFO, logger="msi.access")
    clock = [100.0]
    monkeypatch.setattr(diagnostics.time, "perf_counter", lambda: clock[0])

    def make_client(*, status=200, duration=0, error=False, authenticated=True):
        app = flask.Flask(__name__)
        app.before_request(diagnostics.begin_request_diagnostics)

        @app.before_request
        def authenticate():
            if not authenticated:
                return "Login required", 401

        @app.before_request
        def identify():
            diagnostics.identify_callback_request({"result.children": {"callback": _registered_callback}})

        @app.after_request
        def finish(response):
            elapsed = diagnostics.finish_request_diagnostics(response)
            if diagnostics.should_log_request(flask.request.path, response.status_code,
                                               duration_seconds=elapsed,
                                               trace=flask.g._msi_http_trace):
                diagnostics.log_access(response.status_code, duration_seconds=elapsed)
            return response

        @app.post("/_dash-update-component")
        def callback():
            payload = flask.request.get_json()
            clock[0] += duration
            if error:
                raise RuntimeError("original callback error")
            return flask.jsonify({"received": payload.get("output")}), status

        return app.test_client()

    return make_client


def _messages(caplog):
    return "\n".join(record.getMessage() for record in caplog.records if record.name == "msi.access")


def test_fast_callbacks_remain_quiet_and_ids_are_unique(client_factory, caplog):
    client = client_factory()
    first = client.post("/_dash-update-component", json={"output": "result.children"})
    second = client.post("/_dash-update-component", json={"output": "result.children"})
    assert first.status_code == second.status_code == 200
    assert UUID(first.headers["X-Request-ID"])
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
    assert _messages(caplog) == ""


def test_slow_success_records_function_duration_and_id_but_no_payload(client_factory, caplog):
    response = client_factory(duration=5).post("/_dash-update-component", json={
        "output": "result.children", "inputs": [{"value": "secret-input-value"}],
        "state": [{"value": "secret-state-value"}],
    })
    message = _messages(caplog)
    assert response.status_code == 200
    assert "_registered_callback#" in message
    assert "status=200" in message and "duration_ms=5000.000" in message
    assert response.headers["X-Request-ID"] in message
    assert "secret-input-value" not in message and "secret-state-value" not in message


@pytest.mark.parametrize("setting", ["garbage", "nan", "inf", "0", "-1"])
def test_invalid_slow_threshold_uses_safe_default(client_factory, caplog, monkeypatch, setting):
    monkeypatch.setenv("MSI_HTTP_SLOW_REQUEST_SEC", setting)
    client_factory(duration=5).post("/_dash-update-component", json={"output": "result.children"})
    assert "duration_ms=5000.000" in _messages(caplog)


def test_configured_threshold_and_opt_in_start_end(client_factory, caplog, monkeypatch):
    monkeypatch.setenv("MSI_HTTP_SLOW_REQUEST_SEC", "10")
    client_factory(duration=5).post("/_dash-update-component", json={"output": "result.children"})
    assert _messages(caplog) == ""
    monkeypatch.setenv("MSI_HTTP_TRACE_CALLBACKS", "1")
    response = client_factory().post("/_dash-update-component", json={"output": "result.children"})
    messages = _messages(caplog).splitlines()
    assert len(messages) == 2
    assert "phase=start" in messages[0] and "phase=end" in messages[1]
    assert all(response.headers["X-Request-ID"] in message for message in messages)


@pytest.mark.parametrize("supplied", ["not-a-uuid", "private/path", "x" * 300, "{12345678-1234-1234-1234-123456789abc}"])
def test_invalid_request_id_is_replaced(client_factory, supplied):
    response = client_factory().post("/_dash-update-component", json={"output": "result.children"},
                                     headers={"X-Request-ID": supplied})
    assert UUID(response.headers["X-Request-ID"])
    assert response.headers["X-Request-ID"] != supplied


@pytest.mark.parametrize("supplied", ["12345678-ABCD-1234-1234-123456789ABC", "12345678abcd12341234123456789abc"])
def test_valid_proxy_request_id_is_preserved(client_factory, supplied):
    response = client_factory().post("/_dash-update-component", json={"output": "result.children"},
                                     headers={"X-Request-ID": supplied})
    assert response.headers["X-Request-ID"] == supplied.lower()


def test_unregistered_output_never_becomes_log_content(client_factory, caplog, monkeypatch):
    monkeypatch.setenv("MSI_HTTP_TRACE_CALLBACKS", "1")
    client_factory(status=400).post("/_dash-update-component", json={"output": "private/path\nforged-log-line"})
    assert "callback=unregistered" in _messages(caplog)
    assert "private/path" not in _messages(caplog) and "forged-log-line" not in _messages(caplog)


def test_diagnostics_do_not_hide_callback_exceptions(client_factory, caplog):
    response = client_factory(error=True).post("/_dash-update-component", json={"output": "result.children"})
    assert response.status_code == 500
    assert "status=500" in _messages(caplog)
    assert any(record.exc_info and str(record.exc_info[1]) == "original callback error" for record in caplog.records)


def test_malformed_json_retains_the_original_bad_request(client_factory, caplog):
    response = client_factory().post("/_dash-update-component", data="{", content_type="application/json")
    assert response.status_code == 400
    assert "status=400" in _messages(caplog)


def test_unauthenticated_request_is_not_parsed_for_diagnostics(client_factory, caplog, monkeypatch):
    monkeypatch.setenv("MSI_HTTP_TRACE_CALLBACKS", "1")
    response = client_factory(authenticated=False).post("/_dash-update-component", data="{", content_type="application/json")
    assert response.status_code == 401
    assert "phase=start" not in _messages(caplog)
    assert "callback=-" in _messages(caplog)


_READINESS_PROBE = r'''
import json, sys
from pathlib import Path
from app.main import app

# Deliberately do not call app._setup_server(): this must work on the very
# first HTTP request, when Dash promotes global callbacks into callback_map.
client = app.server.test_client()
first = client.get("/healthz/ready")
assert first.status_code == 200, first.get_data(as_text=True)
assert app.callback_map
assert first.headers.get("X-Request-ID")
original = app.callback_map
app.callback_map = {}
empty = client.get("/healthz/ready")
assert empty.status_code == 503, empty.get_data(as_text=True)
app.callback_map = {"client-only": {}}
client_only = client.get("/healthz/ready")
assert client_only.status_code == 503, client_only.get_data(as_text=True)
app.callback_map = original
recovered = client.get("/healthz/ready")
assert recovered.status_code == 200, recovered.get_data(as_text=True)
assert client.get("/healthz").status_code == 200
Path(sys.argv[1]).write_text(json.dumps({"first": first.status_code,
    "callbacks": len(original), "empty": empty.status_code,
    "client_only": client_only.status_code, "recovered": recovered.status_code}), encoding="utf-8")
'''


def test_real_dash_readiness_on_first_request_and_missing_callbacks(tmp_path):
    """Use an isolated copy: main import writes settings/auth/cache on startup."""
    source = Path(__file__).resolve().parents[1]
    app_root = tmp_path / "App"
    app_root.mkdir()
    for folder in ("app", "Script", "DB"):
        shutil.copytree(source / folder, app_root / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    env = dict(os.environ)
    env.update(PYTHONPATH=str(app_root), FLASK_SECRET_KEY="test-" + "x" * 40,
               MASTER_PASSWORD="runtime-test-password", INITIAL_PASSWORD_B="runtime-share")
    report_path = tmp_path / "report.json"
    result = subprocess.run([sys.executable, "-c", _READINESS_PROBE, str(report_path)],
                            cwd=app_root, env=env, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["callbacks"] > 0
    assert report["first"] == report["recovered"] == 200
    assert report["empty"] == report["client_only"] == 503
