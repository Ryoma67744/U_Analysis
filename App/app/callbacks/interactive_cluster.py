# =============================================================================
# MSI Analysis Application - Interactive Cluster Management Callbacks
# クラスタ管理系コールバック (interactive_callbacks.py から分離)
# =============================================================================

import logging

import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, ctx, no_update, html, ALL, MATCH

from app.config import CLUSTER_PRESET_COLORS
from app.utils.color_utils import (
    cluster_sort_key as _cluster_sort_key,
    cluster_display_name as _cluster_display_name,
)
from app.utils.deg_utils import (
    is_meaningful_annotation as _is_meaningful_annotation,
)

logger = logging.getLogger("msi.interactive")

# ---------------------------------------------------------------------------
# モジュール間共有データ参照
# ---------------------------------------------------------------------------
# interactive_callbacks.py の _interactive_data / helper を参照する
from app.callbacks.interactive_callbacks import (
    _interactive_data,
    _load_interactive_settings,
    _save_interactive_settings,
    _set_active_key,
)


# ---------------------------------------------------------------------------
# クラスタ統計テーブル
# ---------------------------------------------------------------------------

@callback(
    Output("cluster_stats_table", "data"),
    Input("seurat_rds_path_store", "data"),
    Input("cluster_name_map_store", "data"),
)
def update_cluster_stats(rds_path, cluster_name_map=None):
    from app.callbacks.interactive_callbacks import _set_active_key
    _set_active_key(rds_path)
    df = _interactive_data.get("plot_data")
    if df is None:
        return []

    total = len(df)
    stats = df["Cluster"].value_counts()
    stats = stats.reindex(sorted(stats.index, key=_cluster_sort_key))
    return [
        {"Cluster": _cluster_display_name(c, cluster_name_map), "Pixels": int(n), "Percent": f"{n / total * 100:.1f}%"}
        for c, n in stats.items()
    ]


# ---------------------------------------------------------------------------
# クラスタ情報テキスト
# ---------------------------------------------------------------------------

@callback(
    Output("cluster_info_text", "children"),
    [Input("cluster_stats_table", "selected_rows"),
     Input("umap_highlight_cluster", "value"),
     Input("cluster_stats_table", "data")],
    [State("cluster_name_map_store", "data"),
     State("seurat_rds_path_store", "data")],
)
def update_cluster_info(selected_rows, highlight, table_data,
                        cluster_name_map=None, rds_path=None):
    from app.callbacks.interactive_callbacks import _set_active_key
    _set_active_key(rds_path)
    df = _interactive_data.get("plot_data")
    if df is None:
        return "データを読み込んでください"

    cluster_id = None
    if selected_rows and table_data:
        cluster_id = table_data[selected_rows[0]].get("Cluster")
    elif highlight and len(highlight) == 1:
        cluster_id = str(highlight[0])

    if cluster_id is None:
        meta = _interactive_data.get("meta", {})
        return (
            f"Total cells: {meta.get('n_cells', '?')}\n"
            f"Clusters: {meta.get('n_clusters', '?')}\n"
            f"Samples: {', '.join(meta.get('samples', []))}"
        )

    mask = df["Cluster"].astype(str) == str(cluster_id)
    n = mask.sum()
    total = len(df)
    samples = df.loc[mask, "Sample"].value_counts()
    sample_info = "\n".join(f"  {s}: {c} pixels" for s, c in samples.items())

    return f"{_cluster_display_name(cluster_id, cluster_name_map)}: {n} pixels ({n / total * 100:.1f}%)\n{sample_info}"


# ---------------------------------------------------------------------------
# クラスタ比率円グラフ (Dashboard)
# ---------------------------------------------------------------------------

@callback(
    Output("cluster_proportion_chart", "figure"),
    Input("seurat_rds_path_store", "data"),
    Input("cluster_name_map_store", "data"),
    prevent_initial_call=True,
)
def update_cluster_dashboard(rds_path, cluster_name_map=None):
    """クラスタ比率の円グラフ"""
    from app.callbacks.interactive_callbacks import _set_active_key
    _set_active_key(rds_path)
    df = _interactive_data.get("plot_data")
    if df is None or "Cluster" not in df.columns:
        return go.Figure()

    colors = CLUSTER_PRESET_COLORS
    clusters_sorted = sorted(df["Cluster"].unique(), key=_cluster_sort_key)
    color_map = {str(c): colors[i % len(colors)] for i, c in enumerate(clusters_sorted)}

    counts = df["Cluster"].value_counts()
    counts = counts.reindex(clusters_sorted)
    pie_fig = go.Figure(go.Pie(
        labels=[_cluster_display_name(c, cluster_name_map) for c in counts.index],
        values=counts.values,
        marker=dict(colors=[color_map[str(c)] for c in counts.index]),
        textinfo="label+percent",
        textposition="inside",
        hole=0.3,
    ))
    pie_fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=False,
    )
    return pie_fig


