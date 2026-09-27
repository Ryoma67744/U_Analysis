"""★ ver74.0: R新規6桁ID・旧ID参照・科学出力・較正保存の精度を固定する。"""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from app.callbacks import analysis_callbacks as AC
from app.callbacks import interactive_calibration as IC
from app.callbacks import interactive_callbacks
from app.callbacks.interactive_data_export import (
    _read_tims_file, _apply_feature_annotation_columns, _tims_header_columns,
)
from app.services.analysis_runner import compute_calibration_coefficients
from app.services.tims_parquet_contract import TimsParquetContractError
from app.utils.deg_utils import extract_mz_numeric


def _csv(path, masses):
    path.write_text("\n".join([
        ",,,,,,", ",,,meta,meta,,", ",,," + ",".join(map(str, masses)) + ",,",
        ",,,,,,", "1,0,0,11,21,10,20", "2,0,0,12,22,11,20",
    ]) + "\n", encoding="utf-8")


def test_transform_csv_export_preserves_six_decimal_feature_ids(tmp_path):
    path = tmp_path / "source.csv"
    _csv(path, [100.000001, 100.000002])
    data = _read_tims_file(str(path))
    assert data.columns.tolist() == ["id", "x", "y", "m/z 100.000001", "m/z 100.000002"]
    assert data["m/z 100.000001"].tolist() == [11, 12]
    assert data["m/z 100.000002"].tolist() == [21, 22]
    assert _tims_header_columns(str(path)) == data.columns.tolist()


def test_transform_csv_export_rejects_real_six_decimal_collision(tmp_path):
    path = tmp_path / "source.csv"
    _csv(path, [100.0000001, 100.0000002])
    with pytest.raises(TimsParquetContractError):
        _read_tims_file(str(path))


@pytest.mark.parametrize("legacy", [False, True])
def test_active_annotation_lookup_keeps_old_ids_and_precise_export_masses(tmp_path, monkeypatch, legacy):
    path = tmp_path / "source.csv"
    _csv(path, [100.000001, 100.000002])
    data = _read_tims_file(str(path))
    ids = ["m/z 100.00000", "m/z 100.00000.1"] if legacy else ["m/z 100.000001", "m/z 100.000002"]
    active = {ids[0]: {"compound": "A"}, ids[1]: {"compound": "B"}}
    original = deepcopy(active)
    monkeypatch.setattr(interactive_callbacks, "_interactive_data", {
        "rds_path": str(tmp_path / "old.rds"), "feature_annotations": active,
        "naming_settings": {"input_paths": [str(path)]},
    })
    output = _apply_feature_annotation_columns(data, str(tmp_path))
    columns = output.columns.tolist()[3:]
    assert columns == ["A_100.000001", "B_100.000002"]
    assert [extract_mz_numeric(col) for col in columns] == [100.000001, 100.000002]
    assert output[columns].values.tolist() == [[11, 21], [12, 22]]
    assert active == original


@pytest.mark.parametrize("interactive", [False, True])
def test_detected_mass_precision_survives_json_and_coefficient_fit(monkeypatch, interactive):
    refs = [100., 200., 300., 400.]
    observed = [100.000001, 200.000002, 300.000003, 400.000004]
    data = pd.DataFrame({f"m/z {mz:.6f}": [10.] for mz in observed})
    monkeypatch.setattr("app.services.data_manager.read_raw_mz_spectrum", lambda *args, **kwargs: data)
    rows = [{"ref_mz": ref, "obs_mz": "", "use": "Yes"} for ref in refs]
    args = [1, rows, 0.01, None, "/data", None, "TIMS"]
    result, _ = IC.auto_detect_int_cal_peaks(*args) if interactive else AC.auto_detect_observed_peaks(*args, "__all__")
    restored = json.loads(json.dumps(result))
    assert [row["obs_mz"] for row in restored] == observed
    fit = compute_calibration_coefficients(restored, "linear", min_peaks=2)
    assert fit is not None
    assert float(np.polyval(fit["coefficients"], 250.)) == pytest.approx(2.5e-6, abs=1e-12)
