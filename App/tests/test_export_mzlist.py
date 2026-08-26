"""「m/z 一覧」表の回帰テスト。

データ出力の本体は「1 行 = 1 スポット」で m/z が列名・強度が値のため、
「どの m/z が入っているか知りたいだけ」に答えられなかった。1 行 = 1 m/z の
別表を出せるようにしたのが本機能。

最重要は **サイドカー（化合物名の注釈）が無くても m/z 一覧が出ること**。
ここが空表になると、注釈を登録していないデータでは機能そのものが使えない。

次に重要なのが突合の許容差。強度行列の列名リネーム
(`_apply_feature_annotation_columns`) と同じ 0.005 Da でないと、
同じ m/z に別の化合物名が付いた 2 つの出力ができる。
"""

import numpy as np
import pandas as pd
import pytest

from app.services.export_mzlist import (
    MATCH_TOL_DA,
    OUTPUT_COLUMNS,
    build_mz_list,
    mz_columns,
)

_PARQUET_COLS = ["id", "x", "y", "100.0001", "200.0002", "300.0003", "annotation"]


@pytest.fixture
def sidecar(tmp_path):
    """2 件だけ注釈のあるサイドカー（300.0003 は未注釈）。"""
    p = tmp_path / "X_feature_annotations.parquet"
    pd.DataFrame({
        "mz": [100.0001, 200.0002],
        "compound": ["Glutathione", "PC(34:1)"],
        "adduct": ["[M-H]-", "[M+H]+"],
        "formula": ["C10H17N3O6S", "C42H82NO8P"],
        "ppm": [1.2, -0.8],
        "lipid_class": ["", "PC"],
        "database": ["HMDB", "LIPIDMAPS"],
        "raw": ["Glutathione | [M-H]-", "PC(34:1) | [M+H]+"],
    }).to_parquet(p, index=False)
    return p


# ---------------------------------------------------------------------------
# m/z 列の判定
# ---------------------------------------------------------------------------
def test_meta_columns_are_not_treated_as_mz():
    """id/x/y/annotation を m/z と取り違えない。"""
    assert mz_columns(_PARQUET_COLS) == ["100.0001", "200.0002", "300.0003"]


def test_row_count_matches_mz_column_count():
    """行数 = parquet の m/z 列数。"""
    out = build_mz_list(_PARQUET_COLS)
    assert len(out) == 3


def test_columns_from_which_no_mz_can_be_read_are_dropped():
    """列名から m/z を読めない列は落とす。

    残すと mz が inf/NaN の行ができ、下流で黙って壊れる。
    """
    out = build_mz_list(_PARQUET_COLS + ["謎の列", "SomeLabel"])
    assert len(out) == 3
    assert np.isfinite(out["mz"]).all()


def test_output_is_sorted_by_mz():
    out = build_mz_list(["300.0003", "100.0001", "200.0002"])
    assert list(out["mz"]) == sorted(out["mz"])


def test_column_set_is_stable():
    """列の顔ぶれが入力によって変わらない（注釈の有無で列が増減しない）。"""
    assert list(build_mz_list(_PARQUET_COLS).columns) == OUTPUT_COLUMNS


# ---------------------------------------------------------------------------
# 注釈が無くても出る（最重要）
# ---------------------------------------------------------------------------
def test_works_without_sidecar():
    """サイドカー無しでも m/z 一覧が出る。注釈列は空欄。

    ここが空表になると、化合物名を登録していないデータで機能ごと使えない。
    """
    out = build_mz_list(_PARQUET_COLS, sidecar_path=None)
    assert len(out) == 3
    assert list(out["mz"]) == [100.0001, 200.0002, 300.0003]
    assert (out["compound"] == "").all(), "注釈が無いのに何か入っている"


def test_missing_sidecar_file_is_not_an_error(tmp_path):
    """指定されたサイドカーが存在しなくても落ちない。"""
    out = build_mz_list(_PARQUET_COLS, sidecar_path=tmp_path / "無い.parquet")
    assert len(out) == 3


def test_broken_sidecar_falls_back_to_no_annotation(tmp_path):
    """壊れたサイドカーでも一覧は出す（注釈だけ諦める）。"""
    bad = tmp_path / "broken_feature_annotations.parquet"
    bad.write_bytes(b"this is not parquet")
    out = build_mz_list(_PARQUET_COLS, sidecar_path=bad)
    assert len(out) == 3
    assert (out["compound"] == "").all()


