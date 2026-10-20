"""Actual annotation modal callbacks and browser downloads against small Parquet files.

Only project lookup is replaced with a temporary registry. The production reader,
name filtering, snapshot checks, serializers, and Dash Download all run unchanged.
E2E_STRICT=1 makes unavailable Playwright/Chromium a failure rather than a skip.
"""

import csv
import importlib
import io
import os
from pathlib import Path
import re
import threading
from types import SimpleNamespace

import pandas as pd
import pytest

from .conftest import _BrowserDiagnostics, _launch_chromium, _unavailable

pytestmark = pytest.mark.e2e


def _write_annotations(path, names, mz=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "compound": names,
        "mz": mz if mz is not None else [100.0 + index / 1000 for index in range(len(names))],
        "display_name": [f"display-only-{index}" for index in range(len(names))],
        "adduct": ["[M+H]+"] * len(names),
        "formula": ["C10H20"] * len(names),
    }).to_parquet(path, index=False)


@pytest.fixture
def annotation_browser(tmp_path, monkeypatch):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _unavailable("playwright が未インストールです")
    from dash import Dash, dcc, html
    import dash._callback as registration
    import dash._get_app as current_dash_app
    from werkzeug.serving import make_server

    # Isolate module-level @callback registration from other E2E harnesses.
    for name, empty in (("GLOBAL_CALLBACK_MAP", {}), ("GLOBAL_CALLBACK_LIST", []),
                        ("GLOBAL_INLINE_SCRIPTS", [])):
        monkeypatch.setattr(registration, name, empty)
    monkeypatch.setattr(current_dash_app, "APP", current_dash_app.APP)
    from app.callbacks import annotation_preview_callbacks as callbacks
    from app.layouts.annotation_preview_modal import create_annotation_preview_modal
    from app.services import annotation_inspect
    for name, empty in (("GLOBAL_CALLBACK_MAP", {}), ("GLOBAL_CALLBACK_LIST", []),
                        ("GLOBAL_INLINE_SCRIPTS", [])):
        monkeypatch.setattr(registration, name, empty)
    importlib.reload(callbacks)
    monkeypatch.setattr(annotation_inspect, "_INSPECT_CACHE", {})

    names = ["グルタミン酸", "β-Alanine", "1,3,5-Hexatriene",
             'Compound "quoted"', "Name_with_underscore"]
    names += [f"Compound-{index:04d}" for index in range(1385)]
    repeated = names + [names[0], f"  {names[1]}  "]
    source_a = tmp_path / "a" / "A_feature_annotations.parquet"
    source_b = tmp_path / "b" / "B_feature_annotations.parquet"
    all_names = repeated + [None, "", "  ", "No DB hit", "123.456"]
    mz = [100.0 + index / 1000 for index in range(len(all_names))]
    # Same compound at m/z values that collapse in the four-decimal preview.
    mz[0], mz[len(names)] = 100.1234567890123, 100.1234567890124
    # Include an exact duplicate row as well; mapping mode must retain it.
    mz[len(names) + 1] = mz[1]
    _write_annotations(source_a, all_names, mz)
    names_b = ["Only dataset B", "別データの化合物", "B_tail"]
    _write_annotations(source_b, names_b)
    entries = {
        key: {"id": key, "name": f"Dataset_{key.upper()}",
              "ms_instrument": "TIMS", "data_folder": str(path.parent)}
        for key, path in (("a", source_a), ("b", source_b))
    }
    monkeypatch.setattr(callbacks, "get_sub_project",
                        lambda project_id, sub_id: entries.get(sub_id)
                        if project_id == "fixture-project" else None)
    app = Dash(__name__, assets_folder=str(tmp_path / "assets"),
               suppress_callback_exceptions=True)
    app.layout = html.Div([
        dcc.Store(id="selected_project", data={"id": "fixture-project"}),
        html.Button("Open dataset A", id={"type": "sub_action_annotations", "index": "a"}),
        html.Button("Open dataset B", id={"type": "sub_action_annotations", "index": "b"}),
        create_annotation_preview_modal(),
    ])
    server = make_server("127.0.0.1", 0, app.server, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    artifact_root = Path(os.environ.get("ANNOTATION_BROWSER_ARTIFACTS", str(tmp_path / "artifacts")))
    artifact_root.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            try:
                browser = _launch_chromium(playwright)
            except Exception as exc:
                _unavailable(str(exc))
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1100},
                                        accept_downloads=True)
                diagnostics = _BrowserDiagnostics(page, base)
                page.goto(base, wait_until="networkidle")
                # Optional local copy of the deployed theme for visual QA;
                # the contract tests never rely on a third-party CDN.
                theme = os.environ.get("ANNOTATION_BOOTSTRAP_CSS")
                if theme:
                    page.add_style_tag(path=theme)
                    page.add_style_tag(path=str(
                        Path(__file__).resolve().parents[2] / "app/assets/styles.css"))
                yield SimpleNamespace(
                    page=page, callbacks=callbacks, names=names, names_b=names_b,
                    repeated=[name.strip() for name in repeated], entries=entries,
                    mz=mz, mapping_names=[name.strip() for name in repeated] + [""] * 5,
                    source_a=source_a, source_b=source_b, artifacts=artifact_root,
                    diagnostics=diagnostics,
                )
                diagnostics.assert_clean()
            finally:
                diagnostics.save(artifact_root, page)
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _download(page, destination):
    with page.expect_download(timeout=15000) as event:
        page.locator("#annotation_export_btn").click()
    event.value.save_as(destination)
    return event.value.suggested_filename, destination.read_bytes()


