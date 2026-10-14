"""★ ver78.0: パスの再入力と画面往復を減らし、複数原本を一画面で登録する。"""
from copy import deepcopy
import csv
import io
from pathlib import Path

from dash import Input, Output, State, callback, ctx, no_update, html
import dash_bootstrap_components as dbc

from app.layouts.file_browser_modal import list_directory
from app.services import imzml_registration_queue as queue
from app.services.session_id import get_display_name

STATE_LABELS = {
    "queued": "待機中", "running": "処理中", "checking": "確認中", "hashing": "原本を照合中",
    "reusing": "登録済みデータを確認中", "converting": "変換中", "registering": "登録情報を更新中",
    "validating": "検証中", "publishing": "保存中", "completed": "完了", "failed": "失敗",
    "completed_with_errors": "一部のファイルが失敗", "cancelling": "停止処理中",
    "cancelled": "停止", "interrupted": "中断（再試行できます）",
}


def _project(project):
    return str((project or {}).get("id") or "")


def _alert(exc):
    return dbc.Alert(str(exc), color="danger", className="py-2")


@callback(Output("imzml_batch_ppm", "value"), Input("imzml_io_modal", "is_open"),
          prevent_initial_call=True)
def restore_registration_ppm(opened):
    if not opened:
        return no_update
    return queue.registration_preferences(get_display_name())["alignment_ppm"]


def paste_registration(rows, text):
    pasted = list(csv.reader(io.StringIO(text or ""), delimiter="\t"))
    if len(pasted) != len(rows) or any(len(row) != 3 for row in pasted):
        raise ValueError(f"表示中の {len(rows)} 切片について、切片名・個体ID・群の3列を貼り付けてください。")
    return [{**old, **dict(zip(queue.METADATA, values))} for old, values in zip(rows, pasted)]


@callback(Output("imzml_batch_folder", "value"),
          Input("imzml_batch_up", "n_clicks"), Input("imzml_batch_subfolder", "value"),
          State("imzml_batch_folder", "value"), prevent_initial_call=True)
def navigate_registration_folder(up, subfolder, folder):
    if ctx.triggered_id == "imzml_batch_up":
        return str(Path(folder or ".").expanduser().resolve().parent)
    return subfolder or no_update


@callback(Output("imzml_batch_subfolder", "options"), Output("imzml_batch_files", "options"),
          Output("imzml_batch_files", "value"), Output("imzml_batch_browser_message", "children"),
          Input("imzml_batch_folder", "value"), Input("imzml_batch_go", "n_clicks"))
def browse_registration_files(folder, clicks):
    try:
        path = Path(folder or "").expanduser().resolve()
        if not path.is_dir():
            raise ValueError("サーバー上のフォルダを指定してください。")
        items = list_directory(str(path))
        folders = [{"label": row["name"], "value": row["path"]} for row in items if row["is_dir"]]
        from app.services.input_preparation import candidate_info
        candidates = [candidate_info(row["path"]) for row in items
                      if not row["is_dir"] and Path(row["path"]).suffix.lower() == ".imzml"]
        options = [{"label": item["label"], "value": item["path"], "disabled": item["disabled"]}
                   for item in candidates]
        return folders, options, [], f"{path} ／ imzML {len(options)} 件"
    except (OSError, ValueError) as exc:
        return [], [], [], _alert(exc)


@callback(Output("imzml_batch_paths", "options"), Output("imzml_batch_paths", "value"),
          Input("imzml_batch_add", "n_clicks"), Input("imzml_batch_browse_selection", "data"),
          State("imzml_batch_files", "value"), State("imzml_batch_paths", "value"),
          prevent_initial_call=True)
def add_registration_files(clicks, browse_selection, selected, existing):
    if browse_selection is not None and ctx.triggered_id == "imzml_batch_browse_selection":
        selected = [(browse_selection or {}).get("path")]
        selected = [p for p in selected if isinstance(p, str) and Path(p).suffix.lower() == ".imzml"]
    paths = list(dict.fromkeys(str(Path(p).resolve()) for p in [*(existing or []), *(selected or [])]))
    # 同じファイルを再選択しても調査をやり直さず、入力済みの登録表を保持する。
    if paths == list(existing or []):
        return no_update, no_update
    return [{"label": p, "value": p} for p in paths], paths