def test_sidecar_without_mz_column_is_ignored(tmp_path):
    """mz 列を持たないサイドカーは無視して一覧だけ出す。"""
    p = tmp_path / "x_feature_annotations.parquet"
    pd.DataFrame({"compound": ["A"]}).to_parquet(p, index=False)
    out = build_mz_list(_PARQUET_COLS, sidecar_path=p)
    assert len(out) == 3
    assert (out["compound"] == "").all()


# ---------------------------------------------------------------------------
# 注釈の突合
# ---------------------------------------------------------------------------
def test_annotations_are_attached(sidecar):
    """化合物名・アダクト・組成式が m/z に付く。"""
    out = build_mz_list(_PARQUET_COLS, sidecar_path=sidecar).set_index("mz")
    assert out.loc[100.0001, "compound"] == "Glutathione"
    assert out.loc[100.0001, "adduct"] == "[M-H]-"
    assert out.loc[100.0001, "formula"] == "C10H17N3O6S"
    assert out.loc[200.0002, "compound"] == "PC(34:1)"
    assert out.loc[200.0002, "lipid_class"] == "PC"


def test_unannotated_mz_stays_blank(sidecar):
    """サイドカーに無い m/z は空欄のまま。無理に埋めない。"""
    out = build_mz_list(_PARQUET_COLS, sidecar_path=sidecar).set_index("mz")
    assert out.loc[300.0003, "compound"] == ""


def test_match_tolerance_boundary(tmp_path):
    """許容差 0.005 Da の内側は付き、外側は付かない。

    強度行列の列名リネームと同じ値でないと、同じ m/z に別の化合物名が付いた
    2 つの出力ができる。
    """
    p = tmp_path / "tol_feature_annotations.parquet"
    pd.DataFrame({"mz": [500.0000], "compound": ["Target"], "adduct": [""],
                  "formula": [""], "ppm": [0.0], "lipid_class": [""],
                  "database": [""]}).to_parquet(p, index=False)

    inside = build_mz_list([f"{500.0 + MATCH_TOL_DA * 0.5:.6f}"], sidecar_path=p)
    assert inside["compound"].iloc[0] == "Target", "許容内なのに付いていない"

    outside = build_mz_list([f"{500.0 + MATCH_TOL_DA * 2:.6f}"], sidecar_path=p)
    assert outside["compound"].iloc[0] == "", "許容外なのに付いてしまった"


def test_nearest_match_wins(tmp_path):
    """複数候補があるとき最も近い 1 件を採る。"""
    p = tmp_path / "near_feature_annotations.parquet"
    pd.DataFrame({
        "mz": [500.0000, 500.0040],
        "compound": ["Far", "Near"],
        "adduct": ["", ""], "formula": ["", ""], "ppm": [0.0, 0.0],
        "lipid_class": ["", ""], "database": ["", ""],
    }).to_parquet(p, index=False)
    out = build_mz_list(["500.003000"], sidecar_path=p)
    assert out["compound"].iloc[0] == "Near"


def test_sidecar_with_nan_mz_is_skipped(tmp_path):
    """サイドカーに NaN の m/z が混ざっても落ちない。"""
    p = tmp_path / "nan_feature_annotations.parquet"
    pd.DataFrame({
        "mz": [np.nan, 100.0001],
        "compound": ["Bad", "Good"],
        "adduct": ["", ""], "formula": ["", ""], "ppm": [0.0, 0.0],
        "lipid_class": ["", ""], "database": ["", ""],
    }).to_parquet(p, index=False)
    out = build_mz_list(["100.000100"], sidecar_path=p)
    assert out["compound"].iloc[0] == "Good"


# ---------------------------------------------------------------------------
# 強度行列との突き合わせ
# ---------------------------------------------------------------------------
def test_column_name_matches_the_intensity_header():
    """`列名` が実際の出力の列見出しと文字列一致する。

    ここが一致しないと、一覧表と強度行列を突き合わせられない。
    """
    embedded = "Glutathione_100.0001 | [M-H]-"
    out = build_mz_list(["id", "x", "y", embedded, "annotation"])
    assert out["列名"].iloc[0] == embedded
    assert out["mz"].iloc[0] == pytest.approx(100.0001)


def test_empty_input_returns_empty_frame_with_columns():
    """m/z 列が 1 つも無くても、列だけは揃った空の表を返す。"""
    out = build_mz_list(["id", "x", "y", "annotation"])
    assert out.empty
    assert list(out.columns) == OUTPUT_COLUMNS
