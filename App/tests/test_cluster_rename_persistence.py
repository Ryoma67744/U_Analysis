"""クラスタ名リネーム等の永続化が「プロジェクト別 (active key)」に
正しく保存・復元されることの回帰テスト。

ver4.7 で修正したバグ:
  apply_cluster_rename / load_saved_cluster_name_map が冒頭で
  `_set_active_key(rds_path)` を呼んでおらず、保存先 rds_path が
  `__default__`(None) に解決され interactive_settings.json に書き込まれない
  (= 適用直後はメモリ上だけ反映され、閉じると消える) 問題。

Dash の @callback はデコレート後も通常の関数として呼べるため直接呼び出す。
"""

import json

import pytest

import app.callbacks.interactive_callbacks as ic
import app.callbacks.interactive_cluster as cl


@pytest.fixture(autouse=True)
def _clear_state():
    """各テスト前後で project state と active key をクリアして隔離する。"""
    ic._project_states.clear()
    ic._state_access_time.clear()
    ic._set_active_key(None)
    yield
    ic._project_states.clear()
    ic._state_access_time.clear()
    ic._set_active_key(None)


class _FakeCtx:
    """apply_cluster_rename が参照する Dash callback context の最小モック。"""

    def __init__(self, triggered_id, states_list):
        self.triggered_id = triggered_id
        self.states_list = states_list


def _seed_loaded_project(rds_path):
    """データ読み込み後の状態を模擬: state['rds_path'] を設定する。

    実アプリでは load_stage_b/d が state['rds_path'] を埋める。永続化ヘルパは
    `_interactive_data.get('rds_path')` を参照するため、ここで同等に seed する。
    """
    ic._get_state(rds_path)["rds_path"] = rds_path


def _settings_path(rds_file):
    return rds_file.parent / "interactive_settings.json"


def test_apply_cluster_rename_persists_to_correct_project(tmp_path, monkeypatch):
    rds_a = tmp_path / "projA" / "Step2_HarmonyPCA_Result.rds"
    rds_b = tmp_path / "projB" / "Step2_HarmonyPCA_Result.rds"
    rds_a.parent.mkdir(parents=True)
    rds_b.parent.mkdir(parents=True)
    _seed_loaded_project(str(rds_a))
    _seed_loaded_project(str(rds_b))

    # プロジェクト A でクラスタ "0" を "Epithelial" にリネームして適用
    states = [
        [{"id": {"index": "0"}, "value": "Epithelial"}],  # cluster_rename_input (ALL)
        {"id": "seurat_rds_path_store", "value": str(rds_a)},
    ]
    monkeypatch.setattr(cl, "ctx", _FakeCtx("cluster_rename_apply_btn", states))

    name_map, _status = cl.apply_cluster_rename(1, None, ["Epithelial"], rds_path=str(rds_a))

    assert name_map == {"0": "Epithelial"}
    # A の interactive_settings.json にディスク保存されている (= 閉じても残る)
    assert _settings_path(rds_a).exists()
    saved = json.loads(_settings_path(rds_a).read_text(encoding="utf-8"))
    assert saved["cluster_name_map"] == {"0": "Epithelial"}
    # B には書かれていない (プロジェクト隔離)
    assert not _settings_path(rds_b).exists()


def test_load_saved_cluster_name_map_restores_per_project(tmp_path, monkeypatch):
    rds_a = tmp_path / "projA" / "res.rds"
    rds_b = tmp_path / "projB" / "res.rds"
    rds_a.parent.mkdir(parents=True)
    rds_b.parent.mkdir(parents=True)
    _seed_loaded_project(str(rds_a))
    _seed_loaded_project(str(rds_b))

    # A だけにリネームを保存
    states = [
        [{"id": {"index": "1"}, "value": "Stroma"}],
        {"id": "seurat_rds_path_store", "value": str(rds_a)},
    ]
    monkeypatch.setattr(cl, "ctx", _FakeCtx("cluster_rename_apply_btn", states))
    cl.apply_cluster_rename(1, None, ["Stroma"], rds_path=str(rds_a))

    # 「閉じる」を模擬: active key をリセット
    ic._set_active_key(None)

    # 再オープン時の復元: A は戻る、B は空のまま
    assert cl.load_saved_cluster_name_map(str(rds_a)) == {"1": "Stroma"}
    assert cl.load_saved_cluster_name_map(str(rds_b)) == {}


