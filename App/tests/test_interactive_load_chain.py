"""インタラクティブ解析「データ読み込み」4リンク連鎖の単体テスト。

R を起動せず、Seurat bridge / DEG / キャリブレーションを mock して
- 各段の進捗メッセージ／トリガが正しく出る
- 各失敗モードで原因メッセージが出て連鎖が止まる (trigger=no_update)
- プロジェクト別 state (active key) が隔離される
- DEG 未検出・キャリブ失敗が非致命である
ことを検証する。Dash の @callback はデコレート後も通常の関数として呼べる。
"""

import pytest
from dash import no_update
from dash.exceptions import PreventUpdate

import app.callbacks.interactive_callbacks as ic


@pytest.fixture(autouse=True)
def _clear_state():
    """各テスト前後で project state と active key をクリアして隔離する。"""
    ic._project_states.clear()
    ic._state_access_time.clear()
    ic._set_active_key(None)
    yield
    ic._project_states.clear()
    ic._state_access_time.clear()
    ic._set_active_key(None)


def _alert_text(component):
    """dbc.Alert / 文字列から表示テキストを連結して取り出す。"""
    children = getattr(component, "children", component)
    if isinstance(children, list):
        parts = []
        for c in children:
            if isinstance(c, str):
                parts.append(c)
            else:
                sub = getattr(c, "children", "")
                parts.append(sub if isinstance(sub, str) else "")
        return " ".join(parts)
    if isinstance(children, str):
        return children
    return str(children)


def _fake_extract_result(sample_df):
    return {
        "plot_data": sample_df,
        "cluster_stats": None,
        "features_list": [f"mz_{i}" for i in range(20)],
        "meta": {"n_cells": len(sample_df), "n_clusters": 3, "samples": ["S1", "S2"]},
        "cache_dir": "/tmp/fake_cache",
    }


# ---------------------------------------------------------------------------
# Link A: 即時パント + 検証
# ---------------------------------------------------------------------------

def test_link_a_no_method_shows_error_and_no_trigger():
    out = ic.load_stage_a_show_progress(1, None, None, "")
    container_style, label, bar, animated, viz, data_info, trigger, token = out
    assert container_style == ic._PROGRESS_HIDE
    assert trigger is no_update  # 連鎖を起こさない
    assert "統合手法を選択" in _alert_text(data_info)


def test_link_a_missing_rds_path_shows_error(tmp_path):
    rds_map = {"Harmony": str(tmp_path / "does_not_exist.rds")}
    out = ic.load_stage_a_show_progress(1, "Harmony", rds_map, str(tmp_path))
    *_, data_info, trigger, token = out
    assert trigger is no_update
    assert "RDSファイルが見つかりません" in _alert_text(data_info)


def test_link_a_valid_shows_progress_and_triggers(tmp_path):
    rds = tmp_path / "Step2_HarmonyPCA_Result.rds"
    rds.write_bytes(b"dummy")
    out = ic.load_stage_a_show_progress(3, "Harmony", {"Harmony": str(rds)}, str(tmp_path))
    container_style, label, bar, animated, viz, data_info, trigger, token = out
    assert container_style == ic._PROGRESS_SHOW
    assert "RDSデータを抽出中" in label
    assert animated is True
    assert viz == ic._PROGRESS_HIDE  # 既存可視化は隠す
    assert trigger["rds_path"] == str(rds)
    assert trigger["method"] == "Harmony"
    assert trigger["n"] == 3
    # ver4.19: キャンセル用 token が発行され、trigger にも載る
    assert token
    assert trigger["token"] == token


# ---------------------------------------------------------------------------
# Link B: 抽出 + 各エラー分岐
# ---------------------------------------------------------------------------

def _trigger(rds_path, method="Harmony", result_folder="", n=1):
    return {"rds_path": rds_path, "method": method,
            "result_folder": result_folder, "n": n}


def test_link_b_success_stores_state_and_advances(monkeypatch, sample_df):
    rds_path = "/proj/A.rds"
    monkeypatch.setattr(ic._bridge, "extract_data",
                        lambda p, cancel_event=None: _fake_extract_result(sample_df))
    out = ic.load_stage_b_extract(_trigger(rds_path))
    label, bar, container, data_info, trigger2 = out
    assert "マーカー(DEG)" in label
    assert trigger2["rds_path"] == rds_path
    # state に格納されている (key 明示で取得)
    st = ic._get_state(rds_path)
    assert st["plot_data"] is sample_df
    assert st["meta"]["n_clusters"] == 3


def test_link_b_runtime_error_surfaces_stderr(monkeypatch):
    def boom(p, cancel_event=None):
        raise RuntimeError("Seurat extraction failed:\nERROR: object 'x' not found")
    monkeypatch.setattr(ic._bridge, "extract_data", boom)
    out = ic.load_stage_b_extract(_trigger("/proj/A.rds"))
    label, bar, container, data_info, trigger2 = out
    assert trigger2 is no_update             # 連鎖停止
    assert container == ic._PROGRESS_HIDE
    txt = _alert_text(data_info)
    assert "Rエラー" in txt and "object 'x' not found" in txt


