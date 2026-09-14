"""DESI の実行分岐を R で動かし、独立PCA・切片単位・途中再開を検証する。

Seurat演算は小さな代替実装にし、本番Rの分岐・保存順・完了関数自体を実行する。
実データでの数値検証とは区別する。Rscriptが無い環境では明示的にskipする。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESI = ROOT / "Script/DESI/260623_DESI-UMAP_Template_v16.R"
RERUN = ROOT / "Script/DESI/DESI_RDS_ClusterFilter_ver3.R"
HELPERS = ROOT / "Script/helpers"


def make_synthetic_desi_input(base: Path, pixels_per_section=400):
    """実Seurat検証用Waters TXTを作る（6切片、45特徴量、3つの画素集団）。"""
    import numpy as np

    folder = base / "input"
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(872)
    n_features = 45
    rows = []
    for section in range(6):
        for pixel in range(pixels_per_section):
            celltype = pixel % 3
            rate = np.ones(n_features) * 25
            rate[celltype * 12:(celltype + 1) * 12] = 200
            rate *= 1 + 0.025 * section
            counts = rng.poisson(rate) + rng.poisson(8, size=n_features)
            rows.append([str(section * pixels_per_section + pixel + 1),
                         str(pixel % 12), str(pixel // 12), *map(str, counts),
                         f"ROI{section + 1}"])
    header = ["\t\t\t",
              "\t\t\t" + "\t".join(map(str, range(n_features))),
              "\t\t\t" + "\t".join(map(str, range(100, 100 + n_features))),
              "\t\t\t" + "\t".join(map(str, range(40, 40 + n_features)))]
    path = folder / "sample.txt"
    path.write_text("\n".join(header + ["\t".join(row) for row in rows]) + "\n", encoding="utf-8")
    manifest = {"schema_version": 1, "files": [{
        "file_id": "file001", "path": str(path), "selection_mode": "selected",
        "rois": [f"ROI{i}" for i in range(1, 7)], "roi_role": "section",
        "sections": [{"section_id": f"s{i}", "roi": f"ROI{i}", "subject_id": f"S{i}",
                      "group": "Ctrl" if i < 4 else "case", "integration_unit_id": f"s{i}"}
                     for i in range(1, 7)],
    }]}
    (base / "sections.json").write_text(json.dumps(manifest), encoding="utf-8")
    return dict(template_path=str(DESI), data_folder=str(folder), sample_names=["sample"],
                section_manifest=manifest, execution_policy="section_auto_v1",
                pipeline_stage="full", annotation_enable=False,
                umap_n_neighbors=15, umap_dims_n=10, cluster_dims_n=10,
                cluster_k_param=15, cluster_algorithm=1,
                p_thresh=0.05, logfc_thresh=0.25, input_normalized=False)


def _rscript() -> str:
    executable = os.environ.get("U_ANALYSIS_RSCRIPT") or shutil.which("Rscript")
    if not executable:
        pytest.skip("Rscript未導入: Rの実行契約テストは未実施")
    return executable


def _run_r(tmp_path: Path, code: str) -> None:
    script = tmp_path / "verify.R"
    script.write_text(code, encoding="utf-8")
    result = subprocess.run(
        [_rscript(), "--vanilla", str(script)], text=True, capture_output=True,
        cwd=tmp_path, timeout=120,
        env={**os.environ, "DESI_TEMPLATE": str(DESI), "DESI_RERUN": str(RERUN),
             "UA_HELPERS": str(HELPERS)},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_both_desi_sources_parse_in_r(tmp_path):
    _run_r(tmp_path, 'parse(file=Sys.getenv("DESI_TEMPLATE")); parse(file=Sys.getenv("DESI_RERUN"))\n')


def test_file_roi_selection_is_independent_of_split_and_empty_never_means_all(tmp_path):
    _run_r(tmp_path, r'''
source(file.path(Sys.getenv("UA_HELPERS"), "analysis_contract.R"))
src <- readLines(Sys.getenv("DESI_TEMPLATE"), warn=FALSE)
a <- grep("^    \\.entry <- ua_manifest_entry", src)
b <- grep("^    for \\(sub in sub_samples\\)", src)
block <- parse(text=src[a:(b-1L)])
entry <- function(path, mode, rois, role="region") list(path=path, file_id=path,
  selection_mode=mode, rois=as.list(rois), roi_role=role)
file_path <- file.path(getwd(), "a.txt")
sample_name <- "a"; USE_ROI_AS_SAMPLE <- FALSE; ROI_FILTER <- NULL
desi_data <- list(has_roi=TRUE, coordinates=data.frame(ROI=c("ROI1", "ROI2", "ROI1")),
                  count_matrix=matrix(1, 2, 3))
.section_manifest <- list(files=list(entry(file_path, "selected", "ROI1"),
                                   entry(file.path(getwd(), "b.txt"), "none", character())))
eval(block)
stopifnot(length(sub_samples)==1L, identical(sub_samples[[1]]$mask, c(TRUE,FALSE,TRUE)))
seen <- list()
for (fp in c(file_path, file.path(getwd(), "b.txt"))) {
  file_path <- fp
  eval(block)
  seen[[fp]] <- sub_samples
}
stopifnot(length(seen)==1L)
.section_manifest <- NULL
ROI_FILTER <- character()
seen <- FALSE
for (once in 1L) { eval(block); seen <- TRUE }
stopifnot(!seen)
ROI_FILTER <- "ROI2"
eval(block)
stopifnot(identical(sub_samples[[1]]$mask, c(FALSE,TRUE,FALSE)))
''')


_DISPATCH_SETUP = r'''
setClass("MockSeurat", slots=c(meta.data="data.frame", misc="list", reductions="list"))
setMethod("$", "MockSeurat", function(x,name) x@meta.data[[name]])
setReplaceMethod("$", "MockSeurat", function(x,name,value) { x@meta.data[[name]] <- value; x })
setMethod("dim", "MockSeurat", function(x) c(5L,nrow(x@meta.data)))
mk <- function(md) new("MockSeurat", meta.data=md, misc=list(assay="Spatial"), reductions=list())
md <- data.frame(sample=rep("single_file",18L), integration_unit_id=rep(paste0("slice",1:6),each=3L),
                 group=rep(c("Ctrl","case"),each=9L), source_pixel_id=as.character(1:18),
                 row.names=paste0("cell",1:18))
base <- mk(md)
saves <- character(); clusters <- 0L; statuses <- list(); seen_units <- list()
save_rds_compact <- function(obj,path) { saves <<- c(saves,basename(path)); saveRDS(obj,path) }
load_rds_compact <- readRDS
ua_stamp_checkpoint <- function(obj,signature) {obj@misc$analysis_signature <- signature;obj}
ua_checkpoint_matches <- function(obj,signature) identical(obj@misc$analysis_signature,signature)
ua_record_method <- function(outdir,method,status,reason="",stage="",rds_path="") {
 statuses[[method]] <<- list(status=status,stage=stage,reason=reason)
}
ua_prepare_reduction <- function(obj,reduction) {
 obj@meta.data$seurat_clusters <- NULL; obj@reductions <- obj@reductions[reduction];obj
}
`DefaultAssay<-` <- function(obj,value) {obj@misc$assay <- value;obj}
apply_input_norm <- FindVariableFeatures <- function(x) x
.desi_run_pca <- function(obj,features=NULL) {obj@reductions$pca <- matrix(1,ncol(obj),3);obj}
Embeddings <- function(obj,reduction) obj@reductions[[reduction]]
RunHarmony <- function(object,group.by.vars) {
 stopifnot(file.exists(file.path(rds_od,"DESI_Seurat_SingleSample.rds")))
 seen_units$Harmony <<- unique(object@meta.data[[group.by.vars]])
 if (identical(fail_method,"Harmony")) stop("intentional_harmony_failure")
 object@reductions$harmony <- matrix(2,ncol(object),3);object
}
SplitObject <- function(obj,split.by) {
 seen_units$RPCA <<- unique(obj@meta.data[[split.by]])
 lapply(split(obj@meta.data,obj@meta.data[[split.by]]),mk)
}
SelectIntegrationFeatures <- function(object.list) letters[1:5]
FindIntegrationAnchors <- function(object.list,anchor.features,reduction,dims) {
 if (identical(fail_method,"RPCA")) stop("intentional_rpca_failure")
 object.list
}
IntegrateData <- function(anchorset,dims) mk(do.call(rbind,lapply(anchorset,function(x)x@meta.data)))
.desi_cluster <- function(obj,reduction,resolution) {
 clusters <<- clusters+1L
 obj@meta.data$seurat_clusters <- rep(as.character(clusters),ncol(obj))
 obj@misc$cluster_reduction <- reduction
 obj@reductions$umap <- matrix(clusters,ncol(obj),2);obj
}
.desi_export_result <- function(obj,method,rds_path) {
 saved <- readRDS(rds_path)
 stopifnot(identical(saved@meta.data$seurat_clusters,obj@meta.data$seurat_clusters))
 write.csv(obj@meta.data,paste0(rds_path,".markers.csv"),row.names=FALSE)
}
exprs <- parse(file=Sys.getenv("DESI_TEMPLATE"))
for (expr in exprs) {
 if (is.call(expr) && identical(expr[[1]],as.name("<-")) && is.symbol(expr[[2]]) &&
     as.character(expr[[2]]) %in% c(".desi_finish_method",".desi_load_method")) eval(expr)
}
src <- readLines(Sys.getenv("DESI_TEMPLATE"),warn=FALSE)
a <- grep("^pca_filename <-",src); b <- grep("^# ---- Cleanup:",src)
dispatch <- parse(text=src[a:(b-1L)])
od <- getwd(); rds_od <- file.path(od,"RDS_Files"); dir.create(rds_od)
RESUME_DIR_PATH <- rds_od; RESUME_FROM_RDS <- FALSE; ANALYSIS_SIGNATURE <- "same-input-and-settings"
CLUSTER_RESOLUTION_SINGLE <- 0.5; CLUSTER_RESOLUTION_HARMONY <- 0.5; CLUSTER_RESOLUTION_RPCA <- 0.8
BATCH_CORRECTION_ENABLE <- TRUE; .has_single <- FALSE
seu_list <- list(base); .stage_downstream <- FALSE
'''


@pytest.mark.parametrize("fail_method", ["", "Harmony", "RPCA"])
def test_pca_saved_first_and_method_failure_does_not_destroy_other_results(tmp_path, fail_method):
    _run_r(tmp_path, _DISPATCH_SETUP + f'fail_method <- "{fail_method}"\n' + r'''
PIPELINE_STAGE <- "full"
eval(dispatch)
stopifnot(saves[[1]]=="DESI_Seurat_SingleSample.rds")
stopifnot(statuses$PCA$status=="complete",statuses$PCA$stage=="downstream")
stopifnot(identical(sort(seen_units$Harmony),sort(seen_units$RPCA)))
for (method in c("Harmony","RPCA")) {
 if (identical(method,fail_method)) stopifnot(statuses[[method]]$status=="failed",statuses[[method]]$stage=="reduction")
 else stopifnot(statuses[[method]]$status=="complete")
}
pca <- readRDS(file.path(rds_od,"DESI_Seurat_SingleSample.rds"))
stopifnot(identical(pca@meta.data$group,md$group),pca@misc$cluster_reduction=="pca")
if (fail_method=="Harmony") stopifnot(!file.exists(file.path(rds_od,"DESI_SeuratCombined_harmony.rds")))
''')


def test_reduction_only_then_downstream_finishes_all_three_independent_results(tmp_path):
    _run_r(tmp_path, _DISPATCH_SETUP + r'''
fail_method <- ""; PIPELINE_STAGE <- "reduction_only"
eval(dispatch)
stopifnot(clusters==0L,all(vapply(statuses,function(x)x$stage=="reduction",logical(1))))
PIPELINE_STAGE <- "downstream_from_reduction"; .stage_downstream <- TRUE; .has_single <- TRUE
RESUME_FROM_RDS <- TRUE; seu_list <- list()
eval(dispatch)
stopifnot(clusters==3L,all(vapply(statuses,function(x)x$status=="complete" && x$stage=="downstream",logical(1))))
''')


def test_single_integration_unit_skips_both_correction_methods(tmp_path):
    _run_r(tmp_path, _DISPATCH_SETUP + r'''
base@meta.data$integration_unit_id <- "one-section"
seu_list <- list(base); fail_method <- ""; PIPELINE_STAGE <- "full"
eval(dispatch)
stopifnot(statuses$PCA$status=="complete",statuses$Harmony$status=="skipped",statuses$RPCA$status=="skipped")
stopifnot(length(seen_units)==0L)
''')


def test_checkpoint_with_other_input_signature_is_rejected(tmp_path):
    _run_r(tmp_path, _DISPATCH_SETUP + r'''
fail_method <- ""; PIPELINE_STAGE <- "reduction_only"
eval(dispatch)
RESUME_FROM_RDS <- TRUE; ANALYSIS_SIGNATURE <- "different-input"
err <- tryCatch({.desi_load_method("DESI_Seurat_SingleSample.rds","PCA");NULL},error=identity)
stopifnot(inherits(err,"error"))
''')


def test_failure_stage_is_not_inferred_from_stale_rds(tmp_path):
    _run_r(tmp_path, _DISPATCH_SETUP + r'''
fail_method <- "Harmony"; PIPELINE_STAGE <- "full"
saveRDS(base,file.path(rds_od,"DESI_SeuratCombined_harmony.rds"))
eval(dispatch)
stopifnot(statuses$Harmony$status=="failed",statuses$Harmony$stage=="reduction")
''')


def test_rerun_merge_uses_only_completed_method_and_prefers_source_method(tmp_path):
    _run_r(tmp_path, r'''
source(file.path(Sys.getenv("UA_HELPERS"),"analysis_contract.R"))
src <- readLines(Sys.getenv("DESI_RERUN"),warn=FALSE)
a <- grep("^  \\.find_rerun_rds <- function",src)
b <- grep("^  \\.preferred_method <-",src)
eval(parse(text=src[a:(b[1]-1L)]))
rd <- file.path(getwd(),"RDS_Files");dir.create(rd)
pca <- file.path(rd,"DESI_SeuratCombined_PCA_uncorrected.rds")
harmony <- file.path(rd,"DESI_SeuratCombined_harmony.rds")
rpca <- file.path(rd,"DESI_SeuratCombined_RPCA.rds")
file.create(pca,harmony,rpca)
ua_record_method(getwd(),"PCA","complete",stage="downstream",rds_path=pca)
ua_record_method(getwd(),"Harmony","failed",stage="downstream",rds_path=harmony)
ua_record_method(getwd(),"RPCA","complete",stage="downstream",rds_path=rpca)
stopifnot(identical(.find_rerun_rds(getwd(),"pca"),pca))
stopifnot(identical(.find_rerun_rds(getwd(),"harmony"),rpca))
ua_record_method(getwd(),"PCA","complete",stage="reduction",rds_path=pca)
ua_record_method(getwd(),"RPCA","failed",stage="reduction",rds_path=rpca)
stopifnot(is.null(.find_rerun_rds(getwd(),"pca")))
''')


def test_runtime_templates_have_no_machine_specific_drive_defaults():
    """★ ver67.0: 利用者個別の既定ディレクトリを解析テンプレートへ戻さない。"""
    import re

    for path in (DESI, RERUN):
        assert not re.search(r'^\w+\s*<-\s*"[A-Za-z]:[\\/]',
                             path.read_text(encoding="utf-8"), re.MULTILINE)
    assert 'if (!nzchar(output_dir)) stop(' in DESI.read_text(encoding="utf-8")
    assert 'if (!nzchar(RDS_PATH) || !nzchar(ORIGINAL_DATA_FOLDER)) stop(' in RERUN.read_text(encoding="utf-8")
