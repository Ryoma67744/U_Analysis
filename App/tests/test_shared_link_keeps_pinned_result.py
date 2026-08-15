"""共有リンクが、共有した時点の結果を指し続けることの番人。

★ ver56.5 / デバッグ総点検 §4 (C13-H8):

  共有リンクを作った後にそのサブプロジェクトを解析し直すと、リンクを開いた人には
  「共有した時点の結果」ではなく **最新の結果** が表示されていた。画面上は正しい
  共有を開いたように見え、警告も出ないため、送った側も受け取った側も
  別の結果を見ていることに気づけない。

■ なぜ起きるか

  共有ルート `route_share_url` は共有時点の `result_dir` を固定して
  `interactive_result_folder` に入れる。ところが同時に
  `interactive_sub_project_select` も設定するため、その変化が
  `set_interactive_folders_from_sub_project` を発火させ、
  `sub["last_result_dir"]`（= 最新の解析結果）で上書きしていた。
"""
import pytest
from dash import no_update

import app.callbacks.interactive_project as ip


SHARED = {"active": True, "token": "T1", "kind": "persistent",
          "project_id": "P1", "sub_project_id": "SUB1"}


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    monkeypatch.setattr(ip, "_drop_state", lambda *a, **k: None)
    monkeypatch.setattr(ip, "_set_active_key", lambda *a, **k: None)


class TestSharedResultIsPinned:
    """★ 本丸: 共有中は結果フォルダを最新結果で上書きしないこと。"""

    def test_shared_sub_project_is_left_untouched(self):
        out = ip.set_interactive_folders_from_sub_project(
            "SUB1", "P1", False, SHARED)
        assert all(v is no_update for v in out), (
            "共有リンクで開いているのに結果フォルダを上書きしている。"
            "共有した時点ではなく最新の解析結果が相手に見える")

    def test_other_sub_project_still_updates(self, monkeypatch):
        """共有先が別のサブプロジェクトを選んだら通常どおり切り替わること。"""
        monkeypatch.setattr(
            "app.services.project_manager.get_sub_project",
            lambda pid, sid: {"last_result_dir": "/res/other",
                              "data_folder": "/data/other",
                              "ms_instrument": "TIMS"})
        out = ip.set_interactive_folders_from_sub_project(
            "SUB2", "P1", False, SHARED)
        assert out[0] != no_update, (
            "共有中でも、別のサブプロジェクトを選んだら切り替える必要がある")

    def test_normal_session_is_unaffected(self, monkeypatch):
        """共有でない通常操作は従来どおり最新結果を反映すること。"""
        monkeypatch.setattr(
            "app.services.project_manager.get_sub_project",
            lambda pid, sid: {"last_result_dir": "/res/latest",
                              "data_folder": "/data/x",
                              "ms_instrument": "DESI"})
        out = ip.set_interactive_folders_from_sub_project(
            "SUB1", "P1", False, None)
        assert out[0] != no_update

    def test_inactive_shared_session_is_ignored(self, monkeypatch):
        """共有セッションが終了していれば通常動作に戻ること。"""
        monkeypatch.setattr(
            "app.services.project_manager.get_sub_project",
            lambda pid, sid: {"last_result_dir": "/res/latest",
                              "data_folder": "/data/x",
                              "ms_instrument": "DESI"})
        out = ip.set_interactive_folders_from_sub_project(
            "SUB1", "P1", False, {"active": False, "sub_project_id": "SUB1"})
        assert out[0] != no_update


class TestSkipResetGuardIntact:
    """ver51.9 / C-4 で直した並びのずれを再発させていないこと。"""

    def test_skip_reset_lowers_the_flag_only(self):
        out = ip.set_interactive_folders_from_sub_project("SUB1", "P1", True, None)
        assert len(out) == 7
        assert out[5] is False, "sap_skip_reset が降りていない"
        assert out[6] is no_update, "sap_btn_wrapper.style に不正な値が入っている"
