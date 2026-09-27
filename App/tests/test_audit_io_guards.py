"""★ ver74.0: 管理資産不変・SCiLS数値拒否・停止状態の実I/O回帰。"""
from pathlib import Path
import importlib.util
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from app.services import parquet_repack, scils_converter


@pytest.mark.parametrize('marker', ['.ua_imzml_assets', 'conversion_complete.json'])
def test_managed_repack_cannot_change_bytes(tmp_path, marker):
    (tmp_path / marker).write_text('{}')
    path = tmp_path / 'revision' / 'sample.parquet'
    path.parent.mkdir()
    pq.write_table(pa.table({'id': range(10), '100.000000': np.arange(10, dtype=np.float32)}), path, row_group_size=2)
    before = path.read_bytes()
    assert parquet_repack.find_targets(tmp_path, ['*'], recursive=True) == []
    result = parquet_repack.repack_file(path, _budget_override=4 * 1024**3)
    assert result.status == 'error'
    assert '管理' in result.reason
    assert path.read_bytes() == before


def test_copied_registration_parquet_not_repacked(tmp_path):
    path = tmp_path / 'copy.parquet'
    table = pa.table({'id': range(5)}).replace_schema_metadata({b'ua_section_registry': b'[]'})
    pq.write_table(table, path, row_group_size=1)
    before = path.read_bytes()
    assert parquet_repack.repack_file(path, _budget_override=4 * 1024**3).status == 'error'
    assert before == path.read_bytes()


def make_scils(folder, masses, values):
    folder.mkdir()
    (folder / 'sample_Intensity.csv').write_text(
        'm/z,' + ','.join(f'Spot {i}' for i in range(1, 8)) + '\n' +
        '\n'.join(str(mass) + ',' + ','.join([str(value)] * 7) for mass, value in zip(masses, values)) + '\n')
    (folder / 'sample_Spot.csv').write_text('Spot index,X,Y\n' + '\n'.join(f'{i},{i},0' for i in range(1, 8)) + '\n')


@pytest.mark.parametrize('masses,values', [
    ([100.0000001, 100.0000002], [1, 2]), ([float('inf')], [1]),
    ([0], [1]), ([-100], [1]), ([100], [float('inf')]), ([100], [float('nan')]), ([100], [1e100]),
])
def test_invalid_scils_input_never_publishes(tmp_path, masses, values):
    folder = tmp_path / 'input'
    make_scils(folder, masses, values)
    output = tmp_path / 'result.parquet'
    output.write_bytes(b'previous-result')
    with pytest.raises((ValueError, OverflowError, RuntimeError)):
        scils_converter.convert_scils_to_parquet(folder, output, organize=False)
    assert output.read_bytes() == b'previous-result'
    assert not list(tmp_path.rglob('*.writing*'))


def test_finite_negative_corrected_scils_intensity_preserved(tmp_path):
    folder = tmp_path / 'input'
    make_scils(folder, [100.0], [-2.5])
    output = tmp_path / 'result.parquet'
    scils_converter.convert_scils_to_parquet(folder, output, organize=False)
    assert pq.read_table(output)['100.000000'].to_pylist() == [-2.5] * 7


def test_stopped_monitor_is_not_success(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location('audit_status', Path(__file__).parents[1] / 'tools/analysis_status_report.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'build_report', lambda args: {'overall': 'stopped'})
    assert module.main(['--json']) == module.EXIT_STOPPED != module.EXIT_OK
