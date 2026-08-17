"""宣言と実装が食い違っている 3 か所を揃える (S4 3 件)。

3 件とも「いま利用者に見える誤りは無い」ものだが、**コードやコメントが
実際には持っていない安全性を主張している**という共通点がある。
主張だけがある状態は、後から読む人を誤らせ、次の変更で本物の欠陥になる。

--------------------------------------------------------------------------
#7 プロジェクト切替の「state 破棄」が一度も実行されていない
--------------------------------------------------------------------------
プロジェクトを切り替えると「アクティブプロジェクトの state を破棄」する
コードが呼ばれる。ところが引数なしで呼ぶと、破棄対象は「いまアクティブな
プロジェクト」から決まり、それはリクエスト境界で毎回 None に戻されるため、
**関数は先頭で何もせず戻る**（完全な死にコード）。

実害はサーバのメモリに古いデータが残ることだけで、別プロジェクトのデータが
読まれることはない（state はRDSファイルのパスごとに分けて持っている）。
最大 8 件・30 分で自動的に片付く。しかし「破棄する」というコメントが
残っていると、後から読む人は破棄されている前提で設計してしまう。

破棄したい対象（いま読み込んでいる RDS）を明示して渡し、宣言どおりに動かす。

--------------------------------------------------------------------------
#8 共発現散布図に、発現量とスポットの対応検査が無い
--------------------------------------------------------------------------
発現量の行列とスポット座標の並びが食い違った場合、Feature plot は
「対応が取れませんでした」と止まるのに、**共発現散布図だけは何事も無かった
かのように“もっともらしい別の図”を出す**（行数さえ合っていれば通る）。

現状そのような食い違いが生じる経路は見当たらないので、いま誤った図が
出ているわけではない。だが同種の検査が片方にしか無いのは危険側の非対称なので、
既存の検査をこちらでも呼ぶ。

--------------------------------------------------------------------------
#9 タブとアドレスの対応表に H&E タブが無い
--------------------------------------------------------------------------
対応表に「解剖×クラスタ (H&E)」タブが無いため、そのタブを開いても
アドレスバーは直前のタブのアドレスのまま残る。あわせて、モジュール冒頭の
説明文が実在しないアドレス（/app/results・/app/history）を挙げており、
「URL 共有で同じタブが開く」とも書いているが、実装は開き直すと必ずトップへ
戻す設計になっている（これは H&E に限らず全タブ共通の意図的な仕様）。

表に 1 行足し、説明文を実装に合わせる。
"""

import inspect

import pytest
from dash import no_update


# ---------------------------------------------------------------------------
# #7 state 破棄
# ---------------------------------------------------------------------------

def test_drop_state_is_a_noop_without_a_key():
    """前提の確認: 引数なしだと（アクティブキーが無いので）何もしない。"""
    import app.callbacks.interactive_callbacks as ic

    ic.reset_active_key()
    ic._get_state("/keep/a.rds")["plot_data"] = [1]
    before = set(ic._project_states.keys())
    ic._drop_state()          # 引数なし = 死にコードだった呼び方
    assert set(ic._project_states.keys()) == before, (
        "この前提が変わったならテストの意図を見直すこと")
    ic._drop_state("/keep/a.rds")
    assert "/keep/a.rds" not in ic._project_states


def test_project_change_drops_the_loaded_state(monkeypatch):
    """★ プロジェクト切替で、いま読み込んでいる state を実際に破棄すること。"""
    import app.callbacks.interactive_callbacks as ic
    import app.callbacks.interactive_project as ip

    ic.reset_active_key()
    ic._get_state("/proj/old.rds")["plot_data"] = [1, 2, 3]

    ip.reset_interactive_on_project_change("newproj", False, "/proj/old.rds")
    assert "/proj/old.rds" not in ic._project_states, (
        "宣言どおりに state を破棄していない（引数なしの呼び出しは何もしない）")


def test_sub_project_change_drops_the_loaded_state(monkeypatch):
    """サブプロジェクト切替でも同じであること。"""
    import app.callbacks.interactive_callbacks as ic
    import app.callbacks.interactive_project as ip

    ic.reset_active_key()
    ic._get_state("/proj/old2.rds")["plot_data"] = [1]
    monkeypatch.setattr("app.services.project_manager.get_sub_project",
                        lambda p, s: None)

    ip.set_interactive_folders_from_sub_project(
        "sub1", "proj1", False, None, "/proj/old2.rds")
    assert "/proj/old2.rds" not in ic._project_states


