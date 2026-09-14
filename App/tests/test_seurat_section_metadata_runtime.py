"""実 RDS の保存・抽出を経て、切片 ID と群だけが追加されることを検証。"""
from pathlib import Path
import shutil
import subprocess
import pandas as pd
import pytest


@pytest.mark.parametrize("new_policy", [False, True])
def test_extraction_preserves_section_metadata_and_existing_display_keys(tmp_path, new_policy):
    rscript = shutil.which("Rscript")
    if not rscript:
        pytest.skip("Rscript が必要です")
    source = Path(__file__).resolve().parents[1] / "Script" / "helpers" / "extract_seurat_data.R"
    fixture = tmp_path / "fixture.R"
    rds = tmp_path / "object.rds"
    fixture.write_text(r'''
suppressPackageStartupMessages(library(Seurat))
args <- commandArgs(trailingOnly = TRUE)
counts <- matrix(seq_len(24), nrow = 4, dimnames = list(paste0("mz_", 101:104), paste0("cell", 1:6)))
obj <- CreateSeuratObject(counts, assay = "Spatial", project = "legacy_display")
obj <- NormalizeData(obj, verbose = FALSE)
obj$source_file_id <- rep(c("f1", "f2"), each = 3)
obj$source_pixel_id <- rep(c("001", "002", "003"), 2)
obj$section_id <- rep(c("s1", "s2"), each = 3)
obj$subject_id <- rep(c("C1", "T1"), each = 3)
obj$group <- rep(c("Ctrl", "Treat"), each = 3)
obj$integration_unit_id <- obj$section_id
Idents(obj) <- factor(rep(c("0", "1"), each = 3))
obj[["umap"]] <- CreateDimReducObject(embeddings = matrix(seq_len(12), ncol = 2,
  dimnames = list(colnames(obj), c("UMAP_1", "UMAP_2"))), key = "UMAP_", assay = "Spatial")
if (identical(args[2], "new")) {
  obj@misc$analysis_signature <- "section-auto-regression"
  obj$sample <- rep(c("same__f1", "same__f2"), each = 3)
  obj$slice_id <- rep(c("ROI1", "ROI2", "ROI3"), 2)
}
saveRDS(list(obj=obj),args[1])
''')
    subprocess.run([rscript, str(fixture), str(rds), "new" if new_policy else "legacy"], check=True, capture_output=True, text=True)
    output = tmp_path / "extracted"
    result = subprocess.run([rscript, str(source), str(rds), str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    parquet = output / "plot_data.parquet"
    data = pd.read_parquet(parquet) if parquet.exists() else pd.read_csv(output / "plot_data.csv", dtype={"source_pixel_id": str})
    if new_policy:
        assert data["Sample"].nunique() == 6
        assert data.groupby("Sample")["source_file_id"].nunique().max() == 1
        assert all(str(sample).startswith(f"ROI{i % 3 + 1}__") for i, sample in enumerate(data["Sample"]))
    else:
        assert data["Sample"].tolist() == ["legacy_display"] * 6
    assert data["source_pixel_id"].tolist() == ["001", "002", "003"] * 2
    assert data["group"].tolist() == ["Ctrl"] * 3 + ["Treat"] * 3
    assert data["section_id"].tolist() == data["integration_unit_id"].tolist() == ["s1"] * 3 + ["s2"] * 3
    assert data["subject_id"].tolist() == ["C1"] * 3 + ["T1"] * 3
    assert data["source_file_id"].tolist() == ["f1"] * 3 + ["f2"] * 3
    assert data["UMAP_1"].tolist() == list(range(1, 7))
    assert data["Cluster"].astype(str).tolist() == ["0"] * 3 + ["1"] * 3
