"""Rの実際の共通処理へ小規模入力を渡し、選択・由来・数値不変性を検証する。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]
HELPER=ROOT/'Script/helpers/analysis_contract.R'
RSCRIPT=os.environ.get('RSCRIPT') or shutil.which('Rscript')

def run_r(tmp_path, code, seurat=False):
    if not RSCRIPT:
        pytest.skip('Rscript is not available')
    if seurat:
        check=subprocess.run([RSCRIPT,'-e',"quit(status=if(requireNamespace('Seurat',quietly=TRUE)) 0 else 1)"],capture_output=True)
        if check.returncode:
            pytest.skip('Seurat is not installed')
    script=tmp_path/'check.R'
    script.write_text(f'source({json.dumps(str(HELPER))})\n'+code,encoding='utf-8')
    result=subprocess.run([RSCRIPT,str(script)],capture_output=True,text=True,timeout=150)
    assert result.returncode==0,result.stdout+result.stderr

def test_file_specific_selection_and_none_are_not_all(tmp_path):
    run_r(tmp_path,"""
md <- data.frame(annotation=c('ROI1','ROI2','ROI1'),spot_index=1:3,sample='display')
manifest <- list(files=list(
 list(path='/a/same.parquet',file_id='a',selection_mode='selected',rois=list('ROI1'),roi_role='section',
 sections=list(list(roi='ROI1',section_id='a1',subject_id='C1',group='Ctrl',integration_unit_id='a1'))),
 list(path='/b/same.parquet',file_id='b',selection_mode='none',roi_role='section',sections=list())))
a <- ua_section_metadata(md,'/a/same.parquet',manifest)
b <- ua_section_metadata(md,'/b/same.parquet',manifest)
stopifnot(identical(a$spot_index,c(1L,3L)),all(a$section_id=='a1'),nrow(b)==0L,
 all(a$source_file_id=='a'),all(a$sample=='display'),all(a$subject_id=='C1'))
manifest$files[[1]]$rois <- list()
stopifnot(nrow(ua_section_metadata(md,'/a/same.parquet',manifest))==0L)
""")

def test_region_rois_keep_parent_and_runtime_alias(tmp_path):
    run_r(tmp_path,"""
md <- data.frame(ROI=c('Brain','Liver','Heart'),spot_index=11:13,sample='old')
entry <- list(path='/source/a.xlsx',runtime_path='/staging/a.txt',file_id='original',
 selection_mode='selected',rois=list('Brain','Heart'),roi_role='region',
 sections=list(list(section_id='parent',subject_id='mouse1',group='Ctrl',integration_unit_id='parent')))
x <- ua_section_metadata(md,'/staging/a.txt',list(files=list(entry)),roi_col='ROI')
stopifnot(identical(x$spot_index,c(11L,13L)),length(unique(x$integration_unit_id))==1L,
 all(x$integration_unit_id=='parent'),all(x$source_file_id=='original'))
""")

def test_reanalysis_source_ids_and_group_are_preserved(tmp_path):
    run_r(tmp_path,"""
md <- data.frame(annotation='ROI1',spot_index=c(2L,4L),sample='new_KEEP_Cl_1',
 source_file_id='old_file',source_pixel_id=c('2','4'),section_id='old_section',
 subject_id='mouse1',group='Ctrl',integration_unit_id='old_section')
stopifnot(identical(ua_section_metadata(md,'/temporary/new_KEEP_Cl_1.parquet'),md))
""")

def test_group_edit_does_not_change_intensities_pca_or_clusters(tmp_path):
    run_r(tmp_path,"""
suppressPackageStartupMessages(library(Seurat)); set.seed(71)
m <- matrix(rpois(50*90,5),nrow=50,dimnames=list(paste0('f',1:50),paste0('c',1:90)))
s <- CreateSeuratObject(m); s <- NormalizeData(s,verbose=FALSE)
s <- FindVariableFeatures(s,nfeatures=40,verbose=FALSE);s <- ScaleData(s,verbose=FALSE)
s <- RunPCA(s,npcs=8,verbose=FALSE);s$annotation <- rep(c('ROI1','ROI2'),each=45)
s$spot_index <- 1:90;s$seurat_clusters <- rep(0:2,30)
entry <- list(path='/x/a',file_id='a',selection_mode='all',roi_role='section',sections=list(
 list(roi='ROI1',section_id='one',subject_id='m1',group='old',integration_unit_id='one'),
 list(roi='ROI2',section_id='two',subject_id='m2',group='old',integration_unit_id='two')))
