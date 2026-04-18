"""Tests for app.services.scils_converter"""

from pathlib import Path

import pandas as pd
import pytest

from app.services import scils_converter as sc


# ---- helpers ----

def _write_csv(folder: Path, name: str, df: pd.DataFrame, sep: str = ",") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    df.to_csv(path, index=False, sep=sep)
    return path


# ---- unit tests for helpers ----

class TestAliasNormalization:
    def test_canonical_id_x_y(self):
        df = pd.DataFrame({"Spot Index": [1], "X": [10], "Y": [20]})
        out = sc.normalize_column_aliases(df)
        assert "id" in out.columns
        assert "x" in out.columns
        assert "y" in out.columns

    def test_m_over_z_column_becomes_mz(self):
        df = pd.DataFrame({"m/z": [100.5], "Intensity": [5.0]})
        out = sc.normalize_column_aliases(df)
        assert "mz" in out.columns
        assert "intensity" in out.columns

    def test_region_becomes_annotation(self):
        df = pd.DataFrame({"Region": ["Brain"]})
        out = sc.normalize_column_aliases(df)
        assert "annotation" in out.columns


class TestShapeInference:
    def test_long_detected_via_mz_intensity(self):
        df = pd.DataFrame({"id": [1], "x": [0], "y": [0], "mz": [100.5], "intensity": [1.0]})
        assert sc.infer_csv_shape(df) == "long"

    def test_wide_detected_via_mz_value_columns(self):
        df = pd.DataFrame({"id": [1], "x": [0], "y": [0], "100.5": [1.0], "200.1": [2.0]})
        assert sc.infer_csv_shape(df) == "wide"

    def test_unknown(self):
        df = pd.DataFrame({"foo": [1], "bar": [2]})
        assert sc.infer_csv_shape(df) == "unknown"


class TestParseMzLabel:
    @pytest.mark.parametrize("label, expected", [
        ("m/z 100.523", 100.523),
        ("mz_123.456", 123.456),
        ("100.5", 100.5),
        ("M/Z 255.1", 255.1),
    ])
    def test_parses(self, label, expected):
        assert sc._parse_mz_from_label(label) == pytest.approx(expected)

    def test_non_mz_returns_none(self):
        assert sc._parse_mz_from_label("annotation") is None


# ---- integration tests for convert_scils_to_parquet ----

class TestConvertLongFormat:
    def test_basic_long_to_parquet(self, tmp_path):
        input_dir = tmp_path / "scils_long"
        # 2 spots × 3 m/z 値
        long_df = pd.DataFrame({
            "spot_id": [1, 1, 1, 2, 2, 2],
            "x":       [10, 10, 10, 20, 20, 20],
            "y":       [30, 30, 30, 40, 40, 40],
            "m/z":     [100.50, 200.10, 300.25, 100.50, 200.10, 300.25],
            "Intensity": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "Region":  ["Brain"] * 6,
        })
        _write_csv(input_dir, "feature_list.csv", long_df)
        out = tmp_path / "out" / "sample.parquet"

        result = sc.convert_scils_to_parquet(str(input_dir), str(out))

        assert result.shape == "long"
        assert result.n_rows == 2
        assert result.n_mz_features == 3
        assert result.has_annotation is True
        assert Path(result.output_path).is_file()

        wide = pd.read_parquet(out)
        expected_cols = {"id", "x", "y", "mz_100.50000", "mz_200.10000", "mz_300.25000", "annotation"}
        assert set(wide.columns) == expected_cols
        assert wide.loc[wide["id"] == 1, "mz_200.10000"].iloc[0] == 2.0
        assert wide.loc[wide["id"] == 2, "mz_300.25000"].iloc[0] == 6.0


class TestConvertWideFormat:
    def test_basic_wide_passthrough(self, tmp_path):
        input_dir = tmp_path / "scils_wide"
        wide_df = pd.DataFrame({
            "Spot": [1, 2],
            "X": [10, 20],
            "Y": [30, 40],
            "m/z 100.5": [1.0, 4.0],
            "m/z 200.1": [2.0, 5.0],
            "m/z 300.25": [3.0, 6.0],
        })
        _write_csv(input_dir, "export.csv", wide_df)
        out = tmp_path / "out" / "sample.parquet"

        result = sc.convert_scils_to_parquet(str(input_dir), str(out))

        assert result.shape == "wide"
        assert result.n_rows == 2
        assert result.n_mz_features == 3
        assert result.has_annotation is False

        wide = pd.read_parquet(out)
        assert "mz_100.50000" in wide.columns
        assert "mz_300.25000" in wide.columns


class TestConvertErrors:
    def test_missing_folder_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            sc.convert_scils_to_parquet(str(tmp_path / "nope"), str(tmp_path / "out.parquet"))

    def test_no_csv_in_folder_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError):
            sc.convert_scils_to_parquet(str(empty), str(tmp_path / "out.parquet"))

    def test_unknown_shape_raises(self, tmp_path):
        folder = tmp_path / "bad"
        _write_csv(folder, "weird.csv", pd.DataFrame({"foo": [1], "bar": [2]}))
        with pytest.raises(ValueError, match="形式を判定"):
            sc.convert_scils_to_parquet(str(folder), str(tmp_path / "out.parquet"))


class TestMissingIdColumn:
    def test_wide_without_id_assigns_row_number(self, tmp_path):
        input_dir = tmp_path / "noid"
        wide_df = pd.DataFrame({
            "x": [10, 20], "y": [30, 40],
            "m/z 100.5": [1.0, 2.0], "m/z 200.1": [3.0, 4.0],
        })
        _write_csv(input_dir, "export.csv", wide_df)
        out = tmp_path / "out" / "sample.parquet"

        result = sc.convert_scils_to_parquet(str(input_dir), str(out))
        assert any("id" in w for w in result.warnings)
        wide = pd.read_parquet(out)
        assert list(wide["id"]) == [1, 2]


class TestDetectScilsCsv:
    def test_picks_largest(self, tmp_path):
        (tmp_path / "small.csv").write_text("a,b\n1,2\n")
        big = tmp_path / "big.csv"
        big.write_text("a,b\n" + "1,2\n" * 1000)
        picked = sc.detect_scils_csv(tmp_path)
        assert picked == big

    def test_returns_none_for_empty(self, tmp_path):
        assert sc.detect_scils_csv(tmp_path) is None
