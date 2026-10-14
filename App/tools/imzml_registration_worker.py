"""★ ver78.0: 固定した登録バッチを処理し、解析開始を要求せずParquetを準備する。"""
import signal
import sys
import time
import traceback
from pathlib import Path

from filelock import FileLock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.imzml_registration_queue import FILE_TERMINAL, get_job, run_job, write_json
from app.services.input_preparation import PreparationCancelled


def _record_terminal(folder, request_id, root, code, failure=None):
    # ★ ver78.0: 専用queueが終了しても共通launcherのrunning表示を残さず、従来ログと整合させる。
    try:
        with FileLock(str(folder / "state.lock")):
            state = get_job(request_id, root=root, recover=False)
            state["returncode"] = code
            if failure is not None:
                state.update(state="cancelled" if code == 130 else "completed_with_errors",
                             error=failure, finished_at=time.time())
                for item in state["files"]:
                    if item["state"] not in FILE_TERMINAL:
                        item.update(state="cancelled" if code == 130 else "failed", error=failure)
            write_json(folder / "state.json", state)
    except (OSError, ValueError, KeyError):
        traceback.print_exc()
    log = folder / "log"
    status_path = log / "analysis_status.txt"
    status = "finished" if code == 0 else "stopped" if code == 130 else "error"
    try:
        if status_path.read_text(encoding="utf-8").strip() == "stopped":
            status = "stopped"
    except OSError:
        pass
    try:
        log.mkdir(parents=True, exist_ok=True)
        status_path.write_text(status, encoding="utf-8")
    except OSError:
        traceback.print_exc()
    try:
        write_json(folder / "exit.json", {"returncode": code})
    except OSError:
        traceback.print_exc()
    finally:
        # PIDファイルは既存watcher同様に履歴として保持し、生存判定には使用しない。
        print(f"[EXIT] imzML登録プロセスは終了コード {code} で終了しました。状態: {status}", flush=True)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("request_id")
    args = parser.parse_args(argv)
    folder = args.root / "jobs" / args.request_id
    def stop(*_):
        (folder / "cancel").touch()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    code, failure = 2, None
    try:
        state = run_job(args.request_id, root=args.root)
        code = 0 if state["state"] == "completed" else 130 if state["state"] == "cancelled" else 2
    except PreparationCancelled as exc:
        code, failure = 130, str(exc)
    except Exception as exc:
        failure = str(exc)
        traceback.print_exc()
    finally:
        _record_terminal(folder, args.request_id, args.root, code, failure)
    return code


if __name__ == "__main__":
    sys.exit(main())
