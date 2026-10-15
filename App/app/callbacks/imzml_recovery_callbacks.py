"""★ ver81.0: 復元を出力から分離し、保存結果・認証・対象世代を受付ごとに照合する。"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import secrets
import threading
import time

from dash import Input, Output, State, callback, clientside_callback, ctx, no_update
from flask import has_request_context, session

from app.services import project_manager
from app.services.section_group_metadata import load_result_manifest
from app.services.public_paths import get_path_mappings, to_display_path

_GRANTS = {}
_GRANTS_LOCK = threading.RLock()
_GRANT_SECONDS = 3600
_ACTIVE = {"queued", "running", "checking", "cancelling", "orphan_running"}
_STATE_LABELS = {"queued": "待機中", "running": "復元中", "checking": "確認中",
                 "cancelling": "停止処理中", "completed": "復元完了", "failed": "復元失敗",
                 "completed_with_errors": "一部の復元が失敗", "cancelled": "停止済み", "interrupted": "中断",
                 "orphan_running": "変換プロセスの終了待ち"}


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _principal():
    from app.services.auth_middleware import _session_valid_for_tier
    from app.services.shared_callback_policy import shared_readonly_request
    if not has_request_context() or shared_readonly_request() or not _session_valid_for_tier("A"):
        raise PermissionError("復元にはプロジェクトを変更できる解析者のログインが必要です。")
    name = session.get("analyst_name")
    if not isinstance(name, str) or not name.strip():
        raise PermissionError("解析者の認証情報を確認してください。")
    # 編集可能なブラウザ識別Cookieを所有者の証拠にしない。
    identity = session.setdefault("imzml_recovery_principal", secrets.token_urlsafe(32))
    return name, _digest([name, identity])


def _registered_roots(sub):
    roots = {sub.get("last_result_dir"), sub.get("output_dir")}
    roots.update(record.get("path") for record in (sub.get("result_refs") or {}).values()
                 if isinstance(record, dict) and not record.get("unresolved"))
    roots.update(record.get("path") for record in sub.get("result_history", [])
                 if isinstance(record, dict) and not record.get("unresolved"))
    return {str(Path(path).expanduser().resolve()) for path in roots if isinstance(path, str) and path}


def _selected_file_ids(manifest, selection, scope):
    records = manifest.get("files", []) if isinstance(manifest, dict) else None
    if not isinstance(records, list) or not all(isinstance(entry, dict) for entry in records):
        raise ValueError("保存済み入力manifestを確認してください。")
    files = [entry for entry in records if entry.get("selection_mode") != "none"]
    if any(not isinstance(entry.get("path"), str) for entry in files):
        raise ValueError("保存済み入力の元ファイル情報を確認してください。")
    if any(not entry.get("file_id") for entry in files
           if Path(entry.get("path") or "").suffix.lower() == ".imzml"):
        raise ValueError("保存済みimzML入力にファイルIDがありません。")
    imzml_ids = {str(entry["file_id"]) for entry in files
                 if Path(entry.get("path") or "").suffix.lower() == ".imzml"}
    if selection is None or isinstance(selection, dict) and selection.get("mode", "all") == "all":
        return sorted(imzml_ids)
    if not isinstance(selection, dict):
        raise ValueError("出力対象の情報が不正です。対象を選び直してください。")
    if selection.get("mode") != "selected" or selection.get("scope") != scope:
        raise ValueError("出力対象が現在の解析結果と一致しません。対象を選び直してください。")
    ids = selection.get("ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("復元する出力対象を1つ以上選択してください。")
    # クライアントのcatalog/source_file_idは信用せず、保存manifestから単位IDを再構築する。
    allowed = {}
    for entry in files:
        fid = str(entry.get("file_id") or "")
        sections = entry.get("sections", [])
        if not isinstance(sections, list) or not all(isinstance(section, dict) for section in sections):
            raise ValueError("保存済み入力の切片情報を確認してください。")
        for section in sections:
            sid = section.get("section_id")
            if sid:
                allowed["source:" + _digest(["source", fid, str(sid)])] = fid
    if any(value not in allowed for value in ids):
        raise ValueError("出力対象に保存結果へ登録されていない切片があります。対象を選び直してください。")
    return sorted({allowed[value] for value in ids} & imzml_ids)


def _authorize_context(context):
    from app.callbacks.interactive_callbacks import _detect_integration_methods
    from app.services import imzml_recovery_queue as queue
    name, owner = _principal()
    if not isinstance(context, dict) or not isinstance(context.get("id"), str):
        raise ValueError("復元対象を再確認してください。")
    if context.get("shared") or context.get("loading"):
        raise PermissionError("共有表示・読込中の結果は復元できません。")
    project_id, sub_id = context.get("project_id"), context.get("sub_project_id")
    if not isinstance(project_id, str) or not isinstance(sub_id, str) or not project_id or not sub_id:
        raise PermissionError("対象のプロジェクトとサブプロジェクトを選択してください。")
    project = project_manager.get_project(project_id)
    if not project_manager.can_modify_project(project, current_user=name):
        raise PermissionError("このプロジェクトの変換データを復元する権限がありません。")
    sub = project_manager.get_sub_project(project_id, sub_id)
    if not sub:
        raise PermissionError("サブプロジェクトが見つかりません。")
    if not isinstance(context.get("folder"), str) or not context["folder"]:
        raise ValueError("解析結果を読み込んでください。")
    root = Path(context["folder"]).expanduser().resolve()
    if root.name == "RDS_Files":
        root = root.parent
    if str(root) not in _registered_roots(sub) or not root.is_dir():
        raise PermissionError("選択した結果はこのサブプロジェクトに登録されていません。")
    methods = _detect_integration_methods(str(root))
    requested = context.get("methods")
    if (not isinstance(requested, list) or not requested
            or any(not isinstance(method, str) or method not in methods for method in requested)):
        raise ValueError("保存された出力手法を選択してください。")
    rds = context.get("rds")
    method = context.get("method")
    if (not isinstance(rds, str) or not isinstance(method, str) or method not in methods
            or Path(rds).resolve() != Path(methods[method]).resolve()):
        raise ValueError("表示中の解析結果と保存された手法が一致しません。")
    selected_paths = [Path(methods[value]).resolve() for value in set(requested + [method])]
    if any(not path.is_file() or path.parent not in {root, root / "RDS_Files"} for path in selected_paths):
        raise PermissionError("解析結果のファイルが登録範囲外です。")
    # 後から優先sidecarが追加された場合も検出する。復元対象の資産側receiptは含めない。
    records = [root / name for name in ("section_manifest.json", "analysis_params.json", "receipt.json",
                                       "analysis_methods.json", "runtime_params.json")]
    revision = queue.capture_revision([*selected_paths, *records])
    manifest = load_result_manifest(str(Path(rds).resolve()))
    file_ids = _selected_file_ids(manifest, context.get("selection"), str(root))
    if revision != queue.capture_revision([*selected_paths, *records]):
        raise ValueError("確認中に解析結果が変わりました。再確認してください。")
    snapshot = {"project_id": project_id, "sub_project_id": sub_id, "scope": str(root),
                "revision": revision,
                "manifest": deepcopy(manifest), "file_ids": file_ids}
    return owner, snapshot


def _inspect(snapshot):
    from app.services.imzml_asset_recovery import inspect_requirements
    return inspect_requirements(snapshot["manifest"], file_ids=snapshot["file_ids"])


def _issue_grant(owner, context, snapshot, report):
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _GRANTS_LOCK:
        for key in list(_GRANTS):
            if _GRANTS[key]["expires"] < now:
                _GRANTS.pop(key, None)
        if len(_GRANTS) >= 256:
            _GRANTS.pop(min(_GRANTS, key=lambda key: _GRANTS[key]["expires"]), None)
        _GRANTS[token] = {"owner": owner, "context_id": context["id"], "snapshot": deepcopy(snapshot),
                          "report": deepcopy(report), "expires": now + _GRANT_SECONDS}
    return token


def _grant(context, token):
    if not isinstance(token, str):
        raise PermissionError("復元対象を再確認してください。")
    owner, snapshot = _authorize_context(context)
    with _GRANTS_LOCK:
        grant = deepcopy(_GRANTS.get(token))
    if (not grant or grant["expires"] < time.time() or grant["owner"] != owner
            or grant["context_id"] != context["id"] or grant["snapshot"] != snapshot):
        raise PermissionError("復元対象または保存結果が変わりました。再確認してください。")
    with _GRANTS_LOCK:
        if token in _GRANTS:
            _GRANTS[token]["expires"] = time.time() + _GRANT_SECONDS
    return owner, snapshot, grant


def _report_view(context, report, nonce):
    missing = [item for item in report.get("files", []) if item.get("status") != "present"]
    details = "\n".join(f"{Path(item.get('source_path') or item.get('runtime_path') or '').name}: {_display_message(item.get('reason') or item.get('status'))}"
                        for item in missing)
    return {"context_id": context["id"], "nonce": nonce, "visible": bool(missing),
            "can_restore": bool(report.get("can_restore")), "details": details,
            "summary": (f"出力に必要な変換データが {len(missing)} ファイル不足しています。" if missing
                        else "解析時の変換データが揃っています。通常のデータ出力を実行できます。")}


def _display_message(value):
    text = str(value or "")
    for internal, public in sorted(get_path_mappings(), key=lambda pair: len(str(pair[0])), reverse=True):
        text = text.replace(str(internal), str(public)).replace(str(internal).replace("\\", "/"), str(public))
    return text


def _job_view(context, state):
    files = state.get("files") or []
    done = sum(item.get("state") in {"completed", "present"} for item in files)
    status = state.get("state", "queued")
    current_file = next((item for item in files if item.get("state")
                         not in {"queued", "completed", "present", "failed", "cancelled", "interrupted"}), {})
    progress = current_file.get("progress") or {}
    fraction = 0
    try:
        if float(progress.get("total", 0)) > 0:
            fraction = min(1, max(0, float(progress.get("done", 0)) / float(progress["total"])))
    except (TypeError, ValueError, OverflowError):
        pass
    value = 100 if status == "completed" else (100 * (done + fraction) / len(files) if files else 0)
    label = _STATE_LABELS.get(status, "処理中")
    details = "\n".join([state.get("error") or "", *[
        f"{Path(item.get('path') or '').name}: {item['error']}" for item in files if item.get("error")]]).strip()
    if status == "completed":
        label = "復元が完了しました。通常のデータ出力を実行してください。"
    elif progress.get("stage"):
        stages = {"hash": "原本を照合中", "verify_source": "原本を照合中", "source_verified": "原本照合済み",
                  "convert": "変換中", "validate": "内容を検証中", "publish": "保存中",
                  "verify": "保存結果を検証中", "reuse": "既存資産を検証中", "complete": "完了"}
        label += " ／ " + stages.get(progress["stage"], "処理中")
    if files:
        label += f" （{done}/{len(files)} ファイル完了）"
    if current_file.get("path"):
        label += " ／ " + str(to_display_path(current_file["path"]))
    return {"context_id": context["id"], "job_id": state.get("job_id") or state.get("request_id"),
            "state": status, "label": label, "details": _display_message(details), "progress": value}


def _same_job(state, snapshot):
    return all(state.get(key) == snapshot[key]
               for key in ("scope", "revision", "file_ids", "project_id", "sub_project_id"))


@callback(Output("imzml_recovery_report", "data"), Output("imzml_recovery_job", "data"),
          Input("imzml_recovery_context", "data"), Input("imzml_recovery_check", "n_clicks"))
def diagnose_recovery(context, checks):
    if not isinstance(context, dict) or not context.get("rds") or context.get("loading"):
        return None, None
    try:
        from app.services import imzml_recovery_queue as queue
        owner, snapshot = _authorize_context(context)
        report = _inspect(snapshot)
        nonce = _issue_grant(owner, context, snapshot, report)
        # 再表示時も他の結果世代・出力対象のジョブを流用しない。
        jobs = queue.list_jobs(owner, snapshot["scope"])
        matching = [job for job in jobs if _same_job(job, snapshot)]
        job = _job_view(context, matching[0]) if matching else None
        return _report_view(context, report, nonce), job
    except (OSError, ValueError, PermissionError) as exc:
        return {"context_id": context.get("id"), "visible": True, "can_restore": False,
                "summary": str(exc), "details": ""}, None


@callback(Output("imzml_recovery_job", "data", allow_duplicate=True),
          Input("imzml_recovery_start", "n_clicks"), Input("imzml_recovery_cancel", "n_clicks"),
          Input("imzml_recovery_retry", "n_clicks"), State("imzml_recovery_context", "data"),
          State("imzml_recovery_report", "data"), State("imzml_recovery_job", "data"), prevent_initial_call=True)
def recovery_action(start, cancel, retry, context, report, job):
    report = report if isinstance(report, dict) else {}
    job = job if isinstance(job, dict) else {}
    try:
        from app.services import imzml_recovery_queue as queue
        owner, snapshot, grant = _grant(context, (report or {}).get("nonce"))
        if ctx.triggered_id in {"imzml_recovery_cancel", "imzml_recovery_retry"}:
            if (job or {}).get("context_id") != context["id"]:
                raise PermissionError("復元ジョブの対象が変わりました。再確認してください。")
            identity = job.get("job_id")
            state = queue.get_job(identity, owner, scope=snapshot["scope"])
            if not _same_job(state, snapshot):
                raise PermissionError("現在の出力対象とは異なる復元ジョブです。")
            if ctx.triggered_id == "imzml_recovery_cancel":
                return _job_view(context, queue.cancel_job(identity, owner, scope=snapshot["scope"]))
            # 同じ失敗jobの連打をn_clicksで別試行に分けない。次の失敗はretry_ofが変わる。
            identity = queue.retry_job(identity, owner, token=report["nonce"], scope=snapshot["scope"])
        else:
            if not _inspect(snapshot).get("can_restore"):
                raise ValueError("現在の不足状態では復元を開始できません。再確認してください。")
            identity = queue.enqueue(snapshot, owner=owner)
        queue.ensure_dispatcher()
        return _job_view(context, queue.get_job(identity, owner, scope=snapshot["scope"]))
    except (OSError, ValueError, PermissionError) as exc:
        return {"context_id": context.get("id") if isinstance(context, dict) else None,
                "state": "error", "label": str(exc), "progress": 0}


@callback(Output("imzml_recovery_job", "data", allow_duplicate=True),
          Output("imzml_recovery_report", "data", allow_duplicate=True),
          Input("imzml_recovery_poll", "n_intervals"), State("imzml_recovery_context", "data"),
          State("imzml_recovery_report", "data"), State("imzml_recovery_job", "data"), prevent_initial_call=True)
def poll_recovery(tick, context, report, job):
    if (not isinstance(job, dict) or not isinstance(context, dict)
            or job.get("context_id") != context.get("id") or not job.get("job_id")):
        return no_update, no_update
    report = report if isinstance(report, dict) else {}
    try:
        from app.services import imzml_recovery_queue as queue
        owner, snapshot, grant = _grant(context, (report or {}).get("nonce"))
        state = queue.get_job(job["job_id"], owner, scope=snapshot["scope"])
        if not _same_job(state, snapshot):
            raise PermissionError("復元ジョブの結果世代が変わりました。再確認してください。")
        refreshed = (_report_view(context, _inspect(snapshot), report["nonce"])
                     if state.get("state") not in _ACTIVE else no_update)
        return _job_view(context, state), refreshed
    except (OSError, ValueError, PermissionError) as exc:
        return {"context_id": context["id"], "state": "error", "label": str(exc), "progress": 0}, no_update


# ★ ver81.0: 遅れて届いた診断・poll応答はブラウザの最新選択世代と一致する時だけ表示する。
clientside_callback("""function(project, sub, rds, folder, method, methods, selection, loading, shared) {
    const id = Array.from(crypto.getRandomValues(new Uint32Array(4)), x=>x.toString(16).padStart(8,'0')).join('');
    return {id:id, project_id:project, sub_project_id:sub, rds:rds, folder:folder,
        method:method, methods:methods, selection:selection,
        loading:!!(loading && loading.display !== 'none'), shared:!!(shared && shared.active)};
}""", Output("imzml_recovery_context", "data"),
    Input("interactive_project_select", "value"), Input("interactive_sub_project_select", "value"),
    Input("seurat_rds_path_store", "data"), Input("interactive_result_folder", "value"),
    Input("interactive_integration_method", "value"), Input("data_export_method_selector", "value"),
    Input("data_export_selection", "data"), Input("load_progress_container", "style"), Input("shared_session", "data"))

clientside_callback("""function(context, report, job) {
    const current = context && report && report.context_id === context.id;
    const activeJob = context && job && job.context_id === context.id ? job : null;
    if (!current) return [{display:'none'}, '', '', true, true, true, 0, '', true];
    const running = activeJob && ['queued','running','checking','cancelling','orphan_running'].includes(activeJob.state);
    const retry = activeJob && (['failed','completed_with_errors','cancelled','interrupted'].includes(activeJob.state)
        || (activeJob.state === 'completed' && report.can_restore));
    return [{display:report.visible || activeJob ? 'block':'none'}, report.summary || '',
        [report.details || '', activeJob && activeJob.details || ''].filter(Boolean).join('\\n'),
        !report.can_restore || !!running || !!retry, !running || activeJob.state === 'cancelling', !retry,
        activeJob && activeJob.progress || 0, activeJob && activeJob.label || '', !running];
}""", Output("imzml_recovery_panel", "style"), Output("imzml_recovery_summary", "children"),
    Output("imzml_recovery_details", "children"), Output("imzml_recovery_start", "disabled"),
    Output("imzml_recovery_cancel", "disabled"), Output("imzml_recovery_retry", "disabled"),
    Output("imzml_recovery_progress", "value"), Output("imzml_recovery_progress_label", "children"),
    Output("imzml_recovery_poll", "disabled"), Input("imzml_recovery_context", "data"),
    Input("imzml_recovery_report", "data"), Input("imzml_recovery_job", "data"))
