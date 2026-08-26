r"""Excel のシート名が衝突しないこと (ver51.9 / B-3)。

■ 何が起きていたか

DESI エクスポートはサンプルごとに 1 シートを作るが、シート名を

    sheet_name = stem[:31]        # Excel の 31 文字制限

とするだけで**衝突対策が無い**。openpyxl は同名シートへの `to_excel` を
**例外にせず上書き**するので、先頭 31 文字が同じ 2 サンプルは
**1 枚のシートに混ざって**出る。行数も内容も静かに壊れる。

MSI の測定ファイル名は
`20260807_MouseBrain_Section01_Neg_DHB_run1` のように長い共通接頭辞を持ちやすく、
31 文字の切り詰めで衝突するのはむしろ普通。

さらにシート名として使えない文字 `[ ] : * ? / \` を含むファイル名だと、
openpyxl が例外を投げて**エクスポート全体が落ちる**（1 サンプルのせいで全滅）。

★ ここで固定するのは 3 点:
   - 別サンプルは必ず別シートになること
   - 各シートに**自分の**データだけが入ること
   - 禁止文字・空名でもエクスポートが完走すること
"""

import pytest

from app.callbacks.interactive_data_export import _unique_sheet_name


class TestCollision:
    def test_long_names_do_not_collide(self):
        """★ 先頭 31 文字が同じでも別シートになること。"""
        used = {}
        a = "20260807_MouseBrain_Section01_Neg_DHB_run1"
        b = "20260807_MouseBrain_Section01_Neg_DHB_run2"
        n1 = _unique_sheet_name(a, used)
        n2 = _unique_sheet_name(b, used)
        assert n1 != n2, f"{a!r} と {b!r} が同じシート名 {n1!r} になった"

    def test_result_fits_excel_limit(self):
        used = {}
        for i in range(30):
            name = _unique_sheet_name("A" * 40 + str(i), used)
            assert 1 <= len(name) <= 31, (i, name, len(name))

    def test_many_collisions_stay_unique(self):
        """★ 30 サンプルまとめて投入しても全部ユニークであること。"""
        used = {}
        names = [_unique_sheet_name("Sample_" + "x" * 30 + f"_{i}", used)
                 for i in range(30)]
        assert len(set(names)) == 30, sorted(names)

    def test_exact_duplicate_names_are_separated(self):
        """同名ファイルが別フォルダから来た場合も潰さない。"""
        used = {}
        assert _unique_sheet_name("S1", used) != _unique_sheet_name("S1", used)


class TestForbiddenCharacters:
    """Excel のシート名に使えない文字 / 予約された形を弾くこと。"""

    @pytest.mark.parametrize("bad", [
        "a[b]c", "a:b", "a*b", "a?b", "a/b", "a\\b",
    ])
    def test_forbidden_characters_removed(self, bad):
        name = _unique_sheet_name(bad, {})
        assert not any(ch in name for ch in "[]:*?/\\"), name

    def test_leading_or_trailing_apostrophe_removed(self):
        """先頭・末尾のアポストロフィは Excel が拒否する。"""
        name = _unique_sheet_name("'quoted'", {})
        assert not name.startswith("'") and not name.endswith("'"), name

    def test_empty_name_gets_a_fallback(self):
        """空や記号だけでも必ず有効な名前を返すこと。"""
        for src in ("", "   ", "///", "[]"):
            name = _unique_sheet_name(src, {})
            assert name.strip(), f"{src!r} → {name!r}"
            assert 1 <= len(name) <= 31


