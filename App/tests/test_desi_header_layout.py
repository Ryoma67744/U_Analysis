"""DESI .txt の 4 行 / 5 行ヘッダの解釈 (ver55.2)

背景: Python も R も **ヘッダ 4 行決め打ち**だった。実際の装置出力は 5 行
(空 / 化合物名 / 代謝物番号 / Q1 / Q3) で、次の 3 つが同時に起きていた。

  (a) 特徴量名が「代謝物番号-Q1」(例 `1-90.0477`) という無意味な連結になる
      — 本来意図されていた `Q1-Q3` ですらない
  (b) 化合物名は読み込まれた直後に捨てられる
  (c) Q3 の行がデータ 1 行目として読まれ、座標 NA の幽霊ピクセルが混入する

実データ `260729_test_POS1.txt` の形状をそのまま縮小した合成データで検査する。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import data_manager as dm
from app.services import desi_header as dh
from app.utils.deg_utils import extract_mz_numeric


# 実データと同じ形: 1列目に測定条件由来の余計な値 ('5')、列2-3 は空、列4 以降が中身
_FIVE_ROW_HEADER = [
    [],
    ["5", "", "", "POS_AA_Ala_24_6", "POS-NTs-GABA_10_10", "Histamine_25_15"],
    ["", "", "", "1", "2", "3"],
    ["", "", "", "90.0477", "104.0000", "112.2000"],
    ["", "", "", "44.0900", "87.0000", "95.1200"],
]

# desi_converter が新形式から組み替えた形 (空 / 代謝物番号 / 化合物名 / 空)
_FOUR_ROW_NAMED = [
    [],
    ["", "", "", "1", "2", "3"],
    ["", "", "", "Acetylcholine", "Creatine", "Glutamine"],
    [],
]

# 化合物名を持たない 4 行ヘッダ (空 / 代謝物番号 / Q1 / Q3)
_FOUR_ROW_PLAIN = [
    [],
    ["", "", "", "1", "2", "3"],
    ["", "", "", "90.0477", "104.0000", "112.2000"],
    ["", "", "", "44.0900", "87.0000", "95.1200"],
]


def _write(tmp_path: Path, header, n_data=4) -> Path:
    rows = [list(r) for r in header]
    for i in range(1, n_data + 1):
        rows.append([str(i), f"{i}.5", f"-{i}.25", "100", "200", "300"])
    p = tmp_path / "sample.txt"
    p.write_text("\n".join("\t".join(r) for r in rows) + "\n", encoding="utf-8")
    return p


class TestFiveRowHeader:
    def test_layout_is_detected(self, tmp_path):
        h = dh.read_desi_header(_write(tmp_path, _FIVE_ROW_HEADER))
        assert h.n_header == 5
        # 1 列目の '5' を拾わない（拾うと特徴量数と 1 個ズレる）
        assert h.compounds == ["POS_AA_Ala_24_6", "POS-NTs-GABA_10_10", "Histamine_25_15"]
        assert h.numbers == ["1", "2", "3"]
        assert h.q1 == ["90.0477", "104.0000", "112.2000"]
        assert h.q3 == ["44.0900", "87.0000", "95.1200"]

    def test_feature_names_carry_compound_and_transition(self, tmp_path):
        h = dh.read_desi_header(_write(tmp_path, _FIVE_ROW_HEADER))
        assert h.feature_names == [
            "POS_AA_Ala (90.0477-44.0900)",
            "POS-NTs-GABA (104.0000-87.0000)",
            "Histamine (112.2000-95.1200)",
        ]

    def test_legacy_names_are_reproduced_for_matching(self, tmp_path):
        """修正前の名前を再現できないと、保存済みリストと突合できない。"""
        p = _write(tmp_path, _FIVE_ROW_HEADER)
        h = dh.read_desi_header(p)
        assert h.legacy_feature_names == ["1-90.0477", "2-104.0000", "3-112.2000"]
        assert dh.legacy_alias_map(p) == {
            "1-90.0477": "POS_AA_Ala (90.0477-44.0900)",
            "2-104.0000": "POS-NTs-GABA (104.0000-87.0000)",
            "3-112.2000": "Histamine (112.2000-95.1200)",
        }


class TestFourRowHeadersAreUnchanged:
    def test_converter_named_layout(self, tmp_path):
        h = dh.read_desi_header(_write(tmp_path, _FOUR_ROW_NAMED))
        assert h.n_header == 4
        assert h.feature_names == ["Acetylcholine", "Creatine", "Glutamine"]
        # 名前が変わらない構成では対応表は空
        assert dh.legacy_alias_map(_write(tmp_path, _FOUR_ROW_NAMED)) == {}

    def test_plain_transition_layout(self, tmp_path):
        h = dh.read_desi_header(_write(tmp_path, _FOUR_ROW_PLAIN))
        assert h.n_header == 4
        assert not h.has_compounds
        assert h.feature_names == [
            "90.0477-44.0900", "104.0000-87.0000", "112.2000-95.1200",
        ]


class TestDataStartDetection:
    @pytest.mark.parametrize(
        "header,expected",
        [(_FIVE_ROW_HEADER, 5), (_FOUR_ROW_NAMED, 4), (_FOUR_ROW_PLAIN, 4)],
    )
    def test_roi_reader_skips_the_right_number_of_lines(self, tmp_path, header, expected):
        """ROI 判定が Q3 の行をデータとして数えないこと。"""
        p = _write(tmp_path, header)
        assert dh.read_desi_header(p).n_header == expected
        # ROI ラベル列が無いので空。ヘッダ行が混ざると誤検出しうる
        assert dm.read_desi_roi_list(str(p)) == []

    def test_roi_labels_are_found_with_five_row_header(self, tmp_path):
        rows = [list(r) for r in _FIVE_ROW_HEADER]
        labels = ["Tumor", "Normal"]
        for i in range(1, 9):
            rows.append([str(i), f"{i}.5", f"-{i}.25", "100", "200", "300", labels[i % 2]])
        p = tmp_path / "roi.txt"
        p.write_text("\n".join("\t".join(r) for r in rows) + "\n", encoding="utf-8")
        assert dm.read_desi_roi_list(str(p)) == ["Normal", "Tumor"]

    def test_validate_rejects_a_file_whose_header_never_ends(self, tmp_path):
        """★ 以前は「5 行目が 4 列以上あるか」だけを見ており、5 行ヘッダの
        Q3 行をデータと誤認したまま valid を返していた。"""
        rows = [list(r) for r in _FIVE_ROW_HEADER]  # データ行が 1 つも無い
        p = tmp_path / "headeronly.txt"
        p.write_text("\n".join("\t".join(r) for r in rows) + "\n", encoding="utf-8")
        assert dm.validate_msi_file(str(p))["valid"] is False

    def test_validate_accepts_real_shape(self, tmp_path):
        assert dm.validate_msi_file(str(_write(tmp_path, _FIVE_ROW_HEADER)))["valid"] is True


class TestExtractMzFromNamedTransition:
    def test_named_transition_resolves_to_q1(self):
        assert extract_mz_numeric("POS_AA_Ala (90.0477-44.0900)") == pytest.approx(90.0477)

    def test_make_unique_suffix_is_tolerated(self):
        assert extract_mz_numeric("Histamine (112.2000-95.1200).1") == pytest.approx(112.2)

    def test_plain_transition_still_works(self):
        assert extract_mz_numeric("146.1-102.0") == pytest.approx(146.1)

    def test_compound_only_name_has_no_mz(self):
        assert extract_mz_numeric("Acetylcholine") == float("inf")


class TestActiveRTemplatesParse:
    """R テンプレートの構文検査。

    ★ この検査が無かったため、R 側の変更は「この環境に R が無いので未検証」として
      何度も出荷されていた。Rscript があるときは必ず走らせる。
    """

    ACTIVE = [
        "Script/DESI/260623_DESI-UMAP_Template_v16.R",
        "Script/DESI/DESI_RDS_ClusterFilter_ver3.R",
        "Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R",
        "Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R",
    ]

    @pytest.mark.parametrize("rel", ACTIVE)
    def test_parses(self, rel):
        rscript = shutil.which("Rscript")
        if not rscript:
            pytest.skip("Rscript が無い環境")
        path = Path(__file__).resolve().parents[1] / rel
        assert path.is_file(), f"テンプレートが見つかりません: {rel}"
        proc = subprocess.run(
            [rscript, "-e", f"invisible(parse({str(path)!r}))"],
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, f"{rel} の構文エラー:\n{proc.stderr}"


class TestStripParamSuffix:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("POS_AA_Ala_24_6", "POS_AA_Ala"),
            ("POS-NTs-GABA_10_10", "POS-NTs-GABA"),
            ("Ophthalmic_acid_25_20", "Ophthalmic_acid"),
            ("Adenosine-POS_32_18", "Adenosine-POS"),
            ("Methionine1_15_10", "Methionine1"),
            ("Acetylcholine", "Acetylcholine"),
        ],
    )
    def test_trailing_measurement_params_are_removed(self, raw, expected):
        assert dh.strip_param_suffix(raw) == expected
