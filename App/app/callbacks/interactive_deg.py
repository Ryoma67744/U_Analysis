# =============================================================================
# MSI Analysis Application - Interactive DEG/Volcano/Heatmap/Feature Callbacks
# インタラクティブ解析 DEG・Volcano・Heatmap・Feature コールバック
#
# interactive_callbacks.py から分離された DEG 関連の
# ヘルパー関数・コールバックをまとめたモジュール。
# =============================================================================

import logging
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import (Input, Output, State, callback, ctx, no_update, html, dcc,
                  clientside_callback, ClientsideFunction, Patch, ALL)
from dash.exceptions import PreventUpdate

from app.utils.color_utils import (
    cluster_sort_key as _cluster_sort_key,
    cluster_display_name as _cluster_display_name,
)
from app.utils.display_helpers import (
    display_name as _display_name,
    transform_uirevision as _transform_uirevision,
)
from app.utils.deg_utils import (
    is_meaningful_annotation as _is_meaningful_annotation,
    extract_mz_numeric as _extract_mz_numeric,
)
from app.utils.annotation_label import (
    feature_display_label as _feature_display_label,
    label_from_active_state as _label_from_active_state,
)
from app.utils.label_persistence import (
    compute_annotation_offsets as _compute_annotation_offsets,
)

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
    # FUTURE(annot-provenance): 将来「由来表示」を足す場合、ann_map のラベルに
    #   app/services/annotation_sources.format_annotation_label() で由来（source）を併記する想定
    #   （例 "ATP (METASPACE, FDR=10%)"）。取込設計が未確定のため現状は変更なし。
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

    # ver51.8: 独自の正規表現をやめ共通窓口に統一した。
    # ★ 従来は「最初の数字」だったので annotated 名 (`PI 38:4 (...)_760.5851`) を
    #   38 と読み、m/z 範囲で絞ると脂質が丸ごと消えていた。
    # ★ さらに m/z を持たない feature を `filtered.append(f)` へ素通りさせており、
    #   範囲外でも残っていた。inf は「m/z 無し」なので範囲指定時は除外する。
    filtered = []
    for f in features:
        val = _extract_mz_numeric(f)
        if val == float("inf"):
            continue  # m/z が読めない feature は範囲指定時に含めない
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
    ann_map = _interactive_data.get("annotation_map") or {}

    def _opt(f):
        return {"label": _feature_display_label(
            f, annotation_map=ann_map, style="paren"), "value": f}

    if mz_filtered is None:
        # フィルタ解除 -> 全件に戻す
        features = _interactive_data.get("features_list")
        if not features:
            return []
        return [_opt(f) for f in features[:500]]

    return [_opt(f) for f in mz_filtered[:500]]


# ---------------------------------------------------------------------------
# Feature プロットの強度スタイル (ver51.5)
# ---------------------------------------------------------------------------
# 従来は閾値未満の点を visible_mask でトレースから **除外** していた。そのため
# 点の集合が m/z ごとに変わり、x/y/CellID を毎回送り直す必要があった
# (1 タイル 1.12MB gzip のうち 0.44MB がこの再送)。
#
# 全点を常に保持し、閾値未満は opacity=0 で隠す形に変えると、幾何が m/z に
# 依存しなくなり color/opacity/注記だけの差分更新にできる。
#
# ★ ただし **marker.opacity=0 の点でも hover は発生する** (実ブラウザで確認:
#   tests/e2e/test_render_perf.py::test_opacity_zero_points_still_respond_to_hover)。
#   Plotly には「トレース内の一部の点だけ hover 対象外にする」機能が無いので、
#   除外していた頃と厳密に同じにはできない。黙って変えるのではなく、
#   tooltip に「閾値未満」と明示する。
_BELOW_THRESHOLD_NOTE = "（閾値未満）"

# 殻 (グラフの枠・座標・TIC 背景) を作り直さなくてよいトリガ。
# これ以外 (サンプル・行数・回転・表示名など) は幾何が変わるので作り直す。
_FEATURE_DATA_ONLY_TRIGGERS = {
    "feature_select", "feature_intensity_min", "feature_intensity_max",
    "feature_show_compound_names",
}


