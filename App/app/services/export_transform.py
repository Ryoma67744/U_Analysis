# =============================================================================
# MSI Analysis Application - Data export transform (Dash 非依存・単体テスト可)
#
# インタラクティブ「データ出力」で、元データ(1行=1スポット)に UMAP クラスタ列と
# 領域名(ROI)列を「右端に」付与する純粋関数。全 m/z 列を1行ずつ箱詰めする iterrows を
# 使わず、x/y/annotation の3列だけをベクトル処理する（本来の軽さに戻すための中核）。
# =============================================================================

import pandas as pd


def append_cluster_region_columns(
    df: "pd.DataFrame",
    method_lookups: dict,
    region_lookup: "dict | None",
    all_sample_list: list,
    is_multi: bool,
    stem: "str | None",
    match_sample_fn,
) -> "pd.DataFrame":
    """df に手法別 UMAP クラスタ列（＋領域名列）をベクトル辞書引きで付与して返す。

    lookup のキーは (sample, round(x,4), round(y,4))。丸めは lookup 構築側と同一の
    Python round を用い、キー完全一致を保証する（未一致は空欄）。
    match_sample_fn(sample_id, all_sample_list) -> matched | None（サンプル名の曖昧一致）。
    """
    all_sample_set = set(all_sample_list)
    has_annotation = "annotation" in df.columns
    n_rows = len(df)

    # サンプル名の解決はユニーク値だけ（行数に依らず軽い）。
    if has_annotation:
        sample_arr = df["annotation"].astype(str).to_numpy()
        match_map = {
            s: (s if s in all_sample_set else match_sample_fn(s, all_sample_list))
            for s in set(sample_arr.tolist())
        }
    else:
        m0 = stem if stem in all_sample_set else match_sample_fn(stem, all_sample_list)

    xa = (pd.to_numeric(df["x"], errors="coerce").to_numpy()
          if "x" in df.columns else None)
    ya = (pd.to_numeric(df["y"], errors="coerce").to_numpy()
          if "y" in df.columns else None)

    # キー列 (sample, round(x,4), round(y,4)) を構築（触るのは3列のみ）。
    keys: list = [None] * n_rows
    if xa is not None and ya is not None:
        if has_annotation:
            for i, (s, xv, yv) in enumerate(zip(sample_arr, xa, ya)):
                m = match_map.get(s)
                if m is not None and xv == xv and yv == yv:  # xv==xv: 非NaN
                    keys[i] = (m, round(float(xv), 4), round(float(yv), 4))
        elif m0 is not None:
            for i, (xv, yv) in enumerate(zip(xa, ya)):
                if xv == xv and yv == yv:
                    keys[i] = (m0, round(float(xv), 4), round(float(yv), 4))
    keys_ser = pd.Series(keys, index=df.index, dtype=object)

    for method_name in method_lookups.keys():
        col_name = method_name if is_multi else "UMAP cluster"
        df[col_name] = keys_ser.map(method_lookups[method_name]).fillna("").to_numpy()
    if region_lookup is not None:
        df["領域名"] = keys_ser.map(region_lookup).fillna("").to_numpy()
    return df
