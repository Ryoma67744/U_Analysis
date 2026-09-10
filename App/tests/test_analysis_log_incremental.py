"""★ ver66.3: 増分監視でもログ全文から得た末尾・段階記録を維持する。"""

import os
from pathlib import Path

import pytest

from app.services import analysis_log as logs
from app.services import analysis_runner as ar


@pytest.fixture(autouse=True)
def clear_monitors():
    logs._readers.clear()
    ar._output_count_cache.clear()
    yield
    logs._readers.clear()
    ar._output_count_cache.clear()


def _assert_content(snapshot, text):
    lines = text.splitlines()
    assert snapshot.lines == tuple(lines[-600:])
    assert snapshot.markers == "\n".join(
        line for line in lines if line.lstrip().startswith(logs.MARKER_PREFIXES))


def test_long_history_and_small_append_do_not_reread_the_whole_log(tmp_path):
    log = tmp_path / "analysis.log"
    text = "[stage] Finding Markers\n" + "Calculating cluster noise\n" * 6000
    log.write_text(text, encoding="utf-8")
    first = logs.get_log_snapshot(log, "job1")
    _assert_content(first, text)
    assert first.bytes_read >= log.stat().st_size
    assert logs.get_log_snapshot(log, "job1").bytes_read == 0
    extra = "[pass] Second\n[stage] Saving outputs\n"
    with log.open("a", encoding="utf-8") as stream:
        stream.write(extra)
    updated = logs.get_log_snapshot(log, "job1")
    _assert_content(updated, text + extra)
    assert updated.bytes_read <= len(extra.encode()) + 512
    assert updated.bytes_read < log.stat().st_size / 100


def test_utf8_character_marker_and_newline_can_arrive_in_separate_writes(tmp_path):
    log = tmp_path / "analysis.log"
    content = "[stage] 保存処理\r\n進行中\n[plan] 二巡\r次行".encode("utf-8")
    written = b""
    for byte in content:
        written += bytes([byte])
        with log.open("ab") as stream:
            stream.write(bytes([byte]))
        snapshot = logs.get_log_snapshot(log, "job1")
        _assert_content(snapshot, written.decode("utf-8", errors="ignore"))
    assert "�" not in snapshot.tail(600)


@pytest.mark.parametrize("kind", ["shorter", "same_size", "replace", "rewrite_longer"])
def test_truncate_or_replace_discards_the_previous_job_markers(tmp_path, kind):
    log = tmp_path / "analysis.log"
    before = "[stage] Old job\n" + "original noise\n" * 100
    log.write_text(before, encoding="utf-8")
    logs.get_log_snapshot(log, "job1")
    after = "[stage] New job\n"
    if kind == "same_size":
        after += "x" * (len(before) - len(after))
    elif kind == "rewrite_longer":
        after += "new noise\n" * 300
    if kind == "replace":
        replacement = tmp_path / "replacement.log"
        replacement.write_text(after, encoding="utf-8")
        os.replace(replacement, log)
    else:
        log.write_text(after, encoding="utf-8")
    snapshot = logs.get_log_snapshot(log, "job1")
    _assert_content(snapshot, after)
    assert "Old job" not in snapshot.markers


def test_new_generation_or_monitor_restart_rebuilds_cumulative_markers(tmp_path):
    log = tmp_path / "analysis.log"
    text = "[stage] Finding Markers\n" + "noise\n" * 7000
    log.write_text(text, encoding="utf-8")
    first = logs.get_log_snapshot(log, "job1")
    assert logs.get_log_snapshot(log, "job2").bytes_read >= log.stat().st_size
    logs.release_log_snapshot(log, "job1")
    restored = logs.get_log_snapshot(log, "job1")
    assert restored.bytes_read >= log.stat().st_size
    assert (restored.lines, restored.markers) == (first.lines, first.markers)


@pytest.mark.parametrize("text", ["", "a\n", "\n\n", "[stage] 未完", "a\r\nb\rc\v\fd\x85e\u2028f\u2029"])
def test_empty_and_legacy_line_endings_match_splitlines(tmp_path, text):
    log = tmp_path / "analysis.log"
    log.write_bytes(text.encode())
    _assert_content(logs.get_log_snapshot(log), text)


def test_missing_then_recreated_log_recovers(tmp_path):
    log = tmp_path / "analysis.log"
    assert logs.get_log_snapshot(log).lines == ()
    log.write_text("[stage] Reading\n", encoding="utf-8")
    assert logs.get_log_snapshot(log).markers == "[stage] Reading"
    log.unlink()
    assert logs.get_log_snapshot(log).markers == ""
    log.write_text("[stage] Saving\n", encoding="utf-8")
    assert logs.get_log_snapshot(log).markers == "[stage] Saving"


def test_full_api_keeps_the_complete_file_and_trailing_newline(tmp_path):
    log = tmp_path / "analysis.log"
    text = "old match\n" + "noise\n" * 7000
    log.write_text(text, encoding="utf-8")
    logs.get_log_snapshot(log)
    assert ar.get_analysis_log_full(str(log)) == text
    assert ar.get_analysis_log(str(log), last_n=0) == text.rstrip("\n")


def test_output_count_scans_once_then_refreshes_after_interval_or_at_finish(tmp_path, monkeypatch):
    (tmp_path / "a.png").write_bytes(b"")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "a.csv").write_bytes(b"")
    calls = []
    walk = os.walk

    def tracked_walk(path):
        calls.append(path)
        return walk(path)

    monkeypatch.setattr(ar.os, "walk", tracked_walk)
    clock = [100.0]
    monkeypatch.setattr(ar.time, "monotonic", lambda: clock[0])
    assert ar.get_analysis_output_count(tmp_path, "job1") == 2
    assert len(calls) == 1
    (tmp_path / "new.rds").write_bytes(b"")
    assert ar.get_analysis_output_count(tmp_path, "job1") == 2
    assert len(calls) == 1
    clock[0] += 11
    assert ar.get_analysis_output_count(tmp_path, "job1") == 3
    assert len(calls) == 2
    (tmp_path / "final.png").write_bytes(b"")
    assert ar.get_analysis_output_count(tmp_path, "job1", force=True) == 4
    assert len(calls) == 3
    ar.release_analysis_monitor(None, tmp_path, "job1")
    assert not ar._output_count_cache
