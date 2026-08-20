"""データ出力のサンプル名突合の回帰テスト (ver58.3)。

既存の `test_export_transform.py` は fixture が `annotation == plot_data の Sample`
という**成立している前提**しか置かず、しかも本番の `_match_sample_name` ではなく
テスト内の緩い自作関数を渡していたため、実運用で起きていた

  - annotation が全 spot 'Unannotated' (領域アノテーション CSV 無しの変換)
  - annotation ラベル名がファイル名と違う
  - x/y 列が無い legacy Transform CSV
  - DESI の 4 行ヘッダ

のどれも検出できなかった。ここでは**本番の関数をそのまま**使って、
クラスタ列が実際に埋まることを検査する。
"""
import io

import pandas as pd
import pytest

openpyxl = pytest.importorskip("openpyxl")

from app.callbacks.interactive_data_export import (  # noqa: E402
    _export_desi, _match_sample_name, _read_tims_file,
)
from app.services.export_transform import (  # noqa: E402
    append_cluster_region_columns, summarize_coverage,
)


def _rk(s, x, y):
    return (s, round(float(x), 4), round(float(y), 4))


def _raw(annotation):
    """SCiLS 変換 parquet と同じ列構成 (id, x, y, m/z…, annotation)。"""
    return pd.DataFrame({
        "id": [1, 2, 3],
        "x": [10.0, 11.0, 12.0],
        "y": [5.0, 5.0, 5.0],
        "611.1439": [1.0, 2.0, 3.0],
        "annotation": list(annotation),
    })


def _lookup(sample):
    return {"Harmony": {_rk(sample, 10.0, 5.0): "0",
                        _rk(sample, 11.0, 5.0): "1",
                        _rk(sample, 12.0, 5.0): "2"}}


# ---------------------------------------------------------------------------
# ① annotation が解決できないときに stem へフォールバックする
# ---------------------------------------------------------------------------

def test_unannotated_falls_back_to_stem():
    """領域アノテーション CSV 無しの変換 (annotation が全行 'Unannotated')。

    plot_data の Sample はファイル名 ('run01') になるため annotation では
    1 件も当たらない。ver58.3 より前はここで全行空欄になっていた。
    """
    df = _raw(["Unannotated"] * 3)
    stats = {}
    out = append_cluster_region_columns(
        df, _lookup("run01"), None, ["run01", "run02"], False, "run01",
        _match_sample_name, stats=stats)

    assert out["UMAP cluster"].tolist() == ["0", "1", "2"]
    assert stats["resolver"] == "stem-fallback"
    assert stats["matched"] == 3
    assert stats["unresolved_samples"] == ["Unannotated"]


def test_annotation_label_differs_from_filename_falls_back():
    """1 ファイル = 1 切片で annotation 名がファイル名と違う運用。"""
    df = _raw(["Brain_WT"] * 3)
    out = append_cluster_region_columns(
        df, _lookup("run01"), None, ["run01", "run02"], False, "run01",
        _match_sample_name)
    assert out["UMAP cluster"].tolist() == ["0", "1", "2"]


def test_matching_annotation_is_not_overridden():
    """annotation が解決できるときはフォールバックしない（従来動作の維持）。"""
    df = _raw(["Brain_WT", "Brain_WT", "Brain_KO"])
    lk = {"Harmony": {_rk("Brain_WT", 10.0, 5.0): "0",
                      _rk("Brain_WT", 11.0, 5.0): "1",
                      _rk("Brain_KO", 12.0, 5.0): "9"}}
    stats = {}
    out = append_cluster_region_columns(
        df, lk, None, ["Brain_WT", "Brain_KO"], False, "run01",
        _match_sample_name, stats=stats)
    assert out["UMAP cluster"].tolist() == ["0", "1", "9"]
    assert stats["resolver"] == "annotation"


