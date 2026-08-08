"""MRM の空セル 1 つでデータセット全体の化合物注釈が消える (ver52.3 で発見)。

■ 何が起きるか

`_build_mz_to_compound_map` は m/z セルの変換を `try/except (ValueError, TypeError)`
で守っているが、**`float(nan)` は例外を出さない**。MRM ファイルの
`Parent m/z` / `Daughter m/z` に空セルが 1 つでもあると、
`mz_map[nan] = "化合物名"` が入る。

その先で:

    mrm_mz_values = np.array(sorted(mz_to_compound.keys()))   # NaN を含む
    idx = np.argmin(np.abs(mrm_mz_values - mz_val))           # 常に NaN の添字
    if abs(mrm_mz_values[idx] - mz_val) <= tolerance:         # 常に False

`np.argmin` は NaN があるとその添字を返すので、**どの feature も注釈されない**。
1 セルの欠損が、データセット全体の化合物名を消す。警告は出ない。

■ ★ 双子の関数は同じ罠を防いでいる

`_build_annotation_csv_map`（同ファイルの HMDB 節）には

    if mz <= 0 or pd.isna(mz):
        continue

というガードがある。**同じ役割の 2 実装のうち片方だけ守られている**（T3）。
ver51.9 B-2 の Volcano と同じ構図で、片方を直したときに隣を見なかった形。

■ 状態

ver52.3 ④ で直す。それまで `xfail(strict=True)` で記録しておく
（直った瞬間に xpass になって「マーカーを外せ」と落ちる）。
"""

import numpy as np
import pandas as pd
import pytest

from app.callbacks import interactive_calibration as IC


def _write_mrm(tmp_path, rows):
    p = tmp_path / "mrm.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return str(p)


@pytest.fixture
def mrm_with_one_blank_cell(tmp_path):
    """Daughter m/z が 1 行だけ空の MRM ファイル。実データでよくある形。

    ★ 列名は `_build_mz_to_compound_map` の正規化が受け付ける形にする。
      正規化は `lower().replace(" ", ".").replace("_", ".")` なので
      "Parent m/z" は "parent.m/z" になり **一致しない**（`/` は置換されない）。
      最初この形で書いたら地図が空になり、xfail が xpass して気付いた。
    """
    return _write_mrm(tmp_path, [
        {"Compound": "Alpha", "Parent_mz": 100.5, "Daughter_mz": 80.2},
        {"Compound": "Beta", "Parent_mz": 200.7, "Daughter_mz": None},
        {"Compound": "Gamma", "Parent_mz": 300.9, "Daughter_mz": 250.1},
    ])


def test_the_fixture_actually_builds_a_map(mrm_with_one_blank_cell):
    """★ 前提の固定: fixture が空の地図を作っていないこと。

    列名が一致しないと地図が空になり、下の検査が「NaN キーは無い」で
    **通ってしまう**（実際に一度そうなった）。番人が空振りしない形にする。
    """
    mz_map = IC._build_mz_to_compound_map(mrm_with_one_blank_cell)
    assert len(mz_map) >= 4, f"fixture の列名が認識されていない: {mz_map}"


class TestArgminIsPoisonedByNan:
    """前提の固定: NaN が 1 つ入ると最近傍探索が壊れることを実測で示す。"""

    def test_argmin_returns_the_nan_index(self):
        values = np.array([100.5, np.nan, 300.9])
        idx = int(np.argmin(np.abs(values - 300.9)))
        assert np.isnan(values[idx]), \
            "np.argmin が NaN 以外を返した（numpy の挙動が変わった？）"

    def test_tolerance_check_against_nan_is_always_false(self):
        assert not (abs(np.nan - 300.9) <= 0.1)


class TestBlankCellDoesNotPoisonTheMap:
    """ver52.3 ④ で修正済み。以後の再発を防ぐ。"""

    def test_map_has_no_nan_key(self, mrm_with_one_blank_cell):
        """★ 本丸: 空セルが NaN キーとして地図に入らないこと。"""
        mz_map = IC._build_mz_to_compound_map(mrm_with_one_blank_cell)
        nan_keys = [k for k in mz_map if isinstance(k, float) and np.isnan(k)]
        assert not nan_keys, (
            f"m/z→化合物名 の地図に NaN キーが入っている: {nan_keys}。"
            "以後 np.argmin が常に NaN を選ぶので、**全 feature の注釈が消える**")

    def test_other_compounds_are_still_annotated(self, mrm_with_one_blank_cell):
        """★ 利用者から見た症状: 無関係な化合物まで注釈されなくなる。"""
        mz_map = IC._build_mz_to_compound_map(mrm_with_one_blank_cell)
        labels = IC._annotate_gene_labels(["mz_300.900"], mz_map, tolerance=0.1)
        assert "Gamma" in labels[0], (
            f"空セルと無関係な m/z 300.9 の注釈まで消えている: {labels[0]}。"
            "1 セルの欠損がデータセット全体の化合物名を消す")

    def test_the_valid_rows_survive(self, mrm_with_one_blank_cell):
        """★ 過剰修正の番人: 空セルの行以外は落とさないこと。

        「NaN を弾く」を広く書きすぎて正当な m/z まで捨てると、
        症状が「全滅」から「一部欠落」に変わるだけで良くならない。
        """
        mz_map = IC._build_mz_to_compound_map(mrm_with_one_blank_cell)
        assert set(mz_map.values()) == {"Alpha", "Beta", "Gamma"}, (
            f"空セル以外の行まで落ちている: {mz_map}")
        # Beta は Daughter だけが空。Parent 側は残る。
        assert 200.7 in mz_map and mz_map[200.7] == "Beta"


