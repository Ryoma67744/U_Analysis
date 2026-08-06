"""Tests for 解析ジョブ台帳 / 完了処理 / ウォッチャー (ver51.0)

ブラウザを閉じても解析の後片付けが行われることを担保する。
この領域は ver51.0 以前はテストが 1 件も無かった。
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from dash import no_update

from app.services import analysis_finalizer, job_registry, job_watcher


# ---------------------------------------------------------------------------
# ジョブ台帳
# ---------------------------------------------------------------------------

class TestJobRegistry:

    def test_write_and_read_roundtrip(self, tmp_path):
        out = tmp_path / "result"
        job_registry.write_job(
            out, pid=12345, analysis_type="tims_v8",
            project_id="P1", sub_project_id="S1",
            data_folder="/data/foo", script_path="/x/runtime.R",
        )

        got = job_registry.read_job(out)

        assert got["pid"] == 12345
        assert got["project_id"] == "P1"
        assert got["sub_project_id"] == "S1"
        assert got["data_folder"] == "/data/foo"
        assert got["finalized"] is False
        assert got["started_at"]

    def test_written_under_log_dir(self, tmp_path):
        """台帳はログと同じ場所に置く（結果フォルダを汚さない）"""
        out = tmp_path / "result"
        job_registry.write_job(out, pid=1)
        assert (out / "log" / "analysis_job.json").is_file()

    def test_read_missing_returns_none(self, tmp_path):
        assert job_registry.read_job(tmp_path / "nope") is None

    def test_read_corrupt_returns_none(self, tmp_path):
        out = tmp_path / "result"
        p = job_registry.job_file_path(out)
        p.parent.mkdir(parents=True)
        p.write_text("{ broken", encoding="utf-8")
        assert job_registry.read_job(out) is None

    def test_mark_finalized_is_one_shot(self, tmp_path):
        """二重実行防止。最初の 1 回だけ True を返す。"""
        out = tmp_path / "result"
        job_registry.write_job(out, pid=1)

        assert job_registry.mark_finalized(out) is True
        assert job_registry.mark_finalized(out) is False
        assert job_registry.read_job(out)["finalized"] is True

    def test_mark_finalized_without_job_returns_false(self, tmp_path):
        assert job_registry.mark_finalized(tmp_path / "nope") is False

    def test_is_pid_alive_self(self):
        assert job_registry.is_pid_alive(os.getpid()) is True

    def test_is_pid_alive_bogus(self):
        assert job_registry.is_pid_alive(999_999_999) is False
        assert job_registry.is_pid_alive(0) is False
        assert job_registry.is_pid_alive(-1) is False
        assert job_registry.is_pid_alive(None) is False
        assert job_registry.is_pid_alive("abc") is False

    def test_zombie_is_not_alive(self):
        """ゾンビを「実行中」と誤判定しないこと。

        誤判定すると同時実行ガードが永久に塞がる。
        """
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        for _ in range(50):
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        # ここではまだ wait() していないのでゾンビのまま
        try:
            assert job_registry.is_pid_alive(proc.pid) is False
        finally:
            proc.wait()

    def test_find_running_and_stale(self, tmp_path):
        root = tmp_path / "data"
        alive_dir = root / "projA" / "run1"
        dead_dir = root / "projB" / "run2"
        job_registry.write_job(alive_dir, pid=os.getpid(), analysis_type="tims_v8")
        job_registry.write_job(dead_dir, pid=999_999_999)

        running = job_registry.find_running_job([str(root)])
        stale = job_registry.find_stale_jobs([str(root)])

        assert running is not None
        assert Path(running["output_dir"]).name == "run1"
        assert [Path(j["output_dir"]).name for j in stale] == ["run2"]

    def test_finalized_jobs_are_ignored(self, tmp_path):
        root = tmp_path / "data"
        d = root / "projA" / "run1"
        job_registry.write_job(d, pid=os.getpid())
        job_registry.mark_finalized(d)

        assert job_registry.find_running_job([str(root)]) is None
        assert job_registry.find_stale_jobs([str(root)]) == []

    def test_missing_root_is_tolerated(self, tmp_path):
        assert job_registry.find_running_job([str(tmp_path / "nope")]) is None

    @pytest.mark.parametrize("depth", [0, 1, 2, 3])
    def test_found_at_various_depths(self, tmp_path, depth):
        """出力先の階層は UI で自由に決まる。

        ver51.0 は 2 階層固定 glob だったため、深さがずれると再接続が
        無言で起きなかった。
        """
        root = tmp_path / "data"
        out = root.joinpath(*[f"lv{i}" for i in range(depth)])
        job_registry.write_job(out, pid=os.getpid())

        running = job_registry.find_running_job([str(root)])

        assert running is not None
        assert Path(running["output_dir"]) == out

    def test_no_duplicates_across_depths(self, tmp_path):
        """複数の深さ glob で同じ台帳を二重に拾わないこと"""
        root = tmp_path / "data"
        job_registry.write_job(root / "p" / "run", pid=os.getpid())
        assert len(job_registry.find_jobs([str(root)])) == 1


# ---------------------------------------------------------------------------
# 完了処理
# ---------------------------------------------------------------------------

class TestFinalize:

    def test_runs_once_only(self, tmp_path, monkeypatch):
        """ウォッチャーと callback の両方から呼ばれても 1 回しか動かない"""
        out = tmp_path / "result"
        job_registry.write_job(out, pid=1, project_id="P", sub_project_id="S")
        calls = []
        monkeypatch.setattr(analysis_finalizer, "_link_to_project",
                            lambda *a, **k: calls.append("link"))
        monkeypatch.setattr(analysis_finalizer, "_write_receipt",
                            lambda *a, **k: calls.append("receipt"))

        first = analysis_finalizer.finalize(out, status="finished", source="watcher")
        second = analysis_finalizer.finalize(out, status="finished", source="callback")

        assert first["done"] is True and first["skipped"] is False
        assert second["done"] is False and second["skipped"] is True
        assert calls == ["link", "receipt"]

    def test_error_status_skips_registration(self, tmp_path, monkeypatch):
        """失敗した解析をプロジェクトに登録したりレシートを作ったりしない"""
        out = tmp_path / "result"
        job_registry.write_job(out, pid=1, project_id="P", sub_project_id="S")
        calls = []
        monkeypatch.setattr(analysis_finalizer, "_link_to_project",
                            lambda *a, **k: calls.append("link"))
        monkeypatch.setattr(analysis_finalizer, "_write_receipt",
                            lambda *a, **k: calls.append("receipt"))

        res = analysis_finalizer.finalize(out, status="error", source="watcher")

        assert res["done"] is True
        assert calls == []

    def test_uses_data_folder_from_job(self, tmp_path, monkeypatch):
        """data_folder は台帳から取る。

        旧実装は完了時に未定義の `data_folder` を参照して NameError を出し、
        毎回「結果ディレクトリの保存に失敗」と表示していた。
        """
        out = tmp_path / "result"
        job_registry.write_job(out, pid=1, project_id="P", sub_project_id="S",
                               data_folder="/data/raw")
        seen = {}
        monkeypatch.setattr(analysis_finalizer, "_write_receipt", lambda *a, **k: None)
        import app.services.project_manager as pm
        monkeypatch.setattr(pm, "save_sub_project_result_dir",
                            lambda p, s, d: seen.setdefault("result_dir", (p, s, d)))
        monkeypatch.setattr(pm, "update_sub_project",
                            lambda p, s, patch: seen.setdefault("patch", patch))

        res = analysis_finalizer.finalize(out, status="finished")

        assert res["errors"] == []
        assert seen["result_dir"] == ("P", "S", str(out))
        assert seen["patch"] == {"data_folder": "/data/raw"}

    def test_registration_failure_is_reported_not_raised(self, tmp_path, monkeypatch):
        out = tmp_path / "result"
        job_registry.write_job(out, pid=1, project_id="P", sub_project_id="S")
        monkeypatch.setattr(analysis_finalizer, "_write_receipt", lambda *a, **k: None)
        import app.services.project_manager as pm

        def _boom(*a, **k):
            raise RuntimeError("ディスク満杯")
        monkeypatch.setattr(pm, "save_sub_project_result_dir", _boom)

        res = analysis_finalizer.finalize(out, status="finished")

        assert res["done"] is True
        assert any("ディスク満杯" in e for e in res["errors"])

    def test_no_project_is_not_an_error(self, tmp_path, monkeypatch):
        out = tmp_path / "result"
        job_registry.write_job(out, pid=1)   # project_id なし
        monkeypatch.setattr(analysis_finalizer, "_write_receipt", lambda *a, **k: None)

        res = analysis_finalizer.finalize(out, status="finished")

        assert res["errors"] == []

    def test_empty_output_dir_is_rejected(self):
        res = analysis_finalizer.finalize("", status="finished")
        assert res["done"] is False
        assert res["errors"]


# ---------------------------------------------------------------------------
# 起動時の後始末
# ---------------------------------------------------------------------------

class TestReconcile:

    def _make(self, root, name, pid, status):
        d = root / "proj" / name
        job_registry.write_job(d, pid=pid)
        sf = d / "log" / "analysis_status.txt"
        sf.write_text(status, encoding="utf-8")
        (d / "log" / "analysis_log.txt").write_text("...\n", encoding="utf-8")
        return d

    def test_dead_running_job_becomes_error(self, tmp_path, monkeypatch):
        """コンテナ再起動で殺された解析が running のまま残らないこと"""
        root = tmp_path / "data"
        d = self._make(root, "run1", 999_999_999, "running")
        monkeypatch.setattr(analysis_finalizer, "_write_receipt", lambda *a, **k: None)

        closed = analysis_finalizer.reconcile_stale_jobs([str(root)])

        assert [Path(c).name for c in closed] == ["run1"]
        assert (d / "log" / "analysis_status.txt").read_text().strip() == "error"
        assert "[RECOVER]" in (d / "log" / "analysis_log.txt").read_text()
        assert job_registry.read_job(d)["finalized"] is True

    def test_already_finished_job_is_left_alone(self, tmp_path):
        root = tmp_path / "data"
        d = self._make(root, "run1", 999_999_999, "finished")

        closed = analysis_finalizer.reconcile_stale_jobs([str(root)])

        assert closed == []
        assert (d / "log" / "analysis_status.txt").read_text().strip() == "finished"
        # 再走査で拾い続けないよう印は付く
        assert job_registry.read_job(d)["finalized"] is True

    def test_live_job_is_left_alone(self, tmp_path):
        root = tmp_path / "data"
        d = self._make(root, "run1", os.getpid(), "running")

        closed = analysis_finalizer.reconcile_stale_jobs([str(root)])

        assert closed == []
        assert (d / "log" / "analysis_status.txt").read_text().strip() == "running"


# ---------------------------------------------------------------------------
# ウォッチャー（本丸: ブラウザ無しで完了処理が走ること）
# ---------------------------------------------------------------------------

class TestWatcher:

    def _wait(self, thread, timeout=15):
        thread.join(timeout=timeout)
        assert not thread.is_alive(), "ウォッチャーが終わらない"

    def test_finalizes_without_any_browser(self, tmp_path, monkeypatch):
        """これが本題。ブラウザ（callback）を一切介さずに後片付けされること。"""
        out = tmp_path / "result"
        (out / "log").mkdir(parents=True)
        (out / "log" / "analysis_status.txt").write_text("running", encoding="utf-8")
        job_registry.write_job(out, pid=0)
        done = {}
        monkeypatch.setattr(
            analysis_finalizer, "_link_to_project",
            lambda o, j, r: done.setdefault("link", True))
        monkeypatch.setattr(
            analysis_finalizer, "_write_receipt",
            lambda o, r: done.setdefault("receipt", True))

        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        t = job_watcher.watch(proc, out)
        self._wait(t)

        assert (out / "log" / "analysis_status.txt").read_text().strip() == "finished"
        assert done == {"link": True, "receipt": True}

    def test_nonzero_exit_becomes_error(self, tmp_path, monkeypatch):
        out = tmp_path / "result"
        (out / "log").mkdir(parents=True)
        job_registry.write_job(out, pid=0)
        monkeypatch.setattr(analysis_finalizer, "_link_to_project", lambda *a: None)
        monkeypatch.setattr(analysis_finalizer, "_write_receipt", lambda *a: None)

        proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(3)"])
        t = job_watcher.watch(proc, out)
        self._wait(t)

        assert (out / "log" / "analysis_status.txt").read_text().strip() == "error"

    def test_user_stop_is_respected(self, tmp_path, monkeypatch):
        """ユーザーが停止した場合、rc=0 でも finished にしない"""
        out = tmp_path / "result"
        (out / "log").mkdir(parents=True)
        (out / "log" / "analysis_status.txt").write_text("stopped", encoding="utf-8")
        job_registry.write_job(out, pid=0)
        monkeypatch.setattr(analysis_finalizer, "_link_to_project", lambda *a: None)
        monkeypatch.setattr(analysis_finalizer, "_write_receipt", lambda *a: None)

        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        t = job_watcher.watch(proc, out)
        self._wait(t)

        assert (out / "log" / "analysis_status.txt").read_text().strip() == "stopped"

    def test_child_is_reaped_no_zombie(self, tmp_path, monkeypatch):
        """wait() で子を回収するのでゾンビが残らないこと。

        ゾンビが残ると同時実行ガードが「別の解析が実行中です」で
        以後の解析を全て拒否しうる。
        """
        out = tmp_path / "result"
        (out / "log").mkdir(parents=True)
        job_registry.write_job(out, pid=0)
        monkeypatch.setattr(analysis_finalizer, "_link_to_project", lambda *a: None)
        monkeypatch.setattr(analysis_finalizer, "_write_receipt", lambda *a: None)

        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        t = job_watcher.watch(proc, out)
        self._wait(t)

        assert proc.returncode == 0
        assert job_registry.is_pid_alive(proc.pid) is False

    def test_exit_note_written_to_log(self, tmp_path, monkeypatch):
        """異常終了の理由がログに残ること（ブラウザ無しでも）"""
        out = tmp_path / "result"
        (out / "log").mkdir(parents=True)
        log_path = out / "log" / "analysis_log.txt"
        job_registry.write_job(out, pid=0)
        monkeypatch.setattr(analysis_finalizer, "_link_to_project", lambda *a: None)
        monkeypatch.setattr(analysis_finalizer, "_write_receipt", lambda *a: None)

        with open(log_path, "w", encoding="utf-8") as fh:
            proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(7)"])
            t = job_watcher.watch(proc, out, log_file_handle=fh)
            self._wait(t)

        assert "[EXIT]" in log_path.read_text(encoding="utf-8")
        assert "終了コード 7" in log_path.read_text(encoding="utf-8")

    def test_watch_returns_none_without_process(self, tmp_path):
        assert job_watcher.watch(None, tmp_path) is None

    def test_end_to_end_via_start_analysis_process(self, tmp_path, monkeypatch):
        """起動 → 台帳作成 → ウォッチャー → 完了処理 を通しで確認する。

        ブラウザ（dcc.Interval の callback）を一切呼ばずに、
        analysis_status.txt が finished になり結果が登録されること。
        """
        import psutil as _psutil
        from app.services import analysis_runner

        # 事前ゲート（他の Rscript 実行中か）は本題ではないので無効化する。
        # analysis_runner は関数内で psutil を import するため、モジュール側を差し替える。
        monkeypatch.setattr(_psutil, "process_iter", lambda *a, **k: [])
        out = tmp_path / "result"
        script = tmp_path / "fake_analysis.py"
        script.write_text("print('All Done')\n", encoding="utf-8")

        done = {}
        monkeypatch.setattr(analysis_finalizer, "_link_to_project",
                            lambda o, j, r: done.setdefault("job", j))
        monkeypatch.setattr(analysis_finalizer, "_write_receipt",
                            lambda o, r: done.setdefault("receipt", True))

        res = analysis_runner.start_analysis_process(
            str(script), str(out),
            interpreter=[sys.executable, "-u"],
            job_meta={"analysis_type": "tims_v8", "project_id": "P",
                      "sub_project_id": "S", "data_folder": "/data/raw"},
        )
        assert res["success"], res.get("message")

        # 台帳が起動時点で書かれている（＝ブラウザが無くても後から辿れる）
        job = job_registry.read_job(out)
        assert job["pid"] == res["pid"]
        assert job["data_folder"] == "/data/raw"

        for t in list(job_watcher._watchers.values()):
            t.join(timeout=20)
        # ウォッチャーの完了処理が終わるまで少し待つ
        for _ in range(100):
            if (out / "log" / "analysis_status.txt").read_text().strip() != "running":
                break
            time.sleep(0.05)

        assert (out / "log" / "analysis_status.txt").read_text().strip() == "finished"
        assert done.get("receipt") is True
        assert done["job"]["data_folder"] == "/data/raw"
        assert job_registry.read_job(out)["finalized"] is True

    def test_maintenance_tools_get_no_watcher(self, tmp_path, monkeypatch):
        """job_meta を渡さない保守ツールは従来どおり（台帳もウォッチャーも無し）"""
        import psutil as _psutil
        from app.services import analysis_runner
        monkeypatch.setattr(_psutil, "process_iter", lambda *a, **k: [])
        out = tmp_path / "result"
        script = tmp_path / "tool.py"
        script.write_text("pass\n", encoding="utf-8")

        res = analysis_runner.start_analysis_process(
            str(script), str(out), interpreter=[sys.executable, "-u"])

        assert res["success"]
        assert job_registry.read_job(out) is None
        res["process"].wait(timeout=20)

    def test_no_double_watch_for_same_pid(self, tmp_path, monkeypatch):
        out = tmp_path / "result"
        (out / "log").mkdir(parents=True)
        job_registry.write_job(out, pid=0)
        monkeypatch.setattr(analysis_finalizer, "_link_to_project", lambda *a: None)
        monkeypatch.setattr(analysis_finalizer, "_write_receipt", lambda *a: None)

        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
        t1 = job_watcher.watch(proc, out)
        t2 = job_watcher.watch(proc, out)

        assert t1 is t2
        self._wait(t1)


# ---------------------------------------------------------------------------
# 再接続コールバック (ver51.1 の回帰修正)
# ---------------------------------------------------------------------------

class TestRestoreCallback:
    """restore_running_analysis の分岐。

    ver51.0 は「同一タブで F5」の経路で no_update を返しており、
    Interval が disabled のまま復帰せず進捗を永久に見失っていた。
    """

    @staticmethod
    def _restore(app_state):
        from app.callbacks.analysis_callbacks import restore_running_analysis
        return restore_running_analysis("/app/settings", app_state)

    def _state(self, tmp_path, pid, status=None):
        out = tmp_path / "result"
        sf = out / "log" / "analysis_status.txt"
        if status is not None:
            sf.parent.mkdir(parents=True, exist_ok=True)
            sf.write_text(status, encoding="utf-8")
        return {
            "is_running": True,
            "process_pid": pid,
            "status_file": str(sf),
            "full_output_dir": str(out),
        }

    def test_same_tab_reload_rearms_polling(self, tmp_path):
        """F5 リロード: Store は残るがコンポーネントは既定値に戻っている。

        ここで Interval を有効化し直さないと進捗が二度と出ない。
        """
        out = self._restore(self._state(tmp_path, os.getpid()))

        assert out[1] is False, "Interval が無効のままでは進捗が出ない"
        assert out[2] != {"display": "none"}   # 停止ボタン
        assert out[3] != {"display": "none"}   # 進捗バー
        assert out[4] != {"display": "none"}   # ログ

    def test_dead_pid_with_finished_status_lets_progress_render(self, tmp_path):
        """閉じている間に完了していた場合、完了表示のためにポーリングを回す"""
        out = self._restore(self._state(tmp_path, 999_999_999, status="finished"))
        assert out[1] is False

    def test_dead_pid_without_status_folds_and_tells_user(self, tmp_path):
        """中断された場合は実行中を畳み、黙って消えないこと"""
        out = self._restore(self._state(tmp_path, 999_999_999, status="running"))

        assert out[0]["is_running"] is False
        assert out[1] is True
        assert out[6] is True, "利用者に何も知らせないまま消えてはいけない"

    def test_no_running_job_is_silent(self, tmp_path, monkeypatch):
        from app.callbacks import analysis_callbacks as ac
        monkeypatch.setattr(ac, "_analysis_search_roots", lambda: [str(tmp_path)])
        out = self._restore({"is_running": False})
        assert out[0] is no_update

    def test_reconnects_from_registry(self, tmp_path, monkeypatch):
        """別ブラウザ: 台帳から組み立て直す"""
        from app.callbacks import analysis_callbacks as ac
        root = tmp_path / "data"
        out_dir = root / "projA" / "run1"
        job_registry.write_job(out_dir, pid=os.getpid(), analysis_type="tims_v8",
                               project_id="P", sub_project_id="S")
        monkeypatch.setattr(ac, "_analysis_search_roots", lambda: [str(root)])

        res = self._restore({"is_running": False})

        assert res[0]["is_running"] is True
        assert res[0]["full_output_dir"] == str(out_dir)
        assert res[0]["analysis_type"] == "tims_v8"
        assert res[0]["start_time"]
        assert res[1] is False


class TestStoppedStatus:
    """停止は「エラー」にしない (ver51.1)"""

    def test_stopped_is_not_overwritten_as_error(self, tmp_path):
        from app.services.analysis_runner import check_process_completion

        sf = tmp_path / "analysis_status.txt"
        sf.write_text("stopped", encoding="utf-8")

        proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(1)"])
        proc.wait()

        assert check_process_completion(proc, str(sf)) == "stopped"

    def test_genuine_error_is_still_error(self, tmp_path):
        from app.services.analysis_runner import check_process_completion

        sf = tmp_path / "analysis_status.txt"
        sf.write_text("running", encoding="utf-8")

        proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(1)"])
        proc.wait()

        assert check_process_completion(proc, str(sf)) == "error"
