"""Tests for app.services.data_manager の優先度フィルタ関数

- list_tims_files / build_tims_input_paths: Parquet 優先ルール
- _read_tims_raw: 同フィルタを通してキャリブレーション用の 1 ファイルを選ぶ
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.services import data_manager as dm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_dummy_parquet(path: Path, mz_values: list[float] | None = None) -> Path:
    """mz_XXX 列を持つダミー Parquet を生成する"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if mz_values is None:
        mz_values = [100.5, 200.1]
    cols = {"id": pa.array([1, 2], type=pa.int64()),
            "x": pa.array([0.0, 1.0], type=pa.float64()),
            "y": pa.array([0.0, 1.0], type=pa.float64())}
    for mz in mz_values:
        cols[f"mz_{mz:.4f}"] = pa.array([1.0, 2.0], type=pa.float64())
    table = pa.table(cols)
    pq.write_table(table, path)
    return path


def _write_dummy_tims_csv(path: Path, mz_values: list[float] | None = None) -> Path:
    """mz_XXX 列を持つダミー TIMS CSV"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if mz_values is None:
        mz_values = [100.5, 200.1]
    data = {"id": [1, 2], "x": [0.0, 1.0], "y": [0.0, 1.0]}
    for mz in mz_values:
        data[f"mz_{mz:.4f}"] = [1.0, 2.0]
    pd.DataFrame(data).to_csv(path, index=False)
    return path


def _write_annotation_csv(path: Path) -> Path:
    """SpotIndex/X/Y のみの中間 CSV (m/z 列なし)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "Spot index": [1, 2, 3],
        "X": [10, 20, 30],
        "Y": [40, 50, 60],
    }).to_csv(path, index=False)
    return path


# ---------------------------------------------------------------------------
# list_tims_files
# ---------------------------------------------------------------------------

class TestListTimsFiles:
    def test_parquet_only_when_mixed(self, tmp_path):
        """Parquet と CSV 混在時は Parquet のみ返る"""
        _write_dummy_parquet(tmp_path / "sample.parquet")
        _write_annotation_csv(tmp_path / "Brain_Annotation.csv")
        _write_annotation_csv(tmp_path / "sample_Spot.csv")
        _write_dummy_tims_csv(tmp_path / "sample_Intensity.csv")
        result = dm.list_tims_files(str(tmp_path))
        assert result == ["sample"]

    def test_multiple_parquet(self, tmp_path):
        """Parquet 複数本あれば全部返る"""
        _write_dummy_parquet(tmp_path / "sample_a.parquet")
        _write_dummy_parquet(tmp_path / "sample_b.parquet")
        _write_dummy_parquet(tmp_path / "sample_c.pq")
        result = dm.list_tims_files(str(tmp_path))
        assert result == ["sample_a", "sample_b", "sample_c"]

    def test_csv_only_multiple(self, tmp_path):
        """Parquet 無し・CSV 複数なら全部返る"""
        _write_dummy_tims_csv(tmp_path / "a.csv")
        _write_dummy_tims_csv(tmp_path / "b.csv")
        result = dm.list_tims_files(str(tmp_path))
        assert result == ["a", "b"]

    def test_csv_tsv_txt_mixed(self, tmp_path):
        """Parquet 無しで CSV + TSV + TXT 混在なら全部返る"""
        _write_dummy_tims_csv(tmp_path / "a.csv")
        (tmp_path / "b.tsv").write_text("id\tx\ty\tmz_100.0\n1\t0\t0\t1.0\n")
        (tmp_path / "c.txt").write_text("id\tx\ty\tmz_200.0\n1\t0\t0\t2.0\n")
        result = dm.list_tims_files(str(tmp_path))
        assert result == ["a", "b", "c"]

    def test_empty_folder(self, tmp_path):
        assert dm.list_tims_files(str(tmp_path)) == []

    def test_invalid_folder(self, tmp_path):
        assert dm.list_tims_files(str(tmp_path / "does_not_exist")) == []

    def test_ignores_subdirectories(self, tmp_path):
        """Parquet 優先判定にサブディレクトリは混入しない"""
        (tmp_path / "sub").mkdir()
        _write_dummy_tims_csv(tmp_path / "a.csv")
        result = dm.list_tims_files(str(tmp_path))
        assert result == ["a"]


# ---------------------------------------------------------------------------
# build_tims_input_paths
# ---------------------------------------------------------------------------

class TestBuildTimsInputPaths:
    def test_parquet_only_when_mixed(self, tmp_path):
        pq_path = _write_dummy_parquet(tmp_path / "sample.parquet")
        _write_annotation_csv(tmp_path / "Brain_Annotation.csv")
        _write_dummy_tims_csv(tmp_path / "sample.csv")
        result = dm.build_tims_input_paths(str(tmp_path))
        assert result == [str(pq_path)]

    def test_csv_only_full_paths(self, tmp_path):
        a = _write_dummy_tims_csv(tmp_path / "a.csv")
        b = _write_dummy_tims_csv(tmp_path / "b.csv")
        result = dm.build_tims_input_paths(str(tmp_path))
        assert result == [str(a), str(b)]


