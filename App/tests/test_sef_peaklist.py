"""Tests for app.services.sef_peaklist（SCiLS `.sef` = JSON 版 feature list の読み取り）。

`.sef` は peak-list CSV と同じ情報を持つが `name` の**文法が違う**。
CSV が位置区切り（`化合物名 | 分類 | DB | [M+H]+ | 0.85ppm | …`）なのに対し、
`.sef` は key=value 主体（`… | adduct=[M+H]+ | delta=3.85ppm | best_name_audit=… | …`）。
素通しすると例外を出さないまま adduct / ppm が全滅し、化合物名が内部符号になるため、
入口で正規化してから既存経路へ渡す（ver63.0）。ここではその正規化が
**既存パーサで読める形になっていること**と、**情報を捏造も欠落もしないこと**を検証する。
"""

import json

import pytest

from app.services.sef_peaklist import (
    is_sef_dialect,
    looks_like_sef,
    normalize_sef_name,
    read_sef_peaklist,
)
from app.services.peak_annotation import make_column_name, parse_scils_name
from app.utils.deg_utils import extract_mz_numeric


# 実ファイル（P5_aq_DHB_POS_downstream_recommended_273_window10ppm.sef）から採った 2 件。
# 1 件目 = FORMULA_ADDUCT_CANDIDATE、2 件目 = LIPID_SUM_CANDIDATE（脂質サム表記）。
_SEF_NAME_1 = (
    "FORMULA_ADDUCT_CANDIDATE C4H9N [M+H]+ | formula=C4H9N | adduct=[M+H]+ | "
    "delta=3.85ppm | best_name_audit=1-Ethylaziridine | candidates=2 | formulas=1 | "
    "formula_ambiguous=false | v2.8=NOT_APPLICABLE_NONLIPID_OR_UNRESOLVED | "
    "calibration=NONE | intensity=UNAVAILABLE | "
    "subset=DOWNSTREAM_RECOMMENDED_NONCOLLAPSED | original_peak_index=10 | "
    "PUTATIVE_MS1 | NOT_IDENTIFIED"
)
_SEF_NAME_2 = (
    "LIPID_SUM_CANDIDATE ST 28:0;O4;Hex | formula=C34H60O9 | adduct=[M+Na]+ | "
    "delta=0.65ppm | best_name_audit=ST 28:0;O4;Hex | candidates=37 | formulas=7 | "
    "formula_ambiguous=false | v2.8=TIER2_DHB_POS_MODERATE | calibration=NONE | "
    "intensity=UNAVAILABLE | subset=DOWNSTREAM_RECOMMENDED_NONCOLLAPSED | "
    "original_peak_index=609 | PUTATIVE_MS1 | NOT_IDENTIFIED"
)
# CSV 側と同じ書き方（test_molinfo_attach.py の Name と同形）。
_CSV_NAME = ("Phosphocholine | methyl_donor | CE_MS_Common_Soga2003 | [M]+ | 0.85ppm | "
             "annotation_tol=10ppm | mz_window=10ppm | formula=C5H15NO4P | SMILES=NA")


def _write_sef(folder, intervals, *, version="2", name="peaks.sef"):
    doc = {"version": version,
           "peaklist": {"metaInformation": {"numberOfIntervals": str(len(intervals))},
                        "intervals": intervals}}
    p = folder / name
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _iv(lower, upper, nm, color="#999999"):
    return {"lower": lower, "upper": upper, "name": nm, "color": color}


# ---------------------------------------------------------------------------
# 読み取り
# ---------------------------------------------------------------------------

def test_mz_is_center_of_lower_upper(tmp_path):
    """`.sef` は中心 m/z を持たないので (lower+upper)/2 を採る。"""
    p = _write_sef(tmp_path, [_iv(72.07977756262875, 72.08121917259611, _SEF_NAME_1)])
    mz, names = read_sef_peaklist(p)
    assert mz.size == 1
    assert mz[0] == pytest.approx((72.07977756262875 + 72.08121917259611) / 2)
    assert len(names) == 1


def test_reads_all_intervals_in_order(tmp_path):
    p = _write_sef(tmp_path, [_iv(72.0, 72.1, _SEF_NAME_1),
                              _iv(635.4, 635.5, _SEF_NAME_2)])
    mz, names = read_sef_peaklist(p)
    assert mz.size == 2 and len(names) == 2
    assert mz[0] < mz[1]


# ---------------------------------------------------------------------------
# 正規化（本題）
# ---------------------------------------------------------------------------

