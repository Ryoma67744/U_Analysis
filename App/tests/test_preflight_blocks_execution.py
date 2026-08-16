"""入力チェックでエラーが出たら実行を止めること (C03-4)。

--------------------------------------------------------------------------
症状: 赤いエラーと「解析を開始しました」の緑が同時に出る
--------------------------------------------------------------------------
データフォルダの指定が間違っている状態で「解析実行」を押すと、画面上部に
赤い枠で「入力チェックでエラーが見つかりました: データフォルダ:
フォルダが見つかりません」と出る。ところが**同時に**「解析を開始しました」の
緑の通知も出て、進捗バーが動き出す。

利用者はエラーなのか動いているのか判断できず、しばらく待たされた末に
R 側のエラーログで失敗を知ることになる。

原因は、入力チェックが**表示しかしていない**ことにある。チェックの結果を
実行本体が読む経路が無く、実行本体はクリック数しか見ていなかった。
さらにチェック自体が「解析実行」ボタンにしか繋がっておらず、
①「reduction のみ作成」④「reduction を再利用」では走りもしなかった。

--------------------------------------------------------------------------
止めすぎないこと — これが最も避けたい失敗
--------------------------------------------------------------------------
出力先の「親フォルダが見つかりません」は、実行本体が `mkdir(parents=True)` で
**自分で作って解消する**。これを止める対象に入れると、**今まで正常に動いて
いた実行まで止まってしまう**。

そこで検査結果を 2 つに分ける:

    blocking … 実行しても必ず失敗するもの（データフォルダが無い、
               書き込み権限が無い、数値が範囲外 など）→ 実行を止める
    advisory … 実行すれば解消するもの（出力先の親フォルダが無い）
               → 知らせるだけで止めない

--------------------------------------------------------------------------
表示と判断を同じ関数から出す
--------------------------------------------------------------------------
表示側と実行側が別々に検査すると、また食い違う。`_collect_preflight_errors()`
を 1 つ置き、表示用コールバックと実行本体の両方がそれを呼ぶ。
（実行本体が「別のコールバックが書いた Store」を読む形にすると、同じクリックで
両方が走るため **前回の検査結果を読んでしまう**。自前で呼ぶのが確実。）
"""

import inspect

import pytest
from dash import no_update

import app.callbacks.analysis_callbacks as ac


@pytest.fixture
def good(tmp_path):
    """すべて正常な入力一式。"""
    data = tmp_path / "data"
    data.mkdir()
    (data / "s1.txt").write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    return dict(
        desi_method="desi_v8", tims_method=None,
        data_folder=str(data), reanalysis_data_folder="",
        output_dir=str(out),
        p_thresh=0.05, logfc_thresh=0.25, tolerance_mz=0.01,
        resume_rds=False, rds_folder="", rds_folder_reanalysis="",
    )


# ---------------------------------------------------------------------------
# 検査そのもの: 止めるものと知らせるだけのものを分ける
# ---------------------------------------------------------------------------

def test_clean_input_produces_nothing(good):
    blocking, advisory = ac._collect_preflight_errors(**good)
    assert blocking == [] and advisory == [], (blocking, advisory)


def test_missing_data_folder_blocks(good, tmp_path):
    good["data_folder"] = str(tmp_path / "nope")
    blocking, advisory = ac._collect_preflight_errors(**good)
    assert any("データフォルダ" in e for e in blocking), blocking


def test_out_of_range_number_blocks(good):
    good["p_thresh"] = 99
    blocking, _ = ac._collect_preflight_errors(**good)
    assert blocking, "範囲外の数値で止まらない"


def test_blank_output_blocks(good):
    good["output_dir"] = "   "
    blocking, _ = ac._collect_preflight_errors(**good)
    assert any("出力先" in e for e in blocking), blocking


def test_missing_parent_of_output_is_only_advisory(good, tmp_path):
    """★ 実行時に自動作成される件で止めないこと（止めすぎの防止）。"""
    good["output_dir"] = str(tmp_path / "a" / "b" / "c")
    blocking, advisory = ac._collect_preflight_errors(**good)
    assert not blocking, (
        f"実行すれば解消する件で止めている（正常な実行まで止まる）: {blocking}")
    assert advisory, "知らせてもいない"
    assert "作成" in " ".join(advisory), advisory


def test_reanalysis_checks_its_own_folders(good, tmp_path):
    good["desi_method"] = "desi_cluster_filter"
    good["reanalysis_data_folder"] = str(tmp_path / "nope")
    blocking, _ = ac._collect_preflight_errors(**good)
    assert any("データフォルダ" in e for e in blocking), blocking


def test_rds_folder_is_checked_only_when_resuming(good, tmp_path):
    good["rds_folder"] = str(tmp_path / "nope")
    blocking, _ = ac._collect_preflight_errors(**good)
    assert not any("RDS" in e for e in blocking), "途中再開でないのに検査している"

    good["resume_rds"] = True
    blocking, _ = ac._collect_preflight_errors(**good)
    assert any("RDS" in e for e in blocking), blocking


