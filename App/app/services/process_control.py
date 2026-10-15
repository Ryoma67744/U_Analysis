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
        # ★ ver81.0: 監督workerだけを見ていると、その死亡後に旧版変換子が
        # 生存していても次の重い処理を許可してしまう。記録した子も確認する。
        processes = [info, *info.get("children", [])]
        for item in processes:
            if lease_process_alive(item["pid"], item["process_started_at"]):
                return info
        return None
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


def lease_process_alive(pid, expected_start):
    """★ ver81.0: アクセス拒否を死亡と扱わず、受付側へ確認失敗を伝える。"""
    try:
        process = psutil.Process(int(pid))
        return (abs(process.create_time() - float(expected_start)) <= 0.01
                and process.status() != psutil.STATUS_ZOMBIE)
    except psutil.NoSuchProcess:
        return False


def record_lease_child(supervisor_pid, supervisor_start, child_pid, child_start):
    """★ ver81.0: 子の開始gateを開く前に共通受付へPIDと生成時刻を永続化する。"""
    from filelock import FileLock
    path = admission_path()
    with FileLock(str(path.with_name("analysis_admission.lock")), timeout=30):
        info = json.loads(path.read_text(encoding="utf-8"))
        if not lease_has_process(info, supervisor_pid, supervisor_start):
            raise InputPreparationError("復元workerの重い処理受付が一致しません。")
        if not lease_process_alive(supervisor_pid, supervisor_start):
            raise InputPreparationError("復元workerの生存を確認できません。")
        if not lease_process_alive(child_pid, child_start):
            raise InputPreparationError("旧版変換プロセスの生存を確認できません。")
        child = {"pid": int(child_pid), "process_started_at": float(child_start)}
        children = info.setdefault("children", [])
        if child not in children:
            children.append(child)
        write_json(path, info)


def lease_has_process(info, pid, started):
    """★ ver81.0: Windowsのvenv launcherと実workerの両方を同じ受付として扱う。"""
    return any(int(item["pid"]) == int(pid)
               and abs(float(item["process_started_at"]) - float(started)) <= .01
               for item in [info, *info.get("children", [])])


def matching_process(pid, expected_start):
    """停止前にPID再利用を検出する。アクセス拒否時は安全側に停止しない。"""
    try:
        process = psutil.Process(int(pid))
        return abs(process.create_time() - float(expected_start)) <= 0.01 and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, ValueError, TypeError):
        return False