def _select_names_txt(page):
    """Exercise the real content/format controls; do not set Dash stores directly."""
    from playwright.sync_api import expect

    page.locator("#annotation_export_content").click()
    page.get_by_text("化合物名のみ", exact=True).click()
    expect(page.locator("#annotation_export_unique_section")).to_be_visible()
    format_control = page.locator("#annotation_export_format")
    expect(format_control).to_contain_text(re.compile(r"TXT（1行に1化合物名）|CSV（化合物名1列）"))
    if "TXT（1行に1化合物名）" not in format_control.inner_text():
        format_control.click()
        page.get_by_text("TXT（1行に1化合物名）", exact=True).click()
    expect(page.locator("#annotation_export_btn")).to_have_text("化合物名リストをダウンロード")


def test_default_mapping_csv_and_tab_delimited_downloads(annotation_browser):
    from playwright.sync_api import expect

    fixture = annotation_browser
    page = fixture.page
    original = fixture.source_a.read_bytes()
    page.get_by_role("button", name="Open dataset A", exact=True).click()
    expect(page.locator("#annotation_export_btn")).to_be_enabled()
    expect(page.locator("#annotation_export_btn")).to_have_text("m/z・化合物名をダウンロード")
    expect(page.locator("#annotation_export_unique_section")).to_be_hidden()
    expect(page.locator("#annotation_export_count")).to_contain_text(
        re.compile(r"出力予定：\s*1,?397行"))
    expect(page.locator("#annotation_preview_body")).to_contain_text("先頭 200 件")

    filename, data = _download(page, fixture.artifacts / "mz_compounds.csv")
    assert filename.endswith("_mz_compounds_all.csv")
    assert data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert rows[0] == ["mz", "compound"]
    assert len(rows) == 1398
    assert all(len(row) == 2 for row in rows)
    assert [float(row[0]) for row in rows[1:]] == fixture.mz
    assert [row[1] for row in rows[1:]] == fixture.mapping_names
    assert rows[1][1] == rows[1391][1] == "グルタミン酸"
    assert rows[1][0] != rows[1391][0]
    assert rows[2] == rows[1392]
    page.screenshot(path=str(fixture.artifacts / "mapping_downloaded.png"), full_page=True)

    page.locator("#annotation_export_format").click()
    page.get_by_text("TXT（タブ区切り）", exact=True).click()
    filename, tab_data = _download(page, fixture.artifacts / "mz_compounds.txt")
    assert filename.endswith("_mz_compounds_all.txt")
    assert not tab_data.startswith(b"\xef\xbb\xbf")
    tab_rows = list(csv.reader(io.StringIO(tab_data.decode("utf-8")), delimiter="\t"))
    assert tab_rows == rows
    assert fixture.source_a.read_bytes() == original

    # Deduplication preference is relevant only in names mode. Hiding it must
    # never cause mapping rows to be removed when the user switches back.
    _select_names_txt(page)
    expect(page.locator('#annotation_export_unique input[type="checkbox"]')).to_be_checked()
    expect(page.locator("#annotation_export_count")).to_contain_text(
        re.compile(r"出力予定：\s*1,?390件"))
    page.locator('#annotation_export_unique input[type="checkbox"]').uncheck()
    page.locator("#annotation_export_content").click()
    page.get_by_text("m/z・化合物名", exact=True).click()
    expect(page.locator("#annotation_export_unique_section")).to_be_hidden()
    expect(page.locator("#annotation_export_count")).to_contain_text(
        re.compile(r"出力予定：\s*1,?397行"))
    filename, data = _download(page, fixture.artifacts / "mapping_after_names.txt")
    assert filename.endswith("_mz_compounds_all.txt")
    assert list(csv.reader(io.StringIO(data.decode("utf-8")), delimiter="\t")) == rows
    _select_names_txt(page)
    expect(page.locator('#annotation_export_unique input[type="checkbox"]')).not_to_be_checked()


