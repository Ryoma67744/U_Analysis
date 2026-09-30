"""小型の実 Dash で、window ごとの scope と無効フォルダの表示失効を確認する。"""
import ast
import json
from pathlib import Path
import threading

import pandas as pd
import pytest

from .conftest import _launch_chromium, _unavailable

pytestmark = pytest.mark.e2e


def test_browser_scopes_are_distinct_and_invalid_folder_clears_result(tmp_path, monkeypatch):
    try:
        from playwright.sync_api import sync_playwright, expect
    except ImportError:
        _unavailable("playwright 未導入")
    import dash._callback as registration
    import dash._get_app as current_dash_app
    from dash import Dash, Input, Output, State, dcc, html
    from werkzeug.serving import make_server
    from app.callbacks import interactive_callbacks as callbacks
    from app.callbacks.interactive_umap import _build_umap_integrated_fig
    for name, empty in (("GLOBAL_CALLBACK_MAP", {}), ("GLOBAL_CALLBACK_LIST", []), ("GLOBAL_INLINE_SCRIPTS", [])):
        monkeypatch.setattr(registration, name, empty)
    monkeypatch.setattr(current_dash_app, "APP", current_dash_app.APP)
    monkeypatch.setattr(callbacks, "_LOAD_SCOPES", __import__("collections").OrderedDict())
    monkeypatch.setattr(callbacks, "_LOAD_SCOPE_FOLDERS", {})
    # 本物の clientside 関数を読み、テスト用に同じ処理を手書き複製しない。
    tree = ast.parse(Path(callbacks.__file__).read_text(encoding="utf-8"))
    scope_call = next(n.value for n in tree.body if isinstance(n, ast.Expr)
                      and isinstance(n.value, ast.Call) and getattr(n.value.func, "id", None) == "clientside_callback"
                      and isinstance(n.value.args[0], ast.Constant) and "__uaResultLoadScope" in str(n.value.args[0].value))
    scope_javascript = scope_call.args[0].value
    frame = pd.DataFrame({"CellID": ["a", "b"], "Cluster": ["0", "1"], "Sample": ["s", "s"],
                          "UMAP_1": [1., 2.], "UMAP_2": [3., 4.]})
    frame.attrs["result_descriptor"] = {"embedding": {"kind": "pca2d"}}
    figure = _build_umap_integrated_fig(frame, "Cluster", None, True, False)
    app = Dash(__name__)
    app.layout = html.Div([
        dcc.Location(id="url_bar"), dcc.Store(id="interactive_load_scope", data=None),
        dcc.Input(id="interactive_result_folder", value="", debounce=False),
        html.Div(id="interactive_viz_container", children=dcc.Graph(id="diagnostic", figure=figure)),
        dcc.Store(id="seurat_rds_path_store", data="old-result.rds"),
        dcc.Store(id="seurat_cache_dir_store", data="old-cache"),
        dcc.Store(id="deg_data_store", data={"old": True}),
        html.Pre(id="observed-state"), html.Pre(id="observed-scope"),
    ])
    app.clientside_callback(scope_javascript, Output("interactive_load_scope", "data"),
                            Input("url_bar", "pathname"), State("interactive_load_scope", "data"))
    app.callback([Output("interactive_viz_container", "style"), Output("seurat_rds_path_store", "data"),
                  Output("seurat_cache_dir_store", "data"), Output("deg_data_store", "data")],
                 Input("interactive_result_folder", "value"), State("interactive_load_scope", "data"),
                 prevent_initial_call=True)(callbacks.invalidate_result_folder)
    app.callback(Output("observed-state", "children"), Input("seurat_rds_path_store", "data"),
                 Input("seurat_cache_dir_store", "data"), Input("deg_data_store", "data"))(
        lambda path, cache, deg: json.dumps([path, cache, deg]))
    app.callback(Output("observed-scope", "children"), Input("interactive_load_scope", "data"))(lambda scope: scope or "")
    server = make_server("127.0.0.1", 0, app.server, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            try:
                browser = _launch_chromium(playwright)
            except Exception as exc:
                _unavailable(str(exc))
            try:
                context = browser.new_context()
                first, second = context.new_page(), context.new_page()
                url = f"http://127.0.0.1:{server.server_port}"
                for page in (first, second):
                    page.goto(url)
                    expect(page.locator("#observed-scope")).not_to_be_empty()
                    page.wait_for_function("document.querySelector('#diagnostic .js-plotly-plot') && document.querySelector('#diagnostic .js-plotly-plot')._fullLayout")
                    assert page.evaluate("document.querySelector('#diagnostic .js-plotly-plot')._fullLayout.xaxis.title.text") == "PC1"
                assert first.locator("#observed-scope").inner_text() != second.locator("#observed-scope").inner_text()
                for index in range(3):
                    first.locator("#interactive_result_folder").fill(str(tmp_path / f"missing-{index}"))
                expect(first.locator("#observed-state")).to_have_text("[null, null, null]")
                expect(first.locator("#interactive_viz_container")).to_be_hidden()
                expect(second.locator("#observed-state")).to_contain_text("old-result.rds")
                expect(second.locator("#interactive_viz_container")).to_be_visible()
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
