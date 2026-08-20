"""範囲選択 DE で、重なった画素が黙って相手側に取られる (A-9)。

「選択範囲 (A) vs 指定群 (B)」の DE は、A と B の CellID を**そのまま連結**して
1 枚の表にし、R へ渡していた:

    rows_id  = ident1_ids + ident2_ids
    rows_grp = ["A"] * len(ident1_ids) + ["B"] * len(ident2_ids)

R 側は 1 行ずつ Idents を書き込むだけなので、同じ CellID が 2 行あると
**後に書いた方（B）が勝つ**:

    ident_vec[idx[keep]] <- groups$Group[keep]

つまり投げ縄で選んだ範囲が比較対象クラスタと重なっていると、
重なった画素は**無言で B 側の細胞として検定される**。利用者には
「A の一部が B に移った」ことを知る手段が無い。

A ⊂ B（選択範囲がまるごと比較対象に含まれる）の場合は A が空になり、
「too few cells: 0」という原因を指していないエラーで落ちる。

直し方（ご指定）:
- 重なりの扱いを**実行前にラジオで選ばせる**（既定＝重なりを除く）
- 重なりの件数を必ず表示する
- 除いた結果どちらかが 3 画素未満になるなら、実行前に理由を出して止める
- R へは**重複の無い表**を渡し、上書き順への依存をやめる
"""

import inspect
from pathlib import Path

import pandas as pd
import pytest

from app.services.seurat_bridge import SeuratBridge, resolve_group_overlap

R_HELPER = (Path(__file__).resolve().parents[1]
            / "Script" / "helpers" / "run_findmarkers.R")


# ---------------------------------------------------------------------------
# ① 重なりの解決そのもの
# ---------------------------------------------------------------------------

def test_exclude_drops_the_overlap_from_both_sides():
    a, b, n = resolve_group_overlap(["1", "2", "3"], ["3", "4"], "exclude")
    assert a == ["1", "2"], "A から重なりが抜けていない"
    assert b == ["4"], "B から重なりが抜けていない"
    assert n == 1


def test_a_wins_keeps_the_overlap_in_the_selection():
    a, b, n = resolve_group_overlap(["1", "2", "3"], ["3", "4"], "a")
    assert a == ["1", "2", "3"]
    assert b == ["4"], "A 優先なのに B に重なりが残っている"
    assert n == 1


def test_b_wins_keeps_the_overlap_in_the_comparison():
    a, b, n = resolve_group_overlap(["1", "2", "3"], ["3", "4"], "b")
    assert a == ["1", "2"], "B 優先なのに A に重なりが残っている"
    assert b == ["3", "4"]
    assert n == 1


def test_unknown_policy_falls_back_to_excluding():
    """未知の指定は安全側（除く）へ倒すこと。黙って B 優先に戻さない。"""
    a, b, n = resolve_group_overlap(["1", "2", "3"], ["3", "4"], "???")
    assert a == ["1", "2"] and b == ["4"] and n == 1


def test_duplicates_inside_one_side_are_removed_too():
    """同じ側に重複があっても R へは 1 行だけ渡すこと（順序は保つ）。"""
    a, b, n = resolve_group_overlap(["2", "1", "2"], ["4", "4"], "exclude")
    assert a == ["2", "1"]
    assert b == ["4"]
    assert n == 0


def test_no_comparison_group_means_no_overlap():
    """Globally（vs 全体）は B が無いので重なりは 0。"""
    a, b, n = resolve_group_overlap(["1", "2"], [], "exclude")
    assert a == ["1", "2"] and b == [] and n == 0


def test_a_inside_b_is_reported_not_silently_emptied():
    """A ⊂ B は A が空になる。件数が分かる形で返すこと。"""
    a, b, n = resolve_group_overlap(["1", "2"], ["1", "2", "3"], "exclude")
    assert a == [] and b == ["3"] and n == 2


# ---------------------------------------------------------------------------
# ② R へ渡す表と、実行前の停止
# ---------------------------------------------------------------------------

def test_the_bridge_takes_an_overlap_policy():
    sig = inspect.signature(SeuratBridge.run_differential_expression)
    assert "overlap_policy" in sig.parameters, (
        "重なりの扱いを R 実行まで運ぶ引数が無い")
    assert sig.parameters["overlap_policy"].default == "exclude", (
        "既定は「重なりを除く」であること")


def _fake_run(monkeypatch, tmp_path, seen):
    """subprocess を差し替えて、R へ渡る groups_csv を捕まえる。"""
    import app.services.seurat_bridge as sb

    class _R:
        returncode = 0
        stderr = b""

    def _run(cmd, **kw):
        # cmd: [rscript, --vanilla, script, rds, groups, out, mode, ...]
        groups_csv = Path(cmd[4])
        out_csv = Path(cmd[5])
        seen.append(pd.read_csv(groups_csv, dtype=str))
        pd.DataFrame({"gene": ["m1"], "p_val": [0.1], "avg_log2FC": [0.2],
                      "pct.1": [0.5], "pct.2": [0.4], "p_val_adj": [0.5],
                      "cluster": ["A"]}).to_csv(out_csv, index=False)
        return _R()

    monkeypatch.setattr(sb.subprocess, "run", _run)
    bridge = SeuratBridge()
    monkeypatch.setattr(bridge, "_get_cache_dir", lambda rds: tmp_path)
    return bridge


