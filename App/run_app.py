#!/usr/bin/env python
"""MSI Analysis Application - Python Launcher"""

import logging
import signal
import sys
import threading
from pathlib import Path

# アプリケーションルートをパスに追加
app_root = Path(__file__).parent
sys.path.insert(0, str(app_root))

from app.services.log_config import setup_logging

setup_logging()
logger = logging.getLogger("msi.startup")

from app.main import app
from app.config import APP_HOST, APP_PORT

# PR-H3 C3: graceful shutdown 用フラグ + 同期
_shutdown_event = threading.Event()


def _flush_logs_and_caches() -> None:
    """SIGTERM 受信時のクリーンアップ: ログ flush + 進行中タスクの整理。

    完了を保証するものではなく、ベストエフォートで以下を行う:
    1. ログハンドラの flush
    2. _project_states stale eviction (メモリ open file 系のクリーンアップ)
    3. edit_lock_manager の cleanup_expired (壊れた lock 残骸を除去)
    4. R subprocess watchdog の状態は維持 (R は自分で終わる/タイムアウトで死ぬ)
    """
    logger.info("Graceful shutdown sequence start")
    try:
        for h in list(logging.getLogger().handlers):
            try:
                h.flush()
            except Exception:
                pass
    except Exception:
        pass
    try:
        from app.callbacks.interactive_callbacks import evict_stale_project_states
        evicted = evict_stale_project_states()
        if evicted:
            logger.info("Evicted %d stale project states", evicted)
    except Exception as e:
        logger.debug("evict_stale_project_states failed: %s", e)
    try:
        from app.services import edit_lock_manager as elm
        elm.cleanup_expired()
    except Exception as e:
        logger.debug("edit_lock cleanup failed: %s", e)
    logger.info("Graceful shutdown sequence end")


def _signal_handler(signum, frame):
    """SIGTERM / SIGINT を受信した時のクリーンハンドラ。

    Docker stop 時に SIGTERM が来る (10 秒以内に終了しないと SIGKILL)。
    本ハンドラで flush + cleanup を実施した後、デフォルト終了処理に移行する。
    """
    sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    logger.info("Received %s, initiating graceful shutdown...", sig_name)
    if not _shutdown_event.is_set():
        _shutdown_event.set()
        _flush_logs_and_caches()
    # Flask app.run() は内部で werkzeug を使い SIGTERM/SIGINT で停止する。
    # 二度目の signal で強制終了 (sys.exit) する。
    sys.exit(0)


def main():
    """アプリケーションのエントリーポイント。"""
    # graceful shutdown 用のシグナルハンドラを登録
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # 起動時バックアップ
    try:
        from app.services.backup_manager import startup_backup
        backed = startup_backup()
        if backed:
            logger.info("Startup backup created: %s", ", ".join(backed))
    except Exception as e:
        logger.warning("Backup warning: %s", e)

    logger.info(
        "Starting MSI Analysis Application on http://%s:%s", APP_HOST, APP_PORT
    )
    try:
        app.run(debug=False, host=APP_HOST, port=APP_PORT)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, exiting")
    finally:
        # app.run() がブロックを抜けた時 (graceful 完了 / 例外) も flush
        if not _shutdown_event.is_set():
            _flush_logs_and_caches()


if __name__ == "__main__":
    main()
