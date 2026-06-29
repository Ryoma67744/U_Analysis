"""Tests for app.utils.validation (Inc.1: range validation)."""

from app.utils.validation import check_range, validate_param, PARAM_BOUNDS


class TestCheckRange:
    def test_blank_allowed(self):
        assert check_range(None) == (True, "")
        assert check_range("") == (True, "")

    def test_blank_disallowed(self):
        ok, msg = check_range("", allow_blank=False, name="FC")
        assert ok is False and "FC" in msg

    def test_non_numeric(self):
        ok, msg = check_range("abc", name="値")
        assert ok is False and "数値" in msg

    def test_below_lo(self):
        ok, msg = check_range(-1, lo=0, name="FC")
        assert ok is False and "0 以上" in msg

    def test_above_hi(self):
        ok, msg = check_range(150, hi=100, name="強度")
        assert ok is False and "100 以下" in msg

    def test_within(self):
        assert check_range(50, lo=0, hi=100) == (True, "")

    def test_boundary_inclusive(self):
        assert check_range(0, lo=0, hi=100)[0] is True
        assert check_range(100, lo=0, hi=100)[0] is True


class TestValidateParam:
    def test_unknown_param_ok(self):
        assert validate_param("nonexistent", 99999) == (True, "")

    def test_intensity_out_of_range(self):
        ok, msg = validate_param("feature_intensity_max", 120)
        assert ok is False and "100 以下" in msg

    def test_intensity_in_range(self):
        assert validate_param("feature_intensity_min", 10)[0] is True

    def test_fc_negative_rejected(self):
        ok, _ = validate_param("onthefly_de_fc", -0.5)
        assert ok is False

    def test_min_dist_bounds(self):
        assert validate_param("umap_min_dist", 0.1)[0] is True
        assert validate_param("umap_min_dist", 1.5)[0] is False

    def test_pca_dims_bounds(self):
        assert validate_param("pca_dims", 5)[0] is False   # <10
        assert validate_param("pca_dims", 30)[0] is True

    def test_blank_is_ok_for_all_bounds(self):
        for pid in PARAM_BOUNDS:
            assert validate_param(pid, None)[0] is True
