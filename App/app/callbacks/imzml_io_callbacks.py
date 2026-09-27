"""imzML登録／変換CLIをDash request外で実行し、全切片登録を強制する。"""
from __future__ import annotations

import json
import threading
import re
import sys
import uuid
from copy import deepcopy
from pathlib import Path

from dash import Input, Output, State, callback, no_update, html
import dash_bootstrap_components as dbc

from app.config import APP_DIR, OTHER_DIR
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


@callback(
    Output("section_catalog_store_conversion", "data"),
    Output("section_manifest_store_conversion", "data"),
    Output("imzml_conversion_spatial_open_container", "children"),
    Output("imzml_registration_status", "children"),
    Input("imzml_prepare_registration", "n_clicks"),
    State("imzml_source", "value"),
    prevent_initial_call=True,
)
def prepare_conversion_registration(clicks, source):
    if not clicks:
        return no_update, no_update, no_update, no_update
    try:
        path = str(Path(source or "").expanduser().resolve())
        if Path(path).suffix.lower() != ".imzml":
            raise ValueError("入力imzMLを指定してください。")
        from app.services.imzml_spatial_layout import (
            inspect_imzml_spatial_layout, default_spatial_sections,
        )
        from app.services.section_metadata import stable_file_id, build_section_manifest
        layout = inspect_imzml_spatial_layout(path)
        file_id = stable_file_id(path)
        sections = default_spatial_sections(file_id, layout)
        catalog = [{
            "path": path, "file_id": file_id,
            "spatial_layout": layout, "spatial_sections": sections,
            "available_rois": [], "roi_role": "spatial",
        }]
        manifest = build_section_manifest(catalog)
        button = dbc.Button(
            "全切片の配置・登録情報を編集",
            id={"type": "imzml_spatial_open", "scope": "conversion", "index": path},
            n_clicks=0, size="sm", color="primary", outline=True,
        )
        return catalog, manifest, button, dbc.Alert(
            f"{layout.get('component_count', 0)}切片候補を検出しました。"
            "全切片の必須情報を登録してください。",
            color="info", className="py-2",
        )
    except Exception as exc:
        return [], {}, None, dbc.Alert(str(exc), color="danger", className="py-2")


def _registration_from_conversion_state(source, catalog, manifest, overrides):
    path = str(Path(source or "").expanduser().resolve())
    catalog_rows = deepcopy(catalog or [])
    target = next((row for row in catalog_rows
                   if str(Path(row.get("path", "")).expanduser().resolve()) == path), None)
    if not target:
        raise ValueError("先に座標切片を読み込み、全切片の登録情報を設定してください。")
    override = (overrides or {}).get(path) or (overrides or {}).get(source) or {}
    if override.get("coordinate_hash") and override.get("coordinate_hash") != \
            (target.get("spatial_layout") or {}).get("coordinate_hash"):
        raise ValueError("登録時と現在のimzML座標が一致しません。")
    sections = override.get("registered_sections") or override.get("spatial_sections")
    if sections:
        target["spatial_sections"] = deepcopy(sections)
        target["registered_sections"] = deepcopy(sections)
    from app.services.section_metadata import build_section_manifest
    rebuilt = build_section_manifest(catalog_rows, previous=manifest or {})
    entry = next(row for row in rebuilt["files"]
                 if str(Path(row["path"]).expanduser().resolve()) == path)
    from app.services.imzml_registration import build_registration_spec
    return build_registration_spec(entry)


@callback(Output("imzml_job", "data"), Output("imzml_interval", "disabled"),
          Output("imzml_result", "children"), Output("imzml_stop", "disabled"),
          Input("imzml_run", "n_clicks"), State("imzml_action", "value"),
          State("imzml_source", "value"), State("imzml_target", "value"),
          State("imzml_pixel_ids", "value"), State("imzml_alignment_ppm", "value"),
          State("section_catalog_store_conversion", "data"),
          State("section_manifest_store_conversion", "data"),
          State("imzml_spatial_overrides_conversion", "data"),
          prevent_initial_call=True)
def start_imzml_job(clicks, action, source, target, pixel_ids, alignment_ppm=0.0,
                    catalog=None, manifest=None, overrides=None):
    if not clicks:
        return no_update, no_update, no_update, no_update
    if any(proc.poll() is None for proc in list(_active.values())):
        return no_update, no_update, dbc.Alert("imzML処理が実行中です。", color="warning"), no_update
    if not source or not target or action not in {"import", "export"}:
        return no_update, no_update, dbc.Alert("入力・出力・操作を確認してください。", color="warning"), no_update
    expected_source, expected_target = ((".imzml", ".parquet") if action == "import"
                                        else (".parquet", ".zip"))
    if Path(source).suffix.lower() != expected_source or Path(target).suffix.lower() != expected_target:
        return no_update, no_update, dbc.Alert("入力または出力の拡張子が異なります。", color="warning"), no_update
    if pixel_ids and action == "export":
        try:
            if not all(int(part.strip()) > 0 for part in pixel_ids.split(",")):
                raise ValueError
        except ValueError:
            return no_update, no_update, dbc.Alert(
                "画素IDは正の整数をカンマ区切りで指定してください。", color="warning"
            ), no_update

    identity = uuid.uuid4().hex
    root, receipt = _paths({"id": identity})
    args = [action, source, target, "--status", str(receipt)]
    try:
        if action == "import":
            # ★ ver74.0: 空欄を黙って0へ変えず、inline検証と同じ契約で受付する。
            from app.utils.validation import validate_param
            valid, message = validate_param("imzml_alignment_ppm", alignment_ppm)
            if not valid:
                raise ValueError(message)
            ppm = float(alignment_ppm)
            registration = _registration_from_conversion_state(
                source, catalog, manifest, overrides
            )
            registration_path = root / "registration.json"
            write_json(registration_path, registration)
            args.extend(["--alignment-ppm", str(ppm),
                         "--registration-spec", str(registration_path)])
        elif pixel_ids:
            args.extend(["--pixel-ids", pixel_ids])
    except Exception as exc:
        return no_update, no_update, dbc.Alert(str(exc), color="danger"), no_update

    from app.services.analysis_runner import start_analysis_process
    from app.services.session_id import get_display_name
    try:
        result = start_analysis_process(
            str(APP_DIR / "tools" / "imzml_io_cli.py"), str(root),
            extra_args=args, interpreter=[sys.executable, "-u"],
        )
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
        write_json(root / "manual_job.json", {
            "id": identity, "pid": proc.pid, "process_started_at": started,
            "analyst": get_display_name(),
        })
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
        return no_update, dbc.Alert("処理を停止しました。未完成の出力は使用しないでください。",
                                    color="warning"), True, True
    if state.get("state") == "done" and not alive and exit_info.get("returncode") == 0:
        return 100, dbc.Alert(f"完了: {state['result']}", color="success"), True, True
    if not alive:
        if (job or {}).get("id") in _active:
            return no_update, "終了を確認しています。", False, False
        return no_update, dbc.Alert(
            state.get("error", "処理の正常終了を確認できません。ログを確認してください。"),
            color="danger",
        ), True, True
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
        return dbc.Alert("停止できるのは実行者または管理者だけです。",
                         color="warning"), no_update, no_update
    if not matching_process(info.get("pid"), info.get("process_started_at")):
        return no_update, True, True
    (root / "stopped").touch()
    terminate_tree(info["pid"], expected_start=info["process_started_at"], timeout=5)
    return dbc.Alert("処理を停止しました。未完成の出力は使用しないでください。",
                     color="warning"), True, True
