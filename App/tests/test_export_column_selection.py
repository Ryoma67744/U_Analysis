"""データ出力の列選択・集計を TIMS 経路で通しで確認する回帰テスト。

`test_export_options.py` / `test_export_aggregate.py` が部品を、ここが結線を見る。

最重要が 2 つある。

1. **`options=None` で従来と完全に同じ出力**。崩れると既存の出力を前提にしている
   スクリプトが黙って壊れる。
2. **強度を外したとき m/z 列を実際に「読まない」**。読んでから捨てても出力は同じに
   見えるので、出力だけを見るテストではこの退行を検出できない。実データでは
   m/z が数千列あり、読むか読まないかがメモリの支配項になる。
"""

import io
from collections import OrderedDict

import pandas as pd
import pytest

from app.callbacks import interactive_data_export as de
from app.services.export_options import MODE_GROUP, REGION_COLUMN

_MZ = ["100.0001", "200.0002", "300.0003"]


@pytest.fixture
def tims_parquet(tmp_path):
    """2 切片 × 4 スポットの変換済み parquet を 1 ファイル作る。"""
    df = pd.DataFrame({
        "id": range(1, 9),
        "x": [10.0, 11.0, 12.0, 13.0] * 2,
        "y": [20.0, 21.0, 22.0, 23.0] * 2,
        _MZ[0]: [1.0, 3.0, 5.0, 7.0, 11.0, 13.0, 15.0, 17.0],
        _MZ[1]: [2.0, 4.0, 6.0, 8.0, 12.0, 14.0, 16.0, 18.0],
        _MZ[2]: [0.5, 0.5, 0.5, 0.5, 1.5, 1.5, 1.5, 1.5],
        "annotation": ["S1"] * 4 + ["S2"] * 4,
    })
    p = tmp_path / "S.parquet"
    df.to_parquet(p, index=False)
    return tmp_path


@pytest.fixture
def lookups():
    """S1/S2 各 4 スポットに cluster1 / cluster2 を 2 つずつ割り当てる。"""
    return OrderedDict([("Harmony", {
        ("S1", 10.0, 20.0): "cluster1", ("S1", 11.0, 21.0): "cluster1",
        ("S1", 12.0, 22.0): "cluster2", ("S1", 13.0, 23.0): "cluster2",
        ("S2", 10.0, 20.0): "cluster1", ("S2", 11.0, 21.0): "cluster1",
        ("S2", 12.0, 22.0): "cluster2", ("S2", 13.0, 23.0): "cluster2",
    })])


def _read(data, filename):
    if filename.endswith(".parquet"):
        return pd.read_parquet(data)
    return pd.read_csv(data)


# ---------------------------------------------------------------------------
# 後方互換
# ---------------------------------------------------------------------------
def test_options_none_keeps_legacy_columns_and_filename(tims_parquet, lookups):
    """options 未指定なら列もファイル名も従来どおり。"""
    data, filename = de._export_tims(str(tims_parquet), lookups, "csv")
    assert filename == "UMAP_cluster_TIMS.csv"
    got = _read(data, filename)
    assert list(got.columns) == ["id", "x", "y", *_MZ, "annotation", "UMAP cluster"]
    assert len(got) == 8


def test_options_none_and_explicit_full_selection_agree(tims_parquet, lookups):
    """全カテゴリを明示選択した結果が、未指定のときと一致する。"""
    a, _ = de._export_tims(str(tims_parquet), lookups, "csv")
    b, _ = de._export_tims(
        str(tims_parquet), lookups, "csv",
        options={"categories": ["id", "coords", "intensity", "section",
                               "cluster", "roi"]})
    pd.testing.assert_frame_equal(_read(a, "x.csv"), _read(b, "x.csv"))


# ---------------------------------------------------------------------------
# 列選択
# ---------------------------------------------------------------------------
def test_dropping_intensity_removes_mz_columns(tims_parquet, lookups):
    data, filename = de._export_tims(
        str(tims_parquet), lookups, "csv",
        options={"categories": ["coords", "cluster"]})
    got = _read(data, filename)
    assert list(got.columns) == ["x", "y", "UMAP cluster"]
    assert len(got) == 8, "行は減らない（列だけ絞る）"


