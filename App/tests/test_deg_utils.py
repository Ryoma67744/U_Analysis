"""Tests for app.utils.deg_utils"""

import pytest
import pandas as pd
import numpy as np

from app.utils.deg_utils import (
    is_meaningful_annotation,
    extract_mz_numeric,
    standardize_deg_df,
    get_top_n_features_for_cluster,
)


# ---- is_meaningful_annotation ----

class TestIsMeaningfulAnnotation:
    def test_valid_compound(self):
        assert is_meaningful_annotation("PC 34:1") is True

    def test_empty_string(self):
        assert is_meaningful_annotation("") is False

    def test_none(self):
        assert is_meaningful_annotation(None) is False

    def test_whitespace_only(self):
        assert is_meaningful_annotation("   ") is False

    def test_numeric_only(self):
        assert is_meaningful_annotation("240.984") is False

    def test_integer_only(self):
        assert is_meaningful_annotation("123") is False

    def test_same_as_gene(self):
        assert is_meaningful_annotation("mz_100.5", gene="mz_100.5") is False

    def test_different_from_gene(self):
        assert is_meaningful_annotation("PC 34:1", gene="mz_100.5") is True

    def test_non_string_type(self):
        assert is_meaningful_annotation(123) is False


# ---- extract_mz_numeric ----

class TestExtractMzNumeric:
    def test_standard_mz_format(self):
        result = extract_mz_numeric("mz_123.456")
        assert abs(result - 123.456) < 0.001

    def test_no_numeric(self):
        result = extract_mz_numeric("abc")
        assert result == float("inf")

    def test_integer_mz(self):
        result = extract_mz_numeric("mz_500")
        assert abs(result - 500.0) < 0.001

    def test_embedded_number(self):
        result = extract_mz_numeric("feature_42_extra")
        assert abs(result - 42.0) < 0.001

    def test_multiple_numbers_takes_first(self):
        result = extract_mz_numeric("mz_100.5_200.3")
        assert abs(result - 100.5) < 0.001


# ---- standardize_deg_df ----

class TestStandardizeDegDf:
    def test_standard_columns(self):
        df = pd.DataFrame({
            "gene": ["g1", "g2"],
            "avg_log2FC": [1.5, -0.8],
            "p_val_adj": [0.001, 0.05],
        })
        result = standardize_deg_df(df)
        assert result is not None
        assert len(result) == 2
        assert "gene" in result[0]
        assert "avg_log2FC" in result[0]

    def test_alternative_gene_column(self):
        """'row.names' column should be renamed to 'gene'."""
        df = pd.DataFrame({
            "row.names": ["g1", "g2"],
            "avg_log2FC": [1.0, -1.0],
            "p_val_adj": [0.01, 0.02],
        })
        result = standardize_deg_df(df)
        assert result is not None
        assert result[0]["gene"] == "g1"

    def test_x_column_mapped_to_gene(self):
        df = pd.DataFrame({
            "X": ["g1", "g2"],
            "avg_log2FC": [1.0, -1.0],
            "p_val_adj": [0.01, 0.02],
        })
        result = standardize_deg_df(df)
        assert result is not None
        assert result[0]["gene"] == "g1"

    def test_case_insensitive_columns(self):
        df = pd.DataFrame({
            "Gene": ["g1"],
            "Avg_Log2FC": [2.0],
            "P_val_adj": [0.001],
        })
        result = standardize_deg_df(df)
        assert result is not None
        assert "gene" in result[0]
        assert "avg_log2FC" in result[0]

    def test_no_gene_column_uses_first(self):
        """If no recognized gene column, the first column becomes 'gene'."""
        df = pd.DataFrame({
            "my_feature": ["f1", "f2"],
            "avg_log2FC": [1.0, -1.0],
            "p_val_adj": [0.01, 0.02],
        })
        result = standardize_deg_df(df)
        assert result is not None
        assert result[0]["gene"] == "f1"

    def test_p_val_adj_formatted_as_scientific(self):
        df = pd.DataFrame({
            "gene": ["g1"],
            "avg_log2FC": [1.0],
            "p_val_adj": [0.000123],
        })
        result = standardize_deg_df(df)
        assert result is not None
        # p_val_adj should be a scientific notation string
        p_str = result[0]["p_val_adj"]
        assert "e" in str(p_str).lower()

    def test_p_val_adj_raw_preserved(self):
        df = pd.DataFrame({
            "gene": ["g1"],
            "avg_log2FC": [1.0],
            "p_val_adj": [0.000123],
        })
        result = standardize_deg_df(df)
        assert result is not None
        assert abs(result[0]["p_val_adj_raw"] - 0.000123) < 1e-8

    def test_avg_log2fc_rounded(self):
        df = pd.DataFrame({
            "gene": ["g1"],
            "avg_log2FC": [1.123456789],
            "p_val_adj": [0.01],
        })
        result = standardize_deg_df(df)
        assert result is not None
        assert result[0]["avg_log2FC"] == pytest.approx(1.1235, abs=1e-4)

    def test_cluster_column_preserved(self):
        df = pd.DataFrame({
            "gene": ["g1", "g2"],
            "cluster": ["0", "1"],
            "avg_log2FC": [1.0, -1.0],
            "p_val_adj": [0.01, 0.02],
        })
        result = standardize_deg_df(df)
        assert result is not None
        assert result[0]["cluster"] == "0"

    def test_returns_none_on_empty_df(self):
        df = pd.DataFrame()
        result = standardize_deg_df(df)
        # Empty DF may return None or empty list depending on implementation
        assert result is None or result == []


