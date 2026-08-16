"""キャリブレーションのプリセットを読み込んでも巻き戻らないこと (C13-H5)。

--------------------------------------------------------------------------
症状: プリセットを選んだ直後に、読み込み前のテーブルへ戻ってしまう
--------------------------------------------------------------------------
「プリセット読込」は 1 回の応答で 3 つを同時に書き込む。

    calibration_table_data  … プリセットに入っていた較正表
    cal_per_sample_store    … プリセットのサンプル別データ
    cal_sample_selector     … "__all__"（全サンプル共通に戻す）

3 つ目の書き込みがサンプル切替コールバック `switch_cal_sample` を発火させる。
そこは「サンプルを切り替えたのだから、切り替え前のテーブルを控えて、
切替先のテーブルを出す」処理なので、**読み込んだばかりのテーブルを
切替前の内容で上書きしてしまう**。同時に `cal_per_sample_store` も
切替前の内容で書き戻すため、プリセットのサンプル別データごと消える。

利用者から見ると「プリセットを選んだのに、前の較正表のまま」になる。
較正表は m/z の補正係数を決めるので、気づかずに解析すると
**プリセットとは違う補正が掛かる**。

--------------------------------------------------------------------------
併せて直す: テーブルの真実の在り処
--------------------------------------------------------------------------
`switch_cal_sample` は切替前のテーブルを **DataTable** (`calibration_table`)
から読んでいた。DataTable は Store (`calibration_table_data`) から
別コールバック経由で同期される「写し」なので、同じ応答で Store が
書き換わっても DataTable はまだ古いことがある。

手動編集は `recalculate_ppm_on_edit` が Store へ書き戻しているので、
**Store を読めば手動編集も含めた最新値が得られる**。写しではなく
Store を単一の真実として読む。
"""

import pytest
from dash import no_update

import app.callbacks.analysis_callbacks as ac


OLD_ROWS = [
    {"ref_mz": 100.0, "formula": "old", "obs_mz": "100.5",
     "ppm_drift": "+5000.0", "use": "Yes"},
]
PRESET_ROWS = [
    {"ref_mz": 200.0, "formula": "preset", "obs_mz": "",
     "ppm_drift": "--", "use": "Yes"},
    {"ref_mz": 300.0, "formula": "preset", "obs_mz": "",
     "ppm_drift": "--", "use": "Yes"},
]


# ---------------------------------------------------------------------------
# 本題: プリセット読込 → サンプル切替コールバックの連鎖
# ---------------------------------------------------------------------------

def test_preset_load_declares_the_selector_as_already_reset(monkeypatch):
    """★ プリセット読込は「切替前も __all__ だった」と宣言すること。

    これがあると、続けて発火する `switch_cal_sample` が
    「切替は起きていない」と判断して何も上書きしない。
    """
    monkeypatch.setattr(
        ac, "load_calibration_preset",
        lambda name: {"matrix": "DHB", "ion_mode": "Positive",
                      "calibration_table_data": PRESET_ROWS,
                      "per_sample_data": {"__all__": PRESET_ROWS},
                      "regression_mode": "poly3",
                      "search_window": 0.5, "min_peaks": 2})

    out = ac.load_cal_preset("myPreset", "Positive")
    assert out[0] == PRESET_ROWS
    assert out[6] == "__all__", "セレクタを __all__ に戻していない"
    assert out[7] == "__all__", (
        "cal_sample_selector_prev を同時に更新していない"
        "（switch_cal_sample が切替と誤認してテーブルを巻き戻す）")


def test_switch_does_nothing_when_the_sample_did_not_change():
    """★ 同じサンプルへの「切替」では何も書き換えないこと。"""
    out = ac.switch_cal_sample("__all__", "__all__", PRESET_ROWS,
                               {"__all__": PRESET_ROWS})
    assert out == (no_update, no_update, no_update), (
        f"切替が起きていないのにテーブル/ストアを書き換えている: {out}")