def test_intensity_columns_are_not_read_from_parquet(tims_parquet, lookups, monkeypatch):
    """強度 OFF のとき pd.read_parquet に m/z 列が渡っていないこと。

    出力を見るだけでは「読んでから捨てた」と区別が付かない。実データでは
    読むかどうかがメモリの支配項なので、呼び出し引数そのものを固定する。
    """
    seen = {}
    real = de.pd.read_parquet

    def spy(path, columns=None, **kw):
        seen["columns"] = columns
        return real(path, columns=columns, **kw)

    monkeypatch.setattr(de.pd, "read_parquet", spy)
    de._export_tims(str(tims_parquet), lookups, "csv",
                    options={"categories": ["coords", "cluster"]})

    assert seen["columns"] is not None, "列を絞らずに全部読んでいる"
    for mz in _MZ:
        assert mz not in seen["columns"], f"{mz} を読んでしまっている"


def test_join_columns_are_read_even_when_dropped_from_output(
        tims_parquet, lookups, monkeypatch):
    """座標も切片も出さない選択でも、突合できてクラスタが埋まる。

    x/y/annotation を読まずに済ませると、クラスタが 1 件も付かない出力が
    「成功」として出てしまう。
    """
    seen = {}
    real = de.pd.read_parquet

    def spy(path, columns=None, **kw):
        seen["columns"] = columns
        return real(path, columns=columns, **kw)

    monkeypatch.setattr(de.pd, "read_parquet", spy)
    data, filename = de._export_tims(
        str(tims_parquet), lookups, "csv", options={"categories": ["cluster"]})

    assert seen["columns"] is not None, "列を絞らずに全部読んでいる"
    for required in ("x", "y", "annotation"):
        assert required in seen["columns"], f"{required} は突合に必要"

    got = _read(data, filename)
    assert list(got.columns) == ["UMAP cluster"]
    assert set(got["UMAP cluster"]) == {"cluster1", "cluster2"}, "クラスタが埋まっていない"


def test_empty_selection_raises(tims_parquet, lookups):
    """1 つも選ばれていなければ例外。ヘッダだけのファイルを黙って返さない。"""
    with pytest.raises(ValueError, match="出力する列"):
        de._export_tims(str(tims_parquet), lookups, "csv",
                        options={"categories": ["umap"]})   # parquet に無い列だけ


def test_extra_lookups_add_umap_columns(tims_parquet, lookups):
    """plot_data 由来の UMAP 座標が、クラスタと同じ行に載る。"""
    extra = {"UMAP_1": {("S1", 10.0, 20.0): 0.11, ("S2", 10.0, 20.0): 0.21}}
    data, filename = de._export_tims(
        str(tims_parquet), lookups, "csv",
        options={"categories": ["coords", "section", "umap", "cluster"]},
        extra_lookups=extra)
    got = _read(data, filename)
    assert "UMAP_1" in got.columns
    row = got[(got["annotation"] == "S1") & (got["x"] == 10.0)]
    assert float(row["UMAP_1"].iloc[0]) == pytest.approx(0.11)
    # 突合しなかった行は空欄（0 で埋めない。0 は実在し得る座標值）
    assert got["UMAP_1"].isna().sum() == 6


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------
def test_group_by_section_and_cluster(tims_parquet, lookups):
    """切片 × クラスタ。行数とファイル名が変わる。"""
    data, filename = de._export_tims(
        str(tims_parquet), lookups, "csv",
        options={"categories": ["intensity"], "mode": MODE_GROUP,
                 "group_keys": ["section", "cluster"]})
    assert filename == "UMAP_cluster_TIMS_grouped.csv", \
        "集計出力が従来と同じファイル名だと取り違える"

    got = _read(data, filename)
    assert len(got) == 4, "2 切片 × 2 クラスタ"
    assert set(got["n"]) == {2}
    # S1/cluster1 = (1.0 + 3.0) / 2
    row = got[(got["annotation"] == "S1") & (got["Cluster"] == "cluster1")]
    assert float(row[f"{_MZ[0]}_mean"].iloc[0]) == pytest.approx(2.0)
    assert float(row[f"{_MZ[0]}_sd"].iloc[0]) == pytest.approx(2.0 ** 0.5)