def _feature_intensity_style(expr_raw, display_min, display_max):
    """強度から (色, 不透明度, 閾値未満の注記) の 3 配列を作る。

    いずれも点数と同じ長さで、**幾何とは独立**。m/z 切替で差し替えるのは
    この 3 つだけで済む。

    全点が閾値未満のときは従来どおり不透明度が全て 0 になる (カラーバーだけ残る)。
    """
    # 循環 import を避けるため関数内 import (呼び出し元の update_feature_plot と同じ流儀)
    from app.callbacks.interactive_spatial import _round_values_for_display

    expr_raw = np.asarray(expr_raw, dtype=float)
    if display_max > display_min:
        norm = np.clip((expr_raw - display_min) / (display_max - display_min), 0, 1)
    else:
        norm = np.zeros_like(expr_raw)
    alpha = np.where(norm > 0.01, 0.3 + 0.7 * norm, 0.0)
    below = np.where(alpha > 0.0, "", _BELOW_THRESHOLD_NOTE)
    return (_round_values_for_display(expr_raw),
            np.round(alpha, 3),
            below)


def _feature_display_range(expr_vals, intensity_min, intensity_max):
    """強度レンジ (%) から表示下限/上限の実値を出す。None なら描くものが無い。

    CB1 (殻の構築) と CB2 (差分更新) の両方が使う。片方だけ直すと
    「画面と一括保存で色域が違う」形で壊れるので 1 か所にまとめる。
    """
    if len(expr_vals) == 0 or np.all(np.isnan(expr_vals)):
        return None
    global_min = float(np.nanmin(expr_vals))
    global_max = float(np.nanmax(expr_vals))
    if global_min == global_max:
        global_max = global_min + 1.0
    val_range = global_max - global_min
    display_min = (global_min + (intensity_min / 100.0) * val_range
                   if intensity_min is not None else global_min)
    display_max = (global_min + (intensity_max / 100.0) * val_range
                   if intensity_max is not None else global_max)
    return display_min, display_max


def _feature_colorbar_ticktext(intensity_min, intensity_max):
    return [f"{int(intensity_min)}%" if intensity_min is not None else "0%",
            f"{int(intensity_max)}%" if intensity_max is not None else "100%"]


def _feature_graph_config(file_label, display_s):
    """PNG ダウンロード時のファイル名を含む Graph の config。

    ★ ファイル名は m/z に依存するので、図だけを差分更新するときも一緒に
      更新しないと、**古い m/z の名前で保存される**。config は数百バイトなので
      毎回送っても問題にならない。
    """
    cfg = dict(_FEATURE_IMG_CONFIG)
    cfg["toImageButtonOptions"] = dict(
        cfg["toImageButtonOptions"], filename=f"Feature_{file_label}_{display_s}")
    return cfg


def _feature_heading(feature_name, deg_data, show_compound_names, interactive_data):
    """「いまどの m/z を見ているか」の見出し。

    ver51.5 でコンテナの外に出した。m/z 切替でコンテナを作り直さなくなったため、
    中に入れたままだと表示が古いまま残る。
    """
    ext_ann = interactive_data.get("feature_annotations") or {}
    rec = ext_ann.get(feature_name)
    annotation = ""
    if deg_data:
        for r in deg_data:
            if r.get("gene") == feature_name:
                ann = r.get("annotation", "")
                if _is_meaningful_annotation(ann, feature_name):
                    annotation = ann
                break
    if not annotation:
        amap = interactive_data.get("annotation_map") or {}
        cand = amap.get(feature_name)
        if _is_meaningful_annotation(cand or "", feature_name):
            annotation = cand
    if show_compound_names and rec and rec.get("display_name"):
        title_text = rec["display_name"]
    elif annotation:
        title_text = f"{feature_name}  ({annotation})"
    else:
        title_text = feature_name
    return html.H6(title_text, className="text-center mt-2 mb-1",
                   style={"color": "#333", "fontSize": "0.95rem"})


# ---------------------------------------------------------------------------
# Feature プロット（Spatial表示、Parquet高速読み込み優先 -> R fallback）
# ---------------------------------------------------------------------------

