"""一括保存・サムネ登録が「中身の無い図」を成果物にしないこと (C10-5/C10-6/C10-7)。

--------------------------------------------------------------------------
症状 1: 真っ白な PNG が保存され、サムネが白紙で上書きされる (C10-5)
--------------------------------------------------------------------------
UMAP 節で表示を「サンプル別」にしていると、統合 UMAP の figure は
`{"data": [], "layout": {...}}`（トレースが 1 本も無い＝枠線だけ）になる。
この dict は Python では truthy なので `elif umap_fig:` を素通りし、

  - 「📷 一括保存」→ 枠線だけの真っ白な PNG が `UMAP_integrated.png` として
    ZIP に入る
  - 「📌 サムネ登録」→「サムネを umap で登録しました」と**成功**を名乗りながら、
    プロジェクト一覧のサムネイルを真っ白な画像で上書きする（前のサムネは戻せない）

到達に特別な条件は要らない。`update_umap_per_sample` は
「表示=サンプル別／分割基準=選択グループ／グループ未保存」や
「plot_data が無い」で、保存用の図として空リストを**明示的に**書き込む。

--------------------------------------------------------------------------
症状 2: ヒートマップのファイル名が別クラスタの名前になる (C10-6)
--------------------------------------------------------------------------
DEG の一括保存は Volcano と Heatmap の両方のファイル名に
**Volcano 側のクラスタ選択**を付けていた。Heatmap には独立した
「フォーカスクラスタ」の選択があり、図のタイトルにはそちらが入る。
Volcano=3 / Heatmap フォーカス=5 で保存すると、タイトルが Cluster5 の図に
`Heatmap_Cluster3.png` という別クラスタの名前が付いたまま配布される。

--------------------------------------------------------------------------
症状 3: 一括保存だけ、押しても無言で何も起きない (C10-7)
--------------------------------------------------------------------------
保存対象が無いとき、サムネ登録は「保存対象のプロットがありません」と
教えてくれるのに、一括保存は PreventUpdate で終わって画面に何も出なかった。
図が見えているのにボタンだけ死んでいるように見える状態で、理由が分からない。
"""

import pytest
from dash.exceptions import PreventUpdate

import app.callbacks.interactive_batch_save as bs


BLANK = {"data": [], "layout": {"xaxis": {}, "yaxis": {}}}
REAL = {"data": [{"type": "scattergl", "x": [1, 2], "y": [1, 2]}], "layout": {}}


# ---------------------------------------------------------------------------
# 「中身がある図か」の判定そのもの
# ---------------------------------------------------------------------------

def test_blank_figure_is_recognised_as_empty():
    assert bs._figure_has_content(REAL) is True
    assert bs._figure_has_content(BLANK) is False
    assert bs._figure_has_content({}) is False
    assert bs._figure_has_content(None) is False


def test_image_only_figure_counts_as_content():
    """H&E 画像だけの図（トレース 0 本）は中身がある扱いにすること。"""
    hne_only = {"data": [], "layout": {"images": [{"source": "data:image/png;base64,X"}]}}
    assert bs._figure_has_content(hne_only) is True


def test_plotly_graph_objects_figure_is_accepted():
    import plotly.graph_objects as go
    assert bs._figure_has_content(go.Figure()) is False
    assert bs._figure_has_content(
        go.Figure(data=[go.Scattergl(x=[1], y=[1])])) is True


def test_zip_helper_drops_blank_figures(monkeypatch):
    """ZIP 生成の入口でも空の図を落とすこと（最後の砦）。"""
    monkeypatch.setattr(bs, "fig_to_png_bytes",
                        lambda fig, **kw: b"\x89PNG" + b"0" * 400)
    assert bs._create_zip_from_figures(
        [("blank", BLANK)], width=10, height=10, scale=1) is None
    assert bs._create_zip_from_figures(
        [("blank", BLANK), ("real", REAL)], width=10, height=10, scale=1) is not None


# ---------------------------------------------------------------------------
# 症状 1: 空の図が ZIP / サムネに入らないこと
# ---------------------------------------------------------------------------

def test_batch_save_umap_does_not_ship_a_blank_png(monkeypatch):
    """★ サンプル別表示で保存用の図が無いとき、白紙 PNG を出さないこと。"""
    monkeypatch.setattr(bs, "_get_export_figures", lambda *a, **kw: [])
    captured = []
    monkeypatch.setattr(
        bs, "_create_zip_from_figures",
        lambda figs, **kw: captured.append(figs) or b"ZIPZIPZIP")

    out = bs.cb_batch_save_umap(1, BLANK, "per_sample", "s1", "/tmp/x.rds")
    assert captured == [], f"空の図を ZIP に渡している: {captured}"
    # ダウンロードは起こさず、理由をトーストで伝える
    assert out[0] is bs.no_update
    assert out[1] is True and "ありません" in str(out[2])


def test_batch_save_umap_still_saves_a_real_integrated_figure(monkeypatch):
    monkeypatch.setattr(bs, "_get_export_figures", lambda *a, **kw: [])
    monkeypatch.setattr(bs, "_create_zip_from_figures", lambda figs, **kw: b"ZIP")
    out = bs.cb_batch_save_umap(1, REAL, "integrated", "s1", "/tmp/x.rds")
    assert out[0] is not bs.no_update, "中身のある図まで弾いている"


