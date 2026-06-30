"""annotation_sources（外部由来の取込・対応づけ・由来表示）の単体テスト。"""
import numpy as np
import pandas as pd

from app.services import annotation_sources as asrc


def test_import_metaspace_keeps_fdr_and_source():
    df = pd.DataFrame({
        "formula": ["C10H12N5O13P3", "C21H39O7P"],
        "adduct": ["[M-H]-", "[M-H]-"],
        "mz": [505.9885, 433.2356],
        "fdr": [0.1, 0.2],
        "msm": [0.95, 0.7],
        "moleculeNames": ["ATP, Adenosine triphosphate", "PA(18:1)"],
    })
    cands = asrc.import_metaspace_table(df)
    assert cands[0]["source"] == asrc.SOURCE_METASPACE
    assert cands[0]["name"] == "ATP"           # 先頭候補を代表名に
    assert cands[0]["source_metrics"]["fdr"] == 0.1
    assert "moleculeNames" in cands[0]["source_metrics"]


def test_import_lcms_and_msdial_sources():
    lc = pd.DataFrame({"m/z": [100.05], "Name": ["Lactate"], "RT": [3.2]})
    cands = asrc.import_lcms_table(lc)
    assert cands[0]["source"] == asrc.SOURCE_LCMS
    assert cands[0]["source_metrics"]["rt"] == 3.2

    md = pd.DataFrame({"Average Mz": [200.1], "Metabolite name": ["Unknown"],
                       "Adduct type": ["[M+H]+"], "Total score": [80.0]})
    cands2 = asrc.import_msdial_table(md)
    assert cands2[0]["source"] == asrc.SOURCE_MSDIAL
    assert cands2[0]["name"] == ""             # Unknown は名前空
    assert cands2[0]["source_metrics"]["score"] == 80.0


def test_matching_and_priority_order():
    feature_mz = [100.0500, 200.1000]
    cands = (
        asrc.import_metaspace_table(pd.DataFrame(
            {"mz": [100.0502], "adduct": ["[M-H]-"], "fdr": [0.1],
             "moleculeNames": ["Foo"], "formula": ["C3H5O3"]})) +
        asrc.import_lcms_table(pd.DataFrame(
            {"m/z": [100.0498], "Name": ["Lactate"]}))
    )
    matches = asrc.match_candidates_to_features(feature_mz, cands, tol_da=0.01)
    got = matches[100.05]
    # LC-MS/MS が METASPACE より優先（先頭）
    assert got[0]["source"] == asrc.SOURCE_LCMS
    assert got[1]["source"] == asrc.SOURCE_METASPACE
    # ppm が補完される
    assert got[0]["ppm"] is not None


def test_no_match_outside_tolerance():
    matches = asrc.match_candidates_to_features(
        [500.0],
        asrc.import_lcms_table(pd.DataFrame({"m/z": [400.0], "Name": ["X"]})),
        tol_da=0.01,
    )
    assert matches == {}


def test_format_label_and_summary():
    cands = asrc.import_metaspace_table(pd.DataFrame(
        {"mz": [505.9885], "adduct": ["[M-H]-"], "fdr": [0.1],
         "moleculeNames": ["ATP"], "formula": ["C10"]}))
    label = asrc.format_annotation_label(cands[0])
    assert "ATP" in label and "METASPACE" in label and "FDR=10%" in label

    matches = asrc.match_candidates_to_features([505.9885], cands, tol_da=0.01)
    summ = asrc.summarize_feature(matches[505.9885])
    assert summ["primary_source"] == asrc.SOURCE_METASPACE
    assert summ["n_candidates"] == 1


def test_build_feature_source_map_is_json_friendly():
    cands = asrc.import_lcms_table(pd.DataFrame({"m/z": [100.05], "Name": ["Lac"]}))
    fmap = asrc.build_feature_source_map([100.05], cands, tol_da=0.01)
    assert "100.0500" in fmap
    assert fmap["100.0500"]["primary_source"] == asrc.SOURCE_LCMS
