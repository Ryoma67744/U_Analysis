"""★ ver74.0: 実Flask/Dash dispatchでcallback認可と共有対象の束縛を確認する。"""
from copy import deepcopy
import json
from pathlib import Path

import dash
from dash import Input, Output, State, html
import pytest
from flask import Flask

from app.services import auth_middleware as auth
import inspect


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    from app.callbacks import imzml_io_callbacks as io, share_callbacks as shared, interactive_callbacks as viewer
    from app.services import share_manager, persistent_share_manager
    # ★ ver74.0: Dash初回GETはGLOBAL登録をpopするため、HTTP試験が本アプリ登録を奪わないよう隔離。
    # callback moduleのimport後に差し替え、既存testがimport済みのdict/listの実体は保持する。
    from dash import _callback, _get_app
    original_map, original_list = _callback.GLOBAL_CALLBACK_MAP, _callback.GLOBAL_CALLBACK_LIST
    map_before, list_before = dict(original_map), list(original_list)
    monkeypatch.setattr(_callback, "GLOBAL_CALLBACK_MAP", {})
    monkeypatch.setattr(_callback, "GLOBAL_CALLBACK_LIST", [])
    monkeypatch.setattr(_callback, "GLOBAL_INLINE_SCRIPTS", [])
    monkeypatch.setattr(_get_app, "APP", _get_app.APP)
    monkeypatch.setattr(auth.auth_service, "get_password_version", lambda: 7)
    root = tmp_path / "shared"; root.mkdir()
    rds = root / "PCA.rds"; rds.write_bytes(b"read-path fixture; no R invocation")
    record = {"token": "valid-token", "project_id": "p1", "sub_project_id": "s1",
              "result_dir": str(root), "integration_method": "PCA", "require_password": False}
    def lookup(token):
        return deepcopy(record) if token == record["token"] else None
    monkeypatch.setattr(share_manager, "get_share", lookup)
    monkeypatch.setattr(persistent_share_manager, "get_persistent_share", lookup)
    monkeypatch.setattr(shared, "get_share", lookup)
    monkeypatch.setattr(shared, "get_persistent_share", lookup)
    monkeypatch.setattr(shared, "_persistent_increment_view", lambda _: None)
    monkeypatch.setattr("app.services.project_manager.get_sub_project", lambda *_: {})
    # synthetic RDSで実Rを使わず、台帳に紐付いた発見結果だけを固定する。
    monkeypatch.setattr(viewer, "_detect_integration_methods", lambda *_args, **_kwargs: {"PCA": str(rds)})
    calls = []
    monkeypatch.setattr("app.services.analysis_runner.start_analysis_process",
                        lambda *args, **kwargs: calls.append((args, kwargs)) or {"success": False, "message": "intercepted"})
    server = Flask(__name__); server.secret_key = "isolated-test-session"
    application = dash.Dash(__name__, server=server, suppress_callback_exceptions=True)
    application.layout = html.Div()
    application.callback([Output("job", "data"), Output("poll", "disabled"), Output("status", "children"), Output("stop", "disabled")],
        Input("run", "n_clicks"), State("action", "value"), State("source", "value"), State("target", "value"), State("settings", "data"))(io.start_imzml_job)
    application.callback([Output("page", "data"), Output("result", "value"), Output("source-folder", "value"), Output("project", "value"),
        Output("sub", "value"), Output("mode", "data"), Output("subid", "data"), Output("shared", "data")],
        Input("url_bar", "pathname"))(shared.route_share_url)
    application.callback([Output("progress", "style"), Output("label", "children"), Output("bar", "value"), Output("bar", "animated"),
        Output("viz", "style"), Output("info", "children"), Output("trigger", "data"), Output("token", "data")],
        Input("load_interactive_data", "n_clicks"), State("interactive_integration_method", "value"),
        State("interactive_rds_map", "data"), State("interactive_result_folder", "value"))(viewer.load_stage_a_show_progress)
    if "dash_app" in inspect.signature(auth.register).parameters:
        auth.register(server, dash_app=application)
    else:  # 修正前middlewareにも同じ攻撃要求を当て、挙動の差を検証する。
        auth.register(server)
    client = server.test_client()
    # callback_mapは登録済みだがDash固有のdispatch初期化は正規GETで行う。
    client.get("/_dash-layout")
    assert original_map == map_before, "HTTP fixtureが本アプリのcallback登録を消費した"
    assert original_list == list_before, "HTTP fixtureが本アプリのcallback一覧を消費した"
    def body(name, inputs, state):
        key, item = next((key, value) for key, value in application.callback_map.items()
                         if inspect.unwrap(value["callback"]).__name__ == name)
        outputs = item["output"]
        outputs = [{"id": obj.component_id, "property": obj.component_property} for obj in outputs]
        return {"output": key, "outputs": outputs, "inputs": [dict(spec, value=value) for spec, value in zip(item["inputs"], inputs)],
                "state": [dict(spec, value=value) for spec, value in zip(item["state"], state)], "changedPropIds": [item["inputs"][0]["id"] + "." + item["inputs"][0]["property"]]}
    return client, body, calls, record, str(rds)


