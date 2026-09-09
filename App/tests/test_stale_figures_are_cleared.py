"""データセットを切り替えたとき、前の図が残らないこと (ver66.1)。

■ 背景
「データを読み込む」やプロジェクト切替のあと、**直前に見ていた図がそのまま
残っていた**。別のデータを開いたのに前のデータの画像が出ているので、どちらを
見ているのか分からなくなる。しかも画面は正常に見えるので気づけない。

原因は、図を描くコールバックで `seurat_rds_path_store` の扱いが二通りに
分かれていたこと:

  - UMAP / Spatial … **Input**。データセットが変われば描き直る
  - Feature Plot / Violin / Volcano / DEG ヒートマップ / H&E の TIC 図
                   … **State**。自分の操作 (m/z やクラスタの選択) が無いと
                     発火しない ＝ 古い図が残り続ける

■ 直し方
State 側の図は、データセットが変わった時点で **空にする**
(`interactive_resets.clear_stale_figures`)。描き直さないのは、古い選択
(m/z やクラスタ) が新しいデータセットにも存在する保証が無いため。

■ ここで守ること
  1. クリアが対象すべてに空表示を返すこと
  2. 発火元が「読み込みボタン」「プロジェクト切替」「サブプロジェクト切替」であること
  3. ★ **配線漏れの番人**: 図を出すコールバックは「`seurat_rds_path_store` を
     Input に取る」か「クリア対象表に載っている」かのどちらかであること。
     新しい図を足したときに、どちらの配線もしていないと落ちる。
"""

import pytest

pytest.importorskip("dash")
pytest.importorskip("plotly")

import plotly.graph_objects as go  # noqa: E402

from app.callbacks.interactive_resets import (  # noqa: E402
    STALE_FIGURE_OUTPUTS,
    blank_stale_figures,
    clear_stale_figures,
)

# 図・図の入れ物を返すコールバックを見分けるための語。
_FIGURE_HINTS = ("plot", "graph", "container", "heatmap", "volcano", "violin")

