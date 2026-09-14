"""実 Seurat オブジェクトで測定値アッセイのマーカー方向を検証する。"""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_desi_export_tests_measured_spatial_values(tmp_path):
    rscript=shutil.which("Rscript")
    if not rscript:pytest.skip("Rscript が必要です")
    source=Path(__file__).resolve().parents[1]/"Script"/"DESI"/"260623_DESI-UMAP_Template_v16.R"
    code=r'''
suppressPackageStartupMessages(library(Seurat))
suppressPackageStartupMessages(library(dplyr))
suppressPackageStartupMessages(library(ggplot2))
args<-commandArgs(trailingOnly=TRUE)
for(expr in parse(args[[1]])) {
  if(is.call(expr)&&identical(expr[[1]],as.name("<-"))&&
     identical(expr[[2]],as.name(".desi_export_result"))) eval(expr)
}
stopifnot(exists(".desi_export_result"))
set.seed(42)
counts<-matrix(rpois(12*60,3)+1,nrow=12)
counts[1,1:30]<-counts[1,1:30]+30
counts[2,31:60]<-counts[2,31:60]+30
rownames(counts)<-paste0("mz-",100:111)
colnames(counts)<-paste0("cell",seq_len(ncol(counts)))
obj<-CreateSeuratObject(counts,assay="Spatial")
obj<-NormalizeData(obj,verbose=FALSE)
obj$sample<-rep(c("one","two"),each=30)
obj$seurat_clusters<-factor(rep(c("0","1"),each=30))
Idents(obj)<-obj$seurat_clusters
obj[["umap"]]<-CreateDimReducObject(embeddings=matrix(rnorm(120),ncol=2,
 dimnames=list(colnames(obj),c("UMAP_1","UMAP_2"))),key="UMAP_",assay="Spatial")
spatial_before<-LayerData(obj,assay="Spatial",layer="data")
# 補正アッセイを使うと候補の符号が逆転する入力を置く。
obj[["integrated"]]<-CreateAssay5Object(data=-spatial_before)
DefaultAssay(obj)<-"integrated"
od<-args[[2]];mrm_df<-NULL;DB_ANNOTATION_ENABLED<-FALSE;UMAP_SEED<-42L
.assign_cluster_colors<-function(...) c("0"="grey","1"="red")
plot_umap_cluster_variants<-plot_umap_per_sample<-export_cluster_highlights<-function(...) invisible(NULL)
safe_ggsave<-run_volcano_and_msi<-function(...) invisible(NULL)
.floor_zero_padj<-.deg_for_export<-function(x)x
match_mrm_compound<-function(x,...)x
apply_feature_naming_policy<-function(x,...)x
# 図保存のみ省略し、検定は実 Seurat の FindAllMarkers を実行する。
actual_marker_calls<-0L
FindAllMarkers<-function(object,...) {
 actual_marker_calls<<-actual_marker_calls+1L
 stopifnot(DefaultAssay(object)=="Spatial")
 Seurat::FindAllMarkers(object,...)
}
.desi_export_result(obj,"RPCA",file.path(od,"test.rds"))
markers<-read.csv(file.path(od,"RPCA","analysis_deg_all_markers_rpca.csv"))
positive<-subset(markers,gene=="mz-100"&cluster=="0")
negative<-subset(markers,gene=="mz-100"&cluster=="1")
stopifnot(actual_marker_calls==1L,nrow(positive)==1L,positive$avg_log2FC>1,positive$p_val<1e-6)
stopifnot(nrow(negative)==1L,negative$avg_log2FC< -1,negative$p_val<1e-6)
stopifnot(identical(LayerData(obj,assay="Spatial",layer="data"),spatial_before))
cat("Spatial-marker-runtime: passed\n")
'''
    check=tmp_path/"check.R";check.write_text(code,encoding="utf-8")
    r=subprocess.run([rscript,str(check),str(source),str(tmp_path)],
                     text=True,capture_output=True,timeout=90)
    assert r.returncode==0,r.stdout+r.stderr
    assert "Spatial-marker-runtime: passed" in r.stdout
