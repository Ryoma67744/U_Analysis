"""サブプロジェクトを切り替えたとき、前のサブプロジェクトの値が残らないことの番人。

★ ver56.5 / デバッグ総点検 §4.2 で確定した不具合 (C01-1):

  サブプロジェクトカードの「解析」ボタンは `sub_action_new_analysis` を呼ぶが、
  保存済み設定が無い項目に `x or no_update` を返していた。`no_update` は
  「画面を変更しない」という意味なので、**直前に開いていた別サブプロジェクトの
  データフォルダ・出力先がそのまま残る**。

  一方 `current_sub_project_id` は必ず新しい値に更新されるため、

      見出し・サブプロジェクト ID → B
      データフォルダ / 出力先     → A のまま

  という食い違いが起きる。解析画面には「今どのサブプロジェクトを操作中か」の
  表示が無いので気づけず、そのまま実行すると **A のデータを解析し、A の出力先に
  書き、記録上は B の解析**になる (実ブラウザで再現済み)。

  まったく同じ問題が ver52.3 に `sub_action_interactive` (:747-755) で修正されており、
  コードにその旨のコメントまで残っている。**解析設定側にだけ適用されていなかった。**

■ 既定値の出典

閾値系を「明示値」に変えるには既定値が要るが、**発明しない**。
`app/utils/validation.param_default()` が `PARAM_BOUNDS` の宣言を単一出典として
返すので、それを使う (ver52.3 ⑤ が「同じ入力に別々の既定値」を潰すために作った仕組み)。
"""
import pytest

from app.utils.validation import param_default


class _FakeCtx:
    """`ctx.triggered_id` だけを持つ最小のコールバック文脈。"""

    def __init__(self, triggered_id):
        self.triggered_id = triggered_id


@pytest.fixture
def pc():
    import app.callbacks.project_callbacks as module
    return module


def _call_switch(pc, monkeypatch, sub_id, sub_data, settings):
    """`sub_action_new_analysis` を実際に呼んで戻り値タプルを得る。"""
    monkeypatch.setattr(
        pc, "ctx", _FakeCtx({"type": "sub_action_analysis", "index": sub_id}))
    monkeypatch.setattr(pc, "get_sub_project", lambda pid, sid: sub_data)
    monkeypatch.setattr(pc, "get_sub_project_settings", lambda pid, sid: settings)
    return pc.sub_action_new_analysis([1], {"id": "PROJ"})


# 戻り値タプルの並び (callback の Output 順)
IDX = {
    "current_page": 0, "main_tabs": 1, "current_sub_project_id": 2,
    "data_folder": 3, "output_dir": 4, "analysis_method": 5,
    "analysis_method_tims": 6, "annotation_path": 7, "p_thresh": 8,
    "logfc_thresh": 9, "ion_mode": 10, "tolerance_mz": 11,
    "resume_rds": 12, "rds_folder": 13, "reanalysis_data_folder": 14,
    "rds_path": 15, "filter_mode": 16, "target_clusters": 17,
    "reanalysis_p_thresh": 18, "reanalysis_logfc_thresh": 19,
    "reanalysis_ion_mode": 20, "reanalysis_tolerance_mz": 21,
}


