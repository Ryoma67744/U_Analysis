# =============================================================================
# MSI Analysis Application - Interactive DEG/Volcano/Heatmap/Feature Callbacks
# インタラクティブ解析 DEG・Volcano・Heatmap・Feature コールバック
#
# interactive_callbacks.py から分離された DEG 関連の
# ヘルパー関数・コールバックをまとめたモジュール。
# =============================================================================

import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import (Input, Output, State, callback, ctx, no_update, html, dcc)
from dash.exceptions import PreventUpdate

from app.utils.color_utils import (
    cluster_sort_key as _cluster_sort_key,
    cluster_display_name as _cluster_display_name,
)
from app.utils.display_helpers import (
    display_name as _display_name,
)
from app.utils.deg_utils import (
    is_meaningful_annotation as _is_meaningful_annotation,
    extract_mz_numeric as _extract_mz_numeric,
)
from app.utils.label_persistence import (
    compute_annotation_offsets as _compute_annotation_offsets,
)
from app.utils.selection_utils import log_transform_intensities

logger = logging.getLogger("msi.interactive.deg")


_FEATURE_IMG_CONFIG = {
    "scrollZoom": True,
    "toImageButtonOptions": {
        "format": "png", "scale": 3,
    },
}


# ---------------------------------------------------------------------------
# フィーチャー検索（サーバーサイドフィルタ）
# ---------------------------------------------------------------------------

