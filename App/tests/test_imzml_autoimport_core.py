"""ver70.0: 管理境界の実ファイル試験。converter は明示した stand-in（実Parquetではない）。"""
from copy import deepcopy
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
import sys
import subprocess
import time

import pytest
from app.services import input_preparation as ip
from app.services.section_metadata import build_section_manifest, stable_file_id, validate_section_manifest
from app.services.execution_policy import analysis_signature

SPEC = {"schema": 1, "contract": "TEST-DOUBLE-NOT-PARQUET", "block_size": 4, "memory_budget_mb": 16}


def source(tmp_path, stem="sample"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / f"{stem}.imzML"
    p.write_bytes(b"<test-double>not actual imzML</test-double>")
    p.with_suffix(".ibd").write_bytes(b"source bytes")
    return p


def entry(p):
    return build_section_manifest([{"path": str(p), "available_rois": []}])["files"][0]


def fake_convert(xml, output, **kw):
    # 非Parquetの実ファイルを使い、管理層の原子公開・hash・キャッシュだけを検証する。
    output.write_bytes(b"test-only:" + xml.read_bytes() + xml.with_suffix(".ibd").read_bytes())
    output.with_suffix(".imzml.json").write_text(json.dumps({"source": str(xml)}))
    kw["progress"](4, 4)
    return {"parquet": str(output)}


def fake_validate(output, **kw):
    assert output.read_bytes().startswith(b"test-only:")
    return {"test_double": True, "pixels": 4}


def prep(p, cache, **kwargs):
    return ip.prepare_imzml(entry(p), cache_root=cache, spec=SPEC,
        converter=kwargs.pop("converter", fake_convert), validator=fake_validate, **kwargs)


def test_pair_case_and_ambiguity(tmp_path):
    p = source(tmp_path)
    p.with_suffix(".ibd").rename(p.with_suffix(".IBD"))
    assert ip.pair_paths(p)[1].name.endswith(".IBD")
    p.with_suffix(".ibd").write_bytes(b"duplicate")
    assert ip.candidate_info(p)["disabled"]
    with pytest.raises(ip.InputPreparationError):
        ip.pair_paths(p)


def test_missing_binary_visible_but_disabled(tmp_path):
    p = source(tmp_path)
    p.with_suffix(".ibd").unlink()
    info = ip.candidate_info(p)
    assert info["disabled"] and "検出 0 件" in info["reason"]
    assert ip.default_candidates([p]) == []


def test_candidate_read_does_not_hash_or_parse(tmp_path, monkeypatch):
    p = source(tmp_path)
    monkeypatch.setattr(ip, "sha256_file", lambda *a, **kw: pytest.fail("UI must not hash"))
    assert not ip.candidate_info(p)["disabled"]


def test_default_prefers_parquet_and_respects_none(tmp_path):
    p = source(tmp_path)
    q = p.with_suffix(".parquet"); q.touch()
    assert ip.default_candidates([p, q]) == [str(q)]
    assert ip.default_candidates([p, q], []) == []
    assert ip.default_candidates([p, q], [str(p)]) == [str(p)]
    with pytest.raises(ip.InputPreparationError, match="1形式"):
        ip.validate_selected_paths([p, q])


def test_same_stem_different_folders_distinct(tmp_path):
    a, b = source(tmp_path / "a"), source(tmp_path / "b")
    ip.validate_selected_paths([a, b])
    assert stable_file_id(a) != stable_file_id(b)
    ra, rb = prep(a, tmp_path / "cache"), prep(b, tmp_path / "cache")
    assert ra["runtime_path"] != rb["runtime_path"]


def test_atomic_publish_preserves_original_and_reuses(tmp_path):
    p = source(tmp_path / "source")
    original = (p.read_bytes(), p.with_suffix(".ibd").read_bytes())
    calls = []
    def convert(*args, **kw):
        assert not list((tmp_path / "cache").rglob(ip.RECEIPT))
        calls.append(1); return fake_convert(*args, **kw)
    first = prep(p, tmp_path / "cache", converter=convert)
    second = prep(p, tmp_path / "cache", converter=lambda *a, **kw: pytest.fail("should reuse"))
    assert first["file_id"] == second["file_id"] == stable_file_id(p)
    assert first["conversion_key"] == second["conversion_key"]
    assert first["path"] == str(p)
    assert original == (p.read_bytes(), p.with_suffix(".ibd").read_bytes())
    assert len(calls) == 1
    assert ip.validate_asset(first)["state"] == "complete"


@pytest.mark.parametrize("which", ["xml", "ibd"])
def test_each_source_mutation_creates_new_revision(tmp_path, which):
    p = source(tmp_path / "source")
    old = prep(p, tmp_path / "cache")
    changed = p if which == "xml" else p.with_suffix(".ibd")
    changed.write_bytes(changed.read_bytes() + b"!")
    new = prep(p, tmp_path / "cache")
    assert old["conversion_key"] != new["conversion_key"]
    assert old["file_id"] == new["file_id"]
    ip.validate_asset(old)


def test_mutated_during_conversion_no_publish(tmp_path):
    p = source(tmp_path / "source")
    def changed(xml, out, **kw):
        result = fake_convert(xml, out, **kw)
        xml.with_suffix(".ibd").write_bytes(b"changed")
        return result
    with pytest.raises(ip.InputPreparationError, match="変換中"):
        prep(p, tmp_path / "cache", converter=changed)
    assert not list((tmp_path / "cache").rglob(ip.RECEIPT))
    assert not list((tmp_path / "cache").rglob(".stage-*"))


def test_failed_import_no_partial_publication(tmp_path):
    p = source(tmp_path / "source")
    def bad(xml, output, **kw):
        output.write_bytes(b"partial")
        raise RuntimeError("bad source")
    with pytest.raises(RuntimeError):
        prep(p, tmp_path / "cache", converter=bad)
    assert not list((tmp_path / "cache").rglob("*.parquet"))


@pytest.mark.parametrize("remove", [False, True])
def test_pinned_result_survives_original_moved_or_updated(tmp_path, remove):
    p = source(tmp_path / "source")
    old = prep(p, tmp_path / "cache")
    if remove:
        p.rename(p.with_suffix(".moved"));p.with_suffix(".ibd").unlink()
    else:
        p.with_suffix(".ibd").write_bytes(b"changed")
    recovered = ip.prepare_imzml(old, cache_root=tmp_path / "cache", pinned=True,
                                converter=lambda *a, **kw: pytest.fail("must not reconvert"))
    assert recovered == old


def test_corrupt_cache_is_restored_only_if_identical(tmp_path):
    p = source(tmp_path / "source")
    old = prep(p, tmp_path / "cache")
    Path(old["runtime_path"]).write_bytes(b"CORRUPT")
    restored = ip.prepare_imzml(old, cache_root=tmp_path / "cache", pinned=True,
        spec=SPEC, converter=fake_convert, validator=fake_validate)
    assert restored["validation"] == old["validation"]
    assert list((tmp_path / "cache").rglob(".corrupt-*"))


def test_changed_original_cannot_replace_lost_pinned_revision(tmp_path):
    p = source(tmp_path / "source")
    old = prep(p, tmp_path / "cache")
    Path(old["runtime_path"]).unlink()
    p.with_suffix(".ibd").write_bytes(b"changed")
    with pytest.raises(ip.InputPreparationError, match="保存時"):
        ip.prepare_imzml(old, cache_root=tmp_path / "cache", pinned=True,
            spec=SPEC, converter=fake_convert, validator=fake_validate)


def test_different_repair_bytes_not_published(tmp_path):
    p = source(tmp_path / "source"); old = prep(p, tmp_path / "cache")
    Path(old["runtime_path"]).unlink()
    def drift(xml, out, **kw):
        fake_convert(xml, out, **kw);out.write_bytes(out.read_bytes()+b"drift")
    with pytest.raises(ip.InputPreparationError, match="hash"):
        ip.prepare_imzml(old, cache_root=tmp_path / "cache", pinned=True,
            spec=SPEC, converter=drift, validator=fake_validate)
    assert not Path(old["runtime_path"]).exists()


def test_stopped_import_cleans_staging(tmp_path):
    p = source(tmp_path / "source")
    stopped = [False]
    def convert(xml, out, **kw):
        fake_convert(xml, out, **kw);stopped[0] = True
    with pytest.raises(ip.PreparationCancelled):
        prep(p, tmp_path / "cache", converter=convert, cancel=lambda: stopped[0])
    assert not list((tmp_path / "cache").rglob(ip.RECEIPT))


def test_concurrent_same_revision_imports_once(tmp_path):
    p = source(tmp_path / "source");calls = []
    def convert(*a, **kw):
        calls.append(1);time.sleep(.1);return fake_convert(*a, **kw)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(prep, p, tmp_path / "cache", converter=convert) for _ in range(2)]
        a, b = [f.result() for f in futures]
    assert len(calls) == 1 and a["runtime_path"] == b["runtime_path"]


