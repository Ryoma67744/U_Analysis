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

    def test_unmatched_sample_name_returns_none(self, tmp_path):
        """★ ver51.8: 指定サンプルが候補に無ければ **None**（先頭へ落とさない）。

        旧テスト名は test_sample_name_override_still_limited_to_candidates で、
        「candidates 内に一致が無い場合 candidates[0] にフォールバック」を
        **正しい仕様として固定していた**。だが利用者がキャリブレーション対象
        サンプルを明示的に選んでいるのに黙って別サンプルのスペクトルを返すのは
        科学的に誤りで、そのまま ppm ドリフトの推定に使われる。

        候補の絞り込み（_filter_tims_candidates が中間 CSV を除外すること）自体は
        従来どおりで、その結果一致しなければ「見つからない」と答える。
        """
        _write_dummy_parquet(tmp_path / "sample.parquet", mz_values=[100.5])
        _write_annotation_csv(tmp_path / "Brain_Annotation.csv")
        result = dm.read_raw_mz_spectrum(
            str(tmp_path), is_tims=True, sample_name="Brain_Annotation",
        )
        assert result is None, "候補外の指定で別ファイルを返している"

    def test_matched_sample_name_is_used(self, tmp_path):
        """正常系: 候補内の stem を指定したらそのファイルが読まれること。"""
        _write_dummy_parquet(tmp_path / "A.parquet", mz_values=[100.5])
        _write_dummy_parquet(tmp_path / "B.parquet", mz_values=[900.5])

        res_b = dm.read_raw_mz_spectrum(str(tmp_path), is_tims=True, sample_name="B")
        assert res_b is not None and any("900" in c for c in res_b.columns), \
            f"B を指定したのに別ファイル: {list(res_b.columns) if res_b is not None else None}"

        res_a = dm.read_raw_mz_spectrum(str(tmp_path), is_tims=True, sample_name="A")
        assert res_a is not None and any("100" in c for c in res_a.columns)

    def test_no_sample_name_still_takes_the_first(self, tmp_path):
        """未指定のときは従来どおり先頭ファイル（過剰な締め付けの番人）。"""
        _write_dummy_parquet(tmp_path / "A.parquet", mz_values=[100.5])
        _write_dummy_parquet(tmp_path / "B.parquet", mz_values=[900.5])
        res = dm.read_raw_mz_spectrum(str(tmp_path), is_tims=True)
        assert res is not None and any("100" in c for c in res.columns)


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


# ---------------------------------------------------------------------------
# DESI 生データのサンプル選択 (ver51.8)
# ---------------------------------------------------------------------------
# ★ 従来 `_read_desi_raw` は sample_name を **引数に持っていなかった**。
#   `read_raw_mz_spectrum` は TIMS にだけ渡しており、DESI では利用者がどの
#   サンプルを選んでも常に先頭の .txt を読んでいた（警告も無し）。
#   キャリブレーションはその平均スペクトルから ppm ドリフトを決めるため、
#   **サンプル A の補正曲線がサンプル B の測定値から作られる**。
#   DESI 経路にはテストが 1 件も無かった。

def _write_desi_txt(path, mz_values):
    """_read_desi_raw が期待する 5 行ヘッダ + 1 画素の .txt を書く。"""
    n = len(mz_values)
    lines = [
        "",                                        # 行1: 空行
        "header info",                             # 行2
        "\t".join(["idx"] * (n + 3)),              # 行3: 列インデックス
        "\t".join(["", "", ""] + [f"{m}" for m in mz_values]),   # 行4: m/z
        "\t".join(["", "", ""] + ["0"] * n),       # 行5: フラグメント
        "\t".join(["1", "0", "0"] + ["10"] * n),   # 行6〜: 画素データ
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestReadDesiRawSampleSelection:
    def test_named_sample_is_read(self, tmp_path):
        """★ 指定したサンプルの .txt が読まれること（従来は常に先頭）。"""
        _write_desi_txt(tmp_path / "A.txt", [100.5])
        _write_desi_txt(tmp_path / "B.txt", [900.5])

        res = dm.read_raw_mz_spectrum(str(tmp_path), is_tims=False, sample_name="B")
        assert res is not None, "B が読めない"
        assert any("900" in c for c in res.columns), \
            f"B を指定したのに先頭(A)を読んでいる: {list(res.columns)}"

    def test_unmatched_sample_returns_none(self, tmp_path):
        """★ 指定が満たせなければ None（黙って別サンプルを返さない）。"""
        _write_desi_txt(tmp_path / "A.txt", [100.5])
        _write_desi_txt(tmp_path / "B.txt", [900.5])
        assert dm.read_raw_mz_spectrum(
            str(tmp_path), is_tims=False, sample_name="MISSING") is None

    def test_no_sample_name_takes_the_first(self, tmp_path):
        """未指定なら従来どおり先頭ファイル。"""
        _write_desi_txt(tmp_path / "A.txt", [100.5])
        _write_desi_txt(tmp_path / "B.txt", [900.5])
        res = dm.read_raw_mz_spectrum(str(tmp_path), is_tims=False)
        assert res is not None and any("100" in c for c in res.columns)
