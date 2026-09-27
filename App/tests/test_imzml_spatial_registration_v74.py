from __future__ import annotations

from app.services.imzml_spatial_layout import (
    build_spatial_layout,
    default_spatial_sections,
    merge_spatial_sections,
    reset_spatial_sections,
)


def _layout():
    return build_spatial_layout([
        (0, 0, 1), (1, 0, 1),
        (10, 0, 1), (11, 0, 1),
    ], include_preview=False)


def test_merge_and_reset_require_metadata_reconfirmation():
    layout = _layout()
    rows = default_spatial_sections("file_a", layout)
    for index, row in enumerate(rows, 1):
        row.update(
            section_display_name=f"S{index}", subject_id=f"M{index}",
            group="G", metadata_confirmed=True,
        )
    merged = merge_spatial_sections("file_a", layout, rows, [0, 1])
    assert len(merged) == 1
    assert merged[0]["metadata_confirmed"] is False
    reset = reset_spatial_sections("file_a", layout, merged)
    assert len(reset) == 2
    assert all(row["metadata_confirmed"] is False for row in reset)
