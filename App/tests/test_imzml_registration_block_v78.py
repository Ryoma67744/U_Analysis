"""★ ver78.0: 保存精度・再登録の行数と、失敗時のファイル解放を検証する。"""
import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import numpy as np
import pyarrow.parquet as pq
import pytest
from pyimzml.ImzMLWriter import ImzMLWriter

from app.services.imzml_block_policy import deterministic_block_rows
from app.services.imzml_io import import_imzml, ImzMLContractError
from app.services.imzml_registration import build_registration_spec, rewrite_registered_parquet
from app.services.imzml_spatial_layout import inspect_imzml_spatial_layout, default_spatial_sections
from app.services.imzml_validation import checked_import, validate_converted_table
from app.services.input_preparation import InputPreparationError
from .test_imzml_processed_performance import _fixture


def test_float64_source_uses_same_float32_save_profile_for_direct_and_checked_import(tmp_path):
    xml = tmp_path / 'wide.imzML'
    axis = np.arange(400, dtype=np.float64) + 100
    with ImzMLWriter(str(xml), mode='continuous', spec_type='profile',
                     mz_dtype=np.float64, intensity_dtype=np.float64) as writer:
        for i in range(2):
            writer.addSpectrum(axis, np.full(len(axis), i + .25, dtype=np.float64), (i + 1, 1, 1))
    tree = ET.parse(xml)
    for node in tree.iter():
        if node.get('accession') == 'MS:1000511':
            node.set('value', '1')
    tree.write(xml, encoding='utf-8', xml_declaration=True)
    layout = inspect_imzml_spatial_layout(xml)
    sections = default_spatial_sections('wide-test', layout)
    for row in sections:
        row.update(subject_id='Test', group='Test', metadata_confirmed=True)
    registration = build_registration_spec({'path': str(xml), 'file_id': 'wide-test',
        'spatial_layout': layout, 'registered_sections': sections})
    direct, checked = tmp_path / 'direct.parquet', tmp_path / 'checked.parquet'
    import_imzml(xml, direct, registration=registration, memory_budget_mb=1)
    checked_import(xml, checked, registration=registration, memory_budget_mb=1)
    expected = deterministic_block_rows(len(axis), width=4, memory_budget_mb=1)
    assert expected != deterministic_block_rows(len(axis), width=8, memory_budget_mb=1)
    for path in (direct, checked):
        manifest = json.loads(path.with_suffix('.imzml.json').read_text(encoding='utf-8'))
        assert manifest['conversion_qc']['parquet_block_rows'] == expected
    assert pq.read_table(direct).equals(pq.read_table(checked))


def test_registration_rewrite_records_actual_profile_and_preserves_values(tmp_path):
    source, registration = _fixture(tmp_path, pixels=3)
    original = tmp_path / 'old.parquet'
    import_imzml(source, original, registration=registration, block_size=1)
    result = tmp_path / 'new.parquet'
    rewrite_registered_parquet(original, original.with_suffix('.imzml.json'), registration, result)
    manifest = json.loads(result.with_suffix('.imzml.json').read_text(encoding='utf-8'))
    assert manifest['conversion_qc']['parquet_block_rows'] == 512
    assert pq.ParquetFile(original).metadata.num_row_groups == 3
    assert pq.ParquetFile(result).metadata.num_row_groups == 1
    assert pq.read_table(original).equals(pq.read_table(result))
    validate_converted_table(result)


def test_rewrite_closes_input_on_schema_failure(tmp_path, monkeypatch):
    source, registration = _fixture(tmp_path, pixels=2)
    original = tmp_path / 'old.parquet'
    import_imzml(source, original, registration=registration)
    table = pq.read_table(original).drop(['annotation'])
    pq.write_table(table, original)
    actual = pq.ParquetFile
    closed = []
    class Tracked:
        def __init__(self, *a, **kw): self.value = actual(*a, **kw)
        def __getattr__(self, key): return getattr(self.value, key)
        def close(self):
            self.value.close()
            closed.append(True)
    monkeypatch.setattr(pq, 'ParquetFile', Tracked)
    with pytest.raises(ImzMLContractError, match='標準列契約'):
        rewrite_registered_parquet(original, original.with_suffix('.imzml.json'),
                                   registration, tmp_path / 'bad.parquet')
    assert closed == [True]


def test_validation_and_rewrite_reject_low_ram_without_modifying_asset(tmp_path, monkeypatch):
    source, registration = _fixture(tmp_path, pixels=2)
    original = tmp_path / 'old.parquet'
    import_imzml(source, original, registration=registration)
    before = original.read_bytes()
    import psutil
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(available=0))
    with pytest.raises(InputPreparationError):
        validate_converted_table(original)
    with pytest.raises(ValueError):
        rewrite_registered_parquet(original, original.with_suffix('.imzml.json'),
                                   registration, tmp_path / 'unstarted.parquet')
    assert original.read_bytes() == before
    assert not (tmp_path / 'unstarted.parquet').exists()


def test_validator_reads_arrow_schema_once_per_open_file(tmp_path, monkeypatch):
    source, registration = _fixture(tmp_path, pixels=2)
    original = tmp_path / 'sample.parquet'
    import_imzml(source, original, registration=registration)
    actual = pq.ParquetFile
    opened = []
    class Tracked:
        def __init__(self, *a, **kw):
            self.value = actual(*a, **kw)
            self.schema_reads = 0
            opened.append(self)
        def __getattr__(self, key): return getattr(self.value, key)
        def __enter__(self): return self
        def __exit__(self, *_): self.value.close()
        @property
        def schema_arrow(self):
            self.schema_reads += 1
            return self.value.schema_arrow
    monkeypatch.setattr(pq, 'ParquetFile', Tracked)
    validate_converted_table(original)
    assert opened and all(reader.schema_reads == 1 for reader in opened)