def test_skip_reset_still_keeps_the_state():
    """リセットをスキップする経路では破棄しないこと（従来どおり）。"""
    import app.callbacks.interactive_callbacks as ic
    import app.callbacks.interactive_project as ip

    ic.reset_active_key()
    ic._get_state("/proj/keep.rds")["plot_data"] = [1]
    ip.reset_interactive_on_project_change("newproj", True, "/proj/keep.rds")
    assert "/proj/keep.rds" in ic._project_states


def test_shared_session_still_keeps_the_state():
    """共有リンクで開いている間は何も触らないこと（ver56.5 の修正を維持）。"""
    import app.callbacks.interactive_callbacks as ic
    import app.callbacks.interactive_project as ip

    ic.reset_active_key()
    ic._get_state("/proj/shared.rds")["plot_data"] = [1]
    out = ip.set_interactive_folders_from_sub_project(
        "sub1", "proj1", False,
        {"active": True, "sub_project_id": "sub1"}, "/proj/shared.rds")
    assert out == (no_update,) * 7
    assert "/proj/shared.rds" in ic._project_states


# ---------------------------------------------------------------------------
# #8 共発現散布図の対応検査
# ---------------------------------------------------------------------------

def test_coexpression_checks_the_row_alignment():
    """★ Feature plot と同じ対応検査を、共発現散布図でも行うこと。"""
    import app.callbacks.interactive_feature_lists as fl

    src = inspect.getsource(fl)
    assert "_expression_alignment_ok" in src, (
        "行数しか見ていない"
        "（並びが食い違うと、もっともらしい別の図が無警告で出る）")


def test_coexpression_refuses_a_misaligned_matrix(monkeypatch, tmp_path):
    """対応が取れないときは図を出さず、理由を出すこと。"""
    import app.callbacks.interactive_feature_lists as fl

    monkeypatch.setattr(fl, "_expression_alignment_ok", lambda cache, df: False)
    fig, msg = fl._coexpression_guard(str(tmp_path), object())
    assert fig is not None
    assert "対応が取れませんでした" in str(msg)


def test_coexpression_passes_when_aligned_or_unknown(monkeypatch, tmp_path):
    """一致(True)と判定不能(None)は従来どおり通すこと。"""
    import app.callbacks.interactive_feature_lists as fl

    for verdict in (True, None):
        monkeypatch.setattr(fl, "_expression_alignment_ok",
                            lambda cache, df, _v=verdict: _v)
        assert fl._coexpression_guard(str(tmp_path), object()) is None


# ---------------------------------------------------------------------------
# #9 タブとアドレスの対応表
# ---------------------------------------------------------------------------

def test_every_real_tab_has_an_address():
    """★ 実在するタブがすべて対応表に載っていること。"""
    from app.callbacks.tab_url_routing import _TAB_TO_PATH, _PATH_TO_TAB

    assert "hne" in _TAB_TO_PATH, "H&E タブが対応表に無い"
    # 逆引きも自動生成なので両方向そろう
    assert _PATH_TO_TAB[_TAB_TO_PATH["hne"]] == "hne"
    assert len(_PATH_TO_TAB) == len(_TAB_TO_PATH), "アドレスが重複している"


def _module_header(mod):
    """モジュール冒頭の説明（# コメントブロック）を取り出す。"""
    src = inspect.getsource(mod)
    lines = []
    for line in src.splitlines():
        if line.startswith("#"):
            lines.append(line)
        elif lines:
            break
    return "\n".join(lines)


def test_the_docstring_does_not_claim_missing_addresses():
    """説明文が実在しないアドレスを挙げていないこと。"""
    import app.callbacks.tab_url_routing as tr

    doc = _module_header(tr)
    for ghost in ("/app/results", "/app/history"):
        assert ghost not in doc, f"実在しないアドレスが説明文に残っている: {ghost}"
    for real in tr._TAB_TO_PATH.values():
        assert real in doc, f"説明文に載っていないアドレスがある: {real}"


def test_the_docstring_matches_the_deep_link_behaviour():
    """「URL 共有で同じタブが開く」と書かないこと（実装はトップへ戻す）。"""
    import app.callbacks.tab_url_routing as tr

    doc = _module_header(tr)
    src = inspect.getsource(tr._route_app_url_to_analysis)
    assert '"/"' in src, "この前提が変わったなら説明文も見直すこと"
    assert "トップ" in doc or "ランディング" in doc, (
        "開き直すとトップへ戻る仕様が説明文に書かれていない")
