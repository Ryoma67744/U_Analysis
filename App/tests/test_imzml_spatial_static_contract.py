"""R/Dashを起動しない静的配線検査。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_float_is_modeless_and_registered():
    layout = text("app/layouts/imzml_spatial_float.py")
    css = text("app/assets/styles.css")
    js = text("app/assets/imzml_spatial_float.js")
    main = text("app/main.py")
    assert 'className="imzml-spatial-float"' in layout
    assert "position: fixed" in css and "z-index: 1040" in css
    assert "pointerdown" in js and "sessionStorage" in js
    callbacks = text("app/callbacks/imzml_spatial_callbacks.py")
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


def test_compact_card_opens_group_editor_and_float():
    callbacks = text("app/callbacks/section_callbacks.py")
    settings = text("app/layouts/settings_tab.py")
    assert '"配置を確認"' in callbacks
    assert '"切片名・群を設定"' in callbacks
    assert 'section_group_details' in callbacks
    assert 'id="section_group_details" + suffix' in settings
    assert '切片名・群情報を設定（任意）' in settings


def test_section_aggregate_keeps_display_name():
    export = text("app/callbacks/interactive_data_export.py")
    assert '"section_id", "section_display_name"' in export
