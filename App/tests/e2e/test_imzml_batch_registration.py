"""★ ver78.0: 隔離アプリで2原本→登録→実worker変換→新規解析入力までを検証する。"""
from pathlib import Path
import json
import time
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from .test_reanalysis_registration_lifecycle import _open_new_analysis

pytestmark = pytest.mark.e2e


def _source(path):
    from pyimzml.ImzMLWriter import ImzMLWriter
    with ImzMLWriter(str(path), mode="processed", spec_type="centroid") as writer:
        for i, coordinate in enumerate([(1, 1, 1), (2, 1, 1), (10, 1, 1), (11, 1, 1)]):
            writer.addSpectrum(np.array([100., 200.]), np.array([i + 1., i + 2.]), coordinate)
    tree = ET.parse(path)
    for element in tree.iter():
        if element.get("accession") == "MS:1000511":
            element.set("value", "1")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _pick_server_file(page, button_id, source):
    """参照ボタンから共有pickerを操作し、親画面へ戻るまで確認する。"""
    from playwright.sync_api import expect
    with page.expect_response(lambda response: "_dash-update-component" in response.url
                              and "fb_file_list.children" in (response.request.post_data or "")) as opened:
        page.locator(f"#{button_id}").click()
    expect(page.locator("#file_browser_modal")).to_be_visible()
    initial_path = opened.value.json()["response"]["fb_path_input"]["value"]
    expect(page.locator("#fb_path_input")).to_have_value(initial_path)
    page.locator("#fb_path_input").fill(str(source.parent))
    page.locator("#fb_go_btn").click()
    file_list = page.locator("#fb_file_list")
    expect(file_list.get_by_text(f"📄 {source.name}", exact=True)).to_be_visible()
    # imzMLの入口でibdを単独選択させない。ブラウザが前面で操作できることも確認する。
    expect(file_list.get_by_text(f"📄 {source.with_suffix('.ibd').name}", exact=True)).to_have_count(0)
    file_list.get_by_text(f"📄 {source.name}", exact=True).click()
    expect(page.locator("#fb_selected_path")).to_have_text(str(source))
    page.screenshot(path=str(source.parent.parent / f"browse-{button_id}-{source.stem}.png"), full_page=True)
    page.locator("#fb_select_btn").click()
    expect(page.locator("#file_browser_modal")).not_to_be_visible()
    expect(page.locator("#imzml_io_modal")).to_be_visible()