a <- ua_apply_sections(s,'/x/a',list(files=list(entry)))
entry$sections[[1]]$group <- 'Ctrl';entry$sections[[2]]$group <- 'Treatment'
b <- ua_apply_sections(a,'/x/a',list(files=list(entry)))
stopifnot(identical(LayerData(a,layer='counts'),LayerData(b,layer='counts')),
 identical(Embeddings(a,'pca'),Embeddings(b,'pca')),identical(a$seurat_clusters,b$seurat_clusters),!identical(a$group,b$group))
s <- ua_stamp_checkpoint(s,'expected')
stopifnot(ua_checkpoint_matches(list(obj=s,reduction='pca'),'expected'),!ua_checkpoint_matches(list(obj=s,reduction='pca'),'different'))
""",seurat=True)

def test_independent_pca_does_not_inherit_harmony_clusters(tmp_path):
    run_r(tmp_path,"""
suppressPackageStartupMessages(library(Seurat));set.seed(18)
m <- matrix(rpois(60*100,8),nrow=60,dimnames=list(paste0('f',1:60),paste0('c',1:100)))
s <- CreateSeuratObject(m);s <- NormalizeData(s,verbose=FALSE)
s <- FindVariableFeatures(s,nfeatures=45,verbose=FALSE);s <- ScaleData(s,verbose=FALSE)
s <- RunPCA(s,npcs=8,verbose=FALSE)
s[['harmony']] <- CreateDimReducObject(embeddings=Embeddings(s,'pca')*3,key='H_',assay=DefaultAssay(s))
s$seurat_clusters <- rep(99L,ncol(s));Idents(s) <- s$seurat_clusters
clean <- ua_prepare_reduction(s,'pca')
stopifnot(identical(names(clean@reductions),'pca'),!('seurat_clusters'%in%names(clean@meta.data)),!any(as.character(Idents(clean))=='99'))
a <- ua_cluster_reduction(s,'pca',8,8,10,0.1,'cosine',42,10,'euclidean',0.5,1)
b <- ua_cluster_reduction(clean,'pca',8,8,10,0.1,'cosine',42,10,'euclidean',0.5,1)
stopifnot(identical(a$seurat_clusters,b$seurat_clusters),identical(Embeddings(a,'umap'),Embeddings(b,'umap')),
 identical(LayerData(a,layer='counts'),LayerData(s,layer='counts')))
""",seurat=True)


@pytest.mark.parametrize('relative_path', [
    'Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R',
    'Script/Common/UMAP_Merge_Clusters_ver1.R',
])
def test_merge_matches_source_identity_with_duplicate_display_names(tmp_path, relative_path):
    """マージ関数そのものを評価し、同名入力の画素が交差対応しないことを検証。"""
    script_path = json.dumps(str(ROOT / relative_path))
    run_r(tmp_path, f"""
exprs <- parse({script_path})
for (expr in exprs) {{
  if (is.call(expr) && identical(expr[[1]], as.name('<-')) &&
      is.symbol(expr[[2]]) && as.character(expr[[2]]) %in% c('.stopif', '.make_cell_key')) eval(expr)
}}
setClass('MetadataFixture', slots=c(meta.data='data.frame'))
md <- data.frame(sample='same',spot_index=c(1,1),source_file_id=c('a','b'),
 source_pixel_id=c('1','1'),row.names=c('original_a','original_b'))
base <- new('MetadataFixture',meta.data=md)
changed <- md[2:1,,drop=FALSE];rownames(changed) <- c('temporary_b','temporary_a')
rerun <- new('MetadataFixture',meta.data=changed)
a <- .make_cell_key(base); b <- .make_cell_key(rerun)
stopifnot(identical(unname(a),c('a|1','b|1')),identical(match(a,b),c(2L,1L)),
 identical(names(b),c('temporary_b','temporary_a')))
