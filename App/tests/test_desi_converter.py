"""DESI 登録データの Excel/CSV 対応（desi_converter）のテスト。

`.csv` / `.xlsx` を内部で正規 `.txt`（タブ区切り・同一レイアウト）へ変換し、
既存リーダ (read_desi_roi_list / validate_msi_file / list_msi_files) が
透過的に処理できることを検証する。test_desi_roi.py の構造を踏襲。
"""

import csv as _csv
import os
import time
from pathlib import Path

from openpyxl import Workbook

import app.services.data_manager as dm
from app.services import desi_converter as dc

# 先頭4行ヘッダ（行1=空 / 行2=metabolite番号 / 行3=pre_masses / 行4=m/z）。
# m/z は精度確認のため小数を含める。
_HEADER = [
    [],
    ["", "", "", "1", "2"],
    ["", "", "", "100.0", "200.0"],
    ["", "", "", "146.1216", "760.5851"],
]


def _write_csv(folder, name, data_rows, header=_HEADER):
    p = Path(folder) / name
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        for r in header:
            w.writerow(r)
        for r in data_rows:
            w.writerow(r)
    return p


def _write_xlsx(folder, name, data_rows, header=_HEADER):
    wb = Workbook()
    ws = wb.active
    for r in header:
        ws.append(list(r))
    for r in data_rows:
        ws.append(list(r))
    p = Path(folder) / name
    wb.save(p)
    return p


# --- ROI 検出（CSV / XLSX） ------------------------------------------------

def test_csv_roi_none(tmp_path):
    # 末尾が line/pixel の数値連番のみ → ROI 無し
    data = [[i, i * 1.0, i * 2.0, 100 + i, 200 + i, 1, i] for i in range(1, 9)]
    _write_csv(tmp_path, "s.csv", data)
    txt = dc.convert_desi_to_txt(tmp_path / "s.csv", tmp_path / "s.txt")
    assert dm.read_desi_roi_list(str(txt)) == []


def test_csv_roi_detected(tmp_path):
    labels = ["LN", "TDLN", "ROI"]
    data = [[i, i * 1.0, i * 2.0, 100 + i, 200 + i, labels[i % 3]]
            for i in range(1, 10)]
    _write_csv(tmp_path, "s.csv", data)
    txt = dc.convert_desi_to_txt(tmp_path / "s.csv", tmp_path / "s.txt")
    assert dm.read_desi_roi_list(str(txt)) == ["LN", "ROI", "TDLN"]


def test_xlsx_roi_detected(tmp_path):
    labels = ["A", "B"]
    data = [[i, i * 1.0, i * 2.0, 100 + i, 200 + i, labels[i % 2]]
            for i in range(1, 10)]
    _write_xlsx(tmp_path, "s.xlsx", data)
    txt = dc.convert_desi_to_txt(tmp_path / "s.xlsx", tmp_path / "s.txt")
    assert dm.read_desi_roi_list(str(txt)) == ["A", "B"]


def test_csv_label_with_comma_preserved(tmp_path):
    # ラベル内のカンマはクオートされ、1 セルとして保持される
    labels = ["LN, left", "TDLN"]
    data = [[i, i * 1.0, i * 2.0, 100 + i, 200 + i, labels[i % 2]]
            for i in range(1, 10)]
    _write_csv(tmp_path, "s.csv", data)
    txt = dc.convert_desi_to_txt(tmp_path / "s.csv", tmp_path / "s.txt")
    assert dm.read_desi_roi_list(str(txt)) == ["LN, left", "TDLN"]
    # 変換後 .txt のいずれかのデータ行で、カンマ入りラベルが
    # タブで分割されず 1 セルとして保持されていること
    data_lines = txt.read_text(encoding="utf-8").splitlines()[4:]
    assert any("LN, left" in ln.split("\t") for ln in data_lines)


# --- m/z 数値精度 ----------------------------------------------------------

def test_csv_mz_precision_exact(tmp_path):
    data = [[i, i * 1.0, i * 2.0, 1000 + i, 2000 + i] for i in range(1, 4)]
    _write_csv(tmp_path, "s.csv", data)
    txt = dc.convert_desi_to_txt(tmp_path / "s.csv", tmp_path / "s.txt")
    row4 = txt.read_text(encoding="utf-8").splitlines()[3].split("\t")
    assert "146.1216" in row4 and "760.5851" in row4


def test_xlsx_mz_precision_no_exponent(tmp_path):
    # m/z を「数値セル」として書いても、指数表記・桁落ちせず復元される
    header = [
        [],
        ["", "", "", 1, 2],
        ["", "", "", 100.0, 200.0],
        ["", "", "", 146.1216, 760.5851],
    ]
    data = [[i, i * 1.0, i * 2.0, 1000 + i, 2000 + i] for i in range(1, 4)]
    _write_xlsx(tmp_path, "s.xlsx", data, header=header)
    txt = dc.convert_desi_to_txt(tmp_path / "s.xlsx", tmp_path / "s.txt")
    row4 = txt.read_text(encoding="utf-8").splitlines()[3].split("\t")
    assert "146.1216" in row4 and "760.5851" in row4
    # 整数値の x/y/ID は "1.0" でなく "1" 形式
    first_data = txt.read_text(encoding="utf-8").splitlines()[4].split("\t")
    assert first_data[0] == "1"


# --- ragged 行 / 空1行目 / 検証 -------------------------------------------

