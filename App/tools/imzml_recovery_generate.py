"""★ ver81.0: 固定legacyコードを専用Pythonから読み、stage内に生成だけを行う。"""
from __future__ import annotations

import argparse
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import traceback

PROFILE_ROOT = Path(__file__).resolve().parents[1] / "recovery_profiles" / "v6"


def write_json(path, value):
    fd, name = tempfile.mkstemp(prefix=".runtime-json-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def control_write(path, value):
    # ★ ver81.0: 親の小JSON読取と同一lockでatomic置換を保護する。
    from filelock import FileLock
    with FileLock(str(path.parent / ".control.lock"), timeout=5):
        write_json(path, value)


def control_read(path):
    from filelock import FileLock
    with FileLock(str(path.parent / ".control.lock"), timeout=5):
        return json.loads(path.read_text(encoding="utf-8"))


def load_runtime():
    sys.dont_write_bytecode = True
    profile_path = PROFILE_ROOT / "profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if list(sys.version_info[:2]) != profile["python_major_minor"] or sys.implementation.name != "cpython":
        raise ValueError("旧v6 runtimeにはCPython 3.12が必要です。")
    if sys.prefix == sys.base_prefix:
        raise ValueError("旧v6 runtimeは独立venvから起動してください。")
    root = PROFILE_ROOT / "source"
    # 検証した.pyより未署名bytecodeが先に読み込まれる経路を残さない。
    if any(root.rglob("*.pyc")) or {p.relative_to(root).as_posix() for p in root.rglob("*.py")} != set(profile["source_sha256"]):
        raise ValueError("旧runtimeに未承認のコードまたはbytecodeがあります。")
    for relative, expected in profile["source_sha256"].items():
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("旧runtimeのコード境界が不正です。")
        if sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"旧runtimeのコードSHAが一致しません: {relative}")
    actual = {name: version(name) for name in profile["dependencies"]}
    if actual != profile["dependencies"]:
        raise ValueError("旧runtimeの依存版が保存profileと一致しません。")
    # -Iで現行Appを探索対象から外し、固定bundleだけをapp packageとして登録する。
    if "app" in sys.modules:
        raise ValueError("旧runtimeへ別のapp packageが混入しています。")
    sys.path.insert(0, str(root))
    import numpy
    import pyarrow
    import pyimzml
    from app.services import input_preparation as ip
    for module in (numpy, pyarrow, pyimzml):
        if not Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
            raise ValueError("旧runtimeへ外部site-packagesが混入しています。")
    if not Path(ip.__file__).resolve().is_relative_to(root.resolve()):
        raise ValueError("旧runtimeへ現行appが混入しています。")
    return ip, {"id": "legacy-v6", "commit": profile["commit"],
        "python_abi": list(sys.version_info[:2]), "dependencies": actual,
        "profile_sha256": sha256(profile_path.read_bytes()).hexdigest(),
        "interpreter": str(Path(sys.executable).absolute()), "source_root": str(root),
        "code_sha256": profile["source_sha256"]}


def generate(control):
    request = control_read(control / "request.json")
    import psutil
    cancelled = False
    def stop(*_):
        nonlocal cancelled
        cancelled = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def must_stop():
        if cancelled or (control / "cancel").exists():
            return True
        try:
            parent = psutil.Process(request["parent_pid"])
            return abs(parent.create_time() - request["parent_started_at"]) > 0.01 or not parent.is_running()
        except psutil.NoSuchProcess:
            return True
    control_write(control / "ready.json", {"pid": os.getpid(),
                                       "process_started_at": psutil.Process().create_time()})
    deadline = time.monotonic() + 120
    while not (control / "start.json").is_file():
        if must_stop() or time.monotonic() >= deadline:
            raise ValueError("監督workerの開始許可がないため変換を開始しません。")
        time.sleep(0.05)
    gate = control_read(control / "start.json")
    if gate.get("pid") != os.getpid() or abs(gate.get("process_started_at", 0) - psutil.Process().create_time()) > 0.01:
        raise ValueError("監督workerの開始許可が別プロセスを参照しています。")
    ip, runtime = load_runtime()
    ip.check_cancel(must_stop)
    from app.services.imzml_registration import build_registration_spec
    from app.services.imzml_validation import checked_import
    entry = request["entry"]
    entry.pop("metadata_overlay", None)
    output = Path(request["output"])
    if (output.parent != control.parent or output.suffix.lower() != ".parquet"
            or output.exists() or output.with_suffix(".imzml.json").exists()
            or output.resolve() == Path(entry["runtime_path"]).resolve()):
        raise ValueError("旧runtimeの生成先が許可されたstageではありません。")
    registration = build_registration_spec(entry)
    spec = ip.conversion_spec(processed_alignment_ppm=entry["conversion_spec"]["processed_alignment_ppm"],
                              registration_hash=registration["registration_hash"])
    if spec != entry["conversion_spec"] or ip._key(entry["source_fingerprint"], spec) != entry["conversion_key"]:
        raise ValueError("旧runtimeで保存登録仕様またはconversion_keyを再現できません。")
    last = 0.0
    def progress(done, total):
        nonlocal last
        ip.check_cancel(must_stop)
        now = time.monotonic()
        if now - last >= 1 or done >= total:
            control_write(control / "progress.json", {"stage": "convert", "done": done, "total": total})
            last = now
    result = checked_import(entry["path"], output, registration=registration,
        block_size=spec["block_size"], memory_budget_mb=spec["memory_budget_mb"],
        alignment_ppm=spec["processed_alignment_ppm"], progress=progress, cancel=must_stop)
    ip.check_cancel(must_stop)
    return {"ok": True, **result, "runtime": runtime}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--runtime-info", type=Path)
    args = parser.parse_args(argv)
    if args.probe:
        _, result = load_runtime()
        if args.runtime_info:
            write_json(args.runtime_info, result)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.control is None or not args.control.is_absolute() or args.control.is_symlink():
        parser.error("--control には監督workerが作成した絶対stageパスが必要です")
    try:
        result = generate(args.control)
        code = 0
    except Exception as exc:
        traceback.print_exc()
        result, code = {"ok": False, "error": str(exc)}, 2
    control_write(args.control / "result.json", result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
