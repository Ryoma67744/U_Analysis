"""データ出力の列選択・集計単位オプションの回帰テスト。

最重要は **`options=None` で従来と完全に同じ出力になること**。ここが崩れると、
既存の出力を前提にしているスクリプトが黙って壊れる。

次に重要なのが「読む列」と「出す列」の区別。クラスタ列と領域名列の突合は
`(sample, round(x,4), round(y,4))` をキーにしているので、利用者が「座標は要らない」と
言っても x/y を parquet から読まないわけにはいかない。読まずに済ませると
**クラスタが 1 件も付かない出力が「成功」として出る**。
"""

import pytest

from app.services.export_options import (
    LEGACY_CATEGORIES,
    MODE_GROUP,
    MODE_PIXEL,
    REGION_COLUMN,
    describe,
    intensity_columns,
    is_group_mode,
    normalize,
    parquet_columns,
    resolve_group_columns,
    select_output_columns,
    wants,
    wants_mzlist,
    wants_spot_table,
)

# 変換済み parquet の典型的な列並び
_PARQUET_COLS = ["id", "x", "y", "100.0001", "200.0002", "300.0003", "annotation"]
# アプリが後から足す列まで含めた、出力直前の並び
_FULL_COLS = _PARQUET_COLS + ["UMAP_1", "UMAP_2", "TotalCount", "nFeature",
                              "UMAP cluster", REGION_COLUMN]


# ---------------------------------------------------------------------------
# 後方互換
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [None, {}, [], "", 0, {"categories": []},
                                 {"categories": ["存在しない"]}])
def test_unset_or_broken_options_fall_back_to_legacy(bad):
    """未設定・壊れた値はすべて従来どおりに倒す。

    設定 UI の不具合で出力が静かに変わるより、既定に戻る方が安全。
    """
    opt = normalize(bad)
    assert opt["categories"] == set(LEGACY_CATEGORIES)
    assert opt["mode"] == MODE_PIXEL
    assert opt["is_default"] is True


def test_legacy_keeps_every_original_column():
    """options=None の出力列が、元の並びそのままであること。"""
    assert select_output_columns(_FULL_COLS, None, ["UMAP cluster"]) == [
        "id", "x", "y", "100.0001", "200.0002", "300.0003", "annotation",
        "UMAP cluster", REGION_COLUMN,
    ]


def test_legacy_reads_all_parquet_columns():
    """options=None なら列を絞らない（従来と同じ読み方）。"""
    assert parquet_columns(_PARQUET_COLS, None) is None


def test_umap_and_quality_are_off_by_default():
    """新カテゴリを既定 ON にしない。

    ON にすると既存の出力の列位置がずれ、位置で読んでいる利用者が壊れる。
    """
    cols = select_output_columns(_FULL_COLS, None, ["UMAP cluster"])
    for c in ("UMAP_1", "UMAP_2", "TotalCount", "nFeature"):
        assert c not in cols


# ---------------------------------------------------------------------------
# 読む列 vs 出す列
# ---------------------------------------------------------------------------
def test_intensity_off_does_not_read_mz_columns():
    """強度を外したら m/z 列を読まない。読んでから捨てるのでは意味がない。"""
    opts = {"categories": ["coords", "cluster"]}
    cols = parquet_columns(_PARQUET_COLS, opts)
    assert cols is not None
    for mz in ("100.0001", "200.0002", "300.0003"):
        assert mz not in cols, "強度列を読んでしまっている"


def test_join_columns_are_read_even_when_not_output():
    """座標も切片も「出さない」選択でも、突合に要るので読む。

    読まないとクラスタ列・領域名列が 1 件も突合できず、
    空欄だらけの出力が「成功」として出てしまう。
    """
    opts = {"categories": ["cluster"]}          # 座標も切片も出さない
    cols = parquet_columns(_PARQUET_COLS, opts)
    for required in ("x", "y", "annotation"):
        assert required in cols, f"{required} は突合に必要なので読まねばならない"

    # 読みはするが、出力からは落ちる
    out = select_output_columns(_FULL_COLS, opts, ["UMAP cluster"])
    for dropped in ("x", "y", "annotation"):
        assert dropped not in out


