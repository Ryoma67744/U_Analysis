# =============================================================================
# MSI Analysis Application - 解析ウォッチャー
#
# 解析プロセスの終了をサーバ側で待ち受け、完了処理を実行する。
#
# なぜ必要か:
#   R プロセスはブラウザではなく Dash アプリの子として起動されるため、
#   タブを閉じても計算は続く。しかし完了処理（ステータス更新・結果の
#   プロジェクト登録・レシート生成・子プロセスの回収）は、これまで
#   ブラウザのポーリング callback の中にしか無かった。
#   その結果「計算は終わっているのに、アプリからは永久に実行中に見え、
#   結果もプロジェクトに紐づかない」という状態になっていた。
#
#   本モジュールは起動と同時に daemon スレッドを立て、process.wait() で
#   終了を待つ。ブラウザの有無に一切依存しない。
#   wait() は子プロセスを回収するので、ゾンビ化も同時に防げる。
#
# 制約:
#   このスレッドは Dash プロセスの中にある。コンテナ自体が再起動されると
#   Dash アプリ（＝コンテナの PID 1）ごと消え、R もカーネルに SIGKILL される。
#   その場合は analysis_finalizer.reconcile_stale_jobs() が起動時に締める。
# =============================================================================

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("msi.job_watcher")

# 監視スレッドの管理（PID → Thread）。再入防止と診断用。
_watchers: dict = {}
_watchers_lock = threading.Lock()


def watch(process, output_dir, *, status_file: Optional[str] = None,
          log_file_handle=None, job: Optional[dict] = None) -> Optional[threading.Thread]:
    """解析プロセスの終了を待ち、完了処理を行うスレッドを起動する。

    失敗しても解析本体は壊さない（監視が無いだけで従来の挙動に戻る）。
    """
    if process is None or not output_dir:
        return None

    pid = getattr(process, "pid", None)
    if pid is None:
        return None

    with _watchers_lock:
        existing = _watchers.get(pid)
        if existing is not None and existing.is_alive():
            logger.debug("pid=%s は既に監視中", pid)
            return existing

    status_path = status_file or str(Path(output_dir) / "log" / "analysis_status.txt")

    def _run():
        try:
            returncode = process.wait()   # ここで子プロセスを回収する
        except Exception as e:  # noqa: BLE001
            logger.warning("プロセス待機に失敗 (pid=%s): %s", pid, e)
            returncode = None
        finally:
            with _watchers_lock:
                _watchers.pop(pid, None)

        status = _resolve_status(returncode, status_path)
        logger.info("解析プロセス終了を検出: pid=%s rc=%s → status=%s",
                    pid, returncode, status)

        _write_exit_note(log_file_handle, returncode)
        _close_handle(log_file_handle)
        _write_status(status_path, status)

        try:
            from app.services.analysis_finalizer import finalize
            finalize(output_dir, status=status, job=job, source="watcher")
        except Exception:  # noqa: BLE001
            logger.exception("完了処理でエラー (pid=%s)", pid)

    t = threading.Thread(target=_run, name=f"job-watcher-{pid}", daemon=True)
    with _watchers_lock:
        _watchers[pid] = t
    t.start()
    logger.info("解析ウォッチャーを起動: pid=%s → %s", pid, output_dir)
    return t


def _resolve_status(returncode, status_path: str) -> str:
    """終了コードから状態を決める。ユーザーが停止した場合はそれを尊重する。"""
    try:
        cur = Path(status_path).read_text(encoding="utf-8").strip()
        if cur == "stopped":
            return "stopped"
    except OSError:
        pass
    if returncode == 0:
        return "finished"
    return "error"


def _write_status(status_path: str, status: str) -> None:
    try:
        p = Path(status_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(status, encoding="utf-8")
    except OSError as e:
        logger.warning("ステータスの書き込みに失敗: %s", e)


def _write_exit_note(log_file_handle, returncode) -> None:
    """終了コード/シグナルを解析ログにも残す。

    check_process_completion と同じ情報を、ブラウザが無くても残すため。
    負値はシグナルによる強制終了（-9 OOM killer / -15 停止要求 など）。
    """
    if log_file_handle is None or returncode in (None, 0):
        return
    try:
        if returncode < 0:
            import signal as _signal
            try:
                name = _signal.Signals(-returncode).name
            except (ValueError, AttributeError):
                name = "UNKNOWN"
            detail = f"シグナル {name}({-returncode}) による強制終了"
        else:
            detail = f"終了コード {returncode}"
        log_file_handle.write(f"\n[EXIT] R プロセスは {detail} で終了しました。\n")
        log_file_handle.flush()
    except Exception as e:  # noqa: BLE001
        logger.debug("終了コードのログ追記に失敗（非重大）: %s", e)


def _close_handle(log_file_handle) -> None:
    if log_file_handle is None:
        return
    try:
        log_file_handle.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("ログハンドルのクローズに失敗（非重大）: %s", e)


def active_count() -> int:
    """監視中のジョブ数（診断用）"""
    with _watchers_lock:
        return sum(1 for t in _watchers.values() if t.is_alive())