changed$source_file_id <- 'a'
stopifnot(inherits(try(.make_cell_key(new('MetadataFixture',meta.data=changed)),silent=TRUE),'try-error'))
changed$source_file_id <- c(NA,'a')
stopifnot(inherits(try(.make_cell_key(new('MetadataFixture',meta.data=changed)),silent=TRUE),'try-error'))
""")


def test_resumed_reduction_records_all_available_methods(tmp_path):
    """reduction再開は新規計算を伴わなくても手法一覧・続きを実行へ結果を登録する。"""
    script_path = json.dumps(str(ROOT / 'Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R'))
    run_r(tmp_path, f"""
exprs <- parse({script_path})
for (expr in exprs) {{
  if (is.call(expr) && identical(expr[[1]], as.name('<-')) &&
      is.symbol(expr[[2]]) && as.character(expr[[2]]) == '.ua_finish_tims') eval(expr)
}}
PIPELINE_STAGE <- 'reduction_only'; od <- tempfile(); dir.create(od)
for (method in c('pca','harmony','rpca')) {{
  path <- file.path(od,paste0(method,'.rds')); saveRDS(list(reduction=method),path)
  obj <- list(reduction=method)
  stopifnot(identical(.ua_finish_tims(obj,method,method,path),obj))
}}
state <- jsonlite::fromJSON(file.path(od,'analysis_methods.json'),simplifyVector=FALSE)
stopifnot(setequal(names(state$methods),c('pca','harmony','rpca')))
for (entry in state$methods) stopifnot(entry$status=='complete',entry$stage=='reduction',file.exists(entry$rds_path))
""")


def test_cluster_export_selects_exact_file_and_preserves_metadata(tmp_path):
    run_r(tmp_path,"""
md <- data.frame(sample='same',spot_index=c(1L,2L,1L,2L),seurat_clusters=c(0,1,2,3),
 source_file_id=rep(c('a','b'),each=2),source_pixel_id=rep(c('1','2'),2),
 section_id=rep(c('a1','b1'),each=2),subject_id=rep(c('C1','T1'),each=2),
 group=rep(c('Ctrl','Treatment'),each=2),integration_unit_id=rep(c('a1','b1'),each=2))
manifest <- list(files=list(list(path='/a/same.parquet',file_id='a'),list(path='/b/same.parquet',file_id='b')))
a <- ua_input_metadata(md,'/a/same.parquet',manifest)
b <- ua_input_metadata(md,'/b/same.parquet',manifest)
stopifnot(identical(a$seurat_clusters,c(0,1)),identical(b$seurat_clusters,c(2,3)),
 all(a$group=='Ctrl'),all(b$group=='Treatment'),all(b$source_file_id=='b'))
# 表示名だけでは旧データを一意に分けられない場合、誤結合せず停止する。
stopifnot(inherits(try(ua_input_metadata(md,'/unresolved/same.parquet'),silent=TRUE),'try-error'))
""")


def test_reexport_coordinate_mapping_preserves_source_rows_and_rejects_ambiguity(tmp_path):
    run_r(tmp_path,"""
# 新入力順が逆で番号も101/103へ変わっても、元画素1/3を対応づける。
source <- data.frame(source_pixel_id=c('1','3'),x=c(0,2),y=c(0,2))
index <- ua_match_xy(c(2,1,0),c(2,1,0),source$x,source$y)
stopifnot(identical(index,c(2L,NA_integer_,1L)),
 identical(source$source_pixel_id[index[!is.na(index)]],c('3','1')))
# ビンの境界を挟む近接座標も許容誤差内なら拾う。
stopifnot(identical(ua_match_xy(1.01,0,.99,0,.05),1L))
stopifnot(inherits(try(ua_match_xy(1,0,c(.99,1.01),c(0,0),.05),silent=TRUE),'try-error'),
 inherits(try(ua_match_xy(c(1,1),c(0,0),1,0),silent=TRUE),'try-error'))
""")


def test_duplicate_sample_labels_are_stable_and_only_collisions_change(tmp_path):
    run_r(tmp_path,"""
