"""Start the imzML CLI out of the Dash request and track its receipt."""

import json
import subprocess
import sys
import uuid
from pathlib import Path

from dash import Input, Output, State, callback, no_update
import dash_bootstrap_components as dbc

from app.config import APP_DIR, OTHER_DIR

_active = {}
_log_dir = OTHER_DIR / "logs" / "imzml_io"


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
    if not source or not target:
        return no_update, True, dbc.Alert("入力と出力のパスを指定してください。", color="warning"), True
    if action not in {"import", "export"}:
        return no_update, True, dbc.Alert("操作が不正です。", color="danger"), True
    expected_source, expected_target = ((".imzml", ".parquet") if action == "import"
                                        else (".parquet", ".zip"))
    if Path(source).suffix.lower() != expected_source or Path(target).suffix.lower() != expected_target:
        return no_update, True, dbc.Alert("入力または出力の拡張子が異なります。", color="warning"), True
    if any(proc.poll() is None for proc in _active.values()):
        return no_update, True, dbc.Alert("imzML 処理が実行中です。", color="warning"), True
    if pixel_ids and action == "export":
        try:
            if not all(int(part.strip()) > 0 for part in pixel_ids.split(",")):
                raise ValueError
        except ValueError:
            return no_update, True, dbc.Alert("画素 ID は正の整数をカンマ区切りで指定してください。", color="warning"), True
    _log_dir.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    receipt = _log_dir / f"{job_id}.json"
    argv = [sys.executable, "-u", str(APP_DIR / "tools" / "imzml_io_cli.py"),
            action, source, target, "--status", str(receipt)]
    if pixel_ids and action == "export":
        argv.extend(["--pixel-ids", pixel_ids])
    with (_log_dir / f"{job_id}.log").open("wb") as log:
        proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT)
    _active[job_id] = proc
    return {"id": job_id, "receipt": str(receipt)}, False, "処理を開始しました。", False


@callback(Output("imzml_progress", "value"),
          Output("imzml_result", "children", allow_duplicate=True),
          Output("imzml_interval", "disabled", allow_duplicate=True),
          Output("imzml_stop", "disabled", allow_duplicate=True),
          Input("imzml_interval", "n_intervals"), State("imzml_job", "data"),
          prevent_initial_call=True)
def poll_imzml_job(n, job):
    if not job or not job.get("id"):
        return no_update, no_update, True, True
    proc = _active.get(job["id"])
    receipt = Path(job["receipt"])
    state = json.loads(receipt.read_text()) if receipt.is_file() else {}
    if state.get("state") == "done" and (proc is None or proc.poll() == 0):
        _active.pop(job["id"], None)
        return 100, dbc.Alert(f"完了: {state['result']}", color="success"), True, True
    if state.get("state") == "error" or proc is None or proc.poll() is not None:
        _active.pop(job["id"], None)
        return no_update, dbc.Alert(state.get("error", "処理が中断されました。ログを確認してください。"),
                                    color="danger"), True, True
    total = state.get("total", 0)
    pct = int(90 * state.get("done", 0) / total) if total else 0
    return pct, f"処理中: {state.get('done', 0)}/{total}", False, False


@callback(Output("imzml_result", "children", allow_duplicate=True),
          Output("imzml_interval", "disabled", allow_duplicate=True),
          Output("imzml_stop", "disabled", allow_duplicate=True),
          Input("imzml_stop", "n_clicks"), State("imzml_job", "data"),
          prevent_initial_call=True)
def stop_imzml_job(clicks, job):
    proc = _active.pop((job or {}).get("id"), None)
    if proc is None or proc.poll() is not None:
        return no_update, True, True
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    return dbc.Alert("処理を停止しました。未完成の出力は使用しないでください。", color="warning"), True, True
