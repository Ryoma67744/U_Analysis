from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.input_preparation import InputPreparationError

from app.services.imzml_registration import build_registration_spec, section_for_component


def _entry(selected=True):
    return {
        "file_id": "file_a",
        "path": "/tmp/a.imzML",
        "spatial_layout": {
            "coordinate_hash": "coord-hash",
            "components": [
                {"component_id": "c1", "pixel_count": 2},
                {"component_id": "c2", "pixel_count": 3},
            ],
        },
        "registered_sections": [
            {
                "section_id": "s1", "section_display_name": "Control_1",
                "subject_id": "M1", "group": "Control", "metadata_confirmed": True,
                "component_ids": ["c1"], "pixel_count": 2, "selected": selected,
            },
            {
                "section_id": "s2", "section_display_name": "Toxo_1",
                "subject_id": "M2", "group": "Toxoplasma", "metadata_confirmed": True,
                "component_ids": ["c2"], "pixel_count": 3, "selected": False,
            },
        ],
    }


def test_registration_preserves_all_sections_but_ignores_analysis_selection_in_hash():
    a = build_registration_spec(_entry(selected=True))
    b = build_registration_spec(_entry(selected=False))
    assert a["registration_hash"] == b["registration_hash"]
    assert [row["section_display_name"] for row in a["section_registry"]] == [
        "Control_1", "Toxo_1"
    ]
    assert section_for_component(a, "c2")["section_display_name"] == "Toxo_1"


def test_incomplete_unselected_section_blocks_registration():
    entry = _entry()
    entry["registered_sections"][1]["group"] = ""
    with pytest.raises(InputPreparationError):
        build_registration_spec(entry)


def test_registration_hash_changes_when_registered_metadata_changes():
    a = build_registration_spec(_entry())
    changed = _entry()
    changed["registered_sections"][1]["group"] = "Other"
    b = build_registration_spec(changed)
    assert a["registration_hash"] != b["registration_hash"]
