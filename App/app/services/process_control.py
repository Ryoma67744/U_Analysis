"""★ ver70.0: PID 再利用を区別し、Python 親だけでなく R 子孫も停止する。"""
from __future__ import annotations
import json
from pathlib import Path
import psutil

from app.services.input_preparation import InputPreparationError, write_json


def terminate_tree(pid, expected_start=None, timeout=3):
    try:
        parent = psutil.Process(int(pid))
        if expected_start is not None and abs(parent.create_time() - float(expected_start)) > 0.01:
            return False
        children = parent.children(recursive=True)
        for proc in [parent, *reversed(children)]:
            try:
                proc.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs([parent, *children], timeout=timeout)
        for proc in alive:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(alive, timeout=timeout)
        return True
    except psutil.NoSuchProcess:
        return False


def admission_path():
    from app.config import OTHER_DIR
    return OTHER_DIR / "logs" / "heavy_job.json"


def active_lease():
    path = admission_path()
    if not path.exists():
        return None
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        process = psutil.Process(int(info["pid"]))
        if abs(process.create_time() - float(info["process_started_at"])) > 0.01 or process.status() == psutil.STATUS_ZOMBIE:
            return None
        return info
    except psutil.NoSuchProcess:
        return None
    except Exception as exc:
        raise InputPreparationError(f"重い処理の受付状態を確認できません: {exc}") from exc


def record_lease(process, output_dir, analyst=""):
    try:
        started = psutil.Process(process.pid).create_time()
    except psutil.NoSuchProcess:
        if process.poll() is not None:
            return  # 即時終了した保守ツールを起動失敗へ変えない。
        raise
    write_json(admission_path(), {"pid": process.pid,
        "process_started_at": started, "output_dir": str(output_dir), "analyst": analyst})


def matching_process(pid, expected_start):
    """停止前にPID再利用を検出する。アクセス拒否時は安全側に停止しない。"""
    try:
        process = psutil.Process(int(pid))
        return abs(process.create_time() - float(expected_start)) <= 0.01 and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, ValueError, TypeError):
        return False
