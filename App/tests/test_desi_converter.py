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


# --- 新形式 (1行ヘッダ・列名=化合物名) ------------------------------------

_NAMED_COLS = ["x", "y", "Acetylcholine_15_10", "Adenosine-POS_32_18", "GSH-POS_20_15"]


def _named_data(n=6):
    return [[14.5 + i, -2.5, 78206 + i, 19895 + i, 100 + i] for i in range(n)]


def _write_named_csv(folder, name, columns, data_rows):
    p = Path(folder) / name
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(columns)
        for r in data_rows:
            w.writerow(r)
    return p


def _write_named_xlsx(folder, name, columns, data_rows):
    wb = Workbook()
    ws = wb.active
    ws.append(list(columns))
    for r in data_rows:
        ws.append(list(r))
    p = Path(folder) / name
    wb.save(p)
    return p


def test_named_csv_reshape_basic(tmp_path):
    _write_named_csv(tmp_path, "s.csv", _NAMED_COLS, _named_data())
    txt = dc.convert_desi_to_txt(tmp_path / "s.csv", tmp_path / "s.txt")
    lines = txt.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ""           # 1行目空
    assert lines[3] == ""           # 4行目空（Q3なし）
    names = [c for c in lines[2].split("\t") if c]   # 3行目=化合物名
    assert names == ["Acetylcholine", "Adenosine-POS", "GSH-POS"]
    first = lines[4].split("\t")    # 5行目=最初のデータ行
    assert first[0] == "1"          # 自動採番ID
    assert first[1] == "14.5"       # x
    assert first[2] == "-2.5"       # y
    assert len(first) == 1 + 2 + 3  # id + x,y + 3強度


def test_named_xlsx_reshape_and_validate(tmp_path):
    _write_named_xlsx(tmp_path, "s.xlsx", _NAMED_COLS, _named_data())
    txt = dc.convert_desi_to_txt(tmp_path / "s.xlsx", tmp_path / "s.txt")
    lines = txt.read_text(encoding="utf-8").splitlines()
    names = [c for c in lines[2].split("\t") if c]
    assert names == ["Acetylcholine", "Adenosine-POS", "GSH-POS"]
    assert dm.validate_msi_file(str(txt))["valid"] is True


def test_compound_name_before_first_underscore(tmp_path):
    cols = ["x", "y", "Adenosine-POS_32_18", "GSH-POS_20_15", "Acetylcholine_15_10"]
    _write_named_csv(tmp_path, "s.csv", cols, [[1.0, 2.0, 10, 20, 30]])
    txt = dc.convert_desi_to_txt(tmp_path / "s.csv", tmp_path / "s.txt")
    names = [c for c in txt.read_text(encoding="utf-8").splitlines()[2].split("\t") if c]
    assert names == ["Adenosine-POS", "GSH-POS", "Acetylcholine"]


def test_named_with_roi_column(tmp_path):
    cols = ["x", "y", "Creatine_13_10", "Glutamine_12_18", "ROI"]
    labels = ["Tumor", "Normal"]
    data = [[float(i), 0.0, 1000 + i, 2000 + i, labels[i % 2]] for i in range(8)]
    _write_named_csv(tmp_path, "s.csv", cols, data)
    txt = dc.convert_desi_to_txt(tmp_path / "s.csv", tmp_path / "s.txt")
    lines = txt.read_text(encoding="utf-8").splitlines()
    names = [c for c in lines[2].split("\t") if c]   # ROI は特徴量に含めない
    assert names == ["Creatine", "Glutamine"]
    first = lines[4].split("\t")
    assert len(first) == 1 + 2 + 2 + 1   # id + x,y + 2強度 + ROI
    assert first[-1] in labels
    # 末尾ROIラベルが保持され、read_desi_roi_list が領域名を返す
    assert dm.read_desi_roi_list(str(txt)) == ["Normal", "Tumor"]


def test_named_roi_auto_via_read_roi(tmp_path):
    cols = ["x", "y", "Creatine_13_10", "region"]
    labels = ["A", "B", "C"]
    data = [[float(i), 0.0, 100 + i, labels[i % 3]] for i in range(9)]
    _write_named_xlsx(tmp_path, "s.xlsx", cols, data)
    # .txt は無いが read_desi_roi_list が自動変換(=新形式組み替え)して読む
    assert dm.read_desi_roi_list(str(tmp_path / "s.txt")) == ["A", "B", "C"]


# --- 回帰: 従来形式は組み替えない (passthrough) ----------------------------

