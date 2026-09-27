"""R/Dashを起動しないimzML全切片登録の静的配線検査。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_float_is_modeless_large_and_registered_for_three_scopes():
    layout = text("app/layouts/imzml_spatial_float.py")
    css = text("app/assets/styles.css")
    js = text("app/assets/imzml_spatial_float.js")
    callbacks = text("app/callbacks/imzml_spatial_callbacks.py")
    main = text("app/main.py")
    assert 'className="imzml-spatial-float"' in layout
    assert '"conversion": "_conversion"' in layout
    assert '"metadata_confirmed"' in layout
    assert "全切片" in layout and "該当なし" in layout
    assert "position: fixed" in css and "z-index: 1040" in css
    assert "pointerdown" in js and "sessionStorage" in js
    assert '_register("initial")' in callbacks
    assert '_register("reanalysis")' in callbacks
    assert '_register("conversion")' in callbacks
    assert "validate_registered_sections" in callbacks
    assert "imzml_spatial_callbacks" in main


def test_new_standard_parquet_uses_annotation_and_legacy_component_path_remains():
    helper = text("Script/helpers/analysis_contract.R")
    reader = text("Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R")
    contract = text("app/services/tims_parquet_contract.py")
    processed = text("app/services/imzml_processed.py")
    assert 'component_col = "ua_coordinate_component"' in helper
    assert "legacy ver71-73" in helper
    assert "annotation列に全pixelの切片名" in helper
    assert "解析対象外を含む全切片" in helper
    # ★ ver74.0: 全0除外は実行検証できる共通helperへ移した。配線も維持する。
    assert "lapply(seu_list, ua_drop_all_zero_features)" in reader
    assert "Matrix::rowSums(counts != 0) > 0" in helper
    assert 'BASE_COLUMNS = ("id", "x", "y")' in contract
    assert 'TAIL_COLUMNS = ("annotation",)' in contract
    assert "ua_coordinate_component" not in processed
    assert '"annotation"' in processed


def test_compact_card_keeps_preflight_and_opens_unified_editor():
    callbacks = text("app/callbacks/section_callbacks.py")
    settings = text("app/layouts/settings_tab.py")
    assert "_spectral_preflight_notice" in callbacks
    assert "全切片の登録情報・解析選択を編集" in callbacks
    assert "processed_sparse_candidate" in callbacks
    assert "全ファイルの切片名・群情報を一括設定" in settings
    assert "解析対象外を含む全切片" in settings


def test_manual_and_automatic_paths_share_checked_import_and_registration():
    cli = text("tools/imzml_io_cli.py")
    manual = text("app/callbacks/imzml_io_callbacks.py")
    preparation = text("app/services/input_preparation.py")
    validation = text("app/services/imzml_validation.py")
    assert "checked_import" in cli
    assert "--registration-spec" in cli
    assert "build_registration_spec" in manual
    assert "converter = converter or checked_import" in preparation
    assert 'result.get("validation_summary")' in preparation
    assert '"validation_summary": validation_summary' in validation


def test_backend_gate_hydrates_registry_from_actual_parquet():
    preparation = text("app/services/input_preparation.py")
    completeness = text("app/services/section_completeness.py")
    assert "_hydrate_parquet_registries" in preparation
    assert "read_parquet_section_registry" in preparation
    assert "実ファイルから全切片registry" in preparation
    for token in ("section_display_name", "subject_id", "group", "metadata_confirmed"):
        assert token in completeness


def test_section_aggregate_keeps_display_name():
    export = text("app/callbacks/interactive_data_export.py")
    assert '"section_id", "section_display_name"' in export
