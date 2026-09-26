"""R/Dashを起動しない静的配線検査。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_float_is_large_modeless_editable_and_registered():
    layout = text("app/layouts/imzml_spatial_float.py")
    css = text("app/assets/styles.css")
    js = text("app/assets/imzml_spatial_float.js")
    main = text("app/main.py")
    callbacks = text("app/callbacks/imzml_spatial_callbacks.py")

    assert 'className="imzml-spatial-float"' in layout
    assert "position: fixed" in css and "z-index: 1040" in css
    assert "width: 920px" in css and "height: 760px" in css
    assert 'storagePrefix = "ua-imzml-spatial-window:v2:"' in js
    assert "pointerdown" in js and "sessionStorage" in js
    assert '"subject_id"' in layout and '"group"' in layout
    assert "editable=True" in layout
    assert "imzml_spatial_bulk_apply" in layout
    assert 'State("imzml_spatial_table" + suffix, "data")' in callbacks
    assert "_apply_table_metadata" in callbacks
    assert "imzml_spatial_callbacks" in main
    assert "triggered_value" in callbacks  # 動的ボタン追加時のn_clicks=0で自動表示しない。


def test_r_contract_prioritizes_coordinate_component():
    helper = text("Script/helpers/analysis_contract.R")
    reader = text("Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R")
    assert 'component_col = "ua_coordinate_component"' in helper
    assert 'identical(role, "spatial")' in helper
    assert 'coordinates$ua_coordinate_component' in reader
    assert 'seu$ua_coordinate_component <- dat$coordinates$ua_coordinate_component' in reader
    assert '"ua_coordinate_component", "annotation"' in reader


def test_compact_card_opens_unified_float_and_keeps_advanced_table():
    callbacks = text("app/callbacks/section_callbacks.py")
    settings = text("app/layouts/settings_tab.py")
    assert '"配置・切片情報を編集"' in callbacks
    assert '"配置を確認"' not in callbacks
    assert '"切片名・群を設定"' not in callbacks
    assert "section_group_open" not in callbacks
    assert 'id="section_group_details" + suffix' in settings
    assert '全ファイルの切片名・群情報を一括設定（任意）' in settings


def test_section_aggregate_keeps_display_name():
    export = text("app/callbacks/interactive_data_export.py")
    assert '"section_id", "section_display_name"' in export



def test_inferred_ms1_preflight_and_individual_axis_notice_are_wired():
    validation = text("app/services/imzml_validation.py")
    callbacks = text("app/callbacks/section_callbacks.py")
    preparation = text("app/services/input_preparation.py")
    watcher = text("app/services/job_watcher.py")
    runner = text("app/services/analysis_runner.py")
    assert "inspect_spectral_preflight" in validation
    assert '"inferred_ms1"' in validation and '"explicit_ms1"' in validation
    assert "Top-N／閾値付きpeak list" in validation
    assert "科学的背景と推奨入力" in callbacks
    assert "直接解析: 現在は非対応" in callbacks
    assert 'CONTRACT_VERSION = "common-axis-ms1-spatial-v4"' in preparation
    assert "入力準備プロセス" in watcher and "R解析は開始されていません" in watcher
    assert "入力準備プロセス" in runner and "R解析は開始されていません" in runner