def test_old_format_not_reshaped(tmp_path):
    # 従来形式 (先頭空行+4行ヘッダ) は新形式と誤検出されず、レイアウト保持
    data = [[i, i * 1.0, i * 2.0, 100 + i, 200 + i] for i in range(1, 5)]
    _write_csv(tmp_path, "s.csv", data)   # _HEADER（先頭空行）
    txt = dc.convert_desi_to_txt(tmp_path / "s.csv", tmp_path / "s.txt")
    lines = txt.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ""             # 先頭空行のまま
    assert "146.1216" in lines[3]     # 従来4行目(m/z)が化合物名化されず保持
    assert lines[4].split("\t")[0] == "1"   # データ先頭はそのまま


# --- ver6.1: 新形式の .txt も組み替える (normalize_desi_txt) ----------------

def _write_named_txt(folder, name, columns, data_rows, sep="\t"):
    """新形式の .txt を書く（sep=タブ or カンマ）。"""
    p = Path(folder) / name
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(sep.join(str(c) for c in columns) + "\n")
        for r in data_rows:
            f.write(sep.join(str(c) for c in r) + "\n")
    return p


def test_normalize_named_tab_txt(tmp_path):
    p = _write_named_txt(tmp_path, "s.txt", _NAMED_COLS, _named_data(), sep="\t")
    assert dc.normalize_desi_txt(p) is True
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ""           # 先頭空行
    assert lines[3] == ""           # 4行目空
    names = [c for c in lines[2].split("\t") if c]
    assert names == ["Acetylcholine", "Adenosine-POS", "GSH-POS"]
    first = lines[4].split("\t")
    assert first[0] == "1"          # 自動採番ID
    assert first[1] == "14.5"       # x
    assert dm.validate_msi_file(str(p))["valid"] is True


def test_normalize_named_comma_txt(tmp_path):
    # 実運用に近い: .csv 由来のカンマ区切り .txt（区切り自動判定で組み替える）
    p = _write_named_txt(tmp_path, "s.txt", _NAMED_COLS, _named_data(), sep=",")
    assert dc.normalize_desi_txt(p) is True
    lines = p.read_text(encoding="utf-8").splitlines()
    names = [c for c in lines[2].split("\t") if c]
    assert names == ["Acetylcholine", "Adenosine-POS", "GSH-POS"]
    assert lines[4].split("\t")[0] == "1"


def test_normalize_named_txt_roi_label_and_empty(tmp_path):
    # 実データ模擬: ROI列が "Heart1" と 空 の混在
    cols = ["x", "y", "Creatine_13_10", "Glutamine_12_18", "ROI"]
    data = [[float(i), 0.0, 1000 + i, 2000 + i, ("Heart1" if i % 3 == 0 else "")]
            for i in range(12)]
    p = _write_named_txt(tmp_path, "s.txt", cols, data, sep="\t")
    assert dc.normalize_desi_txt(p) is True
    lines = p.read_text(encoding="utf-8").splitlines()
    names = [c for c in lines[2].split("\t") if c]
    assert names == ["Creatine", "Glutamine"]
    # ROI が末尾データ列として保持され、read_desi_roi_list が "Heart1" を返す
    assert dm.read_desi_roi_list(str(p)) == ["Heart1"]


def test_normalize_idempotent(tmp_path):
    p = _write_named_txt(tmp_path, "s.txt", _NAMED_COLS, _named_data(), sep="\t")
    assert dc.normalize_desi_txt(p) is True
    content1 = p.read_text(encoding="utf-8")
    assert dc.normalize_desi_txt(p) is False   # 2回目は先頭空行で no-op
    assert p.read_text(encoding="utf-8") == content1


def test_normalize_old_format_txt_untouched(tmp_path):
    # 従来形式 .txt (先頭空行) は normalize で変更されない
    p = tmp_path / "s.txt"
    p.write_text("\nh2\nh3\nh4\n1\t2\t3\t4\n", encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    assert dc.normalize_desi_txt(p) is False
    assert p.read_text(encoding="utf-8") == before


def test_prepare_normalizes_named_txt_only_folder(tmp_path):
    # 新形式 .txt だけ置いたフォルダ → prepare で canonical 化される（.csv/.xlsx 無し）
    _write_named_txt(tmp_path, "E15_Heart1.txt", _NAMED_COLS, _named_data(), sep="\t")
    out = dc.prepare_desi_data_folder(str(tmp_path), ["E15_Heart1"])
    assert Path(out) == tmp_path
    lines = (tmp_path / "E15_Heart1.txt").read_text(encoding="utf-8").splitlines()
    assert lines[0] == ""                       # canonical 先頭空行
    names = [c for c in lines[2].split("\t") if c]
    assert names == ["Acetylcholine", "Adenosine-POS", "GSH-POS"]
    assert lines[4].split("\t")[0] == "1"       # 自動採番ID