def test_preset_survives_the_full_chain(monkeypatch):
    """★ 読込 → 切替コールバック発火、を通してもプリセットが残ること。

    実際の連鎖を再現する。旧実装ではここで PRESET_ROWS が OLD_ROWS に
    巻き戻り、プリセットの per_sample_data も失われていた。
    """
    monkeypatch.setattr(
        ac, "load_calibration_preset",
        lambda name: {"matrix": "DHB", "ion_mode": "Positive",
                      "calibration_table_data": PRESET_ROWS,
                      "per_sample_data": {"__all__": PRESET_ROWS},
                      "regression_mode": "poly3"})

    # 利用者は直前までサンプル "S1" を編集していた
    prev_sample = "S1"
    old_store = {"__all__": OLD_ROWS, "S1": OLD_ROWS}

    loaded = ac.load_cal_preset("myPreset", "Positive")
    table_after_load, per_sample_after_load = loaded[0], loaded[5]
    new_prev = loaded[7]

    # ここでセレクタが __all__ になったことで switch_cal_sample が発火する。
    # State は「同じ応答で書き込まれた値」で届く（Dash は 1 コールバックの
    # 全 Output を反映してから下流を発火させる）。
    switched = ac.switch_cal_sample(
        "__all__", new_prev, table_after_load, per_sample_after_load)

    assert switched[0] is no_update, (
        "読み込んだばかりのテーブルを上書きしている")
    assert switched[1] is no_update, (
        "プリセットのサンプル別データを上書きしている")
    # 巻き戻っていないこと（値としても確認する）
    final_table = table_after_load if switched[0] is no_update else switched[0]
    assert final_table == PRESET_ROWS
    assert final_table != OLD_ROWS
    # 使わなかった変数を明示（prev_sample / old_store は状況説明のため）
    assert prev_sample == "S1" and old_store["__all__"] == OLD_ROWS


# ---------------------------------------------------------------------------
# 通常のサンプル切替は従来どおり動くこと
# ---------------------------------------------------------------------------

def test_switching_samples_saves_and_restores():
    """前のサンプルの表を控え、切替先の表を出すこと。"""
    store = {"__all__": PRESET_ROWS}
    table, new_store, prev = ac.switch_cal_sample(
        "S2", "S1", OLD_ROWS, store)

    assert new_store["S1"] == OLD_ROWS, "切替前のサンプルの表を控えていない"
    assert prev == "S2"
    # S2 の表は無いので __all__ から ref_mz/formula を引き継ぎ obs_mz はクリア
    assert [r["ref_mz"] for r in table] == [200.0, 300.0]
    assert all(r["obs_mz"] == "" for r in table)
    assert all(r["ppm_drift"] == "--" for r in table)


def test_switching_back_restores_the_saved_table():
    store = {"__all__": PRESET_ROWS, "S1": OLD_ROWS}
    table, _, prev = ac.switch_cal_sample("S1", "S2", PRESET_ROWS, store)
    assert table == OLD_ROWS
    assert prev == "S1"


# ---------------------------------------------------------------------------
# テーブルの真実の在り処: 写し(DataTable) ではなく Store を読むこと
# ---------------------------------------------------------------------------

def test_switch_reads_the_store_not_the_datatable():
    """★ 切替前の表は Store から読むこと。

    DataTable は Store から別コールバック経由で同期される写しなので、
    同じ応答で Store が変わっても古いままのことがある。手動編集は
    `recalculate_ppm_on_edit` が Store へ書き戻しているので Store で足りる。
    """
    import inspect

    src = inspect.getsource(ac)
    decl = src[src.index("def switch_cal_sample") - 900:
               src.index("def switch_cal_sample")]
    assert 'State("calibration_table_data", "data")' in decl, (
        "切替前の表を Store から読んでいない")
    assert 'State("calibration_table", "data")' not in decl, (
        "DataTable(写し)をまだ読んでいる")


def test_manual_edits_reach_the_store():
    """手動編集が Store に届く経路が生きていること（上の前提の裏取り）。"""
    edited = [{"ref_mz": 100.0, "obs_mz": "100.001",
               "ppm_drift": "--", "use": "Yes"}]
    out = ac.recalculate_ppm_on_edit(1, edited)
    assert out is not no_update, "手動編集が Store に反映されない"
    assert out[0]["ppm_drift"] == "+10.0"
