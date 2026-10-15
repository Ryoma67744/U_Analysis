"""★ ver81.0: 共通heavy受付から起動する、旧固定資産復元専用worker。"""
import argparse
from pathlib import Path
import signal
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.imzml_recovery_queue import run_job, write_json


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("request_id")
    args = parser.parse_args(argv)
    from app.services.imzml_recovery_queue import _id
    identity = _id(args.request_id)
    folder = args.root / "jobs" / identity
    def stop(*_):
        (folder / "cancel").touch()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    code = 2
    try:
        state = run_job(identity, root=args.root)
        code = 0 if state["state"] == "completed" else 130 if state["state"] == "cancelled" else 2
    except Exception:
        traceback.print_exc()
    finally:
        status = "finished" if code == 0 else "stopped" if code == 130 else "error"
        log = folder / "log"
        log.mkdir(parents=True, exist_ok=True)
        (log / "analysis_status.txt").write_text(status, encoding="utf-8")
        write_json(folder / "exit.json", {"returncode": code})
        print(f"[EXIT] imzML復元プロセスは終了コード {code} で終了しました。状態: {status}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
