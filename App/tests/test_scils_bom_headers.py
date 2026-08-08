"""BOM 付き CSV のヘッダが読めること (ver51.9)。

■ 何が起きていたか

`first_header_and_skipcount` は `encoding="utf-8"` でファイルを開く。
Windows の Excel / SCiLS が書き出す CSV は **UTF-8 BOM 付き**が既定なので、
先頭セルは `m/z` ではなく `﻿m/z` になる。

ver51.8 で「m/z 列が無ければ例外」にしたため、それまで
**「見つからなければ列 0」で偶然正しく動いていた**ピークリストが
読めなくなった (peak-list の 1 列目は m/z なので偶然一致していた)。

さらに悪いのは BOM が **`#` コメント行の先頭に付く**場合で、
`line.startswith("#")` が False になり **コメント行をヘッダとして採用**する。
このときは例外も出ず、列名が丸ごとずれたまま変換が進む。

★ `utf-8-sig` で開けば両方消える。BOM が無いファイルには影響しない。
"""

import numpy as np
import pytest

from app.services.scils_converter import (
    classify_csv_role,
    first_header_and_skipcount,
    _read_peaklist as read_peaklist,
    read_spot_table,
)

BOM = "﻿"


def _write(path, text, bom=True):
    path.write_text((BOM if bom else "") + text, encoding="utf-8")
    return path


PEAKLIST = (
    "m/z,Color,Name\n"
    "104.10699,#ff0000,Choline\n"
    "184.07332,#00ff00,PC headgroup\n"
)

SPOTS = (
    "Spot index,x,y\n"
    "1,0,0\n"
    "2,1,0\n"
    "3,0,1\n"
)


class TestHeaderIsNotPolluted:
    def test_first_header_cell_has_no_bom(self, tmp_path):
        """★ 先頭セルが `m/z` として読めること。"""
        p = _write(tmp_path / "peaks.csv", PEAKLIST)
        headers, _, _ = first_header_and_skipcount(p)
        assert headers[0] == "m/z", f"BOM が残っている: {headers!r}"

    def test_comment_line_is_still_skipped(self, tmp_path):
        """★ BOM が `#` 行に付いても、それをヘッダにしない。

        こちらは例外が出ないぶん質が悪い。列が丸ごとずれたまま完走する。
        """
        p = _write(tmp_path / "peaks.csv", "# SCiLS Lab export\n" + PEAKLIST)
        headers, _, skip = first_header_and_skipcount(p)
        assert skip == 1, f"コメント行を飛ばせていない (skip={skip})"
        assert headers[0] == "m/z", headers


class TestPeaklistStillReadable:
    def test_bom_peaklist_reads(self, tmp_path):
        """★ ver51.8 の「m/z 列必須」で読めなくなった経路。"""
        p = _write(tmp_path / "peaks.csv", PEAKLIST)
        mz, names = read_peaklist(p)
        assert np.allclose(mz, [104.10699, 184.07332]), mz
        assert names == ["Choline", "PC headgroup"], names

    def test_without_bom_is_unchanged(self, tmp_path):
        """過剰修正の番人: BOM 無しの挙動を変えない。"""
        p = _write(tmp_path / "peaks.csv", PEAKLIST, bom=False)
        mz, names = read_peaklist(p)
        assert np.allclose(mz, [104.10699, 184.07332])
        assert names == ["Choline", "PC headgroup"]

    def test_missing_mz_column_still_raises(self, tmp_path):
        """過剰修正の番人: BOM を剥いでも m/z 列が無ければ例外のまま。"""
        p = _write(tmp_path / "peaks.csv", "Index,Color,Name\n1,#fff,X\n")
        with pytest.raises(ValueError, match="m/z"):
            read_peaklist(p)


class TestSpotTableColumnsResolve:
    """★ spot テーブルは `_find_col` が「該当なし → 0」で黙って続ける。

    BOM で `Spot index` が一致しないと **x 列を spot index として読む**。
    座標の割り当てが静かにずれるので、画像が崩れるまで気づけない。
    """

    def test_spot_index_column_is_found(self, tmp_path):
        p = _write(tmp_path / "spots.csv", SPOTS)
        idx, x, y = read_spot_table(p)
        assert list(idx) == [1, 2, 3], f"spot index がずれている: {idx}"
        assert list(x) == [0.0, 1.0, 0.0]
        assert list(y) == [0.0, 0.0, 1.0]


class TestRoleClassification:
    def test_spot_like_is_recognised_with_bom(self, tmp_path):
        p = _write(tmp_path / "spots.csv", SPOTS)
        assert classify_csv_role(p) == "spot_like"