def test_id_is_read_only_when_requested():
    """id は突合に使わないので、要らなければ読まない。"""
    assert "id" not in parquet_columns(_PARQUET_COLS, {"categories": ["coords"]})
    assert "id" in parquet_columns(_PARQUET_COLS, {"categories": ["id", "coords"]})


def test_parquet_columns_preserve_original_order():
    """読む列の順序が元の parquet の並びを保つ（読み込み後の列順が変わらない）。"""
    cols = parquet_columns(_PARQUET_COLS, {"categories": ["id", "coords", "section"]})
    assert cols == ["id", "x", "y", "annotation"]


def test_missing_columns_are_not_invented():
    """parquet に無い列を読もうとしない（annotation 無しの古いファイル）。"""
    no_annot = ["id", "x", "y", "100.0001"]
    cols = parquet_columns(no_annot, {"categories": ["id", "coords", "section"]})
    assert "annotation" not in cols


# ---------------------------------------------------------------------------
# 出力列の選択
# ---------------------------------------------------------------------------
def test_output_column_order_is_stable():
    """新カテゴリは m/z ブロックの後ろ・クラスタの前。既存の並びを崩さない。"""
    opts = {"categories": list(LEGACY_CATEGORIES) + ["umap", "quality"]}
    assert select_output_columns(_FULL_COLS, opts, ["UMAP cluster"]) == _FULL_COLS


def test_multi_method_cluster_columns_are_all_kept_or_all_dropped():
    """複数手法のクラスタ列がまとめて扱われる。"""
    cols = _PARQUET_COLS + ["RPCA", "Harmony", REGION_COLUMN]
    methods = ["RPCA", "Harmony"]

    kept = select_output_columns(cols, {"categories": ["cluster"]}, methods)
    assert kept == ["RPCA", "Harmony"]

    dropped = select_output_columns(cols, {"categories": ["roi"]}, methods)
    assert dropped == [REGION_COLUMN]


def test_intensity_columns_excludes_app_added_columns():
    """強度列の判定が、アプリの追加列を巻き込まない。

    巻き込むとクラスタ番号や領域名を「平均」しようとして壊れる。
    """
    got = intensity_columns(_FULL_COLS)
    assert got == ["100.0001", "200.0002", "300.0003"]


def test_intensity_columns_needs_method_names_from_caller():
    """複数手法のクラスタ列（手法名そのもの）を強度列と誤認しない。

    手法名は "RPCA" / "Harmony" / "PCA" など任意の文字列を取り得るので、
    固定リストでは判別できない。呼び出し側が渡さないと、クラスタ番号を
    平均しようとして壊れる。
    """
    cols = _PARQUET_COLS + ["RPCA", "Harmony", REGION_COLUMN]
    got = intensity_columns(cols, cluster_columns=["RPCA", "Harmony"])
    assert got == ["100.0001", "200.0002", "300.0003"]
    assert "RPCA" not in got and "Harmony" not in got


# ---------------------------------------------------------------------------
# 集計単位
# ---------------------------------------------------------------------------
def test_pixel_mode_has_no_group_columns():
    assert is_group_mode(None) is False
    assert resolve_group_columns(None, ["UMAP cluster"]) == []


@pytest.mark.parametrize("keys,expected", [
    (["cluster"],                    ["UMAP cluster"]),
    (["section", "cluster"],         ["annotation", "UMAP cluster"]),
    (["section", "roi"],             ["annotation", REGION_COLUMN]),
    (["section", "roi", "cluster"],  ["annotation", REGION_COLUMN, "UMAP cluster"]),
])
def test_group_key_resolution(keys, expected):
    """4 通りのキー組み合わせが実際の列名に解決される。"""
    opts = {"mode": MODE_GROUP, "group_keys": keys}
    assert resolve_group_columns(opts, ["UMAP cluster"]) == expected


