"""「適用」ボタンが無反応にならないこと + タブ復元の説明を実態に合わせる。

--------------------------------------------------------------------------
E: 「適用」ボタンが空欄では無反応（成功もエラーも出ない）
--------------------------------------------------------------------------
サイドバーの「DESI/TIMS 初期設定」「出力先の既定」の欄を空にした状態で
「適用」を押すと、解析設定側の欄が何も変わらない。成功メッセージもエラーも
出ないため、**ボタンが壊れているように見える**（欄に値が入っていれば正常に動く）。

原因は `x or no_update` で、空文字が `no_update`（＝何も変えない）に化けること。

**「空欄で既存の指定を潰さない」という方針自体は正しい**ので変えない。
変えるのは「黙って終わる」ことだけで、何が起きたかを必ず伝える。

--------------------------------------------------------------------------
F: タブ復元の説明が実装と食い違う
--------------------------------------------------------------------------
`prevent_initial_call=True` は `dcc.Location` 由来の初回発火を止めないため、
`/app/interactive` のようなアドレスを直接開くと、**裏で解析画面のタブだけが
切り替わる**。ただし利用者への影響は無い（プロジェクト一覧から解析画面に入る
どの経路も、入る瞬間に開くタブを明示指定し直すため、誤ったタブは見せられない）。

実装を変えると挙動変更になるうえ実害が無いので、**説明を実態に合わせる**。
ver56.6 で同ファイルの冒頭説明を実装に合わせたので、その続き。
"""

import inspect

import pytest
from dash import no_update


# ---------------------------------------------------------------------------
# E: 適用ボタン
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_disk(monkeypatch):
    import app.callbacks.file_handlers as fh
    monkeypatch.setattr(fh, "save_last_settings", lambda d: None)


def test_apply_desi_defaults_reports_what_it_did():
    """★ 値があるときは「適用しました」と伝えること。"""
    from app.callbacks.file_handlers import apply_desi_defaults

    out = apply_desi_defaults(1, "/data", "/ann.xlsx", "/out")
    assert out[:3] == ("/data", "/ann.xlsx", "/out")
    assert "適用" in str(out[3]), f"何をしたか伝えていない: {out[3]}"


def test_apply_desi_defaults_explains_a_blank():
    """★ 空欄で無反応にならないこと（ボタンが壊れて見える）。"""
    from app.callbacks.file_handlers import apply_desi_defaults

    out = apply_desi_defaults(1, "", "", "")
    # 「空欄で潰さない」方針は維持する
    assert out[:3] == (no_update, no_update, no_update)
    assert str(out[3]), "空欄のとき何も言わない（無反応に見える）"
    assert "空" in str(out[3]), out[3]


def test_apply_desi_defaults_partial_blank():
    """一部だけ空欄なら、埋まっている分は適用しつつその旨を伝えること。"""
    from app.callbacks.file_handlers import apply_desi_defaults

    out = apply_desi_defaults(1, "/data", "", "")
    assert out[0] == "/data"
    assert out[1] is no_update and out[2] is no_update
    assert str(out[3])


def test_apply_tims_defaults_behaves_the_same():
    from app.callbacks.file_handlers import apply_tims_defaults

    filled = apply_tims_defaults(1, "/d", "/a.csv", "/o")
    assert filled[:3] == ("/d", "/a.csv", "/o") and "適用" in str(filled[3])

    blank = apply_tims_defaults(1, "", "", "")
    assert blank[:3] == (no_update,) * 3 and "空" in str(blank[3])


def test_apply_output_defaults_behaves_the_same():
    from app.callbacks.file_handlers import apply_output_defaults

    filled = apply_output_defaults(1, "/o")
    assert filled[0] == "/o" and "適用" in str(filled[1])

    blank = apply_output_defaults(1, "  ")
    assert blank[0] is no_update and "空" in str(blank[1])


def test_apply_defaults_ignore_a_stale_zero_click():
    """押されていないときは何もしないこと（従来どおり）。"""
    from app.callbacks.file_handlers import (
        apply_desi_defaults, apply_tims_defaults, apply_output_defaults)

    assert apply_desi_defaults(0, "/d", "/a", "/o")[:3] == (no_update,) * 3
    assert apply_tims_defaults(0, "/d", "/a", "/o")[:3] == (no_update,) * 3
    assert apply_output_defaults(0, "/o")[0] is no_update


def test_the_status_target_exists_in_the_layout():
    """伝える先が実在すること（存在しない宛先へ書くと丸ごと無効になる）。"""
    from app.layouts.sidebar import create_sidebar

    ids = set()

    def walk(node):
        cid = getattr(node, "id", None)
        if isinstance(cid, str):
            ids.add(cid)
        children = getattr(node, "children", None)
        if isinstance(children, (list, tuple)):
            for c in children:
                walk(c)
        elif children is not None:
            walk(children)

    walk(create_sidebar())
    assert "apply_defaults_status" in ids, (
        "適用結果の表示先がレイアウトに無い")


# ---------------------------------------------------------------------------
# F: タブ復元の説明
# ---------------------------------------------------------------------------

def test_the_initial_call_note_matches_reality():
    """★ 「初回は復元されない」と書かないこと（実際は復元される）。"""
    import app.callbacks.tab_url_routing as tr

    src = inspect.getsource(tr._sync_tab_from_url)
    # 冒頭コメントを含めたい場合に備え、関数の直前も見る
    whole = inspect.getsource(tr)
    i = whole.index("def _sync_tab_from_url")
    around = whole[i - 1200:i] + src

    assert "prevent_initial_call" in around, (
        "この前提の説明そのものが消えている")
    assert "止まらない" in around or "発火する" in around, (
        "`prevent_initial_call=True` が dcc.Location 由来の初回発火を"
        "止めないことが書かれていない")
    assert "明示" in around, (
        "誤ったタブが見せられない理由（入る経路がタブを明示指定する）が"
        "書かれていない")


def test_the_deep_link_behaviour_is_unchanged():
    """実装は変えていないこと（実害が無いので挙動は据え置き）。"""
    from app.callbacks.tab_url_routing import _route_app_url_to_analysis

    # analysis 以外から /app/* を開いたらトップへ正規化する
    assert _route_app_url_to_analysis("/app/interactive", "landing")[1] == "/"
    # 既に analysis なら何もしない
    out = _route_app_url_to_analysis("/app/interactive", "analysis")
    assert out[0] is no_update and out[1] is no_update