# ---------------------------------------------------------------------------
# 表示側と実行側が同じ関数を使うこと
# ---------------------------------------------------------------------------

def test_display_and_execution_share_the_check():
    """★ 表示用と実行本体が同じ検査を呼ぶこと（食い違いの防止）。"""
    for fn in (ac.preflight_validation, ac.run_analysis):
        assert "_collect_preflight_errors" in inspect.getsource(fn), (
            f"{fn.__name__} が検査を共有していない")


def test_execution_does_not_read_a_sibling_store():
    """実行本体が別コールバックの書いた Store を頼らないこと。

    同じクリックで両方が走るため、Store 経由だと**前回の検査結果**を読む。
    """
    src = inspect.getsource(ac.run_analysis)
    assert "preflight_errors_store" not in src, (
        "同じクリックで走る兄弟コールバックの結果を読んでいる（前回値になる）")


def test_validation_runs_for_every_start_button():
    """★ ①④ でも入力チェックが走ること。"""
    src = inspect.getsource(ac)
    i = src.index("def preflight_validation")
    decl = "\n".join(line for line in src[i - 1500:i].splitlines()
                     if "#" not in line)
    for btn in ("run_analysis", "btn_make_reduction", "btn_run_downstream"):
        assert f'Input("{btn}"' in decl, f"{btn} でチェックが走らない"


# ---------------------------------------------------------------------------
# 表示: 止める件と知らせるだけの件を区別して見せる
# ---------------------------------------------------------------------------

def _validate(monkeypatch, kwargs, trigger="run_analysis"):
    class _Ctx:
        triggered_id = trigger

    monkeypatch.setattr(ac, "ctx", _Ctx)
    return ac.preflight_validation(1, 1, 1, **kwargs)


def test_display_is_hidden_when_clean(monkeypatch, good):
    children, style = _validate(monkeypatch, good)
    assert children == "" and style == {"display": "none"}


def test_display_shows_an_error_for_blocking(monkeypatch, good, tmp_path):
    good["data_folder"] = str(tmp_path / "nope")
    children, style = _validate(monkeypatch, good)
    assert style["display"] == "block"
    assert "エラー" in str(children)


def test_display_shows_a_notice_for_advisory_only(monkeypatch, good, tmp_path):
    """止めない件は「エラー」と言わないこと（赤と緑の同時表示の元凶）。"""
    good["output_dir"] = str(tmp_path / "a" / "b")
    children, style = _validate(monkeypatch, good)
    assert style["display"] == "block"
    assert "エラー" not in str(children), (
        f"止めないのに『エラー』と表示している: {children}")


# ---------------------------------------------------------------------------
# 実行本体: 止めること / 止めすぎないこと
# ---------------------------------------------------------------------------

def _run(monkeypatch, good, trigger="run_analysis", **over):
    class _Ctx:
        triggered_id = trigger

    monkeypatch.setattr(ac, "ctx", _Ctx)
    monkeypatch.setattr(ac, "_output_has_existing_results", lambda t: False)
    monkeypatch.setattr(ac, "save_last_settings", lambda d: None)

    from tests.test_downstream_overwrite_is_confirmed import _RUN_ARGS
    kwargs = dict(_RUN_ARGS)
    kwargs.update({
        "n_clicks": 1, "downstream_clicks": 0,
        "desi_method": good["desi_method"], "tims_method": good["tims_method"],
        "data_folder": good["data_folder"],
        "reanalysis_data_folder": good["reanalysis_data_folder"],
        "output_dir": good["output_dir"], "p_thresh": good["p_thresh"],
        "logfc_thresh": good["logfc_thresh"],
        "tolerance_mz": good["tolerance_mz"],
        "resume_rds": good["resume_rds"], "rds_folder": good["rds_folder"],
        "rds_folder_reanalysis": good["rds_folder_reanalysis"],
    })
    kwargs.update(over)
    return ac.run_analysis(**kwargs)


def test_run_refuses_when_the_input_is_invalid(monkeypatch, good, tmp_path):
    """★ エラーが出ている状態では解析を起動しないこと。"""
    good["data_folder"] = str(tmp_path / "nope")
    out = _run(monkeypatch, good)
    assert out[7] is True, "通知が出ていない"
    assert "入力" in str(out[6]) or "確認" in str(out[6]), out[6]
    assert out[1] is True, "進捗の更新を有効にしたまま（実行したように見える）"
    assert "開始しました" not in str(out[6]), (
        "エラーなのに『開始しました』と出している")


def test_run_proceeds_when_only_advisory(monkeypatch, good, tmp_path):
    """★ 実行すれば解消する件では止めないこと（止めすぎの防止）。"""
    good["output_dir"] = str(tmp_path / "auto" / "created")
    out = _run(monkeypatch, good)
    assert "入力チェック" not in str(out[6]), (
        f"自動作成される出力先で止めている: {out[6]}")


def test_run_still_proceeds_on_clean_input(monkeypatch, good):
    """正常な入力では従来どおり進むこと。"""
    out = _run(monkeypatch, good)
    assert out != (no_update,) * 10
    assert "入力チェック" not in str(out[6])
