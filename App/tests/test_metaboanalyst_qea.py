"""MetaboAnalyst QEA エクスポート変換のテスト。

対象: app.services.metaboanalyst_qea
- Group 分解（stage/anatomy/cluster）
- Sample ID の一意・class 種別間の一貫
- class 割当（stage/anatomy/cluster）と cluster_ge<N> 除外
- 0→NA 変換
- 脂質名の保守的正規化・非脂質の非改変・衝突時の原文保持
"""
import io

import numpy as np
import pandas as pd

from app.services import metaboanalyst_qea as mq


def _read(files, name):
    return pd.read_csv(io.StringIO(files[name]))


def _sample_matrix():
    rows = [
        ("E14_Brain_cluster1", 1.2, 0.0, 3.4),
        ("E14_Liver_cluster1", 2.1, 1.1, 0.0),
        ("E16_Brain_cluster2", 0.0, 2.2, 1.0),
        ("E16_Liver_cluster2", 1.5, 0.5, 2.0),
        ("E18_Cardiomediastinal region / heart and great vessels_cluster20",
         0.3, 0.0, 0.9),
    ]
    mat = pd.DataFrame(rows, columns=["Group", "PG 32:1", "Glutathione", "ATP"])
    fmap = pd.DataFrame([
        {"compound": "PG 32:1", "lipid_class": "PG", "is_representative": "True"},
        {"compound": "Glutathione", "lipid_class": "", "is_representative": "True"},
        {"compound": "ATP", "lipid_class": "", "is_representative": "True"},
    ])
    return mat, fmap


def test_parse_group_variants():
    assert mq.parse_group("E14_Brain_cluster1") == ("E14", "Brain", "cluster1")
    # anatomy に空白/スラッシュ
    assert mq.parse_group(
        "E18_Cardiomediastinal region / heart and great vessels_cluster20"
    ) == ("E18", "Cardiomediastinal region / heart and great vessels", "cluster20")
    # anatomy にハイフン/スラッシュ
    assert mq.parse_group("E14_Somatic mesoderm/body-wall region_cluster9") == (
        "E14", "Somatic mesoderm/body-wall region", "cluster9")
    # cluster 欠落 → cluster 空
    assert mq.parse_group("E14_Brain") == ("E14", "Brain", "")


def test_sample_ids_unique_and_consistent_across_classes():
    mat, fmap = _sample_matrix()
    files = mq.build_qea_bundle(mat, fmap, cluster_min=2)
    stage = _read(files, "exploratory_QEA_stage_raw.csv")
    meta = _read(files, "sample_metadata.csv")
    # 一意
    assert stage["Sample"].is_unique
    assert list(meta["sample_id"]) == [f"S{i:04d}" for i in range(1, 6)]
    # class 種別間で同一 Sample↔Group 対応（stage と anatomy で照合）
    anat = _read(files, "exploratory_QEA_anatomy_raw.csv")
    s2g = dict(zip(meta["sample_id"], meta["original_group"]))
    # stage/anatomy の Sample 集合は同じ元 Group を指す
    assert set(stage["Sample"]) == set(anat["Sample"]) == set(s2g)


def test_class_assignment_values():
    mat, fmap = _sample_matrix()
    files = mq.build_qea_bundle(mat, fmap, cluster_min=2)
    stage = _read(files, "exploratory_QEA_stage_raw.csv")
    assert set(stage["Class"]) == {"E14", "E16", "E18"}
    anat = _read(files, "exploratory_QEA_anatomy_raw.csv")
    assert "Brain" in set(anat["Class"]) and "Liver" in set(anat["Class"])


def test_cluster_ge_filter_drops_small_clusters():
    mat, fmap = _sample_matrix()
    files = mq.build_qea_bundle(mat, fmap, cluster_min=2)
    # cluster1(n=2), cluster2(n=2) 残り, cluster20(n=1) 除外
    assert "exploratory_QEA_cluster_ge2_raw.csv" in files
    ge = _read(files, "exploratory_QEA_cluster_ge2_raw.csv")
    assert set(ge["Class"]) == {"cluster1", "cluster2"}
    assert "cluster20" not in set(ge["Class"])
    # 全部版には cluster20 も含まれる
    allc = _read(files, "exploratory_QEA_cluster_all_raw.csv")
    assert "cluster20" in set(allc["Class"])


def test_zero_as_na_blanks_zeros():
    mat, fmap = _sample_matrix()
    files = mq.build_qea_bundle(mat, fmap, cluster_min=2)
    na = _read(files, "exploratory_QEA_stage_zeroAsNA.csv")
    raw = _read(files, "exploratory_QEA_stage_raw.csv")
    # raw では 0 が残る、NA 版では欠測
    assert (raw["Glutathione"] == 0).sum() >= 1
    assert na.loc[raw["Glutathione"] == 0, "Glutathione"].isna().all()
    # 非ゼロは保持
    assert (na["ATP"].dropna() != 0).all()


def test_lipid_name_normalization_conservative():
    # 脂質は空白/句読点のみ正規化、非脂質は不変
    assert mq.normalize_lipid_name("PG  32 : 1", "PG")[0] == "PG 32:1"
    assert mq.normalize_lipid_name("Cer 28:4 ; O4", "Cer")[0] == "Cer 28:4;O4"
    assert mq.normalize_lipid_name("Glutathione", "")[0] == "Glutathione"
    assert mq.normalize_lipid_name("Glutathione", "")[1] is False


def test_normalization_collision_keeps_original():
    # 正規化後に衝突する2列は原文を保持して一意性を維持
    mat = pd.DataFrame([
        ("E14_Brain_cluster1", 1.0, 2.0),
        ("E16_Brain_cluster1", 3.0, 4.0),
    ], columns=["Group", "PG 32:1", "PG  32:1"])
    fmap = pd.DataFrame([
        {"compound": "PG 32:1", "lipid_class": "PG", "is_representative": "True"},
        {"compound": "PG  32:1", "lipid_class": "PG", "is_representative": "True"},
    ])
    files = mq.build_qea_bundle(mat, fmap, cluster_min=1)
    stage = _read(files, "exploratory_QEA_stage_raw.csv")
    # 列名が一意（Sample, Class を除く）
    feat_cols = [c for c in stage.columns if c not in ("Sample", "Class")]
    assert len(feat_cols) == len(set(feat_cols))


def test_empty_or_single_class_skipped():
    # stage が1種のみ → stage ファイルは作られない（QEA 不可）
    mat = pd.DataFrame([
        ("E14_Brain_cluster1", 1.0),
        ("E14_Liver_cluster2", 2.0),
    ], columns=["Group", "ATP"])
    files = mq.build_qea_bundle(mat, None, cluster_min=1)
    assert "exploratory_QEA_stage_raw.csv" not in files      # stage=E14 のみ
    assert "exploratory_QEA_anatomy_raw.csv" in files        # Brain/Liver の2種