@callback(Output("imzml_io_modal", "enforceFocus"),
          Input("file_browser_modal", "is_open"))
def focus_imzml_modal(browser_open):
    # 元の変換画面を閉じないので、参照の取消でも表・ppm・進捗は保持される。
    return not bool(browser_open)


@callback(Output("imzml_batch_selection", "data"),
          Input("imzml_batch_paths", "value"), Input("selected_project", "data"),
          prevent_initial_call=True)
def inspect_registration_selection(paths, project):
    if not paths:
        return {}
    return queue.begin_inspection(paths, get_display_name(), _project(project))


@callback(Output("imzml_batch_inspection", "data"),
          Input("imzml_batch_poll", "n_intervals"), State("imzml_batch_selection", "data"),
          State("imzml_batch_rendered", "data"))
def poll_registration_inspection(tick, selection, rendered=None):
    if not (selection or {}).get("id"):
        return {}
    try:
        draft = queue.get_draft(selection["id"], get_display_name())
        # ★ ver79.1: table.data callback自体がloadingを立て、未確定の手入力を失わせる。
        # render側でno_updateを返すだけでは遅いので、同じ表は上流で通知を止める。
        if (draft.get("state") == "ready"
                and {"id": draft["id"], "revision": draft["revision"]} == rendered):
            return no_update
        return draft
    except (OSError, ValueError, PermissionError):
        return no_update


@callback(Output("imzml_batch_table", "data"), Output("imzml_batch_rendered", "data"),
          Output("imzml_batch_inspection_message", "children"),
          Output("imzml_batch_spatial_buttons", "children"),
          Input("imzml_batch_inspection", "data"), Input("imzml_batch_selection", "data"),
          State("imzml_batch_rendered", "data"))
def render_registration_inspection(inspection, selection, rendered):
    if not (selection or {}).get("id"):
        return [], {}, "ファイルを追加してください。", []
    if (inspection or {}).get("id") != selection["id"]:
        return [], {}, "座標・入力形式を確認しています…", []
    # 遅れて届いたcallbackが古い表へ戻さないよう、サーバーの最新revisionを読む。
    draft = queue.get_draft(selection["id"], get_display_name())
    if draft["state"] == "inspecting":
        return no_update, no_update, "座標・入力形式を確認しています…", no_update
    if draft["state"] != "ready":
        return [], {}, [html.Div(f"{e['path']}: {e['error']}") for e in draft["errors"]], []
    marker = {"id": draft["id"], "revision": draft["revision"]}
    if marker == rendered:
        return no_update, no_update, no_update, no_update
    buttons = [dbc.Button(f"{Path(e['path']).name} の配置・切片を修正",
                id={"type": "imzml_spatial_open", "scope": "registration", "index": e["path"]},
                n_clicks=0, outline=True, size="sm", className="me-2 mb-2") for e in draft["entries"]]
    total = sum(e["spatial_layout"]["pixel_count"] for e in draft["entries"])
    return queue.table_rows(draft), marker, f"{len(draft['entries'])} ファイル ／ {total:,} pixels。全切片の不足項目を入力してください。", buttons


@callback(Output("imzml_batch_table", "data", allow_duplicate=True),
          Output("imzml_batch_edit_message", "children"),
          Input("imzml_batch_bulk", "n_clicks"), Input("imzml_batch_apply_paste", "n_clicks"),
          State("imzml_batch_table", "data"), State("imzml_batch_table", "selected_rows"),
          State("imzml_batch_bulk_field", "value"), State("imzml_batch_bulk_value", "value"),
          State("imzml_batch_paste", "value"), prevent_initial_call=True)
