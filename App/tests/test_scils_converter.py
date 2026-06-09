"""Tests for app.services.scils_converter (Intensity+Spot+Annotation 方式)"""

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from app.services import scils_converter as sc


# ---------------------------------------------------------------------------
# Helpers: 小規模な Intensity / Spot / Annotation CSV を生成
# ---------------------------------------------------------------------------

def _write_intensity_csv(folder: Path, name: str, mz_values: list[float], spot_numbers: list[int],
                         matrix: np.ndarray) -> Path:
    """Intensity CSV を生成する。

    Parameters
    ----------
    mz_values : 行ヘッダ (m/z 値) の配列, 長さ = n_mz
    spot_numbers : 列ヘッダ `Spot N` の N 部分, 長さ = n_spots
    matrix : shape (n_mz, n_spots) の強度値
    """
    folder.mkdir(parents=True, exist_ok=True)
    headers = ["m/z"] + [f"Spot {n}" for n in spot_numbers]
    rows = []
    for i, mz in enumerate(mz_values):
        row = [str(mz)] + [str(matrix[i, j]) for j in range(len(spot_numbers))]
        rows.append(",".join(row))
    path = folder / name
    path.write_text(",".join(headers) + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def _write_spot_csv(folder: Path, name: str, spot_numbers: list[int], xs: list[float],
                    ys: list[float]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"Spot index": spot_numbers, "X": xs, "Y": ys})
    path = folder / name
    df.to_csv(path, index=False)
    return path


# ---------------------------------------------------------------------------
# classify_csv_role / auto_detect_file_roles
# ---------------------------------------------------------------------------

