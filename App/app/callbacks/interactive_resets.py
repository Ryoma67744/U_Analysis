# =============================================================================
# MSI Analysis Application - Reset buttons (Inc.1: “調整には必ずリセット”)
# 各調整コントロールを既定値へ戻す。値の出力は他の復元系 (preset/session) と
# 競合しうるため allow_duplicate=True で出力する。
# =============================================================================

import logging

import plotly.graph_objects as go
from dash import Input, Output, callback, html
from dash.exceptions import PreventUpdate

logger = logging.getLogger("msi.interactive.resets")


@callback(
    [Output("feature_colorscale", "value", allow_duplicate=True),
     Output("feature_intensity_min", "value", allow_duplicate=True),
     Output("feature_intensity_max", "value", allow_duplicate=True)],
    Input("feature_colorscale_reset", "n_clicks"),
    prevent_initial_call=True,
)
def reset_feature_colorscale(n_clicks):
    if not n_clicks:
        raise PreventUpdate
    return "Plasma", None, None


@callback(
    [Output("volcano_fc_threshold", "value", allow_duplicate=True),
     Output("volcano_p_threshold", "value", allow_duplicate=True),
     Output("volcano_y_max", "value", allow_duplicate=True)],
    Input("volcano_reset", "n_clicks"),
    prevent_initial_call=True,
)
def reset_volcano(n_clicks):
    if not n_clicks:
        raise PreventUpdate
    return 0.5, 1.3, None


@callback(
    # ★ ver66.0: スポットサイズを撤去したので、戻すのは透明度だけになった。
    Output("hne_overlay_opacity", "value", allow_duplicate=True),
    Input("hne_overlay_reset", "n_clicks"),
    prevent_initial_call=True,
)
def reset_hne_overlay(n_clicks):
    if not n_clicks:
        raise PreventUpdate
    return 100


# ---------------------------------------------------------------------------
# データセットを切り替えたときの「前の図」の後始末 (ver66.1)
# ---------------------------------------------------------------------------
# ★ ver66.1: 「データを読み込む」やプロジェクト切替のあと、**直前に見ていた図が
#   そのまま残っていた**。別のデータを開いたのに前のデータの画像が出ているので、
#   どちらを見ているのか分からなくなる（しかも見た目は正常なので気づけない）。
#
#   原因は、図を描くコールバックで `seurat_rds_path_store` の扱いが二通りに
#   分かれていたこと:
#     - UMAP / Spatial          … **Input**  → データセットが変われば描き直る
#     - Feature Plot / Violin /
#       Volcano / DEG ヒートマップ /
#       H&E の TIC 図           … **State**  → 自分の操作 (m/z やクラスタの選択) が
#                                  無いと発火しない ＝ 古い図が残り続ける
#
#   プロジェクト切替 (`reset_interactive_on_project_change`) も
#   `interactive_viz_container` を display:none にするだけで中身は消していないため、
#   次に読み込んだ瞬間に前のプロジェクトの図が現れる。
#
#   直し方は「消す」側に倒す。State 側の図を描き直すには m/z やクラスタの選択が
#   要るが、それらが新しいデータセットにも存在する保証は無い。勝手に描き直して
#   別物を出すより、空にして選び直してもらうほうが安全。
#
#   発火は「データを読み込む」ボタン **だけ**にする。理由が 2 つある:
#     - プロジェクト/サブプロジェクトの切替は `interactive_viz_container` を
#       display:none にするので、そのままでは古い図は見えない。見えるようになるのは
#       次に読み込んだときで、それはこのボタン（自動読込も n_clicks を増やす）を通る。
#     - 切替イベントで消すと、**保存後の自動切替** (`sap_skip_reset`) まで巻き込む。
#       あれは「表示を壊さない」ために切替時のリセットを意図的に飛ばしている経路で、
#       そこで図を消すと利用者が見ていた Feature Plot が理由もなく消える。
#       skip 旗を State で読む手もあるが、旗を倒すのは別コールバックなので
#       どちらが先に走るかで結果が変わる。発火元を絞るほうが確実。
#
#   ボタン押下は読み込みの **いちばん早い**イベントなので、本来の描画
#   (Stage B〜D の連鎖) は必ずこの後に走る。正しい図がこの空表示を上書きする
#   （順序が逆転して新しい図を消してしまうことがない）。

_BLANK_FEATURE_MESSAGE = "m/z Feature を選択してください"

# データセットが変わったら消す図の一覧 (component_id, prop)。
# `tests/test_stale_figures_are_cleared.py` がこの表を使って、
# **新しい図を足したときの配線漏れ**を検出する。
STALE_FIGURE_OUTPUTS = (
    ("feature_plot_container", "children"),
    ("feature_plot_heading", "children"),
    ("feature_violin_plot", "figure"),
    ("volcano_plot", "figure"),
    ("heatmap_plot", "figure"),
    ("hne_tic_graph", "figure"),
)


def blank_stale_figures():
    """`STALE_FIGURE_OUTPUTS` と同じ順の「空表示」を返す。

    children は既存の未選択時と同じ文言に合わせる（`interactive_deg` の
    `_finish(html.Div("m/z Feature を選択してください", ...))`）。利用者から見て
    「まだ選んでいない状態」と同じ見え方になり、新しい状態として自然に読める。
    """
    return (
        html.Div(_BLANK_FEATURE_MESSAGE, className="text-muted p-3"),
        None,
        go.Figure(),
        go.Figure(),
        go.Figure(),
        go.Figure(),
    )


@callback(
    [Output(cid, prop, allow_duplicate=True) for cid, prop in STALE_FIGURE_OUTPUTS],
    Input("load_interactive_data", "n_clicks"),
    prevent_initial_call=True,
)
def clear_stale_figures(n_clicks):
    """データの読み込みを始めた時点で、前のデータセットの図を空にする。"""
    if not n_clicks:
        raise PreventUpdate
    return blank_stale_figures()
