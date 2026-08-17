"""「押しても何も起きない」の裏で起きていた副作用を潰す (F-C02-1 / C05-3 / C10-4)。

3 件とも「画面上は無反応なのに、内部では望まないことが起きている」型である。

--------------------------------------------------------------------------
F-C02-1: 追加フォルダのサンプルだけ切片で絞り込めない
--------------------------------------------------------------------------
TIMS のサンプル一覧は「基準フォルダ + 追加フォルダ」から作られるのに、
切片 (annotation) のチェックボックスを組み立てる側は**基準フォルダしか
見ていなかった**。追加フォルダにあるサンプルはファイルパスの解決に失敗し、
`continue` で黙って読み飛ばされる。

その結果、サンプル一覧には出ているのに切片の選択欄には現れず、
そのサンプルだけ切片で絞り込めない（＝全切片が解析に入る）。
画面には何のエラーも出ない。

--------------------------------------------------------------------------
C05-3: 拡大ボタンを押すと、裏で全プロットが再描画される
--------------------------------------------------------------------------
データが読めていない状態で「拡大」ボタンを押すと、モーダルは開かない。
ところが処理は `is_open=False` を**書き込んで**終わっていた。モーダルは
元から閉じているので見た目は何も変わらないが、`is_open` の書き込みは
閉鎖ハンドラを発火させる。そこでは「開いたときのラベル位置」の控えが
無いため「変更があった」と判定され、統合 UMAP・サンプル別 UMAP・
Spatial 全タイル・Feature Plot の 5 つの重いコールバックが一斉に走る。

つまり「押しても何も起きない」ように見えて、押すたびに全再描画が走っていた。

--------------------------------------------------------------------------
C10-4: 診断のスピナーが永久に回り続ける
--------------------------------------------------------------------------
PreFlight 診断の進捗ポーリングは、プロセスの控えが消えていると
`no_update` を返して次の回を待っていた。控えが消えるのは
「別の画面で完了処理が済んだ」か「サーバが再起動した」ときで、どちらも
このタブから実行中に戻ることはない。結果、スピナーを表示したまま
1.5 秒間隔のポーリングが止まらなくなっていた。
"""

from pathlib import Path

import pytest
from dash import no_update


# ---------------------------------------------------------------------------
# F-C02-1
# ---------------------------------------------------------------------------

def _write_parquet(path, annotations):
    """切片ラベル入りの TIMS parquet を作る。

    `read_parquet_annotations` は「ラベルが 1 種類だけ＝領域を分けていない」と
    見なして空を返す（ver55.0）。したがって取り違えを検出するには、
    どのファイルにも 2 種類以上のラベルを入れる必要がある。
    """
    import pandas as pd
    assert len(set(annotations)) >= 2, "ラベルが 1 種類だと選択肢に出ない"
    pd.DataFrame({"annotation": annotations,
                  "mz_100": [1.0] * len(annotations)}).to_parquet(path)


def test_find_tims_file_path_multi_searches_every_folder(tmp_path):
    from app.services.data_manager import find_tims_file_path_multi

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "s1.parquet").write_bytes(b"x")
    (b / "s2.parquet").write_bytes(b"x")

    assert find_tims_file_path_multi([str(a), str(b)], "s1") == str(a / "s1.parquet")
    assert find_tims_file_path_multi([str(a), str(b)], "s2") == str(b / "s2.parquet")
    assert find_tims_file_path_multi([str(a), str(b)], "s3") is None
    # 壊れた入力でも落ちない
    assert find_tims_file_path_multi([], "s1") is None
    assert find_tims_file_path_multi(None, "s1") is None
    assert find_tims_file_path_multi([None, "", str(a)], "s1") == str(a / "s1.parquet")


def test_annotation_selector_covers_extra_folders(tmp_path):
    """★ 追加フォルダのサンプルにも切片チェックボックスが出ること。"""
    pytest.importorskip("pyarrow")
    from app.callbacks.file_handlers import update_annotation_selector

    base, extra = tmp_path / "base", tmp_path / "extra"
    base.mkdir()
    extra.mkdir()
    _write_parquet(base / "s_base.parquet", ["ROI_A", "ROI_B"])
    _write_parquet(extra / "s_extra.parquet", ["ROI_C", "ROI_D"])

    children, store = update_annotation_selector(
        ["s_base", "s_extra"], str(base), None, "tims_v8", [str(extra)])

    assert store == ["ROI_A", "ROI_B", "ROI_C", "ROI_D"], (
        f"追加フォルダのサンプルの切片が拾えていない: {store}")
    rendered = str(children)
    assert "s_base" in rendered and "s_extra" in rendered


def test_annotation_selector_without_extra_folders_is_unchanged(tmp_path):
    """追加フォルダが無いときの結果は従来どおりであること。"""
    pytest.importorskip("pyarrow")
    from app.callbacks.file_handlers import update_annotation_selector

    base = tmp_path / "base"
    base.mkdir()
    _write_parquet(base / "s_base.parquet", ["ROI_A", "ROI_B"])

    for extra in (None, [], [""]):
        _, store = update_annotation_selector(
            ["s_base"], str(base), None, "tims_v8", extra)
        assert store == ["ROI_A", "ROI_B"], extra