@callback(
    [Output("feature_plot_container", "children"),
     Output("feature_plot_heading", "children"),
     Output("feature_intensity_min", "placeholder"),
     Output("feature_intensity_max", "placeholder")],
    [Input("feature_select", "value"),
     Input("feature_sample_select", "value"),
     Input("feature_intensity_min", "value"),
     Input("feature_intensity_max", "value"),
     Input("sample_name_map_store", "data"),
     Input("fullscreen_closed_trigger", "data"),
     Input("feature_rows_per_view", "value"),
     Input("feature_show_compound_names", "value")],
    # ver51.3: マーカーサイズと配色は figure のデータを変えないので Input から
    # 外し、clientside の Plotly.restyle (assets/feature_restyle.js) で処理する。
    # State に残すのは、他の理由で作り直すときに現在値でビルドするため。
    #
    # 強度レンジ (feature_intensity_min/max) は Input のまま。**見た目だけの
    # パラメータではない** — しきい値未満の点はトレースから除外しており
    # (visible_mask)、点の集合そのものが変わるので restyle では表現できない。
    [State("feature_marker_size", "value"),
     State("feature_colorscale", "value"),
     # ver51.5: いまブラウザに実在するグラフの id。サーバ側にメモを置く代わりに
     # これで「殻を作り直す必要があるか」を判断する。ページを再読み込みすると
     # 空になるので、**リロード後に自動で復帰する** (メモ方式だと画面が白いまま
     # 残りうる)。
     State({"type": "feature_graph", "index": ALL}, "id"),
     State("seurat_rds_path_store", "data"),
     State("seurat_cache_dir_store", "data"),
     State("spatial_rotation_store", "data"),
     State("deg_data_store", "data"),
     State("session_id_store", "data")],
    prevent_initial_call=True,
)
def update_feature_plot(feature_name, sample,
                        intensity_min, intensity_max,
                        name_map, _fs_trigger, rows,
                        show_compound_names,
                        marker_size, colorscale, existing_graph_ids,
                        rds_path, cache_dir_str, rotation_store,
                        deg_data, session_id=None):
    from app.callbacks.interactive_callbacks import (
        _interactive_data, _bridge, _set_active_key, set_export_figures)
    from app.callbacks.interactive_spatial import (
        _transform_coords, _calc_zero_gap_marker_size, _round_for_display,
        _round_values_for_display)
    from app.utils.perf_trace import perf_trace
    _set_active_key(rds_path)

    # ver51.5: 改善効果を推測ではなく実測で語れるようにする。
    # 無効時 (既定) は何もしない器が返るので、ホットパスに置いても実質ゼロコスト。
    with perf_trace("update_feature_plot") as _pt:
        return _update_feature_plot_inner(
            _pt, feature_name, sample, intensity_min, intensity_max,
            name_map, _fs_trigger, rows, show_compound_names,
            marker_size, colorscale, existing_graph_ids,
            rds_path, cache_dir_str, rotation_store,
            deg_data, session_id,
            _transform_coords, _calc_zero_gap_marker_size,
            _round_for_display, _round_values_for_display,
            _interactive_data, _bridge, set_export_figures)


