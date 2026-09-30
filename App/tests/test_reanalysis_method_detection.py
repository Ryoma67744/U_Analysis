"""再解析ボタンが指定手法の実在クラスタを使うことを検査する。"""
import json
import pytest
from app.callbacks.analysis_callbacks import detect_rds_files


def test_pca_survives_folder_detection_and_explicit_choice(tmp_path):
    rds = tmp_path / 'RDS_Files'
    rds.mkdir()
    for name in ('Step2_PCA_uncorrected.rds', 'Step2_HarmonyPCA_Result.rds', 'Step3_RPCA_Result.rds'):
        (rds / name).write_bytes(b'test')
    _, options, selected, style = detect_rds_files(str(tmp_path), None, 'tims_cluster_filter', 'pca')
    assert {row['value'] for row in options} == {'pca', 'harmony', 'rpca'}
    assert selected == 'pca'
    assert style == {}
    assert detect_rds_files(str(tmp_path), None, 'tims_cluster_filter')[2] == 'rpca'


@pytest.mark.parametrize("from_rds_dir", [False, True])
def test_reduction_only_or_failed_result_cannot_be_cluster_source(tmp_path, from_rds_dir):
    rds = tmp_path / 'RDS_Files'
    rds.mkdir()
    methods = {}
    for key, filename, status, stage in [
        ('pca', 'DESI_SeuratCombined_PCA_uncorrected.rds', 'complete', 'downstream'),
        ('harmony', 'DESI_SeuratCombined_harmony.rds', 'complete', 'reduction'),
        ('rpca', 'DESI_SeuratCombined_RPCA.rds', 'failed', 'downstream'),
    ]:
        path = rds / filename
        path.write_bytes(b'test')
        methods[key] = {'status': status, 'stage': stage, 'rds_path': str(path)}
    (tmp_path / 'analysis_methods.json').write_text(json.dumps({'methods': methods}))
    _, options, selected, _ = detect_rds_files(str(rds if from_rds_dir else tmp_path), 'desi_cluster_filter', 'tims_v8', 'rpca')
    assert options == [{'label': 'PCA', 'value': 'pca'}]
    assert selected == 'pca'


@pytest.mark.parametrize("from_rds_dir", [False, True])
@pytest.mark.parametrize("status", ["complete", "failed"])
def test_v2_completed_cluster_and_umap_remain_available_after_export_failure(tmp_path, monkeypatch, from_rds_dir, status):
    from app.services import result_catalog
    from app.services.seurat_bridge import SeuratBridge
    rds = tmp_path / "RDS_Files"
    rds.mkdir()
    primary = rds / "DESI_SeuratCombined_PCA_uncorrected.rds"
    primary.write_bytes(b"must not be read in UI scan")
    sidecar = rds / "UMAP_pca_umap_embedding.rds"
    sidecar.write_bytes(b"must not be read in UI scan")
    (tmp_path / "analysis_methods.json").write_text(json.dumps({"schema_version": 2, "methods": {
        "pca": {"status": status, "stage": "export", "rds_path": "RDS_Files/" + primary.name,
                "stages": {"reduction": {"status": "complete", "rds_path": "RDS_Files/" + primary.name},
                           "cluster": {"status": "complete", "rds_path": "RDS_Files/" + primary.name},
                           "umap": {"status": "complete", "rds_path": "RDS_Files/" + sidecar.name},
                           "export": {"status": status}}}}}), encoding="utf-8")
    monkeypatch.setattr(result_catalog, "file_sha256", lambda *args: pytest.fail("UI scan hashed an RDS"))
    monkeypatch.setattr(SeuratBridge, "extract_data", lambda *args, **kwargs: pytest.fail("UI scan extracted an RDS"))
    _, options, selected, style = detect_rds_files(str(rds if from_rds_dir else tmp_path),
                                                  "desi_cluster_filter", None, "pca")
    assert options == [{"label": "PCA", "value": "pca"}]
    assert selected == "pca"
    assert style == {}


@pytest.mark.parametrize("missing", ["reduction_only", "umap_failed", "cluster_failed", "missing_sidecar", "other_cluster_rds"])
def test_v2_reanalysis_rejects_incomplete_or_unrelated_cluster_artifacts(tmp_path, missing):
    rds = tmp_path / "RDS_Files"
    rds.mkdir()
    primary = rds / "Step2_HarmonyPCA_Result.rds"
    primary.write_bytes(b"candidate")
    sidecar = rds / "UMAP_harmony_umap_embedding.rds"
    sidecar.write_bytes(b"embedding")
    stages = {"reduction": {"status": "complete", "rds_path": "RDS_Files/" + primary.name},
              "cluster": {"status": "complete", "rds_path": "RDS_Files/" + primary.name},
              "umap": {"status": "complete", "rds_path": "RDS_Files/" + sidecar.name}}
    if missing == "reduction_only":
        stages = {"reduction": stages["reduction"]}
    elif missing == "umap_failed":
        stages["umap"]["status"] = "failed"
    elif missing == "cluster_failed":
        stages["cluster"]["status"] = "failed"
    elif missing == "missing_sidecar":
        stages["umap"]["rds_path"] = "RDS_Files/missing.rds"
    else:
        stages["cluster"]["rds_path"] = "RDS_Files/another.rds"
    (tmp_path / "analysis_methods.json").write_text(json.dumps({"schema_version": 2, "methods": {
        "harmony": {"status": "complete", "stage": "export", "rds_path": "RDS_Files/" + primary.name,
                    "stages": stages}}}), encoding="utf-8")
    _, options, selected, style = detect_rds_files(str(tmp_path), None, "tims_cluster_filter")
    assert options == []
    assert selected is None
    assert style == {"display": "none"}
