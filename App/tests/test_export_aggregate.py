"""グループ平均集計の回帰テスト。

データ出力に「1 ピクセル単位 / グループ平均」の選択を足した際の集計ロジックを固定する。

集計は「切片 / 領域名(ROI) / クラスタ」を自由に組み合わせられる。実データ規模で
全行を同時に持たないよう、ファイルごとの部分集計（n / 総和 / 二乗和）を合成する
方式にしてあるので、**部分集計の合成が一括集計と数値一致すること**が正しさの要。
グループが複数ファイルにまたがるケース（「クラスタのみ」では必ず起きる）を必ず含める。

期待値は pandas の groupby を正解として突き合わせる。手計算の定数を置くと、
式を写し間違えたときにテストごと間違った値で固定されてしまうため。
"""

import numpy as np
import pandas as pd
import pytest

from app.services.export_aggregate import (
    COUNT_COLUMN,
    MEAN_SUFFIX,
    SD_SUFFIX,
    accumulate_partial,
    aggregate,
    combine_partials,
)

_MZ = ["100.0001", "200.0002", "300.0003"]


def _frame(seed, n_rows, sections, clusters, regions=("Brain", "Liver")):
    rng = np.random.RandomState(seed)
    return pd.DataFrame({
        "annotation": rng.choice(list(sections), n_rows),
        "領域名": rng.choice(list(regions), n_rows),
        "UMAP cluster": rng.choice(list(clusters), n_rows),
        **{c: rng.rand(n_rows).astype("float32") * 1000 for c in _MZ},
    })


# ---------------------------------------------------------------------------
# キーの組み合わせ
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("group_cols", [
    ["UMAP cluster"],                            # クラスタのみ
    ["annotation", "UMAP cluster"],              # 切片 × クラスタ
    ["annotation", "領域名"],                     # 切片 × ROI
    ["annotation", "領域名", "UMAP cluster"],     # 切片 × ROI × クラスタ
])
def test_group_key_combinations_match_pandas(group_cols):
    """4 通りのキー組み合わせで、行数・グループ・平均値が pandas と一致する。"""
    df = _frame(0, 400, ["S1", "S2"], ["cluster1", "cluster2", "cluster3"])
    out = aggregate(df, group_cols, _MZ)

    # 正解値は float64 で取る。強度列は float32 なので pandas にそのまま集計させると
    # 累積が float32 で行われ、20 万行規模では相対 1e-7 程度ずれる。こちらの実装は
    # 意図的に float64 で累積しており、float32 の集計と比べると「合わない」のではなく
    # **こちらの方が正確**。テストの正解を float32 に合わせると精度を落とす方向へ
    # 実装を縛ってしまう。
    expected = df.astype({c: "float64" for c in _MZ}).groupby(
        group_cols, sort=True)[_MZ].mean()
    assert len(out) == len(expected), f"行数が不一致: {len(out)} != {len(expected)}"

    got = out.set_index(group_cols)
    for mz in _MZ:
        np.testing.assert_allclose(
            got[f"{mz}{MEAN_SUFFIX}"].to_numpy(),
            expected[mz].to_numpy(), rtol=1e-9,
            err_msg=f"{mz} の平均が pandas と一致しない")


def test_count_matches_group_size():
    """n が各グループの行数と一致し、合計が元の行数になる。"""
    df = _frame(1, 300, ["S1", "S2", "S3"], ["cluster1", "cluster2"])
    cols = ["annotation", "UMAP cluster"]
    out = aggregate(df, cols, _MZ)

    expected = df.groupby(cols, sort=True).size()
    np.testing.assert_array_equal(
        out.set_index(cols)[COUNT_COLUMN].to_numpy(), expected.to_numpy())
    assert int(out[COUNT_COLUMN].sum()) == len(df), "n の合計が元の行数と違う"


