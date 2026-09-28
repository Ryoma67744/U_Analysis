"""数値出力の実パネルから Ctrl + KO2 を選び、配信された CSV の値を検査する。

RDS 抽出だけを合成 plot_data へ置換し、入力 Parquet、対象一覧生成、callback、
ジョブスレッド、writer、poll、main.py の download route は製品実装を通す。
E2E_STRICT=1 / PW_CHROMIUM_PATH に対応し、EXPORT_BROWSER_ARTIFACTS に証拠を保存。
"""
import ast
from collections import OrderedDict
import importlib
import json
import os
from pathlib import Path
import threading

import pandas as pd
import pytest

from .conftest import _launch_chromium, _unavailable

pytestmark = pytest.mark.e2e


def _component(root, component_id):
    if getattr(root, "id", None) == component_id:
        return root
    children = getattr(root, "children", None)
    for child in children if isinstance(children, (list, tuple)) else [children]:
        if child is not None:
            found = _component(child, component_id)
            if found is not None:
                return found
    return None


def _register_actual_download_route(server, app_root):
    # ★ ver75.1: full app 初期化/認証だけを省き、download 処理のコピーは作らない。
    # 本物の route 関数 AST をそのまま実行し、registry→send_file の境界を検査する。
    from flask import abort, send_file
    source = app_root / "app/main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    route = next(node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == "_data_export_download")
    namespace = {"server": server, "_abort": abort, "_send_file": send_file}
    exec(compile(ast.Module(body=[route], type_ignores=[]), str(source), "exec"), namespace)


def _synthetic_result(tmp_path):
    from app.services.section_metadata import build_section_manifest
    data = tmp_path / "input"
    data.mkdir()
    definitions = [("Ctrl_A", "Ctrl", "C1"), ("Ctrl_B", "Ctrl", "C2"),
                   ("KO1_A", "KO1", "K1"), ("KO2_A", "KO2", "K2")]
    manifest = build_section_manifest([
        {"path": str(data / f"{name}.parquet"), "available_rois": [name], "roi_role": "section"}
        for name, _, _ in definitions
    ])
    records = []
    for i, (entry, (name, group, subject)) in enumerate(zip(manifest["files"], definitions)):
        section = entry["sections"][0]
        section.update(subject_id=subject, group=group, section_display_name=name)
        raw = pd.DataFrame({"id": ["001", "002", "009"], "x": [1., 2., 9.],
                            "y": [float(i)] * 3, "annotation": [name] * 3,
                            "100.000001": [10.25 + 100 * i, 20.5 + 100 * i, 9999.],
                            "100.000002": [30.75 + 100 * i, 40.125 + 100 * i, 9998.]})
        raw.to_parquet(entry["path"])
        for j, pixel in enumerate(("001", "002")):
            records.append({"CellID": f"{name}_{pixel}", "Sample": name,
                            "source_file_id": entry["file_id"], "source_pixel_id": pixel,
                            **section, "SpatialX": float(j + 1), "SpatialY": float(i),
                            "UMAP_1": i + .125 + j * .25, "UMAP_2": -i - .375 - j * .5,
                            "Cluster": str(i * 2 + j), "TotalCount": 1000.5 + i * 10 + j,
                            "nFeature": 2,
                            "100.000001": raw.loc[j, "100.000001"],
                            "100.000002": raw.loc[j, "100.000002"]})
    result = tmp_path / "result"
    rds = result / "RDS_Files/PCA.rds"
    rds.parent.mkdir(parents=True)
    rds.write_bytes(b"synthetic RDS extraction boundary")
    params = result / "analysis_params.json"
    params.write_text(json.dumps({"section_manifest": manifest}), encoding="utf-8")
    return data, result, rds, pd.DataFrame(records), params