def edit_registration_rows(bulk, paste, rows, selected, field, value, text):
    try:
        if ctx.triggered_id == "imzml_batch_apply_paste":
            result = paste_registration(rows or [], text)
            return result, f"全 {len(result)} 行へ反映しました。内容を確認してください。"
        if field not in {"subject_id", "group"} or not str(value or "").strip() or not selected:
            raise ValueError("対象行と一括設定する値を指定してください。")
        result = deepcopy(rows or [])
        for index in selected:
            result[index][field] = str(value).strip()
        return result, f"選択した {len(selected)} 行へ反映しました。内容を確認してください。"
    except (ValueError, IndexError) as exc:
        return no_update, _alert(exc)


@callback(Output("section_catalog_store_registration", "data"),
          Output("section_manifest_store_registration", "data"),
          Input("imzml_batch_table", "data"), State("imzml_batch_selection", "data"),
          prevent_initial_call=True)
def registration_spatial_sources(rows, selection):
    if not (selection or {}).get("id"):
        return no_update, no_update
    draft = queue.get_draft(selection["id"], get_display_name())
    if draft["state"] != "ready":
        return no_update, no_update
    entries = deepcopy(draft["entries"])
    edits = {(r["file_path"], r["section_id"]): r for r in rows or []}
    for entry in entries:
        for row in entry["registered_sections"]:
            edit = edits.get((entry["path"], row["section_id"]), {})
            row.update({key: str(edit.get(key) or "") for key in queue.METADATA})
        entry["spatial_sections"] = deepcopy(entry["registered_sections"])
        entry["available_rois"] = []
    from app.services.section_metadata import build_section_manifest
    return entries, build_section_manifest(entries)


@callback(Output("imzml_batch_inspection", "data", allow_duplicate=True),
          Output("imzml_batch_edit_message", "children", allow_duplicate=True),
          Input("imzml_spatial_overrides_registration", "data"),
          State("imzml_batch_selection", "data"), State("imzml_batch_rendered", "data"),
          State("imzml_spatial_active_path_registration", "data"), State("imzml_batch_table", "data"),
          prevent_initial_call=True)
def apply_registration_spatial_edits(overrides, selection, rendered, active_path=None, rows=None):
    if not (selection or {}).get("id") or (rendered or {}).get("id") != selection["id"]:
        return no_update, no_update
    if not active_path or active_path not in (overrides or {}):
        return no_update, no_update
    try:
        draft = queue.revise_draft(selection["id"], get_display_name(), rendered["revision"],
                                   {active_path: overrides[active_path]}, rows=rows)
        return draft, no_update
    except (OSError, ValueError, PermissionError) as exc:
        return no_update, _alert(exc)


@callback(Output("imzml_batch_summary", "children"),
          Input("imzml_batch_table", "data"), Input("imzml_batch_ppm", "value"))
def registration_summary(rows, ppm):
    missing = sum(any(not str(row.get(key) or "").strip() for key in queue.METADATA) for row in rows or [])
    return f"全 {len(rows or [])} 切片 ／ 未入力 {missing} 切片 ／ 実効ppm: {ppm if ppm is not None else '未設定'} ／ 保存先: 管理領域（既存資産は上書きしません）"


@callback(Output("imzml_batch_action_message", "children"),
          Output("imzml_batch_job", "value", allow_duplicate=True),
          Input("imzml_batch_submit", "n_clicks"), Input("imzml_batch_cancel", "n_clicks"),
          Input("imzml_batch_retry", "n_clicks"),
          State("imzml_batch_selection", "data"), State("imzml_batch_rendered", "data"),
          State("imzml_batch_table", "data"), State("imzml_batch_ppm", "value"),
          State("imzml_batch_job", "value"), State("selected_project", "data"),
          prevent_initial_call=True)
