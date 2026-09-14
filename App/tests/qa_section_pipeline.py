"""小規模なTIMS実行ゲートを生成する（実行は別途Rscriptで直列に行う）。

python App/tests/qa_section_pipeline.py /tmp/tims_section_qa

生成された case_index.json の full → single → reduction → failure → reanalysis
を順に実行する。continuation は reduction 完了後に --continue で生成する。
R_HELPERS_DIR は App/Script/helpers を指定する。
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
from app.services.analysis_runner import generate_v8_config,generate_cluster_filter_config


def save_params(out, params):
    (out/'analysis_params.json').write_text(json.dumps({
        'runtime_parameters':params,'input_fingerprints':params.get('input_fingerprints',[]),
        'analysis_signature':params.get('analysis_signature','')},ensure_ascii=False),encoding='utf-8')


def generate(root):
    data=root/'input';data.mkdir(parents=True,exist_ok=True)
    rng=np.random.default_rng(71);n=240;p=90
    mat=rng.poisson(7,(n,p)).astype(float)
    for j in range(3):
        mask=np.arange(n)%3==j
        mat[mask,j*20:(j+1)*20]+=rng.poisson(15,(mask.sum(),20))
    frame=pd.DataFrame(mat,columns=[f'mz_{100+i*2:.5f}' for i in range(p)])
    frame.insert(0,'annotation',np.repeat(['ROI1','ROI2'],n//2))
    frame.insert(0,'y',np.arange(n)//20);frame.insert(0,'x',np.arange(n)%20)
    frame.insert(0,'id',np.arange(n)+1)
    source=data/'sections.parquet';frame.to_parquet(source,index=False)
    manifest={'schema_version':1,'files':[{'file_id':'file_1','path':str(source),
        'selection_mode':'selected','rois':['ROI1','ROI2'],'roi_role':'section',
        'sections':[{'section_id':'s1','roi':'ROI1','subject_id':'C1','group':'Ctrl','integration_unit_id':'s1'},
                    {'section_id':'s2','roi':'ROI2','subject_id':'T1','group':'Treatment','integration_unit_id':'s2'}]}]}
    params={'execution_policy':'section_auto_v1','section_manifest':manifest,
        'template_path':str(APP/'Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R'),
        'data_folder':str(data),'sample_names':['sections'],'input_paths':[str(source)],
        'output_dir_var':'OUTPUT_DIR','annotation_enable':False,'input_normalized':True,
        'norm_mode':'log1p','umap_n_neighbors':10,'umap_dims_n':10,'cluster_k_param':10,
        'cluster_algorithm':4,'cluster_resolution':.4,'pipeline_stage':'full','ion_mode':'Negative'}
    (root/'base_params.json').write_text(json.dumps(params),encoding='utf-8')
    index={}
    for case in ('full','single','reduction','failure'):
        spec=deepcopy(params);out=root/case;out.mkdir(exist_ok=True)
        if case=='single':
            spec['section_manifest']['files'][0]['rois']=['ROI1']
            spec['section_manifest']['files'][0]['sections']=spec['section_manifest']['files'][0]['sections'][:1]
        if case=='reduction': spec['pipeline_stage']='reduction_only'
        script=Path(generate_v8_config(spec,str(out)))
        if case=='failure':
            code=script.read_text(encoding='utf-8').replace('group_var <- "integration_unit_id"',
                'RunHarmony <- function(...) stop("intentional Harmony failure")\n'
                'IntegrateLayers <- function(...) stop("intentional RPCA failure")\n'
                'group_var <- "integration_unit_id"')
            script.write_text(code,encoding='utf-8')
        save_params(out,spec);index[case]=str(script)
    spec=deepcopy(params);out=root/'reanalysis';out.mkdir(exist_ok=True)
    spec.update(template_path=str(APP/'Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R'),
        main_analysis_script_path=params['template_path'],rds_path=str(root/'full/RDS_Files/Step2_PCA_uncorrected.rds'),
        rds_run_dir=str(root/'full'),cluster_source='pca',original_data_folder=params['data_folder'],
        original_input_paths=params['input_paths'],filter_mode='keep',target_clusters=[1,2],
        export_data_dir=str(out),cluster_algorithm=1,cluster_k_param=8,cluster_resolution=.7)
    spec.pop('input_paths',None)
    index['reanalysis']=generate_cluster_filter_config(spec,str(out));save_params(out,spec)
    (root/'case_index.json').write_text(json.dumps(index,indent=2),encoding='utf-8')
    print(json.dumps(index,indent=2))


def continuation(root):
    spec=json.loads((root/'base_params.json').read_text())
    spec.update(resume_from_rds=True,resume_rds_paths=[str(root/'reduction/RDS_Files/Step2_PCA_uncorrected.rds')],
                pipeline_stage='downstream_from_reduction')
    out=root/'continuation';out.mkdir(exist_ok=True)
    script=generate_v8_config(spec,str(out));save_params(out,spec)
    print(script)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path);parser.add_argument('--continue',dest='continue_run',action='store_true')
    args=parser.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    continuation(root) if args.continue_run else generate(root)