def test_partial_annotation_match_does_not_fall_back():
    """一部でも解決できるなら stem に戻さない。

    取り違えて別サンプルのクラスタを書き込むより、空欄のままの方が安全。
    """
    df = _raw(["Brain_WT", "Brain_WT", "SecX"])
    lk = {"Harmony": {_rk("Brain_WT", 10.0, 5.0): "0",
                      _rk("Brain_WT", 11.0, 5.0): "1"}}
    stats = {}
    out = append_cluster_region_columns(
        df, lk, None, ["Brain_WT"], False, "run01", _match_sample_name, stats=stats)
    assert out["UMAP cluster"].tolist() == ["0", "1", ""]
    assert stats["resolver"] == "annotation"


def test_ambiguous_stem_stays_empty():
    """stem が複数サンプルに部分一致するなら解決しない（ver51.8 の方針を維持）。"""
    df = _raw(["Unannotated"] * 3)
    stats = {}
    out = append_cluster_region_columns(
        df, _lookup("Brain01_ROI1"), None, ["Brain01_ROI1", "Brain01_ROI2"],
        False, "Brain01", _match_sample_name, stats=stats)
    assert out["UMAP cluster"].tolist() == ["", "", ""]
    assert stats["matched"] == 0


def test_missing_xy_is_reported():
    """x/y 列が無いと座標キーを作れない。無言で空欄にせず理由を残す。"""
    df = pd.DataFrame({"id": [1, 2], "611.1439": [1.0, 2.0],
                       "annotation": ["run01", "run01"]})
    stats = {}
    out = append_cluster_region_columns(
        df, _lookup("run01"), None, ["run01"], False, "run01",
        _match_sample_name, stats=stats)
    assert out["UMAP cluster"].tolist() == ["", ""]
    assert stats["resolver"] == "no-xy"


# ---------------------------------------------------------------------------
# ② 一致 0 件を「成功」で終わらせない
# ---------------------------------------------------------------------------

def test_summarize_coverage_silent_when_all_matched():
    assert summarize_coverage([{"stem": "a", "rows": 10, "matched": 10,
                                "resolver": "annotation"}]) is None
    assert summarize_coverage([]) is None
    assert summarize_coverage(None) is None


def test_summarize_coverage_reports_total_miss():
    msg = summarize_coverage([{"stem": "run01", "rows": 100, "matched": 0,
                               "resolver": "annotation",
                               "unresolved_samples": ["Unannotated"]}])
    assert msg is not None
    assert "全行空欄" in msg
    assert "run01" in msg
    assert "Unannotated" in msg


def test_summarize_coverage_reports_partial_miss():
    msg = summarize_coverage([{"stem": "run01", "rows": 100, "matched": 40,
                               "resolver": "annotation",
                               "unresolved_samples": ["SecX"]}])
    assert msg is not None and "60" in msg and "全行空欄" not in msg


def test_summarize_coverage_explains_missing_xy():
    msg = summarize_coverage([{"stem": "legacy", "rows": 5, "matched": 0,
                               "resolver": "no-xy", "unresolved_samples": []}])
    assert msg is not None and "x/y" in msg


# ---------------------------------------------------------------------------
# ②-2 解析に含めなかった切片と、本当の不一致を区別する (ver58.4)
# ---------------------------------------------------------------------------

def _two_slices(n1, n2, overlap=0):
    """切片 01 (n1 spot) と 02 (n2 spot)。overlap 個だけ座標を共有させる。"""
    x1 = list(range(n1))
    x2 = list(range(overlap)) + list(range(10_000, 10_000 + n2 - overlap))
    return pd.DataFrame({
        "id": list(range(n1 + n2)),
        "x": [float(v) for v in x1 + x2],
        "y": [0.0] * (n1 + n2),
        "611.1439": [1.0] * (n1 + n2),
        "annotation": ["01"] * n1 + ["02"] * n2,
    })


def _slice1_lookup(n1, sample="260816"):
    return {"Harmony": {_rk(sample, i, 0.0): str(i % 3) for i in range(n1)}}


