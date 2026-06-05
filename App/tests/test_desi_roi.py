"""DESI ROI 列検出 (read_desi_roi_list) のテスト（ver4.16）。

末尾の line/pixel 等の数値連番列を ROI と誤認せず、文字列の領域ラベル列だけを
ROI として返すことを検証する。
"""

import app.services.data_manager as dm


def _write_desi(tmp_path, name, data_rows):
    """4 行のヘッダ + データ行(タブ区切り)の DESI 風 .txt を書く。"""
    lines = ["h1", "h2", "h3", "h4"]  # ヘッダ4行（中身はスキップされるので任意）
    lines += ["\t".join(str(c) for c in r) for r in data_rows]
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def test_roi_rejects_pixel_and_line_numeric_columns(tmp_path):
    # [ID, x, y, mz1, mz2, line, pixel] 末尾は数値連番のみ → ROI 無し
    rows = [[i, i * 1.0, i * 2.0, 100 + i, 200 + i, 1, i] for i in range(1, 9)]
    fp = _write_desi(tmp_path, "no_roi.txt", rows)
    assert dm.read_desi_roi_list(fp) == []


def test_roi_detects_rightmost_label_column(tmp_path):
    labels = ["LN", "TDLN", "ROI"]
    rows = [[i, i * 1.0, i * 2.0, 100 + i, 200 + i, labels[i % 3]] for i in range(1, 10)]
    fp = _write_desi(tmp_path, "roi_last.txt", rows)
    assert dm.read_desi_roi_list(fp) == ["LN", "ROI", "TDLN"]  # sorted


def test_roi_detects_label_before_line_pixel(tmp_path):
    # [ID, x, y, mz1, mz2, ROI, line, pixel] → ROI が数値連番の手前にあっても検出する
    labels = ["A", "B"]
    rows = [[i, i * 1.0, i * 2.0, 100 + i, 200 + i, labels[i % 2], 1, i]
            for i in range(1, 10)]
    fp = _write_desi(tmp_path, "roi_mid.txt", rows)
    assert dm.read_desi_roi_list(fp) == ["A", "B"]


def test_roi_empty_file(tmp_path):
    # ヘッダのみ / データ無し → 空
    p = tmp_path / "empty.txt"
    p.write_text("h1\nh2\nh3\nh4\n", encoding="utf-8")
    assert dm.read_desi_roi_list(str(p)) == []