def _update_feature_plot_inner(
        _pt, feature_name, sample, intensity_min, intensity_max,
        name_map, _fs_trigger, rows, show_compound_names,
        marker_size, colorscale, existing_graph_ids,
        rds_path, cache_dir_str, rotation_store,
        deg_data, session_id,
        _transform_coords, _calc_zero_gap_marker_size,
        _round_for_display, _round_values_for_display,
        _interactive_data, _bridge, set_export_figures):
    """update_feature_plot の本体 (perf_trace で包むために分離した)。"""

    def _finish(children, ph_min, ph_max, fig_dicts, heading=no_update):
        """ver46.1: 一括保存用 figure はサーバ側に保持し、ブラウザへは送らない。"""
        set_export_figures("feature", session_id, rds_path, fig_dicts)
        # ver51.5: PERF_TRACE=1 のときだけ転送量を測る (既定 OFF)。
        _pt.note(tiles=len(fig_dicts))
        _pt.measure_payload(children, "children")
        return children, heading, ph_min, ph_max
    # 名前変更・フルスクリーン閉鎖トリガーだがFeature未選択 -> スキップ
    if ctx.triggered_id in ("sample_name_map_store", "fullscreen_closed_trigger") and not feature_name:
        # ver51.6: Output は 4 つ (children / heading / 下限 / 上限)。
        # 見出しの Output を足したときにこの分岐だけ 3 値のままだった。
        return no_update, no_update, no_update, no_update

    if not feature_name or not rds_path:
        return _finish(html.Div("m/z Feature を選択してください", className="text-muted p-3"),
                       no_update, no_update, [])

    df = _interactive_data.get("plot_data")
    if df is None:
        return _finish(html.Div("データが読み込まれていません", className="text-muted p-3"),
                       no_update, no_update, [])

    if "SpatialX" not in df.columns:
        return _finish(html.Div("空間座標データがありません", className="text-muted p-3"),
                       no_update, no_update, [])

    if not rotation_store:
        rotation_store = {}
    if not name_map:
        name_map = {}

    # ver51.5: 殻 (グラフの枠・座標・TIC 背景) を作り直す必要があるかを判断する。
    # m/z や強度レンジを変えただけなら幾何は一切変わらないので、殻は据え置いて
    # patch_feature_intensity が figure だけを差分更新する。
    #
    # ★ 判断材料は **ブラウザに実在するグラフの id**。サーバ側にメモを持つと、
    #   ページ再読み込みでブラウザ側だけ空になったときに「作り直し不要」と
    #   誤判断して画面が白いまま残る。実在 id を見れば自動で復帰する。
    samples_to_show = [sample] if sample else sorted(df["Sample"].unique())
    heading = _feature_heading(feature_name, deg_data, show_compound_names,
                               _interactive_data)
    have = [str(d.get("index")) for d in (existing_graph_ids or []) if d]
    if (ctx.triggered_id in _FEATURE_DATA_ONLY_TRIGGERS
            and have == [str(s) for s in samples_to_show]):
        _pt.note(patched=1)
        # 見出しだけ更新して抜ける。図は CB2 が差分更新する。
        return no_update, heading, no_update, no_update

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

        # ver51.6: 差分更新側と同じ番人。取れない/長さが合わないまま
        # np.asarray して代入すると pandas が例外を投げ、画面が固まる。
        if expression is None or len(np.asarray(expression)) != len(df):
            logger.warning("発現量を取得できません (feature=%s)", feature_name)
            return _finish(
                html.Div("発現量を取得できませんでした", className="text-muted p-3"),
                no_update, no_update, [])

        # expression を df に結合（CellID順で対応）
        # ver46.1: 発現量 1 列を足すためだけに 10 万行の全列コピーをしていた。
        # Feature Plot が使う列だけを取り出す。
        _cols = [c for c in ("Sample", "SpatialX", "SpatialY", "CellID", "TotalCount")
                 if c in df.columns]
        df_plot = df[_cols].copy()
        df_plot["_expression"] = np.asarray(expression)

        # 表示対象サンプル (早期判定で既に決めてある)

        # 全サンプル共通のカラースケール範囲を計算
        expr_vals = df_plot.loc[
            df_plot["Sample"].isin(samples_to_show), "_expression"
        ].values
        rng = _feature_display_range(expr_vals, intensity_min, intensity_max)
        if rng is None:
            return _finish(html.Div("発現データがありません", className="text-muted p-3"),
                           no_update, no_update, [], heading=heading)
        display_min, display_max = rng

        auto_mode = (marker_size is None or marker_size <= 0)

        # ホバー/ファイル名用ラベル（化合物名を利用可能なら付与）。
        # ホバーは 化合物名⇄m/z トグルに追従、ファイル名は常に安全化した識別子。
        hover_label = _label_from_active_state(
            feature_name, style="paren", show_compound=bool(show_compound_names))
        file_label = _label_from_active_state(feature_name, style="filename")

        graphs = []
        export_figs = []
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
            # ver46.1: 表示用に座標の有効桁を落とす（転送量 2〜3 倍削減、見た目同一）
            plot_x, plot_y = _round_for_display(plot_x, plot_y)

            # マーカーサイズ: 自動モード(0)ならサンプル毎に計算。
            # ver51.3: 自動値は常に計算して layout.meta.auto_msz に載せる。
            # clientside restyle で「自動」に戻されたときの基準になる。
            auto_msz = _calc_zero_gap_marker_size(plot_x, plot_y, render_height=280)
            m_size = auto_msz if auto_mode else marker_size

            # 最後のサンプルのみカラーバーを表示
            is_last = (s == samples_to_show[-1])
            marker_opts = dict(
                size=m_size,
                symbol="square",
                color=df_s["_expression"].values,
                colorscale=colorscale or "Plasma",
                cmin=display_min,
                cmax=display_max,
                showscale=is_last,
            )
            if is_last:
                marker_opts["colorbar"] = dict(
                    title=dict(text="Intensity", side="right"),
                    tickvals=[display_min, display_max],
                    ticktext=_feature_colorbar_ticktext(intensity_min,
                                                       intensity_max),
                    len=0.8,
                    thickness=15,
                )

            fig = go.Figure()
            # ver51.3: どのトレースがどのコントロールに従うかを layout.meta に
            # 記録する。Spatial はトレースの meta を使っているが、Feature の
            # 発現トレースは meta を **hover ラベルの値** に使っている
            # (ver46.3: ユーザー提供の化合物名が "%{x}" を含みうるため)。
            # 上書きすると hovertemplate が壊れるので、こちらは添字で持つ。
            bg_idx = None

            # TIC 背景（Greys, opacity=0.5）
            # ver46.1: go.Scatter(SVG) -> go.Scattergl(WebGL)。SVG は 1 点 = 1 DOM ノードのため
            # 数万 spot で描画・パンが破綻していた（Spatial/UMAP は元から Scattergl）。
            if "TotalCount" in df_s.columns:
                fig.add_trace(go.Scattergl(
                    x=plot_x, y=plot_y, mode="markers",
                    marker=dict(size=m_size, symbol="square",
                                # ver51.3: 背景の TIC も同じ理由で丸める。
                                # hoverinfo="skip" なので数値は画面に出ない。
                                color=_round_values_for_display(
                                    df_s["TotalCount"].values),
                                colorscale="Greys", opacity=0.5,
                                showscale=False),
                    hoverinfo="skip", showlegend=False,
                ))
                bg_idx = len(fig.data) - 1

            # 発現量オーバーレイ
            # ver46.1: 強度しきい値未満の点は opacity=0 で「見えないのに転送・描画される」
            # だけだったので、トレースから除外する（見た目は同一、点数と転送量が減る）。
            # 全点が対象外になる場合（Intensity Range 下限 100% 等）は、カラーバーを
            # 従来どおり表示するため全点を残す（＝従来と完全に同じ図）。
            # ver51.5: 全点を常に保持する (幾何を m/z 非依存にする)。
            # 閾値未満は opacity=0 で隠し、tooltip に「閾値未満」と出す。
            fg_color, fg_alpha, fg_below = _feature_intensity_style(
                df_s["_expression"].values, display_min, display_max)
            fg_marker = dict(marker_opts)
            fg_marker["opacity"] = fg_alpha
            fg_marker["color"] = fg_color
            fig.add_trace(go.Scattergl(
                x=plot_x,
                y=plot_y,
                mode="markers",
                marker=fg_marker,
                text=df_s["CellID"].values,
                # ver51.5: 閾値未満の注記。ほぼ空文字なので gzip でほぼ消える。
                customdata=fg_below,
                # ver46.3: ラベル(化合物名)はユーザー提供のアノテーションファイル由来で、
                # "%{x}" 等の Plotly テンプレート記法を含み得る。hovertemplate に
                # 直接埋めると展開されてしまうため meta 経由で値として渡す。
                meta=hover_label,
                hovertemplate=("%{meta}: %{marker.color:.4f}%{customdata}"
                               "<br>%{text}<extra></extra>"),
                showlegend=False,
            ))

            fg_idx = len(fig.data) - 1
            r_margin = 80 if is_last else 10
            fig.update_layout(
                # ver51.3: 見た目だけのコントロール (マーカーサイズ / 配色) を
                # clientside restyle で処理するための索引。
                #   sz … marker.size がサイズ入力に従うトレース
                #   cs … marker.colorscale が配色プルダウンに従うトレース
                #        (TIC 背景は常に Greys なので入れない)
                meta=dict(kind="feature", auto_msz=float(auto_msz),
                          sz=[i for i in (bg_idx, fg_idx) if i is not None],
                          cs=[fg_idx]),
                title=dict(text=display_s, font=dict(size=14), x=0.5),
                xaxis=dict(showgrid=False, showline=False, zeroline=False,
                           showticklabels=False, title="", visible=False),
                yaxis=dict(scaleanchor="x", showgrid=False, showline=False, zeroline=False,
                           showticklabels=False, title="", visible=False),
                margin=dict(l=10, r=r_margin, t=30, b=10),
                plot_bgcolor="white",
                # ver46.1: 強度レンジ・配色・マーカーサイズ変更でズーム/パンを維持する。
                # 幾何が本当に変わる操作（サンプル・回転・反転）でのみリセットさせる。
                uirevision=_transform_uirevision(s, transform),
            )

            export_figs.append((f"Feature_{file_label}_{display_s}", fig.to_dict()))

            cfg = _feature_graph_config(file_label, display_s)
            if rows:
                # 行数指定: 1 行あたりの列数 = ceil(サンプル数 / 行数)。
                # 結果として最大 rows 行に折り返す（サンプル数 < rows なら 1 行）。
                n_cols = max(1, math.ceil(len(samples_to_show) / rows))
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
                        # ver46.1: id を付与し、React が再マウントではなく差分更新
                        # できるようにする（WebGL コンテキストの作り直しを避ける）。
                        dcc.Graph(id={"type": "feature_graph", "index": str(s)},
                                  figure=fig, style={"height": "350px"}, config=cfg),
                    ],
                )
            )

        container = html.Div(
            style={"display": "flex", "flexWrap": "wrap", "gap": "15px"},
            children=graphs,
        )

        return _finish(container, "0", "100", export_figs, heading=heading)

    except Exception as e:
        return _finish(html.Div(f"\u30a8\u30e9\u30fc: {e}", className="text-danger p-3"),
                       no_update, no_update, [])


