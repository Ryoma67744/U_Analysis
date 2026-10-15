"""★ ver81.0: 固定旧環境の隔離・開始gate・旧checked_importとの完全一致を確認する。"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from app.services import imzml_recovery_runtime as runtime
from app.services import input_preparation as ip


def legacy_spec():
    profile = runtime._json(runtime.PROFILE_ROOT / "profile.json")
    return runtime._legacy_expected({"processed_alignment_ppm": 0.0,
        "registration_hash": "", "memory_budget_mb": 64,
        "processed_max_features": 100000}, profile)


@pytest.fixture
def legacy_python():
    profile = runtime.profile_for({"conversion_spec": legacy_spec()})
    if not profile["available"]:
        if os.environ.get("IMZML_RECOVERY_RUNTIME_STRICT") == "1":
            pytest.fail(profile["reason"])
        pytest.skip(profile["reason"])
    return profile["interpreter"]


def test_vendor_bytes_match_fixed_profile():
    profile = runtime._json(runtime.PROFILE_ROOT / "profile.json")
    assert profile["commit"] == "b34fc86b942d405f4b52d54f0a91f11e7a5f9542"
    assert len(profile["source_sha256"]) == 11
    for relative, expected in profile["source_sha256"].items():
        content = (runtime.PROFILE_ROOT / "source" / relative).read_bytes()
        assert b"\r\n" not in content
        assert sha256(content).hexdigest() == expected
    assert profile["source_sha256"]["app/services/imzml_io.py"] == (
        "affe27bf9b0f3078fcdd1d472ffcdc13415f4716a48410d78208b585a9303cff")


def test_current_profile_detects_spec_drift_without_launch(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    spec = {"contract": "test-current", "dependencies": {"numpy": "test"}}
    monkeypatch.setattr(ip, "conversion_spec", lambda **k: deepcopy(spec))
    assert runtime.profile_for({"conversion_spec": spec})["id"] == "current"
    saved = {**spec, "converter_sha256": "old"}
    assert runtime.profile_for({"conversion_spec": saved})["available"] is False


@pytest.mark.parametrize("change", ["missing", "abi", "deps", "profile_sha", "interpreter"])
def test_unavailable_legacy_runtime_is_explicit(tmp_path, monkeypatch, change):
    interpreter = tmp_path / "Scripts" / "python.exe"
    interpreter.parent.mkdir()
    if change != "missing":
        interpreter.touch()
    profile = runtime._json(runtime.PROFILE_ROOT / "profile.json")
    info = {"id": "legacy-v6", "python_abi": [3, 12],
        "dependencies": profile["dependencies"],
        "profile_sha256": sha256((runtime.PROFILE_ROOT / "profile.json").read_bytes()).hexdigest(),
        "interpreter": str(interpreter)}
    if change == "abi":
        info["python_abi"] = [3, 11]
    if change == "deps":
        info["dependencies"] = {"numpy": "2.5.4"}
    if change == "profile_sha":
        info["profile_sha256"] = "changed"
    if change == "interpreter":
        info["interpreter"] = str(tmp_path / "elsewhere" / "python.exe")
    ip.write_json(tmp_path / "runtime-info.json", info)
    monkeypatch.setenv("MSI_IMZML_RECOVERY_V6_PYTHON", str(interpreter))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    found = runtime.profile_for({"conversion_spec": legacy_spec()})
    assert found["id"] == "legacy-v6"
    assert found["available"] is False
    assert found["reason"]


def make_entry(tmp_path, representation="common"):
    import numpy as np
    from pyimzml.ImzMLWriter import ImzMLWriter
    from app.services.imzml_registration import build_registration_spec
    from app.services.imzml_spatial_layout import inspect_imzml_spatial_layout, default_spatial_sections
    from app.services.section_metadata import stable_file_id
    xml = tmp_path / "sample.imzML"
    coordinates = [(6, 10, 1), (5, 9, 1), (6, 9, 1), (5, 10, 1)]
    axis = np.array([100.123456789, 201.234567891, 302.345678912], dtype=np.float64)
    with ImzMLWriter(str(xml), mode="continuous" if representation == "common" else "processed",
                     spec_type="profile" if representation == "common" else "centroid",
                     mz_dtype=np.float64, intensity_dtype=np.float64) as writer:
        for index, coordinate in enumerate(coordinates):
            mz = axis if representation == "common" or index % 2 else axis[:2]
            writer.addSpectrum(mz, np.arange(len(mz), dtype=np.float64) + index + .125, coordinate)
    tree = ET.parse(xml)
    for element in tree.iter():
        if element.get("accession") == "MS:1000511":
            element.set("value", "1")
    ET.register_namespace("", "http://psi.hupo.org/ms/mzml")
    tree.write(xml, encoding="utf-8", xml_declaration=True)
    layout = inspect_imzml_spatial_layout(xml)
    file_id = stable_file_id(str(xml))
    sections = default_spatial_sections(file_id, layout)
    for section in sections:
        section.update(section_display_name="Whole_section", subject_id="Mouse_01",
                       group="Control", metadata_confirmed=True, selected=True)
    entry = {"path": str(xml), "file_id": file_id, "spatial_layout": layout,
             "registered_sections": sections, "spatial_sections": sections,
             "runtime_path": str(tmp_path / "final" / "sample.parquet")}
    entry["source_fingerprint"] = {"xml": ip.fingerprint(xml), "ibd": ip.fingerprint(xml.with_suffix(".ibd"))}
    entry["conversion_spec"] = legacy_spec()
    entry["conversion_spec"]["registration_hash"] = build_registration_spec(entry)["registration_hash"]
    entry["conversion_key"] = ip._key(entry["source_fingerprint"], entry["conversion_spec"])
    return entry


# adapterを経由しない当時のchecked_import直呼びを独立した基準にする。
BASELINE = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app.services import input_preparation as ip
from app.services.imzml_registration import build_registration_spec
from app.services.imzml_validation import checked_import
entry = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
registration = build_registration_spec(entry)
spec = ip.conversion_spec(processed_alignment_ppm=0, registration_hash=registration["registration_hash"])
assert spec == entry["conversion_spec"]
assert ip._key(entry["source_fingerprint"], spec) == entry["conversion_key"]
result = checked_import(Path(entry["path"]), Path(sys.argv[3]), registration=registration,
    block_size=128, memory_budget_mb=64, alignment_ppm=0)
Path(sys.argv[4]).write_text(json.dumps(result), encoding="utf-8")
'''


@pytest.mark.parametrize("representation", ["common", "processed"])
def test_legacy_generation_matches_historical_bytes_and_current_validation(tmp_path, monkeypatch, legacy_python, representation):
    from app.services.imzml_validation import validate_converted_table
    import psutil
    entry = make_entry(tmp_path, representation)
    before = deepcopy(entry["source_fingerprint"])
    baseline = tmp_path / "baseline"
    stage = tmp_path / "stage"
    baseline.mkdir()
    stage.mkdir()
    script = tmp_path / "baseline.py"
    script.write_text(BASELINE, encoding="utf-8")
    ip.write_json(tmp_path / "entry.json", entry)
    env = {**os.environ, "IMZML_BLOCK_MB": "64", "IMZML_PROCESSED_MAX_FEATURES": "100000"}
    subprocess.run([legacy_python, "-I", "-B", "-X", "utf8", str(script), str(runtime.PROFILE_ROOT / "source"),
                    str(tmp_path / "entry.json"), str(baseline / "sample.parquet"),
                    str(tmp_path / "baseline-result.json")], env=env, check=True, capture_output=True, timeout=60)
    # 現行AppをPYTHONPATHへ載せても-Iと固定bundleから旧実装だけが読み込まれる。
    monkeypatch.setenv("PYTHONPATH", str(runtime.APP_ROOT))
    children = []
    def registered(pid, started):
        assert not (stage / "sample.parquet").exists()
        assert not list(stage.glob(".runtime-*/start.json"))
        assert psutil.Process(pid).create_time() == started
        children.append((pid, started))
    result = runtime.generate(entry, stage, child_callback=registered)
    assert children and not psutil.pid_exists(children[0][0])
    for suffix in (".parquet", ".imzml.json"):
        assert (stage / ("sample" + suffix)).read_bytes() == (baseline / ("sample" + suffix)).read_bytes()
    summary = runtime._json(tmp_path / "baseline-result.json")["validation_summary"]
    assert result["validation_summary"] == summary
    assert validate_converted_table(stage / "sample.parquet") == summary
    assert result["runtime"]["dependencies"] == {"numpy": "2.5.3", "pyarrow": "25.0.1", "pyimzml": "1.5.5"}
    assert result["runtime"]["source_root"] == str(runtime.PROFILE_ROOT / "source")
    assert not list(stage.glob(".runtime-*"))
    assert not Path(entry["runtime_path"]).exists()
    assert {key: ip.fingerprint(value["path"]) for key, value in before.items()} == before


def test_current_generation_stays_inprocess_and_uses_path(tmp_path, monkeypatch):
    from app.services.imzml_registration import build_registration_spec
    from app.services import imzml_validation
    entry = make_entry(tmp_path)
    entry["conversion_spec"] = ip.conversion_spec(registration_hash=build_registration_spec(entry)["registration_hash"])
    entry["conversion_key"] = ip._key(entry["source_fingerprint"], entry["conversion_spec"])
    stage = tmp_path / "stage"
    stage.mkdir()
    calls = []
    def checked(source, output, **kwargs):
        assert isinstance(source, Path)
        calls.append(kwargs)
        return {"parquet": str(output)}
    monkeypatch.setattr(imzml_validation, "checked_import", checked)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("current must not launch"))
    result = runtime.generate(entry, stage)
    assert calls and result["profile_id"] == "current"


@pytest.mark.parametrize("reason", ["cancel", "registration_failure"])
def test_child_never_generates_before_successful_gate(tmp_path, legacy_python, reason):
    import psutil
    entry = make_entry(tmp_path)
    stage = tmp_path / "stage"
    stage.mkdir()
    cancelled = False
    children = []
    def registered(pid, started):
        nonlocal cancelled
        children.append(pid)
        assert not list(stage.glob(".runtime-*/start.json"))
        if reason == "registration_failure":
            raise RuntimeError("lease persistence failed")
        cancelled = True
    error = RuntimeError if reason == "registration_failure" else ip.PreparationCancelled
    with pytest.raises(error):
        runtime.generate(entry, stage, cancel=lambda: cancelled, child_callback=registered)
    assert children and not psutil.pid_exists(children[0])
    assert not list(stage.glob("*.parquet"))
    assert not list(stage.glob(".runtime-*/start.json"))


def test_child_rejects_changed_registration_without_writing(tmp_path, legacy_python, capsys):
    entry = make_entry(tmp_path)
    entry["registered_sections"][0]["subject_id"] = "Changed"
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(ip.InputPreparationError, match="保存登録仕様"):
        runtime.generate(entry, stage)
    assert not list(stage.glob("*.parquet"))
    assert "保存登録仕様" in capsys.readouterr().out


def test_probe_rejects_current_dependency_environment():
    if sys.version_info[:2] != (3, 12):
        pytest.skip("CPython 3.12 runtime test")
    from importlib.metadata import version
    if version("numpy") == "2.5.3" and version("pyarrow") == "25.0.1":
        pytest.skip("current environment happens to equal v6 dependencies")
    result = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(runtime.GENERATOR), "--probe"],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode != 0
    assert "依存版" in result.stderr or "独立venv" in result.stderr


@pytest.mark.parametrize("change", ["source", "pyc", "extra_source"])
def test_runtime_rejects_unsigned_or_changed_code(tmp_path, monkeypatch, change):
    import importlib.util
    import shutil
    profile = tmp_path / "profile"
    shutil.copytree(runtime.PROFILE_ROOT, profile)
    source = profile / "source" / "app" / "__init__.py"
    if change == "source":
        source.write_bytes(source.read_bytes() + b"\n# changed\n")
    elif change == "pyc":
        source.with_suffix(".pyc").write_bytes(b"unsigned")
    else:
        source.with_name("unapproved.py").write_bytes(b"pass\n")
    spec = importlib.util.spec_from_file_location("_recovery_generate_test", runtime.GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "PROFILE_ROOT", profile)
    with pytest.raises(ValueError, match="コードSHA|未承認"):
        module.load_runtime()


def test_child_exits_when_supervisor_identity_disappears(tmp_path, legacy_python):
    import psutil
    control = tmp_path / ".runtime-parent-check"
    control.mkdir()
    parent = psutil.Process()
    # 同一PIDでも生成時刻が異なれば監督workerの死亡/PID再利用と扱う。
    ip.write_json(control / "request.json", {"parent_pid": parent.pid,
                  "parent_started_at": parent.create_time() - 100})
    result = subprocess.run([legacy_python, "-I", "-B", "-X", "utf8", str(runtime.GENERATOR),
                            "--control", str(control)], capture_output=True, timeout=15)
    assert result.returncode == 2
    assert "開始許可がない" in runtime._json(control / "result.json")["error"]
    assert not list(tmp_path.glob("*.parquet"))


def test_saved_toxoplasma_conversion_key_is_reproduced_without_raw_data():
    # 保存specと原本SHAの既知値だけを再計算する。実出力復元を保証する試験ではない。
    spec = legacy_spec()
    spec["registration_hash"] = "1aab20a9e8e35acb1f644f2a71fd9308a58597d38dc4932571696e41e87e2b3e"
    source = {"xml": {"sha256": "d7b8b858fe9ebb89f74956154da89c5c2bad5967b3041d64884ab15d942d0027"},
              "ibd": {"sha256": "7152189162c3537cd7f59d2d31a607b56088d27e3829ca81abe2bb15139ada1b"}}
    assert ip._key(source, spec) == "d4d5cf1851fec80992d3d1508acf1829695aef6f7cdcb261404d7d5afb098522"


def test_control_writer_waits_for_parent_read_handle(tmp_path, monkeypatch, legacy_python):
    """read handleを保持した親と実旧venv子を競合させ、置換が解放まで待つことを確認。"""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import time
    path = tmp_path / "progress.json"
    runtime._write_control_json(path, {"done": 0})
    opened, release = Event(), Event()
    original = runtime._json
    def held_read(candidate, limit=1024 * 1024):
        if Path(candidate) != path:
            return original(candidate, limit)
        with path.open("rb") as stream:
            opened.set()
            assert release.wait(10)
            return json.loads(stream.read())
    monkeypatch.setattr(runtime, "_json", held_read)
    script = tmp_path / "control_writer.py"
    script.write_text('''import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("generator", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Path(sys.argv[3]).touch()
module.control_write(Path(sys.argv[2]), {"done": 1})
''', encoding="utf-8")
    process = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        read = pool.submit(runtime._control_json, path)
        try:
            assert opened.wait(5)
            process = subprocess.Popen([legacy_python, "-I", "-B", "-X", "utf8", str(script),
                str(runtime.GENERATOR), str(path), str(tmp_path / "attempt")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 5
            while not (tmp_path / "attempt").exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert (tmp_path / "attempt").exists()
            # lock保持中にchildが完了すれば親readとatomic置換が排他されていない。
            with pytest.raises(subprocess.TimeoutExpired):
                process.wait(timeout=.2)
        finally:
            release.set()
            if process is not None:
                stdout, stderr = process.communicate(timeout=10)
        assert read.result(timeout=5) == {"done": 0}
    assert process.returncode == 0, (stdout, stderr)
    assert runtime._control_json(path) == {"done": 1}


def test_control_io_does_not_hide_other_failures(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("storage denied")
    monkeypatch.setattr(ip, "write_json", denied)
    with pytest.raises(PermissionError, match="storage denied"):
        runtime._write_control_json(tmp_path / "request.json", {})
    (tmp_path / "progress.json").write_bytes(b"invalid-json")
    with pytest.raises(json.JSONDecodeError):
        runtime._control_json(tmp_path / "progress.json")