def test_group_by_cluster_only_spans_sections(tims_parquet, lookups):
    """クラスタのみ。切片をまたいでまとめる。"""
    data, filename = de._export_tims(
        str(tims_parquet), lookups, "csv",
        options={"categories": ["intensity"], "mode": MODE_GROUP,
                 "group_keys": ["cluster"]})
    got = _read(data, filename)
    assert len(got) == 2
    assert set(got["n"]) == {4}, "切片をまたいで 4 スポットずつ"
    # cluster1 = (1 + 3 + 11 + 13) / 4
    row = got[got["Cluster"] == "cluster1"]
    assert float(row[f"{_MZ[0]}_mean"].iloc[0]) == pytest.approx(7.0)


def test_group_count_sums_to_pixel_row_count(tims_parquet, lookups):
    """n の合計がピクセル出力の行数と一致する（行が消えていない）。"""
    pixel, _ = de._export_tims(str(tims_parquet), lookups, "csv")
    grouped, fn = de._export_tims(
        str(tims_parquet), lookups, "csv",
        options={"categories": ["intensity"], "mode": MODE_GROUP,
                 "group_keys": ["section", "cluster"]})
    assert int(_read(grouped, fn)["n"].sum()) == len(_read(pixel, "x.csv"))


def test_group_without_roi_key_does_not_need_hne(tims_parquet, lookups):
    """ROI をキーにしなければ H&E 未設定でも集計できる。

    既存の MetaboAnalyst ZIP 出力は ROI 未割当を丸ごと捨てるため、
    H&E を設定していないプロジェクトでは平均が 1 行も出せなかった。
    """
    data, fn = de._export_tims(
        str(tims_parquet), lookups, "csv",           # region_lookup を渡さない
        options={"categories": ["intensity"], "mode": MODE_GROUP,
                 "group_keys": ["section", "cluster"]})
    got = _read(data, fn)
    assert len(got) == 4
    assert REGION_COLUMN not in got.columns


def test_group_without_keys_raises(tims_parquet, lookups):
    """平均を選んでキーを 1 つも選ばないのは例外。全体平均を黙って出さない。"""
    with pytest.raises(ValueError, match="集計キー"):
        de._export_tims(str(tims_parquet), lookups, "csv",
                        options={"categories": ["intensity"],
                                 "mode": MODE_GROUP, "group_keys": []})


def test_multi_method_group_output_is_long_with_method_column(tims_parquet):
    """複数手法の集計は Method 列を持つ縦持ちになる。

    手法ごとに列を横へ並べると、意味の違うクラスタ番号が 1 行に同居する。
    """
    multi = OrderedDict([
        ("Harmony", {("S1", 10.0, 20.0): "cluster1", ("S1", 11.0, 21.0): "cluster1",
                     ("S1", 12.0, 22.0): "cluster2", ("S1", 13.0, 23.0): "cluster2",
                     ("S2", 10.0, 20.0): "cluster1", ("S2", 11.0, 21.0): "cluster1",
                     ("S2", 12.0, 22.0): "cluster2", ("S2", 13.0, 23.0): "cluster2"}),
        ("RPCA", {("S1", 10.0, 20.0): "cluster9", ("S1", 11.0, 21.0): "cluster9",
                  ("S1", 12.0, 22.0): "cluster9", ("S1", 13.0, 23.0): "cluster9",
                  ("S2", 10.0, 20.0): "cluster8", ("S2", 11.0, 21.0): "cluster8",
                  ("S2", 12.0, 22.0): "cluster8", ("S2", 13.0, 23.0): "cluster8"}),
    ])
    data, fn = de._export_tims(
        str(tims_parquet), multi, "csv",
        options={"categories": ["intensity"], "mode": MODE_GROUP,
                 "group_keys": ["cluster"]})
    got = _read(data, fn)
    assert "Method" in got.columns and "Cluster" in got.columns
    assert set(got["Method"]) == {"Harmony", "RPCA"}
    # 手法名がそのまま強度列として平均されていないこと
    for bad in ("Harmony_mean", "RPCA_mean"):
        assert bad not in got.columns


