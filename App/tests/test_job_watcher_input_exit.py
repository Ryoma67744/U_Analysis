"""入力準備停止とR開始後停止の終了表示を区別する。"""
import io
import json

from app.services.job_watcher import _write_exit_note


def test_input_preparation_exit_is_not_labeled_as_r(tmp_path):
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    (log_dir / "input_pipeline.json").write_text(
        json.dumps({"stage": "error", "error": "individual axis"}), encoding="utf-8"
    )
    handle = io.StringIO()
    _write_exit_note(handle, 2, output_dir=tmp_path)
    text = handle.getvalue()
    assert "入力準備プロセス" in text
    assert "R解析は開始されていません" in text
    assert "R プロセス" not in text


def test_r_exit_label_is_retained_after_r_started(tmp_path):
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    (log_dir / "input_pipeline.json").write_text(
        json.dumps({"stage": "error", "r_started_at": "2026-09-26T00:00:00"}),
        encoding="utf-8",
    )
    handle = io.StringIO()
    _write_exit_note(handle, 2, output_dir=tmp_path)
    assert "R プロセス" in handle.getvalue()


def test_browser_completion_uses_input_preparation_label(tmp_path):
    from app.services.analysis_runner import check_process_completion

    class Process:
        pid = 987654
        returncode = 2
        def poll(self):
            return self.returncode

    log_dir = tmp_path / "log"
    log_dir.mkdir()
    status = log_dir / "analysis_status.txt"
    status.write_text("running", encoding="utf-8")
    (log_dir / "input_pipeline.json").write_text(
        json.dumps({"stage": "error", "error": "individual axis"}), encoding="utf-8"
    )
    log_path = log_dir / "analysis_log.txt"
    handle = log_path.open("w", encoding="utf-8")
    assert check_process_completion(Process(), str(status), handle) == "error"
    written = log_path.read_text(encoding="utf-8")
    assert "入力準備プロセス" in written
    assert "R解析は開始されていません" in written
