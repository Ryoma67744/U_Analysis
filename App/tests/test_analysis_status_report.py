"""解析の生存確認レポート / PID 生存確認のテスト (ver57.0)

担保したいこと:
  1. 「止まっている」「停滞している」「動いている」を取り違えないこと
  2. 確認そのものが解析を殺さないこと（Windows の os.kill 事故の回帰テスト）
"""

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

from app.services import job_registry


# App/tools/ はパッケージではないのでファイルパスから直接読み込む
_TOOL_PATH = Path(__file__).resolve().parent.parent / "tools" / "analysis_status_report.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("analysis_status_report", _TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


report = _load_tool()


# ---------------------------------------------------------------------------
# 判定ロジック
# ---------------------------------------------------------------------------

class TestClassify:

    def test_alive_and_recently_updated_is_running(self):
        assert report.classify(
            finalized=False, alive=True, status="running",
            idle_sec=10.0, stall_sec=1800.0) == "running"

    def test_dead_and_unfinalized_is_dead(self):
        """プロセスが居ないのに完了処理が済んでいない = 止まっている。

        コンテナ再起動や OOM kill で R ごと消えた場合がこれ。
        """
        assert report.classify(
            finalized=False, alive=False, status="running",
            idle_sec=10.0, stall_sec=1800.0) == "dead"

    def test_alive_but_idle_beyond_threshold_is_stalled(self):
        assert report.classify(
            finalized=False, alive=True, status="running",
            idle_sec=3600.0, stall_sec=1800.0) == "stalled"

    def test_busy_cpu_beats_idle_log(self):
        """CPU を食っているならログが伸びていなくても計算中。

        RPCA / UMAP / DEG は数十分ログを出さないので、ここを救わないと
        正常な長時間解析を「停滞」と誤検出する。
        """
        assert report.classify(
            finalized=False, alive=True, status="running",
            idle_sec=7200.0, stall_sec=1800.0, cpu_percent=180.0) == "running"

    def test_idle_cpu_with_idle_log_is_stalled(self):
        assert report.classify(
            finalized=False, alive=True, status="running",
            idle_sec=7200.0, stall_sec=1800.0, cpu_percent=0.0) == "stalled"

    @pytest.mark.parametrize("status", ["finished", "error", "stopped"])
    def test_terminal_status_is_reported_as_is(self, status):
        assert report.classify(
            finalized=True, alive=False, status=status,
            idle_sec=None, stall_sec=1800.0) == status

    def test_user_stop_is_not_reported_as_dead(self):
        """利用者が止めた直後は完了処理が未でも 'stopped'。

        ここを dead にすると「勝手に落ちた」と読めてしまう。
        """
        assert report.classify(
            finalized=False, alive=False, status="stopped",
            idle_sec=1.0, stall_sec=1800.0) == "stopped"

    def test_no_activity_info_does_not_claim_stalled(self):
        """更新時刻が取れない（ログが無い）だけで停滞とは言わない。"""
        assert report.classify(
            finalized=False, alive=True, status="running",
            idle_sec=None, stall_sec=1800.0) == "running"


class TestOverallVerdict:

    def test_worst_state_wins(self):
        jobs = [{"verdict": "finished"}, {"verdict": "running"}, {"verdict": "dead"}]
        assert report.overall_verdict(jobs) == "dead"

    def test_stalled_beats_running(self):
        assert report.overall_verdict(
            [{"verdict": "running"}, {"verdict": "stalled"}]) == "stalled"

    def test_empty_is_none(self):
        assert report.overall_verdict([]) == "none"


# ---------------------------------------------------------------------------
# 生存確認が対象を殺さないこと（回帰テスト）
# ---------------------------------------------------------------------------

