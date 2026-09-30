"""★ ver75.1: 表示やクラスタ辞書に依存しない、数値出力の明示画素選択。"""
from copy import deepcopy

import pandas as pd
import pytest

from app.services import export_selection as es


@pytest.fixture
def frames():
    rows = []
    for pixel, group in [(1, "Ctrl"), (2, "Ctrl"), (3, "KO1"), (4, "KO2"), (5, "KO2")]:
        rows.append({"source_file_id": "file-A", "source_pixel_id": str(pixel),
                     "section_id": "section-" + group, "section_display_name": group + "切片",
                     "subject_id": group + "-1", "group": group, "Sample": group,
                     "CellID": "cell" + str(pixel), "SpatialX": pixel, "SpatialY": 1,
                     "Cluster": "0", "UMAP_1": pixel / 10, "UMAP_2": 1.})
    full = pd.DataFrame(rows)
    return {"Harmony": full.iloc[:4].copy(), "RPCA": full.iloc[[0, 3, 4]].copy()}


def _selection(frames, groups=("Ctrl", "KO2"), scope="result-A"):
    catalog = es.build_catalog(frames, scope)
    return {"mode": "selected", "scope": scope, "signature": catalog["signature"],
            "ids": [unit["id"] for unit in catalog["units"] if unit["group"] in groups]}


def test_union_catalog_and_summary_are_json_safe(frames):
    import json
    catalog = es.build_catalog(frames, "result-A")
    assert catalog["pixels"] == 5
    assert catalog["method_counts"] == {"Harmony": 4, "RPCA": 3}
    assert {unit["group"]: unit["pixels"] for unit in catalog["units"]} == {"Ctrl": 2, "KO1": 1, "KO2": 2}
    selected = es.resolve_selection(frames, _selection(frames), "result-A")
    assert selected.summary()["pixels"] == 4
    assert selected.summary()["method_counts"] == {"Harmony": 3, "RPCA": 3}
    assert selected.summary()["groups"] == ["Ctrl", "KO2"]
    json.dumps(selected.summary(), allow_nan=False)


def test_catalog_independent_of_method_and_row_order(frames):
    reverse = {method: frame.iloc[::-1] for method, frame in reversed(list(frames.items()))}
    assert es.build_catalog(frames, "result-A") == es.build_catalog(reverse, "result-A")


def test_pixel_selection_preserves_values_order_index_and_inputs(frames, monkeypatch):
    monkeypatch.setattr(es, "input_source_id", lambda *_: "file-A")
    before = deepcopy(frames)
    selected = es.resolve_selection(frames, _selection(frames), "result-A")
    raw = pd.DataFrame({"id": [5., 3., 1., 99., 2., 4.], "100.0": [50., 30., 10., 990., 20., 40.],
                        "UMAP cluster": [7, 8, 9, 10, 11, 12]}, index=[50, 30, 10, 990, 20, 40])
    old = raw.copy(deep=True)
    result = selected.filter_frame(raw, "sample.parquet", "run.rds")
    pd.testing.assert_frame_equal(result, raw.iloc[[0, 2, 4, 5]])
    pd.testing.assert_frame_equal(raw, old)
    for method in frames:
        pd.testing.assert_frame_equal(frames[method], before[method])


def test_all_is_backward_compatible_without_catalog():
    assert es.resolve_selection(None, None, None) is None
    assert es.resolve_selection(None, {"mode": "all"}, None) is None


@pytest.mark.parametrize("change", ["scope", "signature", "empty", "unknown", "duplicate", "not_list", "bad_mode"])
def test_invalid_selection_never_expands_to_all(frames, change):
    selection = _selection(frames)
    if change in ("scope", "signature"):
        selection[change] = "old"
    elif change == "empty":
        selection["ids"] = []
    elif change == "unknown":
        selection["ids"] = ["unknown"]
    elif change == "duplicate":
        selection["ids"] *= 2
    elif change == "not_list":
        selection["ids"] = "Ctrl"
    else:
        selection["mode"] = []
    with pytest.raises(ValueError):
        es.resolve_selection(frames, selection, "result-A")


@pytest.mark.parametrize("field,value", [("group", "changed"), ("subject_id", "changed"),
                                         ("section_id", "changed"), ("SpatialX", 100.)])
def test_cross_method_identity_conflict_rejected(frames, field, value):
    frames["RPCA"].loc[0, field] = value
    with pytest.raises(ValueError, match="矛盾"):
        es.build_catalog(frames, "result-A")


def test_same_method_duplicate_conflicting_cluster_rejected(frames):
    frames["Harmony"] = pd.concat([frames["Harmony"], frames["Harmony"].iloc[[0]].assign(Cluster="other")])
    with pytest.raises(ValueError, match="重複"):
        es.build_catalog(frames, "result-A")


