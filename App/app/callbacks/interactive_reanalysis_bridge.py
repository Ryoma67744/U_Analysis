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
     # 解析手法を「再解析」へ自動選択する（未設定だと既定 desi_v8=UMAP のままで
     # 設定タブに UMAP フォームが出てしまうため）。相互排他コールバックも
     # allow_duplicate でこの2 ID に書くため allow_duplicate=True で出力する。
     Output("analysis_method", "value", allow_duplicate=True),
     Output("analysis_method_tims", "value", allow_duplicate=True),
     Output("main_tabs", "active_tab", allow_duplicate=True),
     Output("reanalysis_bridge_status", "children"),
     # ★ ver58.1 (デバッグ総点検 B-3): 転記も analysis_method を書くので、
     #   auto_switch_data_folder が発火して**それまで指定していた
     #   データフォルダが既定に戻る**。復元中と宣言して黙らせる。
     Output("settings_restore_pending", "data", allow_duplicate=True)],
    Input("btn_send_to_reanalysis", "n_clicks"),
    [State("reanalysis_bridge_mode", "value"),
     State("reanalysis_bridge_clusters", "value"),
     State("seurat_rds_path_store", "data"),
     State("int_cal_ms_instrument", "data")],
    prevent_initial_call=True,
)
def send_to_reanalysis(n_clicks, mode, clusters, rds_path, ms_instrument):
    if not n_clicks:
        raise PreventUpdate
    if not clusters:
        return (no_update, no_update, no_update, no_update, no_update, no_update,
                html.Span("対象クラスタを選択してください", className="text-warning small"),
                no_update)
    target = ", ".join(str(c) for c in clusters)
    fm = mode if mode in ("keep", "exclude") else "keep"
    folder = str(Path(rds_path).parent) if rds_path else ""
    # 読込済み結果の計測種別（DESI/TIMS）を確定し、該当モダリティの「再解析」を選ぶ。
    # 既存ヘルパーを再利用（明示 DESI 優先 / パス規約 /DESI/・/TIMS/ で補正 / 既定 TIMS）。
    from app.callbacks.interactive_data_export import _resolve_instrument
    inst = _resolve_instrument(ms_instrument, rds_path)
    if inst == "DESI":
        desi_method, tims_method = "desi_cluster_filter", None
    else:
        desi_method, tims_method = None, "tims_cluster_filter"
    msg = html.Span(
        f"{inst} 再解析フォームに転記しました（対象クラスタ・モード・RDSフォルダ）。"
        "設定タブで「再解析データフォルダ」等を確認して ▶解析実行 してください。",
        className="text-success small")
    return target, fm, folder, desi_method, tims_method, "settings", msg, True
