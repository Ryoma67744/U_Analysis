# =============================================================================
# MSI Analysis Application - History Tab UI
# セッション履歴タブUI
# =============================================================================

from dash import html, dash_table
import dash_bootstrap_components as dbc


def create_history_tab():
    return html.Div(style={"marginTop": "15px"}, children=[
        html.Div(className="card", children=[
            html.H4(className="card-title", children=["📋 セッション履歴"]),

            dash_table.DataTable(
                id="session_history_table",
                columns=[
                    {"name": "名前", "id": "name"},
                    {"name": "作成日時", "id": "created_at"},
                    {"name": "パス", "id": "path"},
                ],
                data=[],
                row_selectable="single",
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "left", "padding": "12px"},
                style_header={"backgroundColor": "#f8f9fa", "fontWeight": "600"},
                style_data_conditional=[
                    {"if": {"state": "selected"},
                     "backgroundColor": "#e8eaf6", "border": "1px solid #667eea"},
                ],
                page_size=10,
            ),

            html.Div(
                style={"marginTop": "15px", "display": "flex", "gap": "10px"},
                children=[
                    dbc.Button("読込", id="reload_session", size="sm", color="primary"),
                    dbc.Button("削除", id="delete_session", size="sm", color="danger", outline=True),
                ],
            ),
        ]),
    ])