def test_group_edit_preserves_descriptor_and_signature(tmp_path):
    p = source(tmp_path / "source"); e = prep(p, tmp_path / "cache")
    m = {"schema_version": 1, "files": [e]}
    rebuilt = build_section_manifest([{"path": str(p), "available_rois": []}], previous=m)
    assert all(rebuilt["files"][0][k] == e[k] for k in ip.DESCRIPTOR_KEYS if k in e)
    first = analysis_signature({"section_manifest": rebuilt})
    rebuilt["files"][0]["sections"][0].update(group="changed", subject_id="animal1")
    assert first == analysis_signature({"section_manifest": rebuilt})
    rebuilt["files"][0]["conversion_key"] = "newrevision"
    assert first != analysis_signature({"section_manifest": rebuilt})


def test_selected_none_not_prepared_and_zero_selection_rejected(tmp_path):
    a, b = source(tmp_path / "a"), source(tmp_path / "b")
    m = build_section_manifest([{"path": str(p), "available_rois": []} for p in (a, b)], {str(b): []})
    b.unlink()
    result = ip.prepare_inputs({"section_manifest": m}, cache_root=tmp_path / "cache", spec=SPEC,
                               converter=fake_convert, validator=fake_validate)
    assert len(result["input_paths"]) == 1
    for f in m["files"]:
        f["selection_mode"] = "none"
    with pytest.raises(ip.InputPreparationError, match="解析対象"):
        ip.prepare_inputs({"section_manifest": m})