def test_validate_msi_file_on_converted(tmp_path):
    data = [[i, i * 1.0, i * 2.0, 100 + i, 200 + i] for i in range(1, 6)]
    _write_csv(tmp_path, "s.csv", data)
    txt = dc.convert_desi_to_txt(tmp_path / "s.csv", tmp_path / "s.txt")
    assert dm.validate_msi_file(str(txt))["valid"] is True


def test_ragged_rows_preserved(tmp_path):
    # 行ごとに列数が異なる（末尾0省略）入力でも壊れない
    data = [
        [1, 1.0, 2.0, 101, 201],
        [2, 2.0, 4.0, 102],          # 末尾欠落（ragged）
        [3, 3.0, 6.0, 103, 203, 7],  # 余分列
    ]
    _write_csv(tmp_path, "s.csv", data)
    txt = dc.convert_desi_to_txt(tmp_path / "s.csv", tmp_path / "s.txt")
    # 5 行目（最初のデータ行）が 4 列以上 → validate OK
    assert dm.validate_msi_file(str(txt))["valid"] is True
    # ROI 検出が例外を出さない
    assert isinstance(dm.read_desi_roi_list(str(txt)), list)


def test_empty_first_line_preserved(tmp_path):
    data = [[i, i * 1.0, i * 2.0, 100 + i, 200 + i] for i in range(1, 4)]
    _write_csv(tmp_path, "s.csv", data)
    txt = dc.convert_desi_to_txt(tmp_path / "s.csv", tmp_path / "s.txt")
    assert txt.read_text(encoding="utf-8").splitlines()[0] == ""


# --- 冪等性 / 再変換 -------------------------------------------------------

def test_idempotent_and_reconvert(tmp_path):
    data_v1 = [[i, i * 1.0, i * 2.0, 100 + i, 200 + i] for i in range(1, 4)]
    src = _write_csv(tmp_path, "s.csv", data_v1)
    t = dc.ensure_desi_txt("s", tmp_path)
    assert t is not None
    content_v1 = t.read_text(encoding="utf-8")
    mtime1 = t.stat().st_mtime_ns

    # 再呼び出し → 再変換されない（mtime 不変）
    dc.ensure_desi_txt("s", tmp_path)
    assert t.stat().st_mtime_ns == mtime1

    # ソースを更新（内容変更 + mtime 前進）→ 再変換される
    data_v2 = [[i, i * 1.0, i * 2.0, 500 + i, 900 + i] for i in range(1, 4)]
    _write_csv(tmp_path, "s.csv", data_v2)
    future = time.time() + 5
    os.utime(src, (future, future))
    dc.ensure_desi_txt("s", tmp_path)
    assert t.read_text(encoding="utf-8") != content_v1


# --- list_msi_files / ユーザー .txt 優先 -----------------------------------

def test_list_msi_files_includes_and_dedups(tmp_path):
    data = [[1, 1.0, 2.0, 101, 201]]
    _write_csv(tmp_path, "a.csv", data)
    _write_xlsx(tmp_path, "b.xlsx", data)
    (tmp_path / "c.txt").write_text(
        "h1\nh2\nh3\nh4\n1\t2\t3\t4\n", encoding="utf-8")
    # 同一 stem に .txt と .csv → .txt 優先で 1 件のみ
    _write_csv(tmp_path, "c.csv", data)
    assert dm.list_msi_files(str(tmp_path)) == ["a", "b", "c"]


def test_user_txt_not_overwritten(tmp_path):
    user_txt = tmp_path / "s.txt"
    user_txt.write_text(
        "h1\nh2\nh3\nh4\n1\t2\t3\t4\n", encoding="utf-8")
    _write_csv(tmp_path, "s.csv", [[1, 1.0, 2.0, 101, 201]])
    out = dc.ensure_desi_txt("s", tmp_path)
    assert Path(out) == user_txt
    # ユーザーの内容が保持されている（変換で上書きされない）
    assert user_txt.read_text(encoding="utf-8").startswith("h1")


# --- 統合: read_desi_roi_list の自動変換 -----------------------------------

def test_read_roi_auto_converts(tmp_path):
    labels = ["LN", "TDLN", "ROI"]
    data = [[i, i * 1.0, i * 2.0, 100 + i, 200 + i, labels[i % 3]]
            for i in range(1, 10)]
    _write_csv(tmp_path, "s.csv", data)
    # .txt は存在しないが自動変換して読む
    rois = dm.read_desi_roi_list(str(tmp_path / "s.txt"))
    assert rois == ["LN", "ROI", "TDLN"]
    assert (tmp_path / "s.txt").is_file()


# --- 統合: prepare_desi_data_folder（書込可能） ----------------------------

def test_prepare_data_folder_writable(tmp_path):
    data = [[i, i * 1.0, i * 2.0, 100 + i, 200 + i] for i in range(1, 4)]
    _write_csv(tmp_path, "a.csv", data)
    (tmp_path / "b.txt").write_text(
        "h1\nh2\nh3\nh4\n1\t2\t3\t4\n", encoding="utf-8")
    out = dc.prepare_desi_data_folder(str(tmp_path), ["a", "b"])
    assert Path(out) == tmp_path           # 書込可能 → 同フォルダ
    assert (tmp_path / "a.txt").is_file()   # csv が変換された
    assert (tmp_path / "b.txt").read_text(encoding="utf-8").startswith("h1")
