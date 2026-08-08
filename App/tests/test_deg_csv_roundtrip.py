"""ver52.5 ①: 再アノテーションを CSV 保存すると DEG が丸ごと消えていた。

■ 何が起きていたか

`standardize_deg_df` は表示用に `p_val_adj` を文字列へ変換し、数値を
`p_val_adj_raw` として **自分で足す**。ところが列名の判定が部分一致:

    elif "p_val_adj" in cl:          # ← `p_val_adj_raw` にもマッチする
        col_map[col] = "p_val_adj"

「再アノテーション実行」＋「markers_annotated.csv を上書き保存」で
`pd.DataFrame(updated)`（＝派生列を含む）がそのまま CSV に書かれるため、
**次の読み込みで `p_val_adj` が 2 本**になり、`pd.to_numeric` が
DataFrame を受け取って TypeError → 本関数が None を返す。

利用者から見た症状:

    1 回目の読み込み  : ['ATP', 'GTP']
    保存した注釈      : ['ATP (M+H)', 'GTP (M+H)']
    ★ 2 回目の読み込み: None（画面は「DEG が見つかりません」）

マーカー表・Volcano・Heatmap・クラスタ Top5 が **すべて空**になる。

■ 直し方（2 段構え）

  (a) 読み側 — 派生列を除外し、標準名の二重割り当てを禁じる。
      これだけで **既に壊れている手元の CSV も読めるようになる**（復旧経路）
  (b) 書き側 — そもそも派生列を CSV に出さない (`drop_derived_columns`)

(b) だけだと、既に保存してしまった利用者のファイルが救えない。
"""

import pandas as pd
import pytest

import app.utils.deg_utils as DU
from app.utils.deg_utils import standardize_deg_df

# ★ `drop_derived_columns` / `_DERIVED_COLUMNS` / `_standard_name` は ver52.5 で
#   足したもの。`from ... import` で書くと **修正前のコードでは収集ごと失敗**し、
#   「どのテストが振る舞いを固定しているか」を確認できなくなる
#   （ImportError は「直っていない」ではなく「まだ無い」としか言わない）。
#   モジュールとして持ち、属性参照はテストの中で行う。こうすると修正前でも
#   収集は通り、**往復の検査が assert で落ちる**のが見える。


def _raw_markers(p=1.234e-10):
    """解析が出す素のマーカー表。

    ★ p 値は **3 桁以上の仮数** にする。`f"{x:.2e}"` は 2 桁までしか
      残さないので、丸め落ちが起きるかどうかがこれで初めて見える
      （1e-10 のような値だと往復しても差が出ず、検査が空振りする）。
    """
    return pd.DataFrame({
        "gene": ["mz_100.0000", "mz_200.0000"],
        "cluster": ["0", "1"],
        "avg_log2FC": [1.5, -2.0],
        "p_val_adj": [p, 2.5e-5],
        "annotation": ["ATP", "GTP"],
    })


class TestRoundTripDoesNotBreakTheTable:

    def test_the_first_pass_is_unchanged(self):
        """★ 前提の固定: 従来どおりの結果が出ること。"""
        recs = standardize_deg_df(_raw_markers())
        assert recs is not None
        assert recs[0]["gene"] == "mz_100.0000"
        assert recs[0]["annotation"] == "ATP"
        assert recs[0]["p_val_adj"] == "1.23e-10"     # 表示用は従来どおり文字列
        assert recs[0]["p_val_adj_raw"] == 1.234e-10  # 数値も従来どおり

    def test_saving_and_reloading_keeps_the_table(self):
        """★ 本丸。修正前はここで None になり、DEG が画面から消えていた。"""
        first = standardize_deg_df(_raw_markers())
        saved = pd.DataFrame(first)              # 修正前の保存はこの形だった
        again = standardize_deg_df(saved)
        assert again is not None, (
            "保存した CSV を読み直せていない。"
            "画面には「DEG が見つかりません」と出て、"
            "マーカー表・Volcano・Heatmap がすべて空になる")
        assert [r["gene"] for r in again] == [r["gene"] for r in first]
        assert [r["annotation"] for r in again] == ["ATP", "GTP"]

    def test_it_is_stable_over_repeated_round_trips(self):
        """2 往復目以降も同じ答えになること（1 回だけ耐えても意味が無い）。"""
        r1 = standardize_deg_df(_raw_markers())
        r2 = standardize_deg_df(pd.DataFrame(r1))
        r3 = standardize_deg_df(pd.DataFrame(r2))
        assert r2 == r3

    def test_the_raw_precision_is_not_lost(self):
        """★ 丸めた文字列ではなく、生の数値を出典にすること。

        表示用の `p_val_adj` は `f"{x:.2e}"` で仮数 2 桁に落ちている。
        往復のたびにそこから読み直すと、Volcano の y 座標と
        マーカー表のソートが少しずつ変わっていく。
        """
        r1 = standardize_deg_df(_raw_markers(p=1.234e-10))
        r2 = standardize_deg_df(pd.DataFrame(r1))
        assert r2[0]["p_val_adj_raw"] == 1.234e-10, (
            f"往復で精度が落ちている: {r2[0]['p_val_adj_raw']}")

    def test_a_column_named_like_a_derived_one_does_not_hijack(self):
        """派生列そのものが入力に在っても、標準列を乗っ取らないこと。"""
        df = _raw_markers()
        df["p_num"] = [0.1, 0.2]              # 別の場所で使う一時列
        recs = standardize_deg_df(df)
        assert recs is not None
        assert recs[0]["p_val_adj_raw"] == 1.234e-10


