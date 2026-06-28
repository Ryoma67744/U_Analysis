"""Tests for app.services.selection_groups pure CRUD (P3)."""

from app.services.selection_groups import (
    empty_state, add_group, rename_group, delete_group, combine_groups,
    groups_to_csv, groups_from_csv,
)


class TestAddGroup:
    def test_add_assigns_id_and_name(self):
        s = add_group(empty_state(), "腫瘍", ["c1", "c2"])
        assert len(s["groups"]) == 1
        g = s["groups"][0]
        assert g["id"] == "g1"
        assert g["name"] == "腫瘍"
        assert g["cell_ids"] == ["c1", "c2"]
        assert g["color"].startswith("#")

    def test_dedup_preserves_order(self):
        s = add_group(empty_state(), "x", ["c2", "c1", "c2", "c1"])
        assert s["groups"][0]["cell_ids"] == ["c2", "c1"]

    def test_default_name_when_blank(self):
        s = add_group(empty_state(), "  ", ["c1"])
        assert s["groups"][0]["name"] == "選択1"

    def test_ids_increment(self):
        s = add_group(empty_state(), "a", ["c1"])
        s = add_group(s, "b", ["c2"])
        assert [g["id"] for g in s["groups"]] == ["g1", "g2"]

    def test_id_unique_after_delete(self):
        s = add_group(empty_state(), "a", ["c1"])      # g1
        s = add_group(s, "b", ["c2"])                   # g2
        s = delete_group(s, "g1")
        s = add_group(s, "c", ["c3"])                   # next = g3 (max+1)
        assert [g["id"] for g in s["groups"]] == ["g2", "g3"]


class TestRenameDelete:
    def test_rename(self):
        s = add_group(empty_state(), "a", ["c1"])
        s = rename_group(s, "g1", "脳")
        assert s["groups"][0]["name"] == "脳"

    def test_rename_blank_keeps_old(self):
        s = add_group(empty_state(), "a", ["c1"])
        s = rename_group(s, "g1", "   ")
        assert s["groups"][0]["name"] == "a"

    def test_delete(self):
        s = add_group(empty_state(), "a", ["c1"])
        s = delete_group(s, "g1")
        assert s["groups"] == []


class TestCombine:
    def test_union_dedup(self):
        s = add_group(empty_state(), "a", ["c1", "c2"])
        s = add_group(s, "b", ["c2", "c3"])
        s = combine_groups(s, ["g1", "g2"], new_name="merged")
        assert len(s["groups"]) == 3
        merged = s["groups"][-1]
        assert merged["name"] == "merged"
        assert merged["cell_ids"] == ["c1", "c2", "c3"]

    def test_combine_default_name(self):
        s = add_group(empty_state(), "a", ["c1"])
        s = add_group(s, "b", ["c2"])
        s = combine_groups(s, ["g1", "g2"])
        assert s["groups"][-1]["name"] == "a+b"

    def test_combine_empty_noop(self):
        s = add_group(empty_state(), "a", ["c1"])
        out = combine_groups(s, [])
        assert len(out["groups"]) == 1


class TestCsvRoundTrip:
    def test_to_csv(self):
        s = add_group(empty_state(), "脳", ["c1", "c2"])
        csv_text = groups_to_csv(s)
        lines = csv_text.strip().splitlines()
        assert lines[0] == "CellID,Group"
        assert "c1,脳" in csv_text
        assert "c2,脳" in csv_text

    def test_roundtrip(self):
        s = add_group(empty_state(), "脳", ["c1", "c2"])
        s = add_group(s, "腎", ["c3"])
        text = groups_to_csv(s)
        s2 = groups_from_csv(text)
        names = {g["name"]: g["cell_ids"] for g in s2["groups"]}
        assert names == {"脳": ["c1", "c2"], "腎": ["c3"]}

    def test_from_csv_tolerant_headers(self):
        text = "cell_id,cluster\nc1,A\nc2,A\nc3,B\n"
        s = groups_from_csv(text)
        names = {g["name"]: g["cell_ids"] for g in s["groups"]}
        assert names == {"A": ["c1", "c2"], "B": ["c3"]}

    def test_from_csv_empty(self):
        assert groups_from_csv("")["groups"] == []