# ---- get_top_n_features_for_cluster ----

class TestGetTopNFeaturesForCluster:
    def test_returns_up_and_down(self, deg_records):
        up, down = get_top_n_features_for_cluster(deg_records, "0", n=3)
        assert len(up) <= 3
        assert len(down) <= 3
        assert len(up) > 0
        assert len(down) > 0

    def test_respects_n_limit(self, deg_records):
        up, down = get_top_n_features_for_cluster(deg_records, "0", n=2)
        assert len(up) <= 2
        assert len(down) <= 2

    def test_nonexistent_cluster(self, deg_records):
        up, down = get_top_n_features_for_cluster(deg_records, "99", n=3)
        assert up == []
        assert down == []

    def test_empty_data(self):
        up, down = get_top_n_features_for_cluster([], "0", n=3)
        assert up == []
        assert down == []

    def test_none_data(self):
        up, down = get_top_n_features_for_cluster(None, "0", n=3)
        assert up == []
        assert down == []

    def test_features_are_strings(self, deg_records):
        up, down = get_top_n_features_for_cluster(deg_records, "0", n=5)
        for feature in up + down:
            assert isinstance(feature, str)

    def test_no_duplicates(self, deg_records):
        up, down = get_top_n_features_for_cluster(deg_records, "0", n=10)
        assert len(up) == len(set(up))
        assert len(down) == len(set(down))

    def test_all_positive_fc_cluster(self):
        records = [
            {"gene": f"g{i}", "cluster": "0", "avg_log2FC": float(i + 1),
             "p_val_adj": "1.00e-05", "p_val_adj_raw": 1e-5}
            for i in range(5)
        ]
        up, down = get_top_n_features_for_cluster(records, "0", n=3)
        assert len(up) <= 3
        assert down == []

    def test_all_negative_fc_cluster(self):
        records = [
            {"gene": f"g{i}", "cluster": "0", "avg_log2FC": -float(i + 1),
             "p_val_adj": "1.00e-05", "p_val_adj_raw": 1e-5}
            for i in range(5)
        ]
        up, down = get_top_n_features_for_cluster(records, "0", n=3)
        assert up == []
        assert len(down) <= 3