@callback(
    Output("cluster_top_markers_panel", "children"),
    Input("deg_data_store", "data"),
    Input("cluster_name_map_store", "data"),
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def update_cluster_top_markers(deg_data, cluster_name_map=None, rds_path=None):
    """クラスタ別 Top 5 マーカー一覧"""
    from app.callbacks.interactive_callbacks import _set_active_key
    _set_active_key(rds_path)
    if not deg_data:
        return html.P("DEGデータなし", className="text-muted small")

    deg_df = pd.DataFrame(deg_data)
    if "cluster" not in deg_df.columns or "avg_log2FC" not in deg_df.columns:
        return html.P("DEGデータにcluster/avg_log2FC列がありません", className="text-muted small")

    deg_df["avg_log2FC"] = pd.to_numeric(deg_df["avg_log2FC"], errors="coerce")
    clusters = sorted(deg_df["cluster"].unique(), key=_cluster_sort_key)

    cards = []
    for cl in clusters:
        sub = deg_df[deg_df["cluster"] == cl].nlargest(5, "avg_log2FC")
        if sub.empty:
            continue
        rows = []
        for _, r in sub.iterrows():
            gene = r.get("gene", "?")
            ann = r.get("annotation", "")
            fc = r["avg_log2FC"]
            label = f"{gene}"
            if _is_meaningful_annotation(ann, gene):
                label += f" ({ann})"
            rows.append(html.Li(f"{label}  [FC={fc:.2f}]", className="small"))

        cards.append(
            dbc.Col(width=4, className="mb-2", children=[
                html.Div(
                    style={"border": "1px solid #dee2e6", "borderRadius": "5px",
                           "padding": "8px"},
                    children=[
                        html.Strong(_cluster_display_name(cl, cluster_name_map), className="small"),
                        html.Ul(rows, style={"paddingLeft": "18px", "marginBottom": "0"}),
                    ],
                ),
            ]),
        )

    if not cards:
        return html.P("マーカーなし", className="text-muted small")
    return dbc.Row(cards)


# ---------------------------------------------------------------------------
# クラスタ名変更 (B2)
# ---------------------------------------------------------------------------

@callback(
    Output("cluster_rename_panel", "children"),
    Input("seurat_rds_path_store", "data"),
    Input("cluster_name_map_store", "data"),
    prevent_initial_call=True,
)
def populate_cluster_rename_panel(rds_path, current_map):
    """データ読込後、クラスタごとのリネーム入力フィールドを動的生成。

    cluster_name_map_store を Input で受けることで、保存名の復元
    (load_saved_cluster_name_map) 完了後や「適用」「リセット」後にも
    再生成され、各入力欄に変更名がプリフィル表示される。
    （State だと rds_path と同時発火し、保存名ロード前に空で描画されていた）"""
    from app.callbacks.interactive_callbacks import _set_active_key
    _set_active_key(rds_path)
    df = _interactive_data.get("plot_data")
    if df is None or "Cluster" not in df.columns:
        return [html.P("データ読み込み後に表示されます", className="text-muted small")]

    clusters = sorted(df["Cluster"].unique(), key=_cluster_sort_key)
    if not current_map:
        current_map = {}

    rows = []
    for cl in clusters:
        cl_str = str(cl)
        current_name = current_map.get(cl_str, "")
        display_label = f"{cl_str} ({current_name})" if current_name else cl_str
        rows.append(
            dbc.Row(className="mb-1 align-items-center g-2", children=[
                dbc.Col(width=3, children=[
                    html.Span(display_label, className="small fw-bold"),
                ]),
                dbc.Col(width=1, children=[
                    html.Span("→", className="text-muted"),
                ]),
                dbc.Col(width=8, children=[
                    dbc.Input(
                        id={"type": "cluster_rename_input", "index": cl_str},
                        value=current_name,
                        placeholder=cl_str,
                        size="sm",
                    ),
                    html.Small(
                        id={"type": "cluster_rename_lock_indicator", "index": cl_str},
                        className="text-warning",
                        children="",  # ロック中のみ「編集中: alice」が入る
                    ),
                ]),
            ]),
        )
    return rows


# ---------------------------------------------------------------------------
# PR-G2: cluster_rename_input の UI ロック
# ---------------------------------------------------------------------------

@callback(
    Output({"type": "cluster_rename_lock_indicator", "index": ALL}, "id"),
    Input({"type": "cluster_rename_input", "index": ALL}, "value"),
    [State("seurat_rds_path_store", "data"),
     State("session_id_store", "data"),
     State({"type": "cluster_rename_input", "index": ALL}, "id")],
    prevent_initial_call=True,
)
def acquire_cluster_rename_lock(values, rds_path, session_id, ids):
    """値変更時にロック取得。出力 id は Pattern matching を成立させるためのダミー。"""
    from app.callbacks.edit_lock_callbacks import acquire_lock_for_callback
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict) or not rds_path or not session_id:
        return [no_update] * len(ids)
    target_index = triggered.get("index")
    if target_index is None:
        return [no_update] * len(ids)
    field_id = f"cluster_rename:{target_index}"
    acquire_lock_for_callback(rds_path, field_id, session_id)
    return [no_update] * len(ids)


