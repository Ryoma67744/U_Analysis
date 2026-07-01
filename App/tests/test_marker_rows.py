"""build_marker_rows（DEG 非選択時の marker 集約表データ）の単体テスト。

表の描画（python-pptx）は interactive_pptx 側（dash 依存）だが、行データ生成の純ロジックは
deg_utils.build_marker_rows に集約したのでここで担保する。
"""

from app.utils.deg_utils import build_marker_rows


def _deg():
    # cluster "0": up=mz_200.1(FC+2), down=mz_100.5(FC-1.5)
    # cluster "1": up=mz_300.2(FC+3, annotation 有)
    return [
        {"gene": "mz_200.1", "cluster": "0", "avg_log2FC": 2.0,
         "p_val_adj": "1.0e-05", "p_val_adj_raw": 1e-5, "annotation": ""},
        {"gene": "mz_100.5", "cluster": "0", "avg_log2FC": -1.5,
         "p_val_adj": "2.0e-04", "p_val_adj_raw": 2e-4, "annotation": ""},
        {"gene": "mz_300.2", "cluster": "1", "avg_log2FC": 3.0,
         "p_val_adj": "3.0e-06", "p_val_adj_raw": 3e-6, "annotation": "Glucose"},
    ]


def test_headers_and_columns():
    headers, rows = build_marker_rows(["0", "1"], _deg(), top_n=5)
    assert headers == ["クラスタ", "m/z", "化合物名", "方向", "log2FC", "調整p値"]
    assert all(len(r) == 6 for r in rows)


def test_direction_and_values():
    headers, rows = build_marker_rows(["0"], _deg(), top_n=5)
    by_dir = {r[3]: r for r in rows}
    assert "▲Up" in by_dir and "▼Down" in by_dir
    up = by_dir["▲Up"]
    assert up[0] == "0"                 # クラスタ
    assert up[1] == "200.1000"          # m/z（extract_mz_numeric 整形）
    assert up[3] == "▲Up"
    assert up[4] == "2"                 # log2FC (2.0 -> "2")
    down = by_dir["▼Down"]
    assert down[1] == "100.5000"
    assert down[4] == "-1.5"


def test_annotation_used_as_compound():
    headers, rows = build_marker_rows(["1"], _deg(), top_n=5)
    assert rows[0][2] == "Glucose"      # annotation が化合物名として入る


def test_mrm_fallback_compound_by_nearest_mz():
    # annotation 無しの mz_200.1 を MRM マップ（200.12→Alanine）で近傍一致
    headers, rows = build_marker_rows(
        ["0"], _deg(), top_n=5, mz_to_compound={200.12: "Alanine", 999.0: "X"})
    up = [r for r in rows if r[3] == "▲Up"][0]
    assert up[2] == "Alanine"


def test_cluster_name_map_applied():
    headers, rows = build_marker_rows(
        ["0"], _deg(), top_n=5, cluster_name_map={"0": "腫瘍"})
    assert rows[0][0] == "腫瘍"


def test_empty_deg_returns_no_rows():
    headers, rows = build_marker_rows(["0", "1"], [], top_n=5)
    assert headers[0] == "クラスタ"
    assert rows == []
