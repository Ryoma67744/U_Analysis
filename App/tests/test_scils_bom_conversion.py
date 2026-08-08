"""BOM 付き Intensity CSV の変換が最後まで通ること (ver51.9)。

■ なぜ要るか

A-6 で `first_header_and_skipcount` を `utf-8-sig` にした結果、
**ヘッダ名は BOM 無し**になった。ところが Intensity CSV 本体を読むのは
polars / pyarrow で、そのエンジンが BOM を剥ぐかどうかは別問題。
剥がなければ一時 Parquet の 1 列目は `﻿m/z` のままで、

    mz_col_name = int_headers[0]          # → "m/z" (BOM 無し)
    pf.read(columns=[mz_col_name])        # → 一時 Parquet には無い

となり、**BOM 付き CSV の変換が丸ごと落ちる**。A-6 が「読めるようにする」
修正なのに、別経路で読めなくしては意味が無い。

実測: pyarrow は BOM を自前で剥ぐ。**polars はこの環境に無く未検証**で、
しかも既定エンジンはそちら。

★ そこで「エンジンが BOM を剥ぐかどうかに依存しない」ことを固定する。
  - m/z 列は **位置**で解決する（名前で引かない）
  - polars の dtype 指定は **位置指定**で渡す（列名キーにしない）
"""

import os

import numpy as np
import pytest

pytest.importorskip("pyarrow")

BOM = "﻿"


def _make_folder(tmp_path, bom=True):
    """最小の SCiLS 一式 (Intensity 3 m/z × 6 spot + Spot 座標)。

    `classify_csv_role` は `Spot NNN` 列が 5 本以上あるものを Intensity と見なす。
    """
    pre = BOM if bom else ""
    tmp_path.mkdir(parents=True, exist_ok=True)
    spots = [f"Spot {i}" for i in range(1, 7)]
    rows = [
        ("200.5", [1, 2, 3, 4, 5, 6]),
        ("100.25", [7, 8, 9, 10, 11, 12]),
        ("300.75", [13, 14, 15, 16, 17, 18]),
    ]
    (tmp_path / "Data_Intensities.csv").write_text(
        pre + ",".join(["m/z"] + spots) + "\n"
        + "".join(f"{mz}," + ",".join(str(v) for v in vals) + "\n"
                  for mz, vals in rows),
        encoding="utf-8")
    (tmp_path / "Data_Spots.csv").write_text(
        pre + "Spot index,x,y\n"
        + "".join(f"{i},{(i - 1) % 3},{(i - 1) // 3}\n" for i in range(1, 7)),
        encoding="utf-8")
    return tmp_path


def _convert(folder, out):
    from app.services.scils_converter import convert_scils_to_parquet
    return convert_scils_to_parquet(str(folder), str(out), organize=False)


@pytest.fixture(autouse=True)
def _force_pyarrow(monkeypatch):
    """この環境に polars は無いが、入っている環境でも両経路を等しく見たい。"""
    monkeypatch.setenv("SCILS_NO_POLARS", "1")


class TestBomIntensityConverts:
    def test_conversion_succeeds(self, tmp_path):
        """★ BOM 付きで最後まで通ること。"""
        folder = _make_folder(tmp_path / "in", bom=True)
        out = tmp_path / "out.parquet"
        _convert(folder, out)
        assert out.exists(), "BOM 付き Intensity CSV の変換が完了していない"

    def test_values_match_the_no_bom_case(self, tmp_path):
        """★ 過剰修正の番人: BOM の有無で中身が変わらないこと。"""
        import pyarrow.parquet as pq

        a = _make_folder(tmp_path / "with_bom", bom=True)
        b = _make_folder(tmp_path / "no_bom", bom=False)
        out_a, out_b = tmp_path / "a.parquet", tmp_path / "b.parquet"
        _convert(a, out_a)
        _convert(b, out_b)

        ta = pq.read_table(str(out_a))
        tb = pq.read_table(str(out_b))
        assert ta.schema.names == tb.schema.names, (
            f"BOM の有無で列名が変わっている:\n  {ta.schema.names}\n  {tb.schema.names}")
        assert ta.to_pydict() == tb.to_pydict()

    def test_mz_columns_carry_no_bom(self, tmp_path):
        """出力の列名に BOM が漏れないこと（下流の m/z 解析が全滅する）。"""
        import pyarrow.parquet as pq

        folder = _make_folder(tmp_path / "in", bom=True)
        out = tmp_path / "out.parquet"
        _convert(folder, out)
        names = pq.read_table(str(out)).schema.names
        assert not any(BOM in n for n in names), names
        # m/z 昇順にソートされて列になる
        assert names[:3] == ["id", "x", "y"], names
        assert names[3:6] == ["100.250000", "200.500000", "300.750000"], names


class TestEngineMayKeepTheBom:
    """★ ここが本題。**エンジンが BOM を剥がなかった場合**を直接作る。

    polars がこの環境に無いので、`_csv_to_temp_parquet` を差し替えて
    「1 列目が `﻿m/z` のままの一時 Parquet」を書かせ、
    後段が名前ではなく位置で m/z 列を解決していることを確かめる。
    """

    def test_pipeline_survives_a_bom_prefixed_temp_column(self, tmp_path, monkeypatch):
        import pyarrow as pa
        import pyarrow.parquet as pq
        from app.services import scils_converter as SC

        real = SC._csv_to_temp_parquet

        def _bom_keeping(intensity_path, int_headers, delim, skip, temp_parquet):
            real(intensity_path, int_headers, delim, skip, temp_parquet)
            t = pq.read_table(str(temp_parquet))
            names = list(t.schema.names)
            names[0] = BOM + names[0]          # エンジンが剥がなかった状況
            pq.write_table(t.rename_columns(names), str(temp_parquet))

        monkeypatch.setattr(SC, "_csv_to_temp_parquet", _bom_keeping)

        folder = _make_folder(tmp_path / "in", bom=True)
        out = tmp_path / "out.parquet"
        SC.convert_scils_to_parquet(str(folder), str(out), organize=False)

        assert out.exists(), \
            "エンジンが BOM を剥がないと変換が落ちる（m/z 列を名前で引いている）"
        names = pq.read_table(str(out)).schema.names
        assert names[3:6] == ["100.250000", "200.500000", "300.750000"], names


class TestPolarsDtypesArePositional:
    """polars 経路の dtype 指定が **列名キーでない**こと。

    列名キーだと、エンジンが BOM を剥がなかったときに 1 列目の指定が
    黙って無視され、**m/z 列が文字列として推論される**。
    polars がこの環境に無いため、渡している形を直接見る。
    """

    def test_schema_overrides_is_a_sequence(self):
        import ast
        from pathlib import Path

        src = (Path(__file__).resolve().parent.parent
               / "app" / "services" / "scils_converter.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_csv_to_temp_parquet")

        assigns = [n for n in ast.walk(fn)
                   if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "schema_overrides"
                           for t in n.targets)]
        assert assigns, "schema_overrides の組み立てが見つからない"
        for a in assigns:
            assert not isinstance(a.value, ast.DictComp), (
                "schema_overrides を列名キーの dict で渡している。"
                "エンジンが BOM を剥がないと 1 列目の dtype 指定が黙って無視される")