class TestNoResidueOnSwitch:
    """★ 本丸: 設定の無いサブプロジェクトへ切り替えても前の値が残らない。"""

    def test_folders_are_explicit_even_when_unset(self, pc, monkeypatch):
        """データフォルダ・出力先が `no_update` にならないこと。

        `no_update` を返すと画面は前のサブプロジェクトのパスを表示し続け、
        そのまま実行すると別データで解析してしまう。
        """
        from dash import no_update
        out = _call_switch(
            pc, monkeypatch, "SUB_B",
            sub_data={"id": "SUB_B", "name": "B"},   # data_folder / output_dir 無し
            settings={})                              # 保存済み設定も無し

        assert out[IDX["data_folder"]] is not no_update, (
            "設定の無いサブプロジェクトで data_folder が no_update になっている。"
            "前のサブプロジェクトのフォルダが画面に残り、別データで解析される。")
        assert out[IDX["output_dir"]] is not no_update, (
            "output_dir が no_update になっている。前の出力先に書き込まれる。")
        assert out[IDX["data_folder"]] == ""
        assert out[IDX["output_dir"]] == ""

    def test_saved_values_are_restored(self, pc, monkeypatch):
        """保存済みの値はきちんと復元されること (修正で壊していない)。"""
        out = _call_switch(
            pc, monkeypatch, "SUB_A",
            sub_data={"id": "SUB_A", "data_folder": "/data/A", "output_dir": "/out/A"},
            settings={"p_thresh": 0.01, "logfc_thresh": 1.0, "ion_mode": "Negative"})

        assert out[IDX["data_folder"]] == "/data/A"
        assert out[IDX["output_dir"]] == "/out/A"
        assert out[IDX["p_thresh"]] == 0.01
        assert out[IDX["logfc_thresh"]] == 1.0
        assert out[IDX["ion_mode"]] == "Negative"

    def test_thresholds_fall_back_to_declared_defaults(self, pc, monkeypatch):
        """★ 閾値も `no_update` ではなく「宣言された既定値」に戻ること。

        既定値は `PARAM_BOUNDS` を単一出典とする (ver52.3 ⑤ の仕組みを再利用)。
        ここを no_update のままにすると、前のサブプロジェクトの閾値で
        解析してしまう。
        """
        from dash import no_update
        out = _call_switch(
            pc, monkeypatch, "SUB_B",
            sub_data={"id": "SUB_B"}, settings={})

        for key in ("p_thresh", "logfc_thresh", "tolerance_mz",
                    "reanalysis_p_thresh", "reanalysis_logfc_thresh",
                    "reanalysis_tolerance_mz"):
            assert out[IDX[key]] is not no_update, (
                f"{key} が no_update。前のサブプロジェクトの値が残る")
            assert out[IDX[key]] == param_default(key), (
                f"{key} は PARAM_BOUNDS の既定値 {param_default(key)} に戻すこと"
                f" (実際: {out[IDX[key]]})")

    def test_zero_threshold_is_preserved_not_treated_as_missing(self, pc, monkeypatch):
        """★ 保存値 0 が既定値に化けないこと。

        `0` は「絞り込まない」という正当な指定。`x or default` の形だと
        0 が falsy のため既定値へすり替わる (§4.2・C03-1 と同じ型)。
        """
        out = _call_switch(
            pc, monkeypatch, "SUB_A",
            sub_data={"id": "SUB_A"},
            settings={"logfc_thresh": 0, "p_thresh": 0, "tolerance_mz": 0})

        assert out[IDX["logfc_thresh"]] == 0, "0 が既定値に化けている"
        assert out[IDX["p_thresh"]] == 0
        assert out[IDX["tolerance_mz"]] == 0

    def test_text_settings_do_not_leak_between_sub_projects(self, pc, monkeypatch):
        """文字列系の設定 (対象クラスタ・RDS パス等) も残らないこと。"""
        from dash import no_update
        out = _call_switch(
            pc, monkeypatch, "SUB_B", sub_data={"id": "SUB_B"}, settings={})

        for key in ("annotation_path", "rds_folder", "reanalysis_data_folder",
                    "rds_path", "target_clusters"):
            assert out[IDX[key]] is not no_update, (
                f"{key} が no_update。前のサブプロジェクトの値が残る")

    def test_sub_project_id_is_always_updated(self, pc, monkeypatch):
        """サブプロジェクト ID は常に新しい値になること (食い違いの片側)。"""
        out = _call_switch(
            pc, monkeypatch, "SUB_B", sub_data={"id": "SUB_B"}, settings={})
        assert out[IDX["current_sub_project_id"]] == "SUB_B"
        assert out[IDX["current_page"]] == "analysis"
        assert out[IDX["main_tabs"]] == "settings"


class TestGuardsStillHold:
    """誤発火では何も変えないこと (既存の防御を壊していない)。"""

    def test_no_click_changes_nothing(self, pc, monkeypatch):
        from dash import no_update
        monkeypatch.setattr(
            pc, "ctx", _FakeCtx({"type": "sub_action_analysis", "index": "SUB_B"}))
        out = pc.sub_action_new_analysis([None], {"id": "PROJ"})
        assert all(v is no_update for v in out)

    def test_missing_sub_project_changes_nothing(self, pc, monkeypatch):
        from dash import no_update
        monkeypatch.setattr(
            pc, "ctx", _FakeCtx({"type": "sub_action_analysis", "index": "GONE"}))
        monkeypatch.setattr(pc, "get_sub_project", lambda pid, sid: None)
        out = pc.sub_action_new_analysis([1], {"id": "PROJ"})
        assert all(v is no_update for v in out)