class TestWriterDoesNotEmitDerivedColumns:

    def test_derived_columns_are_dropped(self):
        first = standardize_deg_df(_raw_markers())
        out = DU.drop_derived_columns(pd.DataFrame(first))
        assert "p_val_adj_raw" not in out.columns, (
            "画面用の派生列が成果物 CSV に出ている。"
            "次回の読み込みで列名が衝突する")
        # 解析が持つ列と annotation は残ること（消しすぎの番人）
        for c in ("gene", "cluster", "avg_log2FC", "p_val_adj", "annotation"):
            assert c in out.columns, f"{c} まで落としている: {list(out.columns)}"

    def test_it_is_a_no_op_when_there_is_nothing_to_drop(self):
        raw = _raw_markers()
        assert list(DU.drop_derived_columns(raw).columns) == list(raw.columns)

    def test_the_declaration_is_the_single_source(self):
        """列名を書き写していないこと（増えたとき片方だけ直す形を防ぐ）。"""
        df = pd.DataFrame({"gene": ["a"], **{c: [1] for c in DU._DERIVED_COLUMNS}})
        assert list(DU.drop_derived_columns(df).columns) == ["gene"]

    def test_what_the_writer_produces_can_be_read_back(self):
        """★ 書き側と読み側を **つないで** 確かめる。

        片方ずつ通っても、往復が成立しなければ利用者の問題は直らない。
        """
        first = standardize_deg_df(_raw_markers())
        saved = DU.drop_derived_columns(pd.DataFrame(first))
        again = standardize_deg_df(saved)
        assert again is not None
        assert [r["annotation"] for r in again] == ["ATP", "GTP"]
        assert again[0]["p_val_adj_raw"] == pytest.approx(1.23e-10)


class TestExistingBrokenFilesBecomeReadable:
    """★ 復旧経路: 既に保存してしまった利用者のファイルが読めること。

    書き側だけ直しても、手元にある壊れた CSV は救えない。
    """

    def test_a_file_with_both_columns_loads(self, tmp_path):
        p = tmp_path / "markers_annotated.csv"
        pd.DataFrame(standardize_deg_df(_raw_markers())).to_csv(p, index=False)
        # ディスク経由（dtype 推論が入る実際の経路）で読み直す
        recs = standardize_deg_df(pd.read_csv(p))
        assert recs is not None, "壊れた CSV が今も読めない"
        assert [r["annotation"] for r in recs] == ["ATP", "GTP"]
        assert recs[0]["p_val_adj_raw"] == 1.234e-10


class TestOtherColumnNamesStillMap:
    """★ 過剰修正の番人: 従来拾えていた列名を拾えなくしないこと。"""

    @pytest.mark.parametrize("col,expected", [
        ("avg_logFC", "avg_log2FC"),        # Seurat v3 以前
        ("avg_log2FC", "avg_log2FC"),
        ("seurat_clusters", "cluster"),     # 部分一致で拾っていた列
        ("row.names", "gene"),
    ])
    def test_known_aliases(self, col, expected):
        assert DU._standard_name(col.lower()) == expected

    def test_a_full_seurat_table_maps(self):
        df = pd.DataFrame({
            "": ["mz_100.0"], "p_val": [1e-12], "avg_log2FC": [1.5],
            "pct.1": [0.9], "pct.2": [0.1], "p_val_adj": [1e-10],
            "cluster": ["0"],
        })
        recs = standardize_deg_df(df)
        assert recs is not None
        for k in ("gene", "cluster", "avg_log2FC", "p_val_adj", "pct.1", "pct.2"):
            assert k in recs[0], f"{k} が失われた: {recs[0]}"
