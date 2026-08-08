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


def test_backfilled_annotation_flows_to_marker_rows():
    """Phase1→Phase2 の連結: annotation 空でも annotation_map から補完すれば
    marker 表（PPTX と同経路）に化合物名が出る。"""
    from app.utils.deg_utils import backfill_annotations
    deg = _deg()  # mz_200.1 / mz_100.5 は annotation 空
    backfill_annotations(deg, {"mz_200.1": "Taurine"})
    headers, rows = build_marker_rows(["0"], deg, top_n=5)
    up = [r for r in rows if r[3] == "▲Up"][0]
    assert up[2] == "Taurine"


# ---------------------------------------------------------------------------
# ver52.3 ④: 読めなかった record を黙って消さない
# ---------------------------------------------------------------------------
# `avg_log2FC` を数値化できない record は `fc = 0.0` に落とされていたため、
# `> 0` にも `< 0` にも入らず **Up / Down 両方の Top-N から消えて**いた。
# 件数の報告も無いので、切り詰められた一覧が「上位マーカーの全部」として
# 表・スライドに出ていた。

def _deg_with_unreadable():
    return [
        {"gene": "mz_200.1", "cluster": "0", "avg_log2FC": 2.0,
         "p_val_adj": "1e-05", "annotation": ""},
        {"gene": "mz_150.0", "cluster": "0", "avg_log2FC": "n.d.",   # 読めない
         "p_val_adj": "1e-05", "annotation": ""},
        {"gene": "mz_100.5", "cluster": "0", "avg_log2FC": -1.5,
         "p_val_adj": "2e-04", "annotation": ""},
    ]


def test_unreadable_fold_change_is_counted():
    from app.utils.deg_utils import get_top_n_features_for_cluster

    up, down, dropped = get_top_n_features_for_cluster(
        _deg_with_unreadable(), "0", n=5, return_dropped=True)
    assert dropped == 1, (
        "avg_log2FC を読めない record が数えられていない。"
        "Up にも Down にも入らないまま黙って消える")
    assert "mz_200.1" in up and "mz_100.5" in down, \
        "読める record まで巻き込んで落としている"


def test_marker_table_reports_the_dropped_records():
    """★ 表そのものが利用者に届く成果物なので、同じ表に注記を出す。"""
    headers, rows = build_marker_rows(["0"], _deg_with_unreadable(), top_n=5)
    notice = [r for r in rows if "除外" in str(r[2])]
    assert notice, (
        "読み取れなかった record があるのに、表に何の注記も出ていない。"
        "利用者には『これが上位マーカーの全部』に見える")
    assert "1 件" in notice[0][2], f"件数が出ていない: {notice[0]}"
    # 各行の列数が揃っていること（注記行だけ列数が違うと表が崩れる）
    assert all(len(r) == len(headers) for r in rows), \
        f"注記行の列数が他と違う: {[len(r) for r in rows]}"


def test_no_notice_when_everything_is_readable():
    """★ 過剰修正の番人: 問題が無いときに注記を出さないこと。"""
    _headers, rows = build_marker_rows(["0", "1"], _deg(), top_n=5)
    assert not [r for r in rows if "除外" in str(r[2])], \
        "読めない record が無いのに注記行が出ている"


def test_zero_fold_change_is_not_treated_as_unreadable():
    """★ FC=0 は「変動なし」という正当な測定値。読めなかったのとは違う。"""
    from app.utils.deg_utils import get_top_n_features_for_cluster

    deg = [{"gene": "mz_1.0", "cluster": "0", "avg_log2FC": 0.0,
            "p_val_adj": "1e-05", "annotation": ""}]
    _up, _down, dropped = get_top_n_features_for_cluster(
        deg, "0", n=5, return_dropped=True)
    assert dropped == 0, "FC=0 を『読めなかった』として数えている"


def test_default_return_shape_is_unchanged():
    """★ 既存の呼び出しを壊さないこと（2-tuple のまま）。"""
    from app.utils.deg_utils import get_top_n_features_for_cluster

    got = get_top_n_features_for_cluster(_deg(), "0", n=5)
    assert len(got) == 2
    # 空の早期 return も同じ形であること（ここを直し忘れて一度落ちた）
    assert len(get_top_n_features_for_cluster([], "0", n=5)) == 2
    assert len(get_top_n_features_for_cluster([], "0", n=5,
                                              return_dropped=True)) == 3