def test_full_name_downloads_and_source_change(annotation_browser):
    from playwright.sync_api import expect

    fixture = annotation_browser
    page = fixture.page
    originals = {path: path.read_bytes() for path in (fixture.source_a, fixture.source_b)}
    page.get_by_role("button", name="Open dataset A", exact=True).click()
    expect(page.locator("#annotation_export_btn")).to_be_enabled()
    _select_names_txt(page)
    expect(page.locator("#annotation_preview_body")).to_contain_text("先頭 200 件")
    expect(page.locator("#annotation_export_count")).to_contain_text(
        re.compile(r"出力予定：\s*1,?390件"))

    filename, data = _download(page, fixture.artifacts / "names_unique.txt")
    assert filename.endswith("_compound_names_unique.txt")
    assert not data.startswith(b"\xef\xbb\xbf")
    assert data.decode("utf-8").splitlines() == fixture.names
    assert len(data.decode("utf-8").splitlines()) == 1390

    page.locator('#annotation_export_unique input[type="checkbox"]').uncheck()
    expect(page.locator("#annotation_export_count")).to_contain_text(
        re.compile(r"出力予定：\s*1,?392件"))
    page.locator("#annotation_export_format").click()
    page.get_by_text("CSV（化合物名1列）", exact=True).click()
    filename, data = _download(page, fixture.artifacts / "names_all.csv")
    assert filename.endswith("_compound_names_all.csv")
    assert data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert rows == [["compound"]] + [[name] for name in fixture.repeated]
    assert all(path.read_bytes() == before for path, before in originals.items())
    page.screenshot(path=str(fixture.artifacts / "downloaded.png"), full_page=True)

    # An in-place source rewrite after the preview must not yield a stale file.
    downloads = []
    page.on("download", lambda download: downloads.append(download.suggested_filename))
    _write_annotations(fixture.source_a, ["Changed source"])
    page.locator("#annotation_export_btn").click()
    expect(page.locator("#annotation_export_status")).to_contain_text(
        re.compile(r"再.*読|開き直|変更|更新"))
    assert downloads == []

    # Reopening must invalidate the old preview cache and permit the new source.
    page.locator("#annotation_preview_close_btn").click()
    page.get_by_role("button", name="Open dataset A", exact=True).click()
    expect(page.locator("#annotation_preview_body")).to_contain_text("Changed source")
    expect(page.locator("#annotation_export_btn")).to_be_enabled()
    _filename, data = _download(page, fixture.artifacts / "updated_source.csv")
    assert list(csv.reader(io.StringIO(data.decode("utf-8-sig")))) == [
        ["compound"], ["Changed source"]]

    page.locator("#annotation_preview_close_btn").click()
    page.get_by_role("button", name="Open dataset B", exact=True).click()
    expect(page.locator("#annotation_export_btn")).to_be_enabled()
    expect(page.locator("#annotation_preview_body")).to_contain_text("Only dataset B")
    filename, data = _download(page, fixture.artifacts / "dataset_b.csv")
    assert "Dataset_B" in filename
    assert list(csv.reader(io.StringIO(data.decode("utf-8-sig")))) == (
        [["compound"]] + [[name] for name in fixture.names_b])


