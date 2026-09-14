"""選択入力・部分競合・明示的 DB 有効化を実際の表で検証する。"""
import json
import pandas as pd
from app.services.naming_policy import (copy_selected_feature_annotations,
    find_annotation_sidecars, resolve_feature_annotations, compose_annotation_map,
    db_annotation_enabled, apply_annotation_map, load_naming_settings)
from app.services.seurat_bridge import SeuratBridge
from app.utils.annotation_label import feature_display_label


def _input(folder, name, rows):
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / (name + ".parquet")
    source.touch()
    pd.DataFrame(rows, columns=["mz", "compound", "adduct"]).to_parquet(folder / (name + "_feature_annotations.parquet"), index=False)
    return source


def test_only_selected_files_and_same_basename_provenance(tmp_path):
    a = _input(tmp_path / "a", "sample", [(100., "A", "[M+H]+"), (200., "B", "[M+H]+")])
    b = _input(tmp_path / "b", "sample", [(100., "A", "[M+H]+"), (200., "C", "[M+H]+")])
    _input(tmp_path / "a", "unselected", [(100., "WRONG", "[M+H]+")])
    out = tmp_path / "out"
    copy_selected_feature_annotations([a, b], out)
    paths = find_annotation_sidecars(out / "RDS_Files" / "data.rds")
    assert len(paths) == 2 and paths[0].name != paths[1].name
    records = resolve_feature_annotations(paths, ["100.000", "200.000"])
    assert records["100.000"]["compound"] == "A"
    assert records["200.000"]["status"] == "conflict"
    assert {r["compound"] for r in records["200.000"]["candidates"]} == {"B", "C"}
    assert all(r["source_file"] for r in records["200.000"]["candidates"])
    assert set(pd.read_csv(out / "feature_annotation_sources.csv")["source_file"]) == {str(a), str(b)}


def test_db_fills_only_unannotated_and_never_resolves_scils_conflict():
    features = ["100.000", "200.000", "300.000", "400.000"]
    records = {features[0]: {"compound": "SCiLS"}, features[1]: {"status": "conflict"}}
    mapping = compose_annotation_map(features, records, dict.fromkeys(features, "DB"))
    assert mapping == {features[0]: "SCiLS", features[2]: "DB", features[3]: "DB"}
    assert len([feature for feature, name in mapping.items() if name == "DB"]) == 2
    rows = [{"gene": f, "annotation": "OLD", "avg_log2FC": 2.5} for f in features]
    apply_annotation_map(rows, mapping)
    assert [r["annotation"] for r in rows] == ["SCiLS", "200.000", "DB", "DB"]
    assert all(r["avg_log2FC"] == 2.5 for r in rows)
    assert feature_display_label(features[1], feature_annotations=records, annotation_map={features[1]: "DB"}) == features[1]


def test_path_and_string_false_do_not_enable_db_and_negative_is_preserved(tmp_path):
    for settings in ({"annotation_csv": "/exists.csv"}, {"annotation_enable": False}, {"annotation_enable": "false"}, {"annotation_enable": False, "db_annotation_enabled": True}):
        assert not db_annotation_enabled(settings)
    assert db_annotation_enabled({"annotation_enable": True})
    (tmp_path / "analysis_params.json").write_text(json.dumps({"annotation_enable": False, "ion_mode": "Negative"}))
    settings = load_naming_settings(tmp_path / "RDS_Files" / "data.rds")
    assert settings["ion_mode"] == "Negative" and not db_annotation_enabled(settings)


def test_manifest_empty_selection_does_not_rediscover_old_sidecars(tmp_path):
    _input(tmp_path, "old", [(100., "OLD", "[M+H]+")])
    copy_selected_feature_annotations([], tmp_path)
    assert find_annotation_sidecars(tmp_path / "data.rds") == []


def test_added_and_removed_sources_invalidate_names_cache(tmp_path):
    a = _input(tmp_path / "input", "a", [(100., "A", "[M+H]+")])
    b = _input(tmp_path / "input", "b", [(100., "B", "[M+H]+")])
    out = tmp_path / "out"
    copy_selected_feature_annotations([a], out)
    bridge = SeuratBridge()
    rds = out / "data.rds"
    cache = tmp_path / "cache"
    assert bridge._load_feature_annotations(cache, rds, ["100.000"])["100.000"]["compound"] == "A"
    copy_selected_feature_annotations([a, b], out)
    assert bridge._load_feature_annotations(cache, rds, ["100.000"])["100.000"]["status"] == "conflict"
    copy_selected_feature_annotations([a], out)
    assert bridge._load_feature_annotations(cache, rds, ["100.000"])["100.000"]["compound"] == "A"


def test_adduct_conflict_is_not_merged_and_legacy_feature_id_kept(tmp_path):
    a = _input(tmp_path, "a", [(100., "Compound", "[M+H]+"), (100., "Compound", "[M+Na]+")])
    records = resolve_feature_annotations([a.with_name("a_feature_annotations.parquet")], ["100.000", "Legacy_200.000 | DB"])
    assert records["100.000"]["status"] == "conflict"
    assert records["Legacy_200.000 | DB"]["compound"] == "Legacy"


def test_stale_failed_rds_is_not_listed_as_a_completed_method(tmp_path):
    from app.callbacks.interactive_callbacks import _detect_integration_methods
    for filename in ("Step2_PCA_uncorrected.rds", "Step2_HarmonyPCA_Result.rds", "Step3_RPCA_Result.rds"):
        (tmp_path / filename).touch()
    (tmp_path / "analysis_methods.json").write_text(json.dumps({"methods": {
        "pca": {"status": "complete", "stage": "downstream"}, "harmony": {"status": "failed"}, "rpca": {"status": "skipped"}}}))
    result = _detect_integration_methods(str(tmp_path), include_derived=True)
    assert len(result) == 1 and next(iter(result)).startswith("PCA")
