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
