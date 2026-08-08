"""ver52.5 ④: Feature plot の発現量が「位置だけ」で対応づけられていた。

■ 何が起きうるか

    if expression is None or len(np.asarray(expression)) != len(df):   # 長さだけ
        ...
    df_plot["_expression"] = np.asarray(expression)                     # 位置で代入

`expression_matrix.parquet` の 1 列を `plot_data` の行順へ **位置で** 入れており、
検査は長さのみ。ずれると **全ピクセルに別の場所の強度が出る**ため、
症状が「もっともらしい別の画像」になり気づけない。

- `plot_data` は 1 箇所でしか代入されず並べ替えも無いことは確認済み
  （**現状ずれている証拠は無い**）
- ただし同じ parquet には `CellID` 列があり、Heatmap 側は
  `merge(on="CellID")` で正しく突合している。
  **照合できる材料があるのに使っていなかった**

■ 方針

通常時の挙動は変えない。一致（True）と判定不能（None）はどちらも従来どおり進む。
止めるのは **不一致と分かったとき** だけで、そのときは黙って別の画像を出すより
出さないほうが良い。
"""

import time

import pandas as pd
import pytest

from app.services.seurat_bridge import SeuratBridge


def _write(cache_dir, cell_ids, with_cellid=True):
    d = {"mz_100.0000": list(range(len(cell_ids)))}
    if with_cellid:
        d = {"CellID": list(cell_ids), **d}
    pd.DataFrame(d).to_parquet(cache_dir / "expression_matrix.parquet", index=False)
    return cache_dir


CELLS = [f"c{i}" for i in range(8)]


class TestAlignmentVerdict:

    def test_same_order_is_true(self, tmp_path):
        _write(tmp_path, CELLS)
        assert SeuratBridge.expression_row_order_matches(tmp_path, CELLS) is True

    def test_shuffled_order_is_false(self, tmp_path):
        """★ 本丸。長さは同じで並びだけ違う——長さ検査は通ってしまう形。"""
        _write(tmp_path, CELLS)
        assert SeuratBridge.expression_row_order_matches(
            tmp_path, list(reversed(CELLS))) is False

    def test_swapping_two_cells_is_false(self, tmp_path):
        """1 ペア入れ替えただけでも検出すること（端だけ見ていない証明）。"""
        _write(tmp_path, CELLS)
        swapped = list(CELLS)
        swapped[3], swapped[4] = swapped[4], swapped[3]
        assert SeuratBridge.expression_row_order_matches(tmp_path, swapped) is False

    def test_different_length_is_false(self, tmp_path):
        _write(tmp_path, CELLS)
        assert SeuratBridge.expression_row_order_matches(
            tmp_path, CELLS[:3]) is False

    def test_missing_cellid_column_is_undecidable(self, tmp_path):
        """旧い抽出には CellID が無い。判定不能として従来経路に委ねる。"""
        _write(tmp_path, CELLS, with_cellid=False)
        assert SeuratBridge.expression_row_order_matches(tmp_path, CELLS) is None

    def test_missing_parquet_is_undecidable(self, tmp_path):
        assert SeuratBridge.expression_row_order_matches(tmp_path, CELLS) is None

    def test_non_string_ids_compare_by_text(self, tmp_path):
        """parquet 側が数値、plot_data 側が文字列でも同じ並びなら一致とする。"""
        _write(tmp_path, [1, 2, 3])
        assert SeuratBridge.expression_row_order_matches(
            tmp_path, ["1", "2", "3"]) is True


