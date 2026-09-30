from copy import deepcopy
import json

from app.services.project_result_refs import (
    apply_result_reference, describe_result_reference, resolve_result_dir,
    restored_result_patch,
)


def test_preflight_completion_does_not_replace_viewer_raw_folder(monkeypatch, tmp_path):
    from app.services.analysis_finalizer import _link_to_project
    from app.services import project_manager as pm
    old, new = str(tmp_path / "analysis"), str(tmp_path / "preflight")
    state = {"projects": [{"id": "p", "sub_projects": [{"id": "s", "data_folder": "old raw",
        "result_refs": {"analysis": {"path": old}, "reduction": {"path": new}}}]}]}
    _mock_project_storage(monkeypatch, pm, state)
    monkeypatch.setattr("app.services.project_result_refs.describe_result_reference",
                        lambda p: reference(new, ["reduction"]))
    monkeypatch.setattr("app.services.data_manager.has_msi_data", lambda *a: True)
    result = {"errors": []}
    _link_to_project(new, {"project_id": "p", "sub_project_id": "s", "data_folder": "new raw"}, result)
    assert state["projects"][0]["sub_projects"][0]["data_folder"] == "old raw"
    assert not result["errors"]


def _mock_project_storage(monkeypatch, pm, state):
    from threading import RLock
    monkeypatch.setattr(pm, "_projects_lock", RLock())
    monkeypatch.setattr(pm, "_load_all", lambda: deepcopy(state))
    monkeypatch.setattr(pm, "_save_all", lambda value: state.update(deepcopy(value)))
    monkeypatch.setattr(pm, "_write_meta_to_folder", lambda *a, **k: None)


def test_late_completion_keeps_result_and_raw_folder_in_one_transaction(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.services import project_manager as pm
    old, new = str(tmp_path / "old"), str(tmp_path / "new")
    state = {"projects": [{"id": "p", "sub_projects": [{"id": "s"}]}]}
    _mock_project_storage(monkeypatch, pm, state)
    inspected_old, release_old = Event(), Event()
    def describe(path):
        if path == old:
            inspected_old.set()
            assert release_old.wait(5)
            return reference(old, ["analysis", "reduction"], "2026-09-29T10:00:00Z", "old")
        return reference(new, ["analysis", "reduction"])
    monkeypatch.setattr("app.services.project_result_refs.describe_result_reference", describe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        previous = pool.submit(pm.save_sub_project_result_dir, "p", "s", old, data_folder="old raw")
        try:
            assert inspected_old.wait(5)
            assert pm.save_sub_project_result_dir("p", "s", new, data_folder="new raw")
        finally:
            release_old.set()
        assert previous.result(timeout=5)
    sub = state["projects"][0]["sub_projects"][0]
    assert resolve_result_dir(sub, "analysis") == new
    assert sub["data_folder"] == "new raw"
    assert len(sub["result_history"]) == 2


def reference(path, purposes, started="2026-09-30T10:00:00+00:00", run="new"):
    return dict(run_id=run, path=path, purposes=purposes, started_at=started,
                registered_at=started, known_state=True)


def test_preflight_does_not_replace_viewer_analysis():
    old = reference("/old-analysis", ["analysis", "reduction"],
                    "2026-09-29T10:00:00+00:00", "old")
    sub = apply_result_reference({}, old)
    before = deepcopy(sub)
    sub.update(apply_result_reference(sub, reference("/preflight", ["reduction"])))
    assert resolve_result_dir(sub, "analysis") == "/old-analysis"
    assert resolve_result_dir(sub, "reduction") == "/preflight"
    assert sub["last_result_dir"] == "/old-analysis"
    assert before["result_refs"]["reduction"]["path"] == "/old-analysis"


def test_reduction_only_is_not_a_cluster_result():
    sub = apply_result_reference({}, reference("/preflight", ["reduction"]))
    assert resolve_result_dir(sub, "analysis") == ""
    assert resolve_result_dir(sub, "reduction") == "/preflight"


def test_late_old_job_does_not_rewind_either_pointer():
    sub = apply_result_reference({}, reference("/new", ["analysis", "reduction"]))
    sub.update(apply_result_reference(sub, reference(
        "/old", ["analysis", "reduction"], "2026-09-29T01:00:00Z", "old")))
    assert resolve_result_dir(sub, "analysis") == "/new"
    assert resolve_result_dir(sub, "reduction") == "/new"
    assert len(sub["result_history"]) == 2


def test_legacy_viewer_reference_is_preserved_until_inspected():
    sub = {"last_result_dir": "/legacy"}
    sub.update(apply_result_reference(sub, reference("/preflight", ["reduction"])))
    assert resolve_result_dir(sub, "analysis") == "/legacy"
    assert resolve_result_dir(sub, "reduction") == "/preflight"


def test_relocation_does_not_redirect_sibling_run():
    meta = {"this_run_id": "A", "sub_project": {"result_refs": {
        "reduction": {"run_id": "A", "path": "/old/A"},
        "analysis": {"run_id": "B", "path": "/old/B"},
    }}}
    patch = restored_result_patch(meta, "/new/A")
    assert resolve_result_dir(patch, "reduction") == "/new/A"
    assert resolve_result_dir(patch, "analysis") == ""
    assert patch["result_refs"]["analysis"]["path"] == "/old/B"


def test_partial_core_result_remains_available(monkeypatch, tmp_path):
    from app.services import execution_policy
    monkeypatch.setattr(execution_policy, "method_outcome", lambda p: {
        "completed_reductions": ["PCA", "Harmony"], "completed_analyses": ["PCA"],
        "available": ["PCA"], "incomplete": ["Harmony"], "reduction_only": False,
    })
    (tmp_path / "analysis_methods.json").write_text(json.dumps({"run_id": "A"}))
    result = describe_result_reference(tmp_path)
    assert result["run_id"] == "A"
    assert result["purposes"] == ["reduction", "analysis"]
    assert result["incomplete_methods"] == ["Harmony"]


def test_readonly_resolution_never_searches_another_project():
    assert resolve_result_dir({"data_folder": "/same-input"}) == ""
    assert resolve_result_dir({"last_result_dir": "/specific"}) == "/specific"
