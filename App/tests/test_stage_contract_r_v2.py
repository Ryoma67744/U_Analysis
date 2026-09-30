"""Small real-Seurat contracts; CI requires these tests without skips."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "Script/helpers"
R = os.environ.get("RSCRIPT") or shutil.which("Rscript")


def run_r(tmp_path, body, packages=("Seurat", "jsonlite", "digest")):
    if not R:
        pytest.skip("Rscript required")
    required = 'c(' + ','.join(json.dumps(package) for package in packages) + ')'
    check = subprocess.run([R,"--vanilla","-e",f'quit(status=if(all(vapply({required},requireNamespace,logical(1),quietly=TRUE))) 0 else 1)'],capture_output=True)
    if check.returncode:
        pytest.skip("/".join(packages) + " required")
    setup = '\n'.join(f'source({json.dumps(str(HELPERS / file))})' for file in
                      ("analysis_contract.R","rds_io.R"))
    script = tmp_path / "verify.R"
    script.write_text(setup + '\n' + body,encoding="utf-8")
    result = subprocess.run([R,"--vanilla",str(script)],cwd=tmp_path,capture_output=True,
                            text=True,encoding="utf-8",errors="replace",timeout=180,
                            env={**os.environ,"OMP_NUM_THREADS":"1"})
    assert result.returncode == 0,result.stdout + result.stderr


SETUP = r'''
suppressPackageStartupMessages(library(Seurat));set.seed(14)
m<-matrix(rpois(40*80,5),40,dimnames=list(paste0('f',1:40),paste0('c',1:80)))
s<-CreateSeuratObject(m,assay='Spatial');s<-NormalizeData(s,verbose=FALSE)
s<-FindVariableFeatures(s,nfeatures=30,verbose=FALSE);s<-ScaleData(s,verbose=FALSE)
s<-RunPCA(s,npcs=5,verbose=FALSE,seed.use=17)
contract<-list(signature_schema_version=2L,run_id='run-a',execution_mode='resume_same',
 stage_signatures=list(upstream='upstream-a',pca='pca-a',umap='umap-a',cluster='cluster-a',deg='deg-a',export='export-a'))
STAGE_SIGNATURES_PATH<-'contract.json';ANALYSIS_SIGNATURE<-'analysis-a'
jsonlite::write_json(contract,STAGE_SIGNATURES_PATH,auto_unbox=TRUE)
s<-ua_stamp_checkpoint(s,ANALYSIS_SIGNATURE)
dir.create('RDS_Files');path<-file.path(getwd(),'RDS_Files','pca.rds')
'''


def test_real_cluster_provenance_dims_and_partial_failure(tmp_path):
    run_r(tmp_path,SETUP+r'''
stopifnot(inherits(try(ua_validate_dims(s,'pca',6,3),silent=TRUE),'try-error'))
s<-ua_stage_facts(s,'pca','reduction','pca',list(n_dims=5L))
original<-s@misc$result_provenance$stages$reduction
reader_versions<-ua_dependency_versions
ua_dependency_versions<-function() list(Seurat='different-reader-version')
republished<-ua_stage_facts(s,'pca','reduction','pca',list(n_dims=5L))
stopifnot(identical(republished@misc$result_provenance$stages$reduction,original))
ua_dependency_versions<-reader_versions
save_rds_compact(s,path);ua_record_method(getwd(),'pca','complete',stage='reduction',rds_path=path,obj=s)
stopifnot(!ua_can_reuse_clustering(s,'pca','pca'))
s<-ua_cluster_reduction(s,'pca',3,3,10,.3,'cosine',17,10,'euclidean',.5,1,
 method='pca',cluster_seed=23,on_umap=function(x) ua_save_umap_stage(x,getwd(),'pca','pca',path))
save_rds_compact(s,path);ua_record_method(getwd(),'pca','complete',stage='cluster',rds_path=path,obj=s)
ua_record_method(getwd(),'pca','failed','intentional figure failure','export',path)
state<-jsonlite::fromJSON('analysis_methods.json',simplifyVector=FALSE)
stopifnot(state$schema_version==2L,state$methods$pca$stages$reduction$status=='complete',
 state$methods$pca$stages$umap$status=='complete',state$methods$pca$stages$cluster$status=='complete',
 state$methods$pca$stages$export$status=='failed',
 identical(state$methods$pca$artifact_sha256,ua_file_digest(path)),
 ua_can_reuse_clustering(s,'pca','pca'))
bad<-s;bad$seurat_clusters<-rep('copied',ncol(s))
stopifnot(!ua_can_reuse_clustering(bad,'pca','pca'))
contract$execution_mode<-'downstream_new';jsonlite::write_json(contract,STAGE_SIGNATURES_PATH,auto_unbox=TRUE)
stopifnot(!ua_can_reuse_clustering(s,'pca','pca'))
''')


def test_unsigned_import_requires_exact_manifest_file_and_structure(tmp_path):
    run_r(tmp_path,SETUP+r'''
legacy<-s;legacy@misc<-list();saveRDS(list(obj=legacy,reduction='pca'),path)
stopifnot(!ua_checkpoint_matches(legacy,ANALYSIS_SIGNATURE))
manifest<-list(schema_version=1L,kind='validated_reduction_import',artifacts=list(list(
 path='RDS_Files/pca.rds',sha256=ua_file_digest(path),method='pca',reduction='pca',n_cells=80L,n_dims=5L)))
LEGACY_IMPORT_MANIFEST_PATH<-file.path(getwd(),'legacy_import.json')
jsonlite::write_json(manifest,LEGACY_IMPORT_MANIFEST_PATH,auto_unbox=TRUE)
LEGACY_IMPORT_MANIFEST_SHA256<-ua_file_digest(LEGACY_IMPORT_MANIFEST_PATH)
stopifnot(ua_checkpoint_matches(list(obj=legacy,reduction='pca'),ANALYSIS_SIGNATURE,source_path=path))
restored<-ua_refresh_checkpoint_metadata(legacy,NULL,ANALYSIS_SIGNATURE,source_path=path)
stopifnot(is.null(restored@misc$analysis_signature),!is.null(restored@misc$validated_legacy_import))
saveRDS(list(obj=s,reduction='pca'),path)
stopifnot(inherits(try(ua_checkpoint_matches(s,ANALYSIS_SIGNATURE,source_path=path),silent=TRUE),'try-error'))
''')


def test_real_harmony_and_rpca_keep_distinct_cluster_origins(tmp_path):
    template = ROOT / "Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R"
    run_r(tmp_path,SETUP+'\nTIMS_TEMPLATE <- '+json.dumps(str(template))+r'''
s$integration_unit_id<-rep(c('a','b'),each=40)
contract$stage_signatures$harmony<-'harmony-a';contract$stage_signatures$rpca<-'rpca-a'
jsonlite::write_json(contract,STAGE_SIGNATURES_PATH,auto_unbox=TRUE)
pca<-ua_stage_facts(s,'pca','reduction','pca')
set.seed(23)
h<-harmony::RunHarmony(pca,group.by.vars='integration_unit_id',project.dim=FALSE,verbose=FALSE)
h<-ua_stage_facts(h,'harmony','reduction','harmony')
stopifnot(h@misc$result_provenance$result_id!=pca@misc$result_provenance$result_id)
parts<-SplitObject(s,split.by='integration_unit_id')
features<-rownames(s)[1:30]
parts<-lapply(parts,function(x) {
  x<-ScaleData(x,features=features,verbose=FALSE)
  RunPCA(x,features=features,npcs=5,seed.use=17,verbose=FALSE)
})
set.seed(23)
anchors<-FindIntegrationAnchors(parts,anchor.features=features,reduction='rpca',
  dims=1:3,k.anchor=3,k.filter=NA,k.score=10,verbose=FALSE)
r<-IntegrateData(anchors,dims=1:3,k.weight=10,verbose=FALSE)
r<-ScaleData(r,verbose=FALSE)
r<-RunPCA(r,npcs=5,seed.use=17,verbose=FALSE)
r<-ua_stage_facts(r,'rpca','reduction','pca')
for (method in c('pca','harmony','rpca')) {
  obj<-switch(method,pca=pca,harmony=h,rpca=r)
  reduction<-if(method=='harmony') 'harmony' else 'pca'
  obj<-ua_cluster_reduction(obj,reduction,3,3,10,.3,'cosine',17,10,'euclidean',.5,1,
    method=method,cluster_seed=23)
  facts<-obj@misc$result_provenance
  stopifnot(facts$method==method,facts$cluster_space==reduction,facts$cluster_kind=='computed',
    facts$stages$cluster$effective$seed==23,
    identical(facts$stages$cluster$reduction_hash,ua_digest(Embeddings(obj,reduction))),
    ua_can_reuse_clustering(obj,method,reduction),
    !ua_can_reuse_clustering(obj,if(method=='pca') 'harmony' else 'pca',reduction))
  # A failed new UMAP cannot expose stale inherited assignments as completed.
  cleared<-ua_prepare_reduction(obj,reduction)
  stopifnot(is.null(cleared@misc$result_provenance$stages$cluster),
    is.null(cleared@meta.data$seurat_clusters),cleared@misc$result_provenance$cluster_kind=='none')
}
# A verified old RPCA may store its integrated embedding under the name pca.
# The imported method set must not synthesize an uncorrected PCA output.
r@misc<-list();r$integration_unit_id<-NULL
import_path<-file.path(getwd(),'RDS_Files','old_rpca.rds');saveRDS(list(obj=r),import_path)
LEGACY_IMPORT_MANIFEST_PATH<-file.path(getwd(),'legacy_import.json')
manifest<-list(schema_version=1L,kind='validated_reduction_import',artifacts=list(list(
 path='RDS_Files/old_rpca.rds',sha256=ua_file_digest(import_path),method='rpca',reduction='pca',n_cells=80L,n_dims=5L,
 source_result_id='verified-old-rpca')))
jsonlite::write_json(manifest,LEGACY_IMPORT_MANIFEST_PATH,auto_unbox=TRUE)
LEGACY_IMPORT_MANIFEST_SHA256<-ua_file_digest(LEGACY_IMPORT_MANIFEST_PATH)
for (expr in parse(TIMS_TEMPLATE)) if(is.call(expr) && identical(expr[[1]],as.name('<-')) &&
 is.symbol(expr[[2]]) && as.character(expr[[2]])=='.ua_finish_tims') eval(expr)
PIPELINE_STAGE<-'downstream_from_reduction';od<-file.path(getwd(),'import-output');dir.create(od)
dir.create(file.path(od,'RDS_Files'));RESUME_FROM_RDS<-TRUE;ann_db<-NULL
UMAP_DIMS_MAX<-CLUSTER_DIMS_N<-3L;UMAP_N_NEIGHBORS<-CLUSTER_K_PARAM<-10L
UMAP_MIN_DIST<-.3;UMAP_METRIC<-'cosine';GLOBAL_RANDOM_SEED<-17L;CLUSTER_SEED<-23L
CLUSTER_METRIC<-'euclidean';CLUSTER_RESOLUTION<-.5;CLUSTER_ALGORITHM<-1L
run_downstream_analysis<-function(obj,...) obj
calls<-character()
ua_run_imported_reductions(ua_import_records(),od,function(obj,method,reduction) {
  calls<<-c(calls,method)
  .ua_finish_tims(obj,method,method,file.path(od,'RDS_Files','rpca.rds'),TRUE,reduction)
})
state<-jsonlite::fromJSON(file.path(od,'analysis_methods.json'),simplifyVector=FALSE)
stopifnot(identical(calls,'rpca'),identical(names(state$methods),'rpca'),
 state$methods$rpca$stages$cluster$status=='complete',
 state$methods$rpca$provenance$method=='rpca',state$methods$rpca$provenance$cluster_space=='pca',
 state$methods$rpca$provenance$source_result_id=='verified-old-rpca')
''',packages=("Seurat", "jsonlite", "digest", "harmony"))


def test_parquet_column_roles_refuse_malformed_and_preserve_legacy(tmp_path):
    run_r(tmp_path,'source(' + json.dumps(str(HELPERS / "parquet_column_roles.R")) + ')' + r'''
schema<-list(names=c('id','x','y','100.1','UMAP_1','PCA'),metadata=list())
stopifnot(is.null(ua_parquet_feature_columns(schema)))
roles<-list(schema_version=1L,columns=Map(function(n,r) list(name=n,role=r),
  schema$names,c('identity','spatial','spatial','intensity','embedding','cluster')))
schema$metadata$ua_column_roles<-jsonlite::toJSON(roles,auto_unbox=TRUE)
stopifnot(identical(ua_parquet_feature_columns(schema),'100.1'))
for (kind in c('version','missing','duplicate','unknown')) {
  bad<-roles
  if(kind=='version') bad$schema_version<-2L
  if(kind=='missing') bad$columns<-bad$columns[-1]
  if(kind=='duplicate') bad$columns[[2]]$name<-bad$columns[[1]]$name
  if(kind=='unknown') bad$columns[[1]]$role<-'unknown'
  schema$metadata$ua_column_roles<-jsonlite::toJSON(bad,auto_unbox=TRUE)
  stopifnot(inherits(try(ua_parquet_feature_columns(schema),silent=TRUE),'try-error'))
}
''',packages=("jsonlite",))
