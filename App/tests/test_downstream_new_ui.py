from pathlib import Path

from app.callbacks import analysis_callbacks as ac
from app.callbacks import preflight_callbacks as pc


def test_new_condition_run_never_reuses_existing_output(tmp_path):
    first = ac._resolve_full_output_dir(str(tmp_path), "study", downstream=True,
        execution_mode="downstream_new", umap_nn=10, umap_md=.3, umap_dims=20)
    Path(first).mkdir()
    second = ac._resolve_full_output_dir(str(tmp_path), "study", downstream=True,
        execution_mode="downstream_new", umap_nn=10, umap_md=.3, umap_dims=20)
    assert first != second
    assert "reanalyzed" in second and "nn10" in second and "dim20" in second
    assert not Path(second).exists()


def test_source_comes_from_reduction_ref_even_when_viewer_uses_other_run(monkeypatch):
    sub = {"last_result_dir": "/old/view", "result_refs": {
        "reduction": {"path": "/new/preflight"}, "analysis": {"path": "/old/view"}}}
    monkeypatch.setattr(pc, "get_sub_project", lambda *_: sub)
    assert pc._resolve_result_dir({"id": "p"}, "s") == "/new/preflight"


def test_downstream_does_not_require_current_screen_raw_folder(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.project_manager.get_sub_project", lambda *_: {
        "result_refs": {"reduction": {"path": str(tmp_path)}}})
    monkeypatch.setattr("app.services.result_catalog.resume_result_paths", lambda p: [str(tmp_path / "x.rds")])
    assert ac._collect_downstream_errors(str(tmp_path / "new"), {"id": "p"}, "s") == ([], [])
    assert ac._collect_downstream_errors(str(tmp_path), None, None)[0]


def test_new_button_obeys_global_analysis_busy_guard():
    assert "btn_run_downstream_new" in ac._START_BUTTON_IDS
