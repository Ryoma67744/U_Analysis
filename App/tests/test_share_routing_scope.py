"""共有リンクを開いたとき、前のプロジェクトの値が残らないこと (ver52.3 ③)。

■ 何が起きていたか

`route_share_url` は共有トークンを解決して結果フォルダと MSI データフォルダを
store に書くが、書き方が `x or no_update` だった:

    return (
        "analysis",
        result_dir or no_update,
        data_folder or no_update,
        project_id, sub_project_id, ...
    )

`no_update` は Dash では「この Output は変更しない」を意味する。つまり
**値が空のときは直前に開いていた別プロジェクトの値がそのまま残る**。
`project_id` / `sub_project_id` は常に更新されるので、

    見出し・プロジェクト ID  → 新しい共有のもの
    MSI データフォルダ       → 前のプロジェクトのもの

という食い違いが起きる。以後の生スペクトル読み出し（キャリブレーションの
自動検出、DESI エクスポート）が**別のデータセットに当たる**。
画面には何のエラーも出ない。

`data_folder` が空になる経路は 2 つあり、どちらも珍しくない:
  - `get_sub_project` が例外を投げる（プロジェクト台帳が読めない等）
  - サブプロジェクトに `data_folder` が設定されていない

■ 直し方

「空」と「解決できなかった」を区別せず、**常に明示的な値を入れる**。
解決できなければ空文字。下流は空を「未設定」として扱うので安全側に倒れる。
黙って前の値を使い続けるより、開けないほうがましという判断。
"""

import pytest

from dash import no_update

from app.callbacks import share_callbacks as SC

# 戻り値タプルの位置（route_share_url の Output 宣言順）
I_PAGE, I_RESULT_DIR, I_DATA_FOLDER, I_PROJECT, I_SUB = 0, 1, 2, 3, 4


@pytest.fixture
def share_record():
    return {
        "token": "tok-new",
        "project_id": "proj-NEW",
        "sub_project_id": "sub-NEW",
        "result_dir": "/results/NEW",
        "integration_method": "all",
    }


@pytest.fixture
def patched(monkeypatch, share_record):
    """`/view/<token>` が `share_record` を解決するようにする。"""
    monkeypatch.setattr(SC, "get_persistent_share", lambda t: share_record)
    monkeypatch.setattr(SC, "get_share", lambda t: share_record)
    monkeypatch.setattr(SC, "_persistent_increment_view", lambda t: None)
    return share_record


def _set_sub_project(monkeypatch, fn):
    """`route_share_url` が関数内で import する get_sub_project を差し替える。"""
    import app.services.project_manager as pm
    monkeypatch.setattr(pm, "get_sub_project", fn)


class TestNoStaleFolderLeaks:
    """★ 本丸: 解決できなかったときに前の値を残さないこと。"""

    def test_resolved_data_folder_is_passed_through(self, monkeypatch, patched):
        """正常系: 解決できたらその値が入る。"""
        _set_sub_project(monkeypatch, lambda p, s: {"data_folder": "/data/NEW"})
        out = SC.route_share_url("/view/tok-new")
        assert out[I_DATA_FOLDER] == "/data/NEW"
        assert out[I_RESULT_DIR] == "/results/NEW"
        assert out[I_PAGE] == "analysis"

    def test_exception_does_not_keep_the_previous_project(self, monkeypatch, patched):
        """★ 台帳が読めないとき、前のプロジェクトのフォルダを残さないこと。

        修正前は `data_folder or no_update` だったのでここが `no_update` になり、
        直前に開いていた別プロジェクトの MSI フォルダが store に残っていた。
        """
        def _boom(project_id, sub_project_id):
            raise OSError("project registry unreadable")

        _set_sub_project(monkeypatch, _boom)
        out = SC.route_share_url("/view/tok-new")

        assert out[I_DATA_FOLDER] is not no_update, (
            "解決に失敗したとき no_update を返している。"
            "直前に開いていた別プロジェクトの MSI データフォルダが残り、"
            "以後の生スペクトル読み出しが別データセットに当たる")
        assert out[I_DATA_FOLDER] == ""
        # プロジェクト自体は新しいものに切り替わっている（＝食い違いが起きる形）
        assert out[I_PROJECT] == "proj-NEW"
        assert out[I_SUB] == "sub-NEW"

    def test_missing_sub_project_does_not_keep_the_previous_project(
            self, monkeypatch, patched):
        """サブプロジェクトが見つからない場合も同じ。"""
        _set_sub_project(monkeypatch, lambda p, s: None)
        out = SC.route_share_url("/view/tok-new")
        assert out[I_DATA_FOLDER] is not no_update
        assert out[I_DATA_FOLDER] == ""

    def test_sub_project_without_data_folder(self, monkeypatch, patched):
        """`data_folder` キーが無いサブプロジェクトでも前の値を残さない。"""
        _set_sub_project(monkeypatch, lambda p, s: {"name": "no folder here"})
        out = SC.route_share_url("/view/tok-new")
        assert out[I_DATA_FOLDER] is not no_update
        assert out[I_DATA_FOLDER] == ""

    def test_share_without_result_dir_does_not_keep_the_previous_one(
            self, monkeypatch, share_record):
        """★ `result_dir` も同じ形だった。

        共有レコードに結果フォルダが無いのに前の値が残ると、
        **新しい共有の名前で前のプロジェクトの結果が表示される**。
        """
        share_record.pop("result_dir")
        monkeypatch.setattr(SC, "get_persistent_share", lambda t: share_record)
        monkeypatch.setattr(SC, "_persistent_increment_view", lambda t: None)
        _set_sub_project(monkeypatch, lambda p, s: {"data_folder": "/data/NEW"})

        out = SC.route_share_url("/view/tok-new")
        assert out[I_RESULT_DIR] is not no_update, (
            "結果フォルダが空のとき no_update を返している。"
            "前のプロジェクトの結果が新しい共有の名前で表示される")
        assert out[I_RESULT_DIR] == ""