def test_normalized_name_parses_with_existing_parser(tmp_path):
    """正規化後は既存の parse_scils_name が全項目を読める。

    ★ 回帰の要: 正規化を外す（`.sef` の name を素通しする）と adduct / ppm が
      None になり、compound が内部符号 `FORMULA_ADDUCT_CANDIDATE …` になる。
    """
    rec = parse_scils_name(normalize_sef_name(_SEF_NAME_1))
    assert rec["compound"] == "1-Ethylaziridine"      # best_name_audit 側が本当の名前
    assert rec["adduct"] == "[M+H]+"                  # adduct= → 素フィールドへ昇格
    assert rec["ppm"] == pytest.approx(3.85)          # delta=3.85ppm → 素フィールドへ
    assert rec["formula"] == "C4H9N"


def test_raw_name_would_lose_adduct_and_ppm():
    """素通しでは adduct / ppm が取れないことを明示的に固定する。

    正規化が「念のため」ではなく**必須**であることの根拠。
    """
    rec = parse_scils_name(_SEF_NAME_1)
    assert rec["adduct"] is None
    assert rec["ppm"] is None
    assert rec["compound"].startswith("FORMULA_ADDUCT_CANDIDATE")


def test_lipid_sum_candidate_keeps_shorthand(tmp_path):
    """脂質サム表記（`;` を含む）がそのまま化合物名になる。"""
    rec = parse_scils_name(normalize_sef_name(_SEF_NAME_2))
    assert rec["compound"] == "ST 28:0;O4;Hex"
    assert rec["adduct"] == "[M+Na]+"
    assert rec["ppm"] == pytest.approx(0.65)


def test_lipid_class_and_database_are_not_fabricated():
    """`.sef` は分類名も DB 名も持たないので、空欄のままにする。

    ★ ver55.0 が「Spot ファイル名から領域ラベルを捏造していた」のを塞いだのと
      同じ方針。それらしい値を入れると、利用者が指定していない情報が
      画面と出力に出てしまう。
    """
    rec = parse_scils_name(normalize_sef_name(_SEF_NAME_1))
    assert rec["lipid_class"] is None
    assert rec["database"] is None


def test_sef_specific_keys_survive_in_raw():
    """`.sef` 固有の情報は落とさず raw に残す（エクスポート列名に入る）。"""
    norm = normalize_sef_name(_SEF_NAME_2)
    for token in ("candidates=37", "v2.8=TIER2_DHB_POS_MODERATE",
                  "formula_ambiguous=false", "subset=DOWNSTREAM_RECOMMENDED_NONCOLLAPSED",
                  "PUTATIVE_MS1", "NOT_IDENTIFIED"):
        assert token in norm
    # 昇格させたキーは二重に出さない
    assert "adduct=" not in norm
    assert "delta=" not in norm
    assert "best_name_audit=" not in norm


def test_passthrough_scils_dialect():
    """CSV と同じ書き方の name は無変換で通す（分類・DB も復元される）。

    SCiLS 側の書式が変わって CSV と同じ Name が `.sef` に入っても壊れないようにする。
    """
    assert is_sef_dialect(_CSV_NAME) is False
    assert normalize_sef_name(_CSV_NAME) == _CSV_NAME
    rec = parse_scils_name(normalize_sef_name(_CSV_NAME))
    assert rec["compound"] == "Phosphocholine"
    assert rec["lipid_class"] == "methyl_donor"
    assert rec["database"] == "CE_MS_Common_Soga2003"
    assert rec["adduct"] == "[M]+"
    assert rec["ppm"] == pytest.approx(0.85)


def test_missing_adduct_keeps_delta_as_key_value():
    """adduct が無いときは delta を key=value のまま残す（情報を落とさない）。

    ppm は「adduct より後ろの素フィールド」しか見られないので、adduct が無い状態で
    昇格させると拾われず、かつ元の key=value も消えて完全に失われる。
    """
    nm = ("FORMULA_ADDUCT_CANDIDATE C4H9N | formula=C4H9N | delta=3.85ppm | "
          "best_name_audit=1-Ethylaziridine")
    norm = normalize_sef_name(nm)
    assert "delta=3.85ppm" in norm
    assert parse_scils_name(norm)["compound"] == "1-Ethylaziridine"


def test_empty_best_name_falls_back_to_head_field():
    """best_name_audit が空なら元の先頭フィールドへ戻す（名前が消えるよりよい）。"""
    nm = "FORMULA_ADDUCT_CANDIDATE C4H9N [M+H]+ | adduct=[M+H]+ | best_name_audit="
    assert parse_scils_name(normalize_sef_name(nm))["compound"].startswith(
        "FORMULA_ADDUCT_CANDIDATE")