class TestTheCacheDoesNotLie:
    """★★ 最初の実装のバグを固定する。

    当初はキーを `(ファイル署名, 行数)` にして **判定結果そのもの** を
    キャッシュしていた。その結果、並びが違うだけで長さが同じ入力に
    **前回の True を返していた** —— この関数が防ごうとしている
    「長さだけ見る」を、キャッシュ側でやってしまっていた（実測で発覚）。
    """

    def test_a_true_verdict_does_not_leak_to_a_shuffled_input(self, tmp_path):
        _write(tmp_path, CELLS)
        assert SeuratBridge.expression_row_order_matches(tmp_path, CELLS) is True
        assert SeuratBridge.expression_row_order_matches(
            tmp_path, list(reversed(CELLS))) is False, (
            "同じ長さの別の並びに、前回の判定が漏れている")
        # 元に戻したら True に戻ること（False も漏れない）
        assert SeuratBridge.expression_row_order_matches(tmp_path, CELLS) is True

    def test_a_new_file_invalidates_the_cache(self, tmp_path):
        _write(tmp_path, CELLS)
        assert SeuratBridge.expression_row_order_matches(tmp_path, CELLS) is True
        time.sleep(0.01)
        _write(tmp_path, list(reversed(CELLS)))     # 抽出をやり直した想定
        assert SeuratBridge.expression_row_order_matches(tmp_path, CELLS) is False, (
            "parquet を差し替えても古い判定を返している")

    def test_the_parquet_is_not_reread_every_call(self, tmp_path, monkeypatch):
        """★ メモ化の表明: feature 切替のたびに 20 万行を読み直さないこと。

        ver51.3 で消したフッタ再パースの固定費を、ここで戻さない。
        """
        import app.services.seurat_bridge as SB
        _write(tmp_path, CELLS)
        calls = []
        real = SB._read_parquet_columns
        monkeypatch.setattr(
            SB, "_read_parquet_columns",
            lambda entry, cols: (calls.append(tuple(cols)), real(entry, cols))[1])

        for _ in range(5):
            SeuratBridge.expression_row_order_matches(tmp_path, CELLS)
        cellid_reads = [c for c in calls if c == ("CellID",)]
        assert len(cellid_reads) == 1, (
            f"CellID 列を {len(cellid_reads)} 回読んでいる（1 回であるべき）")


class TestCallbackGate:
    """`interactive_deg._expression_alignment_ok` の判断。"""

    def _plot_data(self, cells=CELLS):
        return pd.DataFrame({
            "CellID": list(cells),
            "Sample": ["S1"] * len(cells),
            "SpatialX": list(range(len(cells))),
            "SpatialY": list(range(len(cells))),
        })

    def test_aligned_passes(self, tmp_path):
        from app.callbacks.interactive_deg import _expression_alignment_ok
        _write(tmp_path, CELLS)
        assert _expression_alignment_ok(str(tmp_path), self._plot_data()) is True

    def test_misaligned_is_blocked(self, tmp_path):
        """★ 不一致なら描画を止める（黙って別の画像を出さない）。"""
        from app.callbacks.interactive_deg import _expression_alignment_ok
        _write(tmp_path, list(reversed(CELLS)))
        assert _expression_alignment_ok(str(tmp_path), self._plot_data()) is False

    @pytest.mark.parametrize("cache_dir,df", [
        (None, "plot"),          # cache_dir 未設定
        ("dir", None),           # plot_data 無し
    ])
    def test_undecidable_does_not_block(self, tmp_path, cache_dir, df):
        """★ 通常時の挙動は変えない: 判定材料が無ければ従来どおり進む。"""
        from app.callbacks.interactive_deg import _expression_alignment_ok
        _write(tmp_path, CELLS)
        cd = str(tmp_path) if cache_dir else None
        pdta = self._plot_data() if df else None
        assert _expression_alignment_ok(cd, pdta) is True

    def test_plot_data_without_cellid_does_not_block(self, tmp_path):
        from app.callbacks.interactive_deg import _expression_alignment_ok
        _write(tmp_path, CELLS)
        df = self._plot_data().drop(columns=["CellID"])
        assert _expression_alignment_ok(str(tmp_path), df) is True


class TestBothFeaturePlotPathsAreGated:
    """★ 初回描画と差分更新の **両方** に置くこと。

    片方だけだと「最初は止まるのに、m/z を切り替えると出る」になる。
    """

    def test_the_helper_is_called_from_both_paths(self):
        import ast
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent
               / "app" / "callbacks" / "interactive_deg.py").read_text(encoding="utf-8")
        callers = set()
        tree = ast.parse(src)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(fn):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id == "_expression_alignment_ok"):
                    callers.add(fn.name)
        assert len(callers) >= 2, (
            f"行順の照合が 1 経路にしか無い: {callers}。"
            "初回描画と差分更新の両方を通さないと、"
            "「最初は止まるのに m/z を切り替えると出る」になる")
