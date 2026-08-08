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

    # ★ ver52.3: 旧テストは "umap_min_dist" / "pca_dims" という **画面に存在しない
    #   id** を渡していた。`validate_param` は未知 id を常に ok として通すので、
    #   このテストは「一度も適用されない定義」を固定していただけだった
    #   （ver52.0 で marker_outcome の誤った設計をテストに固定したのと同じ形）。
    #   実際の画面 id に直し、範囲もレイアウトに合わせた。
    def test_min_dist_bounds(self):
        assert validate_param("umap_min_dist_input", 0.3)[0] is True
        assert validate_param("umap_min_dist_input", 1.5)[0] is False

    def test_umap_dims_bounds(self):
        assert validate_param("umap_dims_input", 1)[0] is False    # <2
        assert validate_param("umap_dims_input", 30)[0] is True
        assert validate_param("umap_dims_input", 60)[0] is False   # >50

    def test_n_neighbors_bounds(self):
        assert validate_param("umap_n_neighbors_input", 30)[0] is True
        assert validate_param("umap_n_neighbors_input", 1)[0] is False    # <2
        assert validate_param("umap_n_neighbors_input", 200)[0] is False  # >100

    def test_dead_ids_are_gone(self):
        """★ 旧キーが復活していないこと。

        未知 id は常に ok なので、キーが消えても `validate_param` は
        黙って True を返す。「効いていない検証」を再び作らないよう固定する。
        """
        from app.utils.validation import PARAM_BOUNDS as _PB
        for dead in ("umap_min_dist", "umap_n_neighbors", "pca_dims", "perplexity"):
            assert dead not in _PB, (
                f"画面に存在しない id '{dead}' の定義が復活している。"
                "validate_param は未知 id を常に ok にするので無言で効かない")

    def test_blank_is_ok_for_all_bounds(self):
        for pid in PARAM_BOUNDS:
            assert validate_param(pid, None)[0] is True
