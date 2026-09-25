"""実Rの代わりに明示したrunnerを使う制御試験。R/UMAPの数値試験ではない。"""
from copy import deepcopy
import json
from pathlib import Path
import os
import subprocess
import sys
import time
import pytest
import psutil
from app.services import analysis_pipeline as ap, input_preparation as ip
from app.services import execution_policy as ep, process_control as pc
from app.services.section_metadata import build_section_manifest
from app.config import TIMS_V8_TEMPLATE_PATH
from .test_imzml_autoimport_core import source,fake_convert,fake_validate,SPEC


@pytest.fixture
def setup(tmp_path,monkeypatch):
    p=source(tmp_path/'raw');root=tmp_path/'result';root.mkdir()
    import app.config as config
    monkeypatch.setattr(config,'IMZML_CACHE_DIR',tmp_path/'cache')
    monkeypatch.setattr(ip,'conversion_spec',lambda:SPEC.copy())
    params={'data_folder':str(p.parent),'input_paths':[str(p)],'template_path':str(TIMS_V8_TEMPLATE_PATH),
        'execution_policy':ep.AUTO_POLICY,'section_manifest':build_section_manifest([{'path':str(p),'available_rois':[]}]),
        'annotation_enable':False,'input_normalized':True,'norm_mode':'log1p','sample_names':['sample']}
    return p,root,params


def payload(root,params):return {'output_dir':str(root),'params':params,'kind':'initial','record':{'description':'test-only'}}

def complete(root):
    rds=root/'FAKE_NOT_R_DATA.rds';rds.write_bytes(b'not R data: control test only')
    ip.write_json(root/'analysis_methods.json',{'methods':{'pca':{'status':'complete','stage':'umap','rds_path':str(rds)}}})


def test_full_control_flow_prepares_before_r_and_persists(setup):
    p,root,params=setup;seen=[]
    def runner(script,runtime):
        seen.append(script)
        assert ap.pipeline_state(root)['stage']=='r_analysis'
        assert runtime['input_paths'][0].endswith('.parquet')
        assert str(p) not in runtime['input_paths']
        assert runtime['input_normalized'] is True and runtime['norm_mode']=='log1p'
        assert ip.runtime_paths(runtime['section_manifest'],validate=True)==runtime['input_paths']
        assert Path(script).is_file()
        complete(root);return 0
    assert ap.run_pipeline(payload(root,params),converter=fake_convert,validator=fake_validate,runner=runner)==0
    assert len(seen)==1 and ap.pipeline_state(root)['stage']=='complete'
    saved=json.loads((root/'analysis_params.json').read_text())
    assert saved['section_manifest']['files'][0]['path']==str(p)
    assert saved['section_manifest']['files'][0]['conversion_key']
    assert not any(k.startswith('_imzml_') for k in saved['runtime_parameters'])


def test_any_input_failure_prevents_r(setup):
    p,root,params=setup;bad=source(p.parent,'bad');bad.with_suffix('.ibd').unlink()
    params['section_manifest']=build_section_manifest([{'path':str(x),'available_rois':[]} for x in (p,bad)])
    def unexpected(*a):pytest.fail('R must not start')
    assert ap.run_pipeline(payload(root,params),converter=fake_convert,validator=fake_validate,runner=unexpected)==2
    assert ap.pipeline_state(root)['stage']=='error'
    assert not (root/'analysis_params.json').exists()


@pytest.mark.parametrize('rc',[1,7,-15])
def test_r_failure_propagates(setup,rc):
    _,root,params=setup
    assert ap.run_pipeline(payload(root,params),converter=fake_convert,validator=fake_validate,runner=lambda *_:rc)!=0
    assert ap.pipeline_state(root)['r_exit_code']==rc


def test_zero_exit_without_current_result_not_success(setup):
    _,root,params=setup;complete(root)
    assert ap.run_pipeline(payload(root,params),converter=fake_convert,validator=fake_validate,runner=lambda *_:0)==2
    assert list((root/'log').glob('analysis_methods_before_*.json'))
    assert ap.pipeline_state(root)['stage']=='error'


def test_cancel_before_r(setup):
    _,root,params=setup;(root/'log').mkdir();(root/'log'/'pipeline.cancel').touch()
    assert ap.run_pipeline(payload(root,params),converter=fake_convert,validator=fake_validate,runner=lambda *_:pytest.fail('no R'))==130
    assert ap.pipeline_state(root)['stage']=='stopped'


def test_gate_must_match_pid(setup):
    _,root,params=setup;gate=root/'gate';ip.write_json(gate,{'pid':-1,'request_id':'a'})
    job=payload(root,params);job.update(gate=str(gate),request_id='a')
    assert ap.run_pipeline(job,runner=lambda *_:pytest.fail('no R'))==2
    assert '受付' in ap.pipeline_state(root)['error']


def test_resume_detected_from_saved_conditions_without_original(setup):
    p,root,params=setup
    def runner(*_):complete(root);return 0
    assert ap.run_pipeline(payload(root,params),converter=fake_convert,validator=fake_validate,runner=runner)==0
    p.unlink();p.with_suffix('.ibd').unlink()
    follow={**params,'section_manifest':None,'input_paths':[], 'resume_from_rds':True,
        'resume_rds_paths':[str(root/'FAKE_NOT_R_DATA.rds')]}
    assert isinstance(ap.defer_analysis(follow,'initial'),ap.DeferredAnalysis)
    second=root.parent/'second';second.mkdir()
    def another_runner(script,rt):
        assert rt['section_manifest']['files'][0]['conversion_key']
        complete(second);return 0
    assert ap.run_pipeline(payload(second,follow),converter=lambda *a,**k:pytest.fail('must reuse'),validator=fake_validate,runner=another_runner)==0


