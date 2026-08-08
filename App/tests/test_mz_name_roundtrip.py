"""feature 名 ⇄ m/z の往復一致 (ver51.8)。

■ なぜこのテストが要るか

`extract_mz_numeric` は「文字列中の最初の数字」を m/z としていた。annotated な
feature 名は

    <化合物名>_<m/z> | <DB> | <アダクト>

形式で、これは `peak_annotation.make_column_name()` が作り、`scils_converter` が
**parquet の列名として** 書き、R がそれを Seurat の rowname に採用したものが
`features_list.txt` / `expression_matrix.parquet` の列名として Python に戻ってくる。

したがって化合物名に数字があると m/z を取り違える:

    "PI 38:4 (PI 18:0/20:4)_760.5851"           -> 38.0
    "2-Hydroxybutyric acid_105.0546 | HMDB | …" -> 2.0

同梱 DB (App/DB/TIMS/4500_endogenous_metabolites_mod.csv) は **4,546 化合物中
2,409 件 (53%) が名前に数字を含む**ので、実データでは常時誤る。

★ 正規表現を目で見て確かめるのではなく、**アプリ自身が名前を作る関数を通して
  往復させる**のが本当の受入基準。`make_column_name(raw, mz)` が作った名前から
  `extract_mz_numeric` が元の mz を復元できなければ、どこかで契約が切れている。
"""

import csv
import io
import re
from pathlib import Path

import pytest

from app.services.peak_annotation import make_column_name
from app.utils.deg_utils import extract_mz_numeric

_DB = (Path(__file__).resolve().parent.parent
       / "DB" / "TIMS" / "4500_endogenous_metabolites_mod.csv")


def _compound_names(limit=400):
    """同梱 DB から「数字を含む化合物名」を取り出す。"""
    if not _DB.exists():
        pytest.skip(f"同梱 DB が無い: {_DB}")
    raw = _DB.read_bytes().decode("utf-8", "replace")
    names = []
    for row in csv.reader(io.StringIO(raw)):
        if not row:
            continue
        n = row[0].strip()
        # 数字を含み、かつ m/z らしき数値そのものではない名前
        if n and re.search(r"\d", n) and not re.fullmatch(r"[\d.]+", n):
            names.append(n)
        if len(names) >= limit:
            break
    if not names:
        pytest.skip("DB から数字入り化合物名を取得できなかった")
    return names


class TestRoundTrip:
    """make_column_name → extract_mz_numeric で元の m/z に戻ること。"""

    @pytest.mark.parametrize("mz", [61.0648, 105.0546, 419.2572, 760.5851, 1475.9870])
    def test_roundtrip_over_real_compound_names(self, mz):
        """★ 数字入り化合物名 400 件すべてで m/z を復元できること。"""
        bad = []
        for name in _compound_names():
            col = make_column_name(name, mz)
            got = extract_mz_numeric(col)
            if abs(got - mz) > 1e-6:
                bad.append((name, col, got))
        assert not bad, (
            f"{len(bad)} 件で m/z を復元できない (期待 {mz})。先頭 5 件:\n" +
            "\n".join(f"  {n!r} -> {c!r} -> {g}" for n, c, g in bad[:5]))

    def test_roundtrip_with_pipe_sections(self):
        """DB/アダクト欄が付いた完全な形式でも復元できること。"""
        for name in _compound_names(limit=200):
            raw = f"{name} | HMDB | [M+H]+ | 1.2ppm"
            col = make_column_name(raw, 760.5851)
            assert abs(extract_mz_numeric(col) - 760.5851) < 1e-6, col

    def test_empty_name_falls_back_to_bare_numeric(self):
        """Name が空のとき make_column_name は素の数値列名を作る（その形も読めること）。"""
        col = make_column_name("", 419.2572)
        assert abs(extract_mz_numeric(col) - 419.2572) < 1e-6, col


class TestOldRuleWouldFail:
    """★ この番人が空振りでないことの担保。

    旧規則（最初の数字）なら同じ入力で落ちることを示す。これが無いと
    「たまたま通っているだけ」の可能性が残る。
    """

    def test_old_first_number_rule_breaks_on_the_same_inputs(self):
        def old_rule(f):
            m = re.search(r"(\d+\.?\d*)", f)
            return float(m.group(1)) if m else float("inf")

        mz = 760.5851
        broken = 0
        names = _compound_names(limit=200)
        for name in names:
            if abs(old_rule(make_column_name(name, mz)) - mz) > 1e-6:
                broken += 1
        assert broken > len(names) * 0.5, (
            f"旧規則が {broken}/{len(names)} 件しか壊れていない。"
            "テストデータが「数字入り化合物名」になっていない疑い")