def registration_action(submit, cancel, retry, selection, rendered, rows, ppm, job, project):
    owner = get_display_name()
    try:
        if ctx.triggered_id == "imzml_batch_cancel":
            from flask import session
            queue.cancel_job(job, owner, admin=session.get("access_tier") == "A")
            return "停止を要求しました。完成したデータは保持します。", no_update
        if ctx.triggered_id == "imzml_batch_retry":
            identity = queue.retry_job(job, owner, token=f"{job}:{retry}")
        else:
            if not (selection or {}).get("id"):
                raise ValueError("先にファイルを選択してください。")
            draft = queue.get_draft(selection["id"], owner)
            snapshot = queue.confirm_snapshot(draft, rows, ppm, generation=selection["id"],
                revision=(rendered or {}).get("revision"), owner=owner, project_id=_project(project))
            identity = queue.enqueue(snapshot)
            queue.registration_preferences(owner, ppm=ppm)
        queue.ensure_dispatcher()
        return "登録を受け付けました。他の重い処理がある場合は順番を待ちます。画面を閉じても処理は続きます。", identity
    except (OSError, ValueError, PermissionError) as exc:
        return _alert(exc), no_update


@callback(Output("imzml_batch_job", "options"), Output("imzml_batch_job", "value"),
          Output("imzml_batch_job_status", "children"), Output("imzml_batch_completed", "options"),
          Input("imzml_batch_poll", "n_intervals"), Input("selected_project", "data"),
          Input("imzml_batch_job", "value"))
def registration_job_status(tick, project, selected):
    owner, pid = get_display_name(), _project(project)
    jobs = queue.list_jobs(owner, pid)
    options = [{"label": f"{j['request_id'][:8]} · {STATE_LABELS.get(j['state'], '処理中')} · {len(j['files'])} ファイル", "value": j["request_id"]}
               for j in jobs]
    if selected not in {j["request_id"] for j in jobs}:
        selected = jobs[0]["request_id"] if jobs else None
    job = next((j for j in jobs if j["request_id"] == selected), None)
    children = []
    if job:
        done = sum(item["state"] == "completed" for item in job["files"])
        children.append(html.Div(f"{STATE_LABELS.get(job['state'], '処理中')} ／ 完了 {done}/{len(job['files'])} ファイル ／ 実効 {job['alignment_ppm']:g} ppm",
                                 className="fw-semibold mb-2"))
        if job["state"] == "queued" and job.get("wait_reason"):
            children.append(html.Div(job["wait_reason"], className="text-muted"))
        if job.get("error"):
            children.append(html.Div(job["error"], className="text-danger"))
        for item in job["files"]:
            progress = item.get("progress", {})
            suffix = f" {progress['done']}/{progress['total']}" if progress.get("total") else ""
            children.append(html.Div(f"{item['path']} — {STATE_LABELS.get(item['state'], '処理中')}{suffix} {item.get('error', '')}"))
    assets = queue.completed_assets(owner, pid)
    completed = [{"label": f"{a['source']} ／ {a['alignment_ppm']} ppm ／ revision {key[:8]}", "value": key}
                 for key, a in assets.items()]
    return options, selected, children, completed


@callback(Output("imzml_registered_selection", "data"),
          Output("data_folder", "value", allow_duplicate=True),
          Output("extra_data_folders_store", "data", allow_duplicate=True),
          Output("analysis_method", "value", allow_duplicate=True),
          Output("analysis_method_tims", "value", allow_duplicate=True),
          Output("resume_rds", "value", allow_duplicate=True),
          Output("resume_reanalysis", "value", allow_duplicate=True),
          Output("settings_restore_pending", "data", allow_duplicate=True),
          Output("section_manifest_store", "data", allow_duplicate=True),
          Output("main_tabs", "active_tab", allow_duplicate=True),
          Output("imzml_io_modal", "is_open", allow_duplicate=True),
          Output("imzml_batch_handoff_message", "children"),
          Input("imzml_batch_handoff", "n_clicks"), State("imzml_batch_completed", "value"),
          State("selected_project", "data"), prevent_initial_call=True)
def registration_handoff(clicks, keys, project):
    try:
        selected = queue.handoff_assets(keys, get_display_name(), _project(project))
        return (selected, selected["folders"][0], selected["folders"][1:], None, "tims_v8",
                False, False, True, selected["manifest"], "settings", False, "新規解析の入力に設定しました。")
    except (OSError, ValueError, PermissionError) as exc:
        return (*([no_update] * 11), _alert(exc))