def test_sd_matches_pandas_unbiased():
    """SD が pandas の std(ddof=1) と一致する。"""
    df = _frame(2, 500, ["S1", "S2"], ["cluster1", "cluster2"])
    cols = ["annotation", "UMAP cluster"]
    out = aggregate(df, cols, _MZ)

    # 平均と同じ理由で正解値は float64 で取る。
    expected = df.astype({c: "float64" for c in _MZ}).groupby(
        cols, sort=True)[_MZ].std(ddof=1)
    got = out.set_index(cols)
    for mz in _MZ:
        np.testing.assert_allclose(
            got[f"{mz}{SD_SUFFIX}"].to_numpy(),
            expected[mz].to_numpy(), rtol=1e-6,
            err_msg=f"{mz} の SD が pandas と一致しない")


def test_single_row_group_sd_is_nan_not_zero():
    """n=1 のグループの SD は NaN。

    0 を書くと「ばらつきが無い」と読めてしまうが、実際は「ばらつきが不明」。
    群間比較でそのまま誤読される。
    """
    df = pd.DataFrame({
        "annotation": ["S1", "S1", "S2"],       # S2 は 1 行だけ
        "UMAP cluster": ["cluster1", "cluster1", "cluster1"],
        **{c: [1.0, 3.0, 7.0] for c in _MZ},
    })
    out = aggregate(df, ["annotation", "UMAP cluster"], _MZ).set_index("annotation")

    assert np.isnan(out.loc["S2", f"{_MZ[0]}{SD_SUFFIX}"]), "n=1 の SD が NaN でない"
    # n=2 の側は NaN にならない（std(ddof=1) = sqrt(2)）
    assert not np.isnan(out.loc["S1", f"{_MZ[0]}{SD_SUFFIX}"])
    np.testing.assert_allclose(out.loc["S1", f"{_MZ[0]}{SD_SUFFIX}"], np.sqrt(2.0))


# ---------------------------------------------------------------------------
# 部分集計の合成（この方式の正しさの要）
# ---------------------------------------------------------------------------
def test_partial_accumulation_matches_single_pass():
    """ファイル分割して部分集計→合成した結果が、一括集計と一致する。"""
    whole = _frame(3, 600, ["S1", "S2", "S3"], ["cluster1", "cluster2"])
    cols = ["annotation", "UMAP cluster"]

    chunks = [whole.iloc[0:250], whole.iloc[250:410], whole.iloc[410:]]
    combined = combine_partials(
        [accumulate_partial(c, cols, _MZ) for c in chunks], cols)
    single = aggregate(whole, cols, _MZ)

    pd.testing.assert_frame_equal(
        combined.sort_values(cols).reset_index(drop=True),
        single.sort_values(cols).reset_index(drop=True),
        check_exact=False, rtol=1e-9)


def test_group_spanning_multiple_files_is_correct():
    """グループがファイルをまたぐとき、総和と個数を足してから割る。

    「クラスタのみ」でまとめると必ずまたぐ。ファイルごとの平均を単純平均すると
    行数の違う切片が同じ重みになり、静かに間違った値になる。
    """
    a = pd.DataFrame({"UMAP cluster": ["cluster1"] * 10,
                      **{c: np.arange(10, dtype="float64") for c in _MZ}})
    b = pd.DataFrame({"UMAP cluster": ["cluster1"] * 2,
                      **{c: np.array([100.0, 200.0]) for c in _MZ}})

    combined = combine_partials(
        [accumulate_partial(a, ["UMAP cluster"], _MZ),
         accumulate_partial(b, ["UMAP cluster"], _MZ)], ["UMAP cluster"])

    all_vals = np.concatenate([np.arange(10, dtype="float64"), [100.0, 200.0]])
    assert int(combined[COUNT_COLUMN].iloc[0]) == 12
    np.testing.assert_allclose(
        combined[f"{_MZ[0]}{MEAN_SUFFIX}"].iloc[0], all_vals.mean())
    # 素朴な「平均の平均」(= (4.5 + 150)/2 = 77.25) になっていないこと
    assert not np.isclose(combined[f"{_MZ[0]}{MEAN_SUFFIX}"].iloc[0], 77.25)


