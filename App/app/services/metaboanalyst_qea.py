# =============================================================================
# MSI Analysis Application - MetaboAnalyst QEA export builder
#
# ④出力の region×cluster 平均強度行列（行=Group, 列=化合物）を、MetaboAnalyst の
# Quantitative Enrichment Analysis (QEA) に「そのまま投入できる」濃度表 CSV へ変換する。
#
# MetaboAnalyst 濃度表形式（samples in rows）:
#   1列目 = Sample（英数字の一意ID）, 2列目 = Class（表現型ラベル）, 以降 = 化合物列
#
# 重要（科学的前提）: 各行は「1切片(stage)×臓器×クラスタ」の spot 平均＝擬似バルク
#   （1個体運用）。同一 Class 内の反復は生物学的反復ではなく空間的擬似反復であり、
#   結果は探索的（exploratory）。README/ファイル名で明記する。
# =============================================================================

import logging
import re

import numpy as np
import pandas as pd

logger = logging.getLogger("msi.metaboanalyst_qea")

# `{stage}_{anatomy}_cluster{c}` の末尾 cluster を分離（anatomy に空白/スラッシュ可、
# 末尾の "_cluster<値>" を優先マッチ）。cluster 値はアンダースコアを含まない前提。
_CLUSTER_RE = re.compile(r"_cluster([^_]+)$")
_WS_RE = re.compile(r"\s+")


def parse_group(group):
    """`E14_Brain_cluster1` → ("E14", "Brain", "cluster1")。

    分割規約は build_groups_table / build_region_cluster_export と同一
    (`{stage}_{anatomy}_cluster{c}`)。末尾 `_cluster<c>` を剥がし、残りを先頭 `_` で
    stage / anatomy に分ける（stage にアンダースコア無しを前提）。崩れた場合は
    可能な範囲で埋め、空文字を返す（呼び出し側で警告）。
    """
    g = str(group).strip()
    cluster = ""
    core = g
    m = _CLUSTER_RE.search(g)
    if m:
        cluster = "cluster" + m.group(1)
        core = g[:m.start()]
    if "_" in core:
        stage, anatomy = core.split("_", 1)
    else:
        stage, anatomy = core, ""
    return stage.strip(), anatomy.strip(), cluster.strip()


# --- RefMet/LIPID MAPS 準拠の脂質名（保守的正規化） --------------------------
# アプリの脂質略記（例 `PG 32:1`, `Cer 28:4;O4`, `PE P-42:9`, `LPI O-14:0`）は
# 既に RefMet/LIPID MAPS の shorthand 形式に近い。よって破壊的な書き換えはせず、
# 空白/句読点の正規化と、確信のある少数の別名だけを整える（不明は原文保持）。
# 実際の照合は MetaboAnalyst の smart-matching に委ねる。
#
# 別名表（アプリ表記 → RefMet 表記）。確信できるもののみ。空でも動作する。
_CLASS_ALIASES = {
    # 現状は破壊的変換を避け空。将来 name-check 結果に基づき追加する。
}


def normalize_lipid_name(name, lipid_class):
    """脂質名を RefMet 照合しやすい形へ保守的に正規化して返す。

    lipid_class を持つ（＝脂質）行のみ対象。空白の畳み込みと `:` `;` 前後の空白除去、
    クラス別名の置換のみ行う。非脂質・確信の持てない名は原文をそのまま返す。
    Returns: (normalized_name, changed: bool)
    """
    orig = str(name)
    if not lipid_class or not str(lipid_class).strip():
        return orig, False
    s = _WS_RE.sub(" ", orig.strip())
    s = re.sub(r"\s*:\s*", ":", s)      # "32 : 1" → "32:1"
    s = re.sub(r"\s*;\s*", ";", s)      # "28:4 ; O4" → "28:4;O4"
    # 先頭のクラストークン別名を置換（`PG 32:1` の "PG" 部分のみ）
    parts = s.split(" ", 1)
    if len(parts) == 2 and parts[0] in _CLASS_ALIASES:
        s = _CLASS_ALIASES[parts[0]] + " " + parts[1]
    return s, (s != orig)


def _compound_lipidclass_map(fmap):
    """feature_map（DataFrame）から {compound: lipid_class} を作る（代表行優先）。"""
    if fmap is None or getattr(fmap, "empty", True):
        return {}
    if "compound" not in fmap.columns or "lipid_class" not in fmap.columns:
        return {}
    out = {}
    for _, r in fmap.iterrows():
        comp = str(r.get("compound") or "").strip()
        if not comp:
            continue
        lc = r.get("lipid_class")
        lc = "" if lc is None or (isinstance(lc, float) and pd.isna(lc)) else str(lc).strip()
        # 代表行(is_representative=True)を優先。未設定なら最初の値を採用。
        is_rep = str(r.get("is_representative", "")).strip().lower() in ("true", "1")
        if comp not in out or (is_rep and lc):
            out[comp] = lc
    return out


