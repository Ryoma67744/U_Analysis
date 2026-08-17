"""キャリブレーションのアノテーションファイルが形式を問わず読まれること (C06-2)。

--------------------------------------------------------------------------
症状: 指定したアノテーションファイルが黙って無視される
--------------------------------------------------------------------------
インタラクティブ解析の m/z キャリブレーション節にある「アノテーションファイル」
欄は、ファイル形式を選ばせない 1 本の入力欄である。ところが読み取り側は
**MRM/TraceFinder の列構成 (Compound / Parent m/z) でのみ**解釈していた。

同じ画面の「再アノテーション」機能は拡張子で読み方を切り替えており、
TraceFinder 形式・HMDB 形式の CSV を正しく読む。つまり、

  - 同じファイルを「再アノテーション」に入れると化合物名が付く
  - 同じファイルを「キャリブレーション」に入れると何も起きない

という食い違いが起きていた。しかも読めなかったことは画面にも出ないため、
利用者は「キャリブレーションは効いたが化合物名は付かなかった」としか見えず、
ファイルが読まれていないことに気づけない。

キャリブレーション後の m/z で化合物名を貼り直すのがこの機能の目的なので、
読めなければ機能そのものが空振りする。
"""

import pandas as pd
import pytest

import app.callbacks.interactive_calibration as cal


DEG = [
    {"gene": "mz_180", "annotation": "mz_180"},
    {"gene": "mz_192", "annotation": "mz_192"},
]
CORRECTED = {"mz_180": 180.0634, "mz_192": 192.0270}


_MRM_TABLE = {
    "Compound": ["Glucose", "Citrate"],
    "Parent_mz": [180.0634, 192.0270],
}


@pytest.fixture
def mrm_xlsx(tmp_path):
    path = tmp_path / "mrm.xlsx"
    pd.DataFrame(_MRM_TABLE).to_excel(path, index=False)
    return str(path)


@pytest.fixture
def mrm_csv(tmp_path):
    path = tmp_path / "mrm.csv"
    pd.DataFrame(_MRM_TABLE).to_csv(path, index=False)
    return str(path)


@pytest.fixture
def hmdb_csv(tmp_path):
    """HMDB 形式（付加イオンごとの m/z 列を持つ）。"""
    path = tmp_path / "hmdb.csv"
    path.write_text(
        "name,[M+H]+,[M-H]-\n"
        "Glucose,180.0634,178.0489\n"
        "Citrate,192.0270,190.0125\n",
        encoding="utf-8")
    return str(path)


def _annotations(deg):
    return {r["gene"]: r.get("annotation") for r in deg}


# ---------------------------------------------------------------------------
# 従来から読めていた形式（壊していないことの確認）
# ---------------------------------------------------------------------------

def test_mrm_excel_is_still_read(mrm_xlsx):
    out = cal._reannotate_with_calibration(DEG, CORRECTED, mrm_xlsx, tolerance=0.01)
    assert _annotations(out) == {"mz_180": "Glucose", "mz_192": "Citrate"}


def test_mrm_csv_is_still_read(mrm_csv):
    out = cal._reannotate_with_calibration(DEG, CORRECTED, mrm_csv, tolerance=0.01)
    assert _annotations(out) == {"mz_180": "Glucose", "mz_192": "Citrate"}


# ---------------------------------------------------------------------------
# ★ 本題: MRM 形式でない CSV を指定したとき
# ---------------------------------------------------------------------------

def test_hmdb_csv_in_the_annotation_field_is_read(hmdb_csv):
    """★ 「再アノテーション」で読める CSV は、こちらでも読めること。"""
    out = cal._reannotate_with_calibration(
        DEG, CORRECTED, hmdb_csv, tolerance=0.01,
        ion_mode="Positive", adduct_patterns=["+H"])
    got = _annotations(out)
    assert got["mz_180"] != "mz_180", (
        "アノテーションファイルが黙って無視されている"
        "（同じファイルを再アノテーションに入れれば読める）")


def test_the_two_readers_agree_on_the_same_file(hmdb_csv):
    """再アノテーション側と同じ結果になること（食い違いを固定で潰す）。"""
    reann_map = cal._build_annotation_csv_map(
        hmdb_csv, ion_mode="Positive", adduct_patterns=["+H"], tolerance=0.01)
    cal_map = cal._build_annotation_map_any(
        hmdb_csv, ion_mode="Positive", adduct_patterns=["+H"], tolerance=0.01)
    assert reann_map and cal_map == reann_map


def test_negative_mode_is_honoured(hmdb_csv):
    """イオンモード・付加イオンの指定が効くこと（形式判定で握り潰さない）。"""
    pos = cal._build_annotation_map_any(
        hmdb_csv, ion_mode="Positive", adduct_patterns=["+H"], tolerance=0.01)
    neg = cal._build_annotation_map_any(
        hmdb_csv, ion_mode="Negative", adduct_patterns=["-H"], tolerance=0.01)
    assert pos and neg
    assert set(pos) != set(neg), "極性を変えても m/z が変わっていない"


def test_mrm_layout_wins_when_both_could_parse(mrm_csv):
    """MRM 列構成で読めるならそちらを使うこと（従来の解釈を変えない）。"""
    assert cal._build_annotation_map_any(mrm_csv, tolerance=0.01) == \
        cal._build_mz_to_compound_map(mrm_csv, tolerance=0.01)


def test_missing_or_unreadable_file_is_harmless(tmp_path):
    assert cal._build_annotation_map_any(None) == {}
    assert cal._build_annotation_map_any("") == {}
    assert cal._build_annotation_map_any(str(tmp_path / "nope.csv")) == {}
    junk = tmp_path / "junk.csv"
    junk.write_text("これは表ではありません\n", encoding="utf-8")
    assert cal._build_annotation_map_any(str(junk)) == {}
    # 読めなくても DEG データは壊さずそのまま返す
    out = cal._reannotate_with_calibration(DEG, CORRECTED, str(junk), tolerance=0.01)
    assert _annotations(out) == {"mz_180": "mz_180", "mz_192": "mz_192"}


def test_annotation_csv_argument_still_takes_priority(mrm_csv, hmdb_csv):
    """別枠の annotation_csv_path は従来どおり優先されること。"""
    out = cal._reannotate_with_calibration(
        DEG, CORRECTED, mrm_csv, tolerance=0.01,
        annotation_csv_path=hmdb_csv,
        ion_mode="Positive", adduct_patterns=["+H"])
    # どちらの経路でも化合物名は付く（優先順位は既存仕様のまま）
    assert all(v not in ("mz_180", "mz_192") for v in _annotations(out).values())
