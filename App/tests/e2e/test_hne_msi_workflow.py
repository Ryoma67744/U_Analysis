"""MSI ポリゴンを実際のタブ・コールバックで確定し、保存と集計を確認する。"""

import importlib
import json
from pathlib import Path
import shutil
import threading

import numpy as np
import pandas as pd
import pytest

from .conftest import _launch_chromium, _unavailable


pytestmark = pytest.mark.e2e


def test_real_hne_tab_msi_polygon_save_restore_and_counts(tmp_path, monkeypatch):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _unavailable("playwright が未インストールです")
    from dash import Dash, Input, Output, dcc, html
    import dash._callback as registration
    import dash_bootstrap_components as dbc
    from werkzeug.serving import make_server
    from app.callbacks import hne_overlay_callbacks as cb
    from app.layouts.hne_overlay_tab import create_hne_overlay_tab

    # 他テストで登録した callback と混ぜず、このタブの実 callback だけで起動する。
    monkeypatch.setattr(registration, "GLOBAL_CALLBACK_MAP", {})
    monkeypatch.setattr(registration, "GLOBAL_CALLBACK_LIST", [])
    importlib.reload(cb)
    df = pd.DataFrame({"Sample": ["S1"] * 4, "SpatialX": [0., 2., 8., 10.],
                       "SpatialY": [0., 2., 8., 10.], "Cluster": ["1", "1", "2", "2"],
                       "TotalCount": [1., 10., 20., 2.]})
    monkeypatch.setattr(cb, "_get_state", lambda _: {"plot_data": df})
    path = str(tmp_path / "test.rds")
    Path(path).touch()
    assets = tmp_path / "assets"
    assets.mkdir()
    source = Path(__file__).resolve().parents[2] / "app/assets/hne_polygon_click.js"
    shutil.copy2(source, assets / source.name)
    app = Dash(__name__, assets_folder=str(assets))
    app.layout = html.Div([
        dbc.Tabs(id="main_tabs", active_tab="hne"),
        dcc.Store(id="seurat_rds_path_store"), dcc.Store(id="seurat_cache_dir_store"),
        dcc.Store(id="interactive_rds_map"), dcc.Dropdown(id="interactive_integration_method"),
        create_hne_overlay_tab(), html.Pre(id="test_polys"), html.Pre(id="test_draft"),
    ])
    app.clientside_callback("function(p){return JSON.stringify(p);}",
                            Output("test_polys", "children"), Input("hne_polygons_store", "data"))
    app.clientside_callback("function(p){return JSON.stringify(p);}",
                            Output("test_draft", "children"), Input("hne_polygon_draft_store", "data"))
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
                page = browser.new_page(viewport={"width": 1500, "height": 1100})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="networkidle")
                page.add_style_tag(content=".row{display:flex}.col-3{width:25%}.col-9{width:75%}.col-6{width:50%}")
                page.evaluate("p=>window.dash_clientside.set_props('seurat_rds_path_store',{data:p})", path)
                page.wait_for_function("""()=>{
                    const gd=document.querySelector('#hne_tic_graph .js-plotly-plot');
                    return gd && gd.layout.meta && gd.layout.meta.hne_sample==='S1';
                }""")
                page.locator('#hne_mode input[value="polygon"]').check()
                page.wait_for_function("document.querySelector('#hne_tic_graph .js-plotly-plot').layout.meta.hne_polygon_draw===true")
                # 点のない位置を囲む4頂点。確定までの下書きも実図へ反映されること。
                page.locator("#hne_tic_graph").scroll_into_view_if_needed()
                for x, y in [(1, 1), (4, 1), (4, 4), (1, 4)]:
                    coords = page.evaluate("""([x,y])=>{
                        const gd=document.querySelector('#hne_tic_graph .js-plotly-plot');
                        const f=gd._fullLayout,r=gd.querySelector('.svg-container').getBoundingClientRect();
                        return [r.left+f.xaxis._offset+f.xaxis.d2p(x),r.top+f.yaxis._offset+f.yaxis.d2p(y)];
                    }""", [x, y])
                    page.mouse.click(*coords)
                page.wait_for_function("JSON.parse(document.querySelector('#test_draft').textContent).vertices?.length===4")
                page.wait_for_function("document.querySelector('#hne_tic_graph .js-plotly-plot').data.find(t=>t.name==='下書き').x.length===4")
                page.locator("#hne_polygon_commit").click()
                page.wait_for_function("JSON.parse(document.querySelector('#test_polys').textContent).length===1")
                polys = json.loads(page.locator("#test_polys").inner_text())
                assert polys[0]["coord_space"] == "msi"
                assert np.allclose(polys[0]["vertices"], [[1, 1], [4, 1], [4, 4], [1, 4]], atol=.03)
                page.locator("#hne_assign_btn").click()
                page.wait_for_function("document.querySelector('#hne_result_area').textContent.includes('割当 spot 数: 1 / 4')")
                page.wait_for_timeout(300)
                saved = json.loads((tmp_path / "hne_overlay_state.json").read_text())
                assert saved["S1"]["polygons"] == polys
                # 再読込後も画像・ランドマークなしで復元される。
                page.reload(wait_until="networkidle")
                page.evaluate("p=>window.dash_clientside.set_props('seurat_rds_path_store',{data:p})", path)
                page.wait_for_function("JSON.parse(document.querySelector('#test_polys').textContent).length===1")
                assert json.loads(page.locator("#test_polys").inner_text()) == polys
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
