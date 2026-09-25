"""Start the imzML CLI out of the Dash request and track its receipt."""

import json
import threading
import re
import sys
import uuid
from pathlib import Path

from dash import Input, Output, State, callback, no_update
import dash_bootstrap_components as dbc

from app.config import APP_DIR, OTHER_DIR

# ★ ver70.0: ジョブ情報をディスクに保存し、再接続時も同じPIDを確認する。
from app.services.input_preparation import write_json
from app.services.process_control import matching_process, terminate_tree
_active = {}
_log_dir = OTHER_DIR / "logs" / "imzml_io"


def _paths(job):
    identity = (job or {}).get("id", "")
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise ValueError("ジョブIDが不正です。")
    root = _log_dir / identity
    return root, root / "status.json"


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _watch(process, root, handle):
    try:
        rc = process.wait()
        write_json(root / "exit.json", {"returncode": rc})
    finally:
        from app.services.analysis_runner import _cancel_watchdog
        _cancel_watchdog(process.pid)
        if handle:
            handle.close()
        _active.pop(root.name, None)


@callback(Output("imzml_io_modal", "is_open"),
          Input("open_imzml_io_modal", "n_clicks"), Input("imzml_close", "n_clicks"),
          State("imzml_io_modal", "is_open"), prevent_initial_call=True)
def toggle_imzml_modal(opened, closed, is_open):
    from dash import ctx
    return ctx.triggered_id == "open_imzml_io_modal"


@callback(Output("imzml_job", "data"), Output("imzml_interval", "disabled"),
          Output("imzml_result", "children"), Output("imzml_stop", "disabled"),
          Input("imzml_run", "n_clicks"), State("imzml_action", "value"),
          State("imzml_source", "value"), State("imzml_target", "value"),
          State("imzml_pixel_ids", "value"), prevent_initial_call=True)
def start_imzml_job(clicks, action, source, target, pixel_ids):
    if not clicks:
        return no_update, no_update, no_update, no_update
    # ★ ver70.0: 二度押し・入力欄の編集が現在の監視/停止を無効にしない。
    if any(proc.poll() is None for proc in list(_active.values())):
        return no_update, no_update, dbc.Alert("imzML 処理が実行中です。", color="warning"), no_update
    if not source or not target or action not in {"import", "export"}:
        return no_update, no_update, dbc.Alert("入力・出力・操作を確認してください。", color="warning"), no_update
    expected_source, expected_target = ((".imzml", ".parquet") if action == "import" else (".parquet", ".zip"))
    if Path(source).suffix.lower() != expected_source or Path(target).suffix.lower() != expected_target:
        return no_update, no_update, dbc.Alert("入力または出力の拡張子が異なります。", color="warning"), no_update
    if pixel_ids and action == "export":
        try:
            if not all(int(part.strip()) > 0 for part in pixel_ids.split(",")):
                raise ValueError
        except ValueError:
            return no_update, no_update, dbc.Alert("画素 ID は正の整数をカンマ区切りで指定してください。", color="warning"), no_update
    identity = uuid.uuid4().hex
    root, receipt = _paths({"id": identity})
    args = [action, source, target, "--status", str(receipt)]
    if pixel_ids and action == "export":
        args.extend(["--pixel-ids", pixel_ids])
    # 既存解析と同じ受付ロック・プロセス記録を使用する。UMAPのfinalizerは呼ばない。
    from app.services.analysis_runner import start_analysis_process
    from app.services.session_id import get_display_name
    try:
        result = start_analysis_process(str(APP_DIR / "tools" / "imzml_io_cli.py"),
            str(root), extra_args=args, interpreter=[sys.executable, "-u"])
    except Exception as exc:
        return no_update, no_update, dbc.Alert(str(exc), color="danger"), no_update
    if not result["success"]:
        return no_update, no_update, dbc.Alert(result["message"], color="warning"), no_update
    proc = result["process"]
    try:
        import psutil
        try:
            started = psutil.Process(proc.pid).create_time()
        except psutil.NoSuchProcess:
            started = None
        write_json(root / "manual_job.json", {"id": identity, "pid": proc.pid,
            "process_started_at": started, "analyst": get_display_name()})
        _active[identity] = proc
        threading.Thread(target=_watch, args=(proc, root, result.get("log_file_handle")),
                         name=f"imzml-{identity}", daemon=True).start()
    except Exception:
        terminate_tree(proc.pid)
        if result.get("log_file_handle"):
            result["log_file_handle"].close()
        raise
    return {"id": identity}, False, "処理を開始しました。", False


@callback(Output("imzml_progress", "value"),
          Output("imzml_result", "children", allow_duplicate=True),
          Output("imzml_interval", "disabled", allow_duplicate=True),
          Output("imzml_stop", "disabled", allow_duplicate=True),
          Input("imzml_interval", "n_intervals"), State("imzml_job", "data"),
          prevent_initial_call=True)
def poll_imzml_job(n, job):
    try:
        root, receipt = _paths(job)
    except ValueError:
        return no_update, no_update, True, True
    state, info, exit_info = _read(receipt), _read(root / "manual_job.json"), _read(root / "exit.json")
    alive = matching_process(info.get("pid"), info.get("process_started_at"))
    if (root / "stopped").exists():
        return no_update, dbc.Alert("処理を停止しました。未完成の出力は使用しないでください。", color="warning"), True, True
    # done JSONだけでは成功にしない。終了コード0とプロセス終了の両方を確認する。
    if state.get("state") == "done" and not alive and exit_info.get("returncode") == 0:
        return 100, dbc.Alert(f"完了: {state['result']}", color="success"), True, True
    if not alive:
        # watchスレッドの終了記録との短い競合中は監視を続ける。
        if (job or {}).get("id") in _active:
            return no_update, "終了を確認しています。", False, False
        return no_update, dbc.Alert(state.get("error", "処理の正常終了を確認できません。ログを確認してください。"), color="danger"), True, True
    total = state.get("total", 0)
    pct = int(90 * state.get("done", 0) / total) if total else 0
    return pct, f"処理中: {state.get('done', 0)}/{total}", False, False


@callback(Output("imzml_result", "children", allow_duplicate=True),
          Output("imzml_interval", "disabled", allow_duplicate=True),
          Output("imzml_stop", "disabled", allow_duplicate=True),
          Input("imzml_stop", "n_clicks"), State("imzml_job", "data"),
          prevent_initial_call=True)
def stop_imzml_job(clicks, job):
    try:
        root, _ = _paths(job)
    except ValueError:
        return no_update, True, True
    info = _read(root / "manual_job.json")
    from app.services.session_id import get_display_name
    from app.services.job_registry import may_stop
    from flask import session
    if not may_stop(info, get_display_name()) and session.get("access_tier") != "A":
        return dbc.Alert("停止できるのは実行者または管理者だけです。", color="warning"), no_update, no_update
    if not matching_process(info.get("pid"), info.get("process_started_at")):
        return no_update, True, True
    (root / "stopped").touch()
    terminate_tree(info["pid"], expected_start=info["process_started_at"], timeout=5)
    return dbc.Alert("処理を停止しました。未完成の出力は使用しないでください。", color="warning"), True, True
