"""スコープの取り違えと、途中で捨てられる操作を潰す (S3 5 件)。

--------------------------------------------------------------------------
#2 ブックマークの選択肢から化合物名が常に欠落する
--------------------------------------------------------------------------
ブックマークのドロップダウンは、m/z に化合物名を添えて表示する。名前の解決は
「いま見ているプロジェクトの状態」から行うが、そのプロジェクトを指し示す
`_set_active_key(rds_path)` を呼んでいなかった。

その結果、SCiLS / CSV 由来の化合物名 (プロジェクト状態にしか無い) が常に
解決できず、ブックマークだけ m/z の数字が並ぶ。DEG 表に載っている名前は
たまたま引数で渡っているので出るが、**それ以外の由来の名前は必ず落ちる**。

--------------------------------------------------------------------------
#3 選択グループの取り消しがプロジェクトを跨いで残る
--------------------------------------------------------------------------
選択グループを削除すると「取り消し」用に控えが残る。ところがプロジェクトを
切り替えても控えが消えないため、切替後に「取り消し」を押すと
**別プロジェクトの pixel ID 群が、いま開いているプロジェクトへ保存される**。

--------------------------------------------------------------------------
#4 読み込みのキャンセルが、後から来る応答で上書きされる
--------------------------------------------------------------------------
データ読み込みは 4 段(A→B→C→D)の連鎖で進む。キャンセル用の合図は
段 B の `finally` で破棄され、しかも合図の識別子が段 C・D へ渡っていなかった。

そのため段 B の途中でキャンセルしても、段 C・D はそれを知らずに走り切り、
「読み込みをキャンセルしました。」の表示を後から上書きしてしまう。
利用者から見ると **キャンセルしたのに読み込みが続いているように見える**。

--------------------------------------------------------------------------
#5 出力先が空欄のまま解析を始められてしまう
--------------------------------------------------------------------------
出力先の組み立てに空欄の検査が無く、`Path("")` は `.`(カレント)になる。
空欄のまま実行すると **アプリの作業フォルダの下に解析結果が書き出される**。
出力サブフォルダが未設定だとその場で例外になり、ボタンが死んだように見える。

--------------------------------------------------------------------------
#6 クラスタ色の編集ロックが別のクラスタに掛かる
--------------------------------------------------------------------------
色を編集すると、他の人が同時に触らないようロックを取る。ロック対象は
「操作されたクラスタ」だが、色見本(スウォッチ)から色を選んだ場合は
全ピッカーの値が書き戻されるため、発火元が先頭のクラスタに解決されてしまう。

結果、**触ってもいないクラスタがロックされ、触ったクラスタは無防備**になる。
"""

import inspect
from pathlib import Path

import pytest
from dash import no_update


# ---------------------------------------------------------------------------
# #2 ブックマークの化合物名
# ---------------------------------------------------------------------------

def test_bookmark_options_scope_to_the_open_project(monkeypatch):
    """★ 名前を解決する前に、見ているプロジェクトを指し示すこと。"""
    import app.callbacks.interactive_deg as deg

    seen = []
    monkeypatch.setattr("app.callbacks.interactive_callbacks._set_active_key",
                        lambda k: seen.append(k))
    monkeypatch.setattr(deg, "_label_from_active_state",
                        lambda f, **kw: f"{f} (name)")

    opts = deg.update_bookmark_options(["mz_100"], None, "/data/x.rds")
    assert seen == ["/data/x.rds"], (
        f"_set_active_key を呼んでいない（化合物名が解決できない）: {seen}")
    assert opts == [{"label": "mz_100 (name)", "value": "mz_100"}]


def test_bookmark_options_still_use_the_deg_annotation(monkeypatch):
    """DEG 表由来の名前は従来どおり渡ること。"""
    import app.callbacks.interactive_deg as deg

    got = {}
    monkeypatch.setattr("app.callbacks.interactive_callbacks._set_active_key",
                        lambda k: None)

    def _label(f, deg_annotation=None, style=None):
        got[f] = deg_annotation
        return f

    monkeypatch.setattr(deg, "_label_from_active_state", _label)
    deg.update_bookmark_options(
        ["mz_100"], [{"gene": "mz_100", "annotation": "Glucose"}], "/x.rds")
    assert got["mz_100"] == "Glucose"


