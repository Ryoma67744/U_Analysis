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
        # ★ ver55.0: Annotation CSV が無いとき、以前は **Spot ファイル名**から
        #   ラベルを作って全 spot に割り当てていた。指定していない領域アノテーションが
        #   「ファイルから付いている」ように見え、変換完了画面が
        #   「Annotation CSV: (なし)」と「annotation ラベル: sample」を同時に出す
        #   矛盾を生んでいた。無いことを 'Unannotated' で明示する。
        #   列そのものは残す（R が slice_id → condition の組み立てに使うため）。
        assert result.annotation_labels == ["Unannotated"]
        assert result.annotation_source == "none"

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


# ---------------------------------------------------------------------------
# 出力 row group レイアウト (全行 1 row group)
# ---------------------------------------------------------------------------

def _row_group_sizes(path) -> list[int]:
    md = pq.ParquetFile(str(path)).metadata
    return [md.row_group(i).num_rows for i in range(md.num_row_groups)]


class TestRowGroupLayout:
    def test_default_is_single_row_group(self, tmp_path):
        data_dir = tmp_path / "scils"
        _make_basic_pair(data_dir)
        result = sc.convert_scils_to_parquet(
            str(data_dir), str(tmp_path / "out" / "sample.parquet"), organize=False,
        )
        assert _row_group_sizes(result.output_path) == [5]

    def test_row_group_rows_explicit(self, tmp_path):
        """row_group_rows=2 → 端数を避けて等分割し、値は既定実行と完全一致する。"""
        base_dir = tmp_path / "base"
        split_dir = tmp_path / "split"
        _make_basic_pair(base_dir)
        _make_basic_pair(split_dir)

        base = sc.convert_scils_to_parquet(
            str(base_dir), str(tmp_path / "o1" / "s.parquet"), organize=False)
        split = sc.convert_scils_to_parquet(
            str(split_dir), str(tmp_path / "o2" / "s.parquet"),
            organize=False, row_group_rows=2)

        sizes = _row_group_sizes(split.output_path)
        assert len(sizes) > 1 and sum(sizes) == 5
        assert max(sizes) <= 2
        pd.testing.assert_frame_equal(
            pd.read_parquet(base.output_path), pd.read_parquet(split.output_path))

    def test_spot_block_independent_of_row_group(self, tmp_path):
        """spot_block は読み取り粒度のみ。row group 数にも内容にも影響しない。"""
        frames = {}
        for i, block in enumerate((1, 1000)):
            d = tmp_path / f"in{i}"
            _make_basic_pair(d)
            r = sc.convert_scils_to_parquet(
                str(d), str(tmp_path / f"out{i}" / "s.parquet"),
                organize=False, spot_block=block)
            assert _row_group_sizes(r.output_path) == [5]
            frames[block] = pd.read_parquet(r.output_path)
        pd.testing.assert_frame_equal(frames[1], frames[1000])

    def test_result_records_layout(self, tmp_path):
        data_dir = tmp_path / "scils"
        _make_basic_pair(data_dir)
        result = sc.convert_scils_to_parquet(
            str(data_dir), str(tmp_path / "out" / "sample.parquet"), organize=False)
        md = pq.ParquetFile(result.output_path).metadata
        assert result.n_row_groups == md.num_row_groups == 1
        assert result.row_group_rows == 5
        assert result.footer_bytes == md.serialized_size > 0
        assert result.row_group_policy == "single"

    def test_fallback_splits_and_warns(self, tmp_path, monkeypatch):
        """予算不足なら分割して警告を出し、値は変えない。

        3 m/z × 5 spot・float32 なので、全行 1 つ = 3*5*4 + meta*1 = 61 バイト、
        1 行/group = 3*1*4 + meta*5 = 17 バイト（meta を 1 バイトに縮めた場合）。
        予算を 30 バイトに固定すると必ず前者だけが弾かれる。

        ★ ver56.8: Phase A フッタは「予算から固定 GB を引く」のをやめてコスト側に
        移したので、_PHASE_A_FOOTER_MARGIN_GB ではなく _PER_CHUNK_META_BYTES を
        0 にして同じ意図（フッタ項を無視して分割だけを見る）を作る。
        """
        monkeypatch.setattr(sc, "_PER_CHUNK_META_BYTES", 0)
        monkeypatch.setattr(sc, "_RG_AVAIL_FRACTION", 1.0)
        monkeypatch.setattr(sc, "_RG_METADATA_BYTES", 1.0)
        monkeypatch.setattr(sc, "_available_memory_gb", lambda **kw: 30 / 1024 ** 3)
        data_dir = tmp_path / "scils"
        _make_basic_pair(data_dir)
        result = sc.convert_scils_to_parquet(
            str(data_dir), str(tmp_path / "out" / "sample.parquet"), organize=False)
        assert result.row_group_policy == "single-fallback"
        assert any("row group" in w for w in result.warnings)
        assert sum(_row_group_sizes(result.output_path)) == 5
        # 分割されても値は不変
        assert list(pd.read_parquet(result.output_path)["y"]) == [1.0, 2.0, 3.0, 4.0, 5.0]

    def test_guard_raises_when_hopeless(self, monkeypatch):
        """どう分割しても載らない場合は明示エラー（フォールバックで誤魔化さない）。"""
        monkeypatch.setattr(sc, "_available_memory_gb", lambda **kw: 2.0)  # 予算 1.2 GB
        with pytest.raises(RuntimeError, match="メモリが不足"):
            sc._plan_row_groups(n_spots=1_000_000, n_mz=100_000, itemsize=8, requested=None)

    def test_plan_prefers_single_when_it_fits(self, monkeypatch):
        monkeypatch.setattr(sc, "_available_memory_gb", lambda **kw: 8.0)
        rg, policy, warns = sc._plan_row_groups(
            n_spots=5_000, n_mz=100, itemsize=4, requested=None)
        assert (rg, policy, warns) == (5_000, "single", [])

    def test_plan_minimizes_u_shaped_cost_on_fallback(self, monkeypatch):
        """フォールバックは「小さくする」のではなく総コストを最小化する。

        row group を細かくすると ParquetWriter が抱えるメタデータが増えるため、
        単調に減らすと逆に悪化する。理論最小 rg* = sqrt(meta * n_spots / (itemsize * n_mz))
        の近傍が選ばれること、かつ結果が予算内であることを確認する。

        ★ ver56.8: 規模を 200,000 spot × 5,000 m/z へ落とした。旧値
        (1,000,000 × 100,000) は Phase A の一時 Parquet フッタだけで約 200GB になり
        （196 row group × 100 万列）、どう刻んでも載らない＝送出が正しい領域なので、
        U 字最小化を確かめる題材として成立しなくなったため。
        """
        monkeypatch.setattr(sc, "_available_memory_gb", lambda **kw: 12.0)  # 予算 7.2 GB
        n_spots, n_mz, itemsize = 200_000, 5_000, 8
        rg, policy, warns = sc._plan_row_groups(
            n_spots=n_spots, n_mz=n_mz, itemsize=itemsize, requested=None)
        assert policy == "single-fallback" and warns
        ideal = (sc._RG_METADATA_BYTES * n_spots / (itemsize * n_mz)) ** 0.5
        assert 0.5 * ideal <= rg <= 2 * ideal
        # 全行 1 つより明確に軽く、かつ両端より軽い
        cost = sc._row_group_cost_bytes(rg, n_spots=n_spots, n_mz=n_mz, itemsize=itemsize)
        for other in (1, n_spots):
            assert cost < sc._row_group_cost_bytes(
                other, n_spots=n_spots, n_mz=n_mz, itemsize=itemsize)

    def test_buffer_row_is_zero_copy(self):
        """軸順 (n_mz, rg_rows) を反転すると pa.array が黙ってコピーする。その回帰ガード。"""
        import pyarrow as pa
        buf = np.empty((4, 8), dtype=np.float32)
        arr = pa.array(buf[1, :5])
        assert arr.buffers()[0] is None                       # validity buffer 無し
        assert arr.buffers()[1].address == buf[1, :5].__array_interface__["data"][0]

    def test_failure_before_write_does_not_clobber_existing_output(self, tmp_path, monkeypatch):
        """書き込み開始前に落ちても既存の出力ファイルは無傷のまま残る。"""
        data_dir = tmp_path / "scils"
        out = tmp_path / "out" / "sample.parquet"
        _make_basic_pair(data_dir)
        good = sc.convert_scils_to_parquet(str(data_dir), str(out), organize=False)
        good_bytes = Path(good.output_path).read_bytes()

        def _boom(*a, **k):
            raise RuntimeError("injected failure")

        monkeypatch.setattr(sc, "_plan_row_groups", _boom)
        with pytest.raises(RuntimeError, match="injected failure"):
            sc.convert_scils_to_parquet(str(data_dir), str(out), organize=False)

        assert Path(good.output_path).read_bytes() == good_bytes
        assert not list(out.parent.glob("*.writing.parquet"))

    def test_failure_mid_write_does_not_clobber_existing_output(self, tmp_path, monkeypatch):
        """row group を 1 つ書いた後に落ちても、既存の出力は上書きされない。

        原子化前はここで「有効だが行数が足りない parquet」が正常ファイルを潰していた。
        下流の Python・R いずれもそれを正常なファイルとして受け入れてしまうため、
        この経路こそが本命のガード対象。
        """
        data_dir = tmp_path / "scils"
        out = tmp_path / "out" / "sample.parquet"
        _make_basic_pair(data_dir)
        good = sc.convert_scils_to_parquet(str(data_dir), str(out), organize=False)
        good_bytes = Path(good.output_path).read_bytes()
        assert pq.ParquetFile(out).metadata.num_rows == 5

        # row_group_rows=2 なら内側ループは row group ごとに 1 回。2 回目で落とすと
        # 「1 つ目の row group だけ書けた一時ファイル」が残る状態を再現できる。
        real_column_stack = np.column_stack
        calls = {"n": 0}

        def _flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("injected mid-write failure")
            return real_column_stack(*a, **k)

        monkeypatch.setattr(sc.np, "column_stack", _flaky)
        with pytest.raises(RuntimeError, match="injected mid-write failure"):
            sc.convert_scils_to_parquet(
                str(data_dir), str(out), organize=False, row_group_rows=2)

        assert calls["n"] >= 2, "書き込みループに入る前に落ちてしまい経路を検証できていない"
        # 既存ファイルは 1 バイトも変わっていない
        assert Path(good.output_path).read_bytes() == good_bytes
        assert pq.ParquetFile(out).metadata.num_rows == 5
        # 中途半端な一時ファイルも残っていない
        assert sorted(p.name for p in out.parent.iterdir()) == ["sample.parquet"]


