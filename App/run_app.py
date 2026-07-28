#!/usr/bin/env python
"""MSI Analysis Application - Python Launcher"""

import logging
import os
import signal
import sys
import threading
from pathlib import Path

# アプリケーションルートをパスに追加
app_root = Path(__file__).parent
sys.path.insert(0, str(app_root))

from app.services.log_config import setup_logging

setup_logging()
# 生成ファイル/ディレクトリをグループ書込可(664/775)にし、SFTPユーザー(共有グループ)と
# アプリ(コンテナ)でデータを共有できるようにする。R サブプロセスも親の umask を継承する。
os.umask(0o002)
logger = logging.getLogger("msi.startup")

from app.main import app
from app.config import APP_HOST, APP_PORT

# PR-H3 C3: graceful shutdown 用フラグ + 同期
_shutdown_event = threading.Event()

# PR-H5 E7: メモリ・FD 計測の定期ログ (1 時間ごと)
_METRICS_LOG_INTERVAL_SEC = int(__import__("os").environ.get(
    "METRICS_LOG_INTERVAL_SEC", 3600
))


def _periodic_metrics_logger():
    """1 時間ごとにメモリ / fd / project_states を WARNING で記録。
    異常値検知の post-mortem 用ベースライン。
    """
    try:
        import psutil
        import os as _os
        proc = psutil.Process(_os.getpid())
        rss_mb = proc.memory_info().rss / (1024 * 1024)
        num_fds = proc.num_fds() if hasattr(proc, "num_fds") else 0
        threads = proc.num_threads()
        try:
            from app.callbacks.interactive_callbacks import get_project_states_size
            ps_size = get_project_states_size()
        except Exception:
            ps_size = -1
        logger.info(
            "metrics rss_mb=%.0f num_fds=%d threads=%d project_states=%d",
            rss_mb, num_fds, threads, ps_size,
        )
    except Exception as e:
        logger.debug("metrics logging failed (non-critical): %s", e)
    # 次回の Timer を schedule
    if not _shutdown_event.is_set() and _METRICS_LOG_INTERVAL_SEC > 0:
        t = threading.Timer(_METRICS_LOG_INTERVAL_SEC, _periodic_metrics_logger)
        t.daemon = True
        t.start()


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


# ---------------------------------------------------------------------------
# WSGI サーバ (ver46.3)
# ---------------------------------------------------------------------------
# 従来は Flask の `app.run()` = Werkzeug 開発サーバで本番運用していた。
# waitress は純 Python の本番用 WSGI サーバで、追加のシステム依存が無く
# （gunicorn と違い C 拡張もプロセスマネージャも不要）、Dockerfile を変えずに
# 差し替えられる。接続まわり・タイムアウト・バックプレッシャの扱いが堅い。
#
# **ワーカーは 1 プロセスのままにすること。** このアプリは読み込んだ
# `plot_data`（数百 MB）やエクスポート用 figure、H&E 画像キャッシュを
# **プロセス内メモリ**に保持しており（interactive_callbacks._project_states 等）、
# 複数ワーカーにすると「別ワーカーに当たった瞬間データ未ロード扱い」になる。
# マルチプロセス化する場合は、それらを diskcache 等の共有ストアへ移すのが先。
# スレッド数だけは MSI_WSGI_THREADS で調整できる（既定 8）。
#
# MSI_WSGI_SERVER=werkzeug で従来どおりの起動に戻せる（切り分け用）。
_WSGI_SERVER = os.environ.get("MSI_WSGI_SERVER", "waitress").strip().lower()
_WSGI_THREADS = int(os.environ.get("MSI_WSGI_THREADS", "8"))


def _serve():
    """設定に応じて WSGI サーバを起動する（ブロックする）。"""
    if _WSGI_SERVER == "waitress":
        try:
            from waitress import serve as _waitress_serve
        except ImportError:
            logger.warning(
                "waitress が見つからないため Werkzeug 開発サーバで起動します "
                "(pip install waitress を推奨)")
        else:
            logger.info("Serving with waitress (threads=%d, workers=1)",
                        _WSGI_THREADS)
            _waitress_serve(
                app.server, host=APP_HOST, port=APP_PORT,
                threads=_WSGI_THREADS,
                # 解析ジョブの応答が長いので既定 (120s) では切れる。
                # Caddy 側も read/write 600s に合わせてある。
                channel_timeout=int(os.environ.get("MSI_WSGI_TIMEOUT_SEC", "600")),
                # 大きな figure JSON を返すのでバッファを大きめに取る
                outbuf_overflow=1 << 24,
                ident="MSI",
            )
            return
    logger.info("Serving with Werkzeug development server")
    app.run(debug=False, host=APP_HOST, port=APP_PORT)


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

    # PR-H5 E7: 起動 5 秒後から定期メトリクス logging を開始
    if _METRICS_LOG_INTERVAL_SEC > 0:
        threading.Timer(5.0, _periodic_metrics_logger).start()

    try:
        _serve()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, exiting")
    finally:
        # サーバがブロックを抜けた時 (graceful 完了 / 例外) も flush
        if not _shutdown_event.is_set():
            _flush_logs_and_caches()


if __name__ == "__main__":
    main()
