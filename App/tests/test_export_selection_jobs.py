"""★ ver75.1: 選択出力の結果境界と非同期開始時の固定を検証する。"""
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from app.callbacks import interactive_data_export as de
from app.services.export_selection import build_catalog
from app.services import export_progress as ep


@pytest.fixture
def context(tmp_path, monkeypatch):
    root = tmp_path / "result"
    (root / "RDS_Files").mkdir(parents=True)
    rds = root / "RDS_Files" / "PCA.rds"
    rds.write_bytes(b"fixture")
    frame = pd.DataFrame({
        "Sample": ["Ctrl", "KO1", "KO2"], "group": ["Ctrl", "KO1", "KO2"],
        "source_file_id": ["file"] * 3, "source_pixel_id": ["1", "2", "3"],
        "section_id": ["s1", "s2", "s3"], "subject_id": ["c", "k1", "k2"],
        "SpatialX": [1., 2., 3.], "SpatialY": [1.] * 3,
        "UMAP_1": [.1, .2, .3], "UMAP_2": [.4, .5, .6], "Cluster": ["0", "1", "2"],
    })
    monkeypatch.setattr(de, "_interactive_data", {"plot_data": frame})
    scientific_frame = frame.copy(deep=True)
    monkeypatch.setattr(de._bridge, "extract_data", lambda *_args, **_kwargs: {
        "plot_data": scientific_frame, "meta": {}, "cache_dir": None})
    rmap = {"PCA": str(rds)}
    catalog = build_catalog({"PCA": frame}, str(root.resolve()))
    selection = {"mode": "selected", "scope": catalog["scope"], "signature": catalog["signature"],
                 "ids": [u["id"] for u in catalog["units"] if u["group"] != "KO1"]}
    return root, rds, rmap, frame, selection


def test_catalog_requires_same_loaded_result(context, tmp_path):
    root, rds, rmap, _, _ = context
    other = tmp_path / "other"; other.mkdir()
    with pytest.raises(ValueError, match="切り替え"):
        de.load_export_catalog(str(rds), rmap, "PCA", ["PCA"], str(other))
    with pytest.raises(ValueError, match="一致"):
        de.load_export_catalog(str(rds), {"PCA": str(other / "PCA.rds")}, "PCA", ["PCA"], str(root))


def test_snapshot_copies_current_frame(context):
    root, rds, rmap, frame, _ = context
    snap = de._selection_snapshot(str(rds), rmap, "PCA", ["PCA"], str(root))
    original = snap["frames"]["PCA"].copy(deep=True)
    frame.loc[0, "group"] = "Changed"
    pd.testing.assert_frame_equal(snap["frames"]["PCA"], original)


@pytest.mark.parametrize("invalid", ["empty", "stale", "unknown"])
def test_bad_selection_never_publishes_output(context, monkeypatch, invalid):
    root, rds, rmap, _, selection = context
    if invalid == "empty": selection["ids"] = []
    elif invalid == "stale": selection["signature"] = "stale"
    else: selection["ids"] = ["unknown"]
    class InlineThread:
        def __init__(self, target, args, daemon): self.target, self.args = target, args
        def start(self): self.target(*self.args)
    monkeypatch.setattr(de.threading, "Thread", InlineThread if invalid != "empty"
                        else lambda **_: pytest.fail("空選択でスレッドを開始した"))
    result = de.data_export_start(1, str(root), "TIMS", "csv", rmap, "PCA", str(root),
                                  "p", "s", str(rds), {}, ["PCA"], False, None, selection)
    job = ep.get_job(result[5]["job"])
    ep.pop_job(result[5]["job"])
    assert job["status"] == "error" and job["msg"]


