"""実行条件・中断再開・手法別完了の契約を小さな実ファイルで検証する。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.services.execution_policy import (
    AUTO_POLICY, analysis_signature, apply_group_rows, method_outcome,
    prepare_execution_params, selected_manifest_paths,
)
from app.services.section_metadata import build_section_manifest, manifest_group_rows, summarize_manifest, validate_section_manifest


def _params(tmp_path):
    paths = []
    for folder in ('a', 'b'):
        p = tmp_path / 'source' / folder / 'same.parquet'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'fixture')
        paths.append(p)
    cat = [{'path': str(p), 'available_rois': ['ROI1', 'ROI2'], 'roi_role': 'section'} for p in paths]
    manifest = build_section_manifest(cat, {str(paths[0]): ['ROI1'], str(paths[1]): []})
    return dict(execution_policy=AUTO_POLICY, section_manifest=manifest,
                input_normalized=True, norm_mode='log1p', mz_align_ppm=10,
                annotation_enable=False, umap_n_neighbors=15, umap_min_dist=.1,
                data_folder=str(tmp_path / 'source'), input_paths=[str(paths[0])], sample_names=['same'])


def _checkpoint(tmp_path):
    params = _params(tmp_path)
    result = tmp_path / 'first'
    prepare_execution_params(params, result)
    (result / 'analysis_params.json').write_text(json.dumps({'runtime_parameters': params,
        'input_fingerprints': params['input_fingerprints']}))
    rds = result / 'RDS_Files' / 'Step2_PCA_uncorrected.rds'
    rds.parent.mkdir(); rds.write_bytes(b'rds fixture')
    return params, result, rds


def test_saved_file_selection_and_automatic_batch_policy(tmp_path):
    p = _params(tmp_path)
    prepare_execution_params(p, tmp_path / 'out')
    saved = json.loads(Path(p['section_manifest_path']).read_text())
    assert saved['files'][0]['rois'] == ['ROI1']
    assert saved['files'][1]['selection_mode'] == 'none'
    assert p['batch_var'] == p['v13_batch_var'] == 'integration_unit_id'
    assert p['batch_correction_enable'] and p['use_embedded_annotation']
    assert p['db_annotation_enabled'] is False
    assert len(p['input_fingerprints']) == 1


@pytest.mark.parametrize('mode', ['none', 'selected'])
def test_empty_selection_fails_before_creating_manifest(tmp_path, mode):
    p = _params(tmp_path)
    for f in p['section_manifest']['files']:
        f.update(selection_mode=mode, rois=[], sections=[])
    with pytest.raises(ValueError, match='解析対象'):
        prepare_execution_params(p, tmp_path / 'out')
    assert not (tmp_path / 'out' / 'section_manifest.json').exists()


def test_summary_uses_distinct_integration_units():
    m = build_section_manifest([{'path': '/a', 'available_rois': ['A', 'B'], 'roi_role': 'section'}])
    for s in m['files'][0]['sections']:
        s['integration_unit_id'] = 'shared_parent'
    line, _, _ = summarize_manifest(m)
    assert '2切片' in line and line.endswith('PCA') and 'Harmony' not in line


def test_paths_are_canonical_but_same_basenames_are_distinct(tmp_path):
    p = _params(tmp_path)
    m = p['section_manifest']
    first = Path(m['files'][0]['path'])
    m['files'][0]['path'] = str(first.parent / '..' / first.parent.name / first.name)
    assert selected_manifest_paths(m) == [str(first.resolve())]
    duplicate = deepcopy(m['files'][0])
    duplicate['path'] = str(first.resolve())
    m['files'].append(duplicate)
    assert any('重複' in e for e in validate_section_manifest(m))


def test_group_only_edit_preserves_numeric_signature_and_refreshes_registry(tmp_path):
    p = _params(tmp_path)
    prepare_execution_params(p, tmp_path / 'out')
    old = p['analysis_signature']
    rows = manifest_group_rows(p['section_manifest'])
    rows[0].update(group='Ctrl', subject_id='C1')
    p['section_manifest'] = apply_group_rows(p['section_manifest'], rows)
    sid = rows[0]['section_id']
    assert p['section_manifest']['section_settings'][sid]['group'] == 'Ctrl'
    assert analysis_signature(p) == old
    prepare_execution_params(p, tmp_path / 'out')
    assert p['analysis_signature'] == old
    p['umap_n_neighbors'] = 23
    prepare_execution_params(p, tmp_path / 'changed')
    assert p['analysis_signature'] != old


def test_resume_inherits_saved_values_and_latest_group_sidecar(tmp_path):
    saved, result, rds = _checkpoint(tmp_path)
    m = deepcopy(saved['section_manifest'])
    m['files'][0]['sections'][0].update(group='renamed', subject_id='S1')
    (result / 'section_manifest.json').write_text(json.dumps(m))
    current = dict(execution_policy=AUTO_POLICY, resume_from_rds=True,
                   resume_rds_paths=[str(rds)], norm_mode='sqrt', umap_n_neighbors=90,
                   annotation_enable=True, pipeline_stage='downstream_from_reduction')
    prepare_execution_params(current, tmp_path / 'resumed')
    assert current['norm_mode'] == 'log1p' and current['umap_n_neighbors'] == 15
    assert current['annotation_enable'] is False
    assert current['section_manifest']['files'][0]['sections'][0]['group'] == 'renamed'
    assert current['analysis_signature'] == saved['analysis_signature']


def test_resume_does_not_mix_unsaved_current_ui_values(tmp_path):
    _, _, rds = _checkpoint(tmp_path)
    p = dict(resume_from_rds=True, resume_rds_paths=[str(rds)], umap_seed=777,
             cluster_resolution=4.5, calibration_coefficients={'slope': 999})
    prepare_execution_params(p, tmp_path / 'resumed')
    assert 'umap_seed' not in p and 'cluster_resolution' not in p and 'calibration_coefficients' not in p


@pytest.mark.parametrize('operation', ['change', 'remove'])
def test_resume_rejects_changed_or_missing_input(tmp_path, operation):
    saved, _, rds = _checkpoint(tmp_path)
    raw = Path(saved['input_fingerprints'][0]['path'])
    if operation == 'change':
        raw.write_bytes(b'changed input')
    else:
        raw.unlink()
    with pytest.raises(ValueError, match='入力'):
        prepare_execution_params(dict(resume_from_rds=True, resume_rds_paths=[str(rds)]), tmp_path / 'resume')


def test_exact_reanalysis_source_fingerprint_is_checked_at_resume(tmp_path):
    p = _params(tmp_path)
    source = tmp_path / 'source_pca.rds'; source.write_bytes(b'PCA original')
    p.update(rds_path=str(source), cluster_source='pca', filter_mode='exclude', target_clusters=[2])
    out = tmp_path / 'reanalysis'
    prepare_execution_params(p, out)
    (out / 'analysis_params.json').write_text(json.dumps({'runtime_parameters': p}))
    source.write_bytes(b'changed clusters')
    with pytest.raises(ValueError, match='入力'):
        prepare_execution_params(dict(resume_reanalysis=True, resume_reanalysis_dir=str(out)), tmp_path / 'next')


def test_reanalysis_filter_changes_invalidate_numeric_signature(tmp_path):
    p = _params(tmp_path)
    p.update(filter_mode='exclude', target_clusters=[2], cluster_source='pca')
    old = analysis_signature(p)
    p['target_clusters'] = [4]
    assert analysis_signature(p) != old
    p['target_clusters'] = [2]; p['cluster_source'] = 'rpca'
    assert analysis_signature(p) != old


def test_db_off_does_not_require_stale_path_but_on_requires_file(tmp_path):
    p = _params(tmp_path)
    p['annotation_csv_path'] = '/missing/stale.csv'
    prepare_execution_params(p, tmp_path / 'off')
    assert not p['db_annotation_enabled']
    p['annotation_enable'] = True
    with pytest.raises(ValueError, match='DB'):
        prepare_execution_params(p, tmp_path / 'on')


def test_method_outcome_requires_existing_rds_and_nonempty_status(tmp_path):
    p = tmp_path / 'analysis_methods.json'
    p.write_text(json.dumps({'methods': {}}))
    assert method_outcome(tmp_path)['incomplete']
    p.write_text(json.dumps({'methods': {'pca': {'status': 'complete', 'stage': 'full', 'rds_path': '/missing/pca.rds'}}}))
    status = method_outcome(tmp_path)
    assert status['available'] == [] and status['incomplete'] == ['PCA']
    rds = tmp_path / 'pca.rds'; rds.write_bytes(b'rds')
    p.write_text(json.dumps({'methods': {'pca': {'status': 'complete', 'stage': 'reduction', 'rds_path': 'pca.rds'},
                                       'harmony': {'status': 'skipped'}}}))
    status = method_outcome(tmp_path)
    assert status == {'available': ['PCA'], 'incomplete': [], 'reduction_only': True}
    p.write_text(json.dumps({'methods': {'pca': {'status': 'skipped'}}}))
    assert method_outcome(tmp_path)['incomplete']


def test_fresh_preparation_refreshes_input_and_source_rds_fingerprints(tmp_path):
    p = _params(tmp_path)
    source = tmp_path / 'pca.rds'; source.write_bytes(b'pca')
    p['rds_path'] = str(source)
    prepare_execution_params(p, tmp_path / 'one')
    old = p['analysis_signature']
    raw = Path(p['input_fingerprints'][0]['path'])
    raw.write_bytes(b'modified input size')
    source.write_bytes(b'modified source rds')
    prepare_execution_params(p, tmp_path / 'two')
    assert p['input_fingerprints'][0]['size'] == raw.stat().st_size
    assert p['source_rds_fingerprint']['size'] == source.stat().st_size
    assert p['analysis_signature'] != old


def test_legacy_resume_does_not_reuse_current_unrelated_signature(tmp_path):
    saved, result, rds = _checkpoint(tmp_path)
    saved.pop('analysis_signature')
    (result / 'analysis_params.json').write_text(json.dumps({'runtime_parameters': saved}))
    current = dict(resume_from_rds=True, resume_rds_paths=[str(rds)], analysis_signature='wrong-run')
    prepare_execution_params(current, tmp_path / 'next')
    assert current['analysis_signature'] != 'wrong-run'
    assert current['analysis_signature'] == analysis_signature(current)


def test_missing_status_is_incomplete_for_new_policy_only(tmp_path):
    from app.services.execution_policy import method_outcome, AUTO_POLICY
    assert method_outcome(tmp_path) is None
    (tmp_path / "analysis_params.json").write_text(json.dumps({"execution_policy": AUTO_POLICY}))
    result = method_outcome(tmp_path)
    assert result["incomplete"] and not result["available"]


def test_preflight_apply_changes_new_runs_but_continue_uses_saved_conditions(tmp_path):
    """★ ver67.0: ③の転記が新規実行へ効き、④の保存条件と出力名が矛盾しない。"""
    from app.callbacks.preflight_callbacks import apply_preflight_recommendation
    from app.callbacks.analysis_callbacks import _resolve_full_output_dir

    saved, result, rds = _checkpoint(tmp_path)
    saved.update(umap_dims_n=20, umap_metric='cosine')
    prepare_execution_params(saved, result)
    (result / 'analysis_params.json').write_text(json.dumps({'runtime_parameters': saved}))
    applied = apply_preflight_recommendation(1, {'recommended': {
        'n_neighbors': 45, 'dims': 7, 'min_dist': .6, 'metric': 'euclidean'}})
    assert applied == (45, 7, .6, 'euclidean')
    edited = dict(zip(('umap_n_neighbors', 'umap_dims_n', 'umap_min_dist', 'umap_metric'), applied))
    for stage in ('full', 'reduction_only'):
        new = deepcopy(saved)
        new.update(edited, pipeline_stage=stage)
        prepare_execution_params(new, tmp_path / ('new_' + stage))
        assert all(new[key] == value for key, value in edited.items())
    target = _resolve_full_output_dir(str(tmp_path / 'output'), 'saved_nn15_md0p1_dim20',
        downstream=True, umap_nn=applied[0], umap_dims=applied[1], umap_md=applied[2], umap_metric=applied[3])
    continuing = dict(resume_from_rds=True, resume_rds_paths=[str(rds)],
                      pipeline_stage='downstream_from_reduction', **edited)
    prepare_execution_params(continuing, target)
    assert tuple(continuing[k] for k in edited) == (15, 20, .1, 'cosine')
    assert Path(target).name == 'saved_continued'
    assert continuing['analysis_signature'] == saved['analysis_signature']
    assert rds.exists()
