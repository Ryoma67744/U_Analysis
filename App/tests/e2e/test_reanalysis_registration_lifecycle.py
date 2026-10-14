"""実アプリの登録画面で、再解析 Input が状態遷移中も消えないことを検証する。

認証・プロジェクト操作を含む初回表示前から console/pageerror/HTTP を記録する。
app_server は一時コピーを起動するため、実プロジェクトや設定には書き込まない。
E2E_CADDY_BINARY 未指定時はアプリ直結。指定すると Caddy 経由でも同じ操作を行う。
"""

import json
import uuid

import pandas as pd
import pytest


pytestmark = pytest.mark.e2e


def _is_sample_response(response):
    if "/_dash-update-component" not in response.url:
        return False
    try:
        return "selected_samples_reanalysis.options" in response.request.post_data_json["output"]
    except (KeyError, TypeError, ValueError):
        return False


def _set_folder(page, folder):
    field = page.locator("#reanalysis_data_folder")
    if field.input_value() == str(folder):
        return  # 新規サブプロジェクトの復元により、最初から空のことがある。
    with page.expect_response(_is_sample_response, timeout=15000) as result:
        field.fill(str(folder))
        field.press("Tab")
    assert result.value.status == 200


def _assert_samples(page, expected):
    from playwright.sync_api import expect

    selector = page.locator("#selected_samples_reanalysis")
    expect(selector).to_have_count(1)
    expect(selector.locator('input[type="checkbox"]')).to_have_count(len(expected))
    if expected:
        expect(selector.locator("label")).to_have_text(expected)
        for checkbox in selector.locator('input[type="checkbox"]').all():
            expect(checkbox).to_be_checked()


def _open_new_analysis(page, project_name):
    from playwright.sync_api import expect

    expect(page.locator("#page_landing")).to_be_visible()
    page.locator("#open_create_project_modal").click()
    page.locator("#new_project_name").fill(project_name)
    page.locator("#new_project_experiment_date").fill("2026-01-01")
    page.locator("#confirm_create_project").click()
    card = page.locator("#project_cards_container .project-card").filter(has_text=project_name)
    expect(card).to_have_count(1)
    card.get_by_role("button", name="開く", exact=True).click()
    page.locator("#open_create_sub_project_modal").click()
    page.locator("#new_sub_name").fill("再解析サンプル欄の回帰検証")
    page.locator("#confirm_create_sub_project").click()
    page.locator("#sub_project_cards_container").get_by_role("button", name="解析", exact=True).click()
    expect(page.locator("#analysis_method")).to_be_visible()


def test_reanalysis_input_survives_registration_and_folder_changes(page, tmp_path, app_server_logs):
    from playwright.sync_api import expect

    empty = tmp_path / "empty"
    desi = tmp_path / "desi"
    tims = tmp_path / "tims"
    for directory in (empty, desi, tims):
        directory.mkdir()
    # 最小の実ファイルを読み、メタデータ callback も通す。R 解析は起動しない。
    (desi / "desi_sample.txt").write_text(
        "\n\t\t\t1\n\t\t\t100.0\n\t\t\t50.0\n"
        "1\t0\t0\t10\n2\t1\t0\t20\n", encoding="utf-8")
    pd.DataFrame({
        "spot_index": [1, 2], "x": [0.0, 1.0], "y": [0.0, 0.0],
        "mz_100.0000": [1.0, 2.0], "annotation": ["ROI1", "ROI2"],
    }).to_parquet(tims / "tims_sample.parquet", index=False)

    try:
        # 再解析をまだ開いていない登録画面でも初期 Input が必要。
        _assert_samples(page, [])
        page.diagnostics.assert_clean()
        _open_new_analysis(page, "callback-lifecycle-" + tmp_path.name)
        page.locator("#analysis_method").get_by_label("再解析", exact=True).check()
        expect(page.locator("#reanalysis_settings_panel")).to_be_visible()

        for folder, notice in (("", "データフォルダを指定してください"),
                               (tmp_path / "missing", "データフォルダを指定してください"),
                               (empty, "対応ファイルが見つかりません")):
            _set_folder(page, folder)
            _assert_samples(page, [])
            expect(page.locator("#sample_selector_reanalysis_message")).to_contain_text(notice)

        _set_folder(page, desi)
        _assert_samples(page, ["desi_sample"])
        expect(page.locator("#section_selector_reanalysis")).to_contain_text("desi_sample")
        _set_folder(page, empty)
        _assert_samples(page, [])
        expect(page.locator("#section_selector_reanalysis")).not_to_contain_text("desi_sample")
        _set_folder(page, desi)
        _assert_samples(page, ["desi_sample"])

        # 方法変更は DESI/TIMS の排他 callback とサンプル欄更新を連鎖させる。
        page.locator("#analysis_method_tims").get_by_label("再解析", exact=True).check()
        expect(page.locator("#analysis_method input:checked")).to_have_count(0)
        _set_folder(page, tims)
        _assert_samples(page, ["tims_sample"])
        expect(page.locator("#section_selector_reanalysis")).to_contain_text("ROI1")

        page.locator("#selected_samples_reanalysis input").uncheck()
        expect(page.locator("#selected_samples_reanalysis input:checked")).to_have_count(0)
        expect(page.locator("#section_selector_reanalysis")).not_to_contain_text("tims_sample")
        page.locator("#selected_samples_reanalysis input").check()
        expect(page.locator("#section_selector_reanalysis")).to_contain_text("tims_sample")

        page.locator("#analysis_method_tims").get_by_label("UMAP解析", exact=True).check()
        expect(page.locator("#reanalysis_settings_panel")).not_to_be_visible()
        expect(page.locator("#selected_samples_reanalysis")).to_have_count(1)
        page.locator("#analysis_method_tims").get_by_label("再解析", exact=True).check()
        expect(page.locator("#reanalysis_settings_panel")).to_be_visible()
        _set_folder(page, "")
        _assert_samples(page, [])
        expect(page.locator("#section_selector_reanalysis")).not_to_contain_text("tims_sample")

        # 204 (PreventUpdate) は正常。存在しない Input / 5xx / 切断は別々に失敗させる。
        page.diagnostics.assert_clean()
        responses = page.diagnostics.callback_responses
        assert any("selected_samples_reanalysis.options" in (row["output"] or "")
                   for row in responses), json.dumps(responses, ensure_ascii=False)
        if "proxy" in app_server_logs:
            assert all(row["request_id"] for row in responses), responses
            for row in responses:
                assert str(uuid.UUID(row["request_id"])) == row["request_id"], row
    finally:
        drain = app_server_logs.get("drain")
        proxy = app_server_logs.get("proxy")
        page.diagnostics.save(tmp_path / "diagnostics", page,
                              drain.text() if drain else "", proxy.text() if proxy else "")
