"""★ ver66.3: 全画面の実要求と図内容を照合し、種類別 State 分離を守る。"""

import copy
import json

import numpy as np
import pandas as pd
import pytest
from dash import Dash, html, dcc
from dash._callback import GLOBAL_CALLBACK_MAP
from dash.exceptions import PreventUpdate
from plotly.utils import PlotlyJSONEncoder

import app.callbacks.interactive_fullscreen as fs


def _spec(lane):
    return GLOBAL_CALLBACK_MAP[f"fullscreen_response_{lane}_store.data"]


def test_umap_spatial_server_dependencies_never_request_unrelated_figures():
    """旧実装にも適用できる依存検査。分離を戻すと巨大 State の残存で失敗する。"""
    specs = [spec for spec in GLOBAL_CALLBACK_MAP.values()
             if "callback" in spec and any(item["id"] in {
                 "fullscreen_request_light_store", "expand_umap_btn", "expand_spatial_btn"
             } for item in spec["inputs"])]
    assert specs, "UMAP/Spatial のサーバ描画経路を検出できない"
    for spec in specs:
        states = {item["id"] for item in spec["state"]}
        assert not states & {"feature_plot_container", "deg_data_store"}, states


def _request(lane, kind, states, token="request-1"):
    """実際の登録依存から HTTP JSON を作り、Dash の dispatcher へ渡す。"""
    key = f"fullscreen_response_{lane}_store.data"
    spec = _spec(lane)
    request = {"token": token, "kind": kind, "rds_path": states["seurat_rds_path_store"]}
    payload = {
        "output": key,
        "outputs": {"id": f"fullscreen_response_{lane}_store", "property": "data"},
        "changedPropIds": [f"fullscreen_request_{lane}_store.data"],
        "inputs": [{**item, "value": request} for item in spec["inputs"]],
        "state": [{**item, "value": states[item["id"]]} for item in spec["state"]],
    }
    app = Dash(__name__, suppress_callback_exceptions=True)
    app.layout = html.Div()
    app.callback_map[key] = spec
    with app.server.test_request_context("/_dash-update-component", method="POST", json=payload):
        response = app.dispatch()
    assert response.status_code == 200
    data = json.loads(response.get_data(as_text=True))["response"]
    return payload, data[f"fullscreen_response_{lane}_store"]["data"]


@pytest.fixture
def states(monkeypatch):
    x, y = np.meshgrid(np.arange(3), np.arange(2))
    df = pd.DataFrame({
        "CellID": [f"S1_{i}" for i in range(6)], "Sample": ["S1"] * 6,
        "Cluster": ["0", "1", "0", "1", "0", "1"],
        "SpatialX": x.ravel(), "SpatialY": y.ravel(),
        "UMAP_1": x.ravel() / 3, "UMAP_2": y.ravel() / 2,
    })
    monkeypatch.setattr(fs, "_interactive_data", {"plot_data": df, "rds_path": "/test.rds"})
    return {
        "spatial_rotation_store": {}, "custom_color_map_store": {},
        "spatial_rows_per_view": 0, "hne_overlay_opacity": 100,
        "cluster_name_map_store": {"0": "C0"},
        "seurat_rds_path_store": "/test.rds",
        "feature_plot_container": [html.Div("FEATURE-MARKER").to_plotly_json()],
        "deg_data_store": [{"cluster": "0", "gene": "DEG-MARKER", "avg_log2FC": 2.5}],
    }


@pytest.mark.parametrize("kind", ["umap", "spatial"])
def test_light_request_never_sends_feature_or_deg_and_keeps_the_figure(kind, states):
    before = fs._interactive_data["plot_data"].copy(deep=True)
    expected = fs.toggle_fullscreen(
        None, None, None, None, None, None, {}, {}, 0, 100,
        states["cluster_name_map_store"], "/test.rds", _trigger=f"expand_{kind}_btn",
    )
    # 別欄に巨大なデータがあっても、登録依存から生成される実要求は増えない。
    states["feature_plot_container"] = ["FEATURE-MARKER" * 100000]
    states["deg_data_store"] = [{"gene": "DEG-MARKER" * 100000}]
    payload, response = _request("light", kind, states)
    encoded = json.dumps(payload)
    assert "feature_plot_container" not in encoded
    assert "deg_data_store" not in encoded
    assert "FEATURE-MARKER" not in encoded and "DEG-MARKER" not in encoded
    assert len(encoded.encode()) < 2000
    assert response["is_open"] is True
    assert response["title"] == expected[1]
    assert response["body"] == json.loads(json.dumps(expected[2], cls=PlotlyJSONEncoder))
    pd.testing.assert_frame_equal(fs._interactive_data["plot_data"], before)


def test_feature_request_preserves_children_without_sending_deg(states):
    original = copy.deepcopy(states["feature_plot_container"])
    payload, response = _request("feature", "feature", states)
    assert [s["id"] for s in payload["state"]] == [
        "feature_plot_container", "seurat_rds_path_store"]
    assert response["is_open"] is True
    assert response["body"]["props"]["children"][0] == original[0]
    assert states["feature_plot_container"] == original


