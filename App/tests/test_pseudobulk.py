"""pseudobulk（ROI/サンプル集約と sample-level 比較）の単体テスト。"""
import numpy as np
import pandas as pd

from app.services import pseudobulk as pb


def test_t_two_sided_p_known_value():
    # t=2.0, df=10 の両側 p ≈ 0.0734
    assert abs(pb.t_two_sided_p(2.0, 10) - 0.0734) < 1e-3
    assert abs(pb.t_two_sided_p(0.0, 10) - 1.0) < 1e-9


def test_welch_ttest_separates_groups():
    a = [10.0, 10.2, 9.8, 10.1]
    b = [1.0, 1.1, 0.9, 1.05]
    t, df, p = pb.welch_ttest(a, b)
    assert t > 0 and p < 0.001


def test_aggregate_pseudobulk_means_and_counts():
    meta = pd.DataFrame({
        "Sample": ["s1", "s1", "s2", "s2", "s2"],
        "Cluster": ["1", "1", "1", "1", "1"],
    })
    expr = pd.DataFrame({"mz_100": [2.0, 4.0, 10.0, 20.0, 30.0]})
    out = pb.aggregate_pseudobulk(meta, expr, group_cols=["Sample", "Cluster"])
    s1 = out[out["Sample"] == "s1"].iloc[0]
    s2 = out[out["Sample"] == "s2"].iloc[0]
    assert s1["mz_100"] == 3.0 and s1["n_pixels"] == 2
    assert s2["mz_100"] == 20.0 and s2["n_pixels"] == 3


def test_sample_level_test_with_replicates():
    # cond X の 3 サンプルは高値、cond Y の 3 サンプルは低値
    pbk = pd.DataFrame({
        "Sample": ["a", "b", "c", "d", "e", "f"],
        "Cluster": ["1"] * 6,
        "n_pixels": [10] * 6,
        "mz_100": [9.5, 10.0, 10.5, 1.0, 1.2, 0.8],
    })
    cmap = {"a": "X", "b": "X", "c": "X", "d": "Y", "e": "Y", "f": "Y"}
    out = pb.sample_level_test(pbk, cmap)
    assert out["descriptive_only"] is False
    assert out["n_a"] == 3 and out["n_b"] == 3
    row = out["result"].iloc[0]
    assert row["p_val"] < 0.01
    assert abs(row["log2fc"]) > 2


def test_sample_level_test_descriptive_when_no_replicates():
    pbk = pd.DataFrame({
        "Sample": ["a", "b"],
        "n_pixels": [10, 10],
        "mz_100": [9.0, 1.0],
    })
    out = pb.sample_level_test(pbk, {"a": "X", "b": "Y"})
    assert out["descriptive_only"] is True
    assert np.isnan(out["result"].iloc[0]["p_val"])
    assert "記述統計のみ" in out["note"]


def test_bh_adjust_monotone():
    p = np.array([0.001, 0.01, 0.5, np.nan])
    adj = pb.bh_adjust(p)
    assert np.isnan(adj[3])
    assert adj[0] <= adj[1] <= adj[2]
    assert (adj[np.isfinite(adj)] <= 1).all()