def test_duplicate_identical_record_does_not_inflate_pixel_count(frames):
    frames["Harmony"] = pd.concat([frames["Harmony"], frames["Harmony"].iloc[[0]]])
    assert es.build_catalog(frames, "result-A")["pixels"] == 5


def test_method_cluster_and_umap_difference_is_expected(frames):
    frames["RPCA"]["Cluster"] = "other"
    frames["RPCA"]["UMAP_1"] = 123.
    assert es.build_catalog(frames, "result-A")["pixels"] == 5


def test_changed_groups_pixels_or_methods_invalidate_selection(frames):
    selection = _selection(frames)
    variants = [deepcopy(frames) for _ in range(3)]
    for frame in variants[0].values():
        frame.loc[frame.group == "Ctrl", "group"] = "renamed"
    variants[1]["RPCA"] = variants[1]["RPCA"].iloc[:-1]
    del variants[2]["RPCA"]
    for changed in variants:
        with pytest.raises(ValueError, match="選び直して"):
            es.resolve_selection(changed, selection, "result-A")


def test_same_pixel_id_in_different_files_is_distinct(frames, monkeypatch):
    extra = frames["Harmony"].iloc[[0]].assign(source_file_id="file-B", section_id="section-B", group="other")
    frames["Harmony"] = pd.concat([frames["Harmony"], extra])
    selected = es.resolve_selection(frames, _selection(frames), "result-A")
    monkeypatch.setattr(es, "input_source_id", lambda path, _, manifest=None: "file-B" if path == "B" else "file-A")
    assert selected.includes_path("A", "result")
    assert not selected.includes_path("B", "result")
    assert selected.filter_frame(pd.DataFrame({"id": [1], "100": [99.]}), "B", "result").empty


def test_source_unresolved_path_missing_or_duplicate_ids_rejected(frames, monkeypatch):
    selected = es.resolve_selection(frames, _selection(frames), "result-A")
    monkeypatch.setattr(es, "input_source_id", lambda *_: None)
    with pytest.raises(ValueError):
        selected.includes_path("unknown", "result")
    with pytest.raises(ValueError):
        selected.filter_frame(pd.DataFrame({"id": [1]}), "unknown", "result")
    monkeypatch.setattr(es, "input_source_id", lambda *_: "file-A")
    for raw in [pd.DataFrame({"x": [1]}), pd.DataFrame({"id": [1, 1.]}), pd.DataFrame({"id": [None]})]:
        with pytest.raises(ValueError):
            selected.filter_frame(raw, "A", "result")


def _legacy():
    return {"Harmony": pd.DataFrame({"Sample": ["Ctrl", "Ctrl", "KO1", "KO2"],
        "CellID": ["c1", "c2", "k1", "k2"], "group": ["Ctrl", "Ctrl", "KO1", "KO2"],
        "SpatialX": [1., 2., 1., 1.], "SpatialY": [1., 1., 1., 1.]})}


def test_legacy_exact_annotation_preserves_unselected_matching_samples():
    frames = _legacy()
    selected = es.resolve_selection(frames, _selection(frames), "result-A")
    raw = pd.DataFrame({"annotation": ["KO1", "KO2", "Ctrl", "Ctrl", "Ctrl"],
                        "x": [1., 1., 2., 99., 1.], "y": 1., "100": range(5)})
    assert selected.includes_path("unresolved_name.csv", None)
    pd.testing.assert_frame_equal(selected.filter_frame(raw, "all.csv", None), raw.iloc[[1, 2, 4]])


def test_legacy_exact_stem_fallback_and_partial_name_rejected():
    frames = _legacy()
    selected = es.resolve_selection(frames, _selection(frames), "result-A")
    raw = pd.DataFrame({"annotation": ["Unannotated", "Unannotated"], "x": [1, 2], "y": [1, 1]})
    pd.testing.assert_frame_equal(selected.filter_frame(raw, "Ctrl.csv", None), raw)
    with pytest.raises(ValueError, match="完全一致"):
        selected.filter_frame(raw, "Ctrl-part.csv", None)


def test_legacy_colliding_input_coordinates_or_files_rejected():
    frames = _legacy()
    raw = pd.DataFrame({"annotation": ["Ctrl"], "x": [1], "y": [1]})
    selected = es.resolve_selection(frames, _selection(frames), "result-A")
    with pytest.raises(ValueError, match="複数の画素"):
        selected.filter_frame(pd.concat([raw, raw]), "first.csv", None)
    selected.filter_frame(raw, "first.csv", None)
    selected.filter_frame(raw, "first.csv", None)
    with pytest.raises(ValueError, match="複数の入力"):
        selected.filter_frame(raw, "second.csv", None)