class TestPidAliveIsReadOnly:
    """★ ver57.0 の回帰テスト。

    Windows の CPython では os.kill(pid, 0) が TerminateProcess を呼ぶため、
    生存確認のつもりで解析プロセスを殺していた。この経路が戻ると
    「進捗を見たら解析が終了した」という再現しにくい事故になる。
    """

    def _force_no_psutil(self, monkeypatch):
        # sys.modules に None を入れると import は ImportError になる
        monkeypatch.setitem(sys.modules, "psutil", None)

    @pytest.mark.parametrize("mod", [job_registry, report])
    def test_does_not_call_os_kill_on_windows(self, monkeypatch, mod):
        self._force_no_psutil(monkeypatch)
        monkeypatch.setattr(mod, "_is_windows", lambda: True)

        called = []
        monkeypatch.setattr(os, "kill", lambda *a, **k: called.append(a))
        monkeypatch.setattr(mod, "_pid_alive_windows", lambda pid: True)

        alive = (mod.is_pid_alive(4321) if mod is job_registry
                 else mod.pid_alive(4321))

        assert alive is True
        assert called == [], "Windows で os.kill が呼ばれた（対象を強制終了してしまう）"

    def test_posix_still_uses_signal_zero(self, monkeypatch):
        """POSIX ではシグナル 0 は何も送らないので従来どおりで良い。"""
        self._force_no_psutil(monkeypatch)
        monkeypatch.setattr(job_registry, "_is_windows", lambda: False)

        called = []

        def fake_kill(pid, sig):
            called.append((pid, sig))

        monkeypatch.setattr(os, "kill", fake_kill)
        assert job_registry.is_pid_alive(4321) is True
        assert called == [(4321, 0)]

    def test_self_pid_is_alive(self):
        assert report.pid_alive(os.getpid()) is True

    @pytest.mark.parametrize("bad", [None, "", 0, -1, "abc"])
    def test_invalid_pid_is_not_alive(self, bad):
        assert report.pid_alive(bad) is False


# ---------------------------------------------------------------------------
# レポート全体
# ---------------------------------------------------------------------------

def _make_job(tmp_path, *, pid, finalized=False, status="running",
              log_age_sec=0.0, name="Analysis_x"):
    out = tmp_path / "proj" / name
    log = out / "log"
    log.mkdir(parents=True, exist_ok=True)
    (log / "analysis_job.json").write_text(json.dumps({
        "schema": 1, "pid": pid, "output_dir": str(out),
        "analysis_type": "tims_v8", "analyst": "tester",
        "started_at": "2026-08-18T01:00:00", "finalized": finalized,
    }, ensure_ascii=False), encoding="utf-8")
    (log / "analysis_status.txt").write_text(status, encoding="utf-8")
    lf = log / "analysis_log.txt"
    lf.write_text(">> [TIMS] Reading parquet...\n", encoding="utf-8")
    if log_age_sec:
        old = time.time() - log_age_sec
        os.utime(lf, (old, old))
    return out


def _run(argv):
    """main() を走らせて終了コードを返す"""
    return report.main(argv)


