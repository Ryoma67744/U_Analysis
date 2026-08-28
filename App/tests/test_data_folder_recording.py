"""サブプロジェクトに記録される生データフォルダの回帰テスト。

ver62.4 修正:
  TIMS のプロジェクトなのに、サブプロジェクトの `data_folder` に
  `/app/Data/DESI/Data`（DESI 生データの共有ルート＝サイドバーの既定値）が
  記録され、データ出力が無関係なフォルダを見て失敗していた。

  解析自体は正しいフォルダで走っており、**記録された値だけが壊れていた**。
  値が「作られ → 記録され → 固定される」流れの各段に 1 つずつ穴がある。

  1. auto_switch_data_folder が `desi_val or tims_val` で装置を決めていた。
     選択欄の既定が非対称（DESI="desi_v8" / TIMS=None）なので DESI が構造的に
     勝ち、TIMS の作業中に DESI の既定フォルダが書き込まれる。
  2. ジョブ台帳が、再解析では使っていないメイン欄をそのまま記録していた。
  3. analysis_finalizer が中身を見ずにサブプロジェクトへ上書きしていた
     （＝正しかった値を塗り潰す最後の一撃）。
"""

import pandas as pd
import pytest

from app.services.data_manager import has_msi_data


# ---------------------------------------------------------------------------
# フォルダ作成ヘルパー
# ---------------------------------------------------------------------------

def _parquet_folder(root, name="raw"):
    d = root / name
    d.mkdir(parents=True)
    pd.DataFrame({"id": [1], "x": [1.0], "y": [1.0], "700.1234": [10.0],
                  "annotation": ["s1"]}).to_parquet(d / "s1.parquet")
    return d


def _txt_folder(root, name="raw", stem="s1"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stem}.txt").write_text("a\tb\n1\t2\n", encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# has_msi_data — 判定の単一出典
# ---------------------------------------------------------------------------

def test_has_msi_data_accepts_parquet(tmp_path):
    assert has_msi_data(_parquet_folder(tmp_path)) is True


def test_has_msi_data_accepts_desi_txt(tmp_path):
    assert has_msi_data(_txt_folder(tmp_path)) is True