def test_group_mode_ignores_method_when_cluster_not_a_key(tims_parquet):
    """クラスタをキーにしないなら手法で結果は変わらない＝1 回だけ集計する。"""
    multi = OrderedDict([
        ("Harmony", {("S1", 10.0, 20.0): "cluster1"}),
        ("RPCA", {("S1", 10.0, 20.0): "cluster9"}),
    ])
    data, fn = de._export_tims(
        str(tims_parquet), multi, "csv",
        options={"categories": ["intensity"], "mode": MODE_GROUP,
                 "group_keys": ["section"]})
    got = _read(data, fn)
    assert len(got) == 2, "切片 2 つ分だけ。手法の数だけ重複しない"
    assert "Method" not in got.columns


# ---------------------------------------------------------------------------
# m/z 一覧（1 行 = 1 m/z の別表）
# ---------------------------------------------------------------------------
def _sidecar_in(folder):
    """データフォルダにサイドカーを置く。"""
    pd.DataFrame({
        "mz": [100.0001, 200.0002],
        "compound": ["Glutathione", "PC(34:1)"],
        "adduct": ["[M-H]-", "[M+H]+"],
        "formula": ["C10H17N3O6S", "C42H82NO8P"],
        "ppm": [1.2, -0.8],
        "lipid_class": ["", "PC"],
        "database": ["HMDB", "LIPIDMAPS"],
        "raw": ["Glutathione | [M-H]-", "PC(34:1) | [M+H]+"],
    }).to_parquet(folder / "S_feature_annotations.parquet", index=False)


def test_mzlist_is_off_by_default(tims_parquet, lookups):
    """既定では m/z 一覧を出さない（従来の出力を変えない）。"""
    data, filename = de._export_tims(str(tims_parquet), lookups, "csv")
    assert filename == "UMAP_cluster_TIMS.csv"
    assert "mz" not in _read(data, filename).columns


def test_mzlist_only_outputs_the_list(tims_parquet, lookups):
    """m/z 一覧だけを選ぶと、1 行 = 1 m/z の小さな表が出る。"""
    data, filename = de._export_tims(
        str(tims_parquet), lookups, "csv", options={"categories": ["mzlist"]})
    assert filename == "mz_list_TIMS.csv", "一覧表が本体のときは別のファイル名"

    got = _read(data, filename)
    assert len(got) == 3, "m/z 3 件"
    assert list(got["mz"]) == sorted(got["mz"])
    for col in ("mz", "列名", "compound", "adduct", "formula"):
        assert col in got.columns


def test_mzlist_only_does_not_read_spot_rows(tims_parquet, lookups, monkeypatch):
    """m/z 一覧だけなら parquet の行を 1 行も読まない。

    「どの m/z が入っているか知りたいだけ」で数 GB を読むのは本末転倒。
    """
    called = []
    real = de.pd.read_parquet

    def spy(path, columns=None, **kw):
        called.append(str(path))
        return real(path, columns=columns, **kw)

    monkeypatch.setattr(de.pd, "read_parquet", spy)
    de._export_tims(str(tims_parquet), lookups, "csv",
                    options={"categories": ["mzlist"]})
    # サイドカーは無いので read_parquet は 1 度も呼ばれない（スキーマ読みのみ）
    assert not [c for c in called if c.endswith("S.parquet")], \
        f"スポットの parquet を読んでしまっている: {called}"


