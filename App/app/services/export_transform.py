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
    extra_lookups: "dict | None" = None,
) -> "pd.DataFrame":
    """df に手法別 UMAP クラスタ列（＋領域名列）をベクトル辞書引きで付与して返す。

    extra_lookups: `{列名: {(sample, rx, ry): 値}}`。plot_data 由来の追加列
        （UMAP 座標・品質指標）をクラスタと同じ突合結果に載せるために使う（ver61.0）。

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

    # ★ ver61.0: plot_data 由来の追加列（UMAP 座標・品質指標）。
    #   クラスタ・領域名と **同じ keys_ser** に載せる。別経路で突合し直すと、
    #   annotation → stem のフォールバックや ver58.4 の座標衝突判定を取りこぼし、
    #   「クラスタは切片 A なのに UMAP 座標は切片 B」という行が静かにできる。
    #   数値列なので未一致は空文字ではなく NaN のままにする（空文字を入れると
    #   列全体が object になり、Excel でも数値として扱われなくなる）。
    if extra_lookups:
        for col_name, lookup in extra_lookups.items():
            df[col_name] = keys_ser.map(lookup).to_numpy()

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
        # ★ ver59.0: この出力で実際に 1 行以上突合した解析サンプル名。
        #   「解析に入っていたはずのサンプルが、この出力のどの行とも突合していない」
        #   を検出するために要る（下の plan_exclusions 条件 B）。
        if sample_arr is not None:
            used = {match_map.get(g) for g, v in by_group.items() if v[0] > 0}
        elif resolver == "stem" and matched:
            # annotation 列が無いファイルは by_group が空。ここを数え忘れると
            # そういうファイルが 1 本混じるだけで除外が永久に発火しなくなる。
            used = {stem_match}
        else:
            used = set()
        stats.update({
            "stem": stem,
            "matched_samples": sorted(x for x in used if x),
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


def _classify_groups(report: "list | None") -> tuple:
    """report を annotation ごとに集計し ``(groups, full, absent)`` を返す。

    - groups: ``{annotation: [一致数, 行数]}``
    - full  : 全行一致した annotation の ``[(名前, 行数)]``
    - absent: 1 行も一致しなかった annotation の ``[(名前, 行数)]``

    ★ ver59.0: `summarize_coverage` と `unanalyzed_groups` が同じ分類を使うので、
      二重実装にならないようここへ寄せた。判定基準がずれると「メッセージでは
      解析対象外と言っているのに除外されない」といった食い違いが起きる。
    """
    groups: dict = {}
    for s in (report or []):
        for g, mr in (s.get("by_group") or {}).items():
            cur = groups.setdefault(g, [0, 0])
            cur[0] += int(mr[0])
            cur[1] += int(mr[1])
    full = [(g, v[1]) for g, v in groups.items() if v[1] > 0 and v[0] >= v[1]]
    absent = [(g, v[1]) for g, v in groups.items() if v[1] > 0 and v[0] == 0]
    return groups, full, absent


def matched_analysis_samples(report: "list | None") -> set:
    """この出力で実際に 1 行以上突合した解析サンプル名の集合。"""
    used: set = set()
    for s in (report or []):
        used.update(s.get("matched_samples") or [])
    return used


def plan_exclusions(report: "list | None",
                    all_samples: "list | tuple" = ()) -> tuple:
    """出力から外してよい annotation を決める。

    Returns ``(ファイルごとの除外リスト, 除外を見送らせた解析サンプル名)``。
    第 2 要素が非空なら第 1 要素は必ず全て空（安全側に倒したことを呼び出し側が言える）。

    ★ ver59.0: 判定は `summarize_coverage` の ℹ️ 分岐と**同じ根拠**を使う。
      片方だけ緩めると「メッセージでは解析対象外と言っているのに除外されない」
      （またはその逆）という食い違いが出る。

    採用する条件:
      - annotation が 2 種類以上ある
      - **少なくとも 1 つが全行一致**している（= 解析対象を絞っただけ、の署名）
      - 中途半端に当たった annotation が無い（あれば座標側の異常を疑う状況なので触らない）
      - 座標重複で意図的に空けた行が無い
      - **解析サンプルの取りこぼしがゼロ**（条件 B。下記）

    `resolver` は見ない。`by_group` は `"annotation"` / `"stem-fallback"` のときしか
    埋まらない（`"no-sample"` / `"stem"` / `"no-xy"` は空）ので、resolver で絞っても
    安全側にはならず、実運用で踏まれた `"stem-fallback"` の本命ケースを落とすだけ。

    ★ 条件 B が要る理由（これが無いと解析済みのデータを黙って消す）:
      切片 2 枚が **どちらも解析済み** で、plot_data の Sample が
      `['260816', '260817']` の 2 種類だったとする。ファイル名は片方にしか
      部分一致しないので `stem-fallback` で全行が `'260816'` に潰れ、
      切片 02 の行は 1 件も当たらない。このとき `by_group` は
      `{'01': (n, n), '02': (0, m)}` となり、**「1 枚だけ解析した」本命ケースと
      見分けが付かない**。`'260817'` がどの行とも突合していないことだけが違いなので、
      そこを見て除外を止める。annotation の打ち間違い（`'SecX'` ↔ `'Sec_X'`）で
      ver52.5 が可視化した照合ミスを消してしまう事故も同じ条件で防げる。
    """
    n = len(report or [])
    none: tuple = ([[] for _ in range(n)], [])
    if not report:
        return none
    if any(int(s.get("ambiguous") or 0) for s in report):
        return none
    groups, full, absent = _classify_groups(report)
    if len(groups) < 2 or not full or not absent:
        return none
    if any(0 < v[0] < v[1] for v in groups.values()):
        return none
    used = matched_analysis_samples(report)
    blocked = sorted(str(x) for x in (all_samples or ()) if str(x) not in used)
    if blocked:
        return ([[] for _ in range(n)], blocked)
    drop = {g for g, _ in absent}
    # 全体で「全行一致した annotation」が必ず残るので、出力が空になることはない。
    per_file = [sorted(set((s.get("by_group") or {}).keys()) & drop) for s in report]
    return per_file, []


def unanalyzed_stems(matched_by_stem: dict,
                     all_samples: "list | tuple" = ()) -> tuple:
    """DESI 用: 出力しなくてよい stem（= .txt / シート）を決める。

    Returns ``(除外してよい stem, 除外を見送らせた解析サンプル名)``。
    判定材料は名前解決の結果だけ（DESI は 1 ファイル = 1 サンプル）。

    TIMS の `plan_exclusions` と**同じ思想**:
      - 少なくとも 1 つの stem が解決していること
      - 解析サンプルの取りこぼしがゼロであること（条件 B）
      - 全部を除外することにはならない

    ★ ver59.0 / 条件 B の DESI 版が要る理由:
      `WT_liver_01.txt` / `WT_liver_02.txt` / `wt_liver_03.txt`（3 本目だけ
      大文字小文字違い）で 3 本とも解析済み、という状況では、1・2 が解決するので
      「1 つは解決している」だけでは通ってしまい、3 本目が
      「解析に使っていない」という**事実と異なる理由**で消える。
      ver52.5 が「解析のサンプル名と一致せず」と正しく報告していたものが
      嘘に置き換わるので、解析サンプルの取りこぼしを見て止める。
    """
    if not matched_by_stem:
        return [], []
    used = {v for v in matched_by_stem.values() if v is not None}
    if not used:
        # 1 つも解決しない = 解析対象を絞ったのではなく、フォルダ違い /
        # 大文字小文字違い / 区切り違いの可能性が高い。触らない。
        return [], []
    blocked = sorted(str(x) for x in (all_samples or ()) if str(x) not in used)
    if blocked:
        return [], blocked
    cand = sorted(k for k, v in matched_by_stem.items() if v is None)
    if len(cand) >= len(matched_by_stem):
        return [], []
    return cand, []


def unanalyzed_groups(stats: "dict | None",
                      all_samples: "list | tuple" = ()) -> list:
    """1 ファイル分の「解析に使っていない」annotation 名（`plan_exclusions` の 1 件版）。"""
    if not stats:
        return []
    per_file, blocked = plan_exclusions([stats], all_samples)
    return [] if blocked else per_file[0]


def summarize_exclusions(report: "list | None",
                        blocked_samples: "list | tuple" = ()) -> "str | None":
    """除外した annotation / サンプルを伝える文を返す。無ければ None。

    ★ ver59.0: 除外した行は「空欄」ではなくなるので `summarize_coverage` の
      対象から外れる。黙って行が消えたように見えないよう、必ずこちらで言う。
    """
    excluded: dict = {}
    kept = 0
    for s in (report or []):
        for g, n in (s.get("excluded") or {}).items():
            excluded[g] = excluded.get(g, 0) + int(n)
        kept += int(s.get("rows") or 0)
    if blocked_samples:
        # ★ ver59.0: 除外を見送ったときこそ最も重要な報告。除外候補が実在した
        #   ときだけ出す（毎回出すとただのノイズになる）。
        names = ", ".join(repr(str(x)) for x in list(blocked_samples)[:3])
        more = " など" if len(blocked_samples) > 3 else ""
        return (f"⚠️ 解析に含めた {names}{more} に対応する行が生データに"
                "見つかりませんでした。切片の取り違えを避けるため、除外は行って"
                "いません（クラスタ列が空欄の行もそのまま出力しています）。"
                "annotation の値とサンプル名の対応をご確認ください。")
    if not excluded:
        return None
    total = sum(excluded.values())
    # 1 件のときは行数が二度出て冗長になるので「計 N 行」を付けない。
    tail = f"（計 {total:,} 行）" if len(excluded) > 1 else ""
    return (f"ℹ️ UMAP 解析に使っていない切片を出力から除外しました: "
            f"{_fmt_groups(sorted(excluded.items()))}{tail}。"
            f"出力は {kept:,} 行です。"
            f"全て出すには「解析に使っていない切片を除外」のチェックを外してください。")


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
    groups, full, absent = _classify_groups(report)
    absent_rows = sum(n for _, n in absent)

    # 「まるごと当たった切片」と「1 行も当たらなかった切片」だけで欠損が説明でき、
    # かつ実際に当たった切片がある = 解析対象を絞っただけ。警告ではなく事実を伝える。
    # 取り違え回避で空けた行がある場合は「絞っただけ」ではないので、この道に入れない。
    # ★ ver59.0: 解析サンプルの取りこぼしが見つかっている（= 除外を見送った）ときは、
    #   「解析に含めなかった annotation」と断言できない。直上の ⚠️ と矛盾するので黙る。
    if (matched > 0 and full and absent and absent_rows == missing
            and not any(int(s.get("ambiguous") or 0) for s in report)
            and not any(s.get("blocked_samples") for s in report)):
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
    # 除外を見送った（= 取りこぼしがある）ときは「解析に含めなかった」と断言しない。
    if (matched > 0 and full and absent
            and not any(s.get("blocked_samples") for s in report)):
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