def test_unanalyzed_slice_is_reported_as_fact_not_warning():
    """切片 2 枚のうち 1 枚だけ UMAP を掛けた場合。

    もう 1 枚が空欄になるのは**正しい出力**なので、⚠️ ではなく事実として伝える。
    """
    df = _two_slices(60, 40)
    stats = {}
    out = append_cluster_region_columns(
        df, _slice1_lookup(60), None, ["260816"], False,
        "260816_Kizu_P5_SCiLS_9AA", _match_sample_name, stats=stats)

    assert int((out["UMAP cluster"] != "").sum()) == 60
    assert stats["by_group"] == {"01": (60, 60), "02": (0, 40)}
    assert stats["ambiguous"] == 0

    msg = summarize_coverage([stats])
    assert msg.startswith("ℹ️")
    assert "解析に含めなかった annotation" in msg and "'02'" in msg
    # 誤誘導になる汎用理由は出さない
    assert "座標が一致しません" not in msg


def test_overlapping_slice_coordinates_are_left_blank():
    """切片間で座標が重複する行は、取り違えを避けて空欄にする。

    ファイル名フォールバックは全 annotation を 1 つのサンプル名に潰すため、
    重複座標に値を入れると「黙って別の切片のクラスタ番号」が入ってしまう。
    """
    df = _two_slices(60, 40, overlap=10)
    stats = {}
    out = append_cluster_region_columns(
        df, _slice1_lookup(60), None, ["260816"], False,
        "260816_Kizu_P5_SCiLS_9AA", _match_sample_name, stats=stats)

    # 重複した 10 座標 = 切片 01 側 10 行 + 切片 02 側 10 行の計 20 行を空欄にする
    assert stats["ambiguous"] == 20
    assert int((out["UMAP cluster"] != "").sum()) == 50
    # 重複座標 (x=0..9) は 01 側も空欄
    assert out.loc[0, "UMAP cluster"] == ""
    assert out.loc[10, "UMAP cluster"] != ""

    msg = summarize_coverage([stats])
    assert msg.startswith("⚠️") and "座標が重複" in msg


def test_no_collision_check_when_annotation_resolves():
    """annotation が解決できるときは重複を気にしない（潰していないので安全）。"""
    df = _two_slices(3, 3, overlap=3)
    lk = {"Harmony": {_rk("01", 0.0, 0.0): "A", _rk("02", 0.0, 0.0): "B"}}
    stats = {}
    out = append_cluster_region_columns(
        df, lk, None, ["01", "02"], False, "whatever",
        _match_sample_name, stats=stats)
    assert stats["resolver"] == "annotation"
    assert stats["ambiguous"] == 0
    # 同じ (0,0) でも切片ごとに別の値が入る
    assert out.loc[0, "UMAP cluster"] == "A"
    assert out.loc[3, "UMAP cluster"] == "B"


# ---------------------------------------------------------------------------
# ③ legacy SCiLS Transform CSV を R と同じ読み方で読む
# ---------------------------------------------------------------------------

def _write_transform_csv(path, with_annotation):
    """R の Case 2 と同じ体裁: ヘッダ 4 行 / 末尾に x, y (, annotation)。

    annotation 列は R のコメントどおり「データ行の末尾に後付けされた」もので、
    ヘッダ 4 行は元の列数のまま（= データ行より 1 列狭い）。
    """
    mz = ["611.14390", "700.20000"]
    lines = [
        ",,,,,,",
        ",,,meta,meta,,",
        ",,," + ",".join(mz) + ",,",          # 3 行目 4 列目〜が m/z
        ",,,,,,",
    ]
    rows = [(1, 1.5, 2.5, 10.0, 5.0), (2, 1.5, 2.5, 11.0, 5.0)]
    for i, a, b, x, y in rows:
        cells = [str(i), "0", "0", str(a), str(b), str(x), str(y)]
        if with_annotation:
            cells.append("Brain_WT")
        lines.append(",".join(cells))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize("with_annotation", [False, True])