def test_mzlist_picks_up_annotations(tims_parquet, lookups):
    """サイドカーがあれば化合物名・アダクト・組成式が付く。"""
    _sidecar_in(tims_parquet)
    data, filename = de._export_tims(
        str(tims_parquet), lookups, "csv", options={"categories": ["mzlist"]})
    got = _read(data, filename).set_index("mz")
    assert got.loc[100.0001, "compound"] == "Glutathione"
    assert got.loc[200.0002, "adduct"] == "[M+H]+"
    # 注釈の無い m/z は空欄のまま（無理に埋めない）
    assert pd.isna(got.loc[300.0003, "compound"]) or got.loc[300.0003, "compound"] == ""


def test_mzlist_works_without_sidecar(tims_parquet, lookups):
    """サイドカーが無くても一覧は出る。注釈列が空欄になるだけ。"""
    data, filename = de._export_tims(
        str(tims_parquet), lookups, "csv", options={"categories": ["mzlist"]})
    got = _read(data, filename)
    assert len(got) == 3
    assert list(got["mz"]) == [100.0001, 200.0002, 300.0003]


def test_mzlist_with_spot_columns_errors_on_csv(tims_parquet, lookups):
    """csv でスポット列と併用したら、黙って片方を落とさず xlsx を案内する。"""
    with pytest.raises(ValueError, match="xlsx|Excel"):
        de._export_tims(str(tims_parquet), lookups, "csv",
                        options={"categories": ["coords", "cluster", "mzlist"]})


def test_mzlist_with_spot_columns_errors_on_parquet(tims_parquet, lookups):
    with pytest.raises(ValueError, match="xlsx|Excel"):
        de._export_tims(str(tims_parquet), lookups, "parquet",
                        options={"categories": ["coords", "mzlist"]})


def test_mzlist_becomes_a_separate_sheet_in_xlsx(tims_parquet, lookups):
    """xlsx なら本体と m/z 一覧の 2 シートになる。"""
    _sidecar_in(tims_parquet)
    data, filename = de._export_tims(
        str(tims_parquet), lookups, "xlsx",
        options={"categories": ["coords", "cluster", "mzlist"]})
    assert filename == "UMAP_cluster_TIMS.xlsx"

    sheets = pd.read_excel(data, sheet_name=None)
    assert "Data" in sheets and "m_z" in sheets
    assert list(sheets["Data"].columns) == ["x", "y", "UMAP cluster"]
    assert len(sheets["m_z"]) == 3
    assert sheets["m_z"]["compound"].iloc[0] == "Glutathione"


def test_mzlist_unchanged_by_group_mode(tims_parquet, lookups):
    """集計モードと併用しても m/z 一覧の中身は変わらない。"""
    _sidecar_in(tims_parquet)
    pixel, _ = de._export_tims(
        str(tims_parquet), lookups, "xlsx",
        options={"categories": ["coords", "cluster", "mzlist"]})
    grouped, _ = de._export_tims(
        str(tims_parquet), lookups, "xlsx",
        options={"categories": ["intensity", "mzlist"], "mode": MODE_GROUP,
                 "group_keys": ["section", "cluster"]})
    a = pd.read_excel(pixel, sheet_name="m_z")
    b = pd.read_excel(grouped, sheet_name="m_z")
    pd.testing.assert_frame_equal(a, b)


def test_unknown_categories_fall_back_to_legacy_output(tims_parquet, lookups):
    """知らないカテゴリしか入っていない設定は、例外ではなく従来の出力に倒す。

    `normalize()` の設計どおりの挙動。設定 UI の不具合や古い Store の値で
    出力が静かに変わるより、既定に戻る方が安全なため。
    ここが例外になると、壊れた Store 値ひとつで出力ができなくなる。
    """
    data, filename = de._export_tims(
        str(tims_parquet), lookups, "csv",
        options={"categories": ["存在しないカテゴリ"]})
    assert filename == "UMAP_cluster_TIMS.csv"
    assert list(_read(data, filename).columns) == [
        "id", "x", "y", *_MZ, "annotation", "UMAP cluster"]
