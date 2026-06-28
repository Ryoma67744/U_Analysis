"""Tests for app.utils.selection_utils (P1: selection summary / Top-N / colorscale)."""

import numpy as np
import pandas as pd

from app.utils.selection_utils import (
    extract_selected_cell_ids,
    natural_cluster_key,
    compute_selection_summary,
    log_transform_intensities,
    top_n_markers,
)


def _df():
    return pd.DataFrame({
        "CellID": ["c1", "c2", "c3", "c4", "c5"],
        "Cluster": ["0", "0", "1", "2", "1"],
        "Sample": ["A", "A", "A", "B", "B"],
    })


# ---- extract_selected_cell_ids ----

class TestExtractSelectedCellIds:
    def test_none(self):
        assert extract_selected_cell_ids(None) == []

    def test_empty_points(self):
        assert extract_selected_cell_ids({"points": []}) == []

    def test_extracts_text(self):
        sd = {"points": [{"text": "c1"}, {"text": "c3"}]}
        assert extract_selected_cell_ids(sd) == ["c1", "c3"]

    def test_dedupe_preserves_order(self):
        sd = {"points": [{"text": "c2"}, {"text": "c1"}, {"text": "c2"}]}
        assert extract_selected_cell_ids(sd) == ["c2", "c1"]

    def test_skips_missing_text(self):
        sd = {"points": [{"text": "c1"}, {"curveNumber": 0}]}
        assert extract_selected_cell_ids(sd) == ["c1"]


# ---- natural_cluster_key ----

class TestNaturalClusterKey:
    def test_numeric_order(self):
        keys = sorted(["10", "2", "1"], key=natural_cluster_key)
        assert keys == ["1", "2", "10"]

    def test_subcluster_after_numeric(self):
        assert natural_cluster_key("3-a")[0] == 0  # leading digit → numeric group


# ---- compute_selection_summary ----

class TestComputeSelectionSummary:
    def test_empty_selection(self):
        out = compute_selection_summary(_df(), [])
        assert out["n_selected"] == 0
        assert out["n_total"] == 5
        assert out["by_cluster"] == []

    def test_basic_counts(self):
        out = compute_selection_summary(_df(), ["c1", "c2", "c3"])
        assert out["n_selected"] == 3
        assert out["n_total"] == 5
        assert out["pct"] == 60.0

    def test_cluster_composition_sorted(self):
        out = compute_selection_summary(_df(), ["c1", "c2", "c3"])
        keys = [r["key"] for r in out["by_cluster"]]
        assert keys == ["0", "1"]  # natural order
        by = {r["key"]: r["count"] for r in out["by_cluster"]}
        assert by == {"0": 2, "1": 1}

    def test_sample_composition_count_desc(self):
        out = compute_selection_summary(_df(), ["c1", "c2", "c3", "c4"])
        # A=3 (c1,c2,c3), B=1 (c4) → A first
        assert out["by_sample"][0]["key"] == "A"
        assert out["by_sample"][0]["count"] == 3

    def test_mean_intensity(self):
        df = _df()
        expr = np.array([10.0, 20.0, 5.0, 99.0, 1.0])  # aligned to df rows
        out = compute_selection_summary(df, ["c1", "c2"], expr=expr,
                                        feature_name="mz_100")
        assert out["feature_name"] == "mz_100"
        assert out["mean_intensity"] == 15.0  # mean(10,20)

    def test_mean_intensity_skipped_without_feature(self):
        df = _df()
        expr = np.array([10.0, 20.0, 5.0, 99.0, 1.0])
        out = compute_selection_summary(df, ["c1"], expr=expr, feature_name=None)
        assert out["mean_intensity"] is None


# ---- log_transform_intensities ----

class TestLogTransform:
    def test_monotonic(self):
        out = log_transform_intensities([0.0, 1.0, 10.0, 100.0])
        assert list(out) == sorted(out)  # strictly increasing preserved

    def test_zero_maps_to_zero(self):
        out = log_transform_intensities([0.0])
        assert out[0] == 0.0

    def test_handles_negative_shift(self):
        out = log_transform_intensities([-5.0, 0.0, 5.0])
        assert np.all(np.isfinite(out))
        assert out[0] == 0.0  # min shifted to 0 then log1p(0)=0

    def test_preserves_nan(self):
        out = log_transform_intensities([np.nan, 1.0])
        assert np.isnan(out[0])


# ---- top_n_markers ----

class TestTopNMarkers:
    def _recs(self):
        return [
            {"gene": "a", "p_val_adj_raw": 0.10, "avg_log2FC": 1.0},
            {"gene": "b", "p_val_adj_raw": 0.001, "avg_log2FC": 2.0},
            {"gene": "c", "p_val_adj_raw": 0.05, "avg_log2FC": -1.0},
        ]

    def test_empty(self):
        assert top_n_markers([], 5) == []

    def test_sort_by_pval_ascending(self):
        out = top_n_markers(self._recs(), 0)  # all, sorted
        assert [r["gene"] for r in out] == ["b", "c", "a"]

    def test_head_n(self):
        out = top_n_markers(self._recs(), 2)
        assert [r["gene"] for r in out] == ["b", "c"]

    def test_sort_by_fc_descending(self):
        out = top_n_markers(self._recs(), 0, sort_col="avg_log2FC",
                            ascending=False)
        assert [r["gene"] for r in out] == ["b", "a", "c"]