class TestUnresolvableTokenStillDoesNothing:
    """無効トークンでは何も書き換えない（従来どおり）。

    ここは `no_update` が正しい。共有を開いていないので、
    利用者が今見ているものを壊す理由が無い。
    """

    def test_invalid_token_leaves_everything_untouched(self, monkeypatch):
        monkeypatch.setattr(SC, "get_persistent_share", lambda t: None)
        monkeypatch.setattr(SC, "get_share", lambda t: None)
        monkeypatch.setattr(SC, "_persistent_increment_view", lambda t: None)
        out = SC.route_share_url("/view/bogus")
        assert all(v is no_update for v in out)

    def test_non_share_path_is_ignored(self):
        out = SC.route_share_url("/analysis")
        assert all(v is no_update for v in out)


# ---------------------------------------------------------------------------
# 同じ形はサブプロジェクト切替にもあった（副次調査 手順 4 で発見）
# ---------------------------------------------------------------------------
# 共有リンクだけ直しても、**通常操作のサブプロジェクト切替**に同じ漏れが残る。
# こちらのほうが踏む頻度は高い。

class TestSubProjectSwitchDoesNotLeak:
    """★ サブプロジェクトを切り替えたとき、前のフォルダが残らないこと。"""

    I_RESULT, I_DATA, I_PROJ, I_SUB = 2, 3, 4, 5

    @staticmethod
    def _call(monkeypatch, sub):
        from dash import ctx as _ctx

        import app.callbacks.project_callbacks as PC

        monkeypatch.setattr(PC, "get_sub_project", lambda p, s: sub)
        monkeypatch.setattr(
            type(_ctx), "triggered_id",
            property(lambda self: {"type": "sub_action_interactive",
                                   "index": "sub-NEW"}))
        return PC.sub_action_interactive([1], {"id": "proj-NEW"})

    def test_resolved_folders_are_passed_through(self, monkeypatch):
        out = self._call(monkeypatch, {
            "last_result_dir": "/results/NEW", "data_folder": "/data/NEW"})
        assert out[self.I_RESULT] == "/results/NEW"
        assert out[self.I_DATA] == "/data/NEW"

    def test_sub_project_without_folders_clears_them(self, monkeypatch):
        """★ 新しいサブプロジェクトにフォルダが無いとき、前の値を残さない。

        修正前は `result_dir or no_update` / `data_folder or no_update` だったので
        **切り替える前のサブプロジェクトのフォルダがそのまま残り**、
        見出しだけ新しいサブプロジェクトになる状態が作れた。
        """
        out = self._call(monkeypatch, {"name": "folders not set"})
        assert out[self.I_RESULT] is not no_update, (
            "結果フォルダが no_update。前のサブプロジェクトの結果が残る")
        assert out[self.I_DATA] is not no_update, (
            "MSI フォルダが no_update。前のサブプロジェクトのデータが残る")
        assert out[self.I_RESULT] == ""
        assert out[self.I_DATA] == ""
        # 切り替え自体は起きている（＝食い違いが生じる形）
        assert out[self.I_PROJ] == "proj-NEW"
        assert out[self.I_SUB] == "sub-NEW"
