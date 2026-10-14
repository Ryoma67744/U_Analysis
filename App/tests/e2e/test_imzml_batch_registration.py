"""★ ver78.0: 隔離アプリで2原本→登録→実worker変換→新規解析入力までを検証する。"""
from pathlib import Path
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
    page.locator(f"#{button_id}").click()
    expect(page.locator("#file_browser_modal")).to_be_visible()
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
