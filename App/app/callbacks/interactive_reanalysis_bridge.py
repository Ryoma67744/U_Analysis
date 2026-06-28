# =============================================================================
# MSI Analysis Application - Interactive -> Reanalysis bridge (Phase 5-c)
# インタラクティブ UMAP で「残す/除くクラスタ」を選び、ボタン一つで既存の
# 再解析 (クラスタフィルタ ReUMAP) フォームへ転記して設定タブへ移動する。
# 部分集合の再クラスタリング自体は本番の再解析エンジン (run_analysis +
# *_Cluster_Filter_ReUMAP.R) をそのまま使う。クラスタ単位フィルタが前提。
#
# target_clusters / filter_mode は session/project/preset でも書かれるため
# allow_duplicate=True で出力する。
# =============================================================================

import logging
from pathlib import Path

from dash import Input, Output, State, callback, no_update, html
from dash.exceptions import PreventUpdate

from app.utils.selection_utils import natural_cluster_key

logger = logging.getLogger("msi.interactive.reanalysis_bridge")


@callback(
    Output("reanalysis_bridge_clusters", "options"),
    Input("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def fill_bridge_cluster_options(rds_path):
    if not rds_path:
        return []
    from app.callbacks.interactive_callbacks import _interactive_data, _set_active_key
    _set_active_key(rds_path)
    df = _interactive_data.get("plot_data")
    if df is None or "Cluster" not in df.columns:
        return []
    cats = sorted(df["Cluster"].astype(str).unique(), key=natural_cluster_key)
    return [{"label": c, "value": c} for c in cats]


@callback(
    [Output("target_clusters", "value", allow_duplicate=True),
     Output("filter_mode", "value", allow_duplicate=True),
     Output("rds_folder_reanalysis", "value", allow_duplicate=True),
     Output("main_tabs", "active_tab", allow_duplicate=True),
     Output("reanalysis_bridge_status", "children")],
    Input("btn_send_to_reanalysis", "n_clicks"),
    [State("reanalysis_bridge_mode", "value"),
     State("reanalysis_bridge_clusters", "value"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def send_to_reanalysis(n_clicks, mode, clusters, rds_path):
    if not n_clicks:
        raise PreventUpdate
    if not clusters:
        return (no_update, no_update, no_update, no_update,
                html.Span("対象クラスタを選択してください", className="text-warning small"))
    target = ", ".join(str(c) for c in clusters)
    fm = mode if mode in ("keep", "exclude") else "keep"
    folder = str(Path(rds_path).parent) if rds_path else ""
    msg = html.Span(
        "再解析フォームに転記しました（対象クラスタ・モード・RDSフォルダ）。"
        "設定タブで「再解析データフォルダ」等を確認して ▶解析実行 してください。",
        className="text-success small")
    return target, fm, folder, "settings", msg