labels <- c('same','same','distinct');ids <- c('file_a','file_b','file_c')
renamed <- ua_disambiguate_samples(labels,ids)
stopifnot(length(unique(renamed))==3L,renamed[3]=='distinct',
 identical(ua_disambiguate_samples(labels[3:1],ids[3:1]),renamed[3:1]),
 identical(ua_disambiguate_samples(rep('same',2),rep('file_a',2)),rep('same',2)))
# Sample候補でROI列が勝った場合も、同じROIを別ファイル間で混同しない。
roi <- rep(c('ROI1','ROI2','ROI3'),2);sources <- rep(c('file_a','file_b'),each=3)
resolved <- ua_disambiguate_samples(roi,sources)
stopifnot(length(unique(resolved))==6L,
 all(vapply(split(sources,resolved),function(x)length(unique(x))==1L,logical(1))))
""")


def test_merge_subclusters_retain_parent_cluster_labels(tmp_path):
    """実マージ関数のラベル生成ループを、名前を持たないメタデータ列で評価する。"""
    script_path = json.dumps(str(ROOT / 'Script/Common/UMAP_Merge_Clusters_ver1.R'))
    run_r(tmp_path, f"""
exprs <- parse({script_path})
for (expr in exprs) {{
  if (is.call(expr) && identical(expr[[1]], as.name('<-')) && is.symbol(expr[[2]]) &&
      as.character(expr[[2]]) %in% c('.make_subcluster_label','merge_clusters')) eval(expr)
}}
md_base <- data.frame(seurat_clusters=c('1','2','3'),row.names=c('base_a','base_b','base_c'))
base_new_label <- setNames(as.character(md_base$seurat_clusters),rownames(md_base))
base_key <- setNames(c('file_a|1','file_b|1','file_a|2'),rownames(md_base))
key_to_rercl <- c('file_a|1'='0','file_b|1'='1')
rep_cells <- c('base_a','base_b');subcluster_naming <- 'alpha'
loops <- Filter(function(x) is.call(x) && identical(x[[1]],as.name('for')) &&
  identical(x[[2]],as.name('bc')),as.list(body(merge_clusters)))
stopifnot(length(loops)==1L);eval(loops[[1]])
stopifnot(identical(unname(base_new_label),c('1-a','2-b','3')))
""")


def test_spatial_components_assign_three_sections_and_reject_unknown(tmp_path):
    run_r(tmp_path, """
md <- data.frame(ua_coordinate_component=c('a','a','b','c'),spot_index=1:4,sample='same')
entry <- list(path='/x/a.parquet',file_id='file_a',selection_mode='selected',roi_role='spatial',
 spatial_sections=list(
  list(component_ids=list('a'),section_id='s1',section_display_name='Section 01',integration_unit_id='s1'),
  list(component_ids=list('b'),section_id='s2',section_display_name='Section 02',integration_unit_id='s2'),
  list(component_ids=list('c'),section_id='s3',section_display_name='Section 03',integration_unit_id='s3')),
 sections=list(
  list(component_ids=list('a'),section_id='s1',section_display_name='Section 01',integration_unit_id='s1'),
  list(component_ids=list('b'),section_id='s2',section_display_name='Section 02',integration_unit_id='s2'),
  list(component_ids=list('c'),section_id='s3',section_display_name='Section 03',integration_unit_id='s3')))
x <- ua_section_metadata(md,'/x/a.parquet',list(files=list(entry)))
stopifnot(identical(as.character(x$section_id),c('s1','s1','s2','s3')),
          length(unique(x$integration_unit_id))==3L,
          identical(as.character(x$section_display_name),c('Section 01','Section 01','Section 02','Section 03')))
entry$sections <- entry$sections[1:2]
y <- ua_section_metadata(md,'/x/a.parquet',list(files=list(entry)))
stopifnot(nrow(y)==3L,!('c'%in%y$ua_coordinate_component))
md$ua_coordinate_component[4] <- 'unknown'
stopifnot(inherits(try(ua_section_metadata(md,'/x/a.parquet',list(files=list(entry))),silent=TRUE),'try-error'))
""")