def test_two_file_registration_and_exact_completed_handoff(page, tmp_path, app_server_logs):
    from playwright.sync_api import expect
    sources = tmp_path / "originals"
    sources.mkdir()
    for name in ("day4.imzML", "day7.imzML"):
        _source(sources / name)
    try:
        _open_new_analysis(page, "batch-registration-" + tmp_path.name)
        page.locator("#open_imzml_io_modal").click()
        expect(page.locator("#imzml_io_modal")).to_be_visible()
        page.get_by_text("変換の詳細設定", exact=True).click()
        page.locator("#imzml_batch_ppm").fill("3.5")
        page.locator("#browse_imzml_batch_file").click()
        expect(page.locator("#file_browser_modal")).to_be_visible()
        page.locator("#fb_cancel_btn").click()
        expect(page.locator("#file_browser_modal")).not_to_be_visible()
        expect(page.locator("#imzml_io_modal")).to_be_visible()
        expect(page.locator("#imzml_batch_ppm")).to_have_value("3.5")
        expect(page.locator("#imzml_batch_summary")).to_contain_text("全 0 切片")
        # 取消操作で変換条件を復元しないことを確認後、既定0で実変換する。
        page.locator("#imzml_batch_ppm").fill("0")
        _pick_server_file(page, "browse_imzml_batch_file", sources / "day4.imzML")
        expect(page.locator("#imzml_batch_inspection_message")).to_contain_text("1 ファイル ／ 4 pixels", timeout=30000)
        _pick_server_file(page, "browse_imzml_batch_file", sources / "day7.imzML")
        expect(page.locator("#imzml_batch_inspection_message")).to_contain_text("2 ファイル ／ 8 pixels", timeout=30000)
        page.get_by_text("表を貼り付ける／切片の検出を修正", exact=True).click()
        page.locator("#imzml_batch_paste").fill(
            "D4 slice 1\tApple4a\tDay4\nD4 slice 2\tApple4b\tDay4\n"
            "D7 slice 1\tApple7a\tDay7\nD7 slice 2\tApple7b\tDay7")
        page.locator("#imzml_batch_apply_paste").click()
        expect(page.locator("#imzml_batch_summary")).to_contain_text("未入力 0 切片")
        _pick_server_file(page, "browse_imzml_batch_file", sources / "day4.imzML")
        expect(page.locator("#imzml_batch_inspection_message")).to_contain_text("2 ファイル ／ 8 pixels")
        expect(page.locator("#imzml_batch_summary")).to_contain_text("全 4 切片 ／ 未入力 0 切片")
        expect(page.locator("#imzml_batch_table")).to_contain_text("Apple7b")
        legacy = page.get_by_text("従来の手動変換・任意出力先・imzML再出力", exact=True)
        legacy.click()
        _pick_server_file(page, "browse_imzml_source", sources / "day4.imzML")
        expect(page.locator("#imzml_source")).to_have_value(str(sources / "day4.imzML"))
        expect(page.locator("#imzml_batch_summary")).to_contain_text("全 4 切片 ／ 未入力 0 切片")
        legacy.click()
        page.locator("#imzml_batch_submit").click()
        expect(page.locator("#imzml_batch_action_message")).to_contain_text("登録を受け付けました")
        completed = page.locator('#imzml_batch_completed input[type="checkbox"]')
        expect(completed).to_have_count(2, timeout=90000)
        expect(page.locator("#imzml_batch_job_status")).to_contain_text("完了 2/2 ファイル")
        page.screenshot(path=str(tmp_path / "batch-completed.png"), full_page=True)
        # 閉じて再表示しても完成資産を保持し、解析は勝手に始めない。
        page.locator("#imzml_close").click()
        page.locator("#open_imzml_io_modal").click()
        expect(completed).to_have_count(2)
        for checkbox in completed.all():
            checkbox.check()
        page.locator("#imzml_batch_handoff").click()
        expect(page.locator("#imzml_io_modal")).not_to_be_visible()
        expect(page.locator('#analysis_method_tims input[value="tims_v8"]')).to_be_checked()
        expect(page.locator("#resume_rds")).not_to_be_checked()
        sample_choices = page.locator('#sample_selector input[type="checkbox"]')
        expect(sample_choices).to_have_count(2)
        values = sample_choices.evaluate_all("nodes => nodes.map(n => n.value)")
        # HTMLのcheckbox valueがonでも、表示が2つの登録済みParquetだけなら
        # Storeへは既存sample_check callbackから正確なパスが渡る。
        text = page.locator("#sample_selector").inner_text()
        assert "day4.parquet" in text and "day7.parquet" in text
        assert ".imzML" not in text
        expect(page.locator("#section_selector")).to_contain_text("D4 slice 1")
        expect(page.locator("#section_selector")).to_contain_text("D7 slice 2")
        page.screenshot(path=str(tmp_path / "batch-handoff.png"), full_page=True)
        page.diagnostics.assert_clean()
    finally:
        drain = app_server_logs.get("drain")
        page.diagnostics.save(tmp_path / "diagnostics", page, drain.text() if drain else "")


