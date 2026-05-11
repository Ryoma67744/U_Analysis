# =============================================================================
# MSI Analysis Application - Lite View Layout
#
# /lite/<project_id>/<sub_project_id> で開ける「レポート型」サマリビュー。
# PPT 出力と同等の構造をブラウザ上で即時表示する。
# 全 figure は Plotly の hover/zoom/pan のみで、ドロップダウン・フィルタは
# 持たない（読み物として閉じる設計）。
#
# 構造:
#   1. Header   : プロジェクト名・統合手法・サンプル数・クラスタ数・解析日時
#   2. Overview : Integrated UMAP / Per-sample Spatial / Stats Table / Ratio Pie
#   3. Per-cluster Cards: クラスタ毎にハイライト UMAP+Spatial + Top 5 markers +
#                          Volcano（折りたたみ）
#   4. Heatmap  : Top N markers × clusters（Z-score / RdBu_r）
#
# レイアウト自体は最小限のシェル。コンテンツは
# lite_view_callbacks.initialize_lite_view で構築される。
# =============================================================================

from dash import html, dcc
import dash_bootstrap_components as dbc


def create_lite_view_layout():
    """レポート型サマリビューの最上位レイアウト。

    実際の中身は initialize_lite_view コールバックが lite_target_store の
    更新をトリガに `lv_report_body` に流し込む。
    """
    return html.Div(
        className="lite-view-container",
        style={
            "maxWidth": "1400px",
            "margin": "0 auto",
            "padding": "20px",
        },
        children=[
            # /lite/<pid>/<sid> から書かれるルーティング Store
            dcc.Store(id="lite_target_store", data={}),

            # エラー表示（callback が is_open=True にする）
            dbc.Alert(
                "",
                id="lv_error",
                color="danger",
                is_open=False,
                className="mb-3",
            ),

            # ローディング表示 + レポート本体
            dcc.Loading(
                id="lv_loading",
                type="circle",
                color="#0d6efd",
                children=html.Div(
                    id="lv_report_body",
                    children=html.Div(
                        "URL が無効です。/lite/<project_id>/<sub_project_id> "
                        "の形式でアクセスしてください。",
                        className="text-muted text-center py-5",
                    ),
                ),
            ),
        ],
    )
