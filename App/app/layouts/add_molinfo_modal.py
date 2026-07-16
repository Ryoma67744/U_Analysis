# =============================================================================
# MSI Analysis Application - Add Molecular Info Modal
# 登録済みデータへ「分子情報（化合物名）」を後から付与する。
# SCiLS「Static feature list」CSV をアップロード → サイドカーを生成（本体は書き換えない）。
# =============================================================================

from dash import html, dcc
import dash_bootstrap_components as dbc


def create_add_molinfo_modal():
    """分子情報の後付け登録モーダル。

    本体は add_molinfo_callbacks が upload/confirm で `add_molinfo_body` に注入する。
    """
    return dbc.Modal(
        id="add_molinfo_modal",
        is_open=False,
        size="lg",
        centered=True,
        scrollable=True,
        children=[
            # 対象サブプロジェクト {project_id, sub_id, nonce}
            dcc.Store(id="add_molinfo_target"),
            dbc.ModalHeader(dbc.ModalTitle("🧬 分子情報を後から登録")),
            dbc.ModalBody([
                html.P(
                    "SCiLS Lab の「Static feature list」CSV をアップロードすると、登録済みデータの "
                    "m/z に化合物名を突き合わせて注釈（サイドカー）を生成します。生データ（数GB）は"
                    "書き換えません。UMAP の再計算も不要です。",
                    className="small text-muted",
                ),
                dcc.Upload(
                    id="add_molinfo_upload",
                    children=html.Div([
                        "CSV をドラッグ&ドロップ、または ",
                        html.A("ファイルを選択", className="text-primary"),
                    ]),
                    multiple=False,
                    accept=".csv",
                    className="border rounded p-4 text-center text-muted",
                    style={"borderStyle": "dashed", "cursor": "pointer"},
                ),
                dcc.Loading(html.Div(id="add_molinfo_body", className="small mt-3")),
            ]),
            dbc.ModalFooter([
                dbc.Button("この内容で登録", id="add_molinfo_confirm_btn",
                           color="success", size="sm", disabled=True),
                dbc.Button("閉じる", id="add_molinfo_close_btn",
                           color="secondary", size="sm"),
            ]),
        ],
    )