def test_group_keys_are_ordered_consistently():
    """UI での選択順に関わらず、キーの並びは一定。

    順序が入力次第で変わると、同じ設定でも列順の違う出力が出る。
    """
    a = resolve_group_columns(
        {"mode": MODE_GROUP, "group_keys": ["cluster", "section"]}, ["UMAP cluster"])
    b = resolve_group_columns(
        {"mode": MODE_GROUP, "group_keys": ["section", "cluster"]}, ["UMAP cluster"])
    assert a == b == ["annotation", "UMAP cluster"]


def test_roi_not_required_when_not_a_key():
    """ROI をキーにしなければ、領域名は集計キーに現れない。

    これにより H&E を設定していないプロジェクトでも平均が出せる
    （既存の MetaboAnalyst ZIP 出力にあった最大の制約が外れる）。
    """
    cols = resolve_group_columns(
        {"mode": MODE_GROUP, "group_keys": ["section", "cluster"]}, ["UMAP cluster"])
    assert REGION_COLUMN not in cols


def test_unknown_mode_falls_back_to_pixel():
    assert normalize({"mode": "でたらめ"})["mode"] == MODE_PIXEL


# ---------------------------------------------------------------------------
# 要約表示
# ---------------------------------------------------------------------------
def test_describe_default_says_so():
    assert "既定" in describe(None)


def test_describe_group_mode_shows_keys():
    got = describe({"categories": ["coords", "cluster"],
                    "mode": MODE_GROUP, "group_keys": ["section", "cluster"]})
    assert "平均" in got and "×" in got


def test_describe_warns_when_group_keys_missing():
    """キー未選択のまま平均を選んでいることが要約から分かる。"""
    got = describe({"categories": ["coords"], "mode": MODE_GROUP, "group_keys": []})
    assert "キー未選択" in got


def test_wants_reads_categories():
    assert wants(None, "intensity") is True
    assert wants({"categories": ["coords"]}, "intensity") is False


# ---------------------------------------------------------------------------
# m/z 一覧（強度とは独立した選択肢）
# ---------------------------------------------------------------------------
def test_mzlist_is_off_by_default():
    """既定 OFF。ON にすると既存の出力に別表が付いて挙動が変わる。"""
    assert wants(None, "mzlist") is False
    assert "mzlist" not in LEGACY_CATEGORIES


@pytest.mark.parametrize("cats,mz,spot", [
    (["intensity"],                     False, True),   # 従来どおり
    (["mzlist"],                        True,  False),  # 一覧だけ
    (["intensity", "mzlist"],           True,  True),   # 両方
    (["coords", "cluster"],             False, True),   # どちらも無し
    (["mzlist", "coords"],              True,  True),   # 一覧 + スポット項目
])
def test_intensity_and_mzlist_are_independent(cats, mz, spot):
    """強度と m/z 一覧は独立。4 通りとも意味を持つ。"""
    opts = {"categories": cats}
    assert wants_mzlist(opts) is mz
    assert wants_spot_table(opts) is spot


def test_mzlist_alone_means_no_spot_table():
    """m/z 一覧だけなら、1 行 = 1 スポットの表は出さない。

    行の単位が違う（1 行 = 1 m/z）ので、同じ表には入らない。
    """
    assert wants_spot_table({"categories": ["mzlist"]}) is False


def test_mzlist_is_not_a_spot_column():
    """m/z 一覧は列選択に混ざらない（スポット表の列にはならない）。"""
    cols = select_output_columns(
        _FULL_COLS, {"categories": ["coords", "mzlist"]}, ["UMAP cluster"])
    assert cols == ["x", "y"]
    assert "mzlist" not in cols
