"""PCA 表示の一本化後も読み込み・出力に補正なし結果の内部名を使う。"""

from copy import deepcopy

import pandas as pd
import pytest

from app.callbacks import hne_overlay_callbacks as hne
from app.callbacks import interactive_data_export as data_export
from app.callbacks import interactive_pptx as pptx_export
from app.callbacks import lite_view_callbacks as lite
from app.services import methods_text


UNCORRECTED = "PCA (uncorrected)"


@pytest.mark.parametrize("selector", [
    pptx_export.update_export_method_options,
    data_export.update_data_export_method_options,
    hne.update_hne_export_method_options,
])
def test_export_selectors_label_pca_but_keep_correct_result_key(selector):
    rds_map = {
        UNCORRECTED: "/results/Step2_PCA_uncorrected.rds",
        "RPCA": "/results/Step3_RPCA_Result.rds",
        "Harmony": "/results/Step2_HarmonyPCA_Result.rds",
        "PCA": "/results/pixel_table_rpca.rds",
    }
    options, selected = selector(rds_map)
    assert options == [
        {"label": "PCA", "value": UNCORRECTED},
        {"label": "RPCA", "value": "RPCA"},
        {"label": "Harmony", "value": "Harmony"},
    ]
    assert selected == [UNCORRECTED, "RPCA", "Harmony"]
    assert rds_map[selected[0]].endswith("Step2_PCA_uncorrected.rds")


@pytest.mark.parametrize("selector", [
    pptx_export.update_export_method_options,
    data_export.update_data_export_method_options,
    hne.update_hne_export_method_options,
])
def test_export_selectors_keep_legacy_standalone_pca(selector):
    assert selector({"PCA": "/results/Step2_PCA_Result.rds"}) == (
        [{"label": "PCA", "value": "PCA"}], ["PCA"])
    assert selector({}) == ([], [])


def _walk_components(component):
    yield component
    if isinstance(component, (tuple, list)):
        for child in component:
            yield from _walk_components(child)
    elif hasattr(component, "children"):
        yield from _walk_components(component.children)


@pytest.mark.parametrize("methods", [
    [UNCORRECTED], [UNCORRECTED, "Harmony", "RPCA", "PCA"],
])
def test_lite_header_preserves_selection_and_has_one_pca(methods):
    df = pd.DataFrame({"Sample": ["S1"], "Cluster": ["0"]})
    header = lite._build_header({}, {}, UNCORRECTED, methods, df, df)
    selector = next(c for c in _walk_components(header)
                    if getattr(c, "id", None) == "lv_method_selector")
    assert selector.value == UNCORRECTED
    pca_options = [opt for opt in selector.options if opt["label"] == "PCA"]
    assert pca_options == [{"label": "PCA", "value": UNCORRECTED}]
    assert all(text != UNCORRECTED for text in _walk_components(header)
               if isinstance(text, str))


@pytest.fixture
def lite_results(monkeypatch):
    target = {"project_id": "P", "sub_project_id": "S"}
    rds_map = {
        "Harmony": "/results/Step2_HarmonyPCA_Result.rds",
        UNCORRECTED: "/results/Step2_PCA_uncorrected.rds",
    }
    monkeypatch.setattr(lite, "get_project", lambda _: {"name": "P"})
    monkeypatch.setattr(lite, "get_sub_project", lambda *args: {"output_dir": "/results"})
    monkeypatch.setattr(lite, "_detect_integration_methods", lambda _: rds_map)
    loaded_paths = []
    df = pd.DataFrame({"Sample": ["S1"], "Cluster": ["0"]})

    def extract(path):
        loaded_paths.append(path)
        return {"plot_data": df, "cluster_stats": df,
                "features_list": [], "meta": {}, "cache_dir": "/cache"}

    monkeypatch.setattr(lite._sv_bridge, "extract_data", extract)
    monkeypatch.setattr(lite, "_shared_data_get", lambda _: None)
    monkeypatch.setattr(lite, "_shared_data_put", lambda *args: None)
    monkeypatch.setattr(lite, "_read_lite_display_bundle", lambda *args: {
        key: {} for key in ("_settings", "cluster_name_map", "sample_name_map",
                           "spatial_rotation", "custom_color_map", "saved_positions_all",
                           "umap_display", "spatial_display", "display")
    })
    reports = []
    monkeypatch.setattr(lite, "_build_report_body", lambda **kwargs: reports.append(kwargs) or "report")
    return target, rds_map, loaded_paths, reports


@pytest.mark.parametrize("path", ["initial", "card"])
def test_lite_old_pca_selection_loads_companion_not_harmony(lite_results, path):
    target, rds_map, loaded, reports = lite_results
    if path == "initial":
        _, is_error, _ = lite.initialize_lite_view(target, {"method": "PCA"})
        assert not is_error
        assert reports[0]["integration_method"] == UNCORRECTED
    else:
        assert lite._resolve_lite_data_for_target(target, {"method": "PCA"}) is not None
    assert loaded == [rds_map[UNCORRECTED]]


@pytest.mark.parametrize("path", ["initial", "card"])
def test_lite_missing_explicit_pca_never_loads_harmony(lite_results, path):
    target, rds_map, loaded, _ = lite_results
    del rds_map[UNCORRECTED]
    if path == "initial":
        _, is_error, message = lite.initialize_lite_view(target, {"method": "PCA"})
        assert is_error
        assert "PCA" in message
    else:
        assert lite._resolve_lite_data_for_target(target, {"method": "PCA"}) is None
    assert loaded == []


def test_lite_unspecified_method_retains_harmony_default(lite_results):
    target, rds_map, loaded, _ = lite_results
    _, is_error, _ = lite.initialize_lite_view(target, None)
    assert not is_error
    assert loaded == [rds_map["Harmony"]]


@pytest.mark.parametrize("lang, label", [("ja", "統合手法"), ("en", "Integration method")])
def test_methods_display_name_does_not_rewrite_provenance(lang, label):
    conditions = {"integration_method": UNCORRECTED, "analysis": {}, "warnings": []}
    before = deepcopy(conditions)
    assert dict(methods_text.render_conditions_rows(conditions, lang))[label] == "PCA"
    assert f"| {label} | PCA |" in methods_text.render_methods(conditions, lang)
    assert conditions == before


def test_data_export_cluster_schema_keeps_internal_name():
    assert data_export._tims_cluster_columns({UNCORRECTED: {}, "Harmony": {}}) == [
        UNCORRECTED, "Harmony"]


def test_lite_unspecified_method_uses_visible_pca_when_both_pcas_exist(lite_results):
    target, rds_map, loaded, reports = lite_results
    rds_map.clear()
    rds_map.update({"PCA": "/results/Step2_PCA_Result.rds",
                    UNCORRECTED: "/results/Step2_PCA_uncorrected.rds"})
    _, is_error, _ = lite.initialize_lite_view(target, None)
    assert not is_error
    assert loaded == [rds_map[UNCORRECTED]]
    assert reports[0]["integration_method"] == UNCORRECTED
