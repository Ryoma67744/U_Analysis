"""Tests for app.utils.display_helpers"""

import pytest

from app.utils.display_helpers import (
    compact_sci,
    format_plain_number,
    display_name,
)


# ---- compact_sci ----

class TestCompactSci:
    def test_large_number(self):
        result = compact_sci(1234567)
        assert "e" in result
        # Should be approximately 1.2e6
        assert result == "1.2e6"

    def test_small_number_still_uses_sci(self):
        # compact_sci always uses scientific notation except for 0
        result = compact_sci(123)
        assert "e" in result
        assert result == "1.2e2"

    def test_zero(self):
        assert compact_sci(0) == "0"

    def test_negative_number(self):
        result = compact_sci(-5000)
        assert "e" in result

    def test_decimal(self):
        result = compact_sci(0.005)
        assert "e" in result

    def test_exact_power_of_ten(self):
        result = compact_sci(1000)
        assert result == "1.0e3"

    def test_one(self):
        result = compact_sci(1)
        assert result == "1.0e0"


# ---- format_plain_number ----

class TestFormatPlainNumber:
    def test_large_float(self):
        result = format_plain_number(1234.5)
        assert "e" not in result.lower()
        assert result == "1234.5"

    def test_integer_value_float(self):
        result = format_plain_number(280000.0)
        assert result == "280000"
        assert "e" not in result.lower()

    def test_zero(self):
        assert format_plain_number(0) == "0"

    def test_small_decimal(self):
        result = format_plain_number(0.00123)
        assert "e" not in result.lower()
        # Should preserve significant digits
        assert "0.00123" in result

    def test_integer_as_float(self):
        result = format_plain_number(42.0)
        assert result == "42"

    def test_simple_decimal(self):
        result = format_plain_number(3.5)
        assert result == "3.5"


# ---- display_name ----

class TestDisplayName:
    def test_no_name_map(self):
        assert display_name("original", None) == "original"

    def test_with_rename(self):
        assert display_name("original", {"original": "renamed"}) == "renamed"

    def test_missing_from_map(self):
        assert display_name("other", {"original": "renamed"}) == "other"

    def test_empty_map(self):
        assert display_name("original", {}) == "original"

    def test_preserves_case(self):
        assert display_name("Sample_A", {"Sample_A": "Tissue X"}) == "Tissue X"
