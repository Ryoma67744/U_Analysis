"""★ ver77.0: Pythonが出したParquetをR Arrowで読み、実際の列役割helperで再入力する。"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services.parquet_column_roles import ROLE_KEY, export_metadata


def _rscript():
    executable = os.environ.get("RSCRIPT") or shutil.which("Rscript")
    if not executable:
        candidate = Path("C:/Program Files/R/R-4.4.2/bin/Rscript.exe")
        executable = str(candidate) if candidate.is_file() else None
    if not executable:
        pytest.skip("Rscriptが必要です（R Arrow CIジョブで実行）")
    probe = subprocess.run([executable, "-e", 'quit(status=if(requireNamespace("arrow",quietly=TRUE)&&requireNamespace("jsonlite",quietly=TRUE))0 else 42)'],
                           capture_output=True, text=True, timeout=60)
    if probe.returncode == 42:
        pytest.skip("R arrow/jsonliteが必要です（R Arrow CIジョブで実行）")
    assert probe.returncode == 0, probe.stderr
    return executable


def test_actual_r_arrow_reader_respects_column_roles_and_rejects_invalid_metadata(tmp_path):
    executable = _rscript()
    frame = pa.table({"id": [1, 2], "x": [0., 1.], "y": [0., 0.], "500.123456": [4., 8.],
                      "UMAP_1": [40., 80.], "RPCA__UMAP_2": [400., 800.], "PCA": [0, 1]})
    valid = tmp_path / "valid.parquet"
    invalid = tmp_path / "invalid.parquet"
    legacy = tmp_path / "legacy.parquet"
    pq.write_table(frame.replace_schema_metadata(export_metadata(frame.schema.names)), valid)
    pq.write_table(frame.replace_schema_metadata({ROLE_KEY: b'{"schema_version":1,"columns":[]}'}), invalid)
    pq.write_table(frame, legacy)
    helper = Path(__file__).resolve().parents[1] / "Script" / "helpers" / "parquet_column_roles.R"
    script = tmp_path / "check.R"
    script.write_text('''args <- commandArgs(trailingOnly=TRUE)
source(args[[1]])
read_schema <- function(path) arrow::read_parquet(path, as_data_frame=FALSE)$schema
features <- ua_parquet_feature_columns(read_schema(args[[2]]))
stopifnot(identical(features, "500.123456"))
stopifnot(is.null(ua_parquet_feature_columns(read_schema(args[[4]]))))
failed <- tryCatch({ua_parquet_feature_columns(read_schema(args[[3]])); FALSE}, error=function(e) TRUE)
stopifnot(failed)
cat(jsonlite::toJSON(list(features=features, values=arrow::read_parquet(args[[2]], col_select=features)[[1]]), auto_unbox=TRUE))
''', encoding="utf-8")
    completed = subprocess.run([executable, str(script), str(helper), str(valid), str(invalid), str(legacy)],
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result == {"features": "500.123456", "values": [4, 8]}
