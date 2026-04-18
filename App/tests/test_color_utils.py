"""Tests for app.utils.color_utils"""

import re
import pytest

from app.utils.color_utils import (
    cluster_sort_key,
    get_cluster_color_map,
    cluster_display_name,
    adjust_color_lightness,
    get_sample_color_map,
    get_merged_cluster_color_map,
    get_cluster_colorscale,
)


# ---- cluster_sort_key ----

class TestClusterSortKey:
    def test_numeric_string(self):
        assert cluster_sort_key("3") == (3, "")

    def test_numeric_with_suffix(self):
        assert cluster_sort_key("3-a") == (3, "a")

    def test_non_numeric(self):
        key = cluster_sort_key("abc")
        assert key[0] == float("inf")
        assert key[1] == "abc"

    def test_zero(self):
        assert cluster_sort_key("0") == (0, "")

    def test_large_number(self):
        assert cluster_sort_key("999") == (999, "")

    def test_sorting_order(self):
        items = ["2", "10", "1", "1-b", "1-a", "abc"]
        sorted_items = sorted(items, key=cluster_sort_key)
        assert sorted_items == ["1", "1-a", "1-b", "2", "10", "abc"]


# ---- get_cluster_color_map ----

class TestGetClusterColorMap:
    def test_returns_correct_number_of_colors(self):
        cmap = get_cluster_color_map([0, 1, 2])
        assert len(cmap) == 3
        assert all(isinstance(v, str) for v in cmap.values())

    def test_custom_colors_override(self):
        cmap = get_cluster_color_map([0, 1], custom_colors={"0": "#FF0000"})
        assert cmap["0"] == "#FF0000"

    def test_keys_are_strings(self):
        cmap = get_cluster_color_map([0, 1, 2])
        assert all(isinstance(k, str) for k in cmap.keys())

    def test_deduplicates_clusters(self):
        cmap = get_cluster_color_map([0, 0, 1, 1, 2])
        assert len(cmap) == 3

    def test_empty_input(self):
        cmap = get_cluster_color_map([])
        assert cmap == {}

    def test_colors_are_hex(self):
        cmap = get_cluster_color_map([0, 1, 2])
        hex_pattern = re.compile(r"^#[0-9A-Fa-f]{6}$")
        for color in cmap.values():
            assert hex_pattern.match(color), f"Not a valid hex color: {color}"


# ---- cluster_display_name ----

class TestClusterDisplayName:
    def test_no_name_map(self):
        assert cluster_display_name("2", None) == "2"

    def test_with_name_map(self):
        assert cluster_display_name("2", {"2": "Brain"}) == "Brain"

    def test_missing_from_map(self):
        assert cluster_display_name("5", {"2": "Brain"}) == "5"

    def test_empty_name_map(self):
        assert cluster_display_name("2", {}) == "2"

    def test_whitespace_name_stripped(self):
        # name_map value with whitespace only should fall back to ID
        assert cluster_display_name("2", {"2": "   "}) == "2"

    def test_integer_id(self):
        assert cluster_display_name(2, {"2": "Brain"}) == "Brain"


# ---- adjust_color_lightness ----

class TestAdjustColorLightness:
    def test_returns_valid_hex(self):
        result = adjust_color_lightness("#FF0000", 1.2)
        assert re.match(r"^#[0-9a-f]{6}$", result)

    def test_factor_one_returns_same(self):
        result = adjust_color_lightness("#FF0000", 1.0)
        assert result == "#ff0000"

    def test_brighter(self):
        original = "#800000"
        brighter = adjust_color_lightness(original, 1.5)
        # Red channel should increase
        r_orig = int(original[1:3], 16)
        r_bright = int(brighter[1:3], 16)
        assert r_bright > r_orig

    def test_darker(self):
        original = "#808080"
        darker = adjust_color_lightness(original, 0.5)
        r_orig = int(original[1:3], 16)
        r_dark = int(darker[1:3], 16)
        assert r_dark < r_orig

    def test_clamps_to_255(self):
        result = adjust_color_lightness("#FFFFFF", 2.0)
        assert result == "#ffffff"

    def test_hash_prefix_handling(self):
        # Should work with or without leading #
        result = adjust_color_lightness("FF0000", 1.0)
        assert result.startswith("#")


# ---- get_sample_color_map ----

class TestGetSampleColorMap:
    def test_returns_correct_count(self):
        cmap = get_sample_color_map(["S1", "S2"])
        assert len(cmap) == 2

    def test_keys_are_strings(self):
        cmap = get_sample_color_map(["S1", "S2"])
        assert all(isinstance(k, str) for k in cmap.keys())

    def test_deduplicates(self):
        cmap = get_sample_color_map(["S1", "S1", "S2"])
        assert len(cmap) == 2

    def test_sorted_order(self):
        cmap = get_sample_color_map(["B", "A", "C"])
        keys = list(cmap.keys())
        assert keys == sorted(keys)

    def test_colors_are_hex(self):
        cmap = get_sample_color_map(["S1", "S2", "S3"])
        hex_pattern = re.compile(r"^#[0-9A-Fa-f]{6}$")
        for color in cmap.values():
            assert hex_pattern.match(color), f"Not a valid hex color: {color}"


# ---- get_merged_cluster_color_map ----

class TestGetMergedClusterColorMap:
    def test_shade_mode_subclusters(self):
        cmap = get_merged_cluster_color_map(["1-a", "1-b", "2"])
        assert len(cmap) == 3
        assert "1-a" in cmap
        assert "1-b" in cmap
        assert "2" in cmap

    def test_independent_mode(self):
        cmap = get_merged_cluster_color_map(["1-a", "1-b", "2"], mode="independent")
        assert len(cmap) == 3

    def test_custom_colors_applied(self):
        cmap = get_merged_cluster_color_map(
            ["1-a", "1-b"], custom_colors={"1-a": "#AABBCC"}
        )
        assert cmap["1-a"] == "#AABBCC"


# ---- get_cluster_colorscale ----

class TestGetClusterColorscale:
    def test_returns_mapping_and_scale(self):
        idx_map, colorscale = get_cluster_colorscale([0, 1, 2])
        assert len(idx_map) == 3
        assert isinstance(colorscale, list)
        # Each cluster gets two entries (low, high) in the colorscale
        assert len(colorscale) == 6

    def test_indices_are_sequential(self):
        idx_map, _ = get_cluster_colorscale([2, 0, 1])
        values = sorted(idx_map.values())
        assert values == [0, 1, 2]
