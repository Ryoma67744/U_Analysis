"""★ ver74.1: 全5切片/解析3切片の不足を実描画し、入力・確認・適用まで操作する。

実layout/float callback/外側不足カードを使用する。原本I/Oを避けるため、親画面が
適用済みoverrideをcatalog/manifestへ反映する境界だけを小さなfixtureへ置き換える。
PW_CHROMIUM_PATHで既存browserを指定でき、E2E_STRICT=1では依存不足も失敗する。
REGISTRATION_BROWSER_ARTIFACTSで証拠PNG、BROWSER_NPM_ASSETSでローカルCSS/fontsを指定。
"""
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import threading

import pytest

from .conftest import _launch_chromium, _unavailable

pytestmark = pytest.mark.e2e


def test_five_sections_missing_ids_to_confirmed_registration(tmp_path, monkeypatch):
    try:
        from playwright.sync_api import sync_playwright, expect
    except ImportError:
        _unavailable("playwright が未インストールです")
    from dash import Dash, Input, Output, dcc, html
    import dash._callback as registration
    import dash._get_app as current_dash_app
    from flask import send_from_directory
    from werkzeug.serving import make_server
    from app.callbacks.section_callbacks import _registration_issue_cards
    from app.callbacks import imzml_spatial_callbacks as cb
    from app.layouts.imzml_spatial_float import create_imzml_spatial_float
    from app.services.imzml_spatial_layout import (
        build_spatial_layout, default_spatial_sections, strip_runtime_layout,
    )

    monkeypatch.setattr(registration, "GLOBAL_CALLBACK_MAP", {})
    monkeypatch.setattr(registration, "GLOBAL_CALLBACK_LIST", [])
    # ★ ver74.1: fixtureのDash初期化が後続テストのアプリ参照やinline scriptを奪わない。
    monkeypatch.setattr(registration, "GLOBAL_INLINE_SCRIPTS", [])
    monkeypatch.setattr(current_dash_app, "APP", current_dash_app.APP)
    importlib.reload(cb)
    assets = tmp_path / "assets"
    assets.mkdir()
    app_root = Path(__file__).resolve().parents[2]
    for name in ("styles.css", "imzml_spatial_float.js"):
        shutil.copy2(app_root / "app/assets" / name, assets / name)
    npm = Path(os.environ.get("BROWSER_NPM_ASSETS", str(tmp_path / "absent")))
    css = (["/browser-assets/bootstrap/dist/css/bootstrap.min.css",
            "/browser-assets/@fontsource/noto-sans-jp/400.css"] if npm.is_dir() else [])
    app = Dash(__name__, assets_folder=str(assets), external_stylesheets=css,
               suppress_callback_exceptions=True)
    if npm.is_dir():
        @app.server.route("/browser-assets/<path:filename>")
        def browser_assets(filename):
            return send_from_directory(npm, filename)

    source = str(tmp_path / "five_sections.imzML")
    layout = strip_runtime_layout(build_spatial_layout([
        (component * 20 + x, y, 1)
        for component in range(5) for x in range(3) for y in range(3)
    ]), strip_preview=False)
    rows = default_spatial_sections("fixture_file", layout)
    assert len(rows) == 5
    for i, row in enumerate(rows):
        row.update(section_display_name=f"Slice_{i + 1}", subject_id="", group="Control",
                   metadata_confirmed=False, selected=i < 3)
    entry = {"path": source, "file_id": "fixture_file", "roi_role": "spatial",
             "sections": [row for row in rows if row["selected"]],
             "spatial_layout": layout, "spatial_sections": rows, "registered_sections": rows,
             "selected_section_ids": [row["section_id"] for row in rows if row["selected"]]}
    manifest = {"files": [entry]}
    children = [
        html.H3("全5切片の登録・今回の解析は3切片"),
        html.Div(id="fixture_cards", children=_registration_issue_cards(manifest, "initial")[0]),
        html.Button("登録を再表示", id={"type": "imzml_spatial_open", "scope": "initial", "index": source}, n_clicks=0),
        html.Pre(id="fixture_applied", style={"display": "none"}),
    ]
    for scope, suffix in (("initial", ""), ("reanalysis", "_reanalysis"), ("conversion", "_conversion")):
        children.extend([
            dcc.Store(id="section_catalog_store" + suffix, data=[entry] if not suffix else []),
            dcc.Store(id="section_manifest_store" + suffix, data=manifest if not suffix else {"files": []}),
            create_imzml_spatial_float(scope),
        ])
    app.layout = html.Div(children, style={"fontFamily": "Noto Sans JP,sans-serif", "padding": "24px"})

    @app.callback(Output("section_catalog_store", "data"), Output("section_manifest_store", "data"),
                  Output("fixture_cards", "children"), Output("fixture_applied", "children"),
                  Input("imzml_spatial_overrides", "data"), prevent_initial_call=True)
    def persist_applied_override(overrides):
        if not (overrides or {}).get(source):
            return [entry], manifest, _registration_issue_cards(manifest, "initial")[0], ""
        updated = deepcopy(entry)
        updated.update(overrides[source])
        updated_manifest = {"files": [updated]}
        return ([updated], updated_manifest, _registration_issue_cards(updated_manifest, "initial")[0],
                json.dumps(overrides[source], ensure_ascii=False))

    artifacts = Path(os.environ.get("REGISTRATION_BROWSER_ARTIFACTS", str(tmp_path)))
    artifacts.mkdir(parents=True, exist_ok=True)
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
                page = browser.new_page(viewport={"width": 1400, "height": 1100})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="networkidle")
                if npm.is_dir():
                    # CI用headless OSに日本語system fontが無い場合だけ、配布webfontで可読化する。
                    page.add_style_tag(content='.dash-table-container *{font-family:"Noto Sans JP",sans-serif !important}')
                expect(page.locator("#fixture_cards")).to_contain_text("登録対象：全5切片 ／ 今回の解析対象：3切片")
                expect(page.locator("#fixture_cards").get_by_text("解析対象外", exact=True)).to_have_count(2)
                page.screenshot(path=str(artifacts / "00_outer.png"), full_page=True)
                page.get_by_role("button", name="不足項目を入力する", exact=True).click()
                guidance = page.locator("#imzml_spatial_guidance")
                expect(guidance).to_contain_text("① 入力が必要：5切片・5項目")
                expect(guidance).to_contain_text("今回の解析：3切片")
                cell = lambda i, col="subject_id": page.locator(
                    f'#imzml_spatial_table td[data-dash-column="{col}"][data-dash-row="{i}"]')
                expect(cell(0)).to_have_css("background-color", "rgb(255, 240, 240)")
                expect(cell(4, "metadata_confirmed")).to_have_css("background-color", "rgb(255, 243, 205)")
                # 最初の表示で全5行がfloatの可視領域内にあり、入力欄を探してscrollしなくてよい。
                assert cell(4).bounding_box()["y"] + cell(4).bounding_box()["height"] < page.locator("#imzml_spatial_float").bounding_box()["y"] + page.locator("#imzml_spatial_float").bounding_box()["height"]
                page.screenshot(path=str(artifacts / "01_open.png"), full_page=True)
                page.locator("#imzml_spatial_next_missing").click()
                expect(cell(0)).to_have_class(re.compile("cell--selected"))
                expect(cell(0).locator("input")).to_be_focused()
                page.screenshot(path=str(artifacts / "02_next.png"), full_page=True)
                for i in range(5):
                    cell(i).dblclick()
                    cell(i).locator("input").fill(f"Mouse{i + 1}")
                    cell(i).locator("input").press("Enter")
                    if i < 4:
                        expect(guidance).to_contain_text(f"① 入力が必要：{4 - i}切片・{4 - i}項目")
                expect(guidance).to_contain_text("① 入力完了 → ② 全5切片の内容を確認してください")
                expect(page.locator("#imzml_spatial_next_missing")).to_be_disabled()
                page.locator("#imzml_spatial_confirm_all").click()
                expect(guidance).to_contain_text("② 確認完了 → ③ 適用できます")
                page.screenshot(path=str(artifacts / "03_confirmed.png"), full_page=True)
                page.locator("#imzml_spatial_apply").click()
                expect(page.locator("#imzml_spatial_float")).not_to_be_visible()
                expect(page.locator("#fixture_applied")).to_contain_text("Mouse5")
                saved = json.loads(page.locator("#fixture_applied").inner_text())
                assert len(saved["registered_sections"]) == 5
                assert len(saved["selected_section_ids"]) == 3
                assert all(row["metadata_confirmed"] for row in saved["registered_sections"])
                page.get_by_role("button", name="登録を再表示", exact=True).click()
                expect(page.locator("#imzml_spatial_float")).to_be_visible()
                expect(guidance).to_contain_text("② 確認完了 → ③ 適用できます")
                expect(cell(4)).to_have_text("Mouse5")
                page.screenshot(path=str(artifacts / "04_reopened.png"), full_page=True)
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
