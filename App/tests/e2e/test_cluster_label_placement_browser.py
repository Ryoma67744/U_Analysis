"""★ ver76.0: 実Plotlyの再配置・ドラッグ通知・再読込を保存処理まで通す。"""

import json
from pathlib import Path
import shutil
import threading
import time

import numpy as np
import pandas as pd
import pytest

from .conftest import _launch_chromium, _unavailable


pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("kind", ["umap", "spatial"])
def test_reset_drag_reload_and_stale_signal(tmp_path, monkeypatch, kind):
    from playwright.sync_api import sync_playwright
    from dash import ALL, ClientsideFunction, Dash, Input, Output, State, dcc, html
    import dash._callback as registration
    import dash_bootstrap_components as dbc
    from werkzeug.serving import make_server
    from app.callbacks import interactive_fullscreen as fs
    from app.callbacks import interactive_callbacks as ic
    from app.callbacks import interactive_umap as um
    from app.callbacks import interactive_spatial as sp
    from app.utils import label_persistence as lp

    # 本番と同じ描画・保存関数/JSを使い、実データや他のcallbackからは隔離する。
    monkeypatch.setattr(registration, "GLOBAL_CALLBACK_MAP", {})
    monkeypatch.setattr(registration, "GLOBAL_CALLBACK_LIST", [])
    path = str(tmp_path / "synthetic.rds")
    Path(path).touch()
    method, token = "harmony", "browser-test-load"
    state = {"method": method, "rds_path": path}
    monkeypatch.setattr(ic, "_set_active_key", lambda _: None)
    monkeypatch.setattr(ic, "_interactive_data", state)
    monkeypatch.setattr(fs, "_interactive_data", state)
    gx, gy = np.meshgrid(np.arange(8.), np.arange(8.))
    sx, sy = np.meshgrid(np.arange(20., 22.), np.arange(2.))
    df = pd.DataFrame({"UMAP_1": np.r_[gx.ravel(), sx.ravel()],
                       "UMAP_2": np.r_[gy.ravel(), sy.ravel()],
                       "Cluster": "0", "Sample": "S1"})
    df["CellID"] = [f"cell-{i}" for i in range(len(df))]
    df["SpatialX"], df["SpatialY"] = df.UMAP_1, df.UMAP_2
    untouched = {"Other": {"3": {"x": 99., "y": 100.}}}
    initial = {"umap_integrated": {"0": {"x": 12., "y": 4.}},
               "spatial": {**untouched, "S1": {"0": {"x": 12., "y": 4.}}}}
    lp.save_label_positions(initial, path, method)
    section = "umap_integrated" if kind == "umap" else "spatial"
    graph_id = "interactive_umap_plot" if kind == "umap" else {"type": "spatial_graph", "index": "S1"}
    root_id = "umap_test_root" if kind == "umap" else "spatial_plots_container"

    def target_positions(positions):
        current = positions.get(section, {})
        return current if kind == "umap" else current.get("S1", {})

    assets = tmp_path / "assets"
    assets.mkdir()
    source = Path(__file__).resolve().parents[2] / "app/assets"
    for name in ("relayout_filter.js", "label_positions.js"):
        shutil.copy2(source / name, assets / name)
    app = Dash(__name__, assets_folder=str(assets))
    reset_id = {"type": "reset_cluster_labels", "view": kind}
    app.layout = html.Div([
        dcc.Store(id="seurat_rds_path_store", data=path),
        dcc.Store(id="load_token_store", data=token),
        dcc.Store(id="label_positions_reset_request"),
        dcc.Store(id="accumulated_label_positions", data={}),
        dcc.Store(id="label_positions_revision", data=0),
        dcc.Store(id="annotation_relayout_signal"),
        dbc.Alert(id="label_positions_message", is_open=False),
        html.Button("番号を自動配置に戻す", id=reset_id, n_clicks=0),
        html.Button("図を再読込", id="reload_test_figure", n_clicks=0),
        html.Div(dcc.Graph(id=graph_id, config={"edits": {"annotationPosition": True}}), id=root_id),
    ])
    app.clientside_callback(
        ClientsideFunction(namespace="label_positions", function_name="request_reset"),
        Output("label_positions_reset_request", "data"),
        Input({"type": "reset_cluster_labels", "view": ALL}, "n_clicks"),
        State("seurat_rds_path_store", "data"), State("load_token_store", "data"),
        prevent_initial_call=True)
    app.callback(
        Output("accumulated_label_positions", "data", allow_duplicate=True),
        Output("label_positions_revision", "data"),
        Output("label_positions_message", "children"),
        Output("label_positions_message", "is_open"),
        Input("label_positions_reset_request", "data"),
        State("seurat_rds_path_store", "data"), State("load_token_store", "data"),
        State("label_positions_revision", "data"), prevent_initial_call=True,
    )(fs.reset_visible_label_positions)
    app.clientside_callback(
        "function(rd){return window.dash_clientside.relayout.filter_annotations(rd, [], []);}",
        Output("annotation_relayout_signal", "data"),
        Input(graph_id, "relayoutData"), prevent_initial_call=True)

    @app.callback(Output("accumulated_label_positions", "data", allow_duplicate=True),
                  Input("annotation_relayout_signal", "data"), prevent_initial_call=True)
    def save_drag(signal):
        return fs._save_scoped_signal(signal, path, token)

    @app.callback(Output(graph_id, "figure"),
                  Input("label_positions_revision", "data"),
                  Input("reload_test_figure", "n_clicks"))
    def draw(_revision, _reload):
        positions = lp.load_label_positions(path, method)
        scope = dict(rds_path=path, method=method, load_token=token,
                     revision=lp.label_revision(positions), section=section)
        if kind == "umap":
            fig = um._build_umap_integrated_fig(
                df, "Cluster", None, False, True,
                saved_positions=target_positions(positions), label_scope=scope)
        else:
            fig = sp._create_single_spatial_fig(
                df, {"0": "red"}, None, set(), show_labels=True,
                saved_positions=target_positions(positions), label_scope=dict(scope, sample="S1"))
        fig.update_layout(width=800, height=550)
        return fig

    server = make_server("127.0.0.1", 0, app.server, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as pw:
            try:
                browser = _launch_chromium(pw)
            except Exception as exc:
                _unavailable(str(exc))
            try:
                page = browser.new_page(viewport={"width": 1000, "height": 800})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="networkidle")
                graph = f"document.querySelector('#{root_id} .js-plotly-plot')"
                try:
                    page.wait_for_function(f"()=>{{const g={graph};return g?.layout?.annotations?.[0]?.x===12;}}")
                except Exception:
                    snapshot = page.evaluate(f"()=>{{const g={graph};return {{annotations:g?.layout?.annotations,meta:g?.layout?.meta}};}}")
                    pytest.fail(f"初期描画に失敗: {snapshot}; browser errors={errors}")
                old_scope = page.evaluate(f"()=>{graph}.layout.meta.label_scope")
                page.get_by_role("button", name="番号を自動配置に戻す").click()
                page.wait_for_function(f"()=>{{const g={graph};return g?.layout?.meta?.label_scope?.revision===1;}}")
                anchor = page.evaluate(f"()=>{{const a={graph}.layout.annotations[0];return [a.x,a.y];}}")
                assert 0 <= anchor[0] <= 7 and 0 <= abs(anchor[1]) <= 7
                assert "0" not in target_positions(lp.load_label_positions(path, method))
                assert lp.load_label_positions(path, method)["spatial"]["Other"] == untouched["Other"]

                # 再配置前の遅延ドラッグ通知を実Dash経由で届けても保存し直さない。
                stale = {"label_scope": old_scope, "positions": {"0": {"x": 12., "y": 4.}}}
                page.evaluate("s=>window.dash_clientside.set_props('annotation_relayout_signal',{data:s})", stale)
                page.wait_for_timeout(300)
                assert "0" not in target_positions(lp.load_label_positions(path, method))

                # ユーザーが注釈をドラッグした際と同じPlotly relayoutイベントを発火する。
                page.evaluate(f"async()=>await Plotly.relayout({graph},{{'annotations[0].x':5.25,'annotations[0].y':5.5}})")
                saved_path = lp.get_label_positions_path(path, method)
                # ブラウザと保存callbackの完了を、保存された値で待つ。
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    saved = json.loads(saved_path.read_text(encoding="utf-8"))
                    if target_positions(saved).get("0") == {"x": 5.25, "y": 5.5}:
                        break
                    page.wait_for_timeout(50)
                assert target_positions(saved)["0"] == {"x": 5.25, "y": 5.5}
                page.get_by_role("button", name="図を再読込").click()
                page.wait_for_timeout(300)
                assert page.evaluate(f"()=>{{const a={graph}.layout.annotations[0];return [a.x,a.y];}}") == [5.25, 5.5]
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
