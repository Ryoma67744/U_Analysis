"""receipt（解析レシート集約）の単体テスト。"""
import json

from app.services import receipt as rc


PARAMS = {
    "analysis_type": "tims_v8",
    "data_folder": "/data/tims/projA",
    "annotation_csv": "/data/annot.csv",
    "ion_mode": "Negative",
    "tolerance_mz": 0.01,
    "p_thresh": 0.05,
    "logfc_thresh": 0.25,
    "umap_n_neighbors": 30,
    "umap_min_dist": 0.3,
    "umap_metric": "cosine",
    "umap_dims_n": 30,
    "timestamp": "2026-06-30T10:00:00",
    "execution_end_time": "2026-06-30T10:05:00",
    "output_dir": "/out/run1",
}

R_SIDECAR = {
    "r_version": "4.4.1",
    "seed": 42,
    "clustering_algorithm": "dbscan",
    "norm_mode": "log1p",
    "input_normalized": False,
    "batch_correction": "none",
    "package_versions": {"Seurat": "5.1.0", "dbscan": "1.2-0"},
}


def test_build_receipt_merges_r_and_computes_elapsed():
    r = rc.build_receipt(PARAMS, r_sidecar=R_SIDECAR, annotation_sources=["LC-MS/MS", "METASPACE"])
    assert r["receipt_version"] == rc.RECEIPT_VERSION
    assert r["elapsed_seconds"] == 300.0
    assert r["instrument"]["r_version"] == "4.4.1"
    assert r["instrument"]["packages"]["r"]["Seurat"] == "5.1.0"
    assert r["object"]["umap"]["seed"] == 42
    assert r["object"]["clustering"]["algorithm"] == "dbscan"
    assert r["object"]["annotation"]["sources"] == ["LC-MS/MS", "METASPACE"]
    # Python 版が少なくとも python 本体は入る
    assert "python" in r["instrument"]["packages"]["python"]


def test_input_checksums(tmp_path):
    f = tmp_path / "in.csv"
    f.write_text("a,b\n1,2\n")
    r = rc.build_receipt(PARAMS, inputs=[str(f)])
    ent = r["object"]["inputs"][0]
    assert ent["sha256"] and len(ent["sha256"]) == 64
    assert ent["bytes"] == f.stat().st_size


def test_render_markdown_contains_key_sections():
    r = rc.build_receipt(PARAMS, r_sidecar=R_SIDECAR, annotation_sources=["LC-MS/MS"])
    md = rc.render_receipt_markdown(r)
    assert "解析レシート" in md
    assert "UMAP 設定" in md
    assert "アノテーション由来" in md
    assert "LC-MS/MS" in md


def test_write_receipt_creates_both_files(tmp_path):
    r = rc.build_receipt(PARAMS, r_sidecar=R_SIDECAR)
    paths = rc.write_receipt(tmp_path, r)
    j = json.loads((tmp_path / rc.RECEIPT_JSON).read_text(encoding="utf-8"))
    assert j["object"]["analysis_type"] == "tims_v8"
    assert (tmp_path / rc.RECEIPT_MD).exists()
    assert paths["json"].endswith(rc.RECEIPT_JSON)


def test_load_r_sidecar(tmp_path):
    (tmp_path / "analysis_receipt_r.json").write_text(json.dumps(R_SIDECAR), encoding="utf-8")
    loaded = rc.load_r_sidecar(tmp_path)
    assert loaded["seed"] == 42
    assert rc.load_r_sidecar(tmp_path / "missing") == {}


def test_finalize_receipt_end_to_end(tmp_path):
    (tmp_path / "analysis_params.json").write_text(json.dumps(PARAMS), encoding="utf-8")
    (tmp_path / "analysis_receipt_r.json").write_text(json.dumps(R_SIDECAR), encoding="utf-8")
    receipt = rc.finalize_receipt(tmp_path, app_version="ver32.1",
                                  annotation_sources=["LC-MS/MS"])
    assert receipt is not None
    assert receipt["instrument"]["app_version"] == "ver32.1"
    assert receipt["object"]["umap"]["seed"] == 42
    assert (tmp_path / rc.RECEIPT_JSON).exists()
    assert (tmp_path / rc.RECEIPT_MD).exists()


def test_finalize_receipt_missing_params_returns_none(tmp_path):
    assert rc.finalize_receipt(tmp_path) is None