_GB = 1024 ** 3


class TestConversionMemoryGuard:
    """変換前メモリチェックの番人（ver56.8）。

    ver56.7 以前の `need_gb = CSV サイズ × 1.5 + 1GB` は、Phase A が全経路
    ストリーミングになった後も残っていた比例式で、`mem_limit: 12g` と組み合わさると
    **7.3GB を超える Intensity CSV が空き容量と無関係に必ず弾かれる**という
    構造的な壁になっていた（9.5GB の CSV / 空き 11.8GB で実際に発生）。
    ここは「実ケースが通ること」と「本当に載らないものは弾くこと」を同時に固定する。
    """

    # 実際に失敗した条件。CSV 9.5GB・空き 11.8GB（12GB コンテナ）。
    REAL_CSV_BYTES = int(9.5 * _GB)
    REAL_N_SPOTS = 203_078
    REAL_AVAIL_GB = 11.8

    def _write_fake_csv(self, tmp_path, size_bytes: int) -> Path:
        """指定サイズに見える疎ファイルを作る（実ディスクはほぼ消費しない）。"""
        p = tmp_path / "fake_Intensity.csv"
        with open(p, "wb") as f:
            f.truncate(size_bytes)
        return p

    def test_real_world_case_passes(self, tmp_path, monkeypatch):
        """9.5GB CSV × 空き 11.8GB は通る。これが今回の不具合そのもの。"""
        monkeypatch.setattr(
            sc, "_available_memory_gb", lambda **kw: (self.REAL_AVAIL_GB, "test"))
        csv_path = self._write_fake_csv(tmp_path, self.REAL_CSV_BYTES)
        # 送出しないことが全て（旧実装はここで「約 15.2 GB 必要」と送出していた）
        sc._check_conversion_memory(
            csv_path, n_spots_hint=self.REAL_N_SPOTS, itemsize=4)

    def test_real_world_estimate_is_far_below_old_formula(self):
        """実ケースの見積りが旧式 15.25GB から大きく下がり、空きに収まること。"""
        need_gb, n_mz_est = sc._estimate_conversion_need_gb(
            csv_bytes=self.REAL_CSV_BYTES, n_spots=self.REAL_N_SPOTS, itemsize=4)
        assert need_gb < self.REAL_AVAIL_GB
        old_formula = self.REAL_CSV_BYTES / _GB * 1.5 + 1.0      # = 15.25
        assert need_gb < old_formula / 2
        assert 3_000 < n_mz_est < 6_000       # 9.5GB / (203,079 列 × 11B) ≒ 4,566

    def test_need_stays_well_below_csv_size(self):
        """大きい CSV ほど「CSV サイズより十分小さい」必要量になること。

        Phase A はストリーミングなので、CSV サイズはピーク RAM を直接は決めない。
        旧式 (`CSV × 1.5 + 1`) は必ず CSV サイズを上回るため、この不等式が
        比例項の復活を検出する番人になる（12GB コンテナで CSV が 8GB を超えると、
        比例項がある限り空き容量と無関係に弾かれてしまう）。
        """
        for csv_gb in (5, 10, 20, 40):
            need_gb, _ = sc._estimate_conversion_need_gb(
                csv_bytes=csv_gb * _GB, n_spots=self.REAL_N_SPOTS, itemsize=4)
            assert need_gb < csv_gb / 2, f"CSV {csv_gb}GB の必要量 {need_gb:.2f}GB が大きすぎる"

    def test_quadrupling_csv_does_not_quadruple_need(self):
        """CSV を 4 倍にしても必要量は 4 倍にならない。"""
        base, _ = sc._estimate_conversion_need_gb(
            csv_bytes=5 * _GB, n_spots=self.REAL_N_SPOTS, itemsize=4)
        quad, _ = sc._estimate_conversion_need_gb(
            csv_bytes=20 * _GB, n_spots=self.REAL_N_SPOTS, itemsize=4)
        assert quad < base * 4

    def test_hopeless_case_still_raises(self, tmp_path, monkeypatch):
        """本当に載らないデータは Phase A に入る前に弾く（ガードを殺していない）。"""
        monkeypatch.setattr(sc, "_available_memory_gb", lambda **kw: (2.0, "test"))
        csv_path = self._write_fake_csv(tmp_path, 200 * _GB)
        with pytest.raises(RuntimeError, match="空きメモリが不足"):
            sc._check_conversion_memory(csv_path, n_spots_hint=1_000_000, itemsize=8)

    def test_small_csv_skips_check(self, tmp_path, monkeypatch):
        """0.5GB 未満は空き 0 でも素通りする（従来どおり）。"""
        monkeypatch.setattr(sc, "_available_memory_gb", lambda **kw: (0.0, "test"))
        csv_path = self._write_fake_csv(tmp_path, int(0.1 * _GB))
        sc._check_conversion_memory(csv_path, n_spots_hint=1_000, itemsize=4)

    def test_skips_when_spot_count_unknown(self, tmp_path, monkeypatch):
        """spot 数が読めなければ見積れないので判定しない（_plan_row_groups に委ねる）。"""
        monkeypatch.setattr(sc, "_available_memory_gb", lambda **kw: (0.1, "test"))
        csv_path = self._write_fake_csv(tmp_path, 50 * _GB)
        sc._check_conversion_memory(csv_path, n_spots_hint=0, itemsize=4)

    def test_phase_a_footer_scales_with_n_mz(self):
        """一時 Parquet のフッタは n_mz に比例する。旧実装の固定 1.5GB では追えない。"""
        small = sc._phase_a_footer_bytes(n_spots=203_078, n_mz=2_700)
        large = sc._phase_a_footer_bytes(n_spots=203_078, n_mz=27_000)
        # ★ ver60.0: 実データ規模の絶対値は _TEMP_PARQUET_ROW_GROUP_SIZE から導く。
        #   ここに GB の直値を書くと、あの定数を触るたびに「実装は正しいのに落ちる」
        #   テストになる（512 → 1024 にした ver60.0 で実際にそうなった）。
        #   守りたいのは「chunk 数から算出していること」であって特定の GB 値ではない。
        n_rg = -(-2_700 // sc._TEMP_PARQUET_ROW_GROUP_SIZE)
        assert small == pytest.approx(n_rg * (203_078 + 1) * sc._PER_CHUNK_META_BYTES)
        # 10 倍の m/z ではおよそ 10 倍（固定 1.5GB なら見逃していた領域）
        assert 8 < large / small < 12
        # 固定値に戻す退行のガード: 定数を倍にすればフッタは半分になる
        assert large > small

    def test_footer_is_counted_in_row_group_cost(self):
        """フッタ項がコストに乗っていること（予算から引く旧方式に戻さない）。"""
        kwargs = dict(n_spots=203_078, n_mz=2_700, itemsize=4)
        cost = sc._row_group_cost_bytes(203_078, **kwargs)
        buffer_and_meta = 2_700 * 203_078 * 4 + sc._RG_METADATA_BYTES
        assert cost - buffer_and_meta == pytest.approx(
            sc._phase_a_footer_bytes(n_spots=203_078, n_mz=2_700))

    def test_available_memory_adds_reclaimable_cache(self, tmp_path, monkeypatch):
        """memory.current に含まれる回収可能なページキャッシュを空きへ戻す。

        補正が無いと、大きな CSV を読んだ直後ほど「空きが少ない」と誤認し、
        再試行するほど弾かれやすくなる。
        """
        current = tmp_path / "memory.current"
        stat = tmp_path / "memory.stat"
        current.write_text(str(10 * _GB), encoding="utf-8")     # 使用中 10GB
        stat.write_text(f"anon 1000\ninactive_file {6 * _GB}\nslab 20\n", encoding="utf-8")
        monkeypatch.setattr(sc, "_CGROUP_CURRENT_PATHS", (str(current),))
        monkeypatch.setattr(sc, "_CGROUP_STAT_PATHS", (str(stat),))

        monkeypatch.setattr(sc, "_container_limit_gb", lambda: 12.0)

        gb, source = sc._available_memory_gb(with_source=True)
        # 12 - 10 = 2GB ではなく、回収可能な 6GB を戻した 8GB
        assert gb == pytest.approx(8.0)
        assert source == "cgroup+cache"

    def test_available_memory_never_exceeds_limit(self, tmp_path, monkeypatch):
        """キャッシュを足し戻しても cgroup 上限は超えない。"""
        current = tmp_path / "memory.current"
        stat = tmp_path / "memory.stat"
        current.write_text(str(2 * _GB), encoding="utf-8")
        stat.write_text(f"inactive_file {50 * _GB}\n", encoding="utf-8")
        monkeypatch.setattr(sc, "_CGROUP_CURRENT_PATHS", (str(current),))
        monkeypatch.setattr(sc, "_CGROUP_STAT_PATHS", (str(stat),))

        monkeypatch.setattr(sc, "_container_limit_gb", lambda: 12.0)

        assert sc._available_memory_gb() == pytest.approx(12.0)

    def test_pyarrow_fallback_batches_into_large_row_groups(self, tmp_path, monkeypatch):
        """pyarrow 経路が「1 バッチ = 1 row group」で書かないこと。

        ver56.7 以前は `w.write_batch(batch)` の素の繰り返しだったため、
        バッチ数がそのまま row group 数になっていた。実データ規模では 1 バッチが
        十数行にしかならず、column chunk 数 = row group 数 × spot 数 が
        Phase B のフッタ常駐量を押し上げる（polars 経路とも挙動が乖離していた）。
        読み込みブロックを小さくして大量のバッチを作り、それでも row group が
        ceil(n_mz / 512) 程度に収まることを固定する。
        """
        monkeypatch.setenv("SCILS_NO_POLARS", "1")          # pyarrow 経路を強制
        monkeypatch.setattr(sc, "_CSV_READ_BLOCK_BYTES", 4096)   # 多数のバッチを作る

        n_mz, n_spots = 2_000, 6
        data_dir = tmp_path / "scils"
        _write_intensity_csv(
            data_dir, "big_Intensity.csv",
            mz_values=[100.0 + i for i in range(n_mz)],
            spot_numbers=list(range(1, n_spots + 1)),
            matrix=np.arange(n_mz * n_spots).reshape(n_mz, n_spots).astype(float),
        )
        csv_path = data_dir / "big_Intensity.csv"
        headers, delim, skip = sc.first_header_and_skipcount(csv_path)
        temp = tmp_path / "temp.parquet"
        sc._csv_to_temp_parquet(csv_path, headers, delim, skip, temp)

        md = pq.ParquetFile(str(temp)).metadata
        assert md.num_rows == n_mz
        expected_max = -(-n_mz // sc._TEMP_PARQUET_ROW_GROUP_SIZE) + 1     # = 5
        assert md.num_row_groups <= expected_max, (
            f"row group が {md.num_row_groups} 個。バッチごとに書いている疑い"
        )

    def test_available_memory_without_stat_falls_back(self, tmp_path, monkeypatch):
        """memory.stat が読めなければ従来どおり「上限 - 使用中」。"""
        current = tmp_path / "memory.current"
        current.write_text(str(4 * _GB), encoding="utf-8")
        monkeypatch.setattr(sc, "_CGROUP_CURRENT_PATHS", (str(current),))
        monkeypatch.setattr(sc, "_CGROUP_STAT_PATHS", (str(tmp_path / "missing"),))

        monkeypatch.setattr(sc, "_container_limit_gb", lambda: 12.0)

        gb, source = sc._available_memory_gb(with_source=True)
        assert (gb, source) == (pytest.approx(8.0), "cgroup")


class TestLegacyLayoutCompat:
    """旧レイアウト (200 行/row group 相当 = 複数 row group) も読めることを固定する。

    row group 単位の API を使う reader はリポジトリに無いので本来壊れないが、
    将来 row group 数に依存するコードが混入した場合に気づけるようにしておく。
    """

    @staticmethod
    def _pair(tmp_path):
        """同一内容を「全行 1 row group」と「複数 row group」で出力して返す。"""
        new_dir, old_dir = tmp_path / "new_in", tmp_path / "old_in"
        _make_basic_pair(new_dir, add_annotation=True)
        _make_basic_pair(old_dir, add_annotation=True)
        new = sc.convert_scils_to_parquet(
            str(new_dir), str(tmp_path / "new" / "s.parquet"), organize=False)
        old = sc.convert_scils_to_parquet(
            str(old_dir), str(tmp_path / "old" / "s.parquet"),
            organize=False, row_group_rows=2)
        assert len(_row_group_sizes(new.output_path)) == 1
        assert len(_row_group_sizes(old.output_path)) > 1
        return Path(new.output_path), Path(old.output_path)

    def test_content_identical_across_layouts(self, tmp_path):
        new_p, old_p = self._pair(tmp_path)
        pd.testing.assert_frame_equal(pd.read_parquet(new_p), pd.read_parquet(old_p))
        # スキーマメタデータ (mz_sorted / annotation_files) も同一
        assert (pq.ParquetFile(new_p).schema_arrow.metadata
                == pq.ParquetFile(old_p).schema_arrow.metadata)

    def test_column_selection_identical(self, tmp_path):
        new_p, old_p = self._pair(tmp_path)
        cols = ["id", "100.500000", "annotation"]
        pd.testing.assert_frame_equal(
            pd.read_parquet(new_p, columns=cols), pd.read_parquet(old_p, columns=cols))

    def test_read_parquet_annotations_identical(self, tmp_path):
        from app.services.data_manager import read_parquet_annotations
        new_p, old_p = self._pair(tmp_path)
        got = read_parquet_annotations(str(old_p))
        assert got == read_parquet_annotations(str(new_p))
        assert got == ["Brain", "Heart"]

    def test_mz_sorted_metadata_identical(self, tmp_path):
        from app.services.data_manager import _read_mz_sorted_metadata
        new_p, old_p = self._pair(tmp_path)
        old_mz = _read_mz_sorted_metadata(pq.ParquetFile(old_p))
        new_mz = _read_mz_sorted_metadata(pq.ParquetFile(new_p))
        assert old_mz is not None
        assert np.allclose(old_mz, new_mz)
        assert np.allclose(old_mz, [100.5, 200.1, 300.25])

    def test_mixed_folder_lists_both(self, tmp_path):
        """新旧が同じフォルダに混在しても候補列挙は両方を返す。"""
        from app.services.data_manager import _filter_tims_candidates
        new_p, old_p = self._pair(tmp_path)
        mixed = tmp_path / "mixed"
        mixed.mkdir()
        for src, name in ((new_p, "new.parquet"), (old_p, "old.parquet")):
            (mixed / name).write_bytes(src.read_bytes())
        names = {p.name for p in _filter_tims_candidates(mixed)}
        assert names == {"new.parquet", "old.parquet"}
