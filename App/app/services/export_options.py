"""データ出力の「出力内容の設定」（列カテゴリ選択 / 集計単位）。

Dash に依存させないのは単体テストのためで、`export_transform.py` /
`export_aggregate.py` と同じ方針。

---

## 設計の要点 1: 「読む列」と「出す列」は別物

利用者が「空間座標は要らない」と言っても、`x` / `y` を parquet から読まないわけには
いかない。クラスタ列と領域名列の突合が `(sample, round(x,4), round(y,4))` をキーに
しているため（`export_transform.append_cluster_region_columns`）、x/y を落とすと
**クラスタが 1 件も付かない出力が「成功」として出てしまう**。
`annotation` も同様で、サンプル名の解決と「解析に使っていない切片の除外」
(`plan_exclusions`) が使う。

そこで **読む列 = 出す列 ∪ 結合に必要な列** とし、不要な列は全部済んでから落とす。

## 設計の要点 2: 強度列だけは「読まない」ことに意味がある

逆に m/z 列は結合に使わないので、要らなければ**読まなければよい**。
実データ規模（203,078 spot × 4,566 m/z）では float32 の実体だけで 3.7GB あり、
`_export_tims` はそこから `pd.concat` → `to_csv()` → `.encode()` と複製するため
ピークは実体の 4〜5 倍になる。強度を外したときに読み込み段で落とせば、
この経路のメモリと時間が桁で減る。読んでから捨てるのでは意味がない。

## 設計の要点 3: 未設定は従来どおり

`options=None`（モーダルを一度も開いていない）なら、列も集計も従来と完全に同じ。
UMAP 座標と品質指標は既定 OFF にしてある。既定 ON にすると、これまでの出力を
前提にしているスクリプトの列位置がずれる。
"""

from __future__ import annotations

# --- 列カテゴリ ---------------------------------------------------------
# key: 内部識別子 / label: UI 表示 / desc: UI の補足
CATEGORIES = (
    ("id",        "識別子 (id)",            "スポット連番"),
    ("coords",    "空間座標 (x, y)",        "MSI の測定座標"),
    ("intensity", "強度 (m/z 全列)",        "数百〜数千列。外すと出力が桁で小さくなる"),
    ("section",   "切片 (annotation)",      "SCiLS 由来の切片ラベル"),
    ("umap",      "UMAP 座標",              "UMAP_1 / UMAP_2（既定 OFF）"),
    ("quality",   "品質指標",               "TotalCount / nFeature（既定 OFF・ある場合のみ）"),
    ("cluster",   "クラスタ",               "手法別の UMAP クラスタ番号"),
    ("roi",       "領域名 (ROI)",           "H&E オーバーレイで割り当てた領域"),
    # ★ ver62.0: 強度とは独立した選択肢。1 行 = 1 m/z の別表になる。
    #   「どの m/z が入っているか知りたいだけ」なのに、強度を出すと
    #   4,566 m/z × 203,078 spot で数 GB になってしまうため。
    ("mzlist",    "m/z 一覧",               "1 行 = 1 m/z の別表（化合物名・アダクト付き・既定 OFF）"),
)
CATEGORY_KEYS = tuple(k for k, _, _ in CATEGORIES)

# 従来の出力に含まれていたカテゴリ。`options=None` のときの既定。
LEGACY_CATEGORIES = ("id", "coords", "intensity", "section", "cluster", "roi")

# 「1 行 = 1 スポット」の表に列として現れるカテゴリ。
# `mzlist` だけは行の単位が違う（1 行 = 1 m/z）ので別扱いになる。
# csv / parquet は 1 ファイルに 1 表しか持てないため、この区別が要る。
SPOT_CATEGORIES = tuple(k for k in CATEGORY_KEYS if k != "mzlist")

# parquet 側の非強度列。ここに無い列を強度(m/z)列とみなす。
# `interactive_data_export._apply_feature_annotation_columns` と同じ集合。
META_COLUMNS = ("id", "x", "y", "annotation")

# 突合・除外判定に必要で、出力に出さなくても読まなければならない列。
REQUIRED_FOR_JOIN = ("x", "y", "annotation")

# plot_data から引く列（parquet には無い）。
UMAP_COLUMNS = ("UMAP_1", "UMAP_2")
QUALITY_COLUMNS = ("TotalCount", "nFeature")

# --- 集計 ---------------------------------------------------------------
MODE_PIXEL = "pixel"
MODE_GROUP = "group"

# 集計キーの選択肢。値は「その時点の DataFrame での列名」を解決する手掛かり。
GROUP_KEYS = (
    ("section", "切片 (annotation)"),
    ("roi",     "領域名 (ROI)"),
    ("cluster", "クラスタ"),
)
GROUP_KEY_KEYS = tuple(k for k, _ in GROUP_KEYS)

REGION_COLUMN = "領域名"
SINGLE_METHOD_CLUSTER_COLUMN = "UMAP cluster"


def normalize(options) -> dict:
    """UI の Store 値を扱いやすい dict に正規化する。

    None / 空 / 壊れた値はすべて「従来どおり」に倒す。設定 UI の不具合で
    出力が静かに変わるより、既定に戻る方が安全。
    """
    if not isinstance(options, dict):
        return {"categories": set(LEGACY_CATEGORIES), "mode": MODE_PIXEL,
                "group_keys": [], "is_default": True}

    cats = options.get("categories")
    if not isinstance(cats, (list, tuple, set)) or not cats:
        cats = LEGACY_CATEGORIES
    cats = {c for c in cats if c in CATEGORY_KEYS}
    if not cats:
        cats = set(LEGACY_CATEGORIES)

    mode = options.get("mode")
    if mode not in (MODE_PIXEL, MODE_GROUP):
        mode = MODE_PIXEL

    gkeys = options.get("group_keys")
    if not isinstance(gkeys, (list, tuple)):
        gkeys = []
    gkeys = [k for k in GROUP_KEY_KEYS if k in gkeys]   # 表示順に揃える

    is_default = (set(cats) == set(LEGACY_CATEGORIES) and mode == MODE_PIXEL)
    return {"categories": set(cats), "mode": mode, "group_keys": gkeys,
            "is_default": is_default}