def test_has_msi_data_accepts_csv_and_xlsx(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    (d / "s1.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    assert has_msi_data(d) is True
    d2 = tmp_path / "raw2"
    d2.mkdir()
    pd.DataFrame({"a": [1]}).to_excel(d2 / "s1.xlsx", index=False)
    assert has_msi_data(d2) is True


def test_has_msi_data_rejects_a_folder_of_only_subfolders(tmp_path):
    """報告された症状そのもの: 装置別データのルートを渡された状態。"""
    root = tmp_path / "Data" / "DESI" / "Data"
    for i in range(5):
        (root / f"dataset{i}").mkdir(parents=True)
    assert has_msi_data(root) is False


def test_has_msi_data_rejects_empty_and_missing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert has_msi_data(empty) is False
    assert has_msi_data(tmp_path / "nope") is False
    assert has_msi_data("") is False
    assert has_msi_data(None) is False


def test_export_helper_delegates_to_the_same_judgement(tmp_path):
    """出力側と保存側で「生データがある」の意味がずれないこと。"""
    import app.callbacks.interactive_data_export as de
    d = _parquet_folder(tmp_path)
    assert de._has_any_msi_files(d) is has_msi_data(d) is True
    assert de._has_any_msi_files(tmp_path / "nope") is False


# ---------------------------------------------------------------------------
# analysis_finalizer — 正しかった値を塗り潰さない（最優先の門番）
# ---------------------------------------------------------------------------

def _run_link(monkeypatch, tmp_path, data_folder):
    """_link_to_project を走らせ、update_sub_project に渡された更新を返す。"""
    import app.services.analysis_finalizer as af

    seen = {}
    monkeypatch.setattr("app.services.project_manager.save_sub_project_result_dir",
                        lambda p, s, d: None)
    monkeypatch.setattr("app.services.project_manager.update_sub_project",
                        lambda p, s, updates: seen.update(updates))
    out = tmp_path / "result"
    out.mkdir(exist_ok=True)
    result = {"errors": []}
    af._link_to_project(out, {"project_id": "p1", "sub_project_id": "s1",
                              "data_folder": str(data_folder)}, result)
    return seen, result


def test_finalizer_does_not_overwrite_with_a_folder_that_has_no_data(
        monkeypatch, tmp_path):
    """生データの無いフォルダで既存の登録を塗り潰さないこと。

    これが無かったために、TIMS プロジェクトの登録が DESI ルートで
    上書きされ、以後データ出力が失敗し続けた。
    """
    root = tmp_path / "Data" / "DESI" / "Data"
    for i in range(5):
        (root / f"dataset{i}").mkdir(parents=True)

    seen, result = _run_link(monkeypatch, tmp_path, root)

    assert "data_folder" not in seen, (
        "生データの無いフォルダでサブプロジェクトの登録を上書きしている")
    assert not result["errors"], "解析の完了処理自体は失敗させないこと"


def test_finalizer_still_saves_a_real_data_folder(monkeypatch, tmp_path):
    raw = _parquet_folder(tmp_path)
    seen, _ = _run_link(monkeypatch, tmp_path, raw)
    assert seen.get("data_folder") == str(raw)


# ---------------------------------------------------------------------------
# auto_switch_data_folder — 発火元で決める（実行順に依存しない）
# ---------------------------------------------------------------------------

def _switch(monkeypatch, triggered, desi_val, tims_val, restore_pending=False):
    import app.callbacks.file_handlers as fh

    class _Ctx:
        triggered_id = triggered

    monkeypatch.setattr(fh, "ctx", _Ctx)
    return fh.auto_switch_data_folder(
        desi_val, tims_val, "/app/Data/DESI/Data", "/app/Data/TIMS/Data",
        restore_pending)


def test_tims_selection_does_not_pick_the_desi_default(monkeypatch):
    """TIMS を選んだ瞬間に DESI の既定が入らないこと（本件の供給源）。

    `analysis_method` の既定は "desi_v8" で常に真なので、従来の
    `desi_val or tims_val` では TIMS を選んだ 1 周目に必ず DESI が勝っていた。
    """
    assert _switch(monkeypatch, "analysis_method_tims",
                   "desi_v8", "tims_v8") == "/app/Data/TIMS/Data"
    assert _switch(monkeypatch, "analysis_method_tims",
                   "desi_v8", "tims_cluster_filter") == "/app/Data/TIMS/Data"


def test_exclusive_clear_does_not_clobber_the_folder(monkeypatch):
    """排他クリアの再発火（DESI 側が None になる）では書き換えないこと。"""
    from dash import no_update
    assert _switch(monkeypatch, "analysis_method", None, "tims_v8") is no_update


def test_desi_selection_still_picks_the_desi_default(monkeypatch):
    assert _switch(monkeypatch, "analysis_method",
                   "desi_v8", None) == "/app/Data/DESI/Data"
    assert _switch(monkeypatch, "analysis_method",
                   "desi_cluster_filter", None) == "/app/Data/DESI/Data"


def test_restore_pending_is_still_respected(monkeypatch):
    """ver58.1 の保護（復元中は触らない）を壊していないこと。"""
    from dash import no_update
    assert _switch(monkeypatch, "analysis_method_tims", "desi_v8", "tims_v8",
                   restore_pending=True) is no_update


def test_unknown_trigger_falls_back_to_the_old_reading(monkeypatch):
    """起動直後など発火元が特定できないときは従来どおり。"""
    assert _switch(monkeypatch, None,
                   "desi_v8", None) == "/app/Data/DESI/Data"


def test_direct_call_outside_a_callback_does_not_raise():
    """callback の外から直接呼んでも例外にならないこと。

    ★ PR #172 のレビュー指摘。`ctx.triggered_id` は callback 実行中以外では
    `MissingCallbackContextException` を投げる。この関数はテストから直接
    呼ばれる契約があり（`test_restore_is_not_overwritten_by_defaults`）、
    素で参照すると**その契約を壊す**。しかも全件実行では他のテストが張った
    コンテキストに救われて通ってしまい、単独実行でしか落ちなかった。
    """
    import app.callbacks.file_handlers as fh
    # ctx を差し替えず、素の状態で呼ぶ（＝ callback コンテキストが無い）
    assert fh.auto_switch_data_folder(
        "desi_v8", None, "/defaults/desi", "/defaults/tims",
        False) == "/defaults/desi"


def test_trigger_id_is_read_defensively():
    """コンテキストが無ければ None を返し、例外を投げないこと。"""
    from app.callbacks.file_handlers import _current_trigger_id
    assert _current_trigger_id() is None


@pytest.mark.parametrize("trigger,desi,tims,expected", [
    ("analysis_method_tims", "desi_v8", "tims_v8", "tims_v8"),
    ("analysis_method", "desi_v8", "tims_v8", "desi_v8"),
    ("analysis_method", None, "tims_v8", None),
    (None, "desi_v8", None, "desi_v8"),          # 発火元不明 → 従来の解釈
    (None, None, "tims_v8", "tims_v8"),
])
def test_selected_analysis_method(trigger, desi, tims, expected):
    """判定そのものは純関数として単独で確かめられること。"""
    from app.callbacks.file_handlers import _selected_analysis_method
    assert _selected_analysis_method(desi, tims, trigger) == expected


# ---------------------------------------------------------------------------
# _effective_data_folder — 台帳には「実際に読んだフォルダ」を載せる
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("analysis_type,expected", [
    ("tims_cluster_filter", "/rean"),
    ("desi_cluster_filter", "/rean"),
    ("tims_v8", "/main"),
    ("desi_v8", "/main"),
])
def test_effective_data_folder(analysis_type, expected):
    from app.callbacks.analysis_callbacks import _effective_data_folder
    assert _effective_data_folder(analysis_type, "/main", "/rean") == expected


def test_effective_data_folder_falls_back_when_reanalysis_field_is_blank():
    from app.callbacks.analysis_callbacks import _effective_data_folder
    assert _effective_data_folder("tims_cluster_filter", "/main", "") == "/main"
    assert _effective_data_folder("tims_cluster_filter", "", "") == ""
