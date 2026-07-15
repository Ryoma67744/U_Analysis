# =============================================================================
# MSI Analysis Application - Feature lists & co-expression (Phase 5)
# 複数 m/z を名前付きリストとして保存/改名/削除/CSV 入出力し、2 リストの集約強度を
# 散布図 (共発現/共局在) で表示する。リストの作成元は既存の m/z 絞り込み結果・
# ブックマーク・CSV 取込を流用 (実 feature 名をそのまま使うので堅牢)。
# =============================================================================

import base64
import logging
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from dash import Input, Output, State, callback, ctx, no_update, html, dcc
from dash.exceptions import PreventUpdate

from app.services import feature_lists as fl
from app.utils.color_utils import (
    get_cluster_color_map as _get_cluster_color_map,
    cluster_sort_key as _cluster_sort_key,
)
from app.utils.annotation_label import feature_display_label as _feature_display_label

logger = logging.getLogger("msi.interactive.featurelists")


def _status(msg, cls="text-muted small"):
    return html.Span(msg, className=cls)


# ---------------------------------------------------------------------------
# 変更系: ディスク読込 + 作成(絞り込み/ブックマーク/CSV) + 改名/削除
# ---------------------------------------------------------------------------
@callback(
    [Output("feature_lists_store", "data"),
     Output("feature_lists_status", "children")],
    [Input("seurat_rds_path_store", "data"),
     Input("btn_list_from_mzfilter", "n_clicks"),
     Input("btn_list_from_bookmarks", "n_clicks"),
     Input("btn_rename_feature_list", "n_clicks"),
     Input("btn_delete_feature_list", "n_clicks"),
     Input("upload_feature_list", "contents"),
     Input("btn_list_from_picker", "n_clicks")],
    [State("feature_lists_store", "data"),
     State("feature_list_name", "value"),
     State("feature_mz_filtered_list", "data"),
     State("feature_history_store", "data"),
     State("feature_list_select", "value"),
     State("feature_list_rename", "value"),
     State("feature_list_picker", "value"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def mutate_feature_lists(rds_trigger, _n_mz, _n_bm, _n_ren, _n_del, upload,
                         _n_pick, state, new_name, mz_filtered, bookmarks,
                         sel_lid, rename_text, picker_feats, rds_path):
    trig = ctx.triggered_id
    state = state or fl.empty_state()

    if trig == "seurat_rds_path_store":
        if not rds_path:
            return fl.empty_state(), no_update
        return fl.load_lists(rds_path), no_update

    if trig == "btn_list_from_picker":
        feats = picker_feats or []
        if not feats:
            return no_update, _status("検索して feature を選択してください", "text-warning small")
        state = fl.add_list(state, new_name, feats)
        msg = _status(f"作成: {state['lists'][-1]['name']} ({len(feats)} feature)", "text-success small")
        if rds_path:
            fl.save_lists(rds_path, state)
        return state, msg

    if trig == "btn_list_from_mzfilter":
        feats = mz_filtered or []
        if not feats:
            return no_update, _status("先に Feature の m/z 絞り込みを実行してください", "text-warning small")
        state = fl.add_list(state, new_name, feats)
        msg = _status(f"作成: {state['lists'][-1]['name']} ({len(feats)} feature)", "text-success small")

    elif trig == "btn_list_from_bookmarks":
        feats = bookmarks or []
        if not feats:
            return no_update, _status("ブックマークがありません", "text-warning small")
        state = fl.add_list(state, new_name, feats)
        msg = _status(f"作成: {state['lists'][-1]['name']} ({len(feats)} feature)", "text-success small")

    elif trig == "btn_rename_feature_list":
        if not sel_lid:
            return no_update, _status("改名するリストを選択してください", "text-warning small")
        state = fl.rename_list(state, sel_lid, rename_text)
        msg = _status("改名しました", "text-success small")

    elif trig == "btn_delete_feature_list":
        if not sel_lid:
            return no_update, _status("削除するリストを選択してください", "text-warning small")
        state = fl.delete_list(state, sel_lid)
        msg = _status("削除しました", "text-success small")

    elif trig == "upload_feature_list":
        if not upload:
            raise PreventUpdate
        try:
            _ctype, b64 = str(upload).split(",", 1)
            text = base64.b64decode(b64).decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            return no_update, _status(f"CSV 読込失敗: {e}", "text-danger small")
        imported = fl.lists_from_csv(text)
        lists = list(state.get("lists", []))
        for g in imported.get("lists", []):
            lists = fl.add_list({"lists": lists}, g["name"], g["features"])["lists"]
        state = {**state, "lists": lists}
        msg = _status(f"CSV から {len(imported.get('lists', []))} リスト取込", "text-success small")
    else:
        raise PreventUpdate

    if rds_path:
        fl.save_lists(rds_path, state)
    return state, msg


# ---------------------------------------------------------------------------
# 検索駆動の feature picker (サーバ側検索。interactive_deg.filter_features と同型)
# ---------------------------------------------------------------------------
@callback(
    Output("feature_list_picker", "options"),
    Input("feature_list_picker", "search_value"),
    [State("seurat_rds_path_store", "data"),
     State("feature_list_picker", "value")],
    prevent_initial_call=True,
)
def filter_feature_list_picker(search_value, rds_path, current):
    from app.callbacks.interactive_callbacks import _interactive_data, _set_active_key
    _set_active_key(rds_path)
    features = _interactive_data.get("features_list") or []
    ann_map = _interactive_data.get("annotation_map") or {}
    current = [str(c) for c in (current or [])]

    # ラベルは化合物名付き（"m/z (化合物名)"）。interactive_deg.filter_features と同型。
    # FUTURE(annot-provenance): 将来「由来表示」を足す場合、ここの label に
    #   app/services/annotation_sources.format_annotation_label() で由来（source）を併記する想定。
    def _opt(f):
        return {"label": _feature_display_label(f, annotation_map=ann_map, style="paren"),
                "value": f}

    base = [_opt(c) for c in current]  # 選択済みは表示維持
    if not search_value:
        return base[:500]
    kw = str(search_value).lower()
    cur_set = set(current)
    matches = [str(f) for f in features
               if (kw in str(f).lower() or kw in ann_map.get(f, "").lower())
               and str(f) not in cur_set]
    return base + [_opt(f) for f in matches[:500]]


# ---------------------------------------------------------------------------
# 描画: テーブル + セレクタ選択肢
# ---------------------------------------------------------------------------
@callback(
    [Output("feature_lists_table", "data"),
     Output("feature_list_select", "options"),
     Output("coexpr_list_a", "options"),
     Output("coexpr_list_b", "options")],
    Input("feature_lists_store", "data"),
    prevent_initial_call=True,
)
def render_feature_lists(state):
    lists = (state or {}).get("lists", [])
    rows = [{"name": g.get("name", ""), "n": len(g.get("features", []))}
            for g in lists]
    options = [{"label": f"{g.get('name','')} ({len(g.get('features', []))})",
                "value": g.get("id")} for g in lists]
    return rows, options, options, options


# ---------------------------------------------------------------------------
# 共発現散布図: リスト A の集約 (x) vs リスト B の集約 (y)、Cluster で色分け
# ---------------------------------------------------------------------------
def _features_of(state, lid):
    for g in (state or {}).get("lists", []):
        if g.get("id") == lid:
            return list(g.get("features", [])), g.get("name", "")
    return [], ""


@callback(
    [Output("coexpr_scatter", "figure"),
     Output("coexpr_status", "children")],
    Input("btn_run_coexpr", "n_clicks"),
    [State("coexpr_list_a", "value"),
     State("coexpr_list_b", "value"),
     State("coexpr_agg", "value"),
     State("feature_lists_store", "data"),
     State("seurat_rds_path_store", "data"),
     State("seurat_cache_dir_store", "data")],
    prevent_initial_call=True,
)
def run_coexpression(n_clicks, lid_a, lid_b, agg, state, rds_path, cache_dir_str):
    if not n_clicks:
        raise PreventUpdate
    if not lid_a or not lid_b:
        return go.Figure(), _status("リスト A と B を選択してください", "text-warning small")
    if not rds_path:
        return go.Figure(), _status("データが読み込まれていません", "text-warning small")

    feats_a, name_a = _features_of(state, lid_a)
    feats_b, name_b = _features_of(state, lid_b)
    if not feats_a or not feats_b:
        return go.Figure(), _status("選択したリストが空です", "text-warning small")

    from app.callbacks.interactive_callbacks import (
        _interactive_data, _set_active_key, _bridge)
    _set_active_key(rds_path)
    df = _interactive_data.get("plot_data")
    if df is None:
        return go.Figure(), _status("データが読み込まれていません", "text-warning small")

    # expression_matrix.parquet を確保 (初回 20-60 秒)
    cache_dir = cache_dir_str
    try:
        if not cache_dir:
            cache_dir = str(_bridge.get_cache_dir(rds_path))
        _bridge.ensure_expression_matrix(rds_path)
    except Exception as e:  # noqa: BLE001
        return go.Figure(), _status(f"発現行列の準備に失敗: {e}", "text-danger small")

    union = list(dict.fromkeys([str(f) for f in feats_a] + [str(f) for f in feats_b]))
    matrix, present = _bridge.get_features_matrix(cache_dir, union)
    if matrix is None or not present:
        return go.Figure(), _status(
            "リストの feature が発現行列に見つかりません（m/z 名が一致しているか確認）。",
            "text-warning small")
    if len(matrix) != len(df):
        return go.Figure(), _status("発現行列と座標データの行数が一致しません。", "text-danger small")

    present_set = set(present)
    pa = [f for f in map(str, feats_a) if f in present_set]
    pb = [f for f in map(str, feats_b) if f in present_set]
    if not pa or not pb:
        return go.Figure(), _status("一致した feature が一方のリストで 0 件でした。", "text-warning small")

    def _agg(cols):
        m = matrix[cols].to_numpy(dtype=float)
        return m.mean(axis=1) if agg == "mean" else m.sum(axis=1)

    x = _agg(pa)
    y = _agg(pb)
    clusters = df["Cluster"].astype(str).to_numpy()
    cell_ids = df["CellID"].to_numpy()
    color_map = _get_cluster_color_map(df["Cluster"], None)

    fig = go.Figure()
    for cl in sorted(set(clusters), key=_cluster_sort_key):
        m = clusters == cl
        fig.add_trace(go.Scattergl(
            x=x[m], y=y[m], mode="markers",
            marker=dict(size=4, color=color_map.get(str(cl), "#999999"), opacity=0.6),
            name=str(cl), text=cell_ids[m],
            hovertemplate=(f"Cluster {cl}<br>{name_a}=%{{x:.3g}}"
                           f"<br>{name_b}=%{{y:.3g}}<br>%{{text}}<extra></extra>"),
        ))
    agg_label = "平均" if agg == "mean" else "合計"
    fig.update_layout(
        margin=dict(l=55, r=10, t=34, b=45),
        title=dict(text=f"共発現: {name_a} vs {name_b}（{agg_label}強度）",
                   x=0.5, font=dict(size=13)),
        xaxis_title=f"{name_a} ({agg_label})", yaxis_title=f"{name_b} ({agg_label})",
        plot_bgcolor="white", legend=dict(font=dict(size=9)),
    )
    status = _status(
        f"A: {len(pa)}/{len(feats_a)} feature 一致, B: {len(pb)}/{len(feats_b)} 一致 "
        f"— 右上ほど共局在", "text-success small")
    return fig, status


# ---------------------------------------------------------------------------
# CSV 出力 (Feature,List)
# ---------------------------------------------------------------------------
@callback(
    Output("dl_feature_lists_csv", "data"),
    Input("btn_export_feature_lists", "n_clicks"),
    State("feature_lists_store", "data"),
    prevent_initial_call=True,
)
def export_feature_lists(n_clicks, state):
    if not n_clicks:
        raise PreventUpdate
    if not (state or {}).get("lists"):
        raise PreventUpdate
    return dict(content=fl.lists_to_csv(state), filename="feature_lists.csv")