# 「古い図が残っても困らない」ことがはっきりしている出力。
# ここに足すときは **なぜ残ってよいのか** を書くこと。
@pytest.fixture
def dash_app(monkeypatch):
    """認証用の env を立ててから app を import する（test_render_payload と同型）。"""
    import secrets

    monkeypatch.setenv("FLASK_SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv("MASTER_PASSWORD", "test-master")
    monkeypatch.setenv("INITIAL_PASSWORD_A", "test-a")
    monkeypatch.setenv("INITIAL_PASSWORD_B", "test-b")
    try:
        from app.main import app
    except Exception as exc:  # pragma: no cover - 環境依存
        pytest.skip(f"app を import できない: {exc}")
    return app


_EXEMPT = {
    # フルスクリーンのモーダルは開くたびに toggle_fullscreen が中身ごと作り直す。
    "fs_spatial_graph_container",
    "fs_umap_graph_container",
    # 軽量ビューア (/lite/...) は 1 データセット 1 ページで、切り替えが起きない。
    "lv_spatial_container",
    "lv_umap_container",
    # H&E の登録画像そのもの。TIC 図 (hne_tic_graph) と違い、サンプルを選ぶまで
    # 何も出ない作りなのでデータセットをまたいで残らない。
    "hne_image_graph",
    # 図ではない (一覧・進捗・保存容量などの UI)。
    "project_cards_container",
    "sub_project_cards_container",
    "preflight_results_container",
    "share_links_container",
    "extra_data_folders_container",
    "dm_storage_stats",
    "umap_name_controls_container",
    "spatial_controls_container",
    "export_progress_container",
    "data_export_progress_container",
    "load_progress_container",
    "interactive_viz_container",
    "cluster_stats_container",
}


def test_clear_returns_a_blank_for_every_target():
    """★ 対象すべてに空表示が返ること（数が食い違うと Dash が実行時に落ちる）。"""
    blanks = blank_stale_figures()
    assert len(blanks) == len(STALE_FIGURE_OUTPUTS)
    for (cid, prop), value in zip(STALE_FIGURE_OUTPUTS, blanks):
        if prop == "figure":
            assert isinstance(value, go.Figure), f"{cid}: figure ではない"
            assert len(value.data) == 0, f"{cid}: 空の figure になっていない"
        else:
            # children は「未選択です」の案内か、何も無いか
            assert value is None or "選択" in str(value), f"{cid}: 空表示でない"


def test_clear_covers_the_panels_that_do_not_follow_the_dataset():
    """★ State でしか rds_path を見ていない図が、漏れなく対象に入っていること。"""
    targets = {cid for cid, _prop in STALE_FIGURE_OUTPUTS}
    for cid in ("feature_plot_container", "feature_violin_plot",
                "volcano_plot", "heatmap_plot", "hne_tic_graph"):
        assert cid in targets, f"{cid} がクリア対象から漏れている"


def test_clear_is_wired_to_the_load_button(dash_app):
    """★ 発火元が「データを読み込む」であること。

    外れると「読み込んだのに前の図が残る」に戻る。

    ★ プロジェクト/サブプロジェクトの切替は **入れない**。切替はビュー全体を
      display:none にするので古い図は見えず、見えるようになるのは次の読み込み時
      ＝このボタン経由だから。加えて、切替を入れると保存後の自動切替
      (`sap_skip_reset`: 表示をあえて壊さない経路) まで巻き込んで、
      利用者が見ていた図を理由もなく消してしまう。
    """
    import dash._callback as dc

    key = next(k for k in dc.GLOBAL_CALLBACK_MAP
               if "volcano_plot.figure" in k and "hne_tic_graph.figure" in k)
    spec = dc.GLOBAL_CALLBACK_MAP[key]
    inputs = {i["id"] for i in spec["inputs"]}
    assert inputs == {"load_interactive_data"}, inputs
    # 実際に呼べること（戻り値の数が Output と合っているか）
    assert len(clear_stale_figures(1)) == len(STALE_FIGURE_OUTPUTS)


def test_every_figure_output_follows_the_dataset_or_is_cleared(dash_app):
    """★ 配線漏れの番人。

    図を出すコールバックは次のどちらかでなければならない:
      - `seurat_rds_path_store` を **Input** に取る（データセットが変われば描き直る）
      - クリア対象表 `STALE_FIGURE_OUTPUTS` に載っている（切替時に空にする）

    どちらも無い図を足すと、そこだけ前のデータの画像が残る。
    """
    import dash._callback as dc

    cleared = {cid for cid, _prop in STALE_FIGURE_OUTPUTS}
    # 図の id → 「その図を出すコールバックのどれかが rds_path を Input に取るか」
    # 同じ図を複数のコールバックが出すことがある (例: UMAP は本体の描画と
    # ポリゴン下書きの Patch)。1 つでも追いかけていれば、その図は更新される。
    follows = {}
    for key, spec in dc.GLOBAL_CALLBACK_MAP.items():
        input_ids = {i["id"] for i in spec["inputs"] if isinstance(i.get("id"), str)}
        tracks = "seurat_rds_path_store" in input_ids
        for out in key.split(".."):
            out = out.strip()
            if not out or not (".figure" in out or ".children" in out):
                continue
            if not any(h in out for h in _FIGURE_HINTS):
                continue
            cid = out.split(".")[0].split("@")[0].strip()
            if not cid or cid.startswith("{"):     # pattern-matching id は対象外
                continue
            follows[cid] = follows.get(cid, False) or tracks

    offenders = [cid for cid, tracks in follows.items()
                 if not tracks and cid not in cleared and cid not in _EXEMPT]
    assert follows, "図を出すコールバックが 1 つも見つからない（テストが無意味）"
    assert not offenders, (
        "データセットが変わっても更新されず、クリア対象にも入っていない図がある。\n"
        "  seurat_rds_path_store を Input に足すか、"
        "interactive_resets.STALE_FIGURE_OUTPUTS に足すこと:\n  "
        + "\n  ".join(sorted(set(offenders))))