# ---------------------------------------------------------------------------
# m/z 切替の差分更新 (ver51.5)
# ---------------------------------------------------------------------------
# 幾何 (座標・CellID・TIC 背景) は m/z に依存しないので、殻は据え置いて
# 発現トレースの color / opacity / 注記だけを差し替える。
#
# 実測 (50,000 点 / タイル、gzip 後):
#     殻ごと送る 1.12MB → 差分 0.180MB (color 0.135 + opacity 0.033 + 注記 0.012)
#
# 前例は interactive_loupe.umap_polygon_overlay (末尾トレースを Patch で更新)。
#
# ★ config も一緒に返す。PNG ダウンロードのファイル名が m/z を含むので、
#   図だけ更新すると**古い m/z の名前で保存される**。config は数百バイト。
#
# ★ 一括保存はサーバ保持の figure を使うので、そちらにも同じ差分を当てる。
#   でないと「画面と保存 PNG で m/z が違う」ことになる。

@callback(
    [Output({"type": "feature_graph", "index": ALL}, "figure"),
     Output({"type": "feature_graph", "index": ALL}, "config")],
    [Input("feature_select", "value"),
     Input("feature_intensity_min", "value"),
     Input("feature_intensity_max", "value"),
     Input("feature_show_compound_names", "value")],
    [State("feature_sample_select", "value"),
     State("sample_name_map_store", "data"),
     State("seurat_rds_path_store", "data"),
     State("seurat_cache_dir_store", "data"),
     State("session_id_store", "data")],
    prevent_initial_call=True,
)
def patch_feature_intensity(feature_name, intensity_min, intensity_max,
                            show_compound_names, sample, name_map, rds_path,
                            cache_dir_str, session_id=None):
    """m/z / 強度レンジの変更を figure の差分更新で反映する。"""
    from app.callbacks.interactive_callbacks import (
        _interactive_data, _bridge, _set_active_key,
        get_export_figures, set_export_figures)
    from app.utils.perf_trace import perf_trace

    out_ids = [o["id"] for o in (ctx.outputs_list[0] or [])]
    n = len(out_ids)
    if not n:
        # 殻がまだ無い (初回描画中など)。update_feature_plot が作る。
        return [], []
    if not feature_name or not rds_path:
        return [no_update] * n, [no_update] * n

    with perf_trace("patch_feature_intensity", tiles=n) as _pt:
        _set_active_key(rds_path)
        df = _interactive_data.get("plot_data")
        if df is None or "SpatialX" not in df.columns:
            return [no_update] * n, [no_update] * n

        with _pt.phase("read"):
            expression = None
            if cache_dir_str:
                expression = _bridge.get_feature_expression_fast(
                    Path(cache_dir_str), feature_name)
            if expression is None:
                expression = _bridge.get_feature_expression(rds_path, feature_name)

        # ver51.6: 発現量が取れない / 行数が合わないときは何も差し替えない。
        # ★ ここを素通りさせると np.asarray(None) や長さ不一致で pandas が
        #   例外を投げ、m/z を変えるたびにコールバックが落ちる。R フォールバック
        #   がヘッダ 1 行ぶん長い Series を返しうるので、長さは実際にずれうる。
        if expression is None or len(np.asarray(expression)) != len(df):
            logger.warning("発現量を差分更新に使えません (feature=%s)", feature_name)
            return [no_update] * n, [no_update] * n

        samples_to_show = [sample] if sample else sorted(df["Sample"].unique())
        _cols = [c for c in ("Sample", "TotalCount") if c in df.columns]
        df_plot = df[_cols].copy()
        df_plot["_expression"] = np.asarray(expression)

        expr_vals = df_plot.loc[
            df_plot["Sample"].isin(samples_to_show), "_expression"].values
        rng = _feature_display_range(expr_vals, intensity_min, intensity_max)
        if rng is None:
            return [no_update] * n, [no_update] * n
        display_min, display_max = rng

        hover_label = _label_from_active_state(
            feature_name, style="paren", show_compound=bool(show_compound_names))
        file_label = _label_from_active_state(feature_name, style="filename")
        ticktext = _feature_colorbar_ticktext(intensity_min, intensity_max)

        # 一括保存用にサーバが保持している figure にも同じ差分を当てる
        # ★ 保存用 figure の名前は **表示名** (name_map 適用後) で作られている。
        #   グラフ id は生のサンプル名なので、同じ変換を通してから対応付ける。
        stored = list(get_export_figures("feature", session_id, rds_path) or [])
        stored_by_name = {name: idx for idx, (name, _fd) in enumerate(stored)}

        figures, configs = [], []
        measured = {"color": [], "opacity": [], "note": []}
        # カラーバーは殻を作るとき最後のタイルにだけ付けている
        # (`is_last = (s == samples_to_show[-1])`)。差分でも同じタイルだけを
        # 触る。持っていないタイルに colorbar を書くと、そこに新しく
        # カラーバーが生えてしまう。
        colorbar_index = str(out_ids[-1].get("index")) if out_ids else None
        with _pt.phase("build"):
            for oid in out_ids:
                s_key = str(oid.get("index"))
                df_s = df_plot[df_plot["Sample"].astype(str) == s_key]
                if df_s.empty:
                    figures.append(no_update)
                    configs.append(no_update)
                    continue
                color, alpha, below = _feature_intensity_style(
                    df_s["_expression"].values, display_min, display_max)

                patched = Patch()
                # 発現トレースは常に最後。TIC 背景 (あれば) は触らない。
                patched["data"][-1]["marker"]["color"] = color
                patched["data"][-1]["marker"]["opacity"] = alpha
                patched["data"][-1]["marker"]["cmin"] = display_min
                patched["data"][-1]["marker"]["cmax"] = display_max
                patched["data"][-1]["customdata"] = below
                patched["data"][-1]["meta"] = hover_label
                if s_key == colorbar_index:
                    # ★ 目盛りの位置とラベルの両方。位置は cmin/cmax に、
                    #   ラベルは強度レンジ(%)に対応する。片方だけ直すと
                    #   カラーバーの読み方が狂う。
                    patched["data"][-1]["marker"]["colorbar"]["tickvals"] = [
                        display_min, display_max]
                    patched["data"][-1]["marker"]["colorbar"]["ticktext"] = ticktext
                figures.append(patched)
                configs.append(_feature_graph_config(
                    file_label, _display_name(s_key, name_map or {})))

                display_s = _display_name(s_key, name_map or {})
                new_name = f"Feature_{file_label}_{display_s}"
                si = next((stored_by_name[k] for k in stored_by_name
                           if k.endswith(f"_{display_s}")), None)
                if si is not None:
                    stored[si] = _apply_feature_data_to_stored(
                        stored[si], color, alpha, below, display_min,
                        display_max, hover_label, ticktext, new_name)
                measured["color"].append(color)
                measured["opacity"].append(alpha)
                measured["note"].append(below)

        if stored:
            set_export_figures("feature", session_id, rds_path, stored)
        # Patch オブジェクトは JSON 化できないので、実際に流れる配列そのものを測る
        _pt.measure_payload(measured, "patch")
        return figures, configs


