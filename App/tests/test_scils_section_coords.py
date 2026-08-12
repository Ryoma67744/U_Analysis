"""切片ごとの座標 CSV しか無いフォルダの変換 (ver55.0)

背景: `auto_detect_file_roles` は SpotIndex/X/Y を持つ CSV を**サイズ降順に並べて
先頭をマスター Spot テーブルに確定**していた。切片ごとに座標を Export した
フォルダには測定全体を覆うマスターが存在しないため、一番大きい切片 1 枚だけが
マスターに昇格し、残りの切片は「マスターの部分集合であること」を要求されて
必ず変換が落ちていた。仮に数が合っても、マスターに吸収された切片は領域ラベルを
失っていた。

レイアウトの判定を spot 集合に基づく `build_master_spot_table` へ移し、
マスターが無いときは全ファイルを統合してファイル名を領域ラベルにする。
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.services import scils_converter as sc


def _write_intensity(folder: Path, name: str, mz_values: list[float],
                     spot_numbers: list[int]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    headers = ["m/z"] + [f"Spot {n}" for n in spot_numbers]
    rng = np.arange(len(mz_values) * len(spot_numbers), dtype=float)
    matrix = rng.reshape(len(mz_values), len(spot_numbers))
    rows = [
        ",".join([str(mz)] + [str(matrix[i, j]) for j in range(len(spot_numbers))])
        for i, mz in enumerate(mz_values)
    ]
    path = folder / name
    path.write_text(",".join(headers) + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def _write_coords(folder: Path, name: str, spot_numbers: list[int]) -> Path:
    """SpotIndex/X/Y CSV。座標は spot 番号から決定的に作る。"""
    folder.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({
        "Spot index": spot_numbers,
        "X": [float(n % 10) for n in spot_numbers],
        "Y": [float(n // 10) for n in spot_numbers],
    })
    path = folder / name
    df.to_csv(path, index=False)
    return path


class TestSectionCoordinateFiles:
    def test_union_of_section_files_becomes_master(self, tmp_path):
        """全体を覆うマスターが無ければ切片を統合し、ファイル名が領域ラベルになる"""
        data_dir = tmp_path / "scils"
        _write_intensity(data_dir, "A_Intensity.csv", [100.0, 200.0], [1, 2, 3, 4, 5, 6])
        _write_coords(data_dir, "01.csv", [1, 2, 3])
        _write_coords(data_dir, "02.csv", [4, 5, 6])

        result = sc.convert_scils_to_parquet(
            str(data_dir), str(tmp_path / "out.parquet"),
            organize=False, store_float32=False,
        )

        # 6 spot すべてが残る = どのファイルも「名前の無いマスター」に吸われていない
        assert result.n_spots == 6
        assert result.annotation_labels == ["01", "02"]
        assert result.annotation_source == "csv"

        df = pd.read_parquet(result.output_path)
        label_of = dict(zip(df["id"], df["annotation"]))
        assert [label_of[i] for i in (1, 2, 3)] == ["01", "01", "01"]
        assert [label_of[i] for i in (4, 5, 6)] == ["02", "02", "02"]

    def test_master_plus_annotation_layout_is_unchanged(self, tmp_path):
        """従来レイアウト (全体マスター + 部分集合の注釈) は挙動が変わらない"""
        data_dir = tmp_path / "scils"
        _write_intensity(data_dir, "s_Intensity.csv", [100.0, 200.0], [1, 2, 3, 4, 5, 6])
        _write_coords(data_dir, "s_Spot.csv", [1, 2, 3, 4, 5, 6])
        _write_coords(data_dir, "Brain_Annotation.csv", [1, 2])

        result = sc.convert_scils_to_parquet(
            str(data_dir), str(tmp_path / "out.parquet"),
            organize=False, store_float32=False,
        )

        assert result.n_spots == 6
        # マスターはラベルを持たない。注釈に無い spot は Unannotated。
        assert result.annotation_labels == ["Brain", "Unannotated"]
        assert "s_Spot" not in result.annotation_labels
        assert "s" not in result.annotation_labels

    def test_missing_section_is_skipped_by_default(self, tmp_path):
        """切片統合レイアウトでは、座標を出していない切片があっても変換が通る。

        欲しい切片の座標だけを Export する（例: 04 の座標を出さない）のが通常の
        運用なので、ここでの「座標が無い spot」は不整合ではなく前提。
        ただし捨てた事実は必ず申告する。
        """
        data_dir = tmp_path / "scils"
        _write_intensity(data_dir, "A_Intensity.csv", [100.0, 200.0], [1, 2, 3, 4, 5, 6])
        _write_coords(data_dir, "01.csv", [1, 2, 3])
        _write_coords(data_dir, "02.csv", [4, 5])
        # spot 6 は「座標を出していない切片」に相当する

        result = sc.convert_scils_to_parquet(
            str(data_dir), str(tmp_path / "out.parquet"),
            organize=False, store_float32=False,
        )

        assert result.n_spots == 5
        df = pd.read_parquet(result.output_path)
        assert sorted(df["id"]) == [1, 2, 3, 4, 5]
        assert result.annotation_labels == ["01", "02"]
        assert any("除外しました" in w for w in result.warnings), result.warnings

    def test_partial_coverage_still_errors_with_a_master_spot_csv(self, tmp_path):
        """マスター Spot CSV がある構成では、数が合わないのは本物の不整合"""
        data_dir = tmp_path / "scils"
        _write_intensity(data_dir, "s_Intensity.csv", [100.0, 200.0], [1, 2, 3, 4, 5, 6])
        _write_coords(data_dir, "s_Spot.csv", [1, 2, 3])

        with pytest.raises(ValueError) as exc:
            sc.convert_scils_to_parquet(
                str(data_dir), str(tmp_path / "out.parquet"), organize=False,
            )
        text = str(exc.value)
        assert "一致しません" in text
        # 逃げ道を必ず案内する（利用者がここで詰まらないように）
        assert "除外して変換する" in text

    def test_master_layout_can_opt_into_dropping(self, tmp_path):
        data_dir = tmp_path / "scils"
        _write_intensity(data_dir, "s_Intensity.csv", [100.0, 200.0], [1, 2, 3, 4, 5, 6])
        _write_coords(data_dir, "s_Spot.csv", [1, 2, 3])

        result = sc.convert_scils_to_parquet(
            str(data_dir), str(tmp_path / "out.parquet"),
            organize=False, store_float32=False, drop_uncovered=True,
        )

        assert result.n_spots == 3
        assert sorted(pd.read_parquet(result.output_path)["id"]) == [1, 2, 3]
        assert any("除外しました" in w for w in result.warnings), result.warnings

    def test_overlapping_section_files_are_rejected(self, tmp_path):
        """切片同士が重複していたら、どちらのラベルにすべきか決められない"""
        data_dir = tmp_path / "scils"
        _write_intensity(data_dir, "A_Intensity.csv", [100.0], [1, 2, 3, 4, 5, 6])
        _write_coords(data_dir, "01.csv", [1, 2, 3])
        _write_coords(data_dir, "02.csv", [3, 4])

        with pytest.raises(ValueError, match="重複"):
            sc.convert_scils_to_parquet(
                str(data_dir), str(tmp_path / "out.parquet"), organize=False,
            )

    def test_possible_off_by_one_is_reported_not_silently_corrected(self, tmp_path):
        """部分カバー時は ±1 を自動補正しない。ただし疑いは必ず申告する。

        座標が 1 spot ずれた画像は一見それらしく見えるため、黙って出すと
        後から気付けない。
        """
        data_dir = tmp_path / "scils"
        _write_intensity(data_dir, "A_Intensity.csv", [100.0], [1, 2, 3, 4, 5, 6])
        _write_coords(data_dir, "01.csv", [0, 1, 2])

        result = sc.convert_scils_to_parquet(
            str(data_dir), str(tmp_path / "out.parquet"),
            organize=False, store_float32=False, drop_uncovered=True,
        )

        # 自動補正していない = 素直に一致した spot 1,2 だけが残る
        assert result.n_spots == 2
        assert sorted(pd.read_parquet(result.output_path)["id"]) == [1, 2]
        assert any("±1" in w for w in result.warnings), result.warnings

    def test_zero_based_coordinates_are_corrected(self, tmp_path):
        """実データの形: 座標が 0 始まり、Intensity が 1 始まり。

        座標 CSV の番号空間 (0..11) が Intensity (1..12) をちょうど 1 ずらした
        範囲に一致するので、一部の切片しか座標が無くてもシフトと確定できる。
        補正しないと全 spot が**隣の spot の座標**と結び付き、1 spot ずれた
        画像を黙って出力してしまう。
        """
        data_dir = tmp_path / "scils"
        _write_intensity(data_dir, "A_Intensity.csv", [100.0], list(range(1, 13)))
        _write_coords(data_dir, "01.csv", [0, 1, 2, 3])
        _write_coords(data_dir, "02.csv", [4, 5, 6])
        # 04 に相当する 7, 8 は意図的に出していない
        _write_coords(data_dir, "05.csv", [9, 10, 11])

        result = sc.convert_scils_to_parquet(
            str(data_dir), str(tmp_path / "out.parquet"),
            organize=False, store_float32=False,
        )

        assert any("グローバルシフト" in w for w in result.warnings), result.warnings

        # 補正後は Intensity の `Spot N` が SpotIndex N-1 の座標を使う。
        # 残るのは N-1 が座標に載っている N = 1..7, 10..12 の 10 spot
        # （7, 8 は「04」に相当し座標を出していない）。
        # 補正しなかった場合は 9 spot になるので、件数だけでも区別が付く。
        assert result.n_spots == 10
        df = pd.read_parquet(result.output_path)
        expected_xy = sorted(
            (float(si % 10), float(si // 10)) for si in [0, 1, 2, 3, 4, 5, 6, 9, 10, 11]
        )
        assert sorted(zip(df["x"], df["y"])) == expected_xy
        assert result.annotation_labels == ["01", "02", "05"]
        # 補正できているので「±1 の疑い」は出さない
        assert not any("可能性があります" in w for w in result.warnings), result.warnings

    def test_no_intersection_at_all_is_an_error(self, tmp_path):
        """別測定同士を突き合わせた場合は drop_uncovered でも止める"""
        data_dir = tmp_path / "scils"
        _write_intensity(data_dir, "A_Intensity.csv", [100.0], [101, 102, 103, 104, 105])
        _write_coords(data_dir, "01.csv", [1, 2, 3])

        with pytest.raises(ValueError, match="1 つもありません"):
            sc.convert_scils_to_parquet(
                str(data_dir), str(tmp_path / "out.parquet"),
                organize=False, drop_uncovered=True,
            )


class TestAutoDetectExposesAllSpotLikes:
    def test_spot_likes_is_size_desc_and_complete(self, tmp_path):
        data_dir = tmp_path / "scils"
        _write_intensity(data_dir, "A_Intensity.csv", [100.0], [1, 2, 3, 4, 5, 6])
        _write_coords(data_dir, "01.csv", [1, 2, 3, 4])
        _write_coords(data_dir, "02.csv", [5, 6])

        roles = sc.auto_detect_file_roles(data_dir)
        names = [p.name for p in roles["spot_likes"]]
        assert names == ["01.csv", "02.csv"]
        # 従来キーも維持（プレビュー等の既存呼び出し元のため）
        assert roles["spot"].name == "01.csv"
        assert [p.name for p in roles["annotations"]] == ["02.csv"]


class TestDetectPartialShift:
    def test_zero_based_coords_against_one_based_intensity(self):
        # 実データ相当: 座標 0..76437 (04 の 45938..65751 は欠番) / Intensity 1..76438
        set_spot = set(range(0, 45938)) | set(range(65752, 76438))
        set_int = set(range(1, 76439))
        assert sc._detect_partial_shift(set_int, set_spot) == -1

    def test_same_numbering_is_not_shifted(self):
        """番号体系が同じなら座標は Intensity の部分集合。補正してはならない。"""
        set_spot = {1, 2, 3, 7, 8}
        set_int = set(range(1, 11))
        assert sc._detect_partial_shift(set_int, set_spot) == 0

    def test_ambiguous_partial_coverage_returns_zero(self):
        """端が届いていなければ決め手が無い。推測しない。"""
        set_spot = {0, 1, 2}
        set_int = set(range(1, 7))
        assert sc._detect_partial_shift(set_int, set_spot) == 0

    def test_plus_one_direction(self):
        set_spot = {2, 3, 6, 11}
        set_int = set(range(1, 11))
        assert sc._detect_partial_shift(set_int, set_spot) == +1


class TestBuildMasterSpotTable:
    def test_returns_none_region_map_for_classic_layout(self, tmp_path):
        master = _write_coords(tmp_path, "s_Spot.csv", [1, 2, 3])
        ann = _write_coords(tmp_path, "Brain_Annotation.csv", [1, 2])

        si, x, y, region_map, warnings = sc.build_master_spot_table([master, ann])

        assert region_map is None
        assert sorted(si.tolist()) == [1, 2, 3]
        assert warnings == []

    def test_unions_and_labels_for_section_layout(self, tmp_path):
        a = _write_coords(tmp_path, "01.csv", [1, 2])
        b = _write_coords(tmp_path, "02.csv", [3])

        si, x, y, region_map, warnings = sc.build_master_spot_table([a, b])

        assert sorted(si.tolist()) == [1, 2, 3]
        assert region_map == {1: "01", 2: "01", 3: "02"}
        assert any("統合" in w for w in warnings), warnings


class TestOrganizeMovesEachFileOnce:
    """★ ver55.4: 切片統合レイアウトでは `annotation_files` が座標 CSV **全部**なので、
    `spots_path` (= 最大サイズの 1 本) と重複する。重複したまま移動すると 2 回目が
    `No such file or directory` で落ち、**出力は完成しているのに「変換エラー」**と
    表示され、一部だけ移動した中途半端な状態が残っていた。
    """

    def test_all_source_csvs_are_moved_exactly_once(self, tmp_path):
        data_dir = tmp_path / "scils"
        _write_intensity(data_dir, "A_Intensity.csv", [100.0, 200.0], [1, 2, 3, 4, 5, 6])
        _write_coords(data_dir, "01.csv", [1, 2, 3])   # 最大 = spots_path になる
        _write_coords(data_dir, "02.csv", [4, 5])
        _write_coords(data_dir, "03.csv", [6])

        result = sc.convert_scils_to_parquet(
            str(data_dir), str(tmp_path / "out.parquet"),
            organize=True, store_float32=False,
        )

        assert result.organized is True
        assert not any("移動に失敗" in w for w in result.warnings), result.warnings

        # 重複移動していない = 移動先に (1) 付きの複製が生まれていない
        moved_names = sorted(Path(p).name for p in result.moved_files)
        assert moved_names == ["01.csv", "02.csv", "03.csv", "A_Intensity.csv"]

        transform_dir = data_dir / "A_Transform"
        assert sorted(p.name for p in transform_dir.glob("*.csv")) == moved_names
        # 元フォルダに CSV が残っていない
        assert list(data_dir.glob("*.csv")) == []

    def test_move_failure_does_not_mask_a_successful_conversion(self, tmp_path, monkeypatch):
        """整理は後片付け。失敗しても変換結果を「エラー」にしてはいけない。"""
        data_dir = tmp_path / "scils"
        _write_intensity(data_dir, "A_Intensity.csv", [100.0], [1, 2, 3, 4, 5, 6])
        _write_coords(data_dir, "01.csv", [1, 2, 3])
        _write_coords(data_dir, "02.csv", [4, 5, 6])

        def _boom(src, dst_folder):
            raise OSError("disk is full")

        monkeypatch.setattr(sc, "_move_into_folder", _boom)

        result = sc.convert_scils_to_parquet(
            str(data_dir), str(tmp_path / "out.parquet"),
            organize=True, store_float32=False,
        )

        assert Path(result.output_path).is_file()
        assert result.n_spots == 6
        assert result.organized is False
        assert any("移動に失敗" in w for w in result.warnings), result.warnings
