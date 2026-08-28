"""復元した設定を、直後に走る自動切替が既定値で塗り潰す (B-1〜B-3・3 件)。

プリセット読込・サブプロジェクトの「解析」・「再解析へ送る」は、保存してあった
設定を画面へ書き戻す。ところがその同じレスポンスで `ion_mode` や
`analysis_method` も書くため、**それを Input に持つ自動切替コールバックが発火し、
復元されたばかりの値を既定値で上書きする**。

- `auto_switch_adduct` / `auto_switch_reanalysis_adduct`
  → 付加イオン (Adduct) が「イオンモードに応じた既定の組み合わせ」に戻る
- `reset_reanalysis_defaults`
  → 再解析のイオンモードと m/z 許容誤差だけが Positive / 0.01 に戻る
- `auto_switch_data_folder`
  → **データフォルダがサイドバーの既定に戻り、別の場所のデータを解析してしまう**

いずれも「✅ 読み込みました」と成功表示が出るので気づけない。

「同じ値を書くだけなら下流は発火しないのでは」という反論は成立しない。
実ブラウザ (Chromium) の最小再現で A/B を取った:

    ガード無し … 復元値 ["+H","+Na"] が ["-H"] に塗り潰される
    ガード有り … ["+H","+Na"] が残る

このリポジトリには既に同型の対処がある (`int_cal_restore_pending`,
`interactive_calibration.py`)。同じ形を、設定タブ側の 4 つの自動切替に当てる。
"""

import inspect

import pytest
from dash import no_update

import app.callbacks.file_handlers as fh


# ---------------------------------------------------------------------------
# ① 旗そのもの
# ---------------------------------------------------------------------------

def test_the_layout_declares_the_restore_flag():
    """★ 復元中を示す Store があること。"""
    from app.layouts import settings_tab
    src = inspect.getsource(settings_tab)
    assert 'id="settings_restore_pending"' in src, (
        "復元中を示す Store が無い。復元と自動切替を見分ける手段が要る")


def test_the_flag_is_cleared_after_the_restore():
    """★ 旗は復元の直後に降ろすこと。降ろし忘れると手動変更まで効かなくなる。"""
    fn = getattr(fh, "clear_settings_restore_pending", None)
    assert fn is not None, "復元中フラグを降ろすコールバックが無い"
    assert fn(True) is False, "立った旗を降ろしていない"
    assert fn(False) is no_update, (
        "降りている旗にまた False を書いている（無限に往復する）")


# ---------------------------------------------------------------------------
# ② 4 つの自動切替が旗を見る
# ---------------------------------------------------------------------------

def test_the_adduct_switch_stays_quiet_during_a_restore():
    """★ B-1: 復元中は付加イオンを既定へ振り直さないこと。"""
    assert fh.auto_switch_adduct("Negative", True) is no_update, (
        "復元中なのに付加イオンを既定で上書きしている")


def test_the_adduct_switch_still_works_when_the_user_changes_the_mode():
    """手動のイオンモード変更では従来どおり切り替わること（止めすぎない）。"""
    from app.config import adducts_for_ion_mode
    assert fh.auto_switch_adduct("Negative", False) == adducts_for_ion_mode("Negative")


def test_the_reanalysis_adduct_switch_stays_quiet_during_a_restore():
    assert fh.auto_switch_reanalysis_adduct("Negative", True) is no_update, (
        "復元中なのに再解析の付加イオンを既定で上書きしている")


def test_the_reanalysis_defaults_stay_quiet_during_a_restore():
    """★ B-2: 復元中は再解析のイオンモード・許容誤差を既定へ戻さないこと。"""
    out = fh.reset_reanalysis_defaults("desi_cluster_filter", None, True)
    assert all(v is no_update for v in out), (
        f"復元中なのに再解析パラメータを既定へ戻している: {out}")


def test_the_reanalysis_defaults_still_reset_on_a_manual_switch():
    from app.config import DEFAULT_ION_MODE, DEFAULT_TOLERANCE_MZ
    assert fh.reset_reanalysis_defaults("desi_v8", None, False) == (
        DEFAULT_ION_MODE, DEFAULT_TOLERANCE_MZ)


def test_the_data_folder_switch_stays_quiet_during_a_restore():
    """★ B-3: 復元中はデータフォルダを既定へ戻さないこと。

    ここが最も実害が大きい。出力先やしきい値は正しく戻るので気づきにくく、
    そのまま実行すると **別の場所のデータを解析してしまう**。
    """
    assert fh.auto_switch_data_folder(
        "desi_v8", None, "/defaults/desi", "/defaults/tims", True) is no_update, (
        "復元中なのにデータフォルダを既定で上書きしている")


def test_the_data_folder_switch_still_works_on_a_manual_switch():
    assert fh.auto_switch_data_folder(
        "desi_v8", None, "/defaults/desi", "/defaults/tims", False) == "/defaults/desi"


# ---------------------------------------------------------------------------
# ③ 復元する側が旗を立てる
# ---------------------------------------------------------------------------

def test_the_preset_load_raises_the_flag(monkeypatch):
    """★ プリセット読込が復元中を宣言すること。"""
    import app.callbacks.preset_callbacks as pc

    monkeypatch.setattr(pc, "load_preset",
                        lambda name: {"ion_mode": "Negative",
                                      "adduct_filter": ["+H", "+Na"]})
    out = pc.load_preset_cb(1, "p1")
    assert out[-1] is True, (
        "プリセット読込が復元中フラグを立てていない。"
        "立てないと下流の自動切替が復元値を塗り潰す")


def test_a_failed_preset_load_does_not_raise_the_flag(monkeypatch):
    """見つからないときは旗を立てないこと（自動切替を無駄に黙らせない）。"""
    import app.callbacks.preset_callbacks as pc

    monkeypatch.setattr(pc, "load_preset", lambda name: None)
    out = pc.load_preset_cb(1, "missing")
    assert out[-1] is not True, "読込に失敗したのに復元中フラグを立てている"


def test_send_to_reanalysis_raises_the_flag(monkeypatch):
    """★ 「再解析へ送る」も同じ連鎖を起こすので旗を立てること。"""
    import app.callbacks.interactive_reanalysis_bridge as br

    # ★ ver62.2: 装置の確定は `_decide_instrument`（中身 → metadata → パス）に
    #   移った。ここが見たいのは旗であって装置判定ではないので、その 1 段だけ差す。
    monkeypatch.setattr(
        "app.callbacks.interactive_data_export._decide_instrument",
        lambda inst, data_folder, path: ("DESI", "テスト"))
    out = br.send_to_reanalysis(1, "keep", ["3"], "/tmp/x/obj.rds", None, None)
    assert out[-1] is True, (
        "「再解析へ送る」が復元中フラグを立てていない。"
        "転記直後にデータフォルダが既定へ戻る")


def test_the_sub_project_analysis_button_raises_the_flag():
    """★ サブプロジェクトの「解析」も同じ（実データが要るので結線を見る）。"""
    import app.callbacks.project_callbacks as pj

    src = inspect.getsource(pj)
    i = src.index("def sub_action_new_analysis")
    head = src[:i]
    assert 'Output("settings_restore_pending", "data"' in head, (
        "サブプロジェクトの「解析」が復元中フラグを出力していない")
