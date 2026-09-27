"""★ ver74.0: 保存失敗・DESI列対応・小標本の実行結果を検証する回帰試験。"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

APP = Path(os.environ.get("U_ANALYSIS_APP_ROOT", Path(__file__).resolve().parents[1]))
R = shutil.which("Rscript")


def _r(tmp_path, body):
    if not R:
        pytest.skip("Rscript が必要")
    script = tmp_path / "check.R"
    script.write_text("app <- " + json.dumps(str(APP)) + "\n" + body, encoding="utf-8")
    proc = subprocess.run([R, "--vanilla", str(script)], capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr


@pytest.mark.parametrize("mode", ["rename", "corrupt_write"])
def test_save_failure_preserves_original_and_removes_temp(tmp_path, mode):
    _r(tmp_path, '''
source(file.path(app, "Script/helpers/rds_io.R"))
options(msi.rds_io.has_qs = FALSE, msi.rds_io.has_qs2 = FALSE)
d <- tempfile(); dir.create(d); path <- file.path(d, "old.rds")
saveRDS(list(old = 1:4), path)
before <- tools::md5sum(path)
''' + ('''file.rename <- function(...) FALSE
''' if mode == "rename" else '''saveRDS <- function(object, file, ...) writeLines("corrupt", file)
''') + '''
failed <- inherits(try(save_rds_compact(list(new = 5:9), path, diet = FALSE), silent = TRUE), "try-error")
stopifnot(failed, identical(unname(before), unname(tools::md5sum(path))))
stopifnot(identical(readRDS(path), list(old = 1:4)))
stopifnot(identical(list.files(d, all.files = TRUE, no.. = TRUE), "old.rds"))
''')


def test_backup_false_stops_slim_before_overwrite(tmp_path):
    _r(tmp_path, '''
source(file.path(app, "Script/helpers/rds_io.R"))
exprs <- parse(file.path(app, "Script/helpers/slim_existing_rds.R"))
for (x in exprs) if (is.call(x) && identical(x[[1]], as.name("<-")) &&
  as.character(x[[2]]) %in% c(".parse_args", ".format_bytes", ".format_delta", ".match_any", "main")) eval(x)
d <- tempfile(); dir.create(d); path <- file.path(d, "Step2_test.rds")
saveRDS(list(old = 1:4), path); before <- tools::md5sum(path)
commandArgs <- function(...) c(d, "--backup")
file.copy <- function(...) FALSE
writes <- 0L
save_rds_compact <- function(...) { writes <<- writes + 1L }
status <- main()
stopifnot(identical(status, 2L), writes == 0L, !file.exists(paste0(path, ".bak")))
stopifnot(identical(unname(before), unname(tools::md5sum(path))))
''')


def test_backup_roundtrip_and_shared_seurat_unwrap(tmp_path):
    _r(tmp_path, '''
source(file.path(app, "Script/helpers/rds_io.R"))
d <- tempfile(); dir.create(d); path <- file.path(d, "old.rds")
saveRDS(list(value = 1:10), path); .rds_io_backup_file(path)
stopifnot(identical(readRDS(path), readRDS(paste0(path, ".bak"))))
x <- structure(list(value = 1L), class = "Seurat")
stopifnot(identical(ua_unwrap_seurat(x), x), identical(ua_unwrap_seurat(list(obj = x)), x))
for (bad in list(NULL, list(obj = NULL), list(value = 1), data.frame(obj = 1)))
  stopifnot(inherits(try(ua_unwrap_seurat(bad), silent = TRUE), "try-error"))
''')


def _desi_file(tmp_path, rows):
    header = [[], ["5", "", "", "A_1_1", "", "C_3_3"], ["", "", "", "1", "2", "3"],
              ["", "", "", "100", "200", "300"], ["", "", "", "10", "20", "30"]]
    path = tmp_path / "desi.txt"
    path.write_text("\n".join("\t".join(row) for row in header + rows) + "\n", encoding="utf-8")
    return path


def test_python_desi_blank_header_preserves_column_positions(tmp_path):
    spec = importlib.util.spec_from_file_location("_desi_v74_test", APP / "app/services/desi_header.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    path = _desi_file(tmp_path, [["1", "0", "0", "11", "22", "33"]])
    header = module.read_desi_header(path)
    assert header.feature_names == ["A (100-10)", "200-20", "C (300-30)"]
    assert header.compounds == ["A_1_1", "", "C_3_3"]


@pytest.mark.parametrize("short", [False, True])
def test_r_desi_blank_names_and_trailing_zeros_preserve_values(tmp_path, short):
    path = _desi_file(tmp_path, [["1", "0", "0", "11", "22", "33"],
                                  ["2", "1", "0", "44"] if short else ["2", "1", "0", "44", "55", "66"]])
    _r(tmp_path, '''
suppressPackageStartupMessages(library(Matrix))
helper <- file.path(app, "Script/helpers/desi_input.R")
if (file.exists(helper)) source(helper)
exprs <- parse(file.path(app, "Script/DESI/260623_DESI-UMAP_Template_v16.R"))
for (x in exprs) if (is.call(x) && identical(x[[1]], as.name("<-")) &&
  identical(x[[2]], as.name("read_desi_data"))) eval(x)
''' + f'ans <- read_desi_data({json.dumps(str(path))}, "sample")\n' + '''
stopifnot(identical(rownames(ans$count_matrix), c("A (100-10)", "200-20", "C (300-30)")))
stopifnot(identical(as.numeric(ans$count_matrix[, 1]), c(11, 22, 33)))
''' + ('''stopifnot(identical(as.numeric(ans$count_matrix[, 2]), c(44, 0, 0)))
''' if short else '''stopifnot(identical(as.numeric(ans$count_matrix[, 2]), c(44, 55, 66)))
''') + '''stopifnot(identical(colnames(ans$count_matrix), c("sample_Spot_1", "sample_Spot_2")))
''')


def test_r_desi_internal_missing_intensity_fails(tmp_path):
    path = _desi_file(tmp_path, [["1", "0", "0", "11", "", "33"], ["2", "1", "0", "44", "55", "66"]])
    _r(tmp_path, '''
suppressPackageStartupMessages(library(Matrix))
source(file.path(app, "Script/helpers/desi_input.R"))
''' + f'error <- try(ua_read_desi_data({json.dumps(str(path))}), silent = TRUE)\n' + '''
stopifnot(inherits(error, "try-error"), grepl("途中強度列", as.character(error)))
''')


def test_stability_small_sample_completes_with_actual_seurat(tmp_path):
    _r(tmp_path, '''
suppressPackageStartupMessages(library(Seurat))
set.seed(7)
counts <- matrix(rpois(20L * 12L, 5), nrow = 12L,
                 dimnames = list(paste0("gene", 1:12), paste0("cell", 1:20)))
obj <- CreateSeuratObject(counts)
emb <- matrix(rnorm(20L * 5L), nrow = 20L,
              dimnames = list(colnames(obj), paste0("PC_", 1:5)))
obj[["pca"]] <- CreateDimReducObject(embeddings = emb, key = "PC_", assay = "RNA")
d <- tempfile(); dir.create(d); rds <- file.path(d, "obj.rds"); saveRDS(obj, rds)
args <- c("--vanilla", shQuote(file.path(app, "Script/helpers/stability_diagnostics.R")),
          "--rds", shQuote(rds), "--out", shQuote(d), "--subsample", "0.5", "--seeds", "42,101")
status <- system2(file.path(R.home("bin"), "Rscript"), args)
stopifnot(status == 0L)
labels <- read.csv(file.path(d, "stability_labels.csv"), check.names = FALSE)
stopifnot(nrow(labels) == 20L, !anyNA(labels), identical(labels$CellID, colnames(obj)))
''')
