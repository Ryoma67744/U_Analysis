"""★ ver81.0: 実パネルで不足診断→停止/再試行→全切片復元→選択CSV出力を検査。

RDS抽出とOSのworker起動だけを隔離fixtureへ接続する。signed session、project JSON、
保存manifest、永続queue.run_job、原本照合、実imzML変換/検証/公開、exportは製品実装。
"""
from collections import OrderedDict
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import pytest

from .conftest import _BrowserDiagnostics, _launch_chromium, _unavailable
from .test_export_selection import _component, _register_actual_download_route

pytestmark = pytest.mark.e2e


def _saved_inputs(tmp_path):
    from pyimzml.ImzMLWriter import ImzMLWriter
    from app.services import input_preparation as ip
    from app.services.imzml_spatial_layout import inspect_imzml_spatial_layout, default_spatial_sections
    from app.services.section_metadata import build_section_manifest, stable_file_id
    entries, frames = [], []
    for file_number, count in enumerate((5, 1)):
        raw = tmp_path / f"source_{file_number}.imzML"
        coordinates = [(s * 10 + x, 1, 1) for s in range(count) for x in (1, 2)]
        with ImzMLWriter(str(raw), mode="processed", spec_type="centroid",
                         mz_dtype=np.float64, intensity_dtype=np.float32) as writer:
            for index, coordinate in enumerate(coordinates):
                writer.addSpectrum(np.array([100.125, 200.25]),
                                   np.array([index + 1., index + 2.], dtype=np.float32), coordinate)
        tree = ET.parse(raw)
        for node in tree.iter():
            if node.get("accession") == "MS:1000511":
                node.set("value", "1")
        tree.write(raw, encoding="utf-8", xml_declaration=True)
        layout = inspect_imzml_spatial_layout(raw)
        fid = stable_file_id(str(raw))
        sections = default_spatial_sections(fid, layout)
        assert len(sections) == count
        for index, section in enumerate(sections):
            section.update(section_display_name=f"File{file_number}_Slice{index + 1}",
                           subject_id=f"Mouse{file_number}_{index + 1}", group="Ctrl",
                           metadata_confirmed=True, selected=True)
        entry = build_section_manifest([{
            "path": str(raw), "file_id": fid, "roi_role": "spatial", "available_rois": [],
            "spatial_layout": layout, "spatial_sections": sections, "registered_sections": sections,
        }])["files"][0]
        prepared = ip.prepare_imzml(entry, cache_root=tmp_path / "cache", project_id="project")
        table = pd.read_parquet(prepared["runtime_path"])
        registry = {section["section_display_name"]: section for section in sections}
        records = []
        for index, row in table.iterrows():
            section = registry[row["annotation"]]
            records.append({"CellID": f"{fid}_{row['id']}", "Sample": row["annotation"],
                            "source_file_id": fid, "source_pixel_id": str(row["id"]), **section,
                            "SpatialX": float(row["x"]), "SpatialY": float(row["y"]),
                            "UMAP_1": float(index), "UMAP_2": -float(index), "Cluster": "0",
                            "TotalCount": float(row["100.125000"] + row["200.250000"]),
                            "nFeature": 2, "100.125000": row["100.125000"], "200.250000": row["200.250000"]})
        frames.extend(records)
        entries.append(prepared)
    result = tmp_path / "result"
    rds = result / "RDS_Files/PCA.rds"
    rds.parent.mkdir(parents=True)
    rds.write_bytes(b"saved RDS extraction boundary")
    manifest = {"files": entries}
    (result / "section_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (result / "analysis_params.json").write_text(json.dumps({"section_manifest": manifest}), encoding="utf-8")
    expected = pd.read_parquet(entries[0]["runtime_path"])
    for entry in entries:
        shutil.rmtree(Path(entry["runtime_path"]).parent)
    return result, rds, manifest, pd.DataFrame(frames), expected


def test_selected_file_recovery_preserves_all_slices_then_exports(tmp_path, monkeypatch):
    # Windows の MAX_PATH に試験名を加えない（資産revision自体は実際の64桁のまま）。
    tmp_path = tmp_path.parent / "r"
    tmp_path.mkdir()
    try:
        from playwright.sync_api import sync_playwright, expect
    except ImportError:
        _unavailable("playwright が未インストールです")
    from dash import Dash, Input, Output, dcc, html
    import dash._callback as registration
    import dash._get_app as current_dash_app
    from werkzeug.serving import make_server
    from app import config
    for name, empty in (("GLOBAL_CALLBACK_MAP", {}), ("GLOBAL_CALLBACK_LIST", []), ("GLOBAL_INLINE_SCRIPTS", [])):
        monkeypatch.setattr(registration, name, empty)
    monkeypatch.setattr(current_dash_app, "APP", current_dash_app.APP)
    from app.callbacks import interactive_callbacks as interactive
    from app.callbacks import interactive_data_export as export_cb, export_selection_callbacks as selection_cb
    from app.callbacks import export_options_callbacks as options_cb, imzml_recovery_callbacks as recovery_cb
    from app.layouts.interactive_tab import create_interactive_tab, create_export_selection_panel, create_imzml_recovery_panel
    from app.layouts.export_options_modal import create_export_options_modal, create_export_options_store
    from app.services import export_progress, auth_service, auth_middleware, project_manager, process_control
    from app.services import imzml_recovery_queue as queue, imzml_asset_recovery as core
    from app.services import input_preparation as ip
    from app.services.tims_parquet_contract import read_registered_sections
    for name, empty in (("GLOBAL_CALLBACK_MAP", {}), ("GLOBAL_CALLBACK_LIST", []), ("GLOBAL_INLINE_SCRIPTS", [])):
        monkeypatch.setattr(registration, name, empty)
    for module in (export_cb, selection_cb, options_cb, recovery_cb):
        importlib.reload(module)
    monkeypatch.setattr(export_progress, "_JOBS", {})
    monkeypatch.setattr(interactive, "_project_states", OrderedDict())
    monkeypatch.setattr(interactive, "_state_access_time", {})
    monkeypatch.setattr(config, "DATA_EXPORT_TMP_DIR", tmp_path / "downloads")
    monkeypatch.setattr(ip, "default_cache_root", lambda: tmp_path / "cache")
    monkeypatch.setattr(auth_service, "get_password_version", lambda: 7)
    monkeypatch.setattr(auth_service, "verify_master", lambda value: value == "isolated-browser-password")
    monkeypatch.setattr(queue, "queue_root", lambda: tmp_path / "queue")
    monkeypatch.setattr(process_control, "admission_path", lambda: tmp_path / "heavy_job.json")
    monkeypatch.setattr(project_manager, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(project_manager, "PROJECTS_FILE", tmp_path / "projects/projects.json")
    result, rds, manifest, plot, expected = _saved_inputs(tmp_path)
    project_manager.PROJECTS_DIR.mkdir()
    project_manager.PROJECTS_FILE.write_text(json.dumps({"projects": [{"id": "project", "created_by": "Alice",
        "sub_projects": [{"id": "sub", "last_result_dir": str(result), "data_folder": str(tmp_path)}]}]}), encoding="utf-8")
    protected = [rds, result / "section_manifest.json", result / "analysis_params.json", project_manager.PROJECTS_FILE,
                 *tmp_path.glob("*.imzML"), *tmp_path.glob("*.ibd")]
    original = {str(path): path.read_bytes() for path in protected}
    interactive._get_state(str(rds)).update(plot_data=plot, method="PCA")
    monkeypatch.setattr(export_cb._bridge, "extract_data", lambda *a, **k: {"plot_data": plot.copy(deep=True)})
    assert interactive._detect_integration_methods(str(result)) == {"PCA": str(rds)}
    run_allowed, continue_source = threading.Event(), threading.Event()
    workers, worker_errors = [], []
    verify = core.verify_source
    def controlled_source(*args, **kwargs):
        if not continue_source.wait(timeout=30):
            raise RuntimeError("browser did not release source verification barrier")
        return verify(*args, **kwargs)
    monkeypatch.setattr(core, "verify_source", controlled_source)
    def dispatch_fixture():
        if not run_allowed.is_set():
            return
        process_control.record_lease(SimpleNamespace(pid=os.getpid(), poll=lambda: None), tmp_path, "isolated-browser")
        pending = [path for path in (queue.queue_root() / "jobs").glob("*/state.json")
                   if queue._read(path)["state"] == "queued"]
        def worker():
            try:
                for path in pending:
                    queue.run_job(path.parent.name)
            except Exception as exc:
                worker_errors.append(repr(exc))
        thread = threading.Thread(target=worker, daemon=True)
        workers.append(thread)
        thread.start()
    monkeypatch.setattr(queue, "ensure_dispatcher", dispatch_fixture)

    tab = create_interactive_tab()
    ids = ["btn_export_data", "data_export_format_wrapper", "data_export_options_wrapper",
           "div_data_export_options_summary", "data_export_method_selector", "data_export_exclude_unused",
           "div_data_export_status", "data_export_progress_container", "data_export_job",
           "data_export_download_url", "data_export_download_sink", "data_export_poll",
           "interactive_rds_map", "seurat_rds_path_store", "cluster_name_map_store",
           "int_cal_ms_instrument", "int_section_group_updated", "load_progress_container"]
    components = [_component(tab, name) for name in ids]
    assert all(item is not None for item in components)
    by_id = dict(zip(ids, components))
    by_id["load_progress_container"].style = {"display": "none"}
    by_id["int_cal_ms_instrument"].data = "TIMS"
    by_id["seurat_rds_path_store"].data = str(rds)
    by_id["interactive_rds_map"].data = {"PCA": str(rds)}
    by_id["data_export_method_selector"].options = [{"label": "PCA", "value": "PCA"}]
    by_id["data_export_method_selector"].value = ["PCA"]
    app = Dash(__name__, assets_folder=str(tmp_path / "assets"), suppress_callback_exceptions=True)
    app.server.secret_key = "isolated-browser-session-secret"
    app.server.add_url_rule("/fixture-login", view_func=auth_middleware._login_view, methods=["POST"])
    _register_actual_download_route(app.server, Path(__file__).resolve().parents[2])
    app.layout = html.Div([html.H3("保存済み解析の不足データを復元"), create_export_selection_panel(),
        create_imzml_recovery_panel(), *components, create_export_options_modal(), create_export_options_store(),
        dcc.Input(id="interactive_msi_folder", value=str(tmp_path), style={"display": "none"}),
        dcc.Input(id="interactive_result_folder", value=str(result), style={"display": "none"}),
        dcc.Dropdown(id="interactive_integration_method", value="PCA", style={"display": "none"}),
        dcc.Dropdown(id="interactive_project_select", value="project", style={"display": "none"}),
        dcc.Dropdown(id="interactive_sub_project_select", value="sub", style={"display": "none"}),
        dcc.Store(id="shared_session", data={"active": False}), html.Pre(id="fixture_state", style={"display": "none"}),
    ], style={"maxWidth": "1080px", "margin": "24px auto"})
    app.clientside_callback("function(c,r,j,s,e){return JSON.stringify({context:c,report:r,job:j,selection:s,export:e});}",
                           Output("fixture_state", "children"), Input("imzml_recovery_context", "data"),
                           Input("imzml_recovery_report", "data"), Input("imzml_recovery_job", "data"),
                           Input("data_export_selection", "data"), Input("data_export_job", "data"))
    artifacts = Path(os.environ.get("RECOVERY_BROWSER_ARTIFACTS", str(tmp_path / "artifacts")))
    artifacts.mkdir(parents=True, exist_ok=True)
    server = make_server("127.0.0.1", 0, app.server, threaded=True)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        with sync_playwright() as pw:
            try:
                browser = _launch_chromium(pw)
            except Exception as exc:
                _unavailable(str(exc))
            page = browser.new_page(viewport={"width": 1280, "height": 1100}, accept_downloads=True)
            base_url = f"http://127.0.0.1:{server.server_port}"
            diagnostics = _BrowserDiagnostics(page, base_url)
            try:
                response = page.request.post(base_url + "/fixture-login", form={
                    "analyst_name": "Alice", "password": "isolated-browser-password"}, max_redirects=0)
                assert response.status == 302
                page.goto(base_url, wait_until="networkidle")
                expect(page.locator("#imzml_recovery_summary")).to_contain_text("2 ファイル不足")
                page.locator('#data_export_selection_mode input[value="selected"]').check()
                expect(page.locator("#data_export_units input")).to_have_count(6)
                page.locator("#data_export_select_none").click()
                expect(page.locator("#data_export_units input:checked")).to_have_count(0)
                expect(page.locator("#imzml_recovery_start")).to_be_disabled()
                page.locator("#data_export_units label").filter(has_text="File0_Slice1").locator("input").check()
                expect(page.locator("#imzml_recovery_summary")).to_contain_text("1 ファイル不足")
                expect(page.locator("#imzml_recovery_start")).to_be_enabled()
                page.screenshot(path=str(artifacts / "01_missing_selected.png"), full_page=True)
                page.locator("#imzml_recovery_start").click()
                expect(page.locator("#imzml_recovery_progress_label")).to_contain_text("待機中")
                page.locator("#imzml_recovery_cancel").click()
                expect(page.locator("#imzml_recovery_progress_label")).to_contain_text("停止済み")
                assert not Path(manifest["files"][0]["runtime_path"]).exists()
                run_allowed.set()
                page.locator("#imzml_recovery_retry").click()
                expect(page.locator("#imzml_recovery_progress_label")).to_contain_text("原本を照合中", timeout=15000)
                assert not Path(manifest["files"][0]["runtime_path"]).exists()
                continue_source.set()
                expect(page.locator("#imzml_recovery_progress_label")).to_contain_text("復元が完了", timeout=30000)
                expect(page.locator("#imzml_recovery_summary")).to_contain_text("通常のデータ出力")
                state = json.loads(page.locator("#fixture_state").text_content())
                assert not state.get("export")  # 復元完了は解析やexportを起動しない。
                assert not Path(manifest["files"][1]["runtime_path"]).exists()
                restored = pd.read_parquet(manifest["files"][0]["runtime_path"])
                pd.testing.assert_frame_equal(restored, expected)
                assert len(read_registered_sections(manifest["files"][0]["runtime_path"])) == 5
                job = queue._read(queue.queue_root() / "jobs" / state["job"]["job_id"] / "request.json")
                assert job["file_ids"] == [manifest["files"][0]["file_id"]]
                assert len(job["manifest"]["files"][0]["registered_sections"]) == 5
                page.screenshot(path=str(artifacts / "02_restored_all_five.png"), full_page=True)
                page.evaluate("window.dash_clientside.set_props('int_cal_ms_instrument',{data:'TIMS'})")
                expect(page.locator("#data_export_format")).to_be_visible()
                page.locator("#data_export_format").select_option("csv")
                with page.expect_download(timeout=30000) as download:
                    page.locator("#btn_export_data").click()
                downloaded = artifacts / "selected.csv"
                download.value.save_as(downloaded)
                output = pd.read_csv(downloaded)
                assert len(output) == 2
                assert set(output["section_id"]) == {manifest["files"][0]["sections"][0]["section_id"]}
                assert output["100.125000"].tolist() == [1., 2.]
                assert download.value.failure() is None
                # Chromiumはnavigationをdownloadへ引き渡す際GETをERR_ABORTEDと記録する。
                # 実ファイル保存・内容照合まで成功した同一download URLだけを別記録にする。
                completed_downloads = [item for item in diagnostics.failed_requests
                    if item["url"] == download.value.url and item["method"] == "GET"
                    and item["failure"] == "net::ERR_ABORTED"]
                diagnostics.failed_requests = [item for item in diagnostics.failed_requests
                                               if item not in completed_downloads]
                assert all(Path(path).read_bytes() == data for path, data in original.items())
                # 遅着した別世代の応答を注入しても、最新contextには表示・操作できない。
                stale = {**state["report"], "context_id": "previous-result", "summary": "STALE RESPONSE"}
                page.evaluate("value=>window.dash_clientside.set_props('imzml_recovery_report',{data:value})", stale)
                expect(page.locator("#imzml_recovery_panel")).not_to_be_visible()
                page.evaluate("window.dash_clientside.set_props('imzml_recovery_check',{n_clicks:1})")
                expect(page.locator("#imzml_recovery_progress_label")).to_contain_text("復元が完了")
                assert page.locator("#interactive_result_folder").input_value() == str(result)
                diagnostics.assert_clean()
                (artifacts / "evidence.json").write_text(json.dumps({
                    "selected_files": job["file_ids"], "restored_sections": 5, "restored_pixels": len(restored),
                    "exported_pixels": len(output), "unselected_asset_missing": True,
                    "result_and_source_unchanged": True, "cancel_retry": True,
                    "delayed_response_hidden": True, "automatic_export": False,
                    "completed_download_cancellations": completed_downloads,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            finally:
                continue_source.set()
                diagnostics.save(artifacts, page)
                browser.close()
    finally:
        continue_source.set()
        for worker in workers:
            worker.join(timeout=10)
        server.shutdown()
        serving.join(timeout=5)
        server.server_close()
    assert not worker_errors, worker_errors