def test_reset_clears_only_current_project(tmp_path, monkeypatch):
    rds_a = tmp_path / "projA" / "res.rds"
    rds_a.parent.mkdir(parents=True)
    _seed_loaded_project(str(rds_a))

    apply_states = [
        [{"id": {"index": "0"}, "value": "Epi"}],
        {"id": "seurat_rds_path_store", "value": str(rds_a)},
    ]
    monkeypatch.setattr(cl, "ctx", _FakeCtx("cluster_rename_apply_btn", apply_states))
    cl.apply_cluster_rename(1, None, ["Epi"], rds_path=str(rds_a))
    assert json.loads(_settings_path(rds_a).read_text(encoding="utf-8"))["cluster_name_map"] == {"0": "Epi"}

    # リセットすると当該プロジェクトの cluster_name_map が空に上書きされる
    monkeypatch.setattr(cl, "ctx", _FakeCtx("cluster_rename_reset_btn", []))
    name_map, _status = cl.apply_cluster_rename(None, 1, None, rds_path=str(rds_a))
    assert name_map == {}
    assert json.loads(_settings_path(rds_a).read_text(encoding="utf-8"))["cluster_name_map"] == {}


# ---------------------------------------------------------------------------
# ver4.8: リネームパネルへの変更名プリフィル表示
# ---------------------------------------------------------------------------

def _collect_rename_inputs(node, acc=None):
    """生成ツリーから cluster_rename_input の {index: value} を再帰収集する。"""
    if acc is None:
        acc = {}
    if isinstance(node, (list, tuple)):
        for c in node:
            _collect_rename_inputs(c, acc)
        return acc
    comp_id = getattr(node, "id", None)
    if isinstance(comp_id, dict) and comp_id.get("type") == "cluster_rename_input":
        acc[comp_id.get("index")] = getattr(node, "value", None)
    children = getattr(node, "children", None)
    if children is not None and not isinstance(children, str):
        _collect_rename_inputs(children, acc)
    return acc


def test_rename_panel_prefills_saved_names(tmp_path):
    """クラスタ名変更パネルの入力欄に保存済みの変更名がプリフィルされる。

    populate_cluster_rename_panel が cluster_name_map を受け取った際、各入力欄に
    value=変更名 で描画されること（= 変更名が表示される）を担保する。
    """
    import pandas as pd
    rds = str(tmp_path / "res.rds")
    ic._get_state(rds)["plot_data"] = pd.DataFrame({"Cluster": [0, 0, 1, 2, 2]})

    rows = cl.populate_cluster_rename_panel(rds, {"1": "Epithelial"})
    inputs = _collect_rename_inputs(rows)

    assert inputs == {"0": "", "1": "Epithelial", "2": ""}


def test_rename_panel_empty_when_no_saved_names(tmp_path):
    """保存名が無い場合は入力欄が空（プレースホルダにIDが出るのみ）。"""
    import pandas as pd
    rds = str(tmp_path / "res.rds")
    ic._get_state(rds)["plot_data"] = pd.DataFrame({"Cluster": [0, 1]})

    rows = cl.populate_cluster_rename_panel(rds, {})
    inputs = _collect_rename_inputs(rows)

    assert inputs == {"0": "", "1": ""}


# ---------------------------------------------------------------------------
# 手法(Harmony/RPCA)ごとの独立保存（ver4.13）
# ---------------------------------------------------------------------------

def test_cluster_name_map_key():
    from app.utils import label_persistence as lp
    assert lp.cluster_name_map_key("Harmony") == "cluster_name_map::Harmony"
    assert lp.cluster_name_map_key("RPCA") == "cluster_name_map::RPCA"
    assert lp.cluster_name_map_key("") == "cluster_name_map"
    assert lp.cluster_name_map_key(None) == "cluster_name_map"


def test_cluster_name_map_per_method_independent(tmp_path):
    from app.utils import label_persistence as lp
    (tmp_path / "RDS_Files").mkdir()
    rds = str(tmp_path / "RDS_Files" / "x.rds")
    lp.save_cluster_name_map(rds, "Harmony", {"0": "Epi"})
    lp.save_cluster_name_map(rds, "RPCA", {"0": "Stroma"})
    # 同じ設定ファイルでも手法別に独立
    assert lp.load_cluster_name_map(rds, "Harmony") == {"0": "Epi"}
    assert lp.load_cluster_name_map(rds, "RPCA") == {"0": "Stroma"}


def test_cluster_name_map_legacy_fallback(tmp_path):
    from app.utils import label_persistence as lp
    (tmp_path / "RDS_Files").mkdir()
    rds = str(tmp_path / "RDS_Files" / "x.rds")
    # 旧形式（手法共有キー）のみ保存
    lp.save_interactive_settings("cluster_name_map", {"0": "Legacy"}, rds)
    # 手法別キーが無い → 旧形式にフォールバック（既存リネームを失わない）
    assert lp.load_cluster_name_map(rds, "Harmony") == {"0": "Legacy"}
    # 手法別を保存すると以後はそちらが優先、未設定の手法は旧形式のまま
    lp.save_cluster_name_map(rds, "Harmony", {"0": "New"})
    assert lp.load_cluster_name_map(rds, "Harmony") == {"0": "New"}
    assert lp.load_cluster_name_map(rds, "RPCA") == {"0": "Legacy"}