def test_link_b_timeout(monkeypatch):
    def boom(p, cancel_event=None):
        raise RuntimeError("Seurat extraction timed out (10min): rds=/proj/A.rds")
    monkeypatch.setattr(ic._bridge, "extract_data", boom)
    out = ic.load_stage_b_extract(_trigger("/proj/A.rds"))
    assert out[4] is no_update
    assert "タイムアウト" in _alert_text(out[3])


def test_link_b_rscript_not_found(monkeypatch):
    def boom(p, cancel_event=None):
        raise FileNotFoundError("Rscript")
    monkeypatch.setattr(ic._bridge, "extract_data", boom)
    out = ic.load_stage_b_extract(_trigger("/proj/A.rds"))
    assert out[4] is no_update
    assert "Rscript" in _alert_text(out[3]) and "見つかりません" in _alert_text(out[3])


def test_link_b_empty_extraction(monkeypatch):
    monkeypatch.setattr(ic._bridge, "extract_data", lambda p, cancel_event=None: {"plot_data": None})
    out = ic.load_stage_b_extract(_trigger("/proj/A.rds"))
    assert out[4] is no_update
    assert "抽出結果が空" in _alert_text(out[3])


def test_link_b_prevent_update_on_none():
    with pytest.raises(PreventUpdate):
        ic.load_stage_b_extract(None)


# ---------------------------------------------------------------------------
# Link C: DEG 読込 + キャリブレーション (非致命)
# ---------------------------------------------------------------------------

def _seed_state(rds_path, sample_df):
    st = ic._get_state(rds_path)
    st["plot_data"] = sample_df
    st["features_list"] = [f"mz_{i}" for i in range(20)]
    st["meta"] = {"n_cells": len(sample_df), "n_clusters": 3, "samples": ["S1", "S2"]}
    st["cache_dir"] = "/tmp/fake_cache"
    return st


def test_link_c_deg_none_is_non_fatal(monkeypatch, sample_df):
    rds_path = "/proj/A.rds"
    _seed_state(rds_path, sample_df)
    monkeypatch.setattr(ic, "_load_deg_results", lambda base, m: None)
    out = ic.load_stage_c_deg(_trigger(rds_path), False, None, 0.5, 2,
                              "linear", "Positive", "", 0.01, None, "")
    label, bar, container, data_info, trigger3 = out
    assert "設定を復元中" in label          # 成功して次段へ
    assert trigger3["rds_path"] == rds_path
    assert ic._get_state(rds_path)["_deg_data"] is None  # DEG なしでも続行


def test_link_c_calibration_failure_is_non_fatal(monkeypatch, sample_df, deg_records):
    rds_path = "/proj/A.rds"
    _seed_state(rds_path, sample_df)
    monkeypatch.setattr(ic, "_load_deg_results", lambda base, m: deg_records)
    import app.callbacks.interactive_calibration as cal
    def boom(*a, **k):
        raise ValueError("calibration blew up")
    monkeypatch.setattr(cal, "_calibrate_mz_from_pairs", boom)
    cal_table = [{"use": "Yes", "ref_mz": "100.0", "obs_mz": "100.1"},
                 {"use": "Yes", "ref_mz": "200.0", "obs_mz": "200.2"}]
    out = ic.load_stage_c_deg(_trigger(rds_path), True, cal_table, 0.5, 2,
                              "linear", "Positive", "/mrm.csv", 0.1, None, "")
    label = out[0]
    assert "設定を復元中" in label          # 致命にならない
    st = ic._get_state(rds_path)
    assert "_calib_warning" in st           # 警告は記録
    assert st["_deg_data"] == deg_records   # DEG はそのまま


def test_link_c_state_lost_shows_error(sample_df):
    # state を seed しない → plot_data None
    out = ic.load_stage_c_deg(_trigger("/proj/missing.rds"), False, None, 0.5, 2,
                              "linear", "Positive", "", 0.01, None, "")
    assert out[4] is no_update
    assert "状態が失われました" in _alert_text(out[3])


# ---------------------------------------------------------------------------
# Link D: 最終描画 (32 + teardown = 34 出力)
# ---------------------------------------------------------------------------

def _call_link_d(rds_path):
    return ic.load_stage_d_finish(
        _trigger(rds_path), "Harmony", {"Harmony": rds_path}, "",
        False, "DHB", None, 0.5, 2, "poly3", "Positive", "", 0.01, None,
        None, None, "")