def _apply_feature_data_to_stored(entry, color, alpha, below, cmin, cmax,
                                  hover_label, ticktext, new_name):
    """サーバ保持の一括保存用 figure に、画面と同じ差分を当てる。

    ここを忘れると「画面は新しい m/z、保存 PNG は古い m/z」になる。
    """
    try:
        name, fig_d = entry
        tr = (fig_d.get("data") or [])[-1]
        marker = tr.setdefault("marker", {})
        marker["color"] = color
        marker["opacity"] = alpha
        marker["cmin"] = cmin
        marker["cmax"] = cmax
        if isinstance(marker.get("colorbar"), dict):
            # ver51.6: 目盛りの **位置** も cmin/cmax に追従させる。
            # ticktext だけ直すと、ラベルは新しいのに目盛り線が古い位置に
            # 残り、カラーバーの読み方が狂う。
            marker["colorbar"]["tickvals"] = [cmin, cmax]
            marker["colorbar"]["ticktext"] = ticktext
        tr["customdata"] = below
        tr["meta"] = hover_label
        entry_list = list(entry)
        entry_list[0] = new_name
        # tuple は書き換えられないので呼び出し側のリスト要素を差し替える
        return tuple(entry_list)
    except Exception as e:  # noqa: BLE001 - 保存用の同期失敗で画面を壊さない
        logger.debug("一括保存用 figure の同期に失敗: %s", e)
        return entry