def test_job_selection_and_frames_survive_later_ui_edits(context, monkeypatch, tmp_path):
    root, rds, rmap, frame, selection = context
    pending, seen = [], {}
    class DeferredThread:
        def __init__(self, target, args, daemon): self.target, self.args = target, args
        def start(self): pending.append(self)
    monkeypatch.setattr(de.threading, "Thread", DeferredThread)
    from app import config
    monkeypatch.setattr(config, "DATA_EXPORT_TMP_DIR", tmp_path / "downloads")
    def run(*args, selection=None, snapshot=None, **kwargs):
        seen.update(selection=deepcopy(selection), snapshot=snapshot, args=args)
        output = Path(kwargs["out_dir"]) / (kwargs["prefix"] + "result.csv")
        output.write_text("fixture\n")
        return output, "result.csv", "done"
    monkeypatch.setattr(de, "_do_export", run)
    before = frame.copy(deep=True)
    original_selection = deepcopy(selection)
    result = de.data_export_start(1, str(root), "TIMS", "csv", rmap, "PCA", str(root),
                                  "p", "s", str(rds), {}, ["PCA"], False, None, selection)
    # ★ ver77.0: 受付時に巨大RDSをhash/extractせず、workerに固定済み設定を渡す。
    assert pending[0].args[-1]["pending"] is True
    selection["ids"].clear(); frame.loc[:, "group"] = "Changed"; rmap.clear()
    pending[0].target(*pending[0].args)
    assert seen["selection"] == original_selection
    pd.testing.assert_frame_equal(seen["snapshot"]["frames"]["PCA"], before)
    assert seen["args"][3] == {"PCA": str(rds)}
    assert ep.get_job(result[5]["job"])["status"] == "done"
    ep.pop_job(result[5]["job"])


def test_metadata_union_normalizes_pixel_ids_without_losing_legacy_rows():
    a = pd.DataFrame({"source_file_id": ["f"], "source_pixel_id": ["1"], "group": ["Ctrl"]})
    b = a.copy(); b["source_pixel_id"] = [1.0]
    assert len(de._selection_metadata({"PCA": a, "Harmony": b})) == 1
    legacy = pd.DataFrame({"source_file_id": ["", ""], "source_pixel_id": ["", ""],
                           "Sample": ["Ctrl", "KO2"], "SpatialX": [1, 1], "SpatialY": [2, 2]})
    assert len(de._selection_metadata({"PCA": legacy})) == 2


def test_legacy_union_keeps_first_method_umap_after_coordinate_rounding():
    first = pd.DataFrame({"Sample": ["Ctrl"], "SpatialX": [1.0], "SpatialY": [2.0],
                          "UMAP_1": [10.0], "UMAP_2": [20.0]})
    second = first.copy(); second["SpatialX"] = [1.00001]; second["UMAP_1"] = [99.0]
    combined = de._selection_metadata({"PCA": first, "Harmony": second})
    assert len(combined) == 1 and combined.iloc[0]["UMAP_1"] == 10.0


def test_derived_pca_scope_is_limited_to_its_parent_harmony(context, monkeypatch, tmp_path):
    import hashlib
    from app import config
    from app.callbacks.interactive_callbacks import _DERIVE_PCA_VERSION
    root, harmony, _, _, _ = context
    cache = tmp_path / "cache"
    monkeypatch.setattr(config, "SEURAT_CACHE_DIR", cache)
    digest = hashlib.md5(f"{harmony}|{_DERIVE_PCA_VERSION}".encode()).hexdigest()[:16]
    derived = cache / "derived_pca" / f"{digest}_pca_uncorrected.rds"
    derived.parent.mkdir(parents=True); derived.write_bytes(b"derived")
    rmap = {"Harmony": str(harmony), "PCA": str(derived)}
    assert de._export_scope(str(harmony), rmap, ["Harmony", "PCA"], str(root)) == str(root)
    assert de._export_scope(str(derived), rmap, ["PCA"], str(root / "RDS_Files")) == str(root)
    snap = de._selection_snapshot(str(derived), rmap, "PCA", ["PCA"], str(root))
    assert snap["metadata_rds"] == str(harmony)
    wrong = derived.with_name("other_pca.rds"); wrong.write_bytes(b"other")
    with pytest.raises(ValueError, match="一致"):
        de._export_scope(str(harmony), {**rmap, "PCA": str(wrong)}, ["PCA"], str(root))


