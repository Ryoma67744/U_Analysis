"""★ ver81.0: 旧解析の固定資産だけを復元する、通常登録とは独立した永続キュー。"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import uuid

from filelock import FileLock, Timeout

from app.services.input_preparation import (
    InputPreparationError, PreparationCancelled, sha256_file,
    write_json as _atomic_write_json,
)

TERMINAL = {"completed", "completed_with_errors", "failed", "cancelled", "interrupted"}
FILE_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
_dispatcher = None
_dispatch_lock = threading.Lock()
_log = logging.getLogger(__name__)


def queue_root():
    from app.config import OTHER_DIR
    return Path(os.environ.get("IMZML_RECOVERY_STATE_DIR") or OTHER_DIR / "logs" / "imzml_recovery")


def _root(root=None):
    return Path(root) if root is not None else queue_root()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("復元ジョブの識別情報が不正です。")
    return value


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _read(path):
    path = Path(path)
    if not path.parent.exists():
        raise FileNotFoundError(path)
    with FileLock(str(path) + ".io.lock"):
        return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # ★ ver81.0: Windowsでpoll中のJSON置換が失敗しないよう読取と同じlockを使う。
    with FileLock(str(path) + ".io.lock"):
        _atomic_write_json(path, value)


def _stamp(path):
    path = Path(path)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"path": str(path), "exists": False}
    if not path.is_file():
        raise ValueError("結果revisionには通常ファイルを指定してください。")
    return {"path": str(path), "exists": True, "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns, "inode": stat.st_ino}


def capture_revision(paths):
    """受付用の軽いstatだけ取得する。全SHAは明示受付後のworkerで取得する。"""
    if isinstance(paths, (str, bytes)):
        raise ValueError("結果revisionのファイル一覧が必要です。")
    files = [_stamp(Path(path).expanduser().resolve()) for path in sorted(set(map(str, paths)))]
    if not files:
        raise ValueError("結果revisionが空です。")
    return {"schema": 1, "files": files}


def verify_revision(revision):
    if not isinstance(revision, dict) or revision.get("schema") != 1 or not revision.get("files"):
        raise ValueError("結果revisionが不正です。")
    for record in revision["files"]:
        if not isinstance(record, dict) or not Path(record.get("path", "")).is_absolute():
            raise ValueError("結果revisionのパスが不正です。")
        if _stamp(record["path"]) != record:
            raise InputPreparationError("復元受付後に解析結果または設定が変わりました。再読込してください。")


def _authorize(record, owner, scope=None):
    if not isinstance(owner, str) or not owner or record.get("owner") != owner:
        raise PermissionError("この復元ジョブを操作する権限がありません。")
    if scope is not None and record.get("scope") != scope:
        raise PermissionError("復元ジョブの解析結果が現在の対象と一致しません。")


def _freeze(snapshot, owner):
    if not isinstance(owner, str) or not owner:
        raise PermissionError("認証済みの復元実行者が必要です。")
    frozen = json.loads(json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
    if not isinstance(frozen, dict) or not isinstance(frozen.get("scope"), str) or not Path(frozen["scope"]).is_absolute():
        raise ValueError("復元対象の結果scopeが不正です。")
    verify_revision(frozen.get("revision"))
    manifest = frozen.get("manifest")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise ValueError("保存された入力manifestが必要です。")
    file_ids = frozen.get("file_ids")
    if (not isinstance(file_ids, list) or not file_ids
            or any(not isinstance(item, str) or not item for item in file_ids)
            or len(set(file_ids)) != len(file_ids)):
        raise ValueError("復元対象の元ファイルIDが不正です。")
    entries = [item for item in manifest["files"] if item.get("file_id") in file_ids]
    if len(entries) != len(file_ids) or {e.get("file_id") for e in entries} != set(file_ids):
        raise ValueError("復元対象が旧解析manifestと一致しません。")
    for entry in entries:
        if (Path(entry.get("path", "")).suffix.lower() != ".imzml"
                or not re.fullmatch(r"[a-f0-9]{64}", str(entry.get("conversion_key", "")))):
            raise ValueError("旧変換revisionが固定されたimzMLだけを復元できます。")
    frozen.update(owner=owner, file_ids=sorted(file_ids), schema=1)
    return frozen


def _initial_state(request):
    return {"request_id": request["request_id"], "owner": request["owner"],
            "project_id": request.get("project_id", ""), "sub_project_id": request.get("sub_project_id", ""),
            "scope": request["scope"], "revision": request["revision"], "state": "queued",
            "created_at": request["created_at"], "children": [], "file_ids": request["file_ids"],
            "context_id": request.get("context_id"), "snapshot_digest": _snapshot_digest(request),
            "files": [{"file_id": entry["file_id"], "path": entry["path"],
                       "conversion_key": entry["conversion_key"], "state": "queued"}
                      for entry in request["manifest"]["files"] if entry.get("file_id") in request["file_ids"]]}


def _snapshot_digest(snapshot):
    return _digest({key: value for key, value in snapshot.items()
                    if key not in {"context_id", "request_id", "retry_of", "created_at"}})


def enqueue(snapshot, *, owner, root=None, _retry_of=None, _token=None):
    frozen = _freeze(snapshot, owner)
    identity = _digest({"snapshot": _snapshot_digest(frozen), "retry_of": _retry_of, "token": _token})[:32]
    target = _root(root)
    target.mkdir(parents=True, exist_ok=True)
    with FileLock(str(target / "admission.lock")):
        folder = target / "jobs" / identity
        if (folder / "request.json").exists():
            existing = _read(folder / "request.json")
            if _snapshot_digest(existing) != _snapshot_digest(frozen):
                raise ValueError("復元受付の内容が一致しません。")
            if not (folder / "state.json").exists():
                write_json(folder / "state.json", _initial_state(existing))
            return identity
        frozen.update(request_id=identity, retry_of=_retry_of, created_at=time.time())
        stage = target / "jobs" / (".stage-" + identity + "-" + uuid.uuid4().hex)
        try:
            write_json(stage / "request.json", frozen)
            write_json(stage / "state.json", _initial_state(frozen))
            os.rename(stage, folder)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return identity


def _live_processes(state):
    from app.services.process_control import lease_process_alive
    records = ([state] if state.get("pid") and state.get("process_started_at") is not None else []) + state.get("children", [])
    return [record for record in records if lease_process_alive(record["pid"], record["process_started_at"])]


def _load_state(identity, root=None, *, recover=True):
    folder = _root(root) / "jobs" / _id(identity)
    state = _read(folder / "state.json")
    if recover and state["state"] not in TERMINAL and state.get("pid"):
        with FileLock(str(folder / "state.lock")):
            state = _read(folder / "state.json")
            if state["state"] in TERMINAL:
                return state
            # ★ ver81.0: 共通lease保存とjob保存の間に監督workerが停止しても、
            # 先に記録された子を回収し、照会・取消で見失わない。
            from app.services.process_control import active_lease, lease_has_process
            lease = active_lease()
            if (lease and state.get("process_started_at") is not None
                    and lease_has_process(lease, state["pid"], state["process_started_at"])):
                for child in lease.get("children", []):
                    if child not in state.setdefault("children", []):
                        state["children"].append(child)
            live = _live_processes(state)
            if not live:
                state.update(state="cancelled" if (folder / "cancel").exists() else "interrupted",
                             finished_at=time.time(), needs_verification=True,
                             error="復元workerの終了を確認できません。再試行で公開済み資産も検証します。")
                for item in state["files"]:
                    if item["state"] not in FILE_TERMINAL:
                        item["state"] = "interrupted"
                write_json(folder / "state.json", state)
            elif not any(item.get("pid") == state.get("pid")
                         and item.get("process_started_at") == state.get("process_started_at")
                         for item in live):
                state.update(state="cancelling" if (folder / "cancel").exists() else "orphan_running",
                             orphaned=True,
                             wait_reason="旧版変換プロセスの終了を待っています。")
                write_json(folder / "state.json", state)
    return state


def get_job(identity, owner, *, scope=None, root=None):
    # ★ ver81.0: 他人のjobを読む前に認可し、復旧照会の副作用も所有者だけに限定する。
    state = _load_state(identity, root, recover=False)
    _authorize(state, owner, scope)
    return _load_state(identity, root)


def list_jobs(owner, scope, *, root=None):
    if not owner or not scope:
        return []
    result = []
    for path in (_root(root) / "jobs").glob("*/state.json"):
        if not re.fullmatch(r"[a-f0-9]{32}", path.parent.name):
            continue
        state = _read(path)
        if state.get("owner") == owner and state.get("scope") == scope:
            result.append(get_job(path.parent.name, owner, scope=scope, root=root))
    return sorted(result, key=lambda item: item["created_at"], reverse=True)


def cancel_job(identity, owner, *, scope=None, root=None):
    state = get_job(identity, owner, scope=scope, root=root)
    folder = _root(root) / "jobs" / _id(identity)
    with FileLock(str(folder / "state.lock")):
        state = _read(folder / "state.json")
        if state["state"] in TERMINAL:
            return state
        (folder / "cancel").touch()
        orphan = bool(state.get("orphaned"))
        state["state"] = "cancelled" if not state.get("pid") else "cancelling"
        if state["state"] == "cancelled":
            for item in state["files"]:
                item["state"] = "cancelled"
            state["finished_at"] = time.time()
        write_json(folder / "state.json", state)
    if orphan:
        from app.services.process_control import terminate_tree
        for child in state.get("children", []):
            terminate_tree(child["pid"], expected_start=child["process_started_at"])
    return state


def retry_job(identity, owner, *, token, scope=None, root=None):
    if not isinstance(token, str) or not token or len(token) > 128:
        raise ValueError("再試行の識別子が必要です。")
    state = get_job(identity, owner, scope=scope, root=root)
    if state["state"] not in TERMINAL:
        raise ValueError("復元プロセスの終了後に再試行してください。")
    if _live_processes(state):
        raise ValueError("旧版変換プロセスがまだ実行中です。")
    request = _read(_root(root) / "jobs" / _id(identity) / "request.json")
    snapshot = {key: value for key, value in request.items()
                if key not in {"request_id", "retry_of", "created_at"}}
    # 完了直後の終了もあるため、旧manifestの全要求資産を再検証し正常品は再利用する。
    return enqueue(snapshot, owner=owner, root=root, _retry_of=identity, _token=token)


def _assert_supervisor_lease():
    from app.services.process_control import active_lease, lease_has_process, record_lease_child, lease_process_alive
    import psutil
    lease = active_lease()
    started = psutil.Process().create_time()
    if lease and lease_has_process(lease, os.getpid(), started):
        return started
    # ★ ver81.0: WindowsのvenvではPopenのPIDがlauncherで実Pythonは子となる。
    # PID時刻と親子関係を確認し、実workerを永続登録してから重い処理へ進む。
    if (not lease or not lease_process_alive(lease["pid"], lease["process_started_at"])
            or not any(child.pid == os.getpid() and abs(child.create_time() - started) <= .01
                       for child in psutil.Process(lease["pid"]).children(recursive=True))):
        raise InputPreparationError("共通受付を通して復元workerを起動してください。")
    record_lease_child(lease["pid"], lease["process_started_at"], os.getpid(), started)
    return started


def run_job(identity, *, root=None, recoverer=None):
    from app.services.process_control import record_lease_child
    from app.services.progress_reporting import ProgressReporter
    if recoverer is None:
        from app.services.imzml_asset_recovery import recover_entry
        recoverer = recover_entry
    folder = _root(root) / "jobs" / _id(identity)
    with FileLock(str(folder / "run.lock"), timeout=60):
        request = _read(folder / "request.json")
        state = _load_state(identity, root, recover=False)
        if state["state"] in TERMINAL:
            return state
        started = _assert_supervisor_lease()
        state.update(state="running", pid=os.getpid(), process_started_at=started, started_at=time.time())
        def save():
            with FileLock(str(folder / "state.lock")):
                if (folder / "cancel").exists() and state["state"] not in TERMINAL:
                    state["state"] = "cancelling"
                write_json(folder / "state.json", state)
        def cancelled():
            if (folder / "cancel").exists():
                return True
            verify_revision(request["revision"])
            return False
        def check_cancel():
            if cancelled():
                raise PreparationCancelled("復元を停止しました。")
        def child_callback(pid, created):
            check_cancel()
            # 共通leaseが先。途中でstate保存に失敗しても子が動いている間は受付を保持する。
            record_lease_child(os.getpid(), started, pid, created)
            child = {"pid": int(pid), "process_started_at": float(created)}
            if child not in state["children"]:
                state["children"].append(child)
            save()
            check_cancel()
        save()
        try:
            check_cancel()
            context_hashes = {record["path"]: sha256_file(record["path"], cancelled)
                              for record in request["revision"]["files"] if record["exists"]}
            def verify_context():
                check_cancel()
                if any(sha256_file(path, cancelled) != digest for path, digest in context_hashes.items()):
                    raise InputPreparationError("復元中に解析結果または設定の内容が変わりました。")
            state["result_hashes"] = context_hashes
            save()
            entries = {entry["file_id"]: entry for entry in request["manifest"]["files"]}
            for item in state["files"]:
                check_cancel()
                item["state"] = "checking"
                save()
                def emit(update):
                    item["progress"] = deepcopy(update)
                    item["state"] = "completed" if update.get("stage") == "complete" else update.get("stage", "running")
                    if item["state"] == "completed":
                        item["completed_at"] = time.time()
                    save()
                reporter = ProgressReporter(emit)
                def progress(update):
                    # ★ ver81.0: 公開確定と同時に取消されても完成資産を失敗と表示しない。
                    # 次ファイルと最終検査では通常どおり取消・revision変更を確認する。
                    if update.get("stage") == "complete":
                        reporter.update(update)
                        return
                    check_cancel()
                    if update.get("stage") == "publish":
                        verify_context()
                    reporter.update(update)
                try:
                    recoverer(deepcopy(entries[item["file_id"]]), cache_root=request.get("cache_root"),
                              cancel=cancelled, progress=progress, child_callback=child_callback,
                              operation_id=identity)
                    item.update(state="completed", completed_at=time.time())
                except PreparationCancelled:
                    raise
                except Exception as exc:
                    item.update(state="failed", error=str(exc))
                    _log.exception("復元失敗 file_id=%s", item["file_id"])
                save()
            verify_context()
            state["state"] = "completed_with_errors" if any(i["state"] == "failed" for i in state["files"]) else "completed"
        except PreparationCancelled as exc:
            state.update(state="cancelled", error=str(exc))
        except Exception as exc:
            state.update(state="failed", error=str(exc))
            _log.exception("復元ジョブ失敗 request_id=%s", identity)
        for item in state["files"]:
            if item["state"] not in FILE_TERMINAL:
                item["state"] = "cancelled" if state["state"] == "cancelled" else "failed"
        state["finished_at"] = time.time()
        save()
        return state


def _watch(process, handle):
    try:
        process.wait()
    finally:
        from app.services.analysis_runner import _cancel_watchdog
        _cancel_watchdog(process.pid)
        if handle:
            handle.close()


def dispatch_once(*, root=None, launcher=None):
    target = _root(root)
    if not target.exists():
        return
    from app.config import APP_DIR
    from app.services.analysis_runner import start_analysis_process
    import psutil
    with FileLock(str(target / "dispatch.lock"), timeout=1):
        jobs = [_load_state(path.parent.name, target) for path in (target / "jobs").glob("*/state.json")
                if re.fullmatch(r"[a-f0-9]{32}", path.parent.name)]
        for state in sorted(jobs, key=lambda item: item["created_at"]):
            if state["state"] != "queued" or state.get("pid"):
                continue
            identity = state["request_id"]
            folder = target / "jobs" / identity
            with FileLock(str(folder / "run.lock"), timeout=0):
                state = _load_state(identity, target, recover=False)
                if state["state"] != "queued":
                    continue
                result = (launcher or start_analysis_process)(
                    str(APP_DIR / "tools" / "imzml_recovery_worker.py"), str(folder),
                    extra_args=[str(target.resolve()), identity], interpreter=[sys.executable, "-u"])
                with FileLock(str(folder / "state.lock")):
                    state = _load_state(identity, target, recover=False)
                    if not result["success"]:
                        state["wait_reason"] = result.get("message", "重い処理の空きを待っています。")
                        write_json(folder / "state.json", state)
                        return
                    process = result["process"]
                    try:
                        created = psutil.Process(process.pid).create_time()
                    except psutil.NoSuchProcess:
                        state.update(state="interrupted", error="復元workerを開始できませんでした。")
                        created = None
                    state.update(pid=process.pid, process_started_at=created)
                    if (folder / "cancel").exists() and state["state"] not in TERMINAL:
                        state["state"] = "cancelling"
                    write_json(folder / "state.json", state)
                threading.Thread(target=_watch, args=(process, result.get("log_file_handle")),
                                 daemon=True, name="imzml-recovery-watch").start()
                return


def ensure_dispatcher():
    global _dispatcher
    with _dispatch_lock:
        if _dispatcher is not None and _dispatcher.is_alive():
            return
        def dispatch():
            while True:
                try:
                    dispatch_once()
                except Timeout:
                    pass
                except Exception:
                    _log.exception("復元キューの受付確認に失敗しました")
                time.sleep(2)
        _dispatcher = threading.Thread(target=dispatch, daemon=True, name="imzml-recovery-queue")
        _dispatcher.start()