def test_legacy_catalog_coordinate_collisions_rejected():
    frames = _legacy()
    duplicate = frames["Harmony"].iloc[[0]].assign(CellID="different")
    frames["Harmony"] = pd.concat([frames["Harmony"], duplicate])
    with pytest.raises(ValueError):
        es.build_catalog(frames, "result-A")


def test_legacy_empty_result_does_not_fall_back_to_all():
    frames = _legacy()
    selected = es.resolve_selection(frames, _selection(frames), "result-A")
    raw = pd.DataFrame({"annotation": ["KO1"], "x": [1], "y": [1], "100": [10.]})
    out = selected.filter_frame(raw, "all.csv", None)
    assert out.empty and list(out.columns) == list(raw.columns)


def test_mixed_identity_formats_fail_closed(frames):
    frames["legacy"] = _legacy()["Harmony"]
    with pytest.raises(ValueError, match="混在"):
        es.build_catalog(frames, "result-A")


def test_legacy_duplicate_without_cell_ids_is_still_ambiguous():
    frames = _legacy()
    frames["Harmony"] = pd.concat([frames["Harmony"], frames["Harmony"].iloc[[0]]]).drop(columns="CellID")
    with pytest.raises(ValueError, match="複数の画素"):
        es.build_catalog(frames, "result-A")


def test_legacy_identity_conflict_after_missing_cell_id_is_rejected():
    first = _legacy()["Harmony"].iloc[[0]]
    frames = {"A": first.drop(columns="CellID"), "B": first,
              "C": first.assign(CellID="different")}
    with pytest.raises(ValueError, match="複数の画素"):
        es.build_catalog(frames, "result-A")


def test_legacy_coordinate_rounding_collision_rejected():
    frame = _legacy()["Harmony"].iloc[[0, 1]].drop(columns="CellID")
    frame["SpatialX"] = [1., 1.00001]
    with pytest.raises(ValueError, match="複数の画素"):
        es.build_catalog({"Harmony": frame}, "result-A")


def test_metadata_only_catalog_has_no_intensity_dependency(frames):
    frames = {method: frame.drop(columns=["UMAP_1", "UMAP_2", "Cluster"]) for method, frame in frames.items()}
    result = es.resolve_selection(frames, _selection(frames), "result-A")
    assert result.summary()["pixels"] == 4


def test_missing_selected_source_pixels_rejected_before_publish(frames, monkeypatch):
    monkeypatch.setattr(es, "input_source_id", lambda *_: "file-A")
    selected = es.resolve_selection(frames, _selection(frames), "result-A")
    selected.filter_frame(pd.DataFrame({"id": [1, 2, 4]}), "A", "result")
    with pytest.raises(ValueError, match="1画素"):
        selected.validate_complete()
    selected.filter_frame(pd.DataFrame({"id": [1, 2, 3, 4, 5]}), "A", "result")
    assert selected.validate_complete() is None


def test_missing_legacy_pixels_checked_after_all_files():
    frames = _legacy()
    selected = es.resolve_selection(frames, _selection(frames), "result-A")
    selected.filter_frame(pd.DataFrame({"x": [1, 2], "y": [1, 1]}), "Ctrl.csv", None)
    with pytest.raises(ValueError, match="1画素"):
        selected.validate_complete()
    selected.filter_frame(pd.DataFrame({"x": [1], "y": [1]}), "KO2.csv", None)
    assert selected.validate_complete() is None


def test_zero_prefixed_pixel_ids_not_conflated(frames, monkeypatch):
    monkeypatch.setattr(es, "input_source_id", lambda *_: "file-A")
    frames["Harmony"].loc[0, "source_pixel_id"] = "001"
    frames["RPCA"].loc[0, "source_pixel_id"] = "001"
    selected = es.resolve_selection(frames, _selection(frames), "result-A")
    raw = pd.DataFrame({"id": ["1", "001", "2", "4", "5"], "100": [10, 11, 20, 40, 50]})
    pd.testing.assert_frame_equal(selected.filter_frame(raw, "A", "result"), raw.iloc[1:])
    assert selected.validate_complete() is None


def test_same_source_pixel_in_two_input_paths_rejected(frames, monkeypatch):
    monkeypatch.setattr(es, "input_source_id", lambda *_: "file-A")
    selected = es.resolve_selection(frames, _selection(frames), "result-A")
    raw = pd.DataFrame({"id": [1, 2, 3, 4, 5]})
    selected.filter_frame(raw, "first", "result")
    selected.filter_frame(raw, "first", "result")
    with pytest.raises(ValueError, match="複数の入力"):
        selected.filter_frame(raw, "second", "result")
