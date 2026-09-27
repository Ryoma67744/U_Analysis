"""ver74のR契約を実R/Seurat/Arrowで検証する（依存なしは明示skip）。"""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "Script/helpers"
TIMS = ROOT / "Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R"
R = os.environ.get("RSCRIPT") or shutil.which("Rscript")


def run_r(tmp_path, body, packages=()):
    if not R:
        pytest.skip("Rscriptが必要です")
    if packages:
        check = subprocess.run([R, "--vanilla", "-e", "quit(status=if(all(vapply(c(" +
            ",".join(json.dumps(p) for p in packages) +
            "),requireNamespace,logical(1),quietly=TRUE))) 0 else 1)"], capture_output=True)
        if check.returncode:
            pytest.skip("必要なR package: " + ",".join(packages))
    prelude = "\n".join(f"source({json.dumps(str(HELPERS / name))})" for name in
                         ("analysis_contract.R", "feature_naming_policy.R", "rds_io.R"))
    prelude += "\n" + r'''
load_functions <- function(path, wanted) {
  found <- character()
  for (expr in parse(path)) {
    if (is.call(expr) && identical(expr[[1]],as.name('<-')) && is.symbol(expr[[2]]) &&
        as.character(expr[[2]]) %in% wanted) {
      eval(expr,envir=.GlobalEnv);found<-c(found,as.character(expr[[2]]))
    }
  }
  stopifnot(setequal(found,wanted))
}
'''
    path = tmp_path / "test.R"
    path.write_text(prelude + body, encoding="utf-8")
    result = subprocess.run([R, "--vanilla", str(path)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_six_decimal_ids_reject_only_actual_id_collisions(tmp_path):
    run_r(tmp_path, """
stopifnot(identical(ua_tims_feature_ids(c(100.000001,100.000002)),
 c('m/z 100.000001','m/z 100.000002')))
for (bad in list(c(100.0000001,100.0000002),c(0,1),c(Inf,1),c(NA,1)))
 stopifnot(inherits(try(ua_tims_feature_ids(bad),silent=TRUE),'try-error'))
""")


def test_reader_preserves_close_masses_within_and_between_inputs(tmp_path):
    run_r(tmp_path, f"""
suppressPackageStartupMessages({{library(Seurat);library(Matrix);library(arrow)}})
load_functions({json.dumps(str(TIMS))},c('read_desi_data','.parse_feature_annotations','.rss_gb','.mem_note_base','.feature_mz'))
`%||%` <- function(a,b) if(!is.null(a)) a else b
ANNOTATION_FILTER<-NULL
make_input <- function(name,masses) {{
 d<-data.frame(id=1:8,x=1:8,y=1,annotation='S')
 for (i in seq_along(masses)) d[[sprintf('%.6f',masses[i])]]<-seq_len(8)*i
 arrow::write_parquet(d,name)
 read_desi_data(name,tools::file_path_sans_ext(name))
}}
a<-make_input('a.parquet',100.000001);b<-make_input('b.parquet',100.000002)
ab<-make_input('ab.parquet',c(100.000001,100.000002))
stopifnot(identical(rownames(ab$count_matrix),c('m/z 100.000001','m/z 100.000002')),
 all(is.finite(.feature_mz(rownames(ab$count_matrix)))))
a<-CreateSeuratObject(a$count_matrix,assay='Spatial');b<-CreateSeuratObject(b$count_matrix,assay='Spatial')
merged<-merge(a,b,add.cell.ids=c('A','B'));merged<-JoinLayers(merged)
m<-LayerData(merged,layer='counts')
stopifnot(nrow(m)==2L,identical(as.numeric(m[1,1:8]),as.numeric(1:8)),all(m[2,1:8]==0),all(m[1,9:16]==0))
# ★ ver74.0: 旧形式の化合物付きIDは同質量でも別IDで保持する（素の列との混在）。
d<-data.frame(id=1:8,x=1:8,y=1,annotation='S')
d[['compoundA_101.000001']]<-1:8;d[['compoundB_101.000001']]<-2*(1:8)
d[['100.000001']]<-3*(1:8);d[['100.000002']]<-4*(1:8)
arrow::write_parquet(d,'legacy.parquet')
legacy<-read_desi_data('legacy.parquet','legacy')
stopifnot(setequal(rownames(legacy$count_matrix),c('compoundA_101.000001','compoundB_101.000001','m/z 100.000001','m/z 100.000002')),
 identical(as.numeric(legacy$count_matrix['compoundB_101.000001',]),as.numeric(2*(1:8))))
""", packages=("Seurat", "Matrix", "arrow"))


def test_calibration_source_survives_keep_and_exclude_names(tmp_path):
    run_r(tmp_path, f"""
suppressPackageStartupMessages(library(Seurat))
load_functions({json.dumps(str(TIMS))},c('calibrate_mz','calibrate_feature_names','rename_seurat_features','.feature_mz'))
CALIBRATION_ENABLE<-TRUE;CALIBRATION_COEFFICIENTS<-99
CALIBRATION_BY_SAMPLE<-list(original=0.000001)
make <- function(sample) {{
 m<-matrix(1:16,2,dimnames=list(c('m/z 100.000001','m/z 101.000001'),paste0('c',1:8)))
 s<-CreateSeuratObject(m,assay='Spatial');s$sample<-sample;s$calibration_source_key<-'original'
 s@misc$input_basename<-sample;s
}}
for (name in c('original_KEEP_Cl_1','original_EXCL_Cl_2','original_KEEP_Cl_1_KEEP_Cl_3')) {{
 out<-calibrate_feature_names(list(make(name)))[[1]]
 stopifnot(identical(rownames(out),c('m/z 100.000000','m/z 101.000000')),
   all(out$calibration_source_key=='original'),out@misc$calibration$source_key=='original')
}}
stopifnot(ua_calibration_source_key(data.frame(x=1),CALIBRATION_BY_SAMPLE,c('new','original'))=='original')
CALIBRATION_BY_SAMPLE<-list(unrelated=0)
stopifnot(inherits(try(calibrate_feature_names(list(make('original_KEEP_Cl_1'))),silent=TRUE),'try-error'))
""", packages=("Seurat",))


def test_immutable_annotation_and_metadata_only_resume_preserve_reduction(tmp_path):
    run_r(tmp_path, """
suppressPackageStartupMessages(library(Seurat))
m<-matrix(1:32,4,dimnames=list(paste0('f',1:4),paste0('c',1:8)))
s<-CreateSeuratObject(m,assay='Spatial');s$annotation<-rep(c('fixed A','fixed B'),each=4)
s$source_file_id<-'source';s$source_pixel_id<-as.character(1:8);s$spot_index<-1:8
s$section_id<-rep(c('a','b'),each=4);s$group<-'old'
s[['pca']]<-CreateDimReducObject(embeddings=matrix(seq_len(16),8,dimnames=list(colnames(s),c('PC_1','PC_2'))),key='PC_',assay='Spatial')
s<-ua_stamp_checkpoint(s,'old-full','same-numeric','old-meta')
registered<-list(list(section_id='a',section_display_name='fixed A',subject_id='m1',group='old',metadata_confirmed=TRUE),
 list(section_id='b',section_display_name='fixed B',subject_id='m2',group='old',metadata_confirmed=TRUE))
effective<-registered;effective[[1]]$section_display_name<-'alias A';effective[[1]]$group<-'new'
entry<-list(file_id='source',path='/a.parquet',runtime_path='',roi_role='spatial',selection_mode='all',
 selected_section_ids=list('a','b'),registered_sections=registered,spatial_sections=effective,sections=effective)
manifest<-list(files=list(entry))
REDUCTION_SIGNATURE<-'same-numeric';METADATA_SIGNATURE<-'new-meta'
stopifnot(ua_checkpoint_matches(s,'new-full'),!ua_checkpoint_matches(s,'new-full','different'))
x<-ua_refresh_checkpoint_metadata(s,manifest,'new-full')
stopifnot(identical(Embeddings(s,'pca'),Embeddings(x,'pca')),
 identical(LayerData(s,layer='counts'),LayerData(x,layer='counts')),
 all(x$section_display_name[1:4]=='alias A'),all(x$group[1:4]=='new'),
 identical(x$annotation,s$annotation),isTRUE(x@misc$metadata_changed_on_resume),x@misc$analysis_signature=='new-full')
# ★ ver74.0: 保存画素に無い切片の追加もmetadata-only更新として許可しない。
only_a<-subset(s,cells=colnames(s)[1:4])
stopifnot(inherits(try(ua_refresh_checkpoint_metadata(only_a,manifest,'new-full'),silent=TRUE),'try-error'))
# 元RDS選択がAのみなら、RDS再解析でA+Bを要求した時点で拒否する。
restricted<-manifest;restricted$files[[1]]$source_selected_section_ids<-list('a')
stopifnot(inherits(try(ua_section_metadata(s@meta.data,'/a.parquet',restricted),silent=TRUE),'try-error'))
restricted$files[[1]]$selected_section_ids<-list('a')
stopifnot(nrow(ua_section_metadata(s@meta.data,'/a.parquet',restricted))==4L)
manifest$files[[1]]$selected_section_ids<-list('a')
stopifnot(inherits(try(ua_refresh_checkpoint_metadata(s,manifest,'new-full'),silent=TRUE),'try-error'))
""", packages=("Seurat",))


def test_wrapped_rds_single_feature_extraction_matches_cell_order(tmp_path):
    helper = HELPERS / "extract_features.R"
    run_r(tmp_path, f"""
suppressPackageStartupMessages(library(Seurat))
m<-matrix(1:32,4,dimnames=list(paste0('f',1:4),paste0('c',1:8)))
s<-CreateSeuratObject(m,assay='Spatial');s<-NormalizeData(s,verbose=FALSE)
for (wrapped in c(FALSE,TRUE)) {{
 o<-if(wrapped) list(obj=s,reduction='pca') else s
 saveRDS(o,'input.rds')
 status<-system2({json.dumps(str(R))},c('--vanilla',{json.dumps(str(helper))},'input.rds','f2','feature.csv'))
 stopifnot(status==0L,isTRUE(all.equal(read.csv('feature.csv')$expression,
   as.numeric(LayerData(s,layer='data')['f2',colnames(s)]))))
}}
stopifnot(inherits(try(ua_unwrap_seurat(list(obj=NULL)),silent=TRUE),'try-error'))
""", packages=("Seurat",))


def test_regression_checker_rejects_incomplete_results(tmp_path):
    helper = HELPERS / "regression_check.R"
    run_r(tmp_path, f"""
suppressPackageStartupMessages(library(Seurat))
m<-matrix(1:60,6,dimnames=list(paste0('f',1:6),paste0('c',1:10)))
s<-CreateSeuratObject(m,assay='Spatial');s$seurat_clusters<-rep(0:1,5)
s[['umap']]<-CreateDimReducObject(embeddings=matrix(seq_len(20),10,dimnames=list(colnames(s),c('UMAP_1','UMAP_2'))),key='UMAP_',assay='Spatial')
saveRDS(s,'a.rds')
check <- function(obj,expected) {{
 saveRDS(obj,'b.rds')
 status<-suppressWarnings(system2({json.dumps(str(R))},c('--vanilla',{json.dumps(str(helper))},'--rds-a','a.rds','--rds-b','b.rds'),stdout='regression.log',stderr='regression.err'))
 if(status!=expected) stop(paste(readLines('regression.log'),readLines('regression.err'),collapse='\\n'))
}}
check(s,0L)
check(subset(s,cells=colnames(s)[-1]),2L)
check(subset(s,features=rownames(s)[-1]),2L)
missing<-s;missing[['umap']]<-NULL;check(missing,2L)
load_functions({json.dumps(str(helper))},c('compare_id_sets','compare_embeddings','compare_source_identity'))
e<-Embeddings(s,'umap');stopifnot(compare_embeddings(e,e[,-1,drop=FALSE])$status=='fail')
stopifnot(!compare_id_sets(c('a','a'),c('a','b'))$equal)
""", packages=("Seurat",))


def test_parquet_ingest_equivalence_cli_runs(tmp_path):
    if not R:
        pytest.skip("Rscriptが必要です")
    run_r(tmp_path, f"""
status<-system2({json.dumps(str(R))},c('--vanilla',{json.dumps(str(HELPERS / 'test_parquet_ingest_equiv.R'))}),stdout='ingest.log',stderr='ingest.err')
if(status!=0L) stop(paste(readLines('ingest.log'),readLines('ingest.err'),collapse='\\n'))
stopifnot(any(grepl('RESULT: PASS',readLines('ingest.log'),fixed=TRUE)))
""", packages=("arrow", "Matrix", "dplyr"))


def test_selected_pixels_define_zero_filter_before_normalization_and_pca(tmp_path):
    run_r(tmp_path, """
suppressPackageStartupMessages(library(Seurat));set.seed(74)
m<-matrix(rpois(40*80,8),40,dimnames=list(paste0('f',1:40),paste0('c',1:80)))
m[1,1:40]<-0;m[1,41:80]<-10
s<-CreateSeuratObject(m,assay='Spatial');s$annotation<-rep(c('A','B'),each=40);s$spot_index<-1:80
rows<-list(list(section_id='a',section_display_name='A',subject_id='m1',group='C',metadata_confirmed=TRUE),
 list(section_id='b',section_display_name='B',subject_id='m2',group='T',metadata_confirmed=TRUE))
entry<-list(path='/data.parquet',file_id='x',roi_role='spatial',selection_mode='selected',
 registered_sections=rows,spatial_sections=rows,sections=rows[1],selected_section_ids=list('a'))
x<-ua_apply_sections(s,'/data.parquet',list(files=list(entry)))
x<-ua_drop_all_zero_features(x)
stopifnot(ncol(x)==40L,nrow(x)==39L,!('f1'%in%rownames(x)),all(x$section_id=='a'),
 identical(x@misc$zero_feature_filter$removed_features,'f1'))
x<-NormalizeData(x,verbose=FALSE);x<-FindVariableFeatures(x,nfeatures=25,verbose=FALSE)
x<-ScaleData(x,verbose=FALSE);x<-RunPCA(x,npcs=5,verbose=FALSE)
stopifnot(nrow(Embeddings(x,'pca'))==40L,all(rownames(Embeddings(x,'pca'))%in%paste0('c',1:40)))
entry$selected_section_ids<-list()
stopifnot(is.null(ua_apply_sections(s,'/data.parquet',list(files=list(entry)))))
zero<-CreateSeuratObject(matrix(0,3,4,dimnames=list(paste0('z',1:3),paste0('p',1:4))),assay='Spatial')
stopifnot(inherits(try(ua_drop_all_zero_features(zero),silent=TRUE),'try-error'))
""", packages=("Seurat",))


def test_calibration_collision_rejected_and_ppm_merge_is_explicit(tmp_path):
    run_r(tmp_path, f"""
suppressPackageStartupMessages(library(Seurat))
load_functions({json.dumps(str(TIMS))},c('calibrate_mz','calibrate_feature_names','rename_seurat_features','.feature_mz',
 'align_mz_features','merge_duplicate_features'))
make<-function(mass,label) {{
 m<-matrix(1:8,1,dimnames=list(ua_tims_feature_ids(mass),paste0(label,1:8)))
 s<-CreateSeuratObject(m,assay='Spatial');s$sample<-label;s
}}
a<-make(100.000001,'a');b<-make(100.000002,'b')
aligned<-align_mz_features(list(a,b),1)
stopifnot(identical(rownames(aligned[[1]]),rownames(aligned[[2]])),
 aligned[[1]]@misc$mz_alignment$ppm==1L,length(aligned[[1]]@misc$mz_alignment$groups[[1]]$members)==2L)
CALIBRATION_ENABLE<-TRUE;CALIBRATION_COEFFICIENTS<-c(0.99,0);CALIBRATION_BY_SAMPLE<-list()
joined<-CreateSeuratObject(matrix(1:16,2,dimnames=list(c('m/z 100.000001','m/z 100.000002'),paste0('p',1:8))),assay='Spatial')
joined$sample<-'joined'
stopifnot(inherits(try(calibrate_feature_names(list(joined)),silent=TRUE),'try-error'))
""", packages=("Seurat",))


def test_full_tims_pipeline_and_metadata_only_resume(tmp_path):
    """★ ver74.0: 実スクリプトの読込からDEG/exportとmetadataだけの再開まで検証。"""
    required = ("Seurat", "arrow", "Matrix", "tidyverse", "data.table", "tictoc",
                "ggplot2", "patchwork", "harmony", "pheatmap", "RColorBrewer",
                "ggrepel", "dbscan", "future", "qs2")
    # 240画素中120だけ選択。最後のfeatureは非選択側だけ非ゼロとする。
    run_r(tmp_path, """
suppressPackageStartupMessages(library(arrow));set.seed(741)
n<-240L;p<-60L;rates<-matrix(10,n,p)
for(i in seq_len(n)) rates[i,((i-1L)%%3L)*15L+seq_len(15L)]<-60
x<-matrix(rpois(n*p,as.vector(rates)),n,p);x[1:120,p]<-0
mzs<-c(100.000001,100.000002,102:159)
d<-data.frame(id=seq_len(n),x=(seq_len(n)-1L)%%20L,y=(seq_len(n)-1L)%/%20L)
for(i in seq_len(p)) d[[sprintf('%.6f',mzs[i])]]<-x[,i]
d$annotation<-rep(c('fixed A','fixed B'),each=120)
arrow::write_parquet(d,'input.parquet')
""", packages=required)
    input_path = tmp_path / "input.parquet"
    registered = [
        dict(section_id="a", section_display_name="fixed A", subject_id="s1",
             group="Ctrl", metadata_confirmed=True),
        dict(section_id="b", section_display_name="fixed B", subject_id="s2",
             group="Case", metadata_confirmed=True),
    ]
    manifest = {"files": [dict(path=str(input_path), runtime_path="", file_id="source1",
                roi_role="spatial", selection_mode="selected", selected_section_ids=["a"],
                registered_sections=registered, spatial_sections=registered, sections=registered[:1])]}
    manifest_path = tmp_path / "sections.json"
    output = tmp_path / "output"
    source = TIMS.read_text(encoding="utf-8")
    source, count = re.subn(r"^INPUT_PATHS\s*<-\s*c\([\s\S]*?^\)",
                           f"INPUT_PATHS <- c({json.dumps(str(input_path))})",
                           source, count=1, flags=re.M)
    assert count == 1
    settings = dict(OUTPUT_DIR=json.dumps(str(output)), SECTION_MANIFEST_PATH=json.dumps(str(manifest_path)),
                    ANALYSIS_SIGNATURE='"full-old"', REDUCTION_SIGNATURE='"numeric-unchanged"',
                    METADATA_SIGNATURE='"metadata-old"', BATCH_CORRECTION_ENABLE="FALSE",
                    ENABLE_RPCA="FALSE", SPATIAL_SMOOTH_ENABLE="FALSE", INPUT_NORMALIZED="FALSE",
                    CALIBRATION_ENABLE="FALSE", ANNOTATION_ENABLE="FALSE", CLUSTER_ALGORITHM="1L",
                    CLUSTER_K_PARAM="10L", UMAP_N_NEIGHBORS="10L", PIPELINE_STAGE='"full"',
                    RESUME_FROM_RDS="FALSE", RDS_CACHE_ENABLE="FALSE")

    def execute(name, settings):
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        script = source
        for key, value in settings.items():
            script, count = re.subn(r"^" + key + r"\s*<-.*$", key + " <- " + value, script, flags=re.M)
            assert count == 1, key
        runtime = tmp_path / f"{name}.R"
        runtime.write_text(script, encoding="utf-8")
        env = dict(os.environ, R_HELPERS_DIR=str(HELPERS), DEG_WORKERS="1", OMP_NUM_THREADS="1",
                   OPENBLAS_NUM_THREADS="1", QS_NTHREADS="1")
        result = subprocess.run([R, "--vanilla", str(runtime)], cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=240)
        log = result.stdout + result.stderr
        (tmp_path / f"{name}.log").write_text(log, encoding="utf-8")
        assert result.returncode == 0, log[-15000:]
        state = json.loads((Path(json.loads(settings["OUTPUT_DIR"])) / "analysis_methods.json").read_text())
        assert state["methods"]["pca"]["status"] == "complete", state
        assert state["methods"]["pca"]["stage"] == "downstream", state
        return log

    execute("initial", settings)
    # 固定annotationと選択は保持し、表示名/群だけを更新する。
    effective = [dict(row) for row in registered]
    effective[0].update(section_display_name="alias A", group="Updated")
    manifest["files"][0].update(spatial_sections=effective, sections=effective[:1])
    resumed = tmp_path / "resumed"
    settings.update(OUTPUT_DIR=json.dumps(str(resumed)), RESUME_FROM_RDS="TRUE",
                    RESUME_DIR_PATH=json.dumps(str(output / "RDS_Files")),
                    PIPELINE_STAGE='"downstream_from_reduction"',
                    ANALYSIS_SIGNATURE='"full-new"', METADATA_SIGNATURE='"metadata-new"')
    log = execute("resume", settings)
    assert "[stage] Reading Parquet / input files" not in log
    assert "[stage] Preprocessing (variable features / scaling / PCA)" not in log
    assert "RESUME: Loading DEG for volcano" not in log
    assert "FindAllMarkers 前" in log
    run_r(tmp_path, """
suppressPackageStartupMessages(library(Seurat))
a<-ua_unwrap_seurat(load_rds_compact('output/RDS_Files/Step2_PCA_uncorrected.rds'))
b<-ua_unwrap_seurat(load_rds_compact('resumed/RDS_Files/Step2_PCA_uncorrected.rds'))
stopifnot(ncol(a)==120L,nrow(a)==59L,all(a$section_id=='a'),
 all(a$spot_index %in% 1:120),all(a$annotation=='fixed A'),
 all(c('m/z 100.000001','m/z 100.000002') %in% rownames(a)),
 !('m/z 159.000000'%in%rownames(a)),
 identical(LayerData(a,layer='counts'),LayerData(b,layer='counts')),
 identical(LayerData(a,layer='data'),LayerData(b,layer='data')),
 identical(Embeddings(a,'pca'),Embeddings(b,'pca')),
 identical(Embeddings(a,'umap'),Embeddings(b,'umap')),
 identical(a$seurat_clusters,b$seurat_clusters),
 all(b$section_display_name=='alias A'),all(b$group=='Updated'),
 identical(a$annotation,b$annotation),b@misc$analysis_signature=='full-new',
 b@misc$reduction_signature=='numeric-unchanged',b@misc$metadata_signature=='metadata-new',
 isTRUE(b@misc$metadata_changed_on_resume))
csv<-read.csv('resumed/pca_uncorrected/input_cluster_assignment_pca_uncorrected.csv')
selected<-!is.na(csv$cluster)
stopifnot(sum(selected)==120L,all(csv$section_id[selected]=='a'),all(csv$group[selected]=='Updated'))
""")