class TestClassifyCsvRole:
    def test_intensity_detected(self, tmp_path):
        p = _write_intensity_csv(
            tmp_path, "sample_Intensity.csv",
            mz_values=[100.5, 200.1, 300.25],
            spot_numbers=list(range(1, 11)),
            matrix=np.arange(30).reshape(3, 10).astype(float),
        )
        assert sc.classify_csv_role(p) == "intensity"

    def test_spot_like_detected(self, tmp_path):
        p = _write_spot_csv(tmp_path, "sample_Spot.csv", [1, 2, 3], [10, 20, 30], [40, 50, 60])
        assert sc.classify_csv_role(p) == "spot_like"

    def test_unknown(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text("foo,bar\n1,2\n")
        assert sc.classify_csv_role(p) == "unknown"


class TestAutoDetectFileRoles:
    def test_intensity_plus_spot(self, tmp_path):
        _write_intensity_csv(
            tmp_path, "sample_Intensity.csv",
            mz_values=[100.5, 200.1],
            spot_numbers=list(range(1, 8)),
            matrix=np.arange(14).reshape(2, 7).astype(float),
        )
        _write_spot_csv(
            tmp_path, "sample_Spot.csv",
            list(range(1, 8)), list(range(10, 17)), list(range(20, 27)),
        )
        roles = sc.auto_detect_file_roles(tmp_path)
        assert roles["intensity"].name == "sample_Intensity.csv"
        assert roles["spot"].name == "sample_Spot.csv"
        assert roles["annotations"] == []

    def test_with_annotations(self, tmp_path):
        _write_intensity_csv(
            tmp_path, "data_Intensity.csv",
            mz_values=[100.5, 200.1],
            spot_numbers=list(range(1, 11)),
            matrix=np.arange(20).reshape(2, 10).astype(float),
        )
        _write_spot_csv(
            tmp_path, "data_Spot.csv",
            list(range(1, 11)), list(range(10, 20)), list(range(20, 30)),
        )
        # 小さめの annotation 2 本 (Spot より小さいサイズで分離できるように spot 数を絞る)
        _write_spot_csv(
            tmp_path, "Brain_Annotation.csv",
            [1, 2, 3], [10, 11, 12], [20, 21, 22],
        )
        _write_spot_csv(
            tmp_path, "Heart_Annotation.csv",
            [4, 5], [13, 14], [23, 24],
        )
        roles = sc.auto_detect_file_roles(tmp_path)
        assert roles["intensity"].name == "data_Intensity.csv"
        assert roles["spot"].name == "data_Spot.csv"
        ann_names = sorted(p.name for p in roles["annotations"])
        assert ann_names == ["Brain_Annotation.csv", "Heart_Annotation.csv"]

    def test_raises_when_no_intensity(self, tmp_path):
        _write_spot_csv(tmp_path, "only_Spot.csv", [1, 2], [10, 20], [30, 40])
        with pytest.raises(ValueError, match="Intensity CSV が見つかりません"):
            sc.auto_detect_file_roles(tmp_path)

    def test_raises_when_multiple_intensity(self, tmp_path):
        _write_intensity_csv(
            tmp_path, "a_Intensity.csv", [100.0], list(range(1, 7)),
            np.arange(6).reshape(1, 6).astype(float),
        )
        _write_intensity_csv(
            tmp_path, "b_Intensity.csv", [200.0], list(range(1, 7)),
            np.arange(6).reshape(1, 6).astype(float),
        )
        _write_spot_csv(tmp_path, "a_Spot.csv", list(range(1, 7)),
                        list(range(6)), list(range(6)))
        with pytest.raises(ValueError, match="複数"):
            sc.auto_detect_file_roles(tmp_path)

    def test_raises_when_folder_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            sc.auto_detect_file_roles(tmp_path / "does_not_exist")


# ---------------------------------------------------------------------------
# compute_spot_mapping
# ---------------------------------------------------------------------------

class TestComputeSpotMapping:
    def test_strict_match_sorted_by_y_then_x(self):
        # Intensity header: Spot 1, Spot 2, Spot 3
        int_headers = ["m/z", "Spot 1", "Spot 2", "Spot 3"]
        # Spot table: y=[30, 10, 20], x=[1, 2, 3] → 並び替え後 (y 昇順): Spot2, Spot3, Spot1
        spot_index = np.array([1, 2, 3], dtype=np.int64)
        x_arr = np.array([1.0, 2.0, 3.0])
        y_arr = np.array([30.0, 10.0, 20.0])

        _, x_sorted, y_sorted, labels_sorted, idx_sorted, warnings = sc.compute_spot_mapping(
            int_headers, spot_index, x_arr, y_arr
        )
        assert warnings == []
        assert list(labels_sorted) == ["Spot 2", "Spot 3", "Spot 1"]
        assert list(idx_sorted) == [2, 3, 1]
        assert list(y_sorted) == [10.0, 20.0, 30.0]
        assert list(x_sorted) == [2.0, 3.0, 1.0]

    def test_plus1_shift_auto_corrected(self):
        # Intensity が Spot 2..4 で、Spot テーブルが 1..3 → +1 シフト
        int_headers = ["m/z", "Spot 2", "Spot 3", "Spot 4"]
        spot_index = np.array([1, 2, 3], dtype=np.int64)
        x_arr = np.array([10.0, 20.0, 30.0])
        y_arr = np.array([1.0, 2.0, 3.0])
        _, _, _, _, idx_sorted, warnings = sc.compute_spot_mapping(
            int_headers, spot_index, x_arr, y_arr
        )
        # ラベル -1 補正で 1..3 になったあと (y 昇順) そのまま
        assert list(idx_sorted) == [1, 2, 3]
        assert any("+1" in w for w in warnings)

    def test_mismatch_raises(self):
        int_headers = ["m/z", "Spot 1", "Spot 2", "Spot 100"]
        spot_index = np.array([1, 2, 3], dtype=np.int64)
        x_arr = np.array([1.0, 2.0, 3.0])
        y_arr = np.array([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="一致しません"):
            sc.compute_spot_mapping(int_headers, spot_index, x_arr, y_arr)

    def test_duplicate_header_raises(self):
        int_headers = ["m/z", "Spot 1", "Spot 1", "Spot 2"]
        spot_index = np.array([1, 2], dtype=np.int64)
        x_arr = np.array([1.0, 2.0])
        y_arr = np.array([1.0, 2.0])
        with pytest.raises(ValueError, match="重複"):
            sc.compute_spot_mapping(int_headers, spot_index, x_arr, y_arr)


# ---------------------------------------------------------------------------
# build_annotation_map
# ---------------------------------------------------------------------------

class TestBuildAnnotationMap:
    def test_covers_all_spots(self, tmp_path):
        spot_index = np.array([1, 2, 3, 4], dtype=np.int64)
        x_arr = np.array([1.0, 2.0, 3.0, 4.0])
        y_arr = np.array([1.0, 2.0, 3.0, 4.0])

        ann1 = _write_spot_csv(tmp_path, "Brain_Annotation.csv", [1, 2], [1, 2], [1, 2])
        ann2 = _write_spot_csv(tmp_path, "Heart_Annotation.csv", [3, 4], [3, 4], [3, 4])
        mapping, warnings = sc.build_annotation_map([ann1, ann2], spot_index, x_arr, y_arr)
        assert mapping == {1: "Brain", 2: "Brain", 3: "Heart", 4: "Heart"}
        assert warnings == []

    def test_unlabeled_spots_warned(self, tmp_path):
        spot_index = np.array([1, 2, 3], dtype=np.int64)
        x_arr = np.array([1.0, 2.0, 3.0])
        y_arr = np.array([1.0, 2.0, 3.0])
        ann = _write_spot_csv(tmp_path, "Brain_Annotation.csv", [1, 2], [1, 2], [1, 2])
        mapping, warnings = sc.build_annotation_map([ann], spot_index, x_arr, y_arr)
        assert mapping[3] == "Unannotated"
        assert any("Unannotated" in w for w in warnings)

    def test_duplicate_spot_raises(self, tmp_path):
        spot_index = np.array([1, 2], dtype=np.int64)
        x_arr = np.array([1.0, 2.0])
        y_arr = np.array([1.0, 2.0])
        ann1 = _write_spot_csv(tmp_path, "A_Annotation.csv", [1, 2], [1, 2], [1, 2])
        ann2 = _write_spot_csv(tmp_path, "B_Annotation.csv", [1], [1], [1])
        with pytest.raises(ValueError, match="領域アノテーションが重複"):
            sc.build_annotation_map([ann1, ann2], spot_index, x_arr, y_arr)

    def test_coord_mismatch_raises(self, tmp_path):
        spot_index = np.array([1, 2], dtype=np.int64)
        x_arr = np.array([1.0, 2.0])
        y_arr = np.array([1.0, 2.0])
        ann = _write_spot_csv(tmp_path, "A_Annotation.csv", [1, 2], [999.0, 2.0], [1.0, 2.0])
        with pytest.raises(ValueError, match="座標"):
            sc.build_annotation_map([ann], spot_index, x_arr, y_arr)


# ---------------------------------------------------------------------------
# End-to-end convert_scils_to_parquet
# ---------------------------------------------------------------------------

def _make_basic_pair(folder: Path, *, add_annotation: bool = False) -> None:
    """3 m/z × 5 spot の小規模ペアを生成"""
    mz_values = [100.5, 300.25, 200.1]  # 意図的に非ソート
    spot_numbers = [1, 2, 3, 4, 5]
    # 値は分かりやすく spot_i * 10 + mz_j の形
    matrix = np.array([
        [11, 12, 13, 14, 15],  # m/z 100.5
        [21, 22, 23, 24, 25],  # m/z 300.25
        [31, 32, 33, 34, 35],  # m/z 200.1
    ], dtype=float)
    _write_intensity_csv(folder, "sample_Intensity.csv", mz_values, spot_numbers, matrix)
    # spot 座標: y 昇順で spot 5,4,3,2,1 になるよう逆順に配置
    _write_spot_csv(
        folder, "sample_Spot.csv",
        spot_numbers,
        xs=[1.0, 2.0, 3.0, 4.0, 5.0],
        ys=[5.0, 4.0, 3.0, 2.0, 1.0],
    )
    if add_annotation:
        _write_spot_csv(folder, "Brain_Annotation.csv", [1, 2, 3], [1, 2, 3], [5, 4, 3])
        _write_spot_csv(folder, "Heart_Annotation.csv", [4, 5], [4, 5], [2, 1])


class TestConvertEndToEnd:
    def test_basic_parquet_columns_and_values(self, tmp_path):
        data_dir = tmp_path / "scils"
        out_dir = tmp_path / "out"
        _make_basic_pair(data_dir, add_annotation=False)

        result = sc.convert_scils_to_parquet(
            str(data_dir), str(out_dir / "sample.parquet"),
            organize=False, store_float32=False,
        )

        out_path = Path(result.output_path)
        assert out_path.is_file()
        assert result.n_spots == 5
        assert result.n_mz_features == 3
        assert result.has_annotation is False
        # 単一ラベル (Spot ファイル名由来)
        assert result.annotation_labels == ["sample"]

        df = pd.read_parquet(out_path)
        # 列名: id, x, y, 100.500000, 200.100000, 300.250000, annotation
        assert list(df.columns[:3]) == ["id", "x", "y"]
        assert list(df.columns[-1:]) == ["annotation"]
        mz_cols = list(df.columns[3:-1])
        # mz_ プレフィックス無しの数値文字列、昇順
        assert mz_cols == ["100.500000", "200.100000", "300.250000"]
        # (y 昇順) で行が並んでいることを確認: Spot 5 (y=1) → Spot 1 (y=5)
        assert list(df["y"]) == [1.0, 2.0, 3.0, 4.0, 5.0]
        # 値の整合: y=1 (元 Spot 5) の m/z 100.5 列は元 matrix[0, 4] = 15
        assert df.iloc[0]["100.500000"] == pytest.approx(15.0)
        # y=5 (元 Spot 1) の m/z 300.25 列は matrix[1, 0] = 21
        assert df.iloc[4]["300.250000"] == pytest.approx(21.0)

    def test_float32_dtype(self, tmp_path):
        data_dir = tmp_path / "scils"
        out_dir = tmp_path / "out"
        _make_basic_pair(data_dir)
        result = sc.convert_scils_to_parquet(
            str(data_dir), str(out_dir / "sample.parquet"),
            organize=False, store_float32=True,
        )
        schema = pq.ParquetFile(result.output_path).schema_arrow
        # id/x/y/annotation を除いた列はすべて float32
        for field in schema:
            if field.name in ("id", "x", "y", "annotation"):
                continue
            assert str(field.type) == "float", f"{field.name} expected float32"

    def test_with_annotations(self, tmp_path):
        data_dir = tmp_path / "scils"
        out_dir = tmp_path / "out"
        _make_basic_pair(data_dir, add_annotation=True)
        result = sc.convert_scils_to_parquet(
            str(data_dir), str(out_dir / "sample.parquet"),
            organize=False,
        )
        assert result.has_annotation is True
        assert sorted(result.annotation_labels) == ["Brain", "Heart"]
        df = pd.read_parquet(result.output_path)
        # spot 1..3 が Brain, 4..5 が Heart (並びは y 昇順なので逆)
        brain_ys = df.loc[df["annotation"] == "Brain", "y"].tolist()
        heart_ys = df.loc[df["annotation"] == "Heart", "y"].tolist()
        assert sorted(brain_ys) == [3.0, 4.0, 5.0]
        assert sorted(heart_ys) == [1.0, 2.0]

    def test_organize_moves_originals(self, tmp_path):
        data_dir = tmp_path / "scils"
        _make_basic_pair(data_dir, add_annotation=True)
        result = sc.convert_scils_to_parquet(
            str(data_dir), str(data_dir / "sample.parquet"),
            organize=True,
        )
        assert result.organized is True
        sub = data_dir / "sample_Transform"
        assert sub.is_dir()
        # 出力と元 3 ファイルがサブフォルダに存在
        assert (sub / "sample.parquet").is_file()
        assert (sub / "sample_Intensity.csv").is_file()
        assert (sub / "sample_Spot.csv").is_file()
        assert (sub / "Brain_Annotation.csv").is_file()
        # 元の場所には残っていない
        assert not (data_dir / "sample_Intensity.csv").exists()

    def test_organize_false_preserves_originals(self, tmp_path):
        data_dir = tmp_path / "scils"
        out_dir = tmp_path / "out"
        _make_basic_pair(data_dir)
        sc.convert_scils_to_parquet(
            str(data_dir), str(out_dir / "sample.parquet"),
            organize=False,
        )
        assert (data_dir / "sample_Intensity.csv").exists()
        assert (data_dir / "sample_Spot.csv").exists()


class TestConvertErrors:
    def test_missing_folder_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            sc.convert_scils_to_parquet(
                str(tmp_path / "nope"), str(tmp_path / "out.parquet"),
                organize=False,
            )

    def test_no_csv_in_folder_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError):
            sc.convert_scils_to_parquet(
                str(empty), str(tmp_path / "out.parquet"),
                organize=False,
            )


# ---------------------------------------------------------------------------
# annotation_label_from_filename
# ---------------------------------------------------------------------------

class TestAnnotationLabel:
    @pytest.mark.parametrize("fname, expected", [
        ("Brain_Annotation.csv", "Brain"),
        ("Liver_Spot.csv", "Liver"),
        ("Sample_Intensity.csv", "Sample"),
        ("foo_ANNOTATION.csv", "foo"),
        ("MyData.csv", "MyData"),
    ])
    def test_extracts_label(self, tmp_path, fname, expected):
        p = tmp_path / fname
        p.write_text("dummy")
        assert sc.annotation_label_from_filename(p) == expected


# ---------------------------------------------------------------------------
# _read_peaklist: Name 内に区切り文字 (';') を含む SCiLS Feature list の堅牢読み込み
# ---------------------------------------------------------------------------

class TestReadPeaklist:
    def _write_feature_list(self, folder: Path, name: str) -> Path:
        """`#` コメント + ';' 区切り + adduct_family(';' 内包) 行を持つ SCiLS Feature list。"""
        folder.mkdir(parents=True, exist_ok=True)
        text = (
            "# Exported with SCiLS Lab\n"
            "# Object type: Static feature list\n"
            "m/z;Interval Width (+/- Da);Color;Name;Int1;Int2\n"
            # 通常行（Name 内に ';' 無し）
            "346.0547;0.004;#a6cee3;AMP | purine_nucleotide | [M-H]- | 1.88ppm | "
            "formula=C10H14N5O7P | SMILES=NA;100;200\n"
            # Name 内に adduct_family の ';' を含む行（超過トークン）
            "887.5629;0.010;#ff83fa;PI 38:3 | PI | [M-H]- | 1.61ppm | formula=C47H85O13P | "
            "SMILES=NA | adduct_family=mass_only_same_molecule;n=2;adducts=[M-H]-,[M]-;peaks=12,47 | "
            "image_distribution=no;300;400\n"
            # m/z が数値でない行（除外される）
            "not_a_number;0.0;#000;junk;0;0\n"
        )
        path = folder / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_semicolon_in_name_is_recovered(self, tmp_path):
        from app.services import peak_annotation as pann
        p = self._write_feature_list(tmp_path, "feature_list.csv")
        mz, names = sc._read_peaklist(p)

        # 数値でない m/z 行は除外され、2 feature が読める
        assert np.allclose(mz, [346.0547, 887.5629])
        assert len(names) == 2

        # 通常行の Name は欠けない
        assert names[0].startswith("AMP | purine_nucleotide")
        assert names[0].endswith("SMILES=NA")

        # ';' を含む adduct_family が Name 内に原文復元され、後続の通常フィールドも保持
        assert "adduct_family=mass_only_same_molecule;n=2;adducts=[M-H]-,[M]-;peaks=12,47" in names[1]
        assert names[1].endswith("image_distribution=no")

        # parse_scils_name に通すと adduct_family が ';' 込みで取得できる
        rec = pann.parse_scils_name(names[1])
        assert rec["compound"] == "PI 38:3"
        assert rec["adduct"] == "[M-H]-"
        assert rec["adduct_family"] == "mass_only_same_molecule;n=2;adducts=[M-H]-,[M]-;peaks=12,47"
        assert rec["formula"] == "C47H85O13P"

    def test_missing_name_column_raises(self, tmp_path):
        p = tmp_path / "no_name.csv"
        p.write_text("m/z;Color;Int1\n100.0;#fff;5\n", encoding="utf-8")
        with pytest.raises(ValueError):
            sc._read_peaklist(p)

    def test_comma_delimited_name_last_column(self, tmp_path):
        # カンマ区切りで Name が最終列。Name 内の ',' も吸収される（回帰防止）。
        p = tmp_path / "comma.csv"
        p.write_text(
            "m/z,Color,Name\n"
            "419.2572,#fff,ADP | [M-H]- | adducts=[M-H]-,[M]-\n",
            encoding="utf-8",
        )
        mz, names = sc._read_peaklist(p)
        assert np.allclose(mz, [419.2572])
        assert names[0] == "ADP | [M-H]- | adducts=[M-H]-,[M]-"