def test_bookmark_options_empty_is_unchanged():
    import app.callbacks.interactive_deg as deg
    assert deg.update_bookmark_options([], None, "/x.rds") == []
    assert deg.update_bookmark_options(None, None, None) == []


# ---------------------------------------------------------------------------
# #3 選択グループの取り消し
# ---------------------------------------------------------------------------

def test_undo_is_dropped_when_the_project_changes(monkeypatch):
    """★ プロジェクトを切り替えたら取り消しの控えを捨てること。

    残していると、切替後の「取り消し」で別プロジェクトの pixel ID 群が
    いま開いているプロジェクトへ保存される。
    """
    import app.callbacks.interactive_selection_groups as sg_cb

    class _Ctx:
        triggered_id = "seurat_rds_path_store"

    monkeypatch.setattr(sg_cb, "ctx", _Ctx)
    monkeypatch.setattr(sg_cb.sg, "load_groups", lambda p: {"groups": []})

    out = sg_cb.mutate_selection_groups(
        "/new/project.rds", *([None] * 6), None, None, None, None, None, None,
        {"group": {"cell_ids": [1, 2, 3]}}, "/new/project.rds")
    assert out[2] is None, (
        f"プロジェクト切替で取り消しの控えが残っている: {out[2]!r}")


def test_undo_is_dropped_even_without_a_project(monkeypatch):
    import app.callbacks.interactive_selection_groups as sg_cb

    class _Ctx:
        triggered_id = "seurat_rds_path_store"

    monkeypatch.setattr(sg_cb, "ctx", _Ctx)
    out = sg_cb.mutate_selection_groups(
        None, *([None] * 6), None, None, None, None, None, None,
        {"group": {"cell_ids": [1]}}, None)
    assert out[2] is None


# ---------------------------------------------------------------------------
# #4 読み込みのキャンセル
# ---------------------------------------------------------------------------

def test_cancel_token_is_handed_to_the_later_stages():
    """★ キャンセルの合図の識別子が、後続の段へ渡ること。

    渡っていないと、後続の段はキャンセルを知らずに走り切り、
    「キャンセルしました」の表示を上書きする。
    """
    import app.callbacks.interactive_callbacks as ic

    src = inspect.getsource(ic.load_stage_b_extract)
    assert '"token": token' in src, (
        "段 B が token を後続へ渡していない")

    src_c = inspect.getsource(ic.load_stage_c_deg)
    assert "token" in src_c, "段 C が token を扱っていない"
    assert "_is_load_cancelled" in src_c, "段 C がキャンセルを確認していない"

    src_d = inspect.getsource(ic.load_stage_d_finish)
    assert "_is_load_cancelled" in src_d, "段 D がキャンセルを確認していない"
    assert "_clear_cancel_event" in src_d, (
        "段 D が合図を後片付けしていない（連鎖の最後で捨てる）")


def test_cancel_event_is_not_destroyed_mid_chain():
    """段 B の finally で合図を捨てないこと（連鎖が続くうちは生かす）。"""
    import app.callbacks.interactive_callbacks as ic

    src = inspect.getsource(ic.load_stage_b_extract)
    tail = src[src.rindex("finally:"):] if "finally:" in src else ""
    assert "_clear_cancel_event" not in tail or "if not chain_continues" in tail, (
        "段 B の finally が無条件に合図を捨てている"
        "（連鎖が続く場合は残さないと段 C/D がキャンセルを見られない）")
    # 連鎖が続く印を実際に立てていること
    assert "chain_continues = True" in src, "連鎖継続の印を立てていない"


def test_is_load_cancelled_reads_the_shared_event():
    import app.callbacks.interactive_callbacks as ic

    token = "tok-test-1"
    assert ic._is_load_cancelled(token) is False
    assert ic._is_load_cancelled(None) is False
    ev = ic._get_or_create_cancel_event(token)
    assert ic._is_load_cancelled(token) is False
    ev.set()
    assert ic._is_load_cancelled(token) is True
    ic._clear_cancel_event(token)
    # 破棄後は「キャンセルされていない」に戻る（新しい読み込みを妨げない）
    assert ic._is_load_cancelled(token) is False