def test_block_splitting_does_not_change_result(monkeypatch):
    """強度列をブロックに切っても結果が変わらない。

    メモリ削減のための分割が数値を変えていないことの確認。
    """
    from app.services import export_aggregate as agg
    df = _frame(4, 200, ["S1", "S2"], ["cluster1", "cluster2"])
    cols = ["annotation", "UMAP cluster"]

    full = aggregate(df, cols, _MZ)
    monkeypatch.setattr(agg, "_BLOCK_BYTES", 1)      # 1 列ずつに強制
    chopped = aggregate(df, cols, _MZ)

    pd.testing.assert_frame_equal(full, chopped, check_exact=False, rtol=1e-12)


# ---------------------------------------------------------------------------
# 異常系: 黙って間違えない
# ---------------------------------------------------------------------------
def test_no_group_key_raises():
    """キー未選択は例外。黙って全体平均を出さない。

    全体平均は「何の平均か」が出力から読み取れず、意味を取り違えられる。
    """
    df = _frame(5, 50, ["S1"], ["cluster1"])
    with pytest.raises(ValueError, match="集計キー"):
        aggregate(df, [], _MZ)


def test_missing_key_column_raises():
    """存在しないキー列を指定したら例外。"""
    df = _frame(6, 50, ["S1"], ["cluster1"])
    with pytest.raises(ValueError, match="集計キーの列"):
        aggregate(df, ["領域名が無い列"], _MZ)


def test_nan_in_values_raises_with_column_name():
    """強度に NaN があると例外。列名を挙げて知らせる。

    NaN を素通しすると平均が静かにずれる。変換済み parquet は密なので
    通常は起こらず、起きたらデータ側の異常。
    """
    df = _frame(7, 20, ["S1"], ["cluster1"])
    df.loc[3, _MZ[1]] = np.nan
    with pytest.raises(ValueError, match="欠損値"):
        aggregate(df, ["annotation"], _MZ)
    # どの列かが分かること
    try:
        aggregate(df, ["annotation"], _MZ)
    except ValueError as e:
        assert _MZ[1] in str(e)


def test_unmatched_rows_group_as_blank_not_split():
    """突合できず空欄になった行が、NaN と空文字で 2 グループに割れない。

    割れると「未割当」が 2 種類あるように見え、行数の突き合わせが合わなくなる。
    """
    df = pd.DataFrame({
        "UMAP cluster": ["cluster1", "", None, np.nan],
        **{c: [1.0, 2.0, 3.0, 4.0] for c in _MZ},
    })
    out = aggregate(df, ["UMAP cluster"], _MZ)
    assert len(out) == 2, f"空欄が 1 グループにまとまっていない: {out['UMAP cluster'].tolist()}"
    blank = out[out["UMAP cluster"] == ""]
    assert int(blank[COUNT_COLUMN].iloc[0]) == 3


# ---------------------------------------------------------------------------
# 出力形式
# ---------------------------------------------------------------------------
def test_output_columns_are_ordered_and_interleaved():
    """列順は キー → n → (mean, sd) の m/z ごとの対。

    mean を全部並べてから sd を全部並べると、数千列のときに対応が追えない。
    """
    df = _frame(8, 40, ["S1"], ["cluster1", "cluster2"])
    cols = ["annotation", "UMAP cluster"]
    out = aggregate(df, cols, _MZ)

    assert list(out.columns[:3]) == cols + [COUNT_COLUMN]
    expected_tail = [f"{mz}{sfx}" for mz in _MZ for sfx in (MEAN_SUFFIX, SD_SUFFIX)]
    assert list(out.columns[3:]) == expected_tail


def test_aggregate_without_value_columns_still_counts():
    """強度を出力しない選択でも、件数だけは出せる。"""
    df = _frame(9, 30, ["S1", "S2"], ["cluster1"])
    out = aggregate(df, ["annotation"], [])
    assert list(out.columns) == ["annotation", COUNT_COLUMN]
    assert int(out[COUNT_COLUMN].sum()) == 30


def test_empty_partials_return_empty_frame():
    """部分集計が 1 つも無いとき、例外ではなく空の表を返す。"""
    out = combine_partials([], ["annotation"])
    assert out.empty
    assert COUNT_COLUMN in out.columns