class TestReportingDistinguishesBlankFromBroken:
    """★ 「読めなかった」の報告は、**空セルを数えない**こと。

    ver52.3 ④ の最初の実装は空セルも数えていた。だが MRM も TraceFinder/HMDB も
    付加イオンごとに列を持つので、空セルは「この化合物にこの付加イオンは無い」
    という**正常な状態**であり、実データでは数千件になる。

    「毎回数千件スキップしました」と出る報告は、利用者に無視されるようになる
    ——それは報告しないのと同じで、直したことにならない。
    そこで **非空なのに数値として読めないセルだけ**を数える。

    この 2 本は対で意味を持つ（片方だけだと「報告しない」実装でも通る）。
    """

    def test_blank_cell_is_not_reported(self, mrm_with_one_blank_cell, caplog):
        """空セル（＝正常）では警告を出さない。"""
        import logging
        with caplog.at_level(logging.WARNING):
            _, n = IC._build_mz_to_compound_map(
                mrm_with_one_blank_cell, return_skipped=True)
        assert n == 0, f"空セルを「読めなかった」に数えている: {n} 件"
        messages = [r.getMessage() for r in caplog.records]
        assert not [m for m in messages if "読めませんでした" in m], (
            "空セルは正常な状態なのに警告が出ている。"
            "実データでは数千件になり、報告そのものが無視されるようになる。"
            f"出たログ: {messages}")

    def test_non_blank_unreadable_cell_is_reported(self, tmp_path, caplog):
        """非空なのに数値でないセル（＝書式の壊れ）は必ず報告する。"""
        import logging
        path = _write_mrm(tmp_path, [
            {"Compound": "Alpha", "Parent_mz": 100.5, "Daughter_mz": 80.2},
            {"Compound": "Beta", "Parent_mz": "N.D.", "Daughter_mz": 150.3},
        ])
        with caplog.at_level(logging.WARNING):
            mz_map, n = IC._build_mz_to_compound_map(path, return_skipped=True)
        assert n == 1, (
            f"非空なのに数値化できないセルを数えていない (n={n}, map={mz_map})。"
            "この化合物は注釈から黙って消え、markers_annotated.csv にも出ない")
        messages = [r.getMessage() for r in caplog.records]
        assert any("読めませんでした" in m for m in messages), (
            f"件数は数えたのに報告していない。出たログ: {messages}")

    def test_the_twin_counts_the_same_way(self, tmp_path, caplog):
        """★ 双子 `_build_annotation_csv_map` も同じ数え方であること。

        片方だけ報告できると、利用者から見て
        「ファイル形式によって警告が出たり出なかったり」になる（T3 そのもの）。
        """
        import logging
        p = tmp_path / "hmdb.csv"
        pd.DataFrame([
            {"name": "Alpha", "[M+H]+": 100.5},
            {"name": "Beta", "[M+H]+": ""},        # 空 = 正常
            {"name": "Gamma", "[M+H]+": "N.D."},   # 非空だが読めない
        ]).to_csv(p, index=False)
        with caplog.at_level(logging.WARNING):
            m, n = IC._build_annotation_csv_map(str(p), return_skipped=True)
        assert n == 1, f"双子の数え方が MRM 側と揃っていない (n={n}, map={m})"
        assert any("読めませんでした" in r.getMessage() for r in caplog.records)


class TestTwinFunctionGuardsIt:
    """★ 双子側にガードが在ることを固定する（外されたら気付けるように）。

    片方だけ守られている状態が、そもそもこの欠陥の原因だった。
    MRM 側を直したあとは、両方守られていることの表明になる。
    """

    def test_csv_map_rejects_nan(self, tmp_path):
        import inspect
        src = inspect.getsource(IC._build_annotation_csv_map)
        assert "isna" in src, (
            "`_build_annotation_csv_map` の NaN ガードが消えている。"
            "MRM 側と同じ全滅が起きるようになる")