def test_pipe_inside_value_does_not_break_the_grammar():
    """値に `|` が入っていても区切りが壊れない（名前は前半だけになる）。

    `|` はパイプ文法そのものの区切りなので、値に含まれた時点で分割済みであり
    復元できない。CSV 側も同じ制約。ここで固定したいのは
    「名前が欠けても adduct / ppm は巻き添えにならない」こと。
    """
    nm = "X | adduct=[M+H]+ | delta=1.0ppm | best_name_audit=A|B"
    rec = parse_scils_name(normalize_sef_name(nm))
    assert rec["compound"] == "A"
    assert rec["adduct"] == "[M+H]+"
    assert rec["ppm"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 壊れた入力
# ---------------------------------------------------------------------------

def test_broken_intervals_are_counted_not_silently_dropped(tmp_path):
    """壊れた interval は黙って捨てず内訳を返す（CSV 側 ver52.3 と同じ扱い）。"""
    p = _write_sef(tmp_path, [
        _iv(72.0, 72.1, _SEF_NAME_1),          # 正常
        {"name": "no bounds"},                  # lower/upper 欠損
        _iv("abc", "def", "non numeric"),       # 数値化不能
        _iv(float("inf"), float("inf"), "inf"),  # 非有限
    ])
    mz, names, skipped = read_sef_peaklist(p, return_skipped=True)
    assert mz.size == 1 and len(names) == 1
    assert skipped["short_row"] == 1
    assert skipped["non_numeric_mz"] == 1
    assert skipped["non_finite_mz"] == 1


def test_skip_counts_render_with_existing_message(tmp_path):
    """`peaklist_skip_message` がそのまま使える（キーを CSV 側と揃えてある）。"""
    from app.services.scils_converter import peaklist_skip_message
    p = _write_sef(tmp_path, [_iv(72.0, 72.1, _SEF_NAME_1), {"name": "broken"}])
    _mz, _names, skipped = read_sef_peaklist(p, return_skipped=True)
    assert "1 件" in peaklist_skip_message(skipped)


def test_invalid_json_raises(tmp_path):
    p = tmp_path / "broken.sef"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        read_sef_peaklist(p)


def test_missing_peaklist_key_raises(tmp_path):
    p = tmp_path / "empty.sef"
    p.write_text(json.dumps({"version": "2"}), encoding="utf-8")
    with pytest.raises(ValueError, match="peaklist"):
        read_sef_peaklist(p)


def test_unknown_version_still_reads(tmp_path):
    """未知のバージョンでも止めない（警告のみ）。"""
    p = _write_sef(tmp_path, [_iv(72.0, 72.1, _SEF_NAME_1)], version="99")
    mz, _names = read_sef_peaklist(p)
    assert mz.size == 1


def test_looks_like_sef(tmp_path):
    good = _write_sef(tmp_path, [_iv(72.0, 72.1, _SEF_NAME_1)])
    bad = tmp_path / "other.sef"
    bad.write_text("just text", encoding="utf-8")
    assert looks_like_sef(good) is True
    assert looks_like_sef(bad) is False


# ---------------------------------------------------------------------------
# 下流との整合（エクスポート・m/z 一覧が壊れないこと）
# ---------------------------------------------------------------------------

def test_column_name_roundtrip_recovers_mz(tmp_path):
    """正規化名から作った列名から m/z を復元できる。

    エクスポート（列名の埋め込み）と m/z 一覧表は、列名から m/z を読み戻せることを
    前提にしている。化合物名に数字が入っていても崩れないことを固定する。
    """
    p = _write_sef(tmp_path, [_iv(72.07977756262875, 72.08121917259611, _SEF_NAME_1),
                              _iv(635.4061861554708, 635.4188944062764, _SEF_NAME_2)])
    mz, names = read_sef_peaklist(p)
    for v, nm in zip(mz, names):
        col = make_column_name(nm, float(v))
        assert extract_mz_numeric(col) == pytest.approx(round(float(v), 4))


def test_build_feature_annotation_table_from_sef(tmp_path):
    """変換本体と同じ経路（サイドカーの元テーブル）まで通ることを確認する。"""
    from app.services.peak_annotation import build_feature_annotation_table
    from app.services.annotation_inspect import _is_real_compound

    p = _write_sef(tmp_path, [_iv(72.07977756262875, 72.08121917259611, _SEF_NAME_1),
                              _iv(635.4061861554708, 635.4188944062764, _SEF_NAME_2)])
    pk_mz, pk_names = read_sef_peaklist(p)
    df = build_feature_annotation_table(pk_mz, pk_mz, pk_names, tol_da=0.01)
    assert list(df["compound"]) == ["1-Ethylaziridine", "ST 28:0;O4;Hex"]
    assert list(df["adduct"]) == ["[M+H]+", "[M+Na]+"]
    assert int(df["compound"].map(_is_real_compound).sum()) == 2
    # 捏造していないこと
    assert df["lipid_class"].isna().all()
    assert df["database"].isna().all()