class TestSidecarJoinSurvives:
    """★ 化合物名アノテーションが復活すること。

    seurat_bridge._load_feature_annotations は feature 名から m/z を取り出し、
    サイドカーの `mz` 列と 0.005 Da 以内で突き合わせる。旧規則では annotated な
    feature が全部弾かれ、`feature_annotations` が **空 dict** になっていた
    （サイドカー側には正しい m/z が入っているのに）。結果、化合物名アノテーションを
    持つデータセットに限って化合物名表示が丸ごと死んでいた。
    """

    def test_annotated_features_match_their_sidecar_rows(self, tmp_path):
        pytest.importorskip("pyarrow")
        import pandas as pd
        from app.services.seurat_bridge import SeuratBridge

        mzs = [104.1059, 760.5851, 1475.9870]
        compounds = ["Choline", "PI 38:4 (PI 18:0/20:4)", "CL 74:8"]
        features = [make_column_name(c, m) for c, m in zip(compounds, mzs)]

        side = tmp_path / "S1_feature_annotations.parquet"
        pd.DataFrame({
            "mz": mzs,
            "compound": compounds,
            "display_name": [f"{c}_{m:.4f}" for c, m in zip(compounds, mzs)],
            "adduct": ["[M+H]+"] * 3,
            "formula": [""] * 3,
            "smiles": [""] * 3,
            "database": ["HMDB"] * 3,
            "lipid_class": [""] * 3,
            "ppm": [0.0] * 3,
        }).to_parquet(side)

        rds = tmp_path / "obj.rds"
        rds.write_bytes(b"")
        cache = tmp_path / "cache"
        cache.mkdir()
        out = SeuratBridge()._load_feature_annotations(cache, rds, features)

        assert out, "annotated feature が 1 件も突き合わせられていない"
        assert set(out) == set(features), f"取りこぼし: {set(features) - set(out)}"
        for feat, comp in zip(features, compounds):
            assert out[feat]["compound"] == comp


# ---------------------------------------------------------------------------
# ver51.8 の自己回帰: DESI の MRM 形式と make.unique サフィックス
# ---------------------------------------------------------------------------
# ★ ver51.8 で「認識できない形式は inf」に変えたとき、**DESI の feature 名を
#   まるごと取りこぼしていた**。DESI は MRM の Q1-Q3 ペアを
#       metabolite_names <- paste(pre_masses, post_masses, sep = "-")
#   (DESI テンプレート v16) で作り、そのまま Seurat の rowname → features_list.txt
#   になる。"146.1-102.0" は「認識できない名前」ではなく、先頭がプリカーサ m/z。
#   旧実装は最初の数字を取るので偶然正しく 146.1 を返していた。
#
#   inf になると: feature ドロップダウンが空になる / 対話キャリブレーションが
#   無言で何もしなくなる / 化合物名が付かなくなる。**DESI 全体が壊れる。**
#
# ★ R の make.unique() が重複名に付ける ".1" ".2" も同様に取りこぼしていた
#   (TIMS/DESI 双方の rownames に適用される)。

class TestDesiMrmNames:
    @pytest.mark.parametrize("name,expected", [
        ("146.1-102.0", 146.1),
        ("146.1-102", 146.1),
        ("180.0634-92.0", 180.0634),
        ("419.257200-0.0", 419.2572),
    ])
    def test_mrm_transition_uses_precursor(self, name, expected):
        """★ Q1-Q3 形式はプリカーサ (Q1) を m/z とすること。"""
        assert extract_mz_numeric(name) == pytest.approx(expected)

    def test_desi_features_are_not_all_dropped(self):
        """★ DESI の feature 一覧が m/z フィルタで全滅しないこと。

        inf だらけになると apply_mz_filter が空リストを返し、
        feature ドロップダウンが無言で空になる。
        """
        names = [f"{q1}-{q3}" for q1, q3 in
                 [("146.1", "102.0"), ("180.1", "92.0"), ("204.2", "60.1")]]
        got = [extract_mz_numeric(n) for n in names]
        assert all(g != float("inf") for g in got), got
        assert got == pytest.approx([146.1, 180.1, 204.2])


class TestMakeUniqueSuffix:
    @pytest.mark.parametrize("name,expected", [
        ("m/z 419.25720.1", 419.2572),
        ("Glucose_180.0634.1", 180.0634),
        ("146.1-102.0.1", 146.1),
        ("PI 38:4 (PI 18:0/20:4)_760.5851.2", 760.5851),
    ])
    def test_duplicate_suffix_is_tolerated(self, name, expected):
        """★ make.unique の ".N" が付いても m/z を取れること。"""
        assert extract_mz_numeric(name) == pytest.approx(expected)

    def test_real_decimals_are_not_truncated(self):
        """★ 逆に、本物の小数を ".N" と誤認して削らないこと。

        "419.2572" を素の数値として先に拾えているかの確認。
        削ってしまうと 419 になり、まったく別のピークを指す。
        """
        assert extract_mz_numeric("419.2572") == pytest.approx(419.2572)
        assert extract_mz_numeric("mz_123.456") == pytest.approx(123.456)
        assert extract_mz_numeric("146.1-102.0") == pytest.approx(146.1)
