"""新しい群の保存・CSVボタンを実Dash HTTP経路で確認する。"""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_group_controls_save_and_export_through_registered_http_callbacks(tmp_path):
    source = Path(__file__).resolve().parents[1]
    # 既存の認証・登録・HTTP検証プローブを再利用し、群の操作を追記する。
    tree = ast.parse((source / "tests/test_settings_runtime_callbacks.py").read_text(encoding="utf8"))
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "_PROBE" for t in n.targets))
    probe = ast.literal_eval(assignment.value)
    extra = r'''
import pandas as pd
from app.services.section_metadata import build_section_manifest
from app.services.section_group_metadata import overlay_result_metadata
from app.callbacks import interactive_callbacks as ic
manifest=build_section_manifest([{"path":p,"available_rois":["ROI1"],"roi_role":"section"} for p in paths])
records=[]
for i,f in enumerate(manifest["files"]):
    section=f["sections"][0]
    section.update(subject_id="ID"+str(i),group="Ctrl" if i==0 else "Treatment")
    records.append(dict(CellID="cell"+str(i),Sample="same_ROI1",source_file_id=f["file_id"],
        source_pixel_id="001",**section,UMAP_1=float(i),UMAP_2=float(i+1),Cluster=str(i),SpatialX=i,SpatialY=i))
rds=root/"results"/"RDS_Files"/"PCA.rds"
rds.parent.mkdir(parents=True)
rds.write_bytes(b"RDS unchanged")
params=rds.parent.parent/"analysis_params.json"
params.write_text(json.dumps({"section_manifest":manifest}),encoding="utf8")
before=params.read_bytes()
ic._set_active_key(str(rds))
df=pd.DataFrame(records)
ic._interactive_data.update(plot_data=df,rds_path=str(rds))
got=post(find_key("int_section_group_table"),[str(rds),0],[],"seurat_rds_path_store.data")
rows=got["int_section_group_table"]["data"]
assert len(rows)==2 and got["int_section_group_filter"]["value"]==["Ctrl","Treatment"]
rows[0]["group"]="Changed"
got=post(find_key("int_section_group_status"),[1],[rows,str(rds),0],"int_section_group_save.n_clicks")
assert got["int_section_group_updated"]["data"]==1
assert "保存しました" in got["int_section_group_status"]["children"]
got=post(find_key("int_section_metadata_download"),[1],[str(rds),["Changed"]],"int_section_metadata_export.n_clicks")
csv=got["int_section_metadata_download"]["data"]
assert csv["filename"]=="pixel_section_metadata.csv"
assert "Changed" in csv["content"] and "Treatment" not in csv["content"]
assert rds.read_bytes()==b"RDS unchanged" and params.read_bytes()==before
assert overlay_result_metadata(df,rds.parent/"Harmony.rds").iloc[0]["group"]=="Changed"
assert df.iloc[0]["group"]=="Ctrl"
help_response=client.get("/help/analysis")
assert help_response.status_code==200
help_text=help_response.get_data(as_text=True)
assert "切片数に応じた自動解析" in help_text and "群・個体情報" in help_text
report["group_buttons"]={"load":"passed","save":"passed","csv":"passed","shared_sidecar":"passed","help":"passed"}
'''
    needle = 'Path(sys.argv[1]).write_text(json.dumps(report,ensure_ascii=False),encoding="utf-8")'
    assert needle in probe
    probe = probe.replace(needle, extra + "\n" + needle)
    app_root = tmp_path / "App"
    app_root.mkdir()
    for folder in ("app", "Script", "DB"):
        shutil.copytree(source / folder, app_root / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    env = dict(os.environ, PYTHONPATH=str(app_root), FLASK_SECRET_KEY="test-"+"x"*40,
               MASTER_PASSWORD="runtime-test-password", INITIAL_PASSWORD_B="runtime-share")
    out = tmp_path / "report.json"
    completed = subprocess.run([sys.executable, "-c", probe, str(out)], cwd=app_root,
                               env=env, text=True, capture_output=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(out.read_text(encoding="utf8"))
    assert all(value == "passed" for value in report["group_buttons"].values())
    assert report["orphans"] == [] and report["duplicate_callbacks"] == []