def test_runtime_selection_and_source_id_alias(tmp_path, monkeypatch):
    from app.services import section_group_metadata as gm
    p = source(tmp_path / "source"); e = prep(p, tmp_path / "cache")
    m = {"files": [e]}
    monkeypatch.setattr(gm, "load_result_manifest", lambda *_: m)
    assert gm.input_source_id(str(p), "fake.rds") == e["file_id"]
    assert gm.input_source_id(e["runtime_path"], "fake.rds") == e["file_id"]
    assert gm.selected_input_paths("fake.rds", {".parquet"}) == [e["runtime_path"]]
    Path(e["runtime_path"]).write_bytes(b"broken")
    with pytest.raises(ip.InputPreparationError):
        gm.selected_input_paths("fake.rds", {".parquet"})


def test_gui_candidates_hide_managed_assets(tmp_path):
    from app.services.data_manager import build_tims_input_paths
    p = source(tmp_path / "source"); q = p.with_suffix(".parquet"); q.touch()
    p.with_suffix(".csv").touch()
    assert set(build_tims_input_paths(str(p.parent))) == {str(p), str(q)}
    e = prep(p, tmp_path / "cache")
    assert build_tims_input_paths(str(Path(e["runtime_path"]).parent)) == []


def test_deferred_generation_writes_nothing(tmp_path):
    from app.services.analysis_runner import generate_v8_config
    from app.services.analysis_pipeline import DeferredAnalysis
    p = source(tmp_path / "source")
    params = {"input_paths": [str(p)], "data_folder": str(p.parent), "template_path": "unused"}
    out = tmp_path / "out"
    result = generate_v8_config(params, out)
    assert isinstance(result, DeferredAnalysis) and Path(result).is_file()
    assert not out.exists()


def test_insufficient_disk_before_converter(tmp_path, monkeypatch):
    from types import SimpleNamespace
    p = source(tmp_path / "source")
    monkeypatch.setattr(ip.shutil, "disk_usage", lambda *_: SimpleNamespace(free=1))
    with pytest.raises(ip.InputPreparationError, match="空き容量"):
        prep(p, tmp_path / "cache", converter=lambda *a, **k: pytest.fail("no disk"))


@pytest.mark.parametrize("metadata, expected", [
    (b"[100.000001, 101.0]", [100.000001, 101.0]),
    (b"100.000001,101.0", [100.000001, 101.0]),
    (b"[NaN,101.0]", None), (b"[101.0,100.0]", None),
    (b"[100.0]", None), (b"[true,101.0]", None), (b"[99,101]", None),
])
def test_precise_mz_json_legacy_and_invalid_metadata(metadata, expected):
    from app.services.data_manager import _read_mz_sorted_metadata
    from types import SimpleNamespace
    pf = SimpleNamespace(schema_arrow=SimpleNamespace(metadata={b"mz_sorted": metadata},
                           names=["id", "x", "y", "100.000001", "101.000000", "annotation"]))
    assert _read_mz_sorted_metadata(pf) == expected
