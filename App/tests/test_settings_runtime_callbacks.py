"""実 Dash 登録と HTTP 応答で設定ボタンを検証する。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


_PROBE = r'''
import json,sys
from collections import Counter
from pathlib import Path
from app.main import app
app._setup_server()
nodes=[]
def walk(node):
    if isinstance(node,(list,tuple)):
        for x in node: walk(x)
    elif hasattr(node,"to_plotly_json"):
        d=node.to_plotly_json();p=d.get("props",{});nodes.append((d.get("type"),p))
        for x in p.values():walk(x)
walk(app.layout)
inputs={d["id"] for cb in app._callback_list for d in cb.get("inputs",[])}
buttons=[p for t,p in nodes if t in ("Button","button") and isinstance(p.get("id"),str)]
report={"callback_count":len(app.callback_map),"buttons":len(buttons),
        "orphans":[p["id"] for p in buttons if p["id"] not in inputs and not p.get("href")],
        "duplicate_callbacks":[key for key,n in Counter(cb["output"] for cb in app._callback_list).items() if n>1]}
client=app.server.test_client()
assert client.post("/login",data={"analyst_name":"検証担当","password":"runtime-test-password"}).status_code==302
assert client.get("/_dash-layout").status_code==200
assert client.get("/_dash-dependencies").status_code==200
def find_key(output_id):
    for key,meta in app.callback_map.items():
        outputs=meta["output"] if isinstance(meta["output"],list) else [meta["output"]]
        if any(o.component_id==output_id for o in outputs):return key
    raise AssertionError("出力未登録: "+output_id)
def post(key,values,states,changed,outputs=None):
    meta=app.callback_map[key]
    if outputs is None:
        out=meta["output"]
        outputs=([{"id":o.component_id,"property":o.component_property} for o in out]
                 if isinstance(out,list) else {"id":out.component_id,"property":out.component_property})
    assert len(values)==len(meta["inputs"])
    assert len(states)==len(meta["state"])
    payload={"output":key,"outputs":outputs,
        "inputs":[dict(d,value=v) for d,v in zip(meta["inputs"],values)],
        "state":[dict(d,value=v) for d,v in zip(meta["state"],states)],"changedPropIds":[changed]}
    r=client.post("/_dash-update-component",json=payload)
    assert r.status_code==200,(key,r.status_code,r.get_data(as_text=True))
    return r.get_json()["response"]
def pid(kind,scope,path):return {"type":kind,"scope":scope,"index":path}
def encoded(d):return json.dumps(d,sort_keys=True,separators=(",",":"),ensure_ascii=False)
root=Path(sys.argv[1]).parent
paths=[str(root/"first"/"same.parquet"),str(root/"second"/"same.parquet")]
catalog=[{"path":p,"available_rois":["ROI1","ROI2"]} for p in paths]
selection_key=next(k for k,m in app.callback_map.items() if not isinstance(m["output"],list)
    and isinstance(m["output"].component_id,dict) and m["output"].component_id.get("type")=="section_roi_check")
report["scope_results"]={}
for scope,suffix in [("initial",""),("reanalysis","_reanalysis")]:
    check=pid("section_roi_check",scope,paths[0])
    options=[{"label":x,"value":x} for x in ["ROI1","ROI2"]]
    empty=post(selection_key,[0,1],[options],encoded(pid("section_select_none",scope,paths[0]))+".n_clicks",
        outputs={"id":check,"property":"value"})[encoded(check)]["value"]
    full=post(selection_key,[1,1],[options],encoded(pid("section_select_all",scope,paths[0]))+".n_clicks",
        outputs={"id":check,"property":"value"})[encoded(check)]["value"]
    assert empty==[] and full==["ROI1","ROI2"]
    ids=[pid("section_roi_check",scope,p) for p in paths]
    roleids=[pid("section_roi_role",scope,p) for p in paths]
    key=find_key("section_summary"+suffix)
    got=post(key,[catalog,[["ROI1"],["ROI2"]],["section","section"],None,0],
        [[],ids,roleids,None,[],""],"section_catalog_store"+suffix+".data")
    manifest=got["section_manifest_store"+suffix]["data"]
    rows=got["section_group_table"+suffix]["data"]
    assert [f["rois"] for f in manifest["files"]]==[["ROI1"],["ROI2"]]
    got=post(key,[catalog,[["ROI1"],["ROI2"]],["section","section"],None,1],
        [rows,ids,roleids,manifest,[0],"Ctrl"],"section_apply_group"+suffix+".n_clicks")
    rows=got["section_group_table"+suffix]["data"]
    manifest=got["section_manifest_store"+suffix]["data"]
    assert [r["group"] for r in rows]==["Ctrl",""]
    assert "1" in got["section_group_status"+suffix]["children"]
    cleared=post(key,[catalog,[empty,empty],["section","section"],None,1],
        [rows,ids,roleids,manifest,[],"Ctrl"],encoded(ids[0])+".value")
    assert cleared["section_group_table"+suffix]["data"]==[]
    assert all(f["selection_mode"]=="none" for f in cleared["section_manifest_store"+suffix]["data"]["files"])
    report["scope_results"][scope]={"all":full,"none":empty,"groups":[r["group"] for r in rows]}
key=find_key("ion_mode_panel")
report["db_off"]=post(key,[[],False],[],"use_annotation_check.value")
report["db_on"]=post(key,[["db"],False],[],"use_annotation_check.value")
report["calibration_only"]=post(key,[[],True],[],"calibration_enable.value")
Path(sys.argv[1]).write_text(json.dumps(report,ensure_ascii=False),encoding="utf-8")
'''


@pytest.fixture(scope="module")
def runtime_report(tmp_path_factory):
    """設定や認証の保存先を独立させ、利用者データを変更しない。"""
    source=Path(__file__).resolve().parents[1]
    root=tmp_path_factory.mktemp("settings-runtime")
    app_root=root/"App"
    app_root.mkdir()
    for folder in ("app","Script","DB"):
        shutil.copytree(source/folder,app_root/folder,ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
    env=dict(os.environ)
    env.update(PYTHONPATH=str(app_root),FLASK_SECRET_KEY="test-"+"x"*40,
               MASTER_PASSWORD="runtime-test-password",INITIAL_PASSWORD_B="runtime-share")
    path=root/"report.json"
    r=subprocess.run([sys.executable,"-c",_PROBE,str(path)],cwd=app_root,env=env,
                     text=True,capture_output=True,timeout=60)
    assert r.returncode==0,r.stdout+r.stderr
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_initial_button_has_a_live_callback(runtime_report):
    assert runtime_report["callback_count"]>390
    assert runtime_report["buttons"]>190
    assert runtime_report["orphans"]==[]
    assert runtime_report["duplicate_callbacks"]==[]


@pytest.mark.parametrize("scope",["initial","reanalysis"])
def test_select_all_none_and_bulk_group_buttons_return_results(runtime_report,scope):
    assert runtime_report["scope_results"][scope]=={"all":["ROI1","ROI2"],"none":[],"groups":["Ctrl",""]}


def test_optional_database_and_calibration_panels_follow_actual_inputs(runtime_report):
    assert runtime_report["db_off"]["ion_mode_panel"]["style"]=={"display":"none"}
    assert runtime_report["db_on"]["ion_mode_panel"]["style"]=={}
    assert runtime_report["db_on"]["db_annotation_settings"]["style"]=={}
    assert runtime_report["calibration_only"]["ion_mode_panel"]["style"]=={}
    assert runtime_report["calibration_only"]["db_annotation_settings"]["style"]=={"display":"none"}
