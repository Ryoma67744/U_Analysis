"""群編集・表示・実出力で解析値と元画素の対応を維持する。"""
import json
from pathlib import Path
from collections import OrderedDict
import pandas as pd
import pytest
from app.services import section_group_metadata as gm
from app.services.section_metadata import build_section_manifest

@pytest.fixture
def result(tmp_path):
    files = [{"path": str(tmp_path / f"folder{i}" / "same.parquet"), "available_rois": ["ROI1"], "roi_role": "section"} for i in range(6)]
    manifest = build_section_manifest(files)
    records = []
    for i, f in enumerate(manifest["files"]):
        section = f["sections"][0]
        section.update(subject_id=f"ID{i}", group="Ctrl" if i < 3 else "Treatment")
        path = Path(f["path"]); path.parent.mkdir()
        pd.DataFrame({"id": [1, 2], "x": [1, 2], "y": [1, 1], "500.1": [10.+i, 20.+i], "annotation": ["same_ROI1"]*2}).to_parquet(path)
        for pixel in (1, 2):
            records.append({"CellID": f"cell{i}_{pixel}", "Sample": "same_ROI1", "source_file_id": f["file_id"], "source_pixel_id": str(pixel), **section,
                "SpatialX": pixel, "SpatialY": 1, "UMAP_1": i+.12, "UMAP_2": pixel+.42, "Cluster": str(pixel), "500.1": i+pixel+.53})
    rds = tmp_path / "RDS_Files" / "PCA.rds"; rds.parent.mkdir(); rds.write_bytes(b"original seurat bytes")
    params = tmp_path / "analysis_params.json"; params.write_text(json.dumps({"section_manifest": manifest}), encoding="utf8")
    return rds, manifest, pd.DataFrame(records), params


def test_counts_and_empty_filter(result):
    _, _, df, _ = result
    assert [(x["sections"], x["subjects"]) for x in gm.group_summary(df)] == [(3, 3), (3, 3)]
    df.loc[df.subject_id == "ID1", "subject_id"] = "ID0"
    assert gm.group_summary(df)[0]["subjects"] == 2
    assert gm.group_summary(df)[0]["sections"] == 3
    assert gm.filter_groups(df, None) is df
    assert gm.filter_groups(df, []).empty
    assert len(gm.filter_groups(df, ["Ctrl"])) == 6


def test_edits_preserve_numeric_values_ids_and_original_receipt(result):
    rds, _, df, params = result
    before, params_before = df.copy(deep=True), params.read_bytes()
    rows = gm.group_rows(df); rows[0]["group"] = "Renamed"
    saved = gm.save_group_edits(rds, rows); updated = gm.overlay_result_metadata(df, rds)
    assert updated.iloc[0]["group"] == "Renamed"
    pd.testing.assert_frame_equal(before, df)
    pd.testing.assert_frame_equal(before.drop(columns=["group", "subject_id"]), updated.drop(columns=["group", "subject_id"]))
    assert rds.read_bytes() == b"original seurat bytes" and params.read_bytes() == params_before
    assert gm.load_result_manifest(rds.parent / "Harmony.rds") == saved


def test_invalid_edits_rejected(result):
    rds, _, df, _ = result
    rows = gm.group_rows(df); rows[0]["section_id"] = "bogus"
    with pytest.raises(ValueError, match="対応"): gm.save_group_edits(rds, rows)
    rows = gm.group_rows(df); rows[3]["subject_id"] = rows[0]["subject_id"]
    with pytest.raises(ValueError, match="複数の群"): gm.save_group_edits(rds, rows)
    assert not (rds.parent.parent / "section_manifest.json").exists()