def test_slow_previous_target_cannot_replace_current_export(annotation_browser, monkeypatch):
    from playwright.sync_api import expect

    fixture = annotation_browser
    page = fixture.page
    real_inspect = fixture.callbacks.inspect_annotations
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def delayed_inspect(sub, *args, **kwargs):
        if sub["id"] != "a":
            return real_inspect(sub, *args, **kwargs)
        started.set()
        if not release.wait(timeout=15):
            raise RuntimeError("browser did not release dataset A reader")
        try:
            return real_inspect(sub, *args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(fixture.callbacks, "inspect_annotations", delayed_inspect)
    try:
        page.get_by_role("button", name="Open dataset A", exact=True).click()
        expect(page.locator("#annotation_preview_close_btn")).to_be_visible()
        assert started.wait(timeout=10)
        expect(page.locator("#annotation_export_btn")).to_be_disabled()
        page.locator("#annotation_preview_close_btn").click()
        page.get_by_role("button", name="Open dataset B", exact=True).click()
        expect(page.locator("#annotation_preview_body")).to_contain_text("Only dataset B")
        expect(page.locator("#annotation_export_btn")).to_be_enabled()
        _select_names_txt(page)
        with page.expect_response(
                lambda response: response.url.endswith("/_dash-update-component")
                and response.request.post_data_json.get("output") == "annotation_preview_result.data"
                and any(item.get("value", {}).get("sub_id") == "a"
                        for item in response.request.post_data_json.get("inputs", [])
                        if isinstance(item.get("value"), dict)),
                timeout=15000) as delayed_response:
            release.set()
        delayed_response.value.finished()
        assert finished.wait(timeout=10)
        # Allow the response's React updates to paint before checking that the
        # previous dataset cannot replace the current preview/export state.
        page.evaluate("() => new Promise(resolve => requestAnimationFrame("
                      "() => requestAnimationFrame(resolve)))")
        expect(page.locator("#annotation_preview_body")).to_contain_text("Only dataset B")
        filename, data = _download(page, fixture.artifacts / "dataset_b_after_a.txt")
        assert "Dataset_B" in filename
        assert data.decode("utf-8").splitlines() == fixture.names_b
        expect(page.locator("#annotation_preview_body")).not_to_contain_text("Compound-0000")
    finally:
        release.set()


def test_download_finishing_after_target_switch_is_not_delivered(annotation_browser, monkeypatch):
    from playwright.sync_api import expect

    fixture = annotation_browser
    page = fixture.page
    real_export = fixture.callbacks.export_compound_names
    started = threading.Event()
    release = threading.Event()

    def delayed_export(sub, *args, **kwargs):
        if sub["id"] == "a":
            started.set()
            if not release.wait(timeout=15):
                raise RuntimeError("browser did not release dataset A export")
        return real_export(sub, *args, **kwargs)

    monkeypatch.setattr(fixture.callbacks, "export_compound_names", delayed_export)
    downloads = []
    page.on("download", lambda download: downloads.append(download.suggested_filename))
    try:
        page.get_by_role("button", name="Open dataset A", exact=True).click()
        expect(page.locator("#annotation_export_btn")).to_be_enabled()
        _select_names_txt(page)
        page.locator("#annotation_export_btn").click()
        assert started.wait(timeout=10)
        page.locator("#annotation_preview_close_btn").click()
        page.get_by_role("button", name="Open dataset B", exact=True).click()
        expect(page.locator("#annotation_preview_body")).to_contain_text("Only dataset B")
        with page.expect_response(
                lambda response: response.url.endswith("/_dash-update-component")
                and response.request.post_data_json.get("output") == "annotation_export_result.data",
                timeout=15000) as delayed_response:
            release.set()
        delayed_response.value.finished()
        expect(page.locator("#annotation_export_btn")).to_be_enabled()
        page.evaluate("() => new Promise(resolve => requestAnimationFrame("
                      "() => requestAnimationFrame(resolve)))")
        assert downloads == []
        filename, data = _download(page, fixture.artifacts / "dataset_b_after_export_switch.txt")
        assert "Dataset_B" in filename
        assert data.decode("utf-8").splitlines() == fixture.names_b
        assert downloads == [filename]
    finally:
        release.set()


def test_pending_names_download_is_discarded_after_mapping_selected(annotation_browser, monkeypatch):
    from playwright.sync_api import expect

    fixture = annotation_browser
    page = fixture.page
    real_export = fixture.callbacks.export_compound_names
    started = threading.Event()
    release = threading.Event()

    def delayed_names(sub, *args, **kwargs):
        if kwargs.get("content") == "names":
            started.set()
            if not release.wait(timeout=15):
                raise RuntimeError("browser did not release names export")
        return real_export(sub, *args, **kwargs)

    monkeypatch.setattr(fixture.callbacks, "export_compound_names", delayed_names)
    downloads = []
    page.on("download", lambda download: downloads.append(download.suggested_filename))
    try:
        page.get_by_role("button", name="Open dataset A", exact=True).click()
        expect(page.locator("#annotation_export_btn")).to_be_enabled()
        _select_names_txt(page)
        page.locator("#annotation_export_btn").click()
        assert started.wait(timeout=10)
        page.locator("#annotation_export_content").click()
        page.get_by_text("m/z・化合物名", exact=True).click()
        expect(page.locator("#annotation_export_unique_section")).to_be_hidden()
        with page.expect_response(
                lambda response: response.url.endswith("/_dash-update-component")
                and response.request.post_data_json.get("output") == "annotation_export_result.data",
                timeout=15000) as delayed_response:
            release.set()
        delayed_response.value.finished()
        expect(page.locator("#annotation_export_btn")).to_be_enabled()
        page.evaluate("() => new Promise(resolve => requestAnimationFrame("
                      "() => requestAnimationFrame(resolve)))")
        assert downloads == []
        filename, data = _download(page, fixture.artifacts / "mapping_after_pending_names.txt")
        assert filename.endswith("_mz_compounds_all.txt")
        rows = list(csv.reader(io.StringIO(data.decode("utf-8")), delimiter="\t"))
        assert rows[0] == ["mz", "compound"]
        assert [float(row[0]) for row in rows[1:]] == fixture.mz
        assert [row[1] for row in rows[1:]] == fixture.mapping_names
        assert downloads == [filename]
    finally:
        release.set()