# ---------------------------------------------------------------------------
# 見た目だけのコントロール → clientside restyle (ver51.3)
# ---------------------------------------------------------------------------
# マーカーサイズと配色は figure のデータを変えないため、サーバで全タイルを
# 作り直さずブラウザ側で Plotly.restyle する（assets/feature_restyle.js）。
# ver46.1 で Spatial に入れた仕組み (spatial_restyle.js) の Feature 版。
# Output はダミー Store。実際の更新は JS が DOM 上のグラフに直接行う。
#
# 一括保存はサーバ保持の figure を使うので、そちらには
# display_helpers.apply_feature_display_overrides で同じ変換を掛ける
# (interactive_batch_save.cb_batch_save_feature)。

for _ctl_id, _fn in (
    ("feature_marker_size", "marker_size"),
    ("feature_colorscale", "colorscale"),
):
    clientside_callback(
        ClientsideFunction(namespace="feature_restyle", function_name=_fn),
        Output("feature_restyle_dummy", "data", allow_duplicate=True),
        Input(_ctl_id, "value"),
        prevent_initial_call=True,
    )


# ---------------------------------------------------------------------------
# Feature Plot ブックマークコールバック
# ---------------------------------------------------------------------------

@callback(
    Output("feature_history_store", "data"),
    Input("add_feature_bookmark_btn", "n_clicks"),
    [State("feature_select", "value"),
     State("feature_history_store", "data"),
     # ★ ver51.8: 保存先を決めるため rds_path を受け取る（元は無かった）
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def add_feature_bookmark(n_clicks, feature_name, current_bookmarks, rds_path):
    """ブックマーク追加ボタン -> 現在の Feature をブックマークストアに保存"""
    from app.callbacks.interactive_callbacks import (
        _save_interactive_settings, _set_active_key)
    if not n_clicks or not feature_name or not rds_path:
        return no_update
    # ★ active key を立てないと別プロジェクトのブックマークを書き換える
    _set_active_key(rds_path)
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
     State("feature_history_store", "data"),
     # ★ ver51.8: 保存先を決めるため rds_path を受け取る（元は無かった）
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def remove_feature_bookmark(n_clicks, selected, current_bookmarks, rds_path):
    """選択中のブックマークを削除"""
    from app.callbacks.interactive_callbacks import (
        _save_interactive_settings, _set_active_key)
    if not n_clicks or not selected or not current_bookmarks or not rds_path:
        return no_update, no_update
    _set_active_key(rds_path)
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
    # annotation_map（SCiLS/CSV 由来）も参照して化合物名を付与（deg のみに依存しない）
    return [
        {"label": _label_from_active_state(
            f, deg_annotation=ann_map.get(f), style="paren"), "value": f}
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
