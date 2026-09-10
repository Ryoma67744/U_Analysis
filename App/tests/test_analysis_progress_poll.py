"""★ ver66.3: ログ更新を抑えても完了・停止・全行検索を失わない。"""

import copy
from datetime import datetime
from unittest.mock import Mock

import pytest
from dash import no_update

from app.callbacks import analysis_callbacks as ac
from app.services import analysis_runner as ar


@pytest.fixture
def running_job(tmp_path, monkeypatch):
    log = tmp_path / "analysis.log"
    log.write_text("[stage] Finding Markers\nhello\n", encoding="utf-8")
    status = tmp_path / "analysis_status.txt"
    status.write_text("running", encoding="utf-8")
    process = Mock()
    process.poll.return_value = None
    process.returncode = None
    process.pid = 99999999
    monkeypatch.setitem(ac._process_state, "process", process)
    monkeypatch.setitem(ac._process_state, "log_file_handle", None)
    monkeypatch.setattr(ac._finalizer, "finalize", lambda *a, **k: {})
    state = {"is_running": True, "log_file": str(log), "status_file": str(status),
             "full_output_dir": str(tmp_path), "start_time": datetime.now().isoformat(),
             "analysis_type": "tims_v8"}
    return state, log, status, process


def _texts(result):
    return [span.children for span in result[0]]


def test_unchanged_log_is_not_sent_but_the_process_is_polled(running_job):
    state, _, _, process = running_job
    first = ac.update_progress(1, state, "", "all", 50)
    assert "hello" in _texts(first)
    if "_log_display_token" in state:
        assert all(isinstance(v, str) for v in state["_log_display_token"][2])
    second = ac.update_progress(2, state, "", "all", 50)
    assert second[0] is no_update
    assert second[4] is no_update
    assert process.poll.call_count == 2


def test_another_tab_gets_its_first_display_even_if_shared_reader_is_warm(running_job):
    state, _, _, _ = running_job
    second_tab = copy.deepcopy(state)
    ac.update_progress(1, state, "", "all", 50)
    second = ac.update_progress(1, second_tab, "", "all", 50)
    assert "hello" in _texts(second)


@pytest.mark.parametrize("final_status", ["finished", "error", "stopped"])
def test_silent_termination_is_detected_and_final_output_count_is_current(running_job, final_status):
    state, log, status, process = running_job
    ac.update_progress(1, state, "", "all", 50)
    process.returncode = 0 if final_status == "finished" else -15
    process.poll.return_value = process.returncode
    if final_status == "stopped":
        status.write_text("stopped", encoding="utf-8")
    (log.parent / "last.png").write_bytes(b"")
    result = ac.update_progress(2, state, "", "all", 50)
    assert result[4]["is_running"] is False
    assert result[5] is True
    assert "出力: 1 ファイル" in result[3]
    assert {"finished": "✅", "error": "❌", "stopped": "⏹"}[final_status] in result[8]
    assert status.read_text() == final_status


def test_exit_detail_is_included_in_the_last_log_display(running_job, monkeypatch):
    state, log, _, process = running_job
    ac.update_progress(1, state, "", "all", 50)
    handle = log.open("a", encoding="utf-8")
    monkeypatch.setitem(ac._process_state, "log_file_handle", handle)
    process.returncode = -9
    process.poll.return_value = -9
    result = ac.update_progress(2, state, "", "all", 50)
    assert any("[EXIT]" in text for text in _texts(result))
    assert handle.closed


def test_all_lines_search_and_filter_change_work_without_a_log_append(running_job):
    state, log, _, _ = running_job
    log.write_text("ancient ERROR target\n" + "noise\n" * 7000, encoding="utf-8")
    limited = ac.update_progress(1, state, "target", "error", 50)
    assert _texts(limited) == []
    full = ac.update_progress(2, state, "target", "error", 0)
    assert _texts(full) == ["ancient ERROR target"]
    changed = ac.update_progress(3, state, "noise", "all", 50)
    assert _texts(changed) == ["noise"] * 50
    assert ar.get_analysis_log_full(str(log)).startswith("ancient ERROR target")


def test_old_marker_outside_tail_is_still_used_by_progress(running_job):
    state, log, _, _ = running_job
    log.write_text("[stage] Finding Markers\n" + "noise\n" * 7000, encoding="utf-8")
    result = ac.update_progress(1, state, "", "all", 50)
    assert "Markers" in result[3]
    log.write_text("Reading DESI data\nRunning PCA\n", encoding="utf-8")
    state["analysis_type"] = "desi_v8"
    result = ac.update_progress(2, state, "", "all", 50)
    assert "PCA" in result[3]
