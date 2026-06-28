"""Tests for app.services.feature_lists pure CRUD (P5-b)."""

from app.services.feature_lists import (
    empty_state, add_list, rename_list, delete_list,
    lists_to_csv, lists_from_csv,
)


class TestAddList:
    def test_add_assigns_id_and_name(self):
        s = add_list(empty_state(), "PC脂質", ["700.5", "750.5"])
        assert len(s["lists"]) == 1
        g = s["lists"][0]
        assert g["id"] == "l1"
        assert g["name"] == "PC脂質"
        assert g["features"] == ["700.5", "750.5"]

    def test_dedup_preserves_order(self):
        s = add_list(empty_state(), "x", ["b", "a", "b", "a"])
        assert s["lists"][0]["features"] == ["b", "a"]

    def test_default_name(self):
        s = add_list(empty_state(), "", ["a"])
        assert s["lists"][0]["name"] == "リスト1"

    def test_ids_increment(self):
        s = add_list(empty_state(), "a", ["1"])
        s = add_list(s, "b", ["2"])
        assert [g["id"] for g in s["lists"]] == ["l1", "l2"]


class TestRenameDelete:
    def test_rename(self):
        s = add_list(empty_state(), "a", ["1"])
        s = rename_list(s, "l1", "脂質A")
        assert s["lists"][0]["name"] == "脂質A"

    def test_delete(self):
        s = add_list(empty_state(), "a", ["1"])
        s = delete_list(s, "l1")
        assert s["lists"] == []


class TestCsv:
    def test_to_csv(self):
        s = add_list(empty_state(), "脂質", ["700.5", "750.5"])
        text = lists_to_csv(s)
        assert text.splitlines()[0] == "Feature,List"
        assert "700.5,脂質" in text

    def test_roundtrip(self):
        s = add_list(empty_state(), "脂質", ["700.5", "750.5"])
        s = add_list(s, "糖", ["180.1"])
        s2 = lists_from_csv(lists_to_csv(s))
        names = {g["name"]: g["features"] for g in s2["lists"]}
        assert names == {"脂質": ["700.5", "750.5"], "糖": ["180.1"]}

    def test_from_csv_tolerant_headers(self):
        text = "mz,list\n700.5,A\n750.5,A\n180.1,B\n"
        s = lists_from_csv(text)
        names = {g["name"]: g["features"] for g in s["lists"]}
        assert names == {"A": ["700.5", "750.5"], "B": ["180.1"]}

    def test_from_csv_feature_only_default_list(self):
        text = "Feature\n700.5\n750.5\n"
        s = lists_from_csv(text)
        assert len(s["lists"]) == 1
        assert s["lists"][0]["features"] == ["700.5", "750.5"]

    def test_from_csv_empty(self):
        assert lists_from_csv("")["lists"] == []