def _build_column_rename(compound_cols, lipid_class_map):
    """化合物列名の RefMet 正規化 rename と原文対応表を返す。

    正規化後に列名が衝突する場合は、衝突した列を原文へ戻して一意性を保つ
    （MetaboAnalyst は feature 名の一意性を要求）。
    Returns: (rename_map {orig: new}, name_rows [{original, normalized, lipid_class, changed}])
    """
    proposed = {}
    rows = []
    for c in compound_cols:
        lc = lipid_class_map.get(c, "")
        norm, changed = normalize_lipid_name(c, lc)
        proposed[c] = norm
        rows.append({"original": c, "normalized": norm,
                     "lipid_class": lc, "changed": bool(changed)})
    # 衝突検出: 正規化後の名前が複数の原文に割り当たる or 既存の別原文と衝突
    from collections import Counter
    norm_counts = Counter(proposed.values())
    orig_set = set(compound_cols)
    rename_map = {}
    for c in compound_cols:
        nm = proposed[c]
        collide = (nm != c) and (norm_counts[nm] > 1 or nm in orig_set)
        rename_map[c] = c if collide else nm
    # 対応表にも最終採用を反映
    final = {r["original"]: rename_map[r["original"]] for r in rows}
    for r in rows:
        r["normalized"] = final[r["original"]]
        r["changed"] = bool(final[r["original"]] != r["original"])
    return rename_map, rows


def _to_csv(df):
    """MetaboAnalyst 投入用 CSV 文字列（NaN は空欄＝欠測、カンマ含む名は自動引用）。"""
    return df.to_csv(index=False)


def build_qea_bundle(matrix_df, fmap=None, group_col="Group", cluster_min=10,
                     file_prefix="exploratory_QEA"):
    """QEA 用ファイル群を {相対ファイル名: テキスト} で返す。

    matrix_df: 先頭列 group_col（例 `E14_Brain_cluster1`）＋以降 化合物列（値=平均強度）。
    fmap: feature_map（lipid_class 参照用・任意）。
    cluster_min: cluster_ge<N> 版で残す最小サンプル(行)数。
    """
    if (matrix_df is None or getattr(matrix_df, "empty", True)
            or group_col not in matrix_df.columns):
        return {}

    df = matrix_df.copy()
    groups = df[group_col].astype(str).tolist()
    parsed = [parse_group(g) for g in groups]
    stages = [p[0] for p in parsed]
    anatomies = [p[1] for p in parsed]
    clusters = [p[2] for p in parsed]

    n_bad = sum(1 for s, a, c in parsed if not s or not c)
    if n_bad:
        logger.warning("QEA: Group 分解に失敗した行が %d 件（原文を metadata に保持）", n_bad)

    # Sample ID（S0001…）。Group は一意想定だが重複時も安定に採番。
    sample_ids, seen = [], {}
    for g in groups:
        if g not in seen:
            seen[g] = f"S{len(seen) + 1:04d}"
        sample_ids.append(seen[g])

    compound_cols = [c for c in df.columns if c != group_col]

    # 化合物名の RefMet 保守正規化
    lc_map = _compound_lipidclass_map(fmap)
    rename_map, name_rows = _build_column_rename(compound_cols, lc_map)
    norm_cols = [rename_map[c] for c in compound_cols]

    # 数値ブロック（列=正規化後化合物名）
    values = df[compound_cols].apply(pd.to_numeric, errors="coerce")
    values.columns = norm_cols

    base = pd.DataFrame({"Sample": sample_ids})
    meta = pd.DataFrame({
        "sample_id": sample_ids,
        "original_group": groups,
        "stage": stages,
        "anatomy_original": anatomies,
        "umap_cluster": clusters,
    })

    files = {}

    def _emit(name, class_vals, keep_mask=None):
        """1 class 種別について raw / zeroAsNA の 2 CSV を files へ追加。"""
        tbl = pd.concat(
            [base.assign(Class=class_vals).reset_index(drop=True),
             values.reset_index(drop=True)], axis=1)
        # 列順: Sample, Class, <compounds>
        tbl = tbl[["Sample", "Class"] + norm_cols]
        if keep_mask is not None:
            tbl = tbl[keep_mask.to_numpy()].reset_index(drop=True)
        # Class 空欄行は除外（QEA に投入不可）
        tbl = tbl[tbl["Class"].astype(str).str.len() > 0].reset_index(drop=True)
        if tbl.empty or tbl["Class"].nunique() < 2:
            logger.warning("QEA: %s はクラスが1種以下のためスキップ", name)
            return
        files[f"{file_prefix}_{name}_raw.csv"] = _to_csv(tbl)
        na = tbl.copy()
        na[norm_cols] = na[norm_cols].replace(0, np.nan)
        files[f"{file_prefix}_{name}_zeroAsNA.csv"] = _to_csv(na)

    # stage / anatomy / cluster(all)
    _emit("stage", pd.Series(stages))
    _emit("anatomy", pd.Series(anatomies))
    _emit("cluster_all", pd.Series(clusters))

    # cluster_ge<N>: 行数が cluster_min 未満のクラスタを除外
    cl_ser = pd.Series(clusters)
    counts = cl_ser[cl_ser.str.len() > 0].value_counts()
    keep_clusters = set(counts[counts >= int(cluster_min)].index)
    keep_mask = cl_ser.isin(keep_clusters)
    dropped = sorted(set(counts.index) - keep_clusters,
                     key=lambda x: (len(x), x))
    _emit(f"cluster_ge{int(cluster_min)}", cl_ser, keep_mask=keep_mask)

    # metadata / 対応表 / README
    files["sample_metadata.csv"] = _to_csv(meta)
    files["compound_name_map.csv"] = _to_csv(pd.DataFrame(
        name_rows, columns=["original", "normalized", "lipid_class", "changed"]))
    files["README.txt"] = _build_readme(
        n_samples=len(groups), n_compounds=len(compound_cols),
        stages=stages, anatomies=anatomies, clusters=clusters,
        cluster_min=int(cluster_min), dropped_clusters=dropped,
        n_renamed=sum(1 for r in name_rows if r["changed"]))
    return files


