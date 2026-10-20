"""化合物名の実ダウンロードを Flask/Dash 経由で認可し、応答内容まで確認する。"""

import base64
import csv
from copy import deepcopy
import io

import dash
from dash import Input, Output, State, html
from flask import Flask
import pandas as pd
import pytest

from app.services import auth_middleware as auth


_FIRST_MZ = 100.12345678901234


@pytest.fixture
def export_client(tmp_path, monkeypatch):
    from app.callbacks import annotation_preview_callbacks as annotations
    from app.services import persistent_share_manager, share_manager
    from app.services.annotation_inspect import inspect_annotations
    from dash import _callback, _get_app

    # 初回 GET が他の試験で使うグローバル callback 登録を消費しないよう隔離する。
    original_map = _callback.GLOBAL_CALLBACK_MAP
    original_list = _callback.GLOBAL_CALLBACK_LIST
    map_before, list_before = dict(original_map), list(original_list)
    monkeypatch.setattr(_callback, "GLOBAL_CALLBACK_MAP", {})
    monkeypatch.setattr(_callback, "GLOBAL_CALLBACK_LIST", [])
    monkeypatch.setattr(_callback, "GLOBAL_INLINE_SCRIPTS", [])
    monkeypatch.setattr(_get_app, "APP", _get_app.APP)
    monkeypatch.setattr(auth.auth_service, "get_password_version", lambda: 7)

    source = tmp_path / "registered"
    source.mkdir()
    names = ["β-Alanine", "1,3,5-Hexatriene", "リン脂質"] + [
        f"Compound {index}" for index in range(205)
    ]
    pd.DataFrame({
        "compound": names + [" β-Alanine ", None, "", "No DB hit", "123.45"],
        "mz": [_FIRST_MZ + index for index in range(len(names) + 5)],
    }).to_parquet(source / "fixture_feature_annotations.parquet", index=False)
    sub = {
        "id": "s1", "name": "登録データ", "ms_instrument": "TIMS",
        "data_folder": str(source),
    }
    target = {"project_id": "p1", "sub_id": "s1", "nonce": "test-open"}
    preview = inspect_annotations(sub)
    assert preview["export_source"], preview
    ready = {"target": target, "source": preview["export_source"]}
    calls = []

    def lookup_sub(project_id, sub_id):
        calls.append((project_id, sub_id))
        return deepcopy(sub) if (project_id, sub_id) == ("p1", "s1") else None

    monkeypatch.setattr(annotations, "get_sub_project", lookup_sub)
    share = {
        "token": "valid-token", "project_id": "p1", "sub_project_id": "s1",
        "result_dir": str(source), "require_password": False,
    }

    def lookup_share(token):
        return deepcopy(share) if token == "valid-token" else None

    monkeypatch.setattr(share_manager, "get_share", lookup_share)
    monkeypatch.setattr(persistent_share_manager, "get_persistent_share", lookup_share)

    server = Flask(__name__)
    server.secret_key = "isolated-annotation-export-session"
    application = dash.Dash(__name__, server=server, suppress_callback_exceptions=True)
    application.layout = html.Div()
    application.callback(
        Output("annotation_export_result", "data"),
        Input("annotation_export_btn", "n_clicks"),
        State("annotation_preview_target", "data"),
        State("annotation_preview_ready", "data"),
        State("annotation_export_format", "value"),
        State("annotation_export_unique", "value"),
        State("annotation_export_content", "value"),
        prevent_initial_call=True,
    )(annotations.download_annotation_names)
    auth.register(server, dash_app=application)
    client = server.test_client()
    assert client.get("/_dash-layout").status_code == 200
    assert original_map == map_before
    assert original_list == list_before

    key, declaration = next(iter(application.callback_map.items()))
    body = {
        "output": key,
        "outputs": {"id": "annotation_export_result", "property": "data"},
        "inputs": [dict(declaration["inputs"][0], value=1)],
        "state": [
            dict(spec, value=value)
            for spec, value in zip(
                declaration["state"], [target, ready, "txt", ["unique"], "names"],
            )
        ],
        "changedPropIds": ["annotation_export_btn.n_clicks"],
    }
    return client, body, calls, names


