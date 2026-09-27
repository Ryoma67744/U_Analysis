"""★ ver74.1: 必須ゲートを維持しながら不足箇所へ具体的に誘導する実callback回帰。"""
from copy import deepcopy
from types import SimpleNamespace
import dash
from tests.test_imzml_editor_callback_transitions_v74 import editor, _open, _act
from app.services.imzml_spatial_layout import build_spatial_layout, default_spatial_sections


def texts(node):
    if isinstance(node, (list, tuple)):
        return " ".join(texts(child) for child in node)
    return texts(node.children) if hasattr(node, "children") else str(node or "")


def test_five_registered_three_selected_highlights_every_missing_id(editor):
    layout = build_spatial_layout([(i * 10 + x, 0, 1) for i in range(5) for x in (0, 1)])
    draft = default_spatial_sections("five", layout)
    for i, row in enumerate(draft):
        row.update(group="Ctrl", selected=i < 3)
    table = editor.module._table_rows(draft)
    styles, summary, cells, disabled, label, tips = editor.module._registration_guidance(table, draft, layout)
    assert "登録：全5切片 ／ 今回の解析：3切片 ／ 未確認：5切片" in texts(summary)
    assert "5切片・5項目" in texts(summary)
    assert len(cells) == 5 and all(cell["column_id"] == "subject_id" for cell in cells)
    assert disabled is False and "個体／独立試料ID" in label
    for index in range(5):
        assert any(style["if"] == {"row_index": index, "column_id": "subject_id"}
                   and style.get("border") == "2px solid #c92a2a" for style in styles)
    assert not any("opacity" in style for style in styles)
    assert "解析対象外" in texts(summary)


def test_live_guidance_tracks_input_confirmation_and_edit_after_confirmation(editor):
    opened = _open(editor, "initial")
    table = deepcopy(opened[5])
    for i, row in enumerate(table): row.update(subject_id=f"Mouse{i}", group="Ctrl")
    live = editor.module._registration_guidance(table, opened[3], editor.layout)
    assert live[2] == [] and live[3] is True
    assert "① 入力完了 → ②" in texts(live[1])
    confirmed = _act(editor, "initial", "confirm_all", opened[3], table, base=opened[9])
    ready = editor.module._registration_guidance(confirmed[2], confirmed[0], editor.layout)
    assert "③ 適用できます" in texts(ready[1])
    changed = deepcopy(confirmed[2]); changed[0]["subject_id"] = ""
    live = editor.module._registration_guidance(changed, confirmed[0], editor.layout)
    assert live[2][0]["column_id"] == "subject_id"
    assert "未確認：1切片" in texts(live[1])
    assert any(style.get("textDecoration") == "line-through" for style in live[0])
    rejected = _act(editor, "initial", "confirm_all", confirmed[0], changed, base=opened[9])
    assert rejected[4] is dash.no_update and "赤枠" in rejected[7]


def test_structural_validation_remains_visible(editor):
    opened = _open(editor, "initial")
    table = deepcopy(opened[5])
    for i, row in enumerate(table): row.update(section_display_name="重複", subject_id=f"M{i}", group="G")
    result = editor.module._registration_guidance(table, opened[3], editor.layout)
    assert "切片名が重複" in texts(result[1])
    assert "整合性" in texts(result[1])


def test_next_missing_button_activates_corresponding_cell(editor):
    function = next(fn for fn in editor.captured if fn.__name__ == "focus_next_missing")
    cells = [{"row": 0, "column": 1, "column_id": "subject_id"},
             {"row": 1, "column": 2, "column_id": "group"}]
    assert function(1, cells, None) == (cells[0], [cells[0]])
    assert function(2, cells, cells[0]) == (cells[0], [cells[0]])
    assert function(3, cells[1:], cells[0]) == (cells[1], [cells[1]])
    assert function(3, [], cells[1]) == (dash.no_update, dash.no_update)


def test_external_missing_info_button_opens_exact_file(editor):
    function = next(fn for fn in editor.captured if fn.__name__ == "open_registration_issue")
    trigger = {"type": "imzml_registration_fix", "scope": "initial", "index": editor.path}
    editor.module.ctx = SimpleNamespace(triggered_id=trigger, triggered=[{"value": 1}])
    result = function([1], [trigger], editor.catalog, editor.manifest, {})
    assert len(result) == 10 and result[0] == {"display": "block"}
    assert result[2] == editor.path and len(result[5]) == 2


def test_empty_guidance_does_not_claim_ready_to_apply(editor):
    result = editor.module._registration_guidance([], [], {})
    assert "読み込みを待って" in texts(result[1])
    assert "適用できます" not in texts(result[1])
    assert result[3] is True
