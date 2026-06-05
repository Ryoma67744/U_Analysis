"""サブプロジェクト/プロジェクト削除の所有権ガード挙動テスト（ver4.15）。

ログイン中のユーザーであれば作成者(created_by)に関わらず削除できる(enforce_owner=False)
ことと、enforce_owner=True では非所有者の削除がブロックされることを検証する。
ファイルI/O・ロックは monkeypatch で差し替え、メタデータ操作のみを対象にする。
"""

import contextlib

import pytest

import app.services.project_manager as pm


def _setup(monkeypatch):
    data = {"projects": [{
        "id": "p1",
        "name": "Proj",
        "created_by": "someone_else",      # 現在ユーザーとは別人が作成
        "sub_projects": [
            {"id": "s1", "name": "Sub1"},
            {"id": "s2", "name": "Sub2"},
        ],
    }]}
    saved = {}
    monkeypatch.setattr(pm, "_projects_lock", contextlib.nullcontext())
    monkeypatch.setattr(pm, "_load_all", lambda: data)
    monkeypatch.setattr(pm, "_save_all", lambda d: saved.update(data=d))
    monkeypatch.setattr(pm, "_safe_current_user", lambda: "current_user")
    if hasattr(pm, "_write_meta_to_folder"):
        monkeypatch.setattr(pm, "_write_meta_to_folder", lambda *a, **k: None)
    return data, saved


def test_delete_sub_project_enforce_owner_false_deletes_regardless_of_owner(monkeypatch):
    data, saved = _setup(monkeypatch)
    ok = pm.delete_sub_project("p1", "s1", enforce_owner=False)
    assert ok is True
    # s1 が消え s2 が残る（作成者が別人でも削除できる）
    assert [s["id"] for s in data["projects"][0]["sub_projects"]] == ["s2"]
    assert "data" in saved  # projects.json への保存が呼ばれた


def test_delete_sub_project_enforce_owner_true_blocks_non_owner(monkeypatch):
    data, _saved = _setup(monkeypatch)
    # created_by != current_user なので所有権ガードで拒否される
    with pytest.raises(pm.ProjectAccessDenied):
        pm.delete_sub_project("p1", "s1", enforce_owner=True)
    assert [s["id"] for s in data["projects"][0]["sub_projects"]] == ["s1", "s2"]