def test_unauthenticated_and_tier_b_cannot_start_job(app_client):
    client, body, calls, _, _ = app_client
    payload = body("start_imzml_job", [1], ["export", "/tmp/source.parquet", "/tmp/output.zip", None])
    assert client.post("/_dash-update-component", json=payload).status_code == 403
    with client.session_transaction() as session:
        session.update(access_tier="B", pw_version=7)
    assert client.post("/_dash-update-component", json=payload).status_code == 403
    assert calls == []
    with client.session_transaction() as session:
        session.update(access_tier="A", pw_version=7)
    assert client.post("/_dash-update-component", json=payload).status_code == 200
    assert len(calls) == 1
    with client.session_transaction() as session:
        session["pw_version"] = 6
    assert client.post("/_dash-update-component", json=payload).status_code == 403
    assert len(calls) == 1


def test_share_read_is_scoped_and_revocation_is_immediate(app_client):
    client, body, calls, record, rds = app_client
    route = body("route_share_url", ["/view/valid-token"], [])
    assert client.post("/_dash-update-component", json=route).status_code == 200
    read = body("load_stage_a_show_progress", [1], ["PCA", {"PCA": rds}, record["result_dir"]])
    assert client.post("/_dash-update-component", json=read).status_code == 200
    forged = deepcopy(read); forged["state"][1]["value"] = {"PCA": "/other-project/private.rds"}
    assert client.post("/_dash-update-component", json=forged).status_code == 403
    forged = deepcopy(read); forged["state"][2]["value"] = "/other-project"
    assert client.post("/_dash-update-component", json=forged).status_code == 403
    forged = deepcopy(read); forged["state"][1]["id"] = "harmless_label"
    assert client.post("/_dash-update-component", json=forged).status_code == 403
    mutation = body("start_imzml_job", [1], ["export", rds, "/tmp/output.zip", {"shared_session": {"active": False}}])
    assert client.post("/_dash-update-component", json=mutation).status_code == 403
    assert calls == []
    record["token"] = "revoked-token"
    assert client.post("/_dash-update-component", json=read).status_code == 403


def test_password_protected_share_requires_current_tier_b(app_client):
    client, body, _, record, _ = app_client
    record["require_password"] = True
    route = body("route_share_url", ["/share/valid-token"], [])
    assert client.post("/_dash-update-component", json=route).status_code == 403
    with client.session_transaction() as session:
        session.update(access_tier="B", pw_version=7)
    assert client.post("/_dash-update-component", json=route).status_code == 200