@callback(
    [Output({"type": "cluster_rename_input", "index": MATCH}, "disabled"),
     Output({"type": "cluster_rename_lock_indicator", "index": MATCH}, "children")],
    Input("edit_lock_state", "data"),
    [State({"type": "cluster_rename_input", "index": MATCH}, "id"),
     State("session_id_store", "data")],
)
def reflect_cluster_rename_lock(lock_state, comp_id, my_session_id):
    """edit_lock_state を見て disabled + 編集中表示を反映。"""
    if not lock_state or not comp_id:
        return False, ""
    field_id = f"cluster_rename:{comp_id.get('index')}"
    owner = lock_state.get(field_id)
    if owner and owner.get("user_id") != my_session_id:
        return True, f"編集中: {owner.get('user_display', '?')}"
    return False, ""


@callback(
    Output("cluster_name_map_store", "data"),
    Output("cluster_rename_status", "children"),
    Input("cluster_rename_apply_btn", "n_clicks"),
    Input("cluster_rename_reset_btn", "n_clicks"),
    State({"type": "cluster_rename_input", "index": ALL}, "value"),
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def apply_cluster_rename(apply_clicks, reset_clicks, input_values, rds_path=None):
    """リネーム適用/リセット"""
    _set_active_key(rds_path)
    trigger = ctx.triggered_id
    if trigger == "cluster_rename_reset_btn":
        _save_interactive_settings("cluster_name_map", {})
        return {}, "🔄 リセットしました"

    if trigger == "cluster_rename_apply_btn":
        # pattern-matching の State から cluster_id と value を組み立て
        states_list = ctx.states_list[0] if ctx.states_list else []
        name_map = {}
        for item in states_list:
            cl_id = item["id"]["index"]
            val = item.get("value", "")
            if val and isinstance(val, str) and val.strip():
                name_map[cl_id] = val.strip()

        _save_interactive_settings("cluster_name_map", name_map)
        count = len(name_map)
        return name_map, f"✅ {count}件のクラスタ名を適用しました"

    return no_update, no_update


@callback(
    Output("cluster_name_map_store", "data", allow_duplicate=True),
    Input("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def load_saved_cluster_name_map(rds_path):
    """データ読込時に保存済みクラスタ名マッピングを復元"""
    _set_active_key(rds_path)
    settings = _load_interactive_settings()
    return settings.get("cluster_name_map", {})


# ---------------------------------------------------------------------------
# クラスタ名変更時のドロップダウンオプション一括更新
# ---------------------------------------------------------------------------

@callback(
    Output("umap_highlight_cluster", "options", allow_duplicate=True),
    Output("umap_exclude_cluster", "options", allow_duplicate=True),
    Output("spatial_highlight_cluster", "options", allow_duplicate=True),
    Output("spatial_exclude_cluster", "options", allow_duplicate=True),
    Output("feature_cluster_filter", "options", allow_duplicate=True),
    [Input("cluster_name_map_store", "data"),
     Input("umap_merge_toggle", "value")],
    prevent_initial_call=True,
)
def update_cluster_dropdown_labels(cluster_name_map, merge_toggle):
    """cluster_name_map変更時 or マージ切替時に全クラスタ関連ドロップダウンのラベルを更新"""
    df = _interactive_data.get("plot_data")
    if df is None:
        return (no_update,) * 5

    # マージ表示の場合はマージ後のクラスタラベルを使用
    if merge_toggle == "merged" and "Cluster_merged" in df.columns:
        clusters = sorted(df["Cluster_merged"].unique(), key=_cluster_sort_key)
    else:
        clusters = sorted(df["Cluster"].unique(), key=_cluster_sort_key)

    opts = [{"label": _cluster_display_name(c, cluster_name_map), "value": str(c)}
            for c in clusters]
    return opts, opts, opts, opts, opts
