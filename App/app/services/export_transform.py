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
    stats: "dict | None" = None,
) -> "pd.DataFrame":
    """df に手法別 UMAP クラスタ列（＋領域名列）をベクトル辞書引きで付与して返す。

    lookup のキーは (sample, round(x,4), round(y,4))。丸めは lookup 構築側と同一の
    Python round を用い、キー完全一致を保証する（未一致は空欄）。
    match_sample_fn(sample_id, all_sample_list) -> matched | None（サンプル名の曖昧一致）。

    stats: dict を渡すと突合の内訳を書き込む（呼び出し側が利用者へ報告するため）。
      rows / keyed / matched / resolver / unresolved_samples / stem

    ★ ver58.3: サンプル名の解決を **annotation → ファイル名 stem** の 2 段にした。
      従来は annotation 列があると無条件にそれだけを使い、外れたら stem に戻らなかった。
      SCiLS 変換は annotation 列を**必ず**書き、領域アノテーション CSV を渡さない場合は
      ver55.0 以降 全 spot が `"Unannotated"` になる。一方 plot_data の `Sample` は
      `extract_seurat_data.R` が「ユニーク数が最大の列」を採るため、値が 1 種類しかない
      `"Unannotated"` は絶対に採用されず **ファイル名側**になる。結果、突合が 1 件も
      成立せず、**クラスタ列も領域名列も全行が空文字**のまま「成功」していた
      （annotation ラベル名がファイル名と違う運用でも同じことが起きる）。
      annotation が 1 件も解決できなかったときに限り stem で引き直す。
      一部でも解決できたときは戻さない（サンプルを取り違えるより空欄の方が安全）。
    """
    all_sample_set = set(all_sample_list)
    has_annotation = "annotation" in df.columns
    n_rows = len(df)

    xa = (pd.to_numeric(df["x"], errors="coerce").to_numpy()
          if "x" in df.columns else None)
    ya = (pd.to_numeric(df["y"], errors="coerce").to_numpy()
          if "y" in df.columns else None)

    # stem 側の解決はフォールバック判定に使うので先に済ませる。
    stem_match = None
    if stem is not None:
        stem_match = (stem if stem in all_sample_set
                      else match_sample_fn(stem, all_sample_list))

    resolver = "none"
    unresolved: list = []
    keys: list = [None] * n_rows

    if xa is None or ya is None:
        # x/y 列が無いと座標キーを 1 つも作れない。従来はここも無言で
        # 全行空欄になっていたので、理由を stats に残して呼び出し側から報告する。
        resolver = "no-xy"
    elif has_annotation:
        sample_arr = df["annotation"].astype(str).to_numpy()
        uniq = set(sample_arr.tolist())
        match_map = {
            s: (s if s in all_sample_set else match_sample_fn(s, all_sample_list))
            for s in uniq
        }
        unresolved = sorted(s for s, m in match_map.items() if m is None)
        if uniq and len(unresolved) == len(uniq) and stem_match is not None:
            # annotation が全滅 → ファイル名で引き直す（★ ver58.3）
            match_map = {s: stem_match for s in uniq}
            resolver = "stem-fallback"
        else:
            resolver = "annotation"
        for i, (s, xv, yv) in enumerate(zip(sample_arr, xa, ya)):
            m = match_map.get(s)
            if m is not None and xv == xv and yv == yv:  # xv==xv: 非NaN
                keys[i] = (m, round(float(xv), 4), round(float(yv), 4))
    else:
        if stem_match is None:
            resolver = "no-sample"
            unresolved = [str(stem)] if stem is not None else []
        else:
            resolver = "stem"
            for i, (xv, yv) in enumerate(zip(xa, ya)):
                if xv == xv and yv == yv:
                    keys[i] = (stem_match, round(float(xv), 4), round(float(yv), 4))

    keys_ser = pd.Series(keys, index=df.index, dtype=object)

    matched = 0
    for method_name in method_lookups.keys():
        col_name = method_name if is_multi else "UMAP cluster"
        mapped = keys_ser.map(method_lookups[method_name])
        # 手法ごとに lookup が違うため、最も当たった手法を代表値にする。
        matched = max(matched, int(mapped.notna().sum()))
        df[col_name] = mapped.fillna("").to_numpy()
    if region_lookup is not None:
        df["領域名"] = keys_ser.map(region_lookup).fillna("").to_numpy()

    if stats is not None:
        stats.update({
            "stem": stem,
            "rows": n_rows,
            "keyed": sum(1 for k in keys if k is not None),
            "matched": matched,
            "resolver": resolver,
            "unresolved_samples": unresolved[:5],
        })
    return df


# ---------------------------------------------------------------------------
# 突合結果の要約（Dash 非依存＝単体テスト可）
# ---------------------------------------------------------------------------

# resolver ごとの「なぜ当たらなかったか」。利用者が次に何を見ればよいかまで書く。
_RESOLVER_HINT = {
    "no-xy": "生データに x/y 列がありません（変換前の CSV を指していないか確認してください）",
    "no-sample": "ファイル名が解析のサンプル名と一致しません",
    "annotation": "annotation 列の値が解析のサンプル名と一致しません",
    "stem-fallback": "annotation が一致せずファイル名で引き直しましたが、座標が一致しません",
    "stem": "ファイル名は一致しましたが、座標が一致しません",
}


def summarize_coverage(report: "list | None") -> "str | None":
    """ファイル別の stats をまとめ、クラスタ列が埋まらなかったことを説明する文を返す。

    問題が無ければ None。

    ★ ver58.3: 従来 TIMS 側は突合が 1 件も成立しなくても戻り値・ログ・例外が一切なく、
      `✅ 生成しました` で終わっていた。出力を開いても「クラスタに属さない spot」と
      区別が付かないため、この症状は長く気づかれないまま残った。
      DESI 側の "Skipped" シート（ver52.5）は csv/parquet では使えないので、
      形式に依らず出せるステータス文へ載せる。
    """
    if not report:
        return None
    rows = sum(int(s.get("rows") or 0) for s in report)
    matched = sum(int(s.get("matched") or 0) for s in report)
    if rows == 0 or matched >= rows:
        return None

    # 空欄が出たファイルだけを、理由付きで名指しする。
    bad = [s for s in report if int(s.get("rows") or 0) > int(s.get("matched") or 0)]
    reasons: dict = {}
    for s in bad:
        hint = _RESOLVER_HINT.get(s.get("resolver"))
        if not hint:
            continue
        names = reasons.setdefault(hint, [])
        if s.get("stem") and s["stem"] not in names:
            names.append(str(s["stem"]))

    if matched == 0:
        head = f"⚠️ クラスタ列が全行空欄です（{rows:,} 行すべて）。"
    else:
        pct = 100.0 * (rows - matched) / rows
        head = (f"⚠️ クラスタ列の {rows - matched:,} 行 ({pct:.1f}%) が空欄です"
                f"（{rows:,} 行中）。")

    parts = [head]
    for hint, names in reasons.items():
        shown = ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
        parts.append(f"{hint}: {shown}" if shown else hint)
    # 未解決だった実際の値を出すと、利用者が自分で照合できる。
    seen: list = []
    for s in bad:
        for v in (s.get("unresolved_samples") or []):
            if v not in seen:
                seen.append(v)
    if seen:
        parts.append("一致しなかった値: " + ", ".join(repr(v) for v in seen[:5]))
    return " ".join(parts)
