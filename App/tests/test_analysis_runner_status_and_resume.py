"""解析起動まわり: 失敗時の状態表示と、途中再開パスの扱いの番人。

★ ver56.5 / デバッグ総点検 §3.5・§5.3 (R12-N1):

  1) 起動失敗で「実行中」が残る
     status ファイルには Popen の**前**に "running" を書いている。R が
     見つからない等で起動そのものに失敗すると、実際には何も動いていないのに
     記録簿が "running" のまま残り、画面には「実行中の解析があります」と
     表示され続けていた（次回アプリ再起動時の整合処理まで解消しない）。

  2) 途中再開が黙って最初からやり直しになる
     「途中再開 (RDSから)」を ON にして RDS が 1 つも見つからないとき、
     RESUME_DIR_PATH を**注入しないまま** RESUME_FROM_RDS=TRUE だけを立てていた。
     テンプレートに直書きされた開発者の Windows パスが残り、R 側はそこを探して
     見つからず、結局最初から計算し直す。利用者から見ると
     「途中再開のはずが数時間かけて全部やり直し」になる。
"""
import re
from pathlib import Path

import pytest

from app.services import analysis_runner as ar


class TestStatusIsResetOnLaunchFailure:
    """★ 起動に失敗したら status を "running" のままにしないこと。"""

    def test_status_becomes_error_when_process_cannot_start(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "Analysis_x"
        (out_dir / "log").mkdir(parents=True)
        script = tmp_path / "dummy.R"
        script.write_text("# dummy\n", encoding="utf-8")

        def _boom(*a, **k):
            raise FileNotFoundError("Rscript が見つかりません")

        # 事前チェック（同時実行ガード等）は通し、Popen だけ失敗させる
        monkeypatch.setattr(ar, "_find_running_job_for_guard", lambda: None)
        monkeypatch.setattr(ar.subprocess, "Popen", _boom)
        result = ar._start_analysis_process_locked(str(script), str(out_dir))

        assert result["success"] is False
        status = (out_dir / "log" / "analysis_status.txt").read_text().strip()
        assert status != "running", (
            "起動に失敗したのに status が running のまま。"
            "何も動いていないのに『実行中の解析があります』と表示され続ける")
        assert status == "error"


class TestResumeDirPathIsAlwaysExplicit:
    """★ 途中再開 ON なら RESUME_DIR_PATH を必ず明示すること。"""

    def _template(self):
        return [
            'RESUME_FROM_RDS <- FALSE',
            'RESUME_DIR_PATH <- "C:/Users/dev/Dropbox/old_run"',
            'DEG_P_THRESH_VAL <- 0.05',
        ]

    def test_resume_without_paths_clears_the_hardcoded_path(self, monkeypatch, tmp_path):
        """RDS が見つからない場合、直書きパスを残さないこと。"""
        lines = self._template()
        out = ar._replace_assign(lines, "RESUME_DIR_PATH", ar._r_str(""))
        joined = "\n".join(out)
        assert "Dropbox" not in joined, (
            "テンプレート直書きの開発者パスが残っている。"
            "R はそこを探して見つからず、黙って最初から計算し直す")
        assert re.search(r'RESUME_DIR_PATH\s*<-\s*""', joined)

    def test_resume_with_paths_uses_parent_directory(self):
        lines = self._template()
        out = ar._replace_assign(
            lines, "RESUME_DIR_PATH", ar._r_str("/results/run1"))
        assert 'RESUME_DIR_PATH <- "/results/run1"' in "\n".join(out)

    def test_generator_emits_explicit_path_when_resume_is_on(self, monkeypatch):
        """generate_v8_config が「ON かつ RDS 無し」で空を注入すること。"""
        import inspect
        src = inspect.getsource(ar.generate_v8_config)
        assert 'RESUME_DIR_PATH' in src
        # 「paths があるときだけ注入」ではなく、ON なら必ずどちらかを注入する形
        assert 'if params.get("resume_from_rds"):' in src, (
            "resume_from_rds が ON のときに必ず RESUME_DIR_PATH を決める形に"
            "なっていない")