def test_read_tims_transform_csv(tmp_path, with_annotation):
    p = tmp_path / "legacy.csv"
    _write_transform_csv(p, with_annotation)
    df = _read_tims_file(str(p))

    # ver58.3 より前は 1 行目が列名になり x/y 列が存在しなかった。
    assert "x" in df.columns and "y" in df.columns
    assert df["x"].tolist() == [10.0, 11.0]
    assert df["y"].tolist() == [5.0, 5.0]
    assert len(df) == 2
    assert "m/z 611.14390" in df.columns
    if with_annotation:
        assert df["annotation"].tolist() == ["Brain_WT", "Brain_WT"]
    else:
        assert "annotation" not in df.columns

    # 実際にクラスタ列が埋まるところまで通す
    out = append_cluster_region_columns(
        df, _lookup("legacy"), None, ["legacy"], False, "legacy",
        _match_sample_name)
    assert out["UMAP cluster"].tolist() == ["0", "1"]


def test_headered_csv_is_left_alone(tmp_path):
    """見出し付き CSV (x / y 列を持つ) は従来どおりそのまま読む。"""
    p = tmp_path / "modern.csv"
    p.write_text("id,x,y,611.1439\n1,10.0,5.0,1.0\n2,11.0,5.0,2.0\n",
                 encoding="utf-8")
    df = _read_tims_file(str(p))
    assert df["x"].tolist() == [10.0, 11.0]
    assert df.shape == (2, 4)


# ---------------------------------------------------------------------------
# ④ DESI のヘッダ行数を自動判定する
# ---------------------------------------------------------------------------

def _write_desi_txt(path, n_header):
    """n_header 行のヘッダ + 3 データ行の DESI .txt を書く。"""
    head = {
        5: ["\t\t\t", "\t\t\tAla\tGly", "\t\t\t1\t2",
            "\t\t\t90.0\t76.0", "\t\t\t44.0\t30.0"],
        4: ["\t\t\t", "\t\t\t1\t2", "\t\t\tAla\tGly", "\t\t\t\t"],
    }[n_header]
    data = [f"{i}\t{10.0 + i}\t5.0\t1\t2" for i in range(3)]
    path.write_text("\n".join(head + data) + "\n", encoding="utf-8")


@pytest.mark.parametrize("n_header", [4, 5])
def test_export_desi_detects_header_rows(tmp_path, n_header):
    """4 行ヘッダでも先頭画素のクラスタが落ちないこと。"""
    _write_desi_txt(tmp_path / "Brain01.txt", n_header)
    lk = {"Harmony": {_rk("Brain01", 10.0 + i, 5.0): str(i) for i in range(3)}}

    report = []
    data, _ = _export_desi(str(tmp_path), lk, None, report=report)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb["Brain01"]
    header = [c.value for c in ws[1]]
    col = header.index("UMAP cluster") + 1
    values = [ws.cell(row=r, column=col).value
              for r in range(n_header + 1, n_header + 4)]

    # ver58.3 より前は 4 行ヘッダのとき先頭 1 画素が None のまま出ていた。
    assert values == ["0", "1", "2"]
    assert report[0]["rows"] == 3 and report[0]["matched"] == 3


def test_export_desi_reports_unmatched_sheet(tmp_path):
    """サンプル名が一致しないシートは報告に載る（空欄の理由が分かる）。"""
    _write_desi_txt(tmp_path / "Brain01.txt", 5)
    lk = {"Harmony": {_rk("別のサンプル", 10.0, 5.0): "0"}}
    report = []
    _export_desi(str(tmp_path), lk, None, report=report)
    assert report[0]["matched"] == 0
    assert report[0]["resolver"] == "no-sample"
    assert summarize_coverage(report) is not None
