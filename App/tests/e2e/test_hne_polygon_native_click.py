"""★ ver68.0: MSI の空白クリックと下書きを Dash/Plotly 実ブラウザで検証する。"""

import json
from pathlib import Path
import shutil
import threading

import pytest

from .conftest import _launch_chromium, _unavailable


pytestmark = pytest.mark.e2e
ASSET = Path(__file__).resolve().parents[2] / "app/assets/hne_polygon_click.js"


@pytest.fixture
def polygon_page(tmp_path, monkeypatch):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _unavailable("playwright が未インストールです")
    from dash import Dash, ClientsideFunction, Input, Output, State, dcc, html
    import dash._callback
    from werkzeug.serving import make_server

    # 実アプリの認証・解析データを使わず、同じ asset と Dash Store で検証する。
    # 他のテストが収集時に登録したアプリ全体の callback を取り込まない。
    monkeypatch.setattr(dash._callback, "GLOBAL_CALLBACK_MAP", {})
    monkeypatch.setattr(dash._callback, "GLOBAL_CALLBACK_LIST", [])
    assets = tmp_path / "assets"
    assets.mkdir()
    shutil.copy2(ASSET, assets / ASSET.name)
    app = Dash(__name__, assets_folder=str(assets))
    figure = {
        "data": [{"type": "scattergl", "mode": "markers", "x": [0], "y": [0]}],
        "layout": {
            "width": 600, "height": 500, "margin": {"l": 50, "r": 30, "t": 40, "b": 40},
            "xaxis": {"range": [0, 100]}, "yaxis": {"range": [100, 0]},
            "dragmode": "pan",
            "meta": {"hne_polygon_draw": True, "hne_polygon_context": "A-rotation-0",
                     "hne_sample": "A", "hne_rds_path": "/A.rds"},
        },
    }
    app.layout = html.Div([
        dcc.Graph(id="hne_tic_graph", figure=figure,
                  config={"scrollZoom": True, "doubleClick": False}),
        dcc.Graph(id="hne_image_graph", style={"display": "none"}),
        dcc.Store(id="hne_msi_vertex_store"),
        dcc.Store(id="hne_polygon_draft_store", data=[]),
        html.Button("undo", id="hne_polygon_undo"),
        html.Button("clear", id="hne_polygon_clear_draft"),
        dcc.Dropdown(id="hne_polygon_target", options=["msi", "hne"], value="msi"),
        dcc.Store(id="hne_rotation_store", data={}),
        dcc.Dropdown(id="hne_sample_select", options=["A", "B"], value="A"),
        dcc.Store(id="seurat_rds_path_store", data="/A.rds"),
        dcc.Dropdown(id="hne_mode", options=["polygon", "pan"], value="polygon"),
        html.Pre(id="draft_json", children="[]"),
    ])
    app.clientside_callback(
        ClientsideFunction(namespace="hnePolygon", function_name="updateDraft"),
        Output("hne_polygon_draft_store", "data"),
        Input("hne_image_graph", "clickData"), Input("hne_msi_vertex_store", "data"),
        Input("hne_polygon_undo", "n_clicks"), Input("hne_polygon_clear_draft", "n_clicks"),
        Input("hne_polygon_target", "value"), Input("hne_rotation_store", "data"),
        Input("hne_sample_select", "value"), Input("seurat_rds_path_store", "data"),
        State("hne_mode", "value"), State("hne_polygon_draft_store", "data"),
        State("hne_tic_graph", "figure"), prevent_initial_call=True,
    )
    app.clientside_callback(
        "function (draft) { return JSON.stringify(draft); }",
        Output("draft_json", "children"), Input("hne_polygon_draft_store", "data"),
    )
    server = make_server("127.0.0.1", 0, app.server, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            try:
                browser = _launch_chromium(playwright)
            except Exception as exc:
                _unavailable(f"Chromium の起動に失敗: {exc}")
            try:
                page = browser.new_page(viewport={"width": 900, "height": 950})
                page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="networkidle")
                page.wait_for_function("!!document.querySelector('#hne_tic_graph .js-plotly-plot')._fullLayout")
                yield page
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _screen_point(page, x, y):
    return page.evaluate("""([x,y]) => {
        const gd = document.querySelector('#hne_tic_graph .js-plotly-plot');
        const r = gd.getBoundingClientRect(), f = gd._fullLayout;
        return [r.left + f.xaxis._offset + f.xaxis.d2p(x),
                r.top + f.yaxis._offset + f.yaxis.d2p(y)];
    }""", [x, y])


def _vertices(page, count):
    try:
        page.wait_for_function("""n => {
            const d = JSON.parse(document.getElementById('draft_json').textContent);
            return Array.isArray(d.vertices) && d.vertices.length === n;
        }""", arg=count, timeout=5000)
    except Exception:
        pytest.fail(f"期待した頂点数 {count} に到達せず: "
                    f"{page.locator('#draft_json').inner_text()} / "
                    f"{page.evaluate('window.draft_events')}")
    return json.loads(page.locator("#draft_json").inner_text())["vertices"]


def test_msi_blank_click_zoom_pan_and_rapid_vertices(polygon_page):
    page = polygon_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.evaluate("""() => {
        window.draft_events = [];
        const old = window.dash_clientside.hnePolygon.updateDraft;
        window.dash_clientside.hnePolygon.updateDraft = function(...args) {
            const out = old(...args);
            window.draft_events.push({vertex:args[1], before:args[9], after:out});
            return out;
        };
    }""")

    # 唯一のデータ点は (0, 0)。点の無い背景でも自由な頂点を取得する。
    page.mouse.click(*_screen_point(page, 35, 42))
    assert _vertices(page, 1)[0] == pytest.approx([35, 42], abs=0.3)

    # 現在のズーム軸を使い、反転 Y 軸でも正しい位置に追加する。
    page.evaluate("""() => Plotly.relayout(
        document.querySelector('#hne_tic_graph .js-plotly-plot'),
        {'xaxis.range': [20,60], 'yaxis.range': [80,20]})""")
    page.mouse.click(*_screen_point(page, 43, 57))
    assert _vertices(page, 2)[1] == pytest.approx([43, 57], abs=0.3)

    # パンのドラッグ終了を新しい頂点と誤認しない。
    x, y = _screen_point(page, 40, 50)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 25, y + 20, steps=4)
    page.mouse.up()
    assert len(_vertices(page, 2)) == 2

    # 往復通信を挟まず連続してクリックしても全頂点を保持する。
    page.evaluate("""() => Plotly.relayout(
        document.querySelector('#hne_tic_graph .js-plotly-plot'),
        {'xaxis.range': [0,100], 'yaxis.range': [100,0]})""")
    for i in range(10):
        page.mouse.click(*_screen_point(page, 20 + i * 5, 30 + i * 3))
    vertices = _vertices(page, 12)
    for i, point in enumerate(vertices[2:]):
        assert point == pytest.approx([20 + i * 5, 30 + i * 3], abs=0.3)

    page.locator("#hne_polygon_undo").click()
    assert len(_vertices(page, 11)) == 11

    # 描画モードを無効化すると背景をクリックしても増えない。
    page.evaluate("""() => Plotly.relayout(
        document.querySelector('#hne_tic_graph .js-plotly-plot'),
        {'meta.hne_polygon_draw': false})""")
    page.mouse.click(*_screen_point(page, 80, 80))
    assert len(_vertices(page, 11)) == 11
    page.locator("#hne_polygon_clear_draft").click()
    page.wait_for_function("document.getElementById('draft_json').textContent === '[]'")
    assert not errors
