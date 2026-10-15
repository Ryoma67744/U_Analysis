"""★ ver80.0: WinSCPと同じ表示を使っても、登録・解析の固定パスを変更しない。"""
from pathlib import Path
import json
import os

import pytest

from .test_imzml_batch_registration import _source
from .test_reanalysis_registration_lifecycle import _open_new_analysis

pytestmark = pytest.mark.e2e


def _open_browser(page, button_id):
    from playwright.sync_api import expect
    with page.expect_response(lambda response: "_dash-update-component" in response.url
                              and "fb_file_list.children" in (response.request.post_data or "")) as opened:
        page.locator(f"#{button_id}").click()
    expect(page.locator("#file_browser_modal")).to_be_visible()
    initial_path = opened.value.json()["response"]["fb_path_input"]["value"]
    expect(page.locator("#fb_path_input")).to_have_value(initial_path)


def _go(page, path):
    from playwright.sync_api import expect
    page.locator("#fb_path_input").fill(path)
    page.locator("#fb_go_btn").click()
    expect(page.locator("#fb_path_input")).to_have_value(path)


def test_winscp_display_preserves_exact_registration_and_handoff_paths(page, tmp_path, app_server_logs):
    from playwright.sync_api import expect
    assert os.environ.get("E2E_WINSCP_PATHS") == "1", "Run this test separately with E2E_WINSCP_PATHS=1"
    paths = app_server_logs["paths"]
    source = paths["tims_data"] / "winscp originals" / "specimen.imzML"
    source.parent.mkdir(parents=True, exist_ok=True)
    _source(source)
    public_folder = "/srv/msi/tims_data/winscp originals"
    public_source = public_folder + "/specimen.imzML"
    evidence = {}
    try:
        _open_new_analysis(page, "winscp-paths-" + tmp_path.name)
        # 仮想の/srv/msiを、実在する共有TIMSフォルダまで参照できる。
        _open_browser(page, "browse_folder")
        _go(page, "/srv/msi")
        page.locator("#fb_file_list").get_by_text("📁 tims_data", exact=True).click()
        expect(page.locator("#fb_path_input")).to_have_value("/srv/msi/tims_data")
        expect(page.locator("#fb_selected_path")).to_have_text("/srv/msi/tims_data")
        page.locator("#fb_select_btn").click()
        expect(page.locator("#file_browser_modal")).not_to_be_visible()
        expect(page.locator("#data_folder_display")).to_have_value("/srv/msi/tims_data")
        expect(page.locator("#data_folder")).to_have_value(str(paths["tims_data"]))
        expect(page.locator("#data_folder")).to_be_hidden()

        # WinSCPからの貼付と、既存設定に残る内部パスの貼付をどちらも受理する。
        page.locator("#data_folder_display").fill(public_folder)
        page.locator("#data_folder_display").press("Tab")
        expect(page.locator("#data_folder")).to_have_value(str(source.parent))
        page.locator("#data_folder_display").fill(str(paths["tims_data"]))
        page.locator("#data_folder_display").press("Tab")
        expect(page.locator("#data_folder")).to_have_value(str(paths["tims_data"]))
        page.locator("#data_folder_display").fill(public_folder)
        page.locator("#data_folder_display").press("Tab")
        expect(page.locator("#data_folder")).to_have_value(str(source.parent))

        page.locator("#open_imzml_io_modal").click()
        page.locator("#imzml_batch_folder_display").fill(public_folder)
        page.locator("#imzml_batch_folder_display").press("Tab")
        expect(page.locator("#imzml_batch_folder")).to_have_value(str(source.parent))
        expect(page.locator("#imzml_batch_folder")).to_be_hidden()
        _open_browser(page, "browse_imzml_batch_file")
        _go(page, public_folder)
        file_list = page.locator("#fb_file_list")
        expect(file_list.get_by_text("📄 specimen.imzML", exact=True)).to_be_visible()
        expect(file_list.get_by_text("📄 specimen.ibd", exact=True)).to_have_count(0)
        file_list.get_by_text("📄 specimen.imzML", exact=True).click()
        expect(page.locator("#fb_selected_path")).to_have_text(public_source)
        page.screenshot(path=str(tmp_path / "winscp-picker.png"), full_page=True)
        page.locator("#fb_select_btn").click()
        expect(page.locator("#file_browser_modal")).not_to_be_visible()
        expect(page.locator("#imzml_batch_inspection_message")).to_contain_text("1 ファイル ／ 4 pixels", timeout=30000)
        expect(page.locator("#imzml_batch_paths")).to_contain_text(public_source)
        assert str(source) not in page.locator("#imzml_batch_paths").inner_text()

        page.get_by_text("表を貼り付ける／切片の検出を修正", exact=True).click()
        page.locator("#imzml_batch_paste").fill("First slice\tAnimal1\tControl\nSecond slice\tAnimal2\tTreatment")
        page.locator("#imzml_batch_apply_paste").click()
        expect(page.locator("#imzml_batch_summary")).to_contain_text("未入力 0 切片")
        _open_browser(page, "browse_imzml_batch_file")
        page.locator("#fb_cancel_btn").click()
        expect(page.locator("#file_browser_modal")).not_to_be_visible()
        expect(page.locator("#imzml_io_modal")).to_be_visible()
        expect(page.locator("#imzml_batch_summary")).to_contain_text("全 2 切片 ／ 未入力 0 切片")
        expect(page.locator("#imzml_batch_table")).to_contain_text("Animal2")
        page.locator("#imzml_batch_submit").click()
        expect(page.locator("#imzml_batch_action_message")).to_contain_text("登録を受け付けました")
        completed = page.locator('#imzml_batch_completed input[type="checkbox"]')
        expect(completed).to_have_count(1, timeout=90000)
        expect(page.locator("#imzml_batch_completed")).to_contain_text(public_source)
        expect(page.locator("#imzml_batch_output_paths")).to_contain_text("/srv/msi/imzml_assets/")
        expect(page.locator("#imzml_batch_output_paths")).to_contain_text("specimen.parquet")
        assert str(paths["imzml_assets"]) not in page.locator("#imzml_batch_output_paths").inner_text()
        page.locator("#imzml_batch_output_paths").scroll_into_view_if_needed()
        page.screenshot(path=str(tmp_path / "winscp-completed.png"), full_page=True)

        requests = list((paths["runtime_root"] / "Data/Other/imzml_registrations/jobs").glob("*/request.json"))
        assert len(requests) == 1
        frozen = json.loads(requests[0].read_text(encoding="utf-8"))
        assert frozen["entries"][0]["path"] == str(source)
        assert [row["subject_id"] for row in frozen["entries"][0]["registered_sections"]] == ["Animal1", "Animal2"]
        evidence["request"] = frozen
        completed.check()
        with page.expect_response(lambda response: "_dash-update-component" in response.url
                                  and "imzml_registered_selection.data" in (response.request.post_data or "")) as handed:
            page.locator("#imzml_batch_handoff").click()
        response = handed.value.json()["response"]
        selected = response["imzml_registered_selection"]["data"]
        assert len(selected["paths"]) == 1
        runtime = Path(selected["paths"][0])
        assert runtime.is_file() and runtime.is_relative_to(paths["imzml_assets"])
        assert selected["manifest"]["files"][0]["path"] == str(runtime)
        assert response["section_manifest_store"]["data"] == selected["manifest"]
        evidence["handoff"] = selected
        expect(page.locator("#imzml_io_modal")).not_to_be_visible()
        expect(page.locator("#data_folder")).to_have_value(str(runtime.parent))
        public_output_folder = "/srv/msi/imzml_assets/" + runtime.parent.relative_to(paths["imzml_assets"]).as_posix()
        expect(page.locator("#data_folder_display")).to_have_value(public_output_folder)
        expect(page.locator('#sample_selector input[type="checkbox"]')).to_have_count(1)
        expect(page.locator("#sample_selector")).to_contain_text("specimen.parquet")
        assert ".imzML" not in page.locator("#sample_selector").inner_text()
        expect(page.locator("#section_selector")).to_contain_text("First slice")
        expect(page.locator("#section_selector")).to_contain_text("Second slice")
        page.screenshot(path=str(tmp_path / "winscp-handoff.png"), full_page=True)
        page.diagnostics.assert_clean()
    finally:
        (tmp_path / "winscp-path-evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        drain = app_server_logs.get("drain")
        page.diagnostics.save(tmp_path / "diagnostics", page, drain.text() if drain else "")
