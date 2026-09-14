"""再解析へ送るボタンが表示中のRDS・切片・群を取り違えない回帰検証。"""
import json
from dash import no_update
from app.services.section_metadata import build_section_manifest, manifest_group_rows
from app.callbacks.section_callbacks import clear_stale_reanalysis_source, reanalysis_source_manifest, source_reanalysis_catalog, update_reanalysis_section_selector


def _source(tmp_path):
    cat = []
    for folder in ('one', 'two'):
        path = tmp_path / folder / 'same.parquet'
        path.parent.mkdir(); path.touch()
        cat.append({'path': str(path), 'available_rois': ['ROI1', 'ROI2'], 'roi_role': 'section'})
    m = build_section_manifest(cat)
    rows = manifest_group_rows(m)
    for i, row in enumerate(rows):
        row.update(subject_id=f'S{i}', group='Ctrl')
    m = build_section_manifest(cat, group_rows=rows, previous=m)
    result = tmp_path / 'result'; result.mkdir()
    (result / 'section_manifest.json').write_text(json.dumps(m))
    rd = result / 'RDS_Files'; rd.mkdir()
    rds = rd / 'Step2_PCA_uncorrected.rds'; rds.touch()
    return m, rds, str(tmp_path / 'one')


def test_send_preserves_exact_pca_and_multifolder_roster(tmp_path, monkeypatch):
    import app.callbacks.interactive_reanalysis_bridge as br
    m, rds, folder = _source(tmp_path)
    monkeypatch.setattr('app.callbacks.interactive_data_export._decide_instrument', lambda *_: ('TIMS', 'テスト'))
    out = br.send_to_reanalysis(1, 'exclude', ['2', '4'], str(rds), 'TIMS', folder, 'PCA (uncorrected)')
    assert out[0] == '2, 4' and out[1] == 'exclude'
    assert out[7] == str(rds) and out[8] == 'pca' and out[9] == folder
    assert out[10]['files'] == m['files'] and out[11]['manifest']['files'] == m['files'] and out[-1] is True
    cat, ui = update_reanalysis_section_selector(['same'], folder, None, 'tims_cluster_filter', out[11], str(rds), out[10])
    assert len(cat) == 2 and len({f['file_id'] for f in cat}) == 2 and ui
    assert {f['path'] for f in cat} == {f['path'] for f in m['files']}


def test_source_does_not_add_rois_absent_in_source_result(tmp_path):
    m, _, _ = _source(tmp_path)
    m['files'][0]['rois'] = ['ROI1']; m['files'][0]['sections'] = m['files'][0]['sections'][:1]
    m['files'][1]['selection_mode'] = 'none'
    cat = source_reanalysis_catalog(m)
    assert len(cat) == 1 and cat[0]['available_rois'] == ['ROI1']
    assert m['files'][0]['available_rois'] == ['ROI1', 'ROI2']


def test_changed_folder_clears_hidden_rds():
    assert clear_stale_reanalysis_source('/new', 'pca', '/old/RDS_Files/Step2_PCA_uncorrected.rds') == ('', None)


def test_changed_method_cannot_be_overridden_by_hidden_pca():
    assert clear_stale_reanalysis_source('/old/RDS_Files', 'rpca', '/old/RDS_Files/Step2_PCA_uncorrected.rds') == ('', no_update)
    assert clear_stale_reanalysis_source('/old', 'pca', '/old/RDS_Files/Step2_PCA_uncorrected.rds') == (no_update, no_update)


def test_source_not_applied_to_unrelated_input_folder(tmp_path):
    m, rds, folder = _source(tmp_path)
    src = {'manifest': m, 'rds_path': str(rds), 'data_folder': folder}
    assert reanalysis_source_manifest(src, str(tmp_path / 'other'), str(rds)) is None
    assert reanalysis_source_manifest(src, folder, str(tmp_path / 'elsewhere' / 's.rds')) is None
    assert reanalysis_source_manifest(src, folder, str(rds)) == m


def test_harmonypca_filename_is_harmony_not_uncorrected_pca():
    path = '/old/RDS_Files/Step2_HarmonyPCA_Result.rds'
    assert clear_stale_reanalysis_source('/old/RDS_Files', 'harmony', path) == (no_update, no_update)
    assert clear_stale_reanalysis_source('/old/RDS_Files', 'pca', path) == ('', no_update)
