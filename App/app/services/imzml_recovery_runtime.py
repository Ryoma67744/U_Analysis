"""★ ver81.0: 保存specに合う固定環境で生成し、最終資産の公開は現行側へ委ねる。"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from filelock import FileLock

from app.services import input_preparation as ip

APP_ROOT = Path(__file__).resolve().parents[2]
PROFILE_ROOT = APP_ROOT / "recovery_profiles" / "v6"
GENERATOR = APP_ROOT / "tools" / "imzml_recovery_generate.py"
CODE_FILES = {
    "converter_sha256": "imzml_io.py", "processed_converter_sha256": "imzml_processed.py",
    "validator_sha256": "imzml_validation.py", "spatial_layout_sha256": "imzml_spatial_layout.py",
    "registration_sha256": "imzml_registration.py", "parquet_contract_sha256": "tims_parquet_contract.py",
}


def _json(path, limit=1024 * 1024):
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ip.InputPreparationError("復元runtimeの応答が上限を超えました。")
    return json.loads(data)


def _control_json(path, limit=1024 * 1024):
    # ★ ver81.0: Windowsでもread中のos.replaceを防ぐ。静的profileはlock対象にしない。
    with FileLock(str(Path(path).parent / ".control.lock"), timeout=5):
        return _json(path, limit)


def _write_control_json(path, value):
    with FileLock(str(Path(path).parent / ".control.lock"), timeout=5):
        ip.write_json(path, value)


def _legacy_python():
    configured = os.environ.get("MSI_IMZML_RECOVERY_V6_PYTHON", "").strip()
    return Path(configured) if configured else Path("/opt/ua-recovery-v6/bin/python")


def _legacy_expected(spec, profile):
    return {"contract": "tims-standard-all-sections-v6", "schema": 4,
            "dependencies": profile["dependencies"],
            **{key: profile["source_sha256"]["app/services/" + filename]
               for key, filename in CODE_FILES.items()},
            "processed_alignment_ppm": spec.get("processed_alignment_ppm"),
            "registration_hash": spec.get("registration_hash"),
            "intensity_dtype": "float32", "mz_name_decimals": 6,
            "all_sections_preserved": True, "block_size": 128,
            "memory_budget_mb": spec.get("memory_budget_mb"),
            "processed_max_features": spec.get("processed_max_features")}


def profile_for(entry):
    """原本を開かず、固定specと事前構築済みruntimeの可用性を返す。"""
    spec = entry.get("conversion_spec") or {}
    try:
        current = ip.conversion_spec(processed_alignment_ppm=spec.get("processed_alignment_ppm", 0),
                                     registration_hash=spec.get("registration_hash", ""))
        if current == spec:
            return {"id": "current", "available": True, "reason": "現行の変換仕様と一致します。",
                    "interpreter": sys.executable, "source_root": str(APP_ROOT),
                    "dependencies": current["dependencies"], "python_abi": list(sys.version_info[:2])}
    except (ValueError, OSError, KeyError):
        pass
    result = {"id": "unsupported", "available": False,
              "reason": "保存仕様に一致する承認済み復元runtimeがありません。"}
    try:
        profile = _json(PROFILE_ROOT / "profile.json")
        if spec != _legacy_expected(spec, profile):
            return result
        for key in ("memory_budget_mb", "processed_max_features"):
            if type(spec.get(key)) is not int or spec[key] < 1:
                return {**result, "reason": "保存された変換上限値が不正です。"}
        interpreter = _legacy_python().absolute()
        result.update(id="legacy-v6", interpreter=str(interpreter),
                      source_root=str(PROFILE_ROOT / "source"), dependencies=profile["dependencies"],
                      python_abi=profile["python_major_minor"], commit=profile["commit"],
                      code_sha256=profile["source_sha256"])
        if not interpreter.is_file():
            return {**result, "reason": "旧v6専用のPython環境が未導入です。"}
        info = _json(interpreter.parent.parent / "runtime-info.json")
        if (info.get("id") != "legacy-v6" or info.get("python_abi") != profile["python_major_minor"]
                or info.get("dependencies") != profile["dependencies"]
                or info.get("profile_sha256") != sha256((PROFILE_ROOT / "profile.json").read_bytes()).hexdigest()
                or Path(info.get("interpreter", "")).absolute() != interpreter):
            return {**result, "reason": "旧v6環境の構築記録が固定profileと一致しません。"}
        return {**result, "available": True, "reason": "検証済みの旧v6専用環境を使用します。"}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {**result, "reason": f"旧v6環境の構築記録を確認できません: {exc}"}


def _entry(entry):
    value = deepcopy(entry)
    value.pop("metadata_overlay", None)
    return value


def _relay_log(path):
    """子の異常終了情報を監督workerの永続logへ残す。取り込み量は64KiBに限定する。"""
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 65536))
            content = stream.read(65536)
        if content:
            print("[imzML recovery runtime]\n" + content.decode("utf-8", errors="replace"), flush=True)
    except OSError:
        pass  # 元の生成エラー・取消を診断logの転記失敗で置き換えない。


def _output(entry, stage):
    stage = Path(stage)
    if not stage.is_absolute() or stage.is_symlink() or not stage.is_dir():
        raise ip.InputPreparationError("復元の生成先は既存の非公開stageディレクトリに限定します。")
    output = stage / Path(entry["runtime_path"]).name
    if output.suffix.lower() != ".parquet" or output.exists() or output.with_suffix(".imzml.json").exists():
        raise ip.InputPreparationError("復元の生成先に既存資産があるか、ファイル名が不正です。")
    # stageと保存済み完成先を取り違えて旧解析を変更しない。
    if output.resolve() == Path(entry["runtime_path"]).resolve():
        raise ip.InputPreparationError("完成先へ直接生成することはできません。")
    return output


def generate(entry, stage, *, cancel=None, progress=None, child_callback=None):
    """生成結果のみ返す。旧runtimeの起動gateは子の永続登録が成功した後に開く。"""
    value = _entry(entry)
    profile = profile_for(value)
    if not profile["available"]:
        raise ip.InputPreparationError(profile["reason"])
    output = _output(value, stage)
    ip.check_cancel(cancel)
    if profile["id"] == "current":
        from app.services.imzml_registration import build_registration_spec
        from app.services.imzml_validation import checked_import
        registration = build_registration_spec(value)
        actual = ip.conversion_spec(processed_alignment_ppm=value["conversion_spec"]["processed_alignment_ppm"],
                                    registration_hash=registration["registration_hash"])
        if actual != value["conversion_spec"] or ip._key(value["source_fingerprint"], actual) != value["conversion_key"]:
            raise ip.InputPreparationError("保存された登録情報または変換keyを再現できません。")
        def on_pixels(done, total):
            ip.check_cancel(cancel)
            if progress:
                progress({"stage": "convert", "done": done, "total": total})
        result = checked_import(Path(value["path"]), output, registration=registration,
            block_size=actual["block_size"], memory_budget_mb=actual["memory_budget_mb"],
            alignment_ppm=actual["processed_alignment_ppm"], progress=on_pixels, cancel=cancel)
        ip.check_cancel(cancel)
        return {**result, "profile_id": "current", "runtime": profile}
    return _generate_legacy(value, output, profile, cancel=cancel,
                            progress=progress, child_callback=child_callback)


def _generate_legacy(entry, output, profile, *, cancel, progress, child_callback):
    import psutil
    from app.services.process_control import terminate_tree
    control = output.parent / (".runtime-" + uuid.uuid4().hex)
    control.mkdir(mode=0o700)
    parent = psutil.Process()
    request = {"schema": 1, "entry": entry, "output": str(output),
               "parent_pid": parent.pid, "parent_started_at": parent.create_time()}
    _write_control_json(control / "request.json", request)
    env = {k: v for k, v in os.environ.items()
           if k not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONSTARTUP"}}
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    spec = entry["conversion_spec"]
    env["IMZML_BLOCK_MB"] = str(spec["memory_budget_mb"])
    env["IMZML_PROCESSED_MAX_FEATURES"] = str(spec["processed_max_features"])
    process = None
    started = None
    succeeded = False
    with (control / "worker.log").open("wb") as log:
        try:
            process = subprocess.Popen([profile["interpreter"], "-I", "-B", "-X", "utf8", "-u", str(GENERATOR),
                                        "--control", str(control)], cwd=control, env=env,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            started = psutil.Process(process.pid).create_time()
            # Windows venvの起動redirectorと実Pythonを区別し、実際の生成子を記録する。
            deadline = time.monotonic() + 60
            while not (control / "ready.json").is_file():
                ip.check_cancel(cancel)
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise ip.InputPreparationError("旧runtimeの開始準備を確認できません。")
                time.sleep(0.05)
            ready = _control_json(control / "ready.json", 65536)
            child = psutil.Process(int(ready["pid"]))
            actual_start = child.create_time()
            if (abs(actual_start - float(ready["process_started_at"])) > 0.01
                    or not any(p.pid == process.pid and abs(p.create_time() - started) <= 0.01
                               for p in [child, *child.parents()])):
                raise ip.InputPreparationError("旧runtimeの起動プロセスを検証できません。")
            if child_callback:
                child_callback(child.pid, actual_start)
            ip.check_cancel(cancel)
            _write_control_json(control / "start.json", {"pid": child.pid, "process_started_at": actual_start})
            last = None
            while process.poll() is None:
                ip.check_cancel(cancel)
                path = control / "progress.json"
                try:
                    marker = path.stat().st_mtime_ns
                    if progress and marker != last:
                        progress(_control_json(path, 65536))
                        last = marker
                except FileNotFoundError:
                    pass
                time.sleep(0.05)
            ip.check_cancel(cancel)
            if process.returncode:
                error = _control_json(control / "result.json") if (control / "result.json").is_file() else {}
                raise ip.InputPreparationError(error.get("error") or f"旧runtimeが終了コード {process.returncode} で停止しました。")
            ip.check_cancel(cancel)
            result = _control_json(control / "result.json")
            if (result.get("ok") is not True or result.get("parquet") != str(output)
                    or result.get("manifest") != str(output.with_suffix(".imzml.json"))):
                raise ip.InputPreparationError("旧runtimeの生成結果が要求と一致しません。")
            succeeded = True
        finally:
            if process is not None and process.poll() is None:
                (control / "cancel").touch()
                terminate_tree(process.pid, expected_start=started)
                process.wait(timeout=10)
            log.flush()
            _relay_log(control / "worker.log")
    # ★ ver81.0: 制御要求・ログを完成assetへ公開しない。失敗stageは呼出側が破棄する。
    if succeeded:
        shutil.rmtree(control)
        return {**result, "profile_id": "legacy-v6", "runtime": result["runtime"]}