@pytest.mark.parametrize("as_list", [False, True])
def test_feature_fullscreen_preserves_real_container_values_without_main_graph_ids(states, as_list):
    """実際の殻と同じ単一 Div の JSON を通し、dict のキー展開とID重複を防ぐ。"""
    figure = {"data": [{"type": "heatmap", "z": [[1.25, 2.5], [3.75, None]],
                         "text": [["S1_1", "S1_2"], ["S1_3", "S1_4"]]}],
              "layout": {"meta": {"kind": "feature", "feature_generation": "generation-A"}}}
    graph_id = {"type": "feature_graph", "index": "S1", "generation": "generation-A"}
    component = html.Div([html.Div(dcc.Graph(id=graph_id, figure=figure,
                                            config={"scrollZoom": True}))])
    wire = json.loads(json.dumps(component, cls=PlotlyJSONEncoder))
    states["feature_plot_container"] = [wire] if as_list else wire
    before = copy.deepcopy(states["feature_plot_container"])
    _, response = _request("feature", "feature", states)
    body = response["body"]["props"]["children"][0]
    fs_graph = body["props"]["children"][0]["props"]["children"]
    assert fs_graph["props"]["id"] == {**graph_id, "type": "fs_feature_graph"}
    assert fs_graph["props"]["figure"] == figure
    assert fs_graph["props"]["config"] == {"scrollZoom": True}
    assert states["feature_plot_container"] == before
    # P4 の pattern ALL は type=feature_graph なので、この全画面図を含めない。
    assert fs_graph["props"]["id"]["type"] != graph_id["type"]


def test_deg_request_preserves_data_without_sending_feature(states):
    before = copy.deepcopy(states["deg_data_store"])
    payload, response = _request("deg", "deg", states)
    assert [s["id"] for s in payload["state"]] == [
        "deg_data_store", "cluster_name_map_store", "seurat_rds_path_store"]
    assert response["is_open"] is True
    assert response["title"] == "DEG マーカー"
    assert states["deg_data_store"] == before


@pytest.mark.parametrize("kind", ["umap", "spatial"])
def test_light_open_still_works_before_feature_and_deg_are_generated(kind, states):
    states["feature_plot_container"] = None
    states["deg_data_store"] = None
    _, response = _request("light", kind, states)
    assert response["is_open"] is True


def test_empty_response_does_not_request_a_modal_close(states, monkeypatch):
    monkeypatch.setattr(fs, "_interactive_data", {})
    _, response = _request("light", "umap", states)
    assert response == {"token": "request-1", "kind": "umap",
                        "rds_path": "/test.rds", "is_open": False}


@pytest.mark.parametrize("open_request", [
    None, {"token": "x", "kind": "feature", "rds_path": "/test.rds"},
    {"token": "x", "kind": "umap", "rds_path": "/other.rds"},
    {"token": "", "kind": "umap", "rds_path": "/test.rds"},
])
def test_wrong_kind_or_dataset_never_reaches_the_builder(open_request, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("古い対象または誤った種類の要求で図を生成している")
    monkeypatch.setattr(fs, "toggle_fullscreen", unexpected)
    with pytest.raises(PreventUpdate):
        fs.render_light_fullscreen_request(open_request, {}, {}, 0, 100, {}, "/test.rds")


@pytest.mark.parametrize("scope", [None,
    {"rds_path": "/other.rds", "load_token": "load-1"},
    {"rds_path": "/test.rds", "load_token": "old-load"},
])
def test_label_move_from_previous_fullscreen_never_writes_new_dataset(scope, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("旧図のラベル位置を新しい解析へ書き込んでいる")
    monkeypatch.setattr(fs, "_auto_save_label_positions", unexpected)
    signal = {"relayout": {"annotations[0].x": 123},
              "triggered_id": "fs_umap_integrated_graph", "fullscreen_scope": scope}
    with pytest.raises(PreventUpdate):
        fs.accumulate_annotation_positions_fs(signal, {}, None, None, "/test.rds", "load-1")


def test_valid_fullscreen_label_move_keeps_the_original_save_path(monkeypatch):
    captured = []
    monkeypatch.setattr(fs, "_interactive_data", {
        "method": "Harmony", "plot_data": pd.DataFrame({"Cluster": ["0", "1"]})})
    monkeypatch.setattr(fs, "_auto_save_label_positions", lambda data, **kwargs: captured.append((data, kwargs)))
    signal = {"relayout": {"annotations[0].x": 123, "annotations[0].y": 456},
              "triggered_id": "fs_umap_integrated_graph",
              "fullscreen_scope": {"rds_path": "/test.rds", "load_token": "load-1"}}
    result = fs.accumulate_annotation_positions_fs(signal, {}, None, None, "/test.rds", "load-1")
    assert result["umap_integrated"]["0"] == {"x": 123, "y": 456}
    assert captured == [(result, {"rds_path": "/test.rds", "method": "Harmony"})]