def test_source_matching_keeps_order_and_intensity(result):
    rds, manifest, df, _ = result
    raw = pd.DataFrame({"id": [2.0, 1.0, 3.0], "500.1": [20., 10., 30.]}); before = raw.copy(deep=True)
    out = gm.attach_input_metadata(raw, manifest["files"][4]["path"], rds, df)
    assert out["subject_id"].tolist() == ["ID4", "ID4", ""]
    pd.testing.assert_frame_equal(raw, before)
    assert out["500.1"].tolist() == before["500.1"].tolist()
    assert set(gm.METADATA_COLUMNS).issubset(gm.export_metadata_frame(df).columns)


def test_zero_prefixed_ids_remain_distinct():
    assert gm.normalize_pixel_id("001") != gm.normalize_pixel_id("1")
    assert gm.normalize_pixel_id("1.0") == gm.normalize_pixel_id("1")
    assert gm.normalize_pixel_id("1e5") == gm.normalize_pixel_id(100000.0)


def test_metadata_not_classified_as_intensity():
    from app.services import export_options as eo
    cols = ["id", "x", "500.1", *gm.METADATA_COLUMNS]
    assert set(gm.METADATA_COLUMNS).issubset(eo.select_output_columns(cols, {"categories": ["section"]}, []))
    assert not set(gm.METADATA_COLUMNS).intersection(eo.select_output_columns(cols, {"categories": ["intensity"]}, []))


def test_last_settings_roundtrip(result, tmp_path, monkeypatch):
    from app.services import session_manager as sm
    _, manifest, _, _ = result
    monkeypatch.setattr(sm, "SESSIONS_DIR", tmp_path); monkeypatch.setattr(sm, "_LAST_SETTINGS_FILE", tmp_path / "settings.json")
    settings = {"section_manifest": manifest, "section_manifest_reanalysis": manifest, "execution_policy": "section_auto_v1", "use_annotation_check": [], "reanalysis_use_annotation_check": [], "ion_mode": "Negative"}
    sm.save_last_settings(settings)
    assert all(sm.load_last_settings()[k] == v for k, v in settings.items())


def test_callbacks_save_and_export(result, monkeypatch):
    from app.callbacks import interactive_section_groups as cb
    rds, _, df, _ = result
    monkeypatch.setattr(cb, "_data", lambda _path: gm.overlay_result_metadata(df, rds))
    rows, _, _, groups, _ = cb.load_section_groups(str(rds), 0)
    assert len(rows) == 6 and groups == ["Ctrl", "Treatment"]
    rows[0]["group"] = "Changed"
    msg, trigger = cb.save_section_groups(1, rows, str(rds), 0)
    assert "保存しました" in msg and trigger == 1
    output = cb.export_section_metadata(1, str(rds), ["Changed"])
    assert "Changed" in output["content"] and "Treatment" not in output["content"]


def test_group_figure_uses_group_names(result):
    from app.callbacks.interactive_umap import _build_umap_integrated_fig
    _, _, df, _ = result
    fig = _build_umap_integrated_fig(df, "group", None, True, False, cluster_name_map={"Ctrl": "wrong alias"})
    assert "Ctrl" in [t.name for t in fig.data] and "wrong alias" not in [t.name for t in fig.data]


def test_actual_csv_all_folders_and_source_clusters(result, tmp_path):
    from app.callbacks.interactive_data_export import _build_cluster_lookup, _export_tims
    rds, manifest, df, _ = result
    fid = manifest["files"][4]["file_id"]; df.loc[df.source_file_id == fid, "Cluster"] = ["40", "41"]
    path, _ = _export_tims(str(Path(manifest["files"][0]["path"]).parent), OrderedDict(PCA=_build_cluster_lookup(df)), "csv", out_dir=tmp_path, metadata_rds=rds, metadata_plot=df)
    written = pd.read_csv(path); selected = written.loc[written.source_file_id == fid]
    assert len(written) == 12 and written["source_file_id"].nunique() == 6
    assert selected["UMAP cluster"].tolist() == [40, 41]
    assert selected["subject_id"].tolist() == ["ID4", "ID4"]
    assert selected["500.1"].tolist() == [14., 24.]