def test_actual_export_panel_downloads_ctrl_and_ko2_only(tmp_path, monkeypatch):
    try:
        from playwright.sync_api import sync_playwright, expect
    except ImportError:
        _unavailable("playwright が未インストールです")
    from dash import Dash, Input, Output, dcc, html
    import dash._callback as registration
    import dash._get_app as current_dash_app
    from flask import send_from_directory
    from werkzeug.serving import make_server
    # import 自体の callback 登録も元のグローバル状態へ漏らさない。
    for name, empty in (("GLOBAL_CALLBACK_MAP", {}), ("GLOBAL_CALLBACK_LIST", []),
                        ("GLOBAL_INLINE_SCRIPTS", [])):
        monkeypatch.setattr(registration, name, empty)
    monkeypatch.setattr(current_dash_app, "APP", current_dash_app.APP)
    from app import config
    from app.callbacks import interactive_callbacks as interactive
    from app.callbacks import interactive_data_export as export_cb
    from app.callbacks import export_selection_callbacks as selection_cb
    from app.callbacks import export_options_callbacks as options_cb
    from app.layouts.interactive_tab import create_interactive_tab, create_export_selection_panel
    from app.layouts.export_options_modal import create_export_options_modal, create_export_options_store
    from app.services import export_progress

    for name, empty in (("GLOBAL_CALLBACK_MAP", {}), ("GLOBAL_CALLBACK_LIST", []),
                        ("GLOBAL_INLINE_SCRIPTS", [])):
        monkeypatch.setattr(registration, name, empty)
    monkeypatch.setattr(current_dash_app, "APP", current_dash_app.APP)
    for module in (export_cb, selection_cb, options_cb):
        importlib.reload(module)
    monkeypatch.setattr(export_progress, "_JOBS", {})
    monkeypatch.setattr(interactive, "_project_states", OrderedDict())
    monkeypatch.setattr(interactive, "_state_access_time", {})
    monkeypatch.setattr(config, "DATA_EXPORT_TMP_DIR", tmp_path / "downloads")
    data, result, rds, plot, params = _synthetic_result(tmp_path)
    original = plot.copy(deep=True)
    original_files = {path: path.read_bytes() for path in [rds, params, *data.glob("*.parquet")]}
    interactive._get_state(str(rds))["plot_data"] = plot
    interactive._get_state(str(rds))["method"] = "PCA"
    monkeypatch.setattr(export_cb._bridge, "extract_data", lambda *_args, **_kwargs: {"plot_data": plot.copy(deep=True)})

    tab = create_interactive_tab()
    ids = ["btn_export_data", "data_export_format_wrapper", "data_export_options_wrapper",
           "div_data_export_options_summary", "data_export_method_selector", "data_export_exclude_unused",
           "div_data_export_status", "data_export_progress_container", "data_export_job",
           "data_export_download_url", "data_export_download_sink", "data_export_poll",
           "interactive_rds_map", "seurat_rds_path_store", "cluster_name_map_store",
           "int_cal_ms_instrument", "int_section_group_updated", "load_progress_container"]
    components = [_component(tab, name) for name in ids]
    assert all(item is not None for item in components), ids
    by_id = dict(zip(ids, components))
    by_id["load_progress_container"].style = {"display": "none"}
    by_id["int_cal_ms_instrument"].data = "TIMS"
    by_id["seurat_rds_path_store"].data = str(rds)
    by_id["interactive_rds_map"].data = {"PCA": str(rds)}
    by_id["data_export_method_selector"].options = [{"label": "PCA", "value": "PCA"}]
    by_id["data_export_method_selector"].value = ["PCA"]
    # 図の表示群を KO1 のみにしても出力対象の初期値/選択は影響を受けない。
    display_filter = _component(tab, "int_section_group_filter")
    display_filter.options = [{"label": group, "value": group} for group in ("Ctrl", "KO1", "KO2")]
    display_filter.value = ["KO1"]
    npm = Path(os.environ.get("BROWSER_NPM_ASSETS", str(tmp_path / "absent")))
    css = (["/browser-assets/bootstrap/dist/css/bootstrap.min.css",
            "/browser-assets/@fontsource/noto-sans-jp/400.css"] if npm.is_dir() else [])
    app_root = Path(__file__).resolve().parents[2]
    app = Dash(__name__, assets_folder=str(tmp_path / "assets"), external_stylesheets=css,
               suppress_callback_exceptions=True)
    if npm.is_dir():
        @app.server.route("/browser-assets/<path:filename>")
        def browser_assets(filename):
            return send_from_directory(npm, filename)
    _register_actual_download_route(app.server, app_root)
    app.layout = html.Div([
        html.H3("数値データの出力対象（合成データ）"),
        html.P("Ctrl 2 切片 / KO1 1 切片 / KO2 1 切片。各切片に解析済み 2 画素。"),
        html.Label("図の表示群（数値出力とは独立）"), display_filter,
        create_export_selection_panel(), *components,
        create_export_options_modal(), create_export_options_store(),
        dcc.Input(id="interactive_msi_folder", value=str(data), style={"display": "none"}),
        dcc.Input(id="interactive_result_folder", value=str(result), style={"display": "none"}),
        dcc.Dropdown(id="interactive_integration_method", value="PCA", style={"display": "none"}),
        dcc.Dropdown(id="interactive_project_select", value=None, style={"display": "none"}),
        dcc.Dropdown(id="interactive_sub_project_select", value=None, style={"display": "none"}),
        html.Pre(id="fixture_selection", style={"display": "none"}),
    ], style={"fontFamily": "Noto Sans JP,sans-serif", "maxWidth": "1080px", "margin": "24px auto"})
    app.clientside_callback("function(s){return JSON.stringify(s);}",
                            Output("fixture_selection", "children"), Input("data_export_selection", "data"))
    artifacts = Path(os.environ.get("EXPORT_BROWSER_ARTIFACTS", str(tmp_path / "artifacts")))
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
                page = browser.new_page(viewport={"width": 1360, "height": 1100}, accept_downloads=True)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}", wait_until="networkidle")
                expect(page.locator("#data_export_selection_summary")).to_contain_text("全体")
                # 親画面のデータ読込完了通知を送り、実形式表示 callback を通す。
                page.evaluate("window.dash_clientside.set_props('int_cal_ms_instrument',{data:'TIMS'})")
                expect(page.locator("#data_export_format")).to_be_visible()
                page.locator("#data_export_format").select_option("csv")
                # 実オプションモーダルで UMAP 座標を追加する。
                page.locator("#btn_export_options").click()
                page.locator('#export_opt_categories input[value="umap"]').check()
                page.locator("#export_opt_close").click()
                expect(page.locator("#div_data_export_options_summary")).to_contain_text("UMAP")
                page.locator("#data_export_exclude_unused").uncheck()
                with page.expect_download(timeout=30000) as event:
                    page.locator("#btn_export_data").click()
                all_path = artifacts / "all.csv"
                event.value.save_as(all_path)
                all_rows = pd.read_csv(all_path, dtype={"id": str, "source_pixel_id": str})
                assert len(all_rows) == 12
                page.locator('#data_export_selection_mode input[value="selected"]').check()
                expect(page.locator("#data_export_selection_controls")).to_be_enabled()
                page.locator("#data_export_select_none").click()
                expect(page.locator("#data_export_units input:checked")).to_have_count(0)
                expect(page.locator("#data_export_selection_error")).to_contain_text("0件")
                page.wait_for_function("JSON.parse(document.querySelector('#fixture_selection').textContent).ids.length===0")
                files_before_empty = set((tmp_path / "downloads").iterdir())
                page.locator("#btn_export_data").click()
                expect(page.locator("#div_data_export_status")).to_contain_text("出力対象")
                expect(page.locator("#btn_export_data")).to_be_enabled()
                assert set((tmp_path / "downloads").iterdir()) == files_before_empty
                for group in ("Ctrl", "KO2"):
                    dropdown = page.locator("#data_export_group_pick")
                    dropdown.click()
                    dropdown.locator("input").fill(group)
                    dropdown.locator(".VirtualizedSelectOption").filter(has_text=group).click()
                page.locator("#data_export_group_add").click()
                expect(page.locator("#data_export_units input:checked")).to_have_count(3)
                expect(page.locator("#data_export_exclude_unused")).to_be_disabled()
                page.wait_for_function("JSON.parse(document.querySelector('#fixture_selection').textContent).ids.length===3")
                page.screenshot(path=str(artifacts / "01_ctrl_ko2_selected.png"), full_page=True)
                with page.expect_download(timeout=30000) as event:
                    page.locator("#btn_export_data").click()
                assert event.value.suggested_filename == "UMAP_cluster_TIMS_selected.csv"
                subset_path = artifacts / "ctrl_ko2.csv"
                event.value.save_as(subset_path)
                subset = pd.read_csv(subset_path, dtype={"id": str, "source_pixel_id": str, "UMAP cluster": str})
                assert len(subset) == 6
                assert set(subset["group"]) == {"Ctrl", "KO2"}
                assert set(subset["subject_id"]) == {"C1", "C2", "K2"}
                assert "009" not in subset["id"].tolist()
                expected = original.loc[original["group"].isin(["Ctrl", "KO2"])].set_index(["source_file_id", "source_pixel_id"])
                actual = subset.set_index(["source_file_id", "source_pixel_id"])
                assert set(actual.index) == set(expected.index)
                actual = actual.reindex(expected.index)
                for column in ("UMAP_1", "UMAP_2", "group", "subject_id", "section_id", "100.000001", "100.000002"):
                    pd.testing.assert_series_equal(actual[column], expected[column], check_names=False, check_dtype=False)
                pd.testing.assert_series_equal(actual["UMAP cluster"], expected["Cluster"], check_names=False, check_dtype=False)
                filtered_all = all_rows[all_rows["source_pixel_id"].isin(["001", "002"]) & all_rows["group"].isin(["Ctrl", "KO2"])].set_index(["source_file_id", "source_pixel_id"]).reindex(expected.index)
                for column in ("100.000001", "100.000002", "x", "y"):
                    pd.testing.assert_series_equal(actual[column], filtered_all[column], check_names=False, check_dtype=False)
                expect(page.locator("#div_data_export_status")).to_contain_text("生成しました")
                page.screenshot(path=str(artifacts / "02_downloaded.png"), full_page=True)
                pd.testing.assert_frame_equal(plot, original)
                assert all(path.read_bytes() == before for path, before in original_files.items())
                assert not errors, errors
            except Exception:
                page.screenshot(path=str(artifacts / "failure.png"), full_page=True)
                (artifacts / "failure.html").write_text(page.content(), encoding="utf-8")
                raise
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
