"""★ ver74.0: 診断が対象解析・元ファイルID・選択外画素を区別する。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

APP = Path(os.environ.get("U_ANALYSIS_APP_ROOT", Path(__file__).resolve().parents[1]))
SCRIPT = APP / "tools/diag_umap_cluster_export.py"


def _fixture(tmp_path, pixel_ids=(1, 2, 3)):
    source = tmp_path / "renamed_input.parquet"
    pd.DataFrame({"id": list(pixel_ids), "x": list(pixel_ids), "y": 0,
                  "annotation": "same_ROI"}).to_parquet(source)
    ignored = tmp_path / "unselected.parquet"
    rds = tmp_path / "result" / "RDS_Files" / "Step2.rds"
    rds.parent.mkdir(parents=True)
    manifest = {"files": [{"file_id": "original_fid", "path": str(source), "selection_mode": "all"},
                           {"file_id": "not_selected", "path": str(ignored), "selection_mode": "none"}]}
    (rds.parent.parent / "section_manifest.json").write_text(json.dumps(manifest))
    plot = tmp_path / "plot_data.parquet"
    pd.DataFrame({"Sample": ["display-name", "display-name"], "Cluster": ["0", "1"],
                  "SpatialX": [100, 200], "SpatialY": [100, 200],
                  "source_file_id": ["original_fid"] * 2,
                  "source_pixel_id": ["1.0", "2"]}).to_parquet(plot)
    return source, rds, plot


@pytest.mark.parametrize("pixels,code,summary", [((1, 2, 3), 0, "2/2 一致、未一致 0"),
                                                ((1, 3), 4, "1/2 一致、未一致 1"),
                                                ((3, 4), 3, "0/2 一致、未一致 2")])
def test_source_identity_and_expected_analysis_pixels(tmp_path, pixels, code, summary):
    source, rds, plot = _fixture(tmp_path, pixels)
    proc = subprocess.run([sys.executable, str(SCRIPT), "--rds", str(rds), "--plot-data", str(plot),
                           "--instrument", "TIMS"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == code, proc.stdout + proc.stderr
    assert summary in proc.stdout
    assert "source-id" in proc.stdout
    assert "unselected.parquet" not in proc.stdout
    if code == 0:
        assert "解析対象外 1画素" in proc.stdout


def test_requires_explicit_target_instead_of_union_of_caches(tmp_path):
    proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0
    assert "--plot-data" in proc.stderr


def test_legacy_csv_uses_actual_coordinate_export_reader(tmp_path):
    source = tmp_path / "sample.csv"
    pd.DataFrame({"x": [1, 2, 3], "y": [0, 0, 0], "annotation": ["sample", "sample", "unused"]}).to_csv(source, index=False)
    plot = tmp_path / "plot.parquet"
    pd.DataFrame({"Sample": ["sample", "sample"], "Cluster": ["0", "1"],
                  "SpatialX": [1, 2], "SpatialY": [0, 0]}).to_parquet(plot)
    spec = importlib.util.spec_from_file_location("diag_v74", SCRIPT)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    code, result = module.diagnose(pd.read_parquet(plot), str(tmp_path / "old.rds"), [source], "TIMS")
    assert code == 0
    assert result["matched"] == 2
    assert result["files"][0]["outside_analysis"] == 1
