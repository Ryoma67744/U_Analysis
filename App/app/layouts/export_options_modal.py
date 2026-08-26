# =============================================================================
# MSI Analysis Application - Export Options Modal (ver61.0)
#
# 「📥 データ出力 (UMAP cluster)」で何を出すかを決めるモーダル。
#
#   1. 出力する列（カテゴリ単位）
#   2. 1 ピクセル単位か、グループ平均か
#
# 従来はフル出力しか無く、実データでは m/z が数千列あるため
# 「XY 座標とクラスタ番号だけ欲しい」用途でも数 GB のファイルが出ていた。
#
# 既定は全項目 ON・1 ピクセル単位、つまり **従来と完全に同じ出力**。
# モーダルを一度も開かなければ何も変わらない。
# =============================================================================

import dash_bootstrap_components as dbc
from dash import dcc, html

from app.services.export_options import (
    CATEGORIES,
    LEGACY_CATEGORIES,
    GROUP_KEYS,
    MODE_GROUP,
    MODE_PIXEL,
)


def create_export_options_modal():
    """データ出力の「出力内容の設定」モーダル。"""
    return dbc.Modal(
        id="export_options_modal",
        is_open=False,
        size="lg",
        centered=True,
        children=[
            dbc.ModalHeader(dbc.ModalTitle("⚙ 出力内容の設定")),
            dbc.ModalBody([
                html.P(
                    "データ出力に何を含めるかを決めます。既定は従来どおり"
                    "「全項目・1 ピクセル単位」です。",
                    className="text-muted small mb-3",
                ),

                # ---------- 1. 出力する列 ----------
                html.H6("出力する列", className="fw-bold"),
                dbc.Checklist(
                    id="export_opt_categories",
                    options=[
                        {"label": f"{label} — {desc}", "value": key}
                        for key, label, desc in CATEGORIES
                    ],
                    value=list(LEGACY_CATEGORIES),
                    className="small",
                ),
                dbc.FormText(
                    "「強度」を外すと m/z 列を読み込みません。実データでは数百〜数千列 "
                    "あるため、出力サイズも所要時間も桁で小さくなります。"
                    "空間座標や切片を出力から外しても、クラスタの突合には内部で使うので "
                    "結果は変わりません。",
                    className="text-muted small",
                ),

                html.Hr(className="my-3"),

                # ---------- 2. 集計単位 ----------
                html.H6("集計単位", className="fw-bold"),
                dbc.RadioItems(
                    id="export_opt_mode",
                    options=[
                        {"label": "1 ピクセル単位（1 行 = 1 スポット）", "value": MODE_PIXEL},
                        {"label": "グループ平均（1 行 = 1 グループ）", "value": MODE_GROUP},
                    ],
                    value=MODE_PIXEL,
                    className="small",
                ),
                html.Div(
                    id="export_opt_group_wrapper",
                    style={"display": "none", "marginTop": "10px",
                           "paddingLeft": "18px",
                           "borderLeft": "3px solid #dee2e6"},
                    children=[
                        dbc.Label("まとめるキー（1 つ以上）", className="small fw-bold"),
                        dbc.Checklist(
                            id="export_opt_group_keys",
                            options=[{"label": label, "value": key}
                                     for key, label in GROUP_KEYS],
                            value=["section", "cluster"],
                            className="small",
                        ),
                        dbc.FormText(
                            "選んだキーの組み合わせごとに平均値・スポット数 n・"
                            "標準偏差 SD を出します。"
                            "「領域名」をキーに含めなければ H&E の設定は不要です。",
                            className="text-muted small",
                        ),
                        # ★ n=1 の SD が空欄になる理由を先に書いておく。
                        #   後から「壊れている」と誤解されるのを防ぐ。
                        dbc.Alert(
                            "スポットが 1 つしかないグループの SD は空欄になります"
                            "（ばらつきが 0 なのではなく、不明なため）。",
                            color="light", className="small mt-2 mb-0 py-2",
                        ),
                    ],
                ),

                html.Div(id="export_opt_warning", className="mt-3"),
            ]),
            dbc.ModalFooter([
                dbc.Button("既定に戻す", id="export_opt_reset",
                           color="link", size="sm", className="me-auto"),
                dbc.Button("閉じる", id="export_opt_close",
                           color="secondary", size="sm"),
            ]),
        ],
    )


def create_export_options_store():
    """設定値の保管。モーダルの外（データ出力の callback）から読む。

    None のままなら従来どおり。`export_options.normalize` が None を既定へ倒す。
    """
    return dcc.Store(id="data_export_options", data=None)