def test_r_receives_a_table_without_duplicates(monkeypatch, tmp_path):
    """★ 本丸: R へ渡る表に同じ CellID が 2 度出ないこと。"""
    seen = []
    bridge = _fake_run(monkeypatch, tmp_path, seen)
    bridge.run_differential_expression(
        str(tmp_path / "x.rds"), ["1", "2", "3", "4", "5"],
        ident2_ids=["4", "5", "6", "7", "8"],
        mode="local", overlap_policy="exclude")
    assert seen, "R が呼ばれていない"
    tbl = seen[0]
    assert not tbl["CellID"].duplicated().any(), (
        f"重複した CellID を R へ渡している:\n{tbl}")
    assert sorted(tbl.loc[tbl.Group == "A", "CellID"]) == ["1", "2", "3"]
    assert sorted(tbl.loc[tbl.Group == "B", "CellID"]) == ["6", "7", "8"]


def test_the_policy_changes_the_cache_entry(monkeypatch, tmp_path):
    """★ 扱いを変えたら別の結果になること（前の結果を返さない）。"""
    seen = []
    bridge = _fake_run(monkeypatch, tmp_path, seen)
    args = dict(ident2_ids=["4", "5", "6", "7", "8"], mode="local")
    sel = ["1", "2", "3", "4", "5"]
    bridge.run_differential_expression(str(tmp_path / "x.rds"), sel,
                                       overlap_policy="exclude", **args)
    bridge.run_differential_expression(str(tmp_path / "x.rds"), sel,
                                       overlap_policy="a", **args)
    assert len(seen) == 2, (
        "扱いを変えたのに R を呼び直していない。"
        "キャッシュ鍵に重なりの扱いが入っていない")
    assert sorted(seen[1].loc[seen[1].Group == "A", "CellID"]) == sel


def test_it_stops_before_running_when_a_side_gets_too_small(monkeypatch, tmp_path):
    """★ 重なりを除くと 3 画素未満になるなら、走らせずに理由を出すこと。"""
    seen = []
    bridge = _fake_run(monkeypatch, tmp_path, seen)
    with pytest.raises(RuntimeError) as ei:
        bridge.run_differential_expression(
            str(tmp_path / "x.rds"), ["1", "2", "3"],
            ident2_ids=["1", "2", "3", "4", "5", "6"], mode="local",
            overlap_policy="exclude")
    assert not seen, "小さすぎるのに R を起動している"
    msg = str(ei.value)
    assert "重なり" in msg, f"重なりが原因だと分かる文言が無い: {msg}"
    assert "3" in msg, f"重なりの件数が出ていない: {msg}"


# ---------------------------------------------------------------------------
# ③ R 側の上書き依存をやめる
# ---------------------------------------------------------------------------

def test_the_r_helper_refuses_duplicated_cell_ids():
    """★ R 側も重複を黙って上書きしないこと（後勝ちに依存しない）。"""
    src = R_HELPER.read_text(encoding="utf-8")
    assert "duplicated(" in src, (
        "run_findmarkers.R が重複 CellID を検査していない。"
        "同じ画素が 2 行あると後に書いた方 (B) が黙って勝つ")


# ---------------------------------------------------------------------------
# ④ 画面
# ---------------------------------------------------------------------------

def test_the_panel_has_an_overlap_radio():
    from app.layouts import interactive_tab
    src = Path(inspect.getfile(interactive_tab)).read_text(encoding="utf-8")
    assert 'id="onthefly_de_overlap"' in src, "重なりの扱いを選ぶ欄が無い"
    seg = src[src.index('id="onthefly_de_overlap"'):][:900]
    for v in ('"exclude"', '"a"', '"b"'):
        assert v in seg, f"重なりの扱いに {v} が無い"
    assert 'value="exclude"' in seg, "既定が「重なりを除く」になっていない"


def test_the_status_message_always_reports_the_overlap():
    import app.callbacks.interactive_de as de
    src = inspect.getsource(de.run_onthefly_de)
    assert "overlap" in src, "重なりの扱いを DE 実行が受け取っていない"
    assert "重なり" in src, "重なりの件数を画面に出していない"


def test_the_export_record_keeps_the_overlap_policy():
    """受領書に「どう扱ったか」を残すこと。"""
    import app.callbacks.interactive_de as de
    src = inspect.getsource(de.export_onthefly_de)
    assert "overlap" in src, "CSV 出力の記録に重なりの扱いが残らない"