class TestReportEndToEnd:

    def test_dead_job_exits_with_dead_code(self, tmp_path, capsys):
        # 存在しない PID（自プロセスより十分大きい値を避け、確実に居ない値を使う）
        _make_job(tmp_path, pid=0x7FFFFFFE)
        code = _run(["--root", str(tmp_path), "--cpu-interval", "0"])
        out = capsys.readouterr().out

        assert code == report.EXIT_DEAD
        assert "停止しています" in out

    def test_live_job_exits_ok(self, tmp_path, capsys):
        _make_job(tmp_path, pid=os.getpid())
        code = _run(["--root", str(tmp_path), "--cpu-interval", "0"])
        out = capsys.readouterr().out

        assert code == report.EXIT_OK
        assert "実行中" in out

    def test_stalled_job_exits_with_stalled_code(self, tmp_path, capsys):
        _make_job(tmp_path, pid=os.getpid(), log_age_sec=3 * 3600)
        code = _run(["--root", str(tmp_path), "--cpu-interval", "0",
                     "--stall-minutes", "30", "--no-scan-outputs"])
        out = capsys.readouterr().out

        assert code == report.EXIT_STALLED
        assert "停滞" in out

    def test_output_file_activity_rescues_silent_log(self, tmp_path, capsys):
        """ログが伸びていなくても、出力ファイルが増えていれば実行中。

        R は PNG / RDS を書く工程でログを出さない。ログだけを見ると
        正常な解析を停止と誤診する。
        """
        out_dir = _make_job(tmp_path, pid=os.getpid(), log_age_sec=3 * 3600)
        (out_dir / "umap.png").write_text("x", encoding="utf-8")

        code = _run(["--root", str(tmp_path), "--cpu-interval", "0",
                     "--stall-minutes", "30"])
        assert code == report.EXIT_OK
        assert "実行中" in capsys.readouterr().out

    def test_no_jobs_exits_with_none_code(self, tmp_path, capsys):
        code = _run(["--root", str(tmp_path), "--cpu-interval", "0"])
        assert code == report.EXIT_NONE
        assert "見つかりませんでした" in capsys.readouterr().out

    def test_json_output_is_parsable(self, tmp_path, capsys):
        _make_job(tmp_path, pid=os.getpid())
        _run(["--root", str(tmp_path), "--cpu-interval", "0", "--json"])
        data = json.loads(capsys.readouterr().out)

        assert data["overall"] == "running"
        assert data["jobs"][0]["pid"] == os.getpid()
        assert data["jobs"][0]["analysis_type"] == "tims_v8"

    def test_running_only_filters_finalized(self, tmp_path, capsys):
        _make_job(tmp_path, pid=os.getpid(), finalized=True,
                  status="finished", name="Analysis_done")
        code = _run(["--root", str(tmp_path), "--cpu-interval", "0",
                     "--running-only", "--json"])
        data = json.loads(capsys.readouterr().out)

        assert data["jobs"] == []
        assert code == report.EXIT_NONE

    def test_running_only_says_how_many_finished(self, tmp_path, capsys):
        """★ ver57.4: 「1 件も無い」と「絞り込んだ結果ゼロ」を区別する。

        全部が完了済みのときに「記録が見つかりませんでした」と出していたため、
        探索パスが違うのかと疑わせていた（本番で実際に迷った）。
        """
        for i in range(2):
            _make_job(tmp_path, pid=os.getpid(), finalized=True,
                      status="finished", name=f"Analysis_done{i}")

        _run(["--root", str(tmp_path), "--cpu-interval", "0", "--running-only"])
        out = capsys.readouterr().out

        assert "実行中の解析はありません" in out
        assert "完了済み 2 件" in out
        assert "見つかりませんでした" not in out

    def test_genuinely_empty_still_says_not_found(self, tmp_path, capsys):
        """本当に 1 件も無いときは、探索フォルダを示す従来の案内を出すこと。"""
        _run(["--root", str(tmp_path), "--cpu-interval", "0", "--running-only"])
        out = capsys.readouterr().out

        assert "見つかりませんでした" in out
        assert "探索したフォルダ" in out

    def test_exit_note_is_surfaced(self, tmp_path, capsys):
        """[EXIT] 行は「無言で消えた」と「エラーで落ちた」を分ける唯一の手掛かり。"""
        out_dir = _make_job(tmp_path, pid=0x7FFFFFFE)
        log = out_dir / "log" / "analysis_log.txt"
        log.write_text(
            ">> [TIMS] Reading parquet...\n"
            "[EXIT] R プロセスは シグナル SIGKILL(9) による強制終了 で終了しました。\n",
            encoding="utf-8")

        _run(["--root", str(tmp_path), "--cpu-interval", "0"])
        assert "SIGKILL" in capsys.readouterr().out


class TestUnfinalizedJobsComeFirst:

    def test_running_job_listed_before_finished(self, tmp_path, capsys):
        _make_job(tmp_path, pid=os.getpid(), finalized=True,
                  status="finished", name="Analysis_done")
        _make_job(tmp_path, pid=os.getpid(), name="Analysis_live")

        _run(["--root", str(tmp_path), "--cpu-interval", "0", "--json"])
        data = json.loads(capsys.readouterr().out)

        assert data["jobs"][0]["output_dir"].endswith("Analysis_live")