class TestModalsDoNotDiscardInputSilently:
    """★ §4.2 / C01-3・C01-4: モーダルが内容を黙って捨てないこと。

    以前は toggle 側が confirm で**無条件に閉じ**、handle 側は検証失敗で
    何もしなかったため、「閉じたのに保存されず理由も出ない」無音の失敗になっていた。
    """

    def test_create_toggle_does_not_listen_to_confirm(self, pc):
        """作成モーダルの開閉が confirm を Input に持たないこと。"""
        import ast
        import inspect
        src = inspect.getsource(pc)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef)
                    and node.name == "toggle_create_sub_modal"):
                continue
            deco = ast.dump(node.decorator_list[0])
            assert "confirm_create_sub_project" not in deco, (
                "toggle_create_sub_modal がまだ confirm を Input に持っている。"
                "検証前にモーダルが閉じてしまう")
            return
        pytest.fail("toggle_create_sub_modal が見つからない")

    def test_edit_toggle_does_not_listen_to_confirm(self, pc):
        """編集モーダルの開閉が confirm を Input に持たないこと。"""
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(pc))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef)
                    and node.name == "toggle_edit_sub_modal"):
                continue
            deco = ast.dump(node.decorator_list[0])
            assert "confirm_edit_sub_project" not in deco, (
                "toggle_edit_sub_modal がまだ confirm を Input に持っている。"
                "検証前に閉じて入力欄がクリアされ、編集内容が失われる")
            return
        pytest.fail("toggle_edit_sub_modal が見つからない")

    def test_create_keeps_modal_open_and_explains_when_name_missing(self, pc):
        """タイトル未入力なら開いたままで理由を出すこと。"""
        out = pc.handle_create_sub_project(
            1, {"id": "PROJ"}, "", None, "", "", "", [], "", "", "", 0)
        assert out[-2] is True, "検証失敗なのにモーダルを閉じている"
        assert "タイトル" in str(out[-1]), f"理由が表示されない: {out[-1]!r}"

    def test_create_closes_and_clears_on_success(self, pc, monkeypatch):
        """正常時は閉じてフォームをクリアすること。"""
        created = {}
        monkeypatch.setattr(pc, "create_sub_project",
                            lambda **kw: created.update(kw))
        out = pc.handle_create_sub_project(
            1, {"id": "PROJ"}, "  NEW  ", "2026-01-01", "", "", "", [],
            "/d", "/o", "memo", 3)
        assert created["name"] == "NEW", "前後の空白が除去されていない"
        assert out[-2] is False, "成功したのにモーダルが閉じない"
        assert out[-1] == ""
        assert out[9] == 4, "リフレッシュカウンタが進んでいない"

    def test_edit_keeps_modal_open_and_preserves_input_when_name_missing(self, pc):
        """★ 本丸: タイトルを空にして保存しても編集内容が消えないこと。"""
        out = pc.handle_edit_sub_project(
            1, {"id": "PROJ"}, "SUB_A", "   ", None, "", "", "", [],
            "/edited", "/out", "この内容が失われてはいけない", 0)
        refresh, is_open, msg = out
        assert is_open is True, "検証失敗なのにモーダルを閉じている(入力が消える)"
        assert "タイトル" in str(msg), f"理由が表示されない: {msg!r}"
        from dash import no_update
        assert refresh is no_update, "保存していないのにリフレッシュしている"

    def test_edit_saves_and_closes_on_success(self, pc, monkeypatch):
        """正常時は保存して閉じること。"""
        saved = {}
        monkeypatch.setattr(pc, "update_sub_project",
                            lambda pid, sid, payload: saved.update(payload))
        out = pc.handle_edit_sub_project(
            1, {"id": "PROJ"}, "SUB_A", " NAME ", None, "", "", "", [],
            "/edited", "/out", "memo", 7)
        refresh, is_open, msg = out
        assert saved["name"] == "NAME"
        assert saved["data_folder"] == "/edited"
        assert saved["memo"] == "memo"
        assert refresh == 8
        assert is_open is False
        assert msg == ""