class TestOpenpyxlAccepts:
    """★ 実際に openpyxl が受け付けること（規則の思い込みを潰す）。"""

    def test_generated_names_are_writable(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        import pandas as pd

        stems = [
            "20260807_MouseBrain_Section01_Neg_DHB_run1",
            "20260807_MouseBrain_Section01_Neg_DHB_run2",
            "weird[]:*?/\\name",
            "",
            "S1", "S1",
        ]
        used = {}
        out = tmp_path / "x.xlsx"
        with pd.ExcelWriter(out, engine="openpyxl") as w:
            for i, s in enumerate(stems):
                pd.DataFrame({"v": [i]}).to_excel(
                    w, sheet_name=_unique_sheet_name(s, used),
                    header=False, index=False)

        wb = openpyxl.load_workbook(out)
        assert len(wb.sheetnames) == len(stems), wb.sheetnames
        # 各シートに自分の値だけが入っていること（上書き混入の検出）
        for i, sn in enumerate(wb.sheetnames):
            ws = wb[sn]
            assert ws.max_row == 1, f"{sn}: {ws.max_row} 行ある（混ざっている）"
            assert ws.cell(1, 1).value == i, (sn, ws.cell(1, 1).value)


class TestDesiExportDoesNotMixSamples:
    """★ DESI エクスポート本体を実際に走らせる。

    ヘルパを足しても呼ばなければ意味が無い。しかも「呼んでいるか」を AST で見る
    番人は**元の欠陥を捕まえられない** — 元コードは `sheet_name = stem[:31]` と
    いったん変数へ入れてから渡していたので、呼び出し式だけ見ても区別が付かない。
    出力ファイルの中身で見る。
    """

    # 先頭 31 文字が同じ 2 サンプル（`..._Neg_DHB_run1` / `run2` は 32 文字目で分岐）
    STEM_A = "20260807_MouseBrain_Section01_Neg_DHB_run1"
    STEM_B = "20260807_MouseBrain_Section01_Neg_DHB_run2"

    @staticmethod
    def _write_desi(folder, stem, value):
        """最小の DESI .txt（ヘッダ 1 行 + データ 2 行、x/y は 2・3 列目）。"""
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{stem}.txt").write_text(
            "name\tx\ty\tintensity\n"
            f"{stem}_p1\t1.0\t1.0\t{value}\n"
            f"{stem}_p2\t2.0\t2.0\t{value}\n",
            encoding="utf-8")

    def test_two_samples_get_two_sheets(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        from collections import OrderedDict
        from app.callbacks.interactive_data_export import _export_desi

        folder = tmp_path / "data"
        self._write_desi(folder, self.STEM_A, 11)
        self._write_desi(folder, self.STEM_B, 22)

        lookups = OrderedDict({"Harmony": {
            (self.STEM_A, 1.0, 1.0): "0", (self.STEM_A, 2.0, 2.0): "1",
            (self.STEM_B, 1.0, 1.0): "2", (self.STEM_B, 2.0, 2.0): "3",
        }})

        data, _fn = _export_desi(str(folder), lookups)
        import io
        wb = openpyxl.load_workbook(data)

        assert len(wb.sheetnames) == 2, (
            f"サンプル 2 つに対しシートが {len(wb.sheetnames)} 枚しかない。"
            f"31 文字で衝突して上書きされている: {wb.sheetnames}")

    def test_no_sample_is_silently_lost(self, tmp_path):
        """★ 上書きで消えるサンプルが無いこと。

        ここが「黙って間違う」の本体。衝突すると openpyxl は例外を出さず
        後勝ちで**上書き**するので、先に書いたサンプルの行は
        1 行も残らずブックから消える。それでもエクスポートは成功扱いになる。
        (「1 シートに混ざる」ではなく「片方が丸ごと無くなる」が実際の挙動)
        """
        openpyxl = pytest.importorskip("openpyxl")
        from collections import OrderedDict
        from app.callbacks.interactive_data_export import _export_desi

        folder = tmp_path / "data"
        self._write_desi(folder, self.STEM_A, 11)
        self._write_desi(folder, self.STEM_B, 22)

        lookups = OrderedDict({"Harmony": {
            (self.STEM_A, 1.0, 1.0): "0", (self.STEM_A, 2.0, 2.0): "1",
            (self.STEM_B, 1.0, 1.0): "2", (self.STEM_B, 2.0, 2.0): "3",
        }})

        import io
        data, _ = _export_desi(str(folder), lookups)
        wb = openpyxl.load_workbook(data)

        seen = set()
        for sn in wb.sheetnames:
            ws = wb[sn]
            names = [ws.cell(r, 1).value for r in range(2, ws.max_row + 1)]
            stems = {str(n).rsplit("_p", 1)[0] for n in names if n}
            assert len(stems) <= 1, \
                f"シート {sn!r} に複数サンプルの行が入っている: {stems}"
            seen |= stems

        assert seen == {self.STEM_A, self.STEM_B}, (
            f"サンプルが消えている。ブックにあるのは {seen}、"
            f"期待は {{{self.STEM_A!r}, {self.STEM_B!r}}}")

    def test_forbidden_characters_do_not_abort_the_export(self, tmp_path):
        """★ 1 サンプルの名前のせいで export 全体が落ちないこと。"""
        openpyxl = pytest.importorskip("openpyxl")
        from collections import OrderedDict
        from app.callbacks.interactive_data_export import _export_desi

        folder = tmp_path / "data"
        self._write_desi(folder, "run[1]", 1)
        self._write_desi(folder, "normal", 2)

        lookups = OrderedDict({"Harmony": {("run[1]", 1.0, 1.0): "0"}})

        import io
        data, _ = _export_desi(str(folder), lookups)
        wb = openpyxl.load_workbook(data)
        # ★ ver52.5: 検査対象は「両方のサンプルがシートとして出ること」。
        #   従来は `len(sheetnames) == 2` と書いていたが、これは総数を
        #   数えているだけで、このテストの名前が言っている性質ではない。
        #   ver52.5 ③ で「解析のサンプル名と照合できなかったサンプル」を
        #   Skipped シートに報告するようにしたため総数が 3 になった
        #   （この lookup には "normal" が無いので、そのシートのクラスタ列は
        #   全行空になる ——報告されるのが正しい）。
        data_sheets = [s for s in wb.sheetnames if s not in ("Skipped", "Conditions")]
        assert len(data_sheets) == 2, wb.sheetnames
        assert "normal" in data_sheets
        assert any(s.startswith("run") for s in data_sheets), data_sheets
