"""非表示・フォルダ移動の不要走査を省き、表示復帰と操作完了で現在値を読む。"""

import pytest
from dash import no_update
from dash._callback import GLOBAL_CALLBACK_MAP

from app.callbacks import data_management_callbacks as dm
from app.callbacks import project_callbacks as pc
from app.services import data_browser as db


_VISIBLE = {
    "dm_state.data": {"location_key": "desi", "subpath": ""},
    "dm_refresh_btn.n_clicks": 0,
    "project_list_refresh.data": 0,
    "sub_project_list_refresh.data": 0,
    "current_page.data": "analysis",
    "main_tabs.active_tab": "settings",
    "settings_subtabs.active_tab": "settings_subtab_data",
}


def _dispatch(fn, changed, values=None):
    """実際に登録されたInputで配送する。引数数を固定せず旧版でも同じ操作を行う。"""
    spec = next(spec for spec in GLOBAL_CALLBACK_MAP.values()
                if getattr(spec.get("callback"), "__wrapped__", None) is fn)
    keys = [f"{item['id']}.{item['property']}" for item in spec["inputs"]]
    if changed not in keys:
        return no_update
    state = dict(_VISIBLE, **(values or {}))
    return fn(*(state.get(key) for key in keys))


def test_folder_navigation_never_recalculates_total_capacity(monkeypatch):
    calls = []
    monkeypatch.setattr(dm, "get_storage_stats", lambda: calls.append("scan") or [])
    _dispatch(dm.render_storage_stats, "dm_refresh_btn.n_clicks")
    assert calls == ["scan"]
    for subpath in ("/data/a", "/data/b", ""):
        _dispatch(dm.render_storage_stats, "dm_state.data", {
            "dm_state.data": {"location_key": "desi", "subpath": subpath},
        })
    assert calls == ["scan"]


@pytest.mark.parametrize("fn,service", [
    (dm.render_storage_stats, "get_storage_stats"),
    (dm.render_result_audit, "audit_result_dirs"),
    (dm.render_layout_summary, "get_layout_summary"),
    (dm.render_backup_list, "list_backup_generations"),
    (dm.render_directory, "get_directory_listing"),
])
@pytest.mark.parametrize("hidden", [
    {"current_page.data": "landing"},
    {"main_tabs.active_tab": "interactive"},
    {"settings_subtabs.active_tab": "settings_subtab_analysis"},
])
def test_hidden_data_management_does_not_read_files(monkeypatch, fn, service, hidden):
    def unexpected(*args, **kwargs):
        pytest.fail("非表示のデータ管理がファイルを読んだ")

    monkeypatch.setattr(dm, service, unexpected)
    result = _dispatch(fn, "dm_refresh_btn.n_clicks", hidden)
    assert result is no_update or result == (no_update, no_update)


@pytest.mark.parametrize("changed", [
    "current_page.data", "main_tabs.active_tab", "settings_subtabs.active_tab",
    "project_list_refresh.data", "sub_project_list_refresh.data", "dm_refresh_btn.n_clicks",
])
def test_reentry_and_completed_operations_refresh_current_capacity(monkeypatch, changed):
    calls = []
    monkeypatch.setattr(dm, "get_storage_stats", lambda: calls.append("scan") or [])
    _dispatch(dm.render_storage_stats, changed)
    assert calls == ["scan"]


@pytest.mark.parametrize("changed", ["project_list_refresh.data", "dm_refresh_btn.n_clicks",
                                     "settings_subtabs.active_tab"])
def test_listing_reflects_created_and_removed_files(tmp_path, monkeypatch, changed):
    monkeypatch.setattr(db, "DATA_LOCATIONS", {
        "desi": db.DataLocation("desi", "DESI", tmp_path, None, ""),
    })
    path = tmp_path / "new_input.txt"
    path.write_text("1\t2\t3\n")
    result = _dispatch(dm.render_directory, changed)
    assert result is not no_update
    assert any("new_input.txt" in str(child.children) for child in result[0].children)
    path.unlink()
    result = _dispatch(dm.render_directory, changed)
    assert "new_input.txt" not in str(result[0])


@pytest.mark.parametrize("page", ["action", "analysis", "lite"])
def test_hidden_project_list_is_skipped_and_reentry_reads_latest(monkeypatch, page):
    calls = []
    monkeypatch.setattr(pc, "list_projects", lambda: calls.append("read") or [])
    assert pc.render_project_cards(page, 1, "name_asc", "") is no_update
    assert calls == []
    assert pc.render_project_cards("landing", 2, "name_asc", "") is not no_update
    assert calls == ["read"]