def test_common_lease_identity_and_corruption(tmp_path,monkeypatch):
    path=tmp_path/'lease';monkeypatch.setattr(pc,'admission_path',lambda:path)
    assert pc.active_lease() is None
    proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    try:
        pc.record_lease(proc,tmp_path,'test-owner')
        assert pc.active_lease()['pid']==proc.pid
        info=json.loads(path.read_text());info['process_started_at']-=100;ip.write_json(path,info)
        assert pc.active_lease() is None
        assert not pc.terminate_tree(proc.pid,expected_start=info['process_started_at'],timeout=.1)
        assert proc.poll() is None
        path.write_text('{invalid')
        with pytest.raises(ip.InputPreparationError):pc.active_lease()
    finally:
        pc.terminate_tree(proc.pid,timeout=.2);proc.wait(timeout=3)


def test_tree_stop_does_not_leave_grandchild(tmp_path):
    pidfile=tmp_path/'childpid'
    code='import subprocess,sys,time,pathlib;p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]);pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(30)'
    proc=subprocess.Popen([sys.executable,'-c',code,str(pidfile)])
    try:
        deadline=time.monotonic()+5
        while not pidfile.exists() and time.monotonic()<deadline:time.sleep(.02)
        child=int(pidfile.read_text());assert psutil.pid_exists(child)
        assert pc.terminate_tree(proc.pid,expected_start=psutil.Process(proc.pid).create_time(),timeout=.3)
        proc.wait(timeout=3)
        assert not psutil.pid_exists(child) or psutil.Process(child).status()==psutil.STATUS_ZOMBIE
    finally:
        if proc.poll() is None:pc.terminate_tree(proc.pid,timeout=.2)


def test_double_start_guard_does_not_rewrite_active_run(setup,monkeypatch):
    _,root,params=setup
    from app.services.analysis_runner import start_analysis_process
    lease=root/'shared_lease.json';monkeypatch.setattr(pc,'admission_path',lambda:lease)
    ip.write_json(lease,{'pid':os.getpid(),'process_started_at':psutil.Process().create_time(),
                       'output_dir':str(root),'analyst':'active-test-job'})
    (root/'analysis_params.json').write_text('existing settings')
    (root/'log').mkdir();(root/'log'/'analysis_log.txt').write_text('existing log')
    request=ap.DeferredAnalysis(params,'initial')
    result=start_analysis_process(request,str(root))
    assert not result['success']
    assert (root/'analysis_params.json').read_text()=='existing settings'
    assert (root/'log'/'analysis_log.txt').read_text()=='existing log'
    assert request.request_path is None


def test_callback_double_click_keeps_poll_and_stop_state(monkeypatch):
    # Dash自体の動作ではなく、実コールバック本体の分岐を独立に試験する。
    import ast
    from types import SimpleNamespace
    code=Path(__file__).resolve().parents[1]/'app/callbacks/imzml_io_callbacks.py'
    tree=ast.parse(code.read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='start_imzml_job')
    node.decorator_list=[]
    sentinel=object()
    scope={'no_update':sentinel,'_active':{'x':SimpleNamespace(poll=lambda:None)},
           'dbc':SimpleNamespace(Alert=lambda *a,**k:'busy-message')}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(code),'exec'),scope)
    values=scope['start_imzml_job'](2,None,None,None,None)
    assert values==(sentinel,sentinel,'busy-message',sentinel)


@pytest.mark.parametrize("operation", ["new", "resume", "reanalysis"])
def test_preflight_pinned_input_after_original_folder_moves(setup,operation):
    # Dash依存なしで実関数本体を試験。実画面描画のE2Eではない。
    import ast
    import shutil
    from app.services.section_metadata import validate_section_manifest
    original,root,params=setup
    prepared=ip.prepare_inputs(params,cache_root=root.parent/'cache',converter=fake_convert,validator=fake_validate)
    manifest=prepared['section_manifest']
    ip.write_json(root/'analysis_params.json', {'runtime_parameters':prepared})
    shutil.rmtree(original.parent)
    code=Path(__file__).resolve().parents[1]/'app/callbacks/analysis_callbacks.py'
    tree=ast.parse(code.read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_collect_preflight_errors')
    scope={'Path':Path, 'json':json, 'validate_section_manifest':validate_section_manifest,
           'validate_data_folder':lambda *a,**k:{'ok':False,'msg':'original folder has moved'},
           'validate_rds_folder':lambda *a,**k:{'ok':True},
           'validate_output_dir':lambda *a,**k:{'ok':True},
           'validate_param':lambda *a:(True,'')}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(code),'exec'),scope)
    errors,_=scope['_collect_preflight_errors'](
        None,'tims_cluster_filter' if operation=='reanalysis' else 'tims_v8',
        str(original.parent),str(original.parent),str(root),.05,.1,.01,
        operation=='resume',str(root),str(root),
        section_manifest=None if operation=='resume' else manifest,
        section_manifest_reanalysis=manifest if operation=='reanalysis' else None)
    if operation=='new':
        assert any('データフォルダ' in e for e in errors)
    else:
        assert not errors