def test_annotation_selector_still_hidden_for_non_tims(tmp_path):
    from app.callbacks.file_handlers import update_annotation_selector
    assert update_annotation_selector(
        ["s"], str(tmp_path), "desi_v8", None, None) == ([], None)


# ---------------------------------------------------------------------------
# C05-3
# ---------------------------------------------------------------------------

def test_expand_with_no_data_does_not_touch_the_modal(monkeypatch):
    """★ 拡大するものが無いとき、is_open を書き込まないこと。

    書き込むと閉鎖ハンドラが発火し、裏で 5 つの重い再描画が走る。
    """
    import app.callbacks.interactive_fullscreen as fs

    monkeypatch.setattr(fs, "_interactive_data", {})

    class _Ctx:
        triggered_id = "expand_umap_btn"

    monkeypatch.setattr(fs, "ctx", _Ctx)
    out = fs.toggle_fullscreen(1, None, None, None, None, None, None, None,
                               None, None, None, None)
    assert out == (no_update, no_update, no_update), (
        f"モーダルの状態を書き換えている: {out}")


def test_expand_without_a_trigger_does_not_touch_the_modal(monkeypatch):
    import app.callbacks.interactive_fullscreen as fs

    class _Ctx:
        triggered_id = None

    monkeypatch.setattr(fs, "ctx", _Ctx)
    assert fs.toggle_fullscreen(None, None, None, None, None, None, None,
                                None, None, None, None, None) == \
        (no_update, no_update, no_update)


def test_close_handler_would_have_fired_a_full_redraw():
    """この修正が必要だった理由を固定する（番人が空振りでないことの確認）。

    もし `is_open=False` を書き込むと、控えが無いので閉鎖ハンドラは
    「変更があった」と判定してトリガーを進める＝全再描画が走る。
    """
    from app.callbacks.interactive_fullscreen import on_fullscreen_close
    trigger, snapshot = on_fullscreen_close(False, 3, {"umap": {}}, None)
    assert trigger == 4, "閉鎖ハンドラが再描画トリガーを進めない形に変わった"


def test_close_handler_still_skips_when_nothing_changed():
    """本来の最適化（変更が無ければ再描画しない）は維持されていること。"""
    from app.callbacks.interactive_fullscreen import on_fullscreen_close
    import json
    positions = {"umap_integrated": {"C1": {"x": 1, "y": 2}}}
    snap = json.dumps(positions, sort_keys=True, default=str)
    assert on_fullscreen_close(False, 3, positions, snap) == (no_update, no_update)


# ---------------------------------------------------------------------------
# C10-4
# ---------------------------------------------------------------------------

def test_preflight_poll_stops_when_the_process_is_gone(tmp_path, monkeypatch):
    """★ 追えなくなったら理由を出して止めること（永久ポーリングをやめる）。"""
    import app.callbacks.preflight_callbacks as pf

    monkeypatch.setitem(pf._preflight_process_state, "process", None)
    store = {"out_dir": str(tmp_path), "status_file": str(tmp_path / "st.json")}

    children, new_store, disabled = pf.poll_preflight(5, store)
    assert disabled is True, "ポーリングが止まらない（スピナーが回り続ける）"
    assert new_store["status"] == "unknown"
    assert "追えなく" in str(children)


def test_preflight_poll_renders_results_left_by_another_tab(tmp_path, monkeypatch):
    """別の画面が先に完了処理を済ませていたら、結果を表示して止めること。"""
    import json

    import app.callbacks.preflight_callbacks as pf

    monkeypatch.setitem(pf._preflight_process_state, "process", None)
    (tmp_path / "diagnostics.json").write_text(
        json.dumps({"reductions": []}), encoding="utf-8")
    monkeypatch.setattr(pf, "_render_diagnostics_table",
                        lambda data, methods: ("TABLE", "harmony"))

    children, new_store, disabled = pf.poll_preflight(
        5, {"out_dir": str(tmp_path), "rds_methods": {}})
    assert children == "TABLE"
    assert new_store["status"] == "done"
    assert disabled is True


def test_preflight_poll_keeps_waiting_while_running(tmp_path, monkeypatch):
    """実行中は従来どおり待つこと（早すぎる打ち切りをしない）。"""
    import app.callbacks.preflight_callbacks as pf

    class _Proc:
        pass

    monkeypatch.setitem(pf._preflight_process_state, "process", _Proc())
    monkeypatch.setattr(pf, "check_process_completion", lambda *a, **kw: None)
    assert pf.poll_preflight(1, {"out_dir": str(tmp_path)}) == \
        (no_update, no_update, no_update)


def test_preflight_poll_ignores_an_empty_store():
    import app.callbacks.preflight_callbacks as pf
    assert pf.poll_preflight(1, None) == (no_update, no_update, no_update)
    assert pf.poll_preflight(1, {}) == (no_update, no_update, no_update)