@pytest.mark.parametrize("tier,version", [(None, None), ("B", 7), ("A", 6)])
@pytest.mark.parametrize("content", ["names", "mz_compound"])
def test_export_denies_unauthenticated_tier_b_and_expired_sessions(export_client, tier, version, content):
    client, body, calls, _ = export_client
    body["state"][-1]["value"] = content
    if tier is not None:
        with client.session_transaction() as session:
            session.update(access_tier=tier, pw_version=version)

    response = client.post("/_dash-update-component", json=body)

    assert response.status_code == 403
    assert calls == [], "認可前にサブプロジェクトを取得してはいけない"
    assert "content" not in response.get_json()


@pytest.mark.parametrize("kind,path", [("persistent", "/view/valid-token"), ("expiring", "/share/valid-token")])
@pytest.mark.parametrize("tier", [None, "B"])
@pytest.mark.parametrize("content", ["names", "mz_compound"])
def test_export_denies_valid_shared_read_scope(export_client, kind, path, tier, content):
    client, body, calls, _ = export_client
    body["state"][-1]["value"] = content
    with client.session_transaction() as session:
        session["share_scope"] = {"kind": kind, "token": "valid-token"}
        if tier is not None:
            session.update(access_tier=tier, pw_version=7)

    response = client.post(
        "/_dash-update-component", json=body,
        headers={"Referer": "http://localhost" + path},
    )

    assert response.status_code == 403
    assert calls == [], "有効な共有 URL でも化合物名の全件出力は許可しない"


def test_tier_a_gets_actual_all_names_and_expiry_stops_next_download(export_client):
    client, body, calls, names = export_client
    with client.session_transaction() as session:
        session.update(access_tier="A", pw_version=7)

    response = client.post("/_dash-update-component", json=body)

    assert response.status_code == 200
    result = response.get_json()["response"]["annotation_export_result"]["data"]
    download = result["download"]
    assert download["base64"] is True
    assert download["filename"] == "登録データ_compound_names_unique.txt"
    assert download["type"] == "text/plain;charset=utf-8"
    assert base64.b64decode(download["content"]) == ("\n".join(names) + "\n").encode("utf-8")
    assert str(len(names)) in result["status"]
    assert calls == [("p1", "s1")]

    with client.session_transaction() as session:
        session["pw_version"] = 6
    assert client.post("/_dash-update-component", json=body).status_code == 403
    assert calls == [("p1", "s1")]


@pytest.mark.parametrize("fmt,delimiter", [("csv", ","), ("txt", "\t")])
def test_tier_a_gets_every_feature_in_mapping_without_precision_loss(export_client, fmt, delimiter):
    client, body, calls, names = export_client
    with client.session_transaction() as session:
        session.update(access_tier="A", pw_version=7)
    body["state"][2]["value"] = fmt
    body["state"][-1]["value"] = "mz_compound"
    # 化合物名一覧で重複除去を選んだまま切り替えても、対応表の行は削らない。
    assert body["state"][3]["value"] == ["unique"]

    response = client.post("/_dash-update-component", json=body)

    assert response.status_code == 200
    result = response.get_json()["response"]["annotation_export_result"]["data"]
    download = result["download"]
    assert download is not None, result
    assert download["base64"] is True
    assert download["type"] == (
        "text/csv;charset=utf-8" if fmt == "csv" else "text/plain;charset=utf-8"
    )
    assert download["filename"] == f"登録データ_mz_compounds_all.{fmt}"
    data = base64.b64decode(download["content"])
    assert data.startswith(b"\xef\xbb\xbf") is (fmt == "csv")
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), delimiter=delimiter))
    assert rows[0] == ["mz", "compound"]
    expected_names = names + ["β-Alanine", "", "", "", ""]
    assert len(rows) == len(expected_names) + 1
    assert all(len(row) == 2 for row in rows[1:])
    assert [row[1] for row in rows[1:]] == expected_names
    expected_mz = [_FIRST_MZ + index for index in range(len(expected_names))]
    assert [float(row[0]) for row in rows[1:]] == expected_mz
    assert rows[1][0] != f"{_FIRST_MZ:.4f}", "画面表示の小数4桁を再利用しない"
    assert str(len(expected_names)) in result["status"]
    assert calls == [("p1", "s1")]