def test_thumbnail_umap_refuses_to_overwrite_with_a_blank(monkeypatch):
    """★ 白紙で既存サムネを上書きし、しかも成功を名乗らないこと。"""
    monkeypatch.setattr(bs, "_get_export_figures", lambda *a, **kw: [])
    called = []
    monkeypatch.setattr(
        bs, "_save_figure_as_thumbnail",
        lambda *a, **kw: called.append(a) or (True, "登録しました"))

    is_open, msg, icon, refresh = bs.cb_set_thumbnail_umap(
        1, BLANK, "per_sample", "proj1", 0, "s1", "/tmp/x.rds")
    assert called == [], "白紙の図でサムネを上書きしている"
    assert icon == "danger" and is_open is True
    assert "見つかりません" in str(msg)


def test_thumbnail_helper_rejects_blank_figures(monkeypatch):
    """ヘルパー側でも空の図を弾くこと（別経路からの呼び出し対策）。

    プロジェクトは実在する前提にして、「空の図」だけが失敗理由になるようにする。
    """
    import app.services.project_manager as pm
    monkeypatch.setattr(pm, "get_project", lambda pid: {"id": pid})
    written = []
    monkeypatch.setattr(pm, "update_project",
                        lambda pid, patch: written.append(patch) or {"id": pid})

    ok, msg = bs._save_figure_as_thumbnail(
        [("UMAP_integrated", BLANK)], 10, 10, 1, "proj1", "umap")
    assert ok is False
    assert msg == bs._NOTHING_TO_SAVE
    assert written == [], "白紙なのにプロジェクトのサムネを差し替えている"


# ---------------------------------------------------------------------------
# 症状 2: DEG のファイル名
# ---------------------------------------------------------------------------

def test_deg_zip_names_heatmap_by_its_own_cluster(monkeypatch):
    """★ Heatmap のファイル名は Heatmap 自身のフォーカスクラスタで付けること。"""
    captured = {}
    monkeypatch.setattr(
        bs, "_create_zip_from_figures",
        lambda figs, **kw: captured.setdefault("figs", figs) and None or b"ZIP")
    monkeypatch.setattr(bs, "_conditions_for", lambda *a, **kw: None)

    bs.cb_batch_save_deg(1, REAL, REAL, "3", "5", "/tmp/x.rds")
    names = [n for n, _ in captured["figs"]]
    assert names == ["Volcano_Cluster3", "Heatmap_Cluster5"], names


def test_deg_zip_heatmap_without_focus_is_not_labelled(monkeypatch):
    """フォーカス未選択（全クラスタ）の Heatmap にクラスタ名を付けないこと。"""
    captured = {}
    monkeypatch.setattr(
        bs, "_create_zip_from_figures",
        lambda figs, **kw: captured.setdefault("figs", figs) and None or b"ZIP")
    monkeypatch.setattr(bs, "_conditions_for", lambda *a, **kw: None)

    bs.cb_batch_save_deg(1, REAL, REAL, "3", None, "/tmp/x.rds")
    names = [n for n, _ in captured["figs"]]
    assert names == ["Volcano_Cluster3", "Heatmap"], names


# ---------------------------------------------------------------------------
# 症状 3: 保存対象が無いときに理由を伝えること
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fn,args,patch_target", [
    (lambda: bs.cb_batch_save_spatial(1, "s1", "/tmp/x.rds", None, None, None, None),
     None, "_get_export_figures"),
    (lambda: bs.cb_batch_save_feature(1, "s1", "/tmp/x.rds", None, None),
     None, "_get_feature_export_figures"),
])
def test_batch_save_says_why_nothing_happened(monkeypatch, fn, args, patch_target):
    """★ 押しても無言、をやめる（サムネ登録と同じように理由を出す）。"""
    monkeypatch.setattr(bs, patch_target, lambda *a, **kw: [])
    out = fn()
    assert out[0] is bs.no_update
    assert out[1] is True, "トーストが開かない"
    assert "ありません" in str(out[2])
    assert out[3] == "warning"


def test_batch_save_deg_says_why_nothing_happened():
    out = bs.cb_batch_save_deg(1, None, None, None, None, "/tmp/x.rds")
    assert out[0] is bs.no_update and out[1] is True
    assert "ありません" in str(out[2])


def test_batch_save_still_raises_on_a_stale_zero_click():
    """n_clicks が無い（＝押されていない）ときは従来どおり何もしないこと。"""
    for call in (
        lambda: bs.cb_batch_save_umap(0, REAL, "integrated", "s", "/tmp/x.rds"),
        lambda: bs.cb_batch_save_spatial(0, "s", "/tmp/x.rds", None, None, None, None),
        lambda: bs.cb_batch_save_feature(0, "s", "/tmp/x.rds", None, None),
        lambda: bs.cb_batch_save_deg(0, REAL, REAL, None, None, "/tmp/x.rds"),
    ):
        with pytest.raises(PreventUpdate):
            call()