def test_missing_selected_input_and_conflicting_clusters_rejected(result):
    from app.callbacks.interactive_data_export import _build_cluster_lookup
    rds, manifest, df, _ = result
    Path(manifest["files"][1]["path"]).unlink()
    with pytest.raises(ValueError, match="入力が見つかりません"): gm.selected_input_paths(rds, {".parquet"})
    with pytest.raises(ValueError, match="異なるクラスタ"):
        _build_cluster_lookup(pd.concat([df, df.iloc[[0]].assign(Cluster="conflict")]))



def test_source_matching_reads_ids_even_when_identifier_output_disabled(result, tmp_path):
    from app.callbacks.interactive_data_export import _build_cluster_lookup, _export_tims
    rds, manifest, df, _ = result
    fid = manifest["files"][4]["file_id"]
    df.loc[df.source_file_id == fid, "Cluster"] = ["40", "41"]
    path, _ = _export_tims(str(Path(manifest["files"][0]["path"]).parent),
        OrderedDict(PCA=_build_cluster_lookup(df)), "csv", out_dir=tmp_path,
        metadata_rds=rds, metadata_plot=df, options={"categories": ["section", "cluster"]})
    table = pd.read_csv(path)
    assert "id" not in table.columns and "500.1" not in table.columns
    assert table.loc[table.source_file_id == fid, "UMAP cluster"].tolist() == [40, 41]


def test_same_basename_hne_and_optional_values_follow_source_pixels(result, tmp_path, monkeypatch):
    from app.callbacks import interactive_data_export as exp
    rds, manifest, df, _ = result
    df = df.copy()
    # Neither raw filename nor ROI annotation equals the disambiguated H&E key.
    df["Sample"] = "same_ROI1 [" + df["source_file_id"] + "]"
    df["TotalCount"] = df["UMAP_1"] + 100
    df["nFeature"] = df["source_pixel_id"].astype(int) + 3
    regions = {}
    for i, item in enumerate(manifest["files"]):
        sample = df.loc[df.source_file_id == item["file_id"], "Sample"].iloc[0]
        regions[sample] = {
            "landmarks": {"hne": [[0, 0], [10, 0], [0, 10]], "tic": [[0, 0], [10, 0], [0, 10]]},
            "polygons": [{"name": f"region{i}", "vertices": [[0, 0], [10, 0], [10, 10], [0, 10]]}],
        }
        raw = pd.read_parquet(item["path"])
        raw = pd.concat([raw, raw.iloc[[0]].assign(id=3, x=3)], ignore_index=True)
        raw.to_parquet(item["path"])
    monkeypatch.setattr(exp.hp, "load_hne_sample", lambda _path, sample: regions[sample])
    roi, failed = exp._build_region_lookup(df, rds)
    assert not failed
    options = {"categories": ["section", "cluster", "umap", "quality", "roi"]}
    extras = exp._build_extra_lookups(df, options)
    report = []
    out, _ = exp._export_tims(str(Path(manifest["files"][0]["path"]).parent),
        OrderedDict(PCA=exp._build_cluster_lookup(df)), "csv", roi,
        out_dir=tmp_path, metadata_rds=rds, metadata_plot=df, options=options,
        extra_lookups=extras, exclude_unused=True, report=report)
    table = pd.read_csv(out)
    assert len(table) == 12 and "id" not in table.columns
    assert all(row["resolver"] == "source-id" and row["matched"] == row["rows"] == 2 for row in report)
    for i, item in enumerate(manifest["files"]):
        rows = table.loc[table.source_file_id == item["file_id"]]
        assert rows["領域名"].tolist() == [f"region{i}"] * 2
        assert rows["UMAP_1"].tolist() == [i + .12] * 2
        assert rows["TotalCount"].tolist() == [i + 100.12] * 2
        assert rows["nFeature"].tolist() == [4, 5]