def _build_readme(n_samples, n_compounds, stages, anatomies, clusters,
                  cluster_min, dropped_clusters, n_renamed):
    def _counts(vals):
        s = pd.Series([v for v in vals if v])
        return ", ".join(f"{k}={v}" for k, v in s.value_counts().items())

    zero_note = ""
    return (
        "MetaboAnalyst Quantitative Enrichment Analysis (QEA) 用エクスポート\n"
        "==============================================================\n\n"
        "【重要・探索的解析である旨】\n"
        "各行(Sample)は『1切片(stage)×臓器×UMAPクラスタ』の spot 平均＝擬似バルクです。\n"
        "運用は1個体ずつのため、同一 Class 内の反復は生物学的反復ではなく空間的な擬似反復\n"
        "です。したがって QEA の p 値は個体間差ではなく切片内の空間ばらつきを反映し、結果は\n"
        "探索的（exploratory）です。厳密な統計的推論には用いないでください。\n\n"
        "【投入方法】MetaboAnalyst > Enrichment Analysis > 濃度表(samples in rows)。\n"
        "  各 exploratory_QEA_<class>_*.csv は 1列目=Sample, 2列目=Class, 以降=化合物 です。\n"
        "  そのままアップロードできます（0→NA 版は 0 を欠測として MetaboAnalyst に委ねる版）。\n\n"
        "【ファイル】\n"
        "  exploratory_QEA_stage_{raw,zeroAsNA}.csv     : Class = 発生ステージ\n"
        "  exploratory_QEA_anatomy_{raw,zeroAsNA}.csv   : Class = 臓器(原ラベル)\n"
        "  exploratory_QEA_cluster_all_{raw,zeroAsNA}.csv        : Class = UMAP cluster(全)\n"
        f"  exploratory_QEA_cluster_ge{cluster_min}_{{raw,zeroAsNA}}.csv : Class = UMAP cluster(行数≥{cluster_min})\n"
        "  sample_metadata.csv     : Sample↔原Group と stage/anatomy/cluster の対応\n"
        "  compound_name_map.csv   : 化合物名の原文↔RefMet正規化後(脂質のみ・保守的)\n\n"
        f"【概要】Sample 数={n_samples} / 化合物列={n_compounds} / 名称正規化された脂質={n_renamed}\n"
        f"  stage 別 n: {_counts(stages)}\n"
        f"  anatomy 別 n: {_counts(anatomies)}\n"
        f"  cluster 別 n: {_counts(clusters)}\n"
        f"  cluster_ge{cluster_min} で除外: {', '.join(dropped_clusters) if dropped_clusters else '(なし)'}\n\n"
        "【注意】\n"
        "  - anatomy は原ラベルです。Brain/CNS 等の表記揺れは生物学的標準化表が別途必要です。\n"
        "  - 化合物名の照合は MetaboAnalyst の name-check（RefMet smart-match）に依存します。\n"
        "    脂質略記は概ね RefMet 形式ですが、薬剤/外因性名や切詰め名(…)は未マッピングになり得ます。\n"
        + zero_note
    )
