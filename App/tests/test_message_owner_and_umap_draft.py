"""出たり出なかったりする警告と、見えないのに確定できる下書きを潰す (S3 2 件)。

--------------------------------------------------------------------------
B: 「⚠ 結果フォルダが見つかりません」が出る回と出ない回がある
--------------------------------------------------------------------------
サブプロジェクトのインタラクティブ解析を開くと、2 つの処理が同時に走って
**同じ表示欄を奪い合う**。

    reset_interactive_on_project_change      … 「データを読み込んでください」
    set_interactive_folders_from_sub_project … 「⚠ 結果フォルダが見つかりません: …」

どちらの応答が後に届くかは毎回変わるので、**警告が出る回と出ない回がある**。
出なかった回は、結果フォルダが失われていることに気づけないまま
「データ読込」を押すことになる。

表示欄の持ち主を「フォルダを実際に調べている側」に一本化する。

--------------------------------------------------------------------------
C: UMAP の下書き線が消えるのに「確定」できる
--------------------------------------------------------------------------
UMAP をクリックして選択範囲の頂点を置いたあと、表示設定（サンプル別／マージ）を
変えると、図が作り直されてピンク色の下書き線だけが消える。ところが
「下書き 5 点 —『確定』で選択を確定」の文字は残っており、そのまま確定すると
**見えない範囲でピクセルが選択される**。マージ表示に切り替えてから確定した
場合は、描いた場所とは無関係なピクセルが選ばれる。

図を作り直す操作では下書きも捨てる。ラベルも同時に消えるので、
「見えていないのに確定できる」状態が構造的に無くなる。
"""

import inspect

import pytest
from dash import no_update


# ---------------------------------------------------------------------------
# B: 表示欄の持ち主を 1 つにする
# ---------------------------------------------------------------------------

def test_reset_no_longer_writes_the_message():
    """★ リセット側は表示欄に書かないこと（奪い合いをやめる）。"""
    import app.callbacks.interactive_project as ip

    src = inspect.getsource(ip)
    i = src.index("def reset_interactive_on_project_change")
    # コメント行は対象外（修正の経緯説明で欄の名前に言及しているため）
    decl = "\n".join(
        line for line in src[i - 1200:i].splitlines()
        if "#" not in line)
    assert "interactive_data_info" not in decl, (
        "リセット側がまだ表示欄を持っている"
        "（フォルダを調べる側と奪い合い、警告が出たり出なかったりする）")


def test_folders_owns_the_message_even_without_a_sub_project(monkeypatch):
    """★ サブプロ未選択でも、フォルダを調べる側が表示欄を確定させること。

    ここを no_update のままにすると、リセット側の Output を外した結果
    **前の表示が残り続ける**。
    """
    import app.callbacks.interactive_project as ip

    # プロジェクトはあるがサブプロ未選択
    out = ip.set_interactive_folders_from_sub_project(None, "proj1", False, None, None)
    assert out[2] == "データを読み込んでください", out[2]

    # プロジェクトも無い
    out = ip.set_interactive_folders_from_sub_project(None, None, False, None, None)
    assert out[2] == "", out[2]


def test_folders_still_reports_a_missing_result_dir(monkeypatch, tmp_path):
    """★ 警告が「毎回必ず」出ること（これが症状の本体）。"""
    import app.callbacks.interactive_project as ip

    missing = str(tmp_path / "gone")
    monkeypatch.setattr(
        "app.services.project_manager.get_sub_project",
        lambda p, s: {"last_result_dir": missing, "data_folder": str(tmp_path)})

    for _ in range(5):   # 何度呼んでも同じ（非決定でない）
        out = ip.set_interactive_folders_from_sub_project(
            "sub1", "proj1", False, None, None)
        assert "結果フォルダが見つかりません" in str(out[2]), out[2]


def test_folders_says_ready_when_everything_is_present(monkeypatch, tmp_path):
    """警告が無いときは従来どおりの文言であること。"""
    import app.callbacks.interactive_project as ip

    monkeypatch.setattr(
        "app.services.project_manager.get_sub_project",
        lambda p, s: {"last_result_dir": str(tmp_path),
                      "data_folder": str(tmp_path)})
    out = ip.set_interactive_folders_from_sub_project(
        "sub1", "proj1", False, None, None)
    assert out[2] == "データを読み込んでください"


def test_reset_still_hides_the_viz_and_clears_the_skip_flag():
    """リセット側の残りの役目は変わらないこと。"""
    import app.callbacks.interactive_project as ip

    out = ip.reset_interactive_on_project_change("proj1", False, None)
    assert out[0] == {"display": "none"}
    assert out[1] is False                      # sap_skip_reset を降ろす
    assert out[2] == {"display": "none"}
    # スキップ時は何も変えない
    skipped = ip.reset_interactive_on_project_change("proj1", True, None)
    assert skipped[0] is no_update and skipped[1] is False


# ---------------------------------------------------------------------------
# C: 図を作り直す操作では下書きを捨てる
# ---------------------------------------------------------------------------

def _draft(trigger, monkeypatch, draft=None, click=None, display_mode="integrated"):
    import app.callbacks.interactive_loupe as lp

    class _Ctx:
        triggered_id = trigger

    monkeypatch.setattr(lp, "ctx", _Ctx)
    return lp.umap_polygon_draft(
        click, None, None, display_mode, None, None,
        draft if draft is not None else [[1, 1], [2, 2], [3, 3]])


@pytest.mark.parametrize("trigger", [
    "umap_display_mode", "umap_merge_toggle", "seurat_rds_path_store",
])
def test_draft_is_dropped_when_the_figure_is_rebuilt(monkeypatch, trigger):
    """★ 図を作り直す操作で下書きを捨てること。

    残すと線だけが消えて「見えないのに確定できる」状態になる。
    """
    assert _draft(trigger, monkeypatch) == [], (
        f"{trigger} の変化で下書きが残っている"
        "（見えない範囲が選択される）")


def test_the_label_follows_the_draft():
    """ラベルは下書きストア由来なので、捨てれば同時に消えること。"""
    from app.callbacks.interactive_loupe import umap_polygon_draft_info

    assert umap_polygon_draft_info([]) == ""
    assert "2 点" in umap_polygon_draft_info([[1, 1], [2, 2]])


def test_the_overlay_clears_with_the_draft():
    """下書きが空なら、図のオーバーレイも空になること。"""
    from app.callbacks.interactive_loupe import umap_polygon_overlay

    patched = umap_polygon_overlay([])
    ops = getattr(patched, "_operations", None) or []
    assert ops, "Patch が何も指示していない"


# --- 既存の操作は変わらないこと --------------------------------------------

def test_clicking_still_adds_a_vertex(monkeypatch):
    click = {"points": [{"x": 4.0, "y": 5.0}]}
    out = _draft("interactive_umap_plot", monkeypatch, draft=[[1, 1]], click=click)
    assert out == [[1, 1], [4.0, 5.0]]


def test_undo_and_clear_still_work(monkeypatch):
    assert _draft("umap_polygon_undo", monkeypatch,
                  draft=[[1, 1], [2, 2]]) == [[1, 1]]
    assert _draft("umap_polygon_clear", monkeypatch,
                  draft=[[1, 1], [2, 2]]) == []


def test_per_sample_clicks_are_still_ignored(monkeypatch):
    """サンプル別表示のクリックは頂点にしない（従来どおり）。"""
    click = {"points": [{"x": 4.0, "y": 5.0}]}
    out = _draft("interactive_umap_plot", monkeypatch, draft=[[1, 1]],
                 click=click, display_mode="per_sample")
    assert out is no_update