def test_stage_c_stops_when_cancelled(monkeypatch):
    """★ キャンセル済みなら段 C はそこで打ち切ること。"""
    import app.callbacks.interactive_callbacks as ic

    token = "tok-test-2"
    ic._get_or_create_cancel_event(token).set()
    try:
        out = ic.load_stage_c_deg(
            {"rds_path": "/x.rds", "method": "Harmony",
             "result_folder": "", "n": 1, "token": token},
            *([None] * 10))
        assert "キャンセル" in str(out[3]), f"打ち切っていない: {out[3]}"
        assert out[4] is no_update, "後続の段を起動してしまっている"
    finally:
        ic._clear_cancel_event(token)


# ---------------------------------------------------------------------------
# #5 出力先が空欄
# ---------------------------------------------------------------------------

def test_blank_output_dir_is_refused():
    """★ 出力先が空欄なら、カレントに書かずに理由を出して止めること。"""
    import app.callbacks.analysis_callbacks as ac

    src = inspect.getsource(ac.run_analysis)
    idx = src.index("full_output_dir = str(")
    head = src[:idx]
    assert "_resolved_output_dir(output_dir)" in head, (
        "出力先の空欄検査が無い（Path('') は '.' になり、"
        "アプリの作業フォルダへ結果が書き出される）")
    assert "出力先を指定してください" in head, "理由を利用者に出していない"


def test_resolve_output_dir_helper():
    """空欄・空白のみ・None を等しく「未設定」と扱うこと。"""
    import app.callbacks.analysis_callbacks as ac

    assert ac._resolved_output_dir("") is None
    assert ac._resolved_output_dir("   ") is None
    assert ac._resolved_output_dir(None) is None
    assert ac._resolved_output_dir(" /data/out ") == "/data/out"


def test_output_subfolder_none_does_not_explode():
    """出力サブフォルダ未設定でも例外にしないこと（ボタンが死んで見える）。"""
    import app.callbacks.analysis_callbacks as ac

    src = inspect.getsource(ac.run_analysis)
    assert "Path(output_dir) / output_subfolder)" not in src, (
        "output_subfolder が None のとき TypeError になる形が残っている")


# ---------------------------------------------------------------------------
# #6 クラスタ色の編集ロック
# ---------------------------------------------------------------------------

def test_color_lock_listens_to_the_swatch_too():
    """★ 色見本から選んだ場合も、そのクラスタが発火元になること。"""
    import app.callbacks.interactive_spatial as sp

    src = inspect.getsource(sp)
    i = src.index("def acquire_cluster_color_lock")
    decl = src[i - 800:i]
    assert "cluster_color_swatch" in decl, (
        "色見本を Input に入れていない"
        "（発火元が先頭クラスタに解決され、別クラスタがロックされる）")


def test_color_lock_uses_the_triggered_index(monkeypatch):
    """発火元の index でロックを取ること（既存の挙動を壊していない確認）。"""
    import app.callbacks.interactive_spatial as sp

    class _Ctx:
        triggered_id = {"type": "cluster_color_swatch", "index": "3"}

    monkeypatch.setattr(sp, "ctx", _Ctx)
    got = {}
    monkeypatch.setattr(
        "app.callbacks.edit_lock_callbacks.acquire_lock_for_callback",
        lambda rds, field, sid: got.update(field=field))

    ids = [{"type": "cluster_color_lock_indicator", "index": str(i)}
           for i in range(4)]
    out = sp.acquire_cluster_color_lock(
        [None] * 4, [None] * 8, "/x.rds", "sess", ids)
    assert got["field"] == "cluster_color:3", got
    assert out == [no_update] * 4


def test_color_lock_ignores_a_non_pattern_trigger(monkeypatch):
    import app.callbacks.interactive_spatial as sp

    class _Ctx:
        triggered_id = "something_else"

    monkeypatch.setattr(sp, "ctx", _Ctx)
    ids = [{"type": "cluster_color_lock_indicator", "index": "0"}]
    assert sp.acquire_cluster_color_lock(
        [None], [None], "/x.rds", "s", ids) == [no_update]
