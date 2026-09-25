"""★ ver70.0: 入力準備を GUI callback から切り離し、R 終了まで1つのジョブにする。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from app.services.input_preparation import (
    PreparationCancelled, InputPreparationError, check_cancel, contains_imzml,
    prepare_inputs, write_json,
)


class DeferredAnalysis(str):
    """既存の config_path 契約を保ち、受付の排他区間まで書込みを遅延する。"""
    def __new__(cls, params, kind):
        worker = Path(__file__).resolve().parents[2] / "tools" / "run_analysis_pipeline.py"
        obj = super().__new__(cls, str(worker))
        obj.params = deepcopy(params)
        obj.kind = kind
        obj.record = None
        obj.request_path = None
        return obj


def defer_analysis(params, kind):
    if params.get("_imzml_pipeline_prepared"):
        return None
    uses_imzml = contains_imzml(params)
    # ★ ver70.0: 現在画面が空でも、再開元がimzMLなら準備workerを通す。
    source = params.get("resume_reanalysis_dir") if params.get("resume_reanalysis") else None
    if not source and params.get("resume_from_rds"):
        paths = params.get("resume_rds_paths") or []
        source = paths[0] if paths else None
    if source and not uses_imzml:
        from app.services.execution_policy import result_root
        saved_file = result_root(source) / "analysis_params.json"
        if saved_file.is_file():
            saved = json.loads(saved_file.read_text(encoding="utf-8"))
            uses_imzml = contains_imzml(saved.get("runtime_parameters") or saved)
    if uses_imzml:
        return DeferredAnalysis(params, kind)
    return None


def save_request(request, output_dir, job_meta):
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    identity = uuid.uuid4().hex
    path = root / "log" / f"pipeline_request_{identity}.json"
    gate = path.with_suffix(".accepted")
    params = deepcopy(request.params)
    payload = {"schema": 1, "kind": request.kind, "params": params,
               "record": request.record, "output_dir": str(root),
               "project_id": (job_meta or {}).get("project_id", ""),
               "gate": str(gate), "request_id": identity}
    write_json(path, payload)
    request.request_path = str(path)
    request.gate = str(gate)
    (root / "log" / "pipeline.cancel").unlink(missing_ok=True)
    write_json(root / "log" / "input_pipeline.json", {"stage": "accepted", "request_id": identity,
                "job_started_at": datetime.now().isoformat()})
    return str(path)


def pipeline_state(output_dir):
    try:
        return json.loads((Path(output_dir) / "log" / "input_pipeline.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def run_pipeline(payload, *, converter=None, validator=None, runner=None):
    """worker 本体。例外・R 非ゼロ終了・停止を成功に変換しない。"""
    root = Path(payload["output_dir"])
    state = {"request_id": payload.get("request_id"), "job_started_at": datetime.now().isoformat()}
    cancelled = False
    child = None
    cancel_file = root / "log" / "pipeline.cancel"
    def cancel():
        return cancelled or cancel_file.exists()
    def signal_stop(signum, frame):
        nonlocal cancelled
        cancelled = True
    def status(update):
        state.update(update)
        write_json(root / "log" / "input_pipeline.json", state)
        if update.get("stage"):
            print("[INPUT] " + json.dumps(update, ensure_ascii=False), flush=True)
    previous_handlers = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous_handlers[sig] = signal.signal(sig, signal_stop)
        except ValueError:  # スレッド内の単体試験では signal 登録不可。
            pass
    try:
        gate = payload.get("gate")
        deadline = time.monotonic() + 30
        while gate and not Path(gate).is_file():
            check_cancel(cancel)
            if time.monotonic() > deadline:
                raise InputPreparationError("ジョブ台帳の受付確認が得られないため解析を開始しません。")
            time.sleep(0.1)
        if gate:
            accepted = json.loads(Path(gate).read_text(encoding="utf-8"))
            if accepted.get("pid") != os.getpid() or accepted.get("request_id") != payload.get("request_id"):
                raise InputPreparationError("ジョブ受付の識別情報が一致しません。")
        params = deepcopy(payload["params"])
        from app.services.execution_policy import prepare_execution_params
        # saved-run の条件を先に復元する。元ファイルの検証は下の worker 内で行う。
        params["_imzml_defer_validation"] = True
        prepare_execution_params(params, str(root))
        params.pop("_imzml_defer_validation", None)
        options = {}
        if converter is not None:
            options["converter"] = converter
        if validator is not None:
            options["validator"] = validator
        params = prepare_inputs(params, project_id=payload.get("project_id", ""),
                                progress=status, cancel=cancel, **options)
        check_cancel(cancel)
        from app.services.analysis_runner import generate_v8_config, generate_cluster_filter_config
        generate = generate_cluster_filter_config if payload["kind"] == "reanalysis" else generate_v8_config
        script = generate(params, str(root))
        if isinstance(script, DeferredAnalysis):
            raise InputPreparationError("入力準備が完了していません。")
        record = deepcopy(payload.get("record") or {})
        persisted = {k: v for k, v in params.items() if not k.startswith("_imzml_")}
        record.update(runtime_parameters=persisted, section_manifest=params["section_manifest"],
                      input_paths=params["input_paths"], input_fingerprints=params.get("input_fingerprints"),
                      analysis_signature=params.get("analysis_signature"),
                      input_normalization="unknown; user analysis settings retained")
        write_json(root / "analysis_params.json", record)
        write_json(root / "section_manifest.json", params["section_manifest"])
        check_cancel(cancel)
        # 旧結果の完了JSONを今回のRの成功根拠にしない。Rは今回の記録を新規作成する。
        old_methods = root / "analysis_methods.json"
        if old_methods.exists():
            os.replace(old_methods, root / "log" / f"analysis_methods_before_{uuid.uuid4().hex}.json")
        status({"stage": "r_analysis", "r_started_at": datetime.now().isoformat(), "sample": ""})
        if runner is not None:
            rc = runner(script, params)
        else:
            from app.config import RSCRIPT_PATH
            executable = str(RSCRIPT_PATH) if Path(RSCRIPT_PATH).is_file() else "Rscript"
            child = subprocess.Popen([executable, "--vanilla", str(script)], cwd=Path(script).parent)
            timeout = int(os.environ.get("R_ANALYSIS_TIMEOUT_SEC", "0"))
            started = time.monotonic()
            while child.poll() is None:
                check_cancel(cancel)
                if timeout > 0 and time.monotonic() - started > timeout:
                    raise InputPreparationError(f"R 解析が R_ANALYSIS_TIMEOUT_SEC={timeout} 秒を超過しました。")
                time.sleep(0.2)
            rc = child.returncode
        check_cancel(cancel)
        if rc != 0:
            status({"stage": "error", "error": f"R 解析の終了コード: {rc}", "r_exit_code": rc})
            return rc if rc > 0 else 128 - rc
        status({"stage": "finalizing", "r_exit_code": 0})
        from app.services.execution_policy import method_outcome
        outcome = method_outcome(root)
        if not outcome or outcome.get("incomplete") or not outcome.get("available"):
            raise InputPreparationError("R は終了しましたが、必要な解析成果物を確認できません。")
        status({"stage": "complete", "finished_at": datetime.now().isoformat()})
        return 0
    except PreparationCancelled as exc:
        status({"stage": "stopped", "error": str(exc)})
        return 130
    except Exception as exc:
        status({"stage": "error", "error": str(exc)})
        import traceback
        traceback.print_exc()
        return 2
    finally:
        if child is not None and child.poll() is None:
            from app.services.process_control import terminate_tree
            terminate_tree(child.pid)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