@pytest.mark.parametrize("coordinate_method", ["PCA", "Harmony"])
def test_selected_export_keeps_per_method_coordinates_and_union_clusters(
        context, monkeypatch, tmp_path, coordinate_method):
    """★ ver75.1: 手法間の不足画素を別UMAPで補わず、クラスタは和集合で出す。"""
    import json
    from collections import OrderedDict

    root, rds, _, base, _ = context
    other_method = "Harmony" if coordinate_method == "PCA" else "PCA"
    first = base.iloc[[0, 1]].copy()
    first["TotalCount"] = [100., 200.]
    first["nFeature"] = [10., 20.]
    second = base.iloc[[0, 2]].copy()
    second["UMAP_1"] = [99., 88.]
    second["UMAP_2"] = [-99., -88.]
    second["TotalCount"] = [900., 800.]
    second["nFeature"] = [90., 80.]
    second["Cluster"] = ["9", "8"]
    frames = OrderedDict([(coordinate_method, first), (other_method, second)])
    before = {method: frame.copy(deep=True) for method, frame in frames.items()}
    scope = str(root.resolve())
    catalog = build_catalog(frames, scope)
    selection = {"mode": "selected", "scope": scope, "signature": catalog["signature"],
                 "ids": [u["id"] for u in catalog["units"] if u["group"] != "KO1"]}

    data = tmp_path / "raw"
    data.mkdir()
    source = data / "sample.parquet"
    pd.DataFrame({"id": [1, 2, 3], "x": [1., 2., 3.], "y": [1., 1., 1.],
                  "100.000000": [11., 22., 33.], "annotation": ["Ctrl", "KO1", "KO2"]
                 }).to_parquet(source, index=False)
    manifest = {"files": [{"file_id": "file", "path": str(source),
                            "runtime_path": str(source), "selection_mode": "all"}]}
    (root / "section_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    snapshot = {"scope": scope, "frames": frames,
                "cluster_maps": {method: {} for method in frames}, "metadata_rds": str(rds)}
    monkeypatch.setattr(de, "_build_region_lookup", lambda *_: ({}, []))
    options = {"categories": ["id", "coords", "intensity", "section", "umap", "quality", "cluster"]}

    # _do_exportから実CSV writerと来歴保存まで通す。lookupの引数だけの検査にしない。
    path, filename, message = de._do_export(
        str(data), "TIMS", "csv", {method: str(rds) for method in frames}, coordinate_method,
        str(root), None, None, str(rds), selected_methods=list(frames), options=options,
        out_dir=tmp_path / "exports", selection=selection, snapshot=snapshot)
    assert path is not None, message
    assert filename == "UMAP_cluster_TIMS_selected.csv"
    out = pd.read_csv(path)
    assert out["id"].tolist() == [1, 3]
    assert out["group"].tolist() == ["Ctrl", "KO2"]
    assert out["100.000000"].tolist() == [11., 33.]
    first_columns = [f"{coordinate_method}__{c}" for c in ["UMAP_1", "UMAP_2", "TotalCount", "nFeature"]]
    second_columns = [f"{other_method}__{c}" for c in ["UMAP_1", "UMAP_2", "TotalCount", "nFeature"]]
    assert out.loc[0, first_columns].tolist() == [.1, .4, 100., 10.]
    assert out.loc[1, first_columns].isna().all()
    assert out.loc[1, second_columns].tolist() == [88., -88., 800., 80.]
    assert out.loc[0, coordinate_method] == 0
    assert pd.isna(out.loc[1, coordinate_method])
    assert out[other_method].tolist() == [9, 8]

    records = list((root / "provenance").glob("export_*_data_export.json"))
    assert len(records) == 1
    provenance = json.loads(records[0].read_text(encoding="utf-8"))["extra"]
    assert provenance["embedding_columns"] == "per_method"
    assert provenance["export_selection"]["pixels"] == 2
    assert provenance["export_selection"]["method_counts"] == {coordinate_method: 1, other_method: 2}
    for method in frames:
        pd.testing.assert_frame_equal(frames[method], before[method])