# ---------------------------------------------------------------------------
# list_tims_files_multi / build_tims_input_paths_multi (既存関数の連動確認)
# ---------------------------------------------------------------------------

class TestMultiFolder:
    def test_list_multi_dedupes_by_stem(self, tmp_path):
        f1 = tmp_path / "folder_a"
        f2 = tmp_path / "folder_b"
        _write_dummy_parquet(f1 / "sample.parquet")
        _write_dummy_parquet(f2 / "sample.parquet")  # 同じ stem
        _write_dummy_parquet(f2 / "other.parquet")
        result = dm.list_tims_files_multi([str(f1), str(f2)])
        # stem ベース dedupe で "sample" は 1 つに
        assert result == ["sample", "other"]

    def test_list_multi_applies_parquet_priority_per_folder(self, tmp_path):
        f1 = tmp_path / "folder_a"
        f2 = tmp_path / "folder_b"
        _write_dummy_parquet(f1 / "pq_only.parquet")
        _write_annotation_csv(f1 / "middle.csv")
        _write_dummy_tims_csv(f2 / "csv_only.csv")
        result = dm.list_tims_files_multi([str(f1), str(f2)])
        # folder_a: Parquet 優先で pq_only のみ
        # folder_b: Parquet 無しで csv_only
        assert result == ["pq_only", "csv_only"]


# ---------------------------------------------------------------------------
# _read_tims_raw (キャリブレーション用のフォールバック読込)
# ---------------------------------------------------------------------------

class TestReadTimsRaw:
    def test_picks_parquet_over_annotation_csv(self, tmp_path):
        """Parquet + Annotation CSV 混在時に Parquet が選ばれる"""
        _write_dummy_parquet(tmp_path / "sample.parquet", mz_values=[100.5, 200.1])
        # アルファベット順で先に来る Annotation CSV を置く
        _write_annotation_csv(tmp_path / "Brain_Annotation.csv")
        result = dm.read_raw_mz_spectrum(str(tmp_path), is_tims=True)
        # mz_100.5 / mz_200.1 の列が取れていれば Parquet を読んだ証拠
        assert result is not None
        assert any("100" in c for c in result.columns)
        assert any("200" in c for c in result.columns)

    def test_csv_fallback_when_no_parquet(self, tmp_path):
        """Parquet 無しフォルダで CSV から読む"""
        _write_dummy_tims_csv(tmp_path / "sample.csv", mz_values=[150.0])
        result = dm.read_raw_mz_spectrum(str(tmp_path), is_tims=True)
        assert result is not None
        assert any("150" in c for c in result.columns)

    def test_returns_none_for_empty_folder(self, tmp_path):
        assert dm.read_raw_mz_spectrum(str(tmp_path), is_tims=True) is None

    def test_sample_name_override_still_limited_to_candidates(self, tmp_path):
        """sample_name 指定時も候補は _filter_tims_candidates に絞られる。

        Parquet が存在する場合、sample_name で CSV stem を指定しても CSV は候補外。
        → candidates 内に一致が無い場合 candidates[0] にフォールバック。
        """
        _write_dummy_parquet(tmp_path / "sample.parquet", mz_values=[100.5])
        _write_annotation_csv(tmp_path / "Brain_Annotation.csv")
        # 中間 CSV の stem を指定しても Parquet が選ばれる (フィルタ済み候補から matched[0])
        result = dm.read_raw_mz_spectrum(
            str(tmp_path), is_tims=True, sample_name="Brain_Annotation",
        )
        assert result is not None
        assert any("100" in c for c in result.columns)


# ---------------------------------------------------------------------------
# 内部ヘルパー _filter_tims_candidates の直接テスト
# ---------------------------------------------------------------------------

class TestFilterHelper:
    def test_sorts_results(self, tmp_path):
        _write_dummy_parquet(tmp_path / "z.parquet")
        _write_dummy_parquet(tmp_path / "a.parquet")
        _write_dummy_parquet(tmp_path / "m.parquet")
        result = dm._filter_tims_candidates(tmp_path)
        assert [p.name for p in result] == ["a.parquet", "m.parquet", "z.parquet"]

    def test_case_insensitive_extensions(self, tmp_path):
        pq_path = _write_dummy_parquet(tmp_path / "sample.parquet")
        # 拡張子が大文字でも認識
        big = tmp_path / "BIG.PARQUET"
        pq_path.rename(big)
        result = dm._filter_tims_candidates(tmp_path)
        assert len(result) == 1
        assert result[0].name == "BIG.PARQUET"