@callback(
    Output("feature_select", "options", allow_duplicate=True),
    [Input("feature_select", "search_value"),
     Input("feature_filter_mode", "value"),
     Input("feature_cluster_filter", "value")],
    [State("feature_mz_filtered_list", "data"),
     State("deg_data_store", "data"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def filter_features(search_value, filter_mode, cluster_filter,
                    mz_filtered, deg_data, rds_path=None):
    """フィーチャードロップダウンの検索値に基づいてオプションをフィルタ"""
    from app.callbacks.interactive_callbacks import _interactive_data, _set_active_key
    _set_active_key(rds_path)
    features = _interactive_data.get("features_list")
    if not features:
        return []

    # annotation マッピングは load_interactive_data / m/z キャリブで既に
    # DEG マージ済みのため、ここでは直接参照のみ（検索のたびの再構築を回避）
    ann_map = _interactive_data.get("annotation_map") or {}

    def _make_option(f, rank=None):
        """フィーチャー名からドロップダウン用オプションを生成"""
        prefix = f"\u2605{rank} " if rank is not None else ""
        if f in ann_map:
            return {"label": f"{prefix}{f} ({ann_map[f]})", "value": f}
        return {"label": f"{prefix}{f}", "value": f}

    # DEGマーカーモード: クラスタのマーカーm/zのみ表示
    top15_ordered = None
    rest_ordered = None

    if filter_mode == "deg" and deg_data:
        if cluster_filter:
            # 選択クラスタのDEGレコードを抽出
            cluster_records = [
                r for r in deg_data
                if str(r.get("cluster", "")) == str(cluster_filter)
            ]

            # p値昇順（最も有意が先頭）、|log2FC|降順でタイブレーク
            def _sort_key(r):
                p = r.get("p_val_adj_raw")
                if p is None or (isinstance(p, float) and np.isnan(p)):
                    p = 1.0
                fc = r.get("avg_log2FC", 0)
                if fc is None or (isinstance(fc, float) and np.isnan(fc)):
                    fc = 0.0
                return (float(p), -abs(float(fc)))

            cluster_records_sorted = sorted(cluster_records, key=_sort_key)

            # Top 15 ユニーク遺伝子を抽出
            seen = set()
            top15_genes = []
            for r in cluster_records_sorted:
                g = str(r.get("gene", ""))
                if g and g not in seen:
                    seen.add(g)
                    top15_genes.append(g)
                    if len(top15_genes) >= 15:
                        break

            top15_set = set(top15_genes)
            features_set = set(features)

            # features_listに存在するもののみ保持
            top15_ordered = [f for f in top15_genes if f in features_set]

            # 残りのDEG遺伝子をm/z数値順でソート
            all_deg_set = set(str(r.get("gene", "")) for r in cluster_records)
            rest_ordered = sorted(
                [f for f in features if f in all_deg_set and f not in top15_set],
                key=_extract_mz_numeric,
            )
        else:
            # 全クラスタのDEGマーカー（従来通り）
            deg_genes = [str(r.get("gene", "")) for r in deg_data]
            deg_set = set(deg_genes)
            features = [f for f in features if f in deg_set]
    else:
        # 全m/zモード: m/zフィルタ適用済みリストがあればそれをベースにする
        if mz_filtered:
            features = mz_filtered

    # --- Top15 + 残り の特別表示モード ---
    if top15_ordered is not None:
        if not search_value:
            options = []
            for rank, f in enumerate(top15_ordered, 1):
                options.append(_make_option(f, rank=rank))
            if top15_ordered and rest_ordered:
                options.append({
                    "label": "\u2500\u2500\u2500\u2500 \u305d\u306e\u4ed6\u306e DEG \u30de\u30fc\u30ab\u30fc \u2500\u2500\u2500\u2500",
                    "value": "__separator__",
                    "disabled": True,
                })
            for f in rest_ordered[:500 - len(top15_ordered) - 1]:
                options.append(_make_option(f))
            return options
        else:
            keyword = search_value.lower()
            options = []
            for rank, f in enumerate(top15_ordered, 1):
                if keyword in f.lower() or keyword in ann_map.get(f, "").lower():
                    options.append(_make_option(f, rank=rank))
            matched_rest = [
                f for f in rest_ordered
                if keyword in f.lower() or keyword in ann_map.get(f, "").lower()
            ]
            if options and matched_rest:
                options.append({
                    "label": "\u2500\u2500\u2500\u2500 \u305d\u306e\u4ed6\u306e DEG \u30de\u30fc\u30ab\u30fc \u2500\u2500\u2500\u2500",
                    "value": "__separator__",
                    "disabled": True,
                })
            for f in matched_rest[:100]:
                options.append(_make_option(f))
            return options

    # --- 通常モード ---
    if not search_value:
        # 検索なしの場合は全件（最大500件）
        return [_make_option(f) for f in features[:500]]

    # 検索値でフィルタ（大文字小文字区別なし、アノテーションも検索対象）
    keyword = search_value.lower()
    filtered = [
        f for f in features
        if keyword in f.lower() or keyword in ann_map.get(f, "").lower()
    ]
    return [_make_option(f) for f in filtered[:100]]


# ---------------------------------------------------------------------------
# m/z 範囲フィルタ
# ---------------------------------------------------------------------------

@callback(
    Output("feature_mz_filtered_list", "data"),
    Input("apply_feature_mz_filter", "n_clicks"),
    [State("feature_mz_min", "value"),
     State("feature_mz_max", "value"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def apply_mz_filter(n_clicks, mz_min, mz_max, rds_path=None):
    """m/z最小値・最大値で feature リストを絞り込み、Storeに保存"""
    from app.callbacks.interactive_callbacks import _interactive_data, _set_active_key
    _set_active_key(rds_path)
    features = _interactive_data.get("features_list")
    if not features:
        return None
    if mz_min is None and mz_max is None:
        return None  # フィルタなし -> 全件に戻す

    filtered = []
    for f in features:
        # feature名から数値部分を抽出（例: "mz_123.456" -> 123.456）
        match = re.search(r"(\d+\.?\d*)", f)
        if match:
            val = float(match.group(1))
            if mz_min is not None and val < mz_min:
                continue
            if mz_max is not None and val > mz_max:
                continue
        filtered.append(f)
    return filtered


@callback(
    Output("feature_select", "options", allow_duplicate=True),
    Input("feature_mz_filtered_list", "data"),
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def update_feature_options_on_mz_filter(mz_filtered, rds_path=None):
    """m/zフィルタ適用後、ドロップダウンの選択肢を即時更新"""
    from app.callbacks.interactive_callbacks import _interactive_data, _set_active_key
    _set_active_key(rds_path)
    if mz_filtered is None:
        # フィルタ解除 -> 全件に戻す
        features = _interactive_data.get("features_list")
        if not features:
            return []
        return [{"label": f, "value": f} for f in features[:500]]

    return [{"label": f, "value": f} for f in mz_filtered[:500]]


# ---------------------------------------------------------------------------
# Feature プロット（Spatial表示、Parquet高速読み込み優先 -> R fallback）
# ---------------------------------------------------------------------------

@callback(
    [Output("feature_plot_container", "children"),
     Output("feature_intensity_min", "placeholder"),
     Output("feature_intensity_max", "placeholder"),
     Output("batch_feature_figures_store", "data")],
    [Input("feature_select", "value"),
     Input("feature_sample_select", "value"),
     Input("feature_marker_size", "value"),
     Input("feature_intensity_min", "value"),
     Input("feature_intensity_max", "value"),
     Input("sample_name_map_store", "data"),
     Input("fullscreen_closed_trigger", "data"),
     Input("feature_columns_per_row", "value"),
     Input("feature_show_compound_names", "value"),
     Input("feature_colorscale", "value"),
     Input("feature_log_scale", "value"),
     Input("feature_reverse_scale", "value")],
    [State("seurat_rds_path_store", "data"),
     State("seurat_cache_dir_store", "data"),
     State("spatial_rotation_store", "data"),
     State("deg_data_store", "data")],
    prevent_initial_call=True,
)
def update_feature_plot(feature_name, sample, marker_size,
                        intensity_min, intensity_max,
                        name_map, _fs_trigger, columns_per_row,
                        show_compound_names,
                        colorscale, log_scale, reverse_scale,
                        rds_path, cache_dir_str, rotation_store,
                        deg_data):
    from app.callbacks.interactive_callbacks import _interactive_data, _bridge, _set_active_key
    from app.callbacks.interactive_spatial import _transform_coords, _calc_zero_gap_marker_size
    _set_active_key(rds_path)
    # 名前変更・フルスクリーン閉鎖トリガーだがFeature未選択 -> スキップ
    if ctx.triggered_id in ("sample_name_map_store", "fullscreen_closed_trigger") and not feature_name:
        return no_update, no_update, no_update, no_update

    if not feature_name or not rds_path:
        return html.Div("m/z Feature を選択してください", className="text-muted p-3"), no_update, no_update, []

    df = _interactive_data.get("plot_data")
    if df is None:
        return html.Div("データが読み込まれていません", className="text-muted p-3"), no_update, no_update, []

    if "SpatialX" not in df.columns:
        return html.Div("空間座標データがありません", className="text-muted p-3"), no_update, no_update, []

    if not rotation_store:
        rotation_store = {}
    if not name_map:
        name_map = {}

    try:
        # 必要時に expression_matrix.parquet を生成（初回 feature plot で 20-60 秒、以降は即座）
        try:
            _bridge.ensure_expression_matrix(rds_path)
        except Exception:
            pass  # 失敗時は R subprocess fallback に委ねる
        # Parquet からの高速読み込みを優先
        expression = None
        if cache_dir_str:
            cache_dir = Path(cache_dir_str)
            expression = _bridge.get_feature_expression_fast(
                cache_dir, feature_name
            )

        # Parquet にない場合は R subprocess で取得
        if expression is None:
            expression = _bridge.get_feature_expression(rds_path, feature_name)

        # expression を df に結合（CellID順で対応）
        df_plot = df.copy()
        df_plot["_expression"] = expression

        # P1: log10 表示トグル（MSI のダイナミックレンジ対策。以降の
        # global_min/max・正規化も変換後の値で一貫させる）
        if log_scale:
            df_plot["_expression"] = log_transform_intensities(
                df_plot["_expression"].values)

        # 表示対象サンプル
        if sample:
            samples_to_show = [sample]
        else:
            samples_to_show = sorted(df_plot["Sample"].unique())

        # 全サンプル共通のカラースケール範囲を計算
        expr_vals = df_plot.loc[
            df_plot["Sample"].isin(samples_to_show), "_expression"
        ].values
        if len(expr_vals) == 0 or np.all(np.isnan(expr_vals)):
            return html.Div("発現データがありません", className="text-muted p-3"), no_update, no_update, []
        global_min = float(np.nanmin(expr_vals))
        global_max = float(np.nanmax(expr_vals))
        if global_min == global_max:
            global_max = global_min + 1.0

        # ユーザー指定の Intensity Range（パーセント値）を強度値に変換
        val_range = global_max - global_min
        if intensity_min is not None:
            display_min = global_min + (intensity_min / 100.0) * val_range
        else:
            display_min = global_min
        if intensity_max is not None:
            display_max = global_min + (intensity_max / 100.0) * val_range
        else:
            display_max = global_max

        auto_mode = (marker_size is None or marker_size <= 0)

        graphs = []
        batch_fig_dicts = []
        for s in samples_to_show:
            df_s = df_plot[df_plot["Sample"] == s]
            display_s = _display_name(s, name_map)

            # 変換設定を取得
            transform = rotation_store.get(
                s, rotation_store.get("__all__", {"angle": 0, "flip_h": False, "flip_v": False}))
            if isinstance(transform, (int, float)):
                transform = {"angle": int(transform), "flip_h": False, "flip_v": False}

            raw_x = df_s["SpatialX"].values
            raw_y = -df_s["SpatialY"].values  # Y軸反転
            plot_x, plot_y = _transform_coords(
                raw_x, raw_y,
                transform.get("angle", 0),
                flip_h=transform.get("flip_h", False),
                flip_v=transform.get("flip_v", False),
            )

            # マーカーサイズ: 自動モード(0)ならサンプル毎に計算
            if auto_mode:
                m_size = _calc_zero_gap_marker_size(plot_x, plot_y, render_height=280)
            else:
                m_size = marker_size

            # 最後のサンプルのみカラーバーを表示
            is_last = (s == samples_to_show[-1])
            marker_opts = dict(
                size=m_size,
                symbol="square",
                color=df_s["_expression"].values,
                colorscale=colorscale or "Plasma",
                reversescale=bool(reverse_scale),
                cmin=display_min,
                cmax=display_max,
                showscale=is_last,
            )
            if is_last:
                marker_opts["colorbar"] = dict(
                    title=dict(text="Intensity", side="right"),
                    tickvals=[display_min, display_max],
                    ticktext=[
                        f"{int(intensity_min)}%" if intensity_min is not None else "0%",
                        f"{int(intensity_max)}%" if intensity_max is not None else "100%",
                    ],
                    len=0.8,
                    thickness=15,
                )

            fig = go.Figure()

            # TIC 背景（Greys, opacity=0.5）
            if "TotalCount" in df_s.columns:
                fig.add_trace(go.Scatter(
                    x=plot_x, y=plot_y, mode="markers",
                    marker=dict(size=m_size, symbol="square",
                                color=df_s["TotalCount"].values,
                                colorscale="Greys", opacity=0.5,
                                showscale=False),
                    hoverinfo="skip", showlegend=False,
                ))

            # 発現量オーバーレイ
            expr_raw = df_s["_expression"].values
            if display_max > display_min:
                norm = np.clip((expr_raw - display_min) / (display_max - display_min), 0, 1)
            else:
                norm = np.zeros_like(expr_raw)
            marker_opts["opacity"] = np.where(norm > 0.01, 0.3 + 0.7 * norm, 0.0).tolist()
            fig.add_trace(go.Scatter(
                x=plot_x,
                y=plot_y,
                mode="markers",
                marker=marker_opts,
                text=df_s["CellID"],
                hovertemplate=f"{feature_name}: " + "%{marker.color:.4f}<br>%{text}<extra></extra>",
                showlegend=False,
            ))

            r_margin = 80 if is_last else 10
            fig.update_layout(
                title=dict(text=display_s, font=dict(size=14), x=0.5),
                xaxis=dict(showgrid=False, showline=False, zeroline=False,
                           showticklabels=False, title="", visible=False),
                yaxis=dict(scaleanchor="x", showgrid=False, showline=False, zeroline=False,
                           showticklabels=False, title="", visible=False),
                margin=dict(l=10, r=r_margin, t=30, b=10),
                plot_bgcolor="white",
            )

            batch_fig_dicts.append((f"Feature_{feature_name}_{display_s}", fig.to_dict()))

            cfg = dict(_FEATURE_IMG_CONFIG)
            cfg["toImageButtonOptions"] = dict(cfg["toImageButtonOptions"],
                                               filename=f"Feature_{feature_name}_{display_s}")
            if columns_per_row:
                n_cols = columns_per_row
                gap_total = (n_cols - 1) * 15
                flex_basis = f"calc({100 / n_cols:.2f}% - {gap_total / n_cols:.1f}px)"
                min_w = "0"
            else:
                n_cols = len(samples_to_show)
                flex_basis = f"{max(20, 90 // n_cols)}%"
                min_w = "300px"
            graphs.append(
                html.Div(
                    style={"flex": f"1 1 {flex_basis}", "minWidth": min_w,
                            "border": "1px solid #dee2e6", "borderRadius": "6px",
                            "padding": "5px", "backgroundColor": "#fff"},
                    children=[
                        dcc.Graph(figure=fig, style={"height": "350px"}, config=cfg),
                    ],
                )
            )

        container = html.Div(
            style={"display": "flex", "flexWrap": "wrap", "gap": "15px"},
            children=graphs,
        )

        # --- 見出し（表示トグルで 化合物名 ⇄ m/z を切替）---
        ext_ann = _interactive_data.get("feature_annotations") or {}
        rec = ext_ann.get(feature_name)
        annotation = ""
        if deg_data:
            for r in deg_data:
                if r.get("gene") == feature_name:
                    ann = r.get("annotation", "")
                    if _is_meaningful_annotation(ann, feature_name):
                        annotation = ann
                    break
        if show_compound_names and rec and rec.get("display_name"):
            title_text = rec["display_name"]
        elif annotation:
            title_text = f"{feature_name}  ({annotation})"
        else:
            title_text = feature_name
        heading = html.H6(
            title_text,
            className="text-center mt-2 mb-1",
            style={"color": "#333", "fontSize": "0.95rem"},
        )

        return html.Div([heading, container]), "0", "100", batch_fig_dicts

    except Exception as e:
        return html.Div(f"\u30a8\u30e9\u30fc: {e}", className="text-danger p-3"), no_update, no_update, []


# ---------------------------------------------------------------------------
# Feature Plot ブックマークコールバック
# ---------------------------------------------------------------------------

@callback(
    Output("feature_history_store", "data"),
    Input("add_feature_bookmark_btn", "n_clicks"),
    [State("feature_select", "value"),
     State("feature_history_store", "data")],
    prevent_initial_call=True,
)
def add_feature_bookmark(n_clicks, feature_name, current_bookmarks):
    """ブックマーク追加ボタン -> 現在の Feature をブックマークストアに保存"""
    from app.callbacks.interactive_callbacks import _save_interactive_settings
    if not n_clicks or not feature_name:
        return no_update
    bookmarks = list(current_bookmarks) if current_bookmarks else []
    if feature_name in bookmarks:
        bookmarks.remove(feature_name)
    bookmarks.insert(0, feature_name)
    bookmarks = bookmarks[:50]
    _save_interactive_settings("feature_bookmarks", bookmarks)
    return bookmarks


@callback(
    [Output("feature_history_store", "data", allow_duplicate=True),
     Output("feature_history_select", "value")],
    Input("remove_feature_bookmark_btn", "n_clicks"),
    [State("feature_history_select", "value"),
     State("feature_history_store", "data")],
    prevent_initial_call=True,
)
def remove_feature_bookmark(n_clicks, selected, current_bookmarks):
    """選択中のブックマークを削除"""
    from app.callbacks.interactive_callbacks import _save_interactive_settings
    if not n_clicks or not selected or not current_bookmarks:
        return no_update, no_update
    bookmarks = list(current_bookmarks)
    if selected in bookmarks:
        bookmarks.remove(selected)
    _save_interactive_settings("feature_bookmarks", bookmarks)
    return bookmarks, None


@callback(
    Output("feature_history_select", "options"),
    Input("feature_history_store", "data"),
    State("deg_data_store", "data"),
    prevent_initial_call=True,
)
def update_bookmark_options(bookmarks, deg_data):
    """ブックマークストアからドロップダウンオプションを生成"""
    if not bookmarks:
        return []
    ann_map = {}
    if deg_data:
        for r in deg_data:
            gene = r.get("gene", "")
            ann = r.get("annotation", "")
            if gene and _is_meaningful_annotation(ann, gene):
                ann_map[gene] = ann
    return [
        {"label": f"{f} ({ann_map[f]})" if f in ann_map else f, "value": f}
        for f in bookmarks
    ]


@callback(
    Output("feature_select", "value", allow_duplicate=True),
    Input("feature_history_select", "value"),
    prevent_initial_call=True,
)
def bookmark_to_feature(selected):
    """ブックマークドロップダウンから選択 -> feature_select に値セット"""
    if not selected:
        return no_update
    return selected


# ---------------------------------------------------------------------------
# Volcano Plot（DEG インタラクティブ可視化）
# ---------------------------------------------------------------------------

@callback(
    Output("volcano_cluster_select", "options"),
    Output("volcano_highlight_name", "options"),
    Output("heatmap_cluster_select", "options"),
    Input("deg_data_store", "data"),
    Input("cluster_name_map_store", "data"),
    prevent_initial_call=True,
)
def update_volcano_cluster_options(deg_data, cluster_name_map=None):
    """DEGデータからVolcano Plotのクラスタ選択肢+ハイライト化合物名選択肢を生成"""
    if not deg_data:
        return [], [], []
    clusters = sorted(
        set(str(r.get("cluster", "")) for r in deg_data),
        key=_cluster_sort_key,
    )
    cluster_opts = [{"label": _cluster_display_name(c, cluster_name_map), "value": c} for c in clusters]

    # ハイライト用: 意味のあるアノテーション名を抽出
    ann_set = set()
    for r in deg_data:
        ann = r.get("annotation", "")
        gene = r.get("gene", "")
        if _is_meaningful_annotation(ann, gene):
            ann_set.add(ann)
    ann_opts = [{"label": a, "value": a} for a in sorted(ann_set)]

    return cluster_opts, ann_opts, cluster_opts


@callback(
    Output("volcano_plot", "figure"),
    [Input("volcano_cluster_select", "value"),
     Input("volcano_fc_threshold", "value"),
     Input("volcano_p_threshold", "value"),
     Input("volcano_y_max", "value"),
     Input("volcano_marker_size", "value"),
     Input("volcano_highlight_mz", "value"),
     Input("volcano_highlight_name", "value"),
     Input("volcano_label_top_n", "value"),
     Input("volcano_annotation_switch", "value")],
    State("deg_data_store", "data"),
    State("cluster_name_map_store", "data"),
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def update_volcano_plot(cluster, fc_thresh, p_thresh, y_max, marker_size,
                        highlight_mz, highlight_names, label_top_n,
                        annotation_on,
                        deg_data, cluster_name_map=None, rds_path=None):
    from app.callbacks.interactive_callbacks import _set_active_key
    _set_active_key(rds_path)
    """DEGデータからVolcano Plotを生成"""
    if not deg_data:
        return go.Figure()

    df = pd.DataFrame(deg_data)
    # p_val_adj_raw があればそちらを使用（文字列変換前の精度を保持）
    if "p_val_adj_raw" in df.columns:
        df["p_num"] = pd.to_numeric(df["p_val_adj_raw"], errors="coerce")
    else:
        df["p_num"] = pd.to_numeric(df["p_val_adj"], errors="coerce")
    df["avg_log2FC"] = pd.to_numeric(df["avg_log2FC"], errors="coerce")
    # p=0 は非ゼロ最小p値にclip
    min_nonzero_p = df.loc[df["p_num"] > 0, "p_num"].min() if (df["p_num"] > 0).any() else 5e-324
    df["neg_log10_p"] = -np.log10(df["p_num"].clip(lower=min_nonzero_p))

    # annotation列があれば、表示テキストに化合物名を含める
    # is_meaningful_annotation の判定をベクトル化（行ごと apply を回避）
    if "annotation" in df.columns:
        gene_s = df["gene"].fillna("").astype(str)
        ann_s = df["annotation"].fillna("").astype(str).str.strip()
        meaningful = (
            (ann_s != "")
            & (~ann_s.str.match(r"^[\d.]+$"))
            & (ann_s != gene_s)
        )
        df["display_text"] = np.where(
            meaningful, gene_s + "\n(" + ann_s + ")", gene_s
        )
    else:
        df["display_text"] = df["gene"]

    if cluster:
        df = df[df["cluster"].astype(str) == str(cluster)]

    fc_thresh = fc_thresh or 0.5
    p_thresh = p_thresh or 1.3
    marker_size = marker_size or 8

    fig = go.Figure()
    for reg, color, label in [
        ("Up", "#FF2D2D", "Up-regulated"),
        ("Down", "#1E5BFF", "Down-regulated"),
        ("NS", "#7A7A7A", "Not significant"),
    ]:
        if reg == "Up":
            mask = (df["neg_log10_p"] >= p_thresh) & (df["avg_log2FC"] >= fc_thresh)
        elif reg == "Down":
            mask = (df["neg_log10_p"] >= p_thresh) & (df["avg_log2FC"] <= -fc_thresh)
        else:
            mask = ~(
                (df["neg_log10_p"] >= p_thresh)
                & (df["avg_log2FC"].abs() >= fc_thresh)
            )
        sub = df[mask]
        if len(sub) > 0:
            fig.add_trace(go.Scatter(
                x=sub["avg_log2FC"],
                y=sub["neg_log10_p"],
                mode="markers",
                marker=dict(size=marker_size, color=color, opacity=0.7),
                name=label,
                text=sub["display_text"],
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "log2FC: %{x:.3f}<br>"
                    "-log10(p): %{y:.2f}<extra></extra>"
                ),
            ))

    # --- 自動ラベル: Top N UP + Top N DOWN ---
    _top_n = int(label_top_n or 5)
    if _top_n > 0 and annotation_on:
        sig_mask = (df["neg_log10_p"] >= p_thresh) & (df["avg_log2FC"].abs() >= fc_thresh)
        sig_df = df[sig_mask]
        top_up = sig_df[sig_df["avg_log2FC"] > 0].nlargest(_top_n, "avg_log2FC")
        top_down = sig_df[sig_df["avg_log2FC"] < 0].nsmallest(_top_n, "avg_log2FC")
        auto_label_df = pd.concat([top_up, top_down])
        if len(auto_label_df) > 0:
            # 重複防止: 各アノテーションのオフセットを計算
            points = list(zip(
                auto_label_df["avg_log2FC"].values,
                auto_label_df["neg_log10_p"].values,
                auto_label_df["display_text"].values,
            ))
            offsets = _compute_annotation_offsets(points)
            for (x_val, y_val, text_val), (ax, ay) in zip(points, offsets):
                fig.add_annotation(
                    x=x_val,
                    y=y_val,
                    text=text_val,
                    showarrow=True,
                    arrowhead=0,
                    arrowwidth=1,
                    arrowcolor="#999",
                    ax=ax,
                    ay=ay,
                    font=dict(size=9, color="#333"),
                    bgcolor="rgba(255,255,255,0.8)",
                    borderpad=2,
                )

    # --- ハイライトトレース ---
    hl_mask = pd.Series(False, index=df.index)

    # m/z 入力によるハイライト
    if highlight_mz and isinstance(highlight_mz, str) and highlight_mz.strip():
        tolerance = 0.01
        for token in highlight_mz.split(","):
            token = token.strip()
            try:
                target_mz = float(token)
            except ValueError:
                continue
            gene_mz = df["gene"].apply(_extract_mz_numeric)
            hl_mask = hl_mask | (np.abs(gene_mz - target_mz) <= tolerance)

    # 化合物名によるハイライト
    if highlight_names and "annotation" in df.columns:
        hl_mask = hl_mask | df["annotation"].isin(highlight_names)

    hl_df = df[hl_mask]
    if len(hl_df) > 0:
        # ラベルテキスト: アノテーションがあればそれ、なければ gene
        hl_labels = hl_df.apply(
            lambda r: r["annotation"]
            if "annotation" in r.index
            and _is_meaningful_annotation(r.get("annotation", ""), r.get("gene", ""))
            else r["gene"],
            axis=1,
        )
        fig.add_trace(go.Scatter(
            x=hl_df["avg_log2FC"],
            y=hl_df["neg_log10_p"],
            mode="markers+text",
            marker=dict(
                size=marker_size + 8,
                color="rgba(0,0,0,0)",
                line=dict(color="#00CC96", width=3),
            ),
            text=hl_labels,
            textposition="top center",
            textfont=dict(size=10, color="#00CC96"),
            name="Highlight",
            hovertemplate=(
                "<b>%{text}</b><br>"
                "log2FC: %{x:.3f}<br>"
                "-log10(p): %{y:.2f}<extra></extra>"
            ),
        ))

    # 閾値ライン
    fig.add_hline(y=p_thresh, line_dash="dash", line_color="gray", opacity=0.5)
    fig.add_vline(x=fc_thresh, line_dash="dash", line_color="gray", opacity=0.5)
    fig.add_vline(x=-fc_thresh, line_dash="dash", line_color="gray", opacity=0.5)

    title = (f"Volcano Plot - {_cluster_display_name(cluster, cluster_name_map)}" if cluster
             else "Volcano Plot (\u5168\u30af\u30e9\u30b9\u30bf)")
    yaxis_opts = {}
    if y_max is not None and y_max > 0:
        yaxis_opts["range"] = [0, y_max]
    fig.update_layout(
        title=dict(text=title, font=dict(size=14), x=0.5),
        xaxis_title="avg_log2FC",
        yaxis_title="-log10(p_val_adj)",
        yaxis=yaxis_opts,
        template="plotly_white",
        margin=dict(l=50, r=20, t=40, b=40),
    )
    return fig


# ---------------------------------------------------------------------------
# Heatmap（DEG Top N マーカーのクラスタ別平均発現量）
# ---------------------------------------------------------------------------

@callback(
    Output("heatmap_plot", "figure"),
    [Input("heatmap_top_n", "value"),
     Input("heatmap_scale", "value"),
     Input("heatmap_annotation_switch", "value"),
     Input("umap_merge_toggle", "value"),
     Input("heatmap_cluster_select", "value")],
    [State("deg_data_store", "data"),
     State("seurat_cache_dir_store", "data"),
     State("annotation_path", "value"),
     State("cluster_name_map_store", "data"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def update_heatmap(top_n, scale, annotation_on, merge_toggle, selected_cluster,
                   deg_data, cache_dir_str, mrm_path_str,
                   cluster_name_map=None, rds_path=None):
    """DEG Top N マーカーのクラスタ別平均発現量ヒートマップを生成"""
    from app.callbacks.interactive_callbacks import _set_active_key
    _set_active_key(rds_path)
    from app.callbacks.interactive_callbacks import _interactive_data
    from app.callbacks.interactive_calibration import (
        _build_mz_to_compound_map, _annotate_gene_labels,
    )
    if not deg_data or not cache_dir_str:
        return go.Figure()

    top_n = top_n or 5
    df_deg = pd.DataFrame(deg_data)
    df_deg["p_num"] = pd.to_numeric(df_deg["p_val_adj"], errors="coerce")

    # フォーカスクラスタが選択されている場合、そのクラスタのマーカーのみに絞る
    if selected_cluster:
        df_deg_filtered = df_deg[df_deg["cluster"].astype(str) == str(selected_cluster)]
    else:
        df_deg_filtered = df_deg

    # 各クラスタの Top N マーカーを抽出
    top_markers = df_deg_filtered.sort_values("p_num").groupby("cluster").head(top_n)
    genes = top_markers["gene"].unique().tolist()

    if not genes:
        fig = go.Figure()
        fig.add_annotation(
            text="\u30de\u30fc\u30ab\u30fc\u304c\u898b\u3064\u304b\u308a\u307e\u305b\u3093", showarrow=False,
            xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    # expression_matrix.parquet から発現量取得
    cache_dir = Path(cache_dir_str)
    expr_path = cache_dir / "expression_matrix.parquet"
    if not expr_path.exists():
        fig = go.Figure()
        fig.add_annotation(
            text="\u767a\u73fe\u91cf\u30c7\u30fc\u30bf\u304c\u3042\u308a\u307e\u305b\u3093", showarrow=False,
            xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    # 利用可能な遺伝子を Parquet schema から一括判定（I/O 1 回で完結）
    import pyarrow.parquet as pq
    try:
        schema_names = set(pq.read_schema(expr_path).names)
    except Exception:
        schema_names = set()
    available = [g for g in genes if g in schema_names]

    if not available:
        fig = go.Figure()
        fig.add_annotation(
            text="\u767a\u73fe\u91cf\u30ab\u30e9\u30e0\u304c\u898b\u3064\u304b\u308a\u307e\u305b\u3093", showarrow=False,
            xref="paper", yref="paper", x=0.5, y=0.5,
        )
        return fig

    expr_df = pd.read_parquet(expr_path, columns=["CellID"] + available)

    # クラスタ情報を結合
    plot_data = _interactive_data.get("plot_data")
    if plot_data is None:
        return go.Figure()

    # マージ表示切替: マージ時は Cluster_merged カラムを使用
    cluster_col = "Cluster"
    if merge_toggle == "merged" and "Cluster_merged" in plot_data.columns:
        cluster_col = "Cluster_merged"
    merged = expr_df.merge(
        plot_data[["CellID", cluster_col]].rename(columns={cluster_col: "Cluster"}),
        on="CellID", how="inner"
    )

    # クラスタ別平均発現量
    cluster_means = merged.groupby("Cluster")[available].mean()
    cluster_means = cluster_means.reindex(
        sorted(cluster_means.index, key=_cluster_sort_key)
    )

    # Z-score 変換（scipy なしで手動計算）
    z_data = cluster_means.values.copy()
    if scale == "zscore":
        col_mean = z_data.mean(axis=0)
        col_std = z_data.std(axis=0)
        col_std[col_std == 0] = 1
        z_data = (z_data - col_mean) / col_std

    # Y軸ラベル: アノテーション表示
    y_labels = available
    if annotation_on:
        # 1次ソース: CSV annotation列（deg_dataから取得）
        gene_to_annotation = {}
        if deg_data:
            for r in deg_data:
                gene = r.get("gene", "")
                ann = r.get("annotation", "")
                if gene and _is_meaningful_annotation(ann, gene):
                    gene_to_annotation[gene] = ann
        if gene_to_annotation:
            y_labels = [
                f"{g} ({gene_to_annotation[g]})" if g in gene_to_annotation else g
                for g in available
            ]
        elif mrm_path_str:
            # フォールバック: MRMファイルマッチング
            mz_to_compound = _build_mz_to_compound_map(mrm_path_str, tolerance=0.1)
            y_labels = _annotate_gene_labels(available, mz_to_compound, tolerance=0.1)

    fig = go.Figure(go.Heatmap(
        z=z_data.T,
        x=[str(c) for c in cluster_means.index],
        y=y_labels,
        colorscale="RdBu_r",
        zmid=0 if scale == "zscore" else None,
        hovertemplate=(
            "Cluster: %{x}<br>Gene: %{y}<br>"
            "Value: %{z:.3f}<extra></extra>"
        ),
    ))
    # Y軸ラベルの余白を動的に調整
    max_label_len = max(len(str(l)) for l in y_labels) if y_labels else 10
    left_margin = min(max(max_label_len * 7, 120), 350)
    fig.update_layout(
        title=dict(
            text=(f"Heatmap \u2014 {_cluster_display_name(selected_cluster, cluster_name_map)} vs Rest (Top {top_n})"
                  if selected_cluster
                  else f"Heatmap \u2014 All Clusters (Top {top_n})"),
            font=dict(size=14), x=0.5,
        ),
        xaxis_title="Cluster",
        yaxis_title="Gene / m/z",
        template="plotly_white",
        margin=dict(l=left_margin, r=20, t=40, b=40),
        yaxis=dict(autorange="reversed"),
    )
    return fig
