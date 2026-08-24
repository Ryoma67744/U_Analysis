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

        # 差し替えは *args/**kwargs で受ける。本物のシグネチャに引数が増えるたび
        # （ver60.0 の store_float32 など）ここが TypeError で落ちると、
        # BOM とは無関係な理由でこのテストが赤くなる。
        def _bom_keeping(*args, **kwargs):
            real(*args, **kwargs)
            temp_parquet = args[4]
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


class TestPolarsPathActuallyRuns:
    """polars 経路が **実際に完走し、m/z が数値として読める**こと。

    ★ ver55.0: ここには `schema_overrides` が dict 内包表記でないことを AST で
    検査する `TestPolarsDtypesArePositional` があった。そのテストは合否が正しさと
    逆相関していた:
      - 正しい修正（列名キーの dict）は `ast.DictComp` なので **落ちる**
      - `dict(zip(...))` / `dict.fromkeys(...)` は `ast.Call` なので **通る**
        （中身は同じ列名キーの dict なのに）
    守っているつもりの性質を実際には守っておらず、一方で
    `pl.scan_csv` が list を拒否して `TypeError: expected 'schema_overrides' dict,
    found 'list'` で即死する **本物の欠陥は検出できなかった**（polars を一度も
    実行しないため）。構文ではなく振る舞いを固定する。

    守りたい不変条件は 2 つ:
      1. polars 経路が例外なく完走する（= scan_csv が受け付ける形で渡している）
      2. m/z 列が String に推論されず Float64 になる（BOM の有無に依存しない）
      3. 強度列が `store_float32` の指定どおりの幅になる（★ ver60.0）

    3 は 1・2 より強い踏み絵になっている。dtype 指定が丸ごと無視されると
    polars の推論で全列 Float64 になり、m/z 列だけを見ても気づけないが、
    強度列が float32 にならないことで検出できる。
    """

    @pytest.fixture(autouse=True)
    def _allow_polars(self, monkeypatch):
        """モジュール全体の `_force_pyarrow` を打ち消し、polars 経路を通す。"""
        monkeypatch.delenv("SCILS_NO_POLARS", raising=False)

    @pytest.mark.parametrize("store_float32", [True, False])
    @pytest.mark.parametrize("bom_in_file,bom_in_headers", [
        (True, False),   # ファイルに BOM。int_headers は utf-8-sig 読みなのでクリーン
        (False, True),   # 逆向き: int_headers 側だけ BOM 付き（列名キー実装の踏み絵）
        (False, False),
    ])
    def test_mz_column_is_float(self, tmp_path, bom_in_file, bom_in_headers,
                                store_float32):
        pytest.importorskip("polars")
        import pyarrow.parquet as pq
        from app.services import scils_converter as SC

        folder = _make_folder(tmp_path / "in", bom=bom_in_file)
        intensity = folder / "Data_Intensities.csv"
        headers, delim, skip = SC.first_header_and_skipcount(intensity)
        assert not headers[0].startswith(BOM), "utf-8-sig 読みなので BOM は落ちているはず"
        if bom_in_headers:
            headers = [BOM + headers[0]] + headers[1:]

        temp = tmp_path / "temp.parquet"
        SC._csv_to_temp_parquet(intensity, headers, delim, skip, temp,
                                store_float32=store_float32)

        schema = pq.read_schema(str(temp))
        # m/z 列は store_float32 に関わらず必ず float64。列名と mz_sorted メタデータの
        # 精度がこの列で決まるので、float32 に落とすと m/z そのものが丸まる。
        assert str(schema.field(0).type) == "double", (
            f"m/z 列が {schema.field(0).type} になっている。dtype 指定が効いていない"
        )
        want = "float" if store_float32 else "double"
        bad = [f.name for f in list(schema)[1:] if str(f.type) != want]
        assert not bad, (
            f"強度列は store_float32={store_float32} なら全て {want} のはず。"
            f"違う型の列: {bad[:5]}"
        )
        # Phase B は spot 列を **名前で** 読む (`pf.read(columns=spot_cols)`) ので、
        # 一時 Parquet の列名が int_headers と一致することが要件。
        # 列数と同数の dict はキーが一致しないとき index 適用にフォールバックし、
        # そのとき列名も dict のキー（= int_headers）に揃えられる。だから
        # 「ファイル側に BOM がある」「int_headers 側に BOM がある」のどちらでも
        # この一致は保たれる。
        assert list(schema.names) == list(headers), (
            "一時 Parquet の列名が int_headers と一致しない。"
            "Phase B の名前引きが KeyError になる"
        )