def test_link_d_success_returns_34_and_shows_viz(monkeypatch, sample_df, deg_records):
    rds_path = "/proj/A.rds"
    st = _seed_state(rds_path, sample_df)
    st["_deg_data"] = deg_records
    monkeypatch.setattr(ic, "_load_interactive_settings", lambda: {})
    import app.callbacks.interactive_calibration as cal
    # spy: アノテーション関数が state["features_list"] で呼ばれることを検証。
    # （万一 result[...] 参照が残ると NameError で呼ばれず、ここで検出できる）
    captured = {}
    def spy_anno(features, **kwargs):
        captured["features"] = features
        captured["kwargs"] = kwargs
        # ★ ver52.3 ④: 読み込み経路は `return_skipped=True` で呼ぶので
        #   スタブも 2-tuple を返す。dict のままだとアンパックが例外になり、
        #   本体の `except Exception` が拾って **注釈ゼロで「読み込み完了」**に
        #   なる（実際にこのテストがそれを捕まえた）。
        return ({"built": True}, 0)
    monkeypatch.setattr(cal, "_build_feature_annotation_map", spy_anno)
    out = _call_link_d(rds_path)
    assert len(out) == 34
    assert "読み込み完了" in str(out[0])     # info_text
    assert out[1] == {}                       # viz container 表示
    assert out[6] == "/tmp/fake_cache"        # cache_dir (state 由来)
    assert out[7] == deg_records              # deg_data_store
    assert out[-2] == ic._PROGRESS_HIDE       # 進捗コンテナ非表示
    assert out[-1] == "完了"                  # 進捗ラベル
    # アノテーションが正しい features で構築された (result 参照バグの回帰防止)
    assert captured.get("features") == st["features_list"]
    assert ic._get_state(rds_path).get("annotation_map") == {"built": True}
    # ★ 読めなかったセルを数えられる形で呼んでいること。
    #   ここが False/未指定に戻ると、読み込み経路は再び黙って化合物名を落とす。
    assert captured["kwargs"].get("return_skipped") is True


def test_link_d_reports_unreadable_annotation_cells(monkeypatch, sample_df):
    """★ ver52.3 ④: 読み込み時点で「読めなかった質量セル」を利用者に出すこと。

    ここで落ちた化合物名は backfill 経由で PPTX / export にも欠けたまま出る。
    再アノテーション画面を開かない利用者には、ここ以外に気づく機会が無い。
    """
    rds_path = "/proj/A.rds"
    st = _seed_state(rds_path, sample_df)
    st["_deg_data"] = None
    monkeypatch.setattr(ic, "_load_interactive_settings", lambda: {})
    import app.callbacks.interactive_calibration as cal
    monkeypatch.setattr(cal, "_build_feature_annotation_map",
                        lambda *a, **k: ({}, 3))
    out = _call_link_d(rds_path)
    info = str(out[0])
    assert "3 件" in info and "注釈されていません" in info, (
        f"読めなかった質量セルが読み込み完了行に出ていない: {info}")


def test_link_d_says_nothing_when_all_cells_are_readable(monkeypatch, sample_df):
    """★ 過剰報告の番人: 0 件なら何も足さない。"""
    rds_path = "/proj/A.rds"
    st = _seed_state(rds_path, sample_df)
    st["_deg_data"] = None
    monkeypatch.setattr(ic, "_load_interactive_settings", lambda: {})
    import app.callbacks.interactive_calibration as cal
    monkeypatch.setattr(cal, "_build_feature_annotation_map",
                        lambda *a, **k: ({}, 0))
    assert "注釈されていません" not in str(_call_link_d(rds_path)[0])


def test_link_d_calib_warning_appended(monkeypatch, sample_df):
    rds_path = "/proj/A.rds"
    st = _seed_state(rds_path, sample_df)
    st["_deg_data"] = None
    st["_calib_warning"] = "（注: m/zキャリブレーションに失敗したため未適用）"
    monkeypatch.setattr(ic, "_load_interactive_settings", lambda: {})
    import app.callbacks.interactive_calibration as cal
    monkeypatch.setattr(cal, "_build_feature_annotation_map", lambda *a, **k: ({}, 0))
    out = _call_link_d(rds_path)
    assert "キャリブレーション" in str(out[0])  # info_text に警告付加


def test_link_d_state_lost_returns_error_34():
    out = _call_link_d("/proj/missing.rds")
    assert len(out) == 34
    assert "状態が失われました" in _alert_text(out[0])
    assert out[1] == {"display": "none"}       # viz は隠す
    assert out[-2] == ic._PROGRESS_HIDE


# ---------------------------------------------------------------------------
# active key (プロジェクト) 隔離
# ---------------------------------------------------------------------------

def test_active_key_isolation_between_projects(monkeypatch, sample_df):
    import pandas as pd
    df_a = sample_df
    df_b = sample_df.copy()
    df_b["Cluster"] = df_b["Cluster"] + 100  # 区別用

    # プロジェクト A を先に seed
    ic._get_state("/proj/A.rds")["plot_data"] = df_a
    # プロジェクト B を Link B で駆動
    monkeypatch.setattr(ic._bridge, "extract_data",
                        lambda p, cancel_event=None: _fake_extract_result(df_b))
    ic.load_stage_b_extract(_trigger("/proj/B.rds", method="RPCA"))

    # A は不変、B は B のデータ
    assert ic._get_state("/proj/A.rds")["plot_data"] is df_a
    assert ic._get_state("/proj/B.rds")["plot_data"] is df_b
