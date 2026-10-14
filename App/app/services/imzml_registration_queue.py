"""★ ver78.0: 1件ずつの手動変換を、受付内容が変わらない永続キューへまとめる。

選択世代ごとにdraftを分け、受付後は固定snapshotだけをworkerへ渡す。
ブラウザを閉じても処理を続け、完成品の検証と原子的公開は既存サービスに委ねる。
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import uuid

from filelock import FileLock

from app.services.input_preparation import (
    InputPreparationError, PreparationCancelled, conversion_spec, pair_paths,
    prepare_imzml, validate_asset, write_json as _atomic_write_json,
)

TERMINAL = {"completed", "completed_with_errors", "cancelled", "interrupted"}
FILE_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
METADATA = ("section_display_name", "subject_id", "group")
_dispatcher = None
_dispatch_lock = threading.Lock()


def queue_root():
    from app.config import OTHER_DIR
    return OTHER_DIR / "imzml_registrations"


def _root(root=None):
    return Path(root) if root is not None else queue_root()


def registration_preferences(owner, *, ppm=None, root=None):
    # ★ ver78.0: last_settingsは起動時に消去されるため、登録条件は実行者別に永続化する。
    path = _root(root) / "preferences" / (sha256(str(owner).encode()).hexdigest() + ".json")
    if ppm is not None:
        value = float(ppm)
        if not math.isfinite(value) or value < 0:
            raise ValueError("m/zアライメント(ppm)は0以上の有限値で指定してください。")
        write_json(path, {"alignment_ppm": value})
    try:
        return _read(path)
    except FileNotFoundError:
        return {"alignment_ppm": 0}


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("登録の識別情報が不正です。")
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
    # ★ ver78.0: Windowsでは開いたJSONの置換が拒否されるため、poll読取と公開を直列化する。
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".io.lock"):
        _atomic_write_json(path, value)


def _authorize(record, owner, admin=False):
    from app.services.job_registry import may_stop
    if not admin and (not owner or not may_stop(record, owner)):
        raise PermissionError("登録の操作は実行者または管理者だけが行えます。")


def source_stat(path):
    from app.services.imzml_input_inspection import source_stat as inspect_stat
    return inspect_stat(path)


def inspect_paths(paths, generation, owner, project_id="", *, root=None):
    """固定した選択の座標・形式をDash request外で調べる。"""
    path = _root(root) / "drafts" / (_id(generation) + ".json")
    current = {"id": generation, "analyst": owner, "project_id": project_id,
               "state": "inspecting", "entries": [], "errors": [],
               "created_at": time.time()}
    write_json(path, current)
    from app.services.imzml_spatial_layout import default_spatial_sections
    from app.services.imzml_input_inspection import inspect_selected_input
    from app.services.section_metadata import stable_file_id
    known_assets = completed_assets(owner, project_id, root=root)
    for value in dict.fromkeys(str(Path(p).expanduser().resolve()) for p in paths):
        try:
            inspected = inspect_selected_input(value)
            if inspected.get("spatial_error") or inspected["spectral_preflight"].get("status") == "invalid":
                raise ValueError(inspected.get("spatial_error") or inspected["spectral_preflight"]["error"])
            before = inspected["source_stat"]
            layout = inspected["spatial_layout"]
            preflight = inspected["spectral_preflight"]
            fid = stable_file_id(value)
            sections = default_spatial_sections(fid, layout)
            previous_ppm = None
            for asset in reversed(list(known_assets.values())):
                old = asset["entry"]
                if (asset["source"] == value and old.get("source_stat") == before
                        and (old.get("spatial_layout") or {}).get("coordinate_hash") == layout["coordinate_hash"]):
                    try:
                        validate_asset(old)
                    except InputPreparationError:
                        continue
                    from app.services.tims_parquet_contract import read_registered_sections
                    sections = read_registered_sections(old["runtime_path"])
                    for row in sections:
                        row.update(metadata_confirmed=False, selected=True)
                    previous_ppm = asset["alignment_ppm"]
                    break
            current["entries"].append({"path": value, "file_id": fid,
                "spatial_layout": layout, "spectral_preflight": preflight,
                "registered_sections": sections, "spatial_sections": sections,
                "source_stat": before, "roi_role": "spatial", "previous_alignment_ppm": previous_ppm})
        except Exception as exc:
            current["errors"].append({"path": value, "error": str(exc)})
        write_json(path, current)
    current["state"] = "ready" if current["entries"] and not current["errors"] else "invalid"
    current["revision"] = _digest(current["entries"])
    write_json(path, current)
    return current


def begin_inspection(paths, owner, project_id="", *, root=None):
    generation = uuid.uuid4().hex
    frozen = list(dict.fromkeys(str(Path(p).expanduser().resolve()) for p in paths))
    if not frozen:
        raise ValueError("imzMLを1つ以上選択してください。")
    target = _root(root)
    write_json(target / "drafts" / (generation + ".json"), {
        "id": generation, "analyst": owner, "project_id": project_id,
        "state": "inspecting", "entries": [], "errors": []})
    threading.Thread(target=inspect_paths, args=(frozen, generation, owner, project_id),
                     kwargs={"root": target}, daemon=True, name="imzml-inspect").start()
    return {"id": generation, "paths": frozen, "project_id": project_id}


def get_draft(identity, owner, *, root=None):
    value = _read(_root(root) / "drafts" / (_id(identity) + ".json"))
    _authorize(value, owner)
    return value


def table_rows(draft):
    return [{"id": row["section_id"], "file_path": entry["path"],
             "file": Path(entry["path"]).name, "section_id": row["section_id"],
             "pixels": row["pixel_count"], **{key: row.get(key, "") for key in METADATA}}
            for entry in draft.get("entries", []) for row in entry["registered_sections"]]


def revise_draft(identity, owner, revision, overrides, *, rows=None, root=None):
    path = _root(root) / "drafts" / (_id(identity) + ".json")
    with FileLock(str(path) + ".lock"):
        draft = get_draft(identity, owner, root=root)
        if draft.get("revision") != revision or draft.get("state") != "ready":
            raise ValueError("登録表が更新されています。現在の表を開き直してください。")
        from app.services.imzml_spatial_layout import normalize_spatial_sections
        from app.services.section_completeness import require_complete_sections
        edits = {(r.get("file_path"), r.get("section_id")): r for r in rows or []}
        for entry in draft["entries"]:
            # ★ ver78.0: 1ファイルの配置変更で、別ファイルに入力済みの個体ID・群を失わない。
            for row in entry["registered_sections"]:
                edit = edits.get((entry["path"], row["section_id"]))
                if edit is not None:
                    row.update({k: str(edit.get(k) or "") for k in METADATA})
            override = overrides.get(entry["path"]) or {}
            if not override or override.get("coordinate_hash") != entry["spatial_layout"]["coordinate_hash"]:
                continue
            sections = normalize_spatial_sections(entry["file_id"], entry["spatial_layout"],
                override.get("registered_sections") or override.get("spatial_sections"))
            require_complete_sections(sections, components={c["component_id"]: c["pixel_count"]
                                      for c in entry["spatial_layout"]["components"]})
            entry["registered_sections"] = sections
            entry["spatial_sections"] = deepcopy(sections)
        draft["revision"] = _digest(draft["entries"])
        write_json(path, draft)
        return draft


def confirm_snapshot(draft, rows, ppm, *, generation, revision, owner, project_id):
    """明示的な確認操作で、解析対象外を含む全切片の登録を検証する。"""
    _authorize(draft, owner)
    if (draft.get("state") != "ready" or draft.get("id") != generation
            or draft.get("revision") != revision or draft.get("project_id") != project_id):
        raise ValueError("選択または登録表が更新されています。読込完了後の表を確認してください。")
    if ppm is None or isinstance(ppm, bool):
        raise ValueError("m/zアライメント(ppm)を指定してください。")
    ppm = float(ppm)
    if not math.isfinite(ppm) or ppm < 0:
        raise ValueError("m/zアライメント(ppm)は0以上の有限値で指定してください。")
    expected = {(e["path"], r["section_id"]) for e in draft["entries"] for r in e["registered_sections"]}
    edits = {}
    for row in rows or []:
        key = (row.get("file_path"), row.get("section_id"))
        if key not in expected or key in edits:
            raise ValueError("登録表に古い切片・不明な切片・重複があります。再読込してください。")
        edits[key] = row
    if set(edits) != expected:
        raise ValueError("解析対象外を含む全切片の情報が必要です。")
    from app.services.imzml_registration import build_registration_spec
    entries = []
    for original in draft["entries"]:
        entry = deepcopy(original)
        if source_stat(entry["path"]) != entry["source_stat"]:
            raise ValueError(f"{Path(entry['path']).name}: 選択後に原本が変更されました。再読込してください。")
        sections = []
        for old in entry["registered_sections"]:
            edited = edits[(entry["path"], old["section_id"])]
            sections.append({**old, **{k: str(edited.get(k) or "").strip() for k in METADATA},
                             "metadata_confirmed": True, "selected": True})
        entry.update(registered_sections=sections, spatial_sections=deepcopy(sections),
                     sections=deepcopy(sections), selected_section_ids=[r["section_id"] for r in sections],
                     selection_mode="selected")
        registration = build_registration_spec(entry)
        entry["conversion_spec"] = conversion_spec(processed_alignment_ppm=ppm,
                                    registration_hash=registration["registration_hash"])
        entries.append(entry)
    return {"schema": 1, "analyst": owner, "project_id": project_id,
            "generation": generation, "table_revision": revision,
            "alignment_ppm": ppm, "entries": entries}


def enqueue(snapshot, *, root=None, retry_of=None, token=None):
    target = _root(root)
    identity = _digest({"snapshot": snapshot, "retry_of": retry_of, "token": token})[:32]
    target.mkdir(parents=True, exist_ok=True)
    with FileLock(str(target / "admission.lock")):
        folder = target / "jobs" / identity
        if (folder / "request.json").exists():
            request = _read(folder / "request.json")
            if any(request.get(k) != v for k, v in snapshot.items()):
                raise ValueError("受付記録が一致しません。管理者へ確認してください。")
            if not (folder / "state.json").exists():
                write_json(folder / "state.json", _initial_state(request))
            return identity
        frozen = deepcopy(snapshot)
        frozen.update(request_id=identity, retry_of=retry_of, created_at=time.time())
        stage = target / "jobs" / (".stage-" + identity + "-" + uuid.uuid4().hex)
        try:
            write_json(stage / "request.json", frozen)
            write_json(stage / "state.json", _initial_state(frozen))
            os.rename(stage, folder)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return identity


def _initial_state(request):
    return {"request_id": request["request_id"], "state": "queued",
            "analyst": request["analyst"], "project_id": request["project_id"],
            "created_at": request["created_at"], "alignment_ppm": request["alignment_ppm"],
            "files": [{"file_id": e["file_id"], "path": e["path"], "state": "queued"}
                      for e in request["entries"]]}


def get_job(identity, owner=None, *, admin=False, root=None, recover=True):
    folder = _root(root) / "jobs" / _id(identity)
    state = _read(folder / "state.json")
    if owner is not None:
        _authorize(state, owner, admin)
    if recover and state["state"] not in TERMINAL and state.get("pid"):
        from app.services.process_control import matching_process
        if not matching_process(state["pid"], state.get("process_started_at")):
            with FileLock(str(folder / "state.lock")):
                state = _read(folder / "state.json")
                if (state["state"] not in TERMINAL
                        and not matching_process(state.get("pid"), state.get("process_started_at"))):
                    state["state"] = "interrupted"
                    for item in state["files"]:
                        if item["state"] not in FILE_TERMINAL:
                            item.update(state="interrupted", error="処理の終了を確認できません。再試行できます。")
                    write_json(folder / "state.json", state)
    return state


def list_jobs(owner, project_id, *, root=None):
    result = []
    for path in (_root(root) / "jobs").glob("*/state.json"):
        if not re.fullmatch(r"[a-f0-9]{32}", path.parent.name):
            continue
        try:
            state = get_job(path.parent.name, root=root)
            if state.get("analyst") == owner and state.get("project_id") == project_id:
                result.append(state)
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda v: v["created_at"], reverse=True)


def cancel_job(identity, owner, *, admin=False, root=None):
    folder = _root(root) / "jobs" / _id(identity)
    with FileLock(str(folder / "state.lock")):
        state = get_job(identity, owner, admin=admin, root=root, recover=False)
        if state["state"] in TERMINAL:
            return state
        (folder / "cancel").touch()
        if state["state"] == "queued" and not state.get("pid"):
            state["state"] = "cancelled"
            for item in state["files"]:
                item["state"] = "cancelled"
        else:
            state["state"] = "cancelling"
        write_json(folder / "state.json", state)
        return state


def retry_job(identity, owner, *, token, root=None):
    old = get_job(identity, owner, root=root)
    if old["state"] not in TERMINAL:
        raise ValueError("処理の終了後に再試行してください。")
    snapshot = _read(_root(root) / "jobs" / _id(identity) / "request.json")
    failed = {row["file_id"] for row in old["files"] if row["state"] != "completed"}
    if not failed:
        raise ValueError("再試行するファイルがありません。")
    snapshot = {key: value for key, value in snapshot.items()
                if key not in {"request_id", "retry_of", "created_at"}}
    snapshot["entries"] = [e for e in snapshot["entries"] if e["file_id"] in failed]
    return enqueue(snapshot, root=root, retry_of=identity, token=token)


def run_job(identity, *, root=None, cache_root=None, preparer=None):
    """worker入口。1件の失敗で別ファイルの完成品を破棄しない。"""
    import psutil
    from app.services.imzml_registration import build_registration_spec
    from app.services.progress_reporting import ProgressReporter
    folder = _root(root) / "jobs" / _id(identity)
    request = _read(folder / "request.json")
    prepare = preparer or prepare_imzml
    with FileLock(str(folder / "run.lock"), timeout=30):
        state = get_job(identity, root=root, recover=False)
        if state["state"] in TERMINAL:
            return state
        state.update(state="running", pid=os.getpid(),
                     process_started_at=psutil.Process().create_time(), started_at=time.time())
        def save():
            with FileLock(str(folder / "state.lock")):
                if (folder / "cancel").exists() and state["state"] not in TERMINAL:
                    state["state"] = "cancelling"
                write_json(folder / "state.json", state)
        def cancelled():
            return (folder / "cancel").exists()
        save()
        for entry, item in zip(request["entries"], state["files"]):
            if cancelled():
                item["state"] = "cancelled"
                continue
            item["state"] = "checking"
            save()
            try:
                if source_stat(entry["path"]) != entry["source_stat"]:
                    raise InputPreparationError("受付後に原本が変更されました。再選択して登録してください。")
                spec = conversion_spec(processed_alignment_ppm=request["alignment_ppm"],
                        registration_hash=build_registration_spec(entry)["registration_hash"])
                if spec != entry["conversion_spec"]:
                    raise InputPreparationError("受付後に変換仕様が変更されました。再登録してください。")
                def progress(update):
                    if cancelled():
                        raise PreparationCancelled("入力準備を停止しました。")
                    reporter.update(update)
                def emit(update):
                    item["progress"] = update
                    if update.get("stage") == "source_verified":
                        item["source_identity"] = deepcopy(update)
                    item["state"] = {"hash": "hashing", "reuse": "reusing", "convert": "converting",
                                     "register": "registering", "validate": "validating",
                                     "source_verified": "hashing", "publish": "publishing"}.get(
                                         update.get("stage"), item["state"])
                    save()
                reporter = ProgressReporter(emit)
                result = prepare(deepcopy(entry), project_id=request["project_id"],
                    alignment_ppm=request["alignment_ppm"], spec=entry["conversion_spec"],
                    cache_root=cache_root, progress=progress, cancel=cancelled)
                # 検証済み資産の公開直後に停止要求が届いても、完成品は巻き戻さない。
                item.update(state="completed", result=result, completed_at=time.time())
            except PreparationCancelled as exc:
                item.update(state="cancelled", error=str(exc))
                (folder / "cancel").touch()
            except Exception as exc:
                item.update(state="failed", error=str(exc))
            save()
        state["state"] = ("cancelled" if cancelled() else
                          "completed_with_errors" if any(i["state"] == "failed" for i in state["files"])
                          else "completed")
        state["finished_at"] = time.time()
        save()
        return state


def completed_assets(owner, project_id, *, root=None):
    """ジョブ記録から表示索引を再構築する。利用前の資産検証は省略しない。"""
    values = {}
    for job in reversed(list_jobs(owner, project_id, root=root)):
        for item in job["files"]:
            if item["state"] == "completed" and item.get("result"):
                entry = item["result"]
                key = entry.get("conversion_key")
                if key:
                    values[key] = {"request_id": job["request_id"], "file_id": item["file_id"],
                        "source": item["path"], "alignment_ppm": job["alignment_ppm"], "entry": entry}
    return values


def handoff_assets(keys, owner, project_id, *, root=None):
    assets = completed_assets(owner, project_id, root=root)
    if not keys or len(set(keys)) != len(keys) or any(k not in assets for k in keys):
        raise ValueError("完成した登録データを選択してください。")
    from app.services.section_metadata import build_section_manifest
    from app.services.tims_parquet_contract import read_registered_sections
    catalog, paths = [], []
    for key in keys:
        entry = assets[key]["entry"]
        validate_asset(entry)
        path = str(Path(entry["runtime_path"]).resolve())
        registry = read_registered_sections(path)
        if not registry:
            raise ValueError("登録済みParquetの切片情報を確認できません。")
        paths.append(path)
        catalog.append({"path": path, "available_rois": [r["section_display_name"] for r in registry],
                        "section_registry": registry, "registered_sections": registry, "roi_role": "section"})
    if len(paths) != len(set(paths)):
        raise ValueError("同じ登録データが重複しています。")
    return {"id": uuid.uuid4().hex, "paths": paths, "conversion_keys": list(keys),
            "manifest": build_section_manifest(catalog), "project_id": project_id,
            "folders": list(dict.fromkeys(str(Path(p).parent) for p in paths))}


def _watch(process, handle):
    try:
        process.wait()
    finally:
        from app.services.analysis_runner import _cancel_watchdog
        _cancel_watchdog(process.pid)
        if handle:
            handle.close()


def dispatch_once(*, root=None, launcher=None):
    """通常解析と共通のプロセス横断受付を通し、既存の重い処理へ割り込まない。"""
    target = _root(root)
    if not target.exists():
        return
    from app.services.analysis_runner import start_analysis_process
    from app.config import APP_DIR
    with FileLock(str(target / "dispatch.lock"), timeout=1):
        jobs = []
        for path in (target / "jobs").glob("*/state.json"):
            if not re.fullmatch(r"[a-f0-9]{32}", path.parent.name):
                continue
            state = get_job(path.parent.name, root=target)
            if state["state"] == "queued" and not state.get("pid"):
                jobs.append(state)
        for state in sorted(jobs, key=lambda s: s["created_at"]):
            folder = target / "jobs" / state["request_id"]
            with FileLock(str(folder / "run.lock"), timeout=0):
                if get_job(state["request_id"], root=target, recover=False)["state"] != "queued":
                    continue
                result = (launcher or start_analysis_process)(
                    str(APP_DIR / "tools" / "imzml_registration_worker.py"), str(folder),
                    extra_args=[str(target), state["request_id"]], interpreter=[sys.executable, "-u"])
                if not result["success"]:
                    with FileLock(str(folder / "state.lock")):
                        waiting = get_job(state["request_id"], root=target, recover=False)
                        if waiting["state"] == "queued":
                            waiting["wait_reason"] = result.get("message", "開始できるまで待機しています。")
                            write_json(folder / "state.json", waiting)
                    return
                process = result["process"]
                import psutil
                try:
                    started = psutil.Process(process.pid).create_time()
                except psutil.NoSuchProcess:
                    state.update(state="interrupted", error="登録workerを開始できませんでした。ログを確認してください。")
                    for item in state["files"]:
                        item["state"] = "interrupted"
                    started = None
                state.update(pid=process.pid, process_started_at=started)
                with FileLock(str(folder / "state.lock")):
                    if (folder / "cancel").exists() and state["state"] not in TERMINAL:
                        state["state"] = "cancelling"
                    write_json(folder / "state.json", state)
                threading.Thread(target=_watch, args=(process, result.get("log_file_handle")),
                                 daemon=True, name="imzml-registration-watch").start()
                return


def ensure_dispatcher():
    global _dispatcher
    with _dispatch_lock:
        if _dispatcher is not None and _dispatcher.is_alive():
            return
        def dispatch():
            import logging
            from filelock import Timeout
            while True:
                try:
                    dispatch_once()
                except Timeout:
                    pass
                except Exception:
                    logging.getLogger(__name__).exception("imzML queue dispatch failed")
                time.sleep(2)
        _dispatcher = threading.Thread(target=dispatch, daemon=True, name="imzml-registration-queue")
        _dispatcher.start()
