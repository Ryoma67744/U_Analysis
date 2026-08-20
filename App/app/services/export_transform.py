# =============================================================================
# MSI Analysis Application - Data export transform (Dash 非依存・単体テスト可)
#
# インタラクティブ「データ出力」で、元データ(1行=1スポット)に UMAP クラスタ列と
# 領域名(ROI)列を「右端に」付与する純粋関数。全 m/z 列を1行ずつ箱詰めする iterrows を
# 使わず、x/y/annotation の3列だけをベクトル処理する（本来の軽さに戻すための中核）。
# =============================================================================

import pandas as pd


def _cross_group_collisions(group_arr, xa, ya) -> set:
    """異なる group（annotation）が共有している (x,y) の集合を返す。

    ★ ver58.4: ファイル名フォールバックは全 annotation を 1 つのサンプル名へ潰すため、
      切片間で座標が重複していると「どちらの切片の spot か」を決められない。
      そこに値を入れると、**空欄より悪い「黙って間違ったクラスタ番号」**になる
      （ver51.8 が曖昧なサンプル名一致を禁じたのと同じ事故）。
      重複した座標だけを除ければ、決められる行はそのまま埋められる。
      丸め方はキー生成と同一（round(...,4)）にしないと判定が食い違う。
    """
    seen: dict = {}
    bad: set = set()
    for g, xv, yv in zip(group_arr, xa, ya):
        if xv != xv or yv != yv:      # NaN はキーを作らないので無関係
            continue
        k = (round(float(xv), 4), round(float(yv), 4))
        prev = seen.get(k)
        if prev is None:
            seen[k] = g
        elif prev != g:
            bad.add(k)
    return bad


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
    # フォールバックで切片を潰したときに「どちらの切片か決められない」行の数と座標。
    ambiguous_coords: set = set()
    n_ambiguous = 0
    # annotation 列を実際に読めたときだけ入る（x/y が無ければ読まずに終わる）。
    sample_arr = None

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
            #
            # ★ ver58.4: ただし引き直すと **全 annotation が 1 つのサンプル名に潰れる**。
            #   切片 A と切片 B が同じ (x,y) を持っていると、B の行に A のクラスタ番号が
            #   入る — 空欄より悪い「黙って間違った値」になる（ver51.8 が曖昧一致を
            #   禁じたのと同じ事故）。潰す前に切片間で座標が重複しないか確かめ、
            #   重複するなら引き直さない（空欄のままにして理由を報告する）。
            if len(uniq) > 1:
                ambiguous_coords = _cross_group_collisions(sample_arr, xa, ya)
            match_map = {s: stem_match for s in uniq}
            resolver = "stem-fallback"
        else:
            resolver = "annotation"
        for i, (s, xv, yv) in enumerate(zip(sample_arr, xa, ya)):
            m = match_map.get(s)
            if m is not None and xv == xv and yv == yv:  # xv==xv: 非NaN
                rx, ry = round(float(xv), 4), round(float(yv), 4)
                if (rx, ry) in ambiguous_coords:
                    # どの切片の spot か決められない → 埋めない（★ ver58.4）
                    n_ambiguous += 1
                    continue
                keys[i] = (m, rx, ry)
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
    best_hit = None
    for method_name in method_lookups.keys():
        col_name = method_name if is_multi else "UMAP cluster"
        mapped = keys_ser.map(method_lookups[method_name])
        # 手法ごとに lookup が違うため、最も当たった手法を代表値にする。
        n_hit = int(mapped.notna().sum())
        if n_hit > matched or best_hit is None:
            matched, best_hit = n_hit, mapped.notna().to_numpy()
        df[col_name] = mapped.fillna("").to_numpy()
    if region_lookup is not None:
        df["領域名"] = keys_ser.map(region_lookup).fillna("").to_numpy()

    if stats is not None:
        # ★ ver58.4: annotation ごとの一致数を残す。
        #   「切片 01 は全部埋まった / 切片 02 は 1 行も埋まらなかった」は
        #   **解析に 02 を入れていない**という意味で、不具合ではない。
        #   総数だけを見せると両者を区別できず、正常な出力を不具合に見せてしまう。
        by_group: dict = {}
        if sample_arr is not None and best_hit is not None:
            for g, ok in zip(sample_arr, best_hit):
                cur = by_group.setdefault(str(g), [0, 0])
                cur[1] += 1
                if ok:
                    cur[0] += 1
        stats.update({
            "stem": stem,
            "rows": n_rows,
            "keyed": sum(1 for k in keys if k is not None),
            "matched": matched,
            "resolver": resolver,
            "unresolved_samples": unresolved[:5],
            "by_group": {k: tuple(v) for k, v in by_group.items()},
            "ambiguous": n_ambiguous,
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


def _fmt_groups(items) -> str:
    """[(名前, 行数)] を `'01' (110,031 行)` の形に整える（先頭 3 件まで）。"""
    shown = ", ".join(f"{g!r} ({n:,} 行)" for g, n in items[:3])
    return shown + (" …" if len(items) > 3 else "")


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
    missing = rows - matched
    pct = 100.0 * missing / rows

    # ★ ver58.4: annotation ごとに集計して「解析に含めなかった切片」を切り分ける。
    #   切片 2 枚のデータで 1 枚だけ UMAP を掛けた場合、もう 1 枚が空欄になるのは
    #   **正しい出力**。それを「⚠️ 座標が一致しません」と言うと不具合に見えるうえ、
    #   本物の不一致（＝直すべきもの）と区別が付かなくなる。
    groups: dict = {}
    for s in report:
        for g, mr in (s.get("by_group") or {}).items():
            cur = groups.setdefault(g, [0, 0])
            cur[0] += int(mr[0])
            cur[1] += int(mr[1])
    full = [(g, v[1]) for g, v in groups.items() if v[1] > 0 and v[0] >= v[1]]
    absent = [(g, v[1]) for g, v in groups.items() if v[1] > 0 and v[0] == 0]
    absent_rows = sum(n for _, n in absent)

    # 「まるごと当たった切片」と「1 行も当たらなかった切片」だけで欠損が説明でき、
    # かつ実際に当たった切片がある = 解析対象を絞っただけ。警告ではなく事実を伝える。
    # 取り違え回避で空けた行がある場合は「絞っただけ」ではないので、この道に入れない。
    if (matched > 0 and full and absent and absent_rows == missing
            and not any(int(s.get("ambiguous") or 0) for s in report)):
        return (f"ℹ️ クラスタ列の {missing:,} 行 ({pct:.1f}%) が空欄です"
                f"（{rows:,} 行中）。これは解析に含めなかった annotation の spot です: "
                f"{_fmt_groups(sorted(absent))}。"
                f"解析に含めた分は全て埋まっています: {_fmt_groups(sorted(full))}。")

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
        head = (f"⚠️ クラスタ列の {missing:,} 行 ({pct:.1f}%) が空欄です"
                f"（{rows:,} 行中）。")

    parts = [head]
    # 一部の切片だけが解析対象だった分は、先に事実として切り出しておく
    # （残りが「本当に調べるべき不一致」だと分かるようにするため）。
    if matched > 0 and full and absent:
        parts.append(f"うち {absent_rows:,} 行は解析に含めなかった annotation です: "
                     f"{_fmt_groups(sorted(absent))}。")
    # 取り違えを避けて意図的に空けた行は、原因が別なので必ず分けて言う。
    n_amb = sum(int(s.get("ambiguous") or 0) for s in report)
    if n_amb:
        parts.append(
            f"うち {n_amb:,} 行は切片間で座標が重複しており、どの切片の spot か"
            "決められないため空欄にしました（変換時に領域アノテーション CSV を"
            "指定して解析し直すと解消します）。")
    # 上の 2 行で欠損が説明しきれているなら、汎用の理由は足さない。
    # 「座標が一致しません」は原因が分かっている行にとっては誤誘導になる。
    if absent_rows + n_amb < missing:
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