@pytest.mark.parametrize("bad", [None, "", [], {}, ["/other.rds"], {"path": "/other.rds"}])
def test_shared_required_rds_cannot_fall_back_to_default_state(tmp_path, monkeypatch, bad):
    from app.services import shared_callback_policy as policy
    from app.callbacks.interactive_deg import filter_features
    server = Flask(__name__); server.secret_key = "test"
    rds = str(tmp_path / "PCA.rds")
    monkeypatch.setattr(policy, "_share_rds_map", lambda _: {"PCA": rds})
    entry = {"callback": filter_features, "inputs": [{"id": "seurat_rds_path_store", "property": "data"}], "state": []}
    body = {"inputs": [{"id": "seurat_rds_path_store", "property": "data", "value": bad}], "state": []}
    with server.test_request_context():
        assert not policy.authorize_shared_callback(body, entry, {"result_dir": str(tmp_path)}, "persistent", "token")


def test_shared_load_trigger_requires_scope_and_all_pattern_is_checked(tmp_path, monkeypatch):
    from app.services import shared_callback_policy as policy
    from app.callbacks.interactive_callbacks import load_stage_d_finish
    from app.callbacks.interactive_deg import _render_feature_plot_for_view
    server = Flask(__name__); server.secret_key = "test"
    rds = str(tmp_path / "PCA.rds")
    monkeypatch.setattr(policy, "_share_rds_map", lambda _: {"PCA": rds})
    spec = {"id": "load_stage_trigger_3", "property": "data"}
    entry = {"callback": load_stage_d_finish, "inputs": [spec], "state": []}
    with server.test_request_context():
        for bad in ({"n": 1}, {"rds_path": [], "method": "PCA"}, {"rds_path": rds, "method": "Harmony"}):
            assert not policy.authorize_shared_callback({"inputs": [dict(spec, value=bad)]}, entry,
                {"result_dir": str(tmp_path)}, "persistent", "token")
        good = {"rds_path": rds, "method": "PCA", "result_folder": str(tmp_path), "derive_from": None}
        assert policy.authorize_shared_callback({"inputs": [dict(spec, value=good)]}, entry,
                {"result_dir": str(tmp_path)}, "persistent", "token")
        wildcard = {"id": '{"index":["ALL"],"type":"feature_graph"}', "property": "id"}
        entry = {"callback": _render_feature_plot_for_view, "inputs": [wildcard], "state": []}
        body = {"inputs": [[{"id": {"type": "feature_graph", "index": 0}, "property": "id", "value": "graph0"}]]}
        assert policy.authorize_shared_callback(body, entry, {"result_dir": str(tmp_path)}, "persistent", "token")
        body["inputs"][0][0]["id"]["type"] = "other_component"
        assert not policy.authorize_shared_callback(body, entry, {"result_dir": str(tmp_path)}, "persistent", "token")


@pytest.mark.parametrize("payload", [None, [], {"output": {}}, {"output": "x", "inputs": {}},
    {"output": "x", "state": "bad"}])
def test_malformed_callback_request_is_rejected(app_client, payload):
    client, _, _, _, _ = app_client
    response = client.post("/_dash-update-component", data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 403


@pytest.mark.parametrize("pathname", [123, [], {}, None])
def test_malformed_share_path_is_rejected(app_client, pathname):
    client, body, _, _, _ = app_client
    assert client.post("/_dash-update-component", json=body("route_share_url", [pathname], [])).status_code == 403


@pytest.mark.parametrize("url,expected", [
    ("https://request.example/view/token", True),
    ("http://REQUEST.EXAMPLE/view/token", True),
    ("https://advertised.example/view/token", False),
    ("https://forwarded.example/view/token", False),
    ("https://request.example.evil/view/token", False),
    ("https://user@request.example/view/token", False),
    ("https://[invalid/view/token", False),
    (None, False),
])
def test_request_host_context_is_separate_from_advertised_url(monkeypatch, url, expected):
    from app.services.url_utils import is_same_request_host_url
    monkeypatch.setattr("app.config.SHARE_BASE_URL", "https://advertised.example")
    server = Flask(__name__)
    with server.test_request_context(base_url="http://request.example", headers={"X-Forwarded-Host": "forwarded.example"}):
        assert is_same_request_host_url(url) is expected
