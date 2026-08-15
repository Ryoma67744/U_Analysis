"""Methods モーダルが開閉のたびに施錠し直されることの番人。

★ ver56.5 / デバッグ総点検 §4 (C07-F1):

  Methods 文を Master Password で表示させたあと、モーダルを閉じてもう一度
  「📝 Methods 文を表示」を押すと、**パスワードを聞かれずに本文がそのまま出て**
  いた（席を離れた端末では他人にも読める）。さらに「Methods をダウンロード」は
  有効な見た目のまま、本文の Store は空なので押しても無反応という食い違いも
  起きていた。

  原因は、開閉 callback が `methods_unlock_store` を None にするだけで、
  パネルの表示状態（lock_panel / content_panel の style）とボタンの
  disabled を戻していなかったこと。解錠側が設定した表示がそのまま残っていた。
"""
import pytest

import app.callbacks.provenance_callbacks as pv


class _FakeCtx:
    def __init__(self, tid):
        self.triggered_id = tid


@pytest.fixture
def as_open(monkeypatch):
    monkeypatch.setattr("dash.ctx", _FakeCtx("btn_show_methods"))
    return pv.toggle_methods_modal(1, None, False)


@pytest.fixture
def as_close(monkeypatch):
    monkeypatch.setattr("dash.ctx", _FakeCtx("btn_methods_close"))
    return pv.toggle_methods_modal(None, 1, True)


def _assert_locked(out, expect_open):
    (is_open, unlock_store, err, lock_style, content_style,
     rendered, dl_disabled, copy_disabled) = out
    assert is_open is expect_open
    assert unlock_store is None, "解錠フラグが残っている"
    assert lock_style == {"display": "block"}, (
        "パスワード入力欄が隠れたまま。再度開いても解錠状態に見える")
    assert content_style == {"display": "none"}, (
        "本文パネルが表示されたまま。パスワード無しで Methods 文が読める")
    assert rendered is None, "本文がブラウザ側に残っている"
    assert dl_disabled is True, "ダウンロードボタンが有効なまま"
    assert copy_disabled is True, "コピーボタンが有効なまま"


class TestRelockOnEveryOpenAndClose:
    """★ 本丸: 開くときも閉じるときも施錠状態へ戻すこと。"""

    def test_reopen_is_locked(self, as_open):
        _assert_locked(as_open, expect_open=True)

    def test_close_is_locked(self, as_close):
        _assert_locked(as_close, expect_open=False)

    def test_output_count_matches(self, as_open):
        """出力の個数が宣言と一致すること（ずれると別の値が別の欄に入る）。"""
        assert len(as_open) == 8


class TestLockedHelperIsConsistent:
    """解錠処理が失敗したときの施錠値と、開閉時の施錠値が一致すること。"""

    def test_same_locked_representation(self, as_open):
        # unlock_methods._locked が返す施錠状態と同じ形であること
        (_, _, _, lock_style, content_style, rendered,
         dl_disabled, copy_disabled) = as_open
        assert (lock_style, content_style, rendered, dl_disabled, copy_disabled) == (
            {"display": "block"}, {"display": "none"}, None, True, True)