def parquet_columns(available: list, options) -> "list | None":
    """parquet から読む列を返す。None は「全列読む」（従来どおり）。

    available: そのファイルが実際に持つ列名。
    """
    opt = normalize(options)
    # 強度を出すなら結局ほぼ全列が要る。列リストを渡さない方が pyarrow も素直。
    if "intensity" in opt["categories"]:
        return None

    keep: list = []
    for col in available:
        if col not in META_COLUMNS:
            continue                                  # 強度列は読まない
        if col in REQUIRED_FOR_JOIN:
            keep.append(col)                          # 出力に出さなくても必要
        elif col == "id" and "id" in opt["categories"]:
            keep.append(col)
    # 元の並び順を保つ（読み込み後の列順が従来と変わらないように）
    return [c for c in available if c in set(keep)]


def intensity_columns(df_columns, cluster_columns=()) -> list:
    """DataFrame の列から強度(m/z)列を抜き出す。

    cluster_columns を**必ず呼び出し側から渡す**。クラスタ列の名前は単一手法なら
    "UMAP cluster" だが、複数手法では手法名そのもの（"RPCA" / "Harmony" / "PCA" …）
    になり、任意の文字列を取り得る。固定リストで判別しようとすると手法名を
    強度列と誤認し、**クラスタ番号を平均しようとして壊れる**（テストで検出済み）。
    呼び出し側は `method_lookups.keys()` を持っているので推測する必要がない。
    """
    added = (set(META_COLUMNS) | set(UMAP_COLUMNS) | set(QUALITY_COLUMNS)
             | {REGION_COLUMN, SINGLE_METHOD_CLUSTER_COLUMN}
             | set(cluster_columns))
    return [c for c in df_columns if c not in added]


def wants(options, category: str) -> bool:
    """カテゴリが選択されているか。"""
    return category in normalize(options)["categories"]


def is_group_mode(options) -> bool:
    return normalize(options)["mode"] == MODE_GROUP


def wants_mzlist(options) -> bool:
    """m/z 一覧（1 行 = 1 m/z の別表）を出すか。"""
    return "mzlist" in normalize(options)["categories"]


def wants_spot_table(options) -> bool:
    """1 行 = 1 スポットの表を出すか（＝スポット単位の項目が 1 つでも選ばれているか）。

    m/z 一覧だけを選んだ場合は False。このとき出力は m/z 一覧そのものになる。
    """
    return bool(normalize(options)["categories"] & set(SPOT_CATEGORIES))


def resolve_group_columns(options, cluster_columns: list) -> list:
    """集計キー（内部識別子）を、実際の DataFrame の列名へ解決する。

    cluster_columns: その出力に存在するクラスタ列名のリスト。
        複数手法のときは手法ごとに列があるため、呼び出し側が手法ごとに
        1 列だけ渡す（縦持ちにして Method 列で区別するため）。
    """
    opt = normalize(options)
    if opt["mode"] != MODE_GROUP:
        return []
    cols: list = []
    for key in opt["group_keys"]:
        if key == "section":
            cols.append("annotation")
        elif key == "roi":
            cols.append(REGION_COLUMN)
        elif key == "cluster":
            cols.extend(cluster_columns)
    return cols


def select_output_columns(df_columns: list, options,
                          cluster_columns: list) -> list:
    """最終的に出力へ残す列を、元の並び順を保って返す。

    並びは従来の `id, x, y, <m/z>, annotation` を崩さず、新カテゴリ
    （UMAP 座標・品質指標）はその後ろ、クラスタ・領域名の前に置く。
    既存の出力を列位置で読んでいる利用者を壊さないため。
    """
    opt = normalize(options)
    cats = opt["categories"]
    cluster_set = set(cluster_columns)
    keep: list = []
    for col in df_columns:
        if col == "id":
            ok = "id" in cats
        elif col in ("x", "y"):
            ok = "coords" in cats
        elif col == "annotation":
            ok = "section" in cats
        elif col in UMAP_COLUMNS:
            ok = "umap" in cats
        elif col in QUALITY_COLUMNS:
            ok = "quality" in cats
        elif col == REGION_COLUMN:
            ok = "roi" in cats
        elif col in cluster_set:
            ok = "cluster" in cats
        else:
            ok = "intensity" in cats          # 残りは強度(m/z)列
        if ok:
            keep.append(col)
    return keep


def describe(options) -> str:
    """現在の設定の一行要約。ボタン脇に出して、開かなくても分かるようにする。"""
    opt = normalize(options)
    if opt["is_default"]:
        return "既定（従来どおり全項目・1 ピクセル単位）"
    labels = {k: lbl for k, lbl, _ in CATEGORIES}
    cats = "・".join(labels[k].split(" ")[0] for k in CATEGORY_KEYS
                     if k in opt["categories"])
    if opt["mode"] == MODE_GROUP:
        gl = {k: lbl.split(" ")[0] for k, lbl in GROUP_KEYS}
        keys = " × ".join(gl[k] for k in opt["group_keys"]) or "（キー未選択）"
        return f"{cats} / {keys} の平均"
    return f"{cats} / 1 ピクセル単位"