def test_manual_registration_edits_survive_poll_and_commit(page, tmp_path, app_server_logs):
    """未確定の手入力を2秒poll中も保持し、Enterとblurで確定した値も失わない。"""
    from playwright.sync_api import expect
    source = tmp_path / "originals" / "manual.imzML"
    source.parent.mkdir()
    _source(source)
    delayed = {"enabled": False, "table_requests": 0, "job_requests": 0}

    def delay_table_update(route):
        payload = route.request.post_data_json or {}
        output = payload.get("output", "")
        if delayed["enabled"]:
            if "section_catalog_store_registration.data" in output:
                values = next((item["value"] for item in payload.get("inputs", [])
                               if item.get("id") == "imzml_batch_table"), [])
                delayed.setdefault("committed_rows", []).append(values[0] if values else {})
            if "imzml_batch_action_message.children" in output:
                delayed["submitted_rows"] = next((item["value"] for item in payload.get("state", [])
                                                   if item.get("id") == "imzml_batch_table"), [])
            if "imzml_batch_job.options" in output:
                delayed["job_requests"] += 1
            if "imzml_batch_table.data" in output:
                delayed["table_requests"] += 1
                # 遅いネットワークでも編集中のセルを非編集状態へ戻さない。
                time.sleep(0.5)
        route.continue_()

    page.route("**/_dash-update-component", delay_table_update)
    cell = lambda column: page.locator(
        f'#imzml_batch_table td[data-dash-column="{column}"][data-dash-row="0"]')
    try:
        _open_new_analysis(page, "manual-registration-" + tmp_path.name)
        page.locator("#open_imzml_io_modal").click()
        _pick_server_file(page, "browse_imzml_batch_file", source)
        expect(page.locator("#imzml_batch_inspection_message")).to_contain_text("1 ファイル ／ 4 pixels", timeout=30000)
        cell("section_display_name").dblclick()
        cell("section_display_name").locator("input").fill("Manual slice name")
        delayed["enabled"] = True
        # 2回以上の定期更新を跨ぐ。Enterはまだ押さず、編集DOMの値自体を検査する。
        page.wait_for_timeout(5200)
        expect(cell("section_display_name").locator("input")).to_have_value("Manual slice name")
        expect(cell("section_display_name").locator("input")).to_be_focused()
        cell("section_display_name").locator("input").press("Enter")
        expect(cell("section_display_name")).to_contain_text("Manual slice name")
        page.wait_for_timeout(700)
        cell("subject_id").dblclick()
        cell("subject_id").locator("input").press_sequentially("Manual specimen", delay=60)
        expect(cell("subject_id").locator("input")).to_have_value("Manual specimen")
        cell("subject_id").locator("input").click()
        expect(cell("subject_id").locator("input")).to_have_value("Manual specimen")
        expect(cell("subject_id").locator("input")).to_be_focused()
        # 非focusableな説明文ではなく別Inputへ移動し、本当のblurを発生させる。
        page.locator("#imzml_batch_bulk_value").click()
        expect(page.locator("#imzml_batch_bulk_value")).to_be_focused()
        page.wait_for_timeout(500)
        expect(cell("subject_id")).to_contain_text("Manual specimen")
        cell("group").dblclick()
        cell("group").locator("input").fill("Group before direct click")
        cell("section_display_name").click()
        expect(cell("group")).to_contain_text("Group before direct click")
        cell("group").dblclick()
        cell("group").locator("input").fill("Manual group")
        cell("group").locator("input").press("Tab")
        page.wait_for_timeout(3200)
        expect(cell("section_display_name")).to_contain_text("Manual slice name")
        expect(cell("subject_id")).to_contain_text("Manual specimen")
        expect(cell("group")).to_contain_text("Manual group")
        assert delayed["job_requests"] >= 2
        assert any(row.get("section_display_name") == "Manual slice name"
                   and row.get("subject_id") == "Manual specimen"
                   and row.get("group") == "Manual group"
                   for row in delayed.get("committed_rows", [])), delayed
        page.screenshot(path=str(tmp_path / "manual-edits-kept.png"), full_page=True)
        # 最後の未確定入力から直接登録しても、受付側Stateが最新の値になる。
        page.get_by_text("表を貼り付ける／切片の検出を修正", exact=True).click()
        page.locator("#imzml_batch_paste").fill(
            "Manual slice name\tManual specimen\tManual group\n"
            "Second slice\tSecond specimen\tSecond group")
        page.locator("#imzml_batch_apply_paste").click()
        expect(page.locator("#imzml_batch_summary")).to_contain_text("未入力 0 切片")
        cell("group").dblclick()
        cell("group").locator("input").fill("Final group at submit")
        page.evaluate("""() => {
            window.__registrationEvents = [];
            for (const name of ['mousedown', 'mouseup', 'click', 'focusin', 'focusout']) {
                window.addEventListener(name, event => {
                    const target = event.target;
                    window.__registrationEvents.push({name, id: target.id, tag: target.tagName,
                        column: target.closest('td')?.dataset.dashColumn,
                        active: document.activeElement?.id, activeTag: document.activeElement?.tagName,
                        x: event.clientX, y: event.clientY,
                        buttonY: document.querySelector('#imzml_batch_submit').getBoundingClientRect().top});
                }, true);
            }
        }""")
        page.locator("#imzml_batch_submit").click()
        expect(page.locator("#imzml_batch_action_message")).to_contain_text("登録を受け付けました")
        assert delayed["submitted_rows"][0]["group"] == "Final group at submit"
        expect(page.locator('#imzml_batch_completed input[type="checkbox"]')).to_have_count(1, timeout=90000)
        page.diagnostics.assert_clean()
    finally:
        delayed["submit_events"] = page.evaluate("() => window.__registrationEvents || []")
        (tmp_path / "manual-edit-trace.json").write_text(json.dumps(delayed), encoding="utf-8")
        drain = app_server_logs.get("drain")
        page.diagnostics.save(tmp_path / "diagnostics", page, drain.text() if drain else "")
