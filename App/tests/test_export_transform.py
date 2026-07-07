"""データ出力の列付与（ベクトル化）が旧 iterrows 実装と同一結果になることのテスト。

対象: app.services.export_transform.append_cluster_region_columns（Dash 非依存）
"""
import numpy as np
import pandas as pd

from app.services.export_transform import append_cluster_region_columns


def _match(stem, names):
    if stem in names:
        return stem
    for n in names:
        if stem in n or n in stem:
            return n
    return None


def _reference_iterrows(df, method_lookups, region_lookup, all_sample_list,
                        is_multi, stem):
    """旧実装（iterrows）の参照。ベクトル版の同値性確認用。"""
    out = df.copy()
    has_ann = "annotation" in out.columns
    row_keys = []
    for _, row in out.iterrows():
        xv, yv = row.get("x"), row.get("y")
        sid = str(row["annotation"]) if has_ann else stem
        m = sid if sid in all_sample_list else _match(sid, all_sample_list)
        if m and pd.notna(xv) and pd.notna(yv):
            row_keys.append((m, round(float(xv), 4), round(float(yv), 4)))
        else:
            row_keys.append((None, None, None))
    for mn in method_lookups:
        cn = mn if is_multi else "UMAP cluster"
        out[cn] = [method_lookups[mn].get((m, x, y), "") if m else ""
                   for m, x, y in row_keys]
    if region_lookup is not None:
        out["領域名"] = [region_lookup.get((m, x, y), "") if m else ""
                       for m, x, y in row_keys]
    return out


def _fixture():
    df = pd.DataFrame({
        "id": [1, 2, 3, 4, 5, 6],
        "x": [12.34565, 56.7, 10.0, np.nan, 12.34565, 99.9],
        "y": [1.0, 2.22225, 3.0, 4.0, 1.0, np.nan],
        "611.1439": [100, 200, 300, 400, 500, 600],
        "annotation": ["SecA", "SecA", "SecB", "SecA", "SecX", "SecB"],
    })
    all_sample_list = sorted(["SecA", "SecB"])

    def rk(s, x, y):
        return (s, round(float(x), 4), round(float(y), 4))
    methodA = {rk("SecA", 12.34565, 1.0): "cluster1",
               rk("SecB", 10.0, 3.0): "cluster2",
               rk("SecB", 99.9, 3.0): "cluster9"}
    region = {rk("SecA", 12.34565, 1.0): "Brain",
              rk("SecB", 10.0, 3.0): "Liver"}
    return df, all_sample_list, methodA, region


def test_vectorized_equals_iterrows_single_method():
    df, asl, methodA, region = _fixture()
    ml = {"RPCA": methodA}
    new = append_cluster_region_columns(df.copy(), ml, region, asl, False,
                                        "stem", _match)
    ref = _reference_iterrows(df.copy(), ml, region, asl, False, "stem")
    assert (new["UMAP cluster"] == ref["UMAP cluster"]).all()
    assert (new["領域名"] == ref["領域名"]).all()
    # 元 m/z 列は不変
    assert (new["611.1439"] == df["611.1439"]).all()
    # 未一致(SecX)・NaN 座標行は空欄
    assert new.loc[3, "UMAP cluster"] == "" and new.loc[4, "UMAP cluster"] == ""


def test_vectorized_equals_iterrows_multi_method_and_no_region():
    df, asl, methodA, _ = _fixture()

    def rk(s, x, y):
        return (s, round(float(x), 4), round(float(y), 4))
    methodB = {rk("SecA", 12.34565, 1.0): "H1"}
    ml = {"RPCA": methodA, "Harmony": methodB}
    new = append_cluster_region_columns(df.copy(), ml, None, asl, True,
                                        "stem", _match)
    ref = _reference_iterrows(df.copy(), ml, None, asl, True, "stem")
    for cn in ("RPCA", "Harmony"):
        assert (new[cn] == ref[cn]).all()
    assert "領域名" not in new.columns  # region_lookup=None なら付けない


def test_no_annotation_uses_stem():
    # annotation 列が無い場合は stem をサンプル名に使う
    df = pd.DataFrame({"x": [10.0, 20.0], "y": [3.0, 5.0], "mz1": [1, 2]})
    asl = ["SecB"]

    def rk(s, x, y):
        return (s, round(float(x), 4), round(float(y), 4))
    ml = {"RPCA": {rk("SecB", 10.0, 3.0): "cluster2"}}
    new = append_cluster_region_columns(df.copy(), ml, None, asl, False,
                                        "SecB", _match)
    assert new.loc[0, "UMAP cluster"] == "cluster2"
    assert new.loc[1, "UMAP cluster"] == ""
