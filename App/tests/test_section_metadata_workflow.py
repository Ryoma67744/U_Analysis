"""切片・ROI・群の対応表と操作の回帰検証。"""
from copy import deepcopy
from app.services.section_metadata import assign_group, build_section_manifest, manifest_group_rows, summarize_manifest, validate_section_manifest


def test_identical_roi_names_are_file_scoped(tmp_path):
    ps = [str(tmp_path / d / 'same.parquet') for d in ('one', 'two')]
    cat = [{'path': p, 'available_rois': ['ROI1', 'ROI2']} for p in ps]
    m = build_section_manifest(cat, {ps[0]: ['ROI1'], ps[1]: ['ROI2']}, {p: 'section' for p in ps})
    assert [f['rois'] for f in m['files']] == [['ROI1'], ['ROI2']]
    assert len({f['file_id'] for f in m['files']}) == 2
    assert len({s['section_id'] for f in m['files'] for s in f['sections']}) == 2
    assert not validate_section_manifest(m)


def test_explicit_empty_never_becomes_all():
    cat = [{'path': '/data/a', 'available_rois': ['A', 'B']}]
    m = build_section_manifest(cat, {'/data/a': []}, {'/data/a': 'section'})
    assert m['files'][0]['selection_mode'] == 'none' and not m['files'][0]['sections']
    assert validate_section_manifest(m)
    assert build_section_manifest(cat, previous=m)['files'][0]['selection_mode'] == 'none'


def test_whole_file_can_clear_and_reselect():
    cat = [{'path': '/data/a', 'available_rois': []}]
    m = build_section_manifest(cat, {'/data/a': []})
    assert m['files'][0]['selection_mode'] == 'none'
    m = build_section_manifest(cat, {'/data/a': ['__all__']}, previous=m)
    assert m['files'][0]['selection_mode'] == 'all' and len(m['files'][0]['sections']) == 1


def test_region_keeps_parent_unit():
    cat = [{'path': '/data/a', 'available_rois': ['brain', 'liver']}]
    a = build_section_manifest(cat, roles={'/data/a': 'region'})
    b = build_section_manifest(cat, roles={'/data/a': 'section'})
    assert len(a['files'][0]['sections']) == 1 and len(b['files'][0]['sections']) == 2
    row = a['files'][0]['sections'][0]
    assert row['section_id'] == row['integration_unit_id']


def test_unknown_roi_role_requires_choice_and_is_saved():
    cat = [{'path': '/data/a', 'available_rois': ['A', 'B']}]
    assert any('ROI' in e for e in validate_section_manifest(build_section_manifest(cat)))
    m = build_section_manifest(cat, roles={'/data/a': 'section'})
    assert not validate_section_manifest(build_section_manifest(cat, previous=m))


def test_six_sections_three_controls_and_three_comparison():
    cat = [{'path': f'/data/{i}', 'available_rois': []} for i in range(6)]
    original = build_section_manifest(cat)
    rows, _ = assign_group(manifest_group_rows(original), [0, 1, 2], 'Ctrl')
    rows, _ = assign_group(rows, [3, 4, 5], '比較群')
    for i, r in enumerate(rows):
        r['subject_id'] = ('C' if i < 3 else 'T') + str(i % 3 + 1)
    m = build_section_manifest(cat, group_rows=rows, previous=original)
    line, groups, errors = summarize_manifest(m)
    assert '6切片' in line and 'PCA・Harmony・RPCA' in line
    assert 'Ctrl：3切片・独立試料3例' in groups and '比較群：3切片・独立試料3例' in groups
    assert not errors
    assert [s['integration_unit_id'] for f in m['files'] for s in f['sections']] == [s['integration_unit_id'] for f in original['files'] for s in f['sections']]


def test_bulk_assign_requires_selection_and_does_not_mutate_input():
    rows = [{'section_id': 'x', 'subject_id': 'C1', 'group': ''}]
    before = deepcopy(rows)
    updated, msg = assign_group(rows, [], 'Ctrl')
    assert updated == rows == before and '行を選択' in msg
    updated, _ = assign_group(rows, [0], 'Ctrl')
    assert updated[0]['group'] == 'Ctrl' and rows == before


def test_subject_cannot_belong_to_two_groups():
    cat = [{'path': f'/data/{i}', 'available_rois': []} for i in range(2)]
    m = build_section_manifest(cat)
    rows = manifest_group_rows(m)
    for i, row in enumerate(rows):
        row.update(subject_id='mouse1', group=['Ctrl', 'KO'][i])
    assert any('複数の群' in e for e in validate_section_manifest(build_section_manifest(cat, group_rows=rows, previous=m)))


def test_callback_body_updates_groups_and_empty_selection():
    from app.callbacks.section_callbacks import update_section_state
    cat, ids = [{'path': '/data/a', 'available_rois': ['A', 'B']}], [{'index': '/data/a'}]
    rows, m, _, msg = update_section_state(cat, [['A', 'B']], ['section'], [], ids, ids, None, [0, 1], 'Ctrl', True)
    assert len(rows) == 2 and '2' in msg and all(s['group'] == 'Ctrl' for s in m['files'][0]['sections'])
    rows, m, _, _ = update_section_state(cat, [[]], ['section'], rows, ids, ids, m)
    assert not rows and m['files'][0]['selection_mode'] == 'none'


def test_registration_survives_deselecting_roi_and_file():
    cat = [{'path': '/data/a', 'available_rois': ['A', 'B']}]
    m = build_section_manifest(cat, roles={'/data/a': 'section'})
    rows = manifest_group_rows(m); rows[0].update(subject_id='C1', group='Ctrl')
    m = build_section_manifest(cat, group_rows=rows, previous=m)
    m = build_section_manifest(cat, {'/data/a': ['B']}, previous=m)
    m = build_section_manifest(cat, {'/data/a': ['A', 'B']}, previous=m)
    assert m['files'][0]['sections'][0]['group'] == 'Ctrl'
    m = build_section_manifest([], previous=m)
    m = build_section_manifest(cat, previous=m)
    assert m['files'][0]['roi_role'] == 'section' and m['files'][0]['sections'][0]['subject_id'] == 'C1'


def test_optional_ion_controls_only_when_used():
    from app.callbacks.section_callbacks import toggle_optional_molecule_settings, toggle_optional_reanalysis_molecule_settings
    h = {'display': 'none'}
    assert toggle_optional_molecule_settings([], False) == (h,) * 4
    assert toggle_optional_molecule_settings([], True) == ({}, h, h, h)
    assert toggle_optional_molecule_settings(['db'], False) == ({},) * 4
    assert toggle_optional_reanalysis_molecule_settings([], False) == (h,) * 3
    assert toggle_optional_reanalysis_molecule_settings([], True) == ({}, h, h)


def test_all_named_rois_do_not_include_unlabelled_pixels():
    m = build_section_manifest([{'path': '/data/a', 'available_rois': ['A', 'B']}], roles={'/data/a': 'section'})
    assert m['files'][0]['selection_mode'] == 'selected' and m['files'][0]['rois'] == ['A', 'B']
