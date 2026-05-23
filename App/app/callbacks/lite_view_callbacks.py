# =============================================================================
# MSI Analysis Application - Lite View Callbacks (Report Style)
#
# /lite/<project_id>/<sub_project_id> の「レポート型」サマリビュー。
# PPT 出力と同等の構造をブラウザ上で即時表示する。
#
# 大きく 4 系統の callback:
#   1. URL ルーティング (route_lite_url + navigate_to_lite_page)
#       url_bar.pathname を lite_target_store に変換し、その後ページ遷移
#       2 段に分けているのは share_callbacks.route_share_url とのハッシュ衝突回避のため
#   2. レポート初期化 (initialize_lite_view)
#       lite_target_store → 全レポート HTML を一気に組み立てて lv_report_body に投入
#       per-cluster カードの詳細部は遅延挿入のため初期 DOM に含まれない
#   3. Volcano 開閉 (toggle_volcano_section)
#       pattern-matching で各カードの Volcano セクションを折りたたむ
#   4. クラスタカード開閉 (toggle_cluster_card)
#       pattern-matching で各クラスタの UMAP/Spatial/Volcano グリッドを遅延描画
# =============================================================================

import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import (
    Input, Output, State, callback, html, dcc, dash_table, no_update,
    MATCH, ALL,
    clientside_callback,
)

logger = logging.getLogger(__name__)

from app.services.project_manager import get_project, get_sub_project
from app.callbacks.interactive_callbacks import (
    _detect_integration_methods,
    _cluster_sort_key,
    _get_cluster_color_map,
    _load_deg_results,
)
from app.callbacks.share_callbacks import _shared_data, _sv_bridge
from app.callbacks.interactive_umap import _build_umap_integrated_fig
from app.callbacks.interactive_spatial import _create_single_spatial_fig
from app.utils.deg_utils import is_meaningful_annotation
from app.utils.label_persistence import (
    load_interactive_settings,
    load_label_positions,
)

_LITE_URL_RE = re.compile(r"^/lite/([^/]+)/([^/]+)/?$")


# =============================================================================
# URL ルーティング
# =============================================================================

@callback(
    Output("lite_target_store", "data"),
    Input("url_bar", "pathname"),
    prevent_initial_call=True,
)
def route_lite_url(pathname):
    """URL パスが /lite/<project_id>/<sub_project_id> なら lite_target_store に書く"""
    if not pathname:
        return no_update
    m = _LITE_URL_RE.match(pathname)
    if not m:
        return no_update
    return {"project_id": m.group(1), "sub_project_id": m.group(2)}


@callback(
    Output("current_page", "data", allow_duplicate=True),
    Input("lite_target_store", "data"),
    prevent_initial_call=True,
)
def navigate_to_lite_page(target):
    """lite_target_store の更新をトリガに lite ページへ遷移。

    route_lite_url から current_page.data 出力を分離することで、
    share_callbacks.route_share_url（同じ Input=url_bar.pathname,
    Output=current_page.data allow_duplicate）と Dash の
    allow_duplicate ハッシュが衝突しないようにする。
    """
    if target and target.get("project_id") and target.get("sub_project_id"):
        return "lite"
    return no_update


# =============================================================================
# レポート初期化（メイン callback）
# =============================================================================

@callback(
    [Output("lv_report_body", "children"),
     Output("lv_error", "is_open"),
     Output("lv_error", "children")],
    [Input("lite_target_store", "data"),
     Input("lv_method_store", "data")],
    prevent_initial_call=True,
)
def initialize_lite_view(target, method_data):
    """lite_target_store / lv_method_store の更新で全レポートを構築する。"""
    if not target or not target.get("project_id"):
        return no_update, False, ""

    project_id = target["project_id"]
    sub_id = target["sub_project_id"]
    project = get_project(project_id)
    sub = get_sub_project(project_id, sub_id) if project else None
    if not sub:
        return (
            html.Div(),
            True,
            f"プロジェクトまたはサブプロジェクトが見つかりません: "
            f"{project_id}/{sub_id}",
        )

    result_dir = sub.get("last_result_dir") or sub.get("output_dir", "")
    rds_map = _detect_integration_methods(result_dir) if result_dir else {}
    if not rds_map:
        return (
            html.Div(),
            True,
            "解析結果が見つかりません（解析がまだ実行されていない可能性があります）。",
        )

    # 切り替え対応: lv_method_store 優先、無効値ならデフォルトにフォールバック
    requested_method = (method_data or {}).get("method")
    if requested_method and requested_method in rds_map:
        integration_method = requested_method
    else:
        integration_method = "Harmony" if "Harmony" in rds_map else next(iter(rds_map))
    rds_path = rds_map[integration_method]

    # データロード（lite 専用 cache key で _shared_data を流用）
    cache_key = f"lite::{project_id}::{sub_id}::{integration_method}"
    if cache_key not in _shared_data:
        try:
            extracted = _sv_bridge.extract_data(rds_path)
            _shared_data[cache_key] = {
                "plot_data": extracted["plot_data"],
                "cluster_stats": extracted["cluster_stats"],
                "features_list": extracted["features_list"],
                "meta": extracted["meta"],
                "rds_path": rds_path,
                "cache_dir": str(extracted["cache_dir"]),
            }
        except Exception as e:
            return html.Div(), True, f"RDS 読込エラー: {e}"

    data = _shared_data[cache_key]
    df_plot = data["plot_data"]
    df_stats = data["cluster_stats"]

    deg_records = []
    if result_dir and Path(result_dir).is_dir():
        deg_records = _load_deg_results(Path(result_dir), integration_method) or []
    logger.info(
        "lite_view DEG loaded: rows=%d integration=%s result_dir=%s",
        len(deg_records), integration_method, result_dir,
    )

    # メイン解析で永続化されたインタラクティブ設定 + ラベル位置を読み込む
    settings = load_interactive_settings(rds_path) or {}
    settings_mtime = "n/a"
    try:
        sp = Path(rds_path).parent / "interactive_settings.json" if rds_path else None
        if sp and sp.exists():
            from datetime import datetime as _dt
            settings_mtime = _dt.fromtimestamp(sp.stat().st_mtime).isoformat()
    except Exception:
        pass
    logger.info("lite_view settings loaded: mtime=%s keys=%s",
                settings_mtime, list(settings.keys()))
    cluster_name_map = settings.get("cluster_name_map") or {}
    sample_name_map = settings.get("sample_name_map") or {}
    spatial_rotation = settings.get("spatial_rotation") or {}
    custom_color_map = settings.get("custom_color_map") or None
    umap_display = settings.get("umap_display") or {}
    spatial_display = settings.get("spatial_display") or {}
    saved_positions_all = load_label_positions(rds_path, integration_method) or {}

    # per-cluster カード遅延展開時に再利用するため bundle をキャッシュ
    color_map = _get_cluster_color_map(df_plot["Cluster"], custom_color_map)
    _shared_data[cache_key]["_lite_bundle"] = {
        "df_plot": df_plot,
        "color_map": color_map,
        "deg_records": deg_records,
        "cluster_name_map": cluster_name_map,
        "sample_name_map": sample_name_map,
        "spatial_rotation": spatial_rotation,
        "saved_positions_all": saved_positions_all,
        "umap_display": umap_display,
        "spatial_display": spatial_display,
    }

    # レポート組み立て
    body = _build_report_body(
        project=project,
        sub=sub,
        integration_method=integration_method,
        available_methods=list(rds_map.keys()),
        df_plot=df_plot,
        df_stats=df_stats,
        deg_records=deg_records,
        cluster_name_map=cluster_name_map,
        sample_name_map=sample_name_map,
        spatial_rotation=spatial_rotation,
        custom_color_map=custom_color_map,
        saved_positions_all=saved_positions_all,
        umap_display=umap_display,
        spatial_display=spatial_display,
    )
    return body, False, ""


# Harmony / RPCA 切替: ラジオボタン → lv_method_store
@callback(
    Output("lv_method_store", "data"),
    Input("lv_method_selector", "value"),
    State("lv_method_store", "data"),
    prevent_initial_call=True,
)
def update_method_store(value, current):
    if not value:
        return no_update
    if (current or {}).get("method") == value:
        return no_update
    return {"method": value}


# =============================================================================
# Volcano 折りたたみ
# =============================================================================

@callback(
    Output({"type": "lv_volcano_collapse", "cluster": MATCH}, "is_open"),
    Input({"type": "lv_volcano_toggle", "cluster": MATCH}, "n_clicks"),
    State({"type": "lv_volcano_collapse", "cluster": MATCH}, "is_open"),
    prevent_initial_call=True,
)
def toggle_volcano_section(n_clicks, is_open):
    if n_clicks:
        return not is_open
    return is_open


# =============================================================================
# クラスタカード遅延展開
# =============================================================================

def _resolve_lite_data_for_target(target, method_data):
    """target/method から per-cluster カードの展開に必要な bundle を返す。

    initialize_lite_view が事前に `_shared_data[cache_key]["_lite_bundle"]` を
    詰めるため、通常はこのキャッシュをそのまま返すだけ（高速）。
    キャッシュ未ヒット時のみ RDS 再読込 + 設定ロードを実施する。

    Returns: dict(df_plot, color_map, deg_records, cluster_name_map,
                  sample_name_map, spatial_rotation, saved_positions_all)
             失敗時は None。
    """
    if not target or not target.get("project_id"):
        return None
    project_id = target["project_id"]
    sub_id = target.get("sub_project_id")
    if not sub_id:
        return None

    project = get_project(project_id)
    sub = get_sub_project(project_id, sub_id) if project else None
    if not sub:
        return None

    result_dir = sub.get("last_result_dir") or sub.get("output_dir", "")
    rds_map = _detect_integration_methods(result_dir) if result_dir else {}
    if not rds_map:
        return None

    requested_method = (method_data or {}).get("method")
    if requested_method and requested_method in rds_map:
        integration_method = requested_method
    else:
        integration_method = (
            "Harmony" if "Harmony" in rds_map else next(iter(rds_map))
        )
    rds_path = rds_map[integration_method]

    cache_key = f"lite::{project_id}::{sub_id}::{integration_method}"
    cached = _shared_data.get(cache_key)
    if cached and "_lite_bundle" in cached:
        return cached["_lite_bundle"]

    # キャッシュ未ヒット: RDS 抽出から再構築（initialize_lite_view 未実行などの
    # レアケース。サーバ再起動後にユーザーが lite ページに留まっていた場合など）
    try:
        if not cached:
            extracted = _sv_bridge.extract_data(rds_path)
            _shared_data[cache_key] = {
                "plot_data": extracted["plot_data"],
                "cluster_stats": extracted["cluster_stats"],
                "features_list": extracted["features_list"],
                "meta": extracted["meta"],
                "rds_path": rds_path,
                "cache_dir": str(extracted["cache_dir"]),
            }
            cached = _shared_data[cache_key]
    except Exception:
        return None

    df_plot = cached["plot_data"]
    deg_records = []
    if result_dir and Path(result_dir).is_dir():
        deg_records = _load_deg_results(Path(result_dir), integration_method) or []

    settings = load_interactive_settings(rds_path) or {}
    cluster_name_map = settings.get("cluster_name_map") or {}
    sample_name_map = settings.get("sample_name_map") or {}
    spatial_rotation = settings.get("spatial_rotation") or {}
    custom_color_map = settings.get("custom_color_map") or None
    umap_display = settings.get("umap_display") or {}
    spatial_display = settings.get("spatial_display") or {}
    saved_positions_all = load_label_positions(rds_path, integration_method) or {}
    color_map = _get_cluster_color_map(df_plot["Cluster"], custom_color_map)

    bundle = {
        "df_plot": df_plot,
        "color_map": color_map,
        "deg_records": deg_records,
        "cluster_name_map": cluster_name_map,
        "sample_name_map": sample_name_map,
        "spatial_rotation": spatial_rotation,
        "saved_positions_all": saved_positions_all,
        "umap_display": umap_display,
        "spatial_display": spatial_display,
    }
    cached["_lite_bundle"] = bundle
    return bundle


@callback(
    Output({"type": "lv_card_collapse", "cluster": MATCH}, "is_open",
           allow_duplicate=True),
    Output({"type": "lv_card_body", "cluster": MATCH}, "children",
           allow_duplicate=True),
    Output({"type": "lv_card_toggle", "cluster": MATCH}, "children",
           allow_duplicate=True),
    Input({"type": "lv_card_toggle", "cluster": MATCH}, "n_clicks"),
    State({"type": "lv_card_collapse", "cluster": MATCH}, "is_open"),
    State({"type": "lv_card_toggle", "cluster": MATCH}, "id"),
    State({"type": "lv_card_body", "cluster": MATCH}, "children"),
    State("lite_target_store", "data"),
    State("lv_method_store", "data"),
    prevent_initial_call=True,
)
def toggle_cluster_card(n_clicks, is_open, btn_id, current_body,
                          target, method_data):
    """個別カードの「詳細を表示／閉じる」トグル。

    開く操作のみ重量パートを生成する。閉じるときは children を保持
    （再展開を高速化）。
    """
    if not n_clicks:
        return no_update, no_update, no_update
    new_open = not is_open
    if new_open:
        if current_body:
            # 既に展開済みの DOM を保持していた場合は再構築不要
            return True, no_update, "▼ 詳細を閉じる"
        bundle = _resolve_lite_data_for_target(target, method_data)
        if bundle is None:
            return no_update, no_update, no_update
        contents = _build_cluster_card_expand_contents(
            btn_id["cluster"],
            df_plot=bundle["df_plot"],
            color_map=bundle["color_map"],
            deg_records=[
                r for r in bundle["deg_records"]
                if str(r.get("cluster", "")) == str(btn_id["cluster"])
            ],
            cluster_name_map=bundle["cluster_name_map"],
            sample_name_map=bundle["sample_name_map"],
            spatial_rotation=bundle["spatial_rotation"],
            saved_positions_all=bundle["saved_positions_all"],
            umap_display=bundle.get("umap_display") or {},
        )
        return True, contents, "▼ 詳細を閉じる"
    return False, no_update, "▶ 詳細を表示 (UMAP / Spatial / Volcano)"


# =============================================================================
# 番号 ON/OFF Switch による Per-sample Spatial Mapping の再描画
# =============================================================================

@callback(
    Output("lv_spatial_container", "children"),
    [Input({"type": "lv_show_labels_switch", "scope": ALL}, "value"),
     Input("lv_spatial_label_size", "value"),
     Input("lv_spatial_panel_size", "value")],
    State("lite_target_store", "data"),
    State("lv_method_store", "data"),
    prevent_initial_call=True,
)
def update_spatial_labels(show_labels_list, label_size, panel_size,
                            target, method_data):
    """「番号」Switch / ラベルサイズ / パネル高さの変更で
    Per-sample Spatial Mapping を再描画する (ver3.6 で size 制御追加)。

    Switch は initialize_lite_view 後に動的生成されるため、id を
    pattern-matching dict 形式にして Input も ALL pattern で受ける。
    bundle は _resolve_lite_data_for_target からキャッシュで返るため RDS
    再読込は発生しない。
    """
    show_labels = bool(show_labels_list[0]) if show_labels_list else False
    bundle = _resolve_lite_data_for_target(target, method_data)
    if bundle is None:
        return no_update
    saved_positions_all = bundle["saved_positions_all"] or {}
    spatial_pos = saved_positions_all.get("spatial") or {}
    return _build_per_sample_spatial(
        bundle["df_plot"], bundle["color_map"],
        highlight_clusters=None,
        cluster_name_map=bundle["cluster_name_map"],
        sample_name_map=bundle["sample_name_map"],
        spatial_rotation=bundle["spatial_rotation"],
        saved_positions_per_sample=spatial_pos,
        show_labels=show_labels,
        panel_height=int(panel_size) if panel_size else 350,
        spatial_display=bundle.get("spatial_display") or {},
        label_size_override=label_size,
    )


@callback(
    Output("lv_umap_container", "children"),
    [Input({"type": "lv_show_umap_labels_switch", "scope": ALL}, "value"),
     Input("lv_umap_label_size", "value"),
     Input("lv_umap_panel_size", "value")],
    State("lite_target_store", "data"),
    State("lv_method_store", "data"),
    prevent_initial_call=True,
)
def update_umap_labels(show_labels_list, label_size, panel_size,
                        target, method_data):
    """「番号」Switch / ラベルサイズ / パネル高さの変更で Per-sample UMAP
    grid を再描画する (ver3.6 で size 制御追加)。
    """
    show_labels = bool(show_labels_list[0]) if show_labels_list else False
    bundle = _resolve_lite_data_for_target(target, method_data)
    if bundle is None:
        return no_update
    saved_positions_all = bundle["saved_positions_all"] or {}
    umap_per_sample_pos = saved_positions_all.get("umap_per_sample") or {}
    return _build_per_sample_umap_grid(
        bundle["df_plot"], bundle["color_map"],
        cluster_name_map=bundle["cluster_name_map"],
        sample_name_map=bundle["sample_name_map"],
        saved_positions_per_sample=umap_per_sample_pos,
        umap_display=bundle.get("umap_display") or {},
        show_labels=show_labels,
        panel_height=int(panel_size) if panel_size else 340,
        label_size_override=label_size,
    )


# =============================================================================
# レポート構築ヘルパー（Phase 2 で /share/ に流用できる純関数として分離）
# =============================================================================

def _build_report_body(project, sub, integration_method, available_methods,
                       df_plot, df_stats, deg_records,
                       cluster_name_map=None, sample_name_map=None,
                       spatial_rotation=None, custom_color_map=None,
                       saved_positions_all=None, umap_display=None,
                       spatial_display=None):
    """全体レポートを html.Div の children リストとして返す。"""
    color_map = _get_cluster_color_map(df_plot["Cluster"], custom_color_map)
    return [
        _build_header(project, sub, integration_method, available_methods,
                      df_plot, df_stats, sample_name_map),
        _build_overview_section(
            df_plot, df_stats, color_map,
            cluster_name_map=cluster_name_map,
            sample_name_map=sample_name_map,
            spatial_rotation=spatial_rotation,
            saved_positions_all=saved_positions_all,
            umap_display=umap_display,
            spatial_display=spatial_display,
        ),
        _build_per_cluster_cards(
            df_plot, deg_records, color_map,
            cluster_name_map=cluster_name_map,
        ),
        _build_heatmap_section(deg_records, cluster_name_map=cluster_name_map),
    ]


def _resolve_sample_label(sample, sample_name_map):
    """サンプル名マップがあれば表示名を返す（無ければ元名）"""
    if sample_name_map and sample in sample_name_map:
        v = sample_name_map.get(sample)
        if v:
            return str(v)
    return str(sample)


def _build_header(project, sub, integration_method, available_methods,
                  df_plot, df_stats, sample_name_map=None):
    """ヘッダー: プロジェクト名 / 統合手法選択 / 統計サマリ / サンプル名リスト"""
    samples = (
        sorted(df_plot["Sample"].unique())
        if "Sample" in df_plot.columns
        else []
    )
    n_clusters = (
        int(df_stats["Cluster"].nunique())
        if df_stats is not None and "Cluster" in df_stats.columns
        else 0
    )
    n_cells = len(df_plot) if df_plot is not None else 0

    meta_items = [
        ("サンプル数", str(len(samples))),
        ("クラスタ数", str(n_clusters)),
        ("総セル数", f"{n_cells:,}"),
        ("解析日時",
         sub.get("last_modified") or sub.get("created_at", "不明")),
    ]
    meta_spans = [
        html.Span(
            [html.Strong(f"{label}: ", className="text-secondary"),
             html.Span(value)]
        ) for label, value in meta_items
    ]

    # 統合手法トグル（複数あるときだけ操作可能、1 つのときは表示のみ）
    methods = list(available_methods or [integration_method])
    if len(methods) >= 2:
        method_control = html.Span(
            [
                html.Strong("統合手法: ", className="text-secondary"),
                dcc.RadioItems(
                    id="lv_method_selector",
                    options=[{"label": m, "value": m} for m in methods],
                    value=integration_method,
                    inline=True,
                    inputStyle={"marginRight": "4px", "marginLeft": "8px"},
                    labelStyle={"marginRight": "10px"},
                ),
            ],
            style={"display": "inline-flex", "alignItems": "center"},
        )
    else:
        method_control = html.Span(
            [
                html.Strong("統合手法: ", className="text-secondary"),
                html.Span(integration_method or "—"),
                # 単一手法でも callback の Input id 解決ができるように非表示で残す
                dcc.RadioItems(
                    id="lv_method_selector",
                    options=[{"label": integration_method,
                              "value": integration_method}],
                    value=integration_method,
                    style={"display": "none"},
                ),
            ],
        )

    meta_row = html.Div(
        style={"display": "flex", "gap": "20px", "flexWrap": "wrap",
               "alignItems": "center"},
        children=[method_control, *meta_spans],
    )

    sample_labels = [_resolve_sample_label(s, sample_name_map) for s in samples]
    samples_line = (
        html.Small(
            [html.Strong("サンプル: "), ", ".join(sample_labels)],
            className="text-muted d-block mt-2",
        )
        if samples else None
    )

    return html.Div(
        className="report-header py-3 px-4 mb-4",
        style={
            "background": "#f8f9fa",
            "borderLeft": "4px solid #0d6efd",
            "borderRadius": "4px",
        },
        children=[
            html.H3(
                [project.get("name", ""), " / ", sub.get("name", "")],
                className="mb-2",
            ),
            meta_row,
            samples_line,
        ],
    )


def _build_overview_section(df_plot, df_stats, color_map,
                              cluster_name_map=None, sample_name_map=None,
                              spatial_rotation=None,
                              saved_positions_all=None,
                              umap_display=None,
                              spatial_display=None):
    """Overview: Sample 色統合 UMAP / Per-sample UMAP グリッド /
    Per-sample Spatial（クラスタ番号ラベル付き）/ Stats Table / Ratio Pie"""
    saved_positions_all = saved_positions_all or {}
    umap_pos = saved_positions_all.get("umap_integrated") or {}
    umap_per_sample_pos = saved_positions_all.get("umap_per_sample") or {}
    spatial_pos = saved_positions_all.get("spatial") or {}
    umap_display = umap_display or {}
    spatial_display = spatial_display or {}
    marker_size = umap_display.get("marker_size", 2) or 2

    # 1. Sample 色分け統合 UMAP（ラベルなしは固定: Sample 色のため番号ラベル非対象）
    sample_umap_fig = _build_umap_integrated_fig(
        df_plot, color_by="Sample", highlight_clusters=None,
        show_legend=True, show_labels=False, marker_size=marker_size,
    )
    sample_umap_fig.update_layout(
        height=420, margin=dict(l=10, r=10, t=10, b=10),
    )

    # 2. Per-sample UMAP（Cluster 色）グリッド（初期は番号 OFF。
    #    Switch トグルで show_labels が切り替わる）
    per_sample_umap_grid = _build_per_sample_umap_grid(
        df_plot, color_map,
        cluster_name_map=cluster_name_map,
        sample_name_map=sample_name_map,
        saved_positions_per_sample=umap_per_sample_pos,
        umap_display=umap_display,
        show_labels=False,
    )

    # 3. Per-sample Spatial（インタラクティブ側と同じく初期は番号 OFF、
    #    高さ 350px。Switch トグルで show_labels が切り替わる）
    spatial_grid = _build_per_sample_spatial(
        df_plot, color_map, highlight_clusters=None,
        cluster_name_map=cluster_name_map,
        sample_name_map=sample_name_map,
        spatial_rotation=spatial_rotation,
        saved_positions_per_sample=spatial_pos,
        show_labels=False,
        panel_height=350,
        spatial_display=spatial_display,
    )

    stats_table = _build_cluster_stats_table(df_stats)
    pie_fig = _build_cluster_ratio_pie(df_plot, color_map, cluster_name_map)

    # ver3.6: 軽量ビューア側でもプロットサイズ・ラベルサイズを調整可能に
    # 初期値はインタラクティブ側で保存された設定 (umap_display / spatial_display)
    # を採用し、ユーザー操作で即座に再描画される
    umap_init_label = umap_display.get("label_size", 11) or 11
    spatial_init_label = spatial_display.get("label_size", 10) or 10
    umap_init_panel = 340
    spatial_init_panel = 350

    def _size_toolbar(label_id, panel_id, init_label, init_panel,
                       label_range=(8, 30), panel_range=(200, 700)):
        """ラベルサイズ + パネル高さの数値入力 (compact horizontal layout)"""
        return html.Div(
            style={"display": "flex", "alignItems": "center",
                   "gap": "12px", "fontSize": "0.85rem"},
            children=[
                html.Span("ラベル", className="text-muted small"),
                dbc.Input(
                    id=label_id, type="number",
                    min=label_range[0], max=label_range[1], step=1,
                    value=int(init_label),
                    style={"width": "70px"}, size="sm",
                ),
                html.Span("パネル高", className="text-muted small"),
                dbc.Input(
                    id=panel_id, type="number",
                    min=panel_range[0], max=panel_range[1], step=20,
                    value=int(init_panel),
                    style={"width": "80px"}, size="sm",
                ),
                html.Span("px", className="text-muted small"),
            ],
        )

    return html.Div(
        className="overview-section mb-5",
        children=[
            html.H4("Overview", className="mb-3 border-bottom pb-2"),
            html.H6("Integrated UMAP (sample-colored)",
                    className="text-muted small"),
            dcc.Graph(figure=sample_umap_fig,
                      config={"displayModeBar": True}),
            html.Div(
                style={"display": "flex", "alignItems": "center",
                       "gap": "16px", "marginTop": "1rem",
                       "flexWrap": "wrap"},
                children=[
                    html.H6("Per-sample UMAP (cluster-colored)",
                            className="text-muted small mb-0"),
                    dbc.Switch(
                        id={"type": "lv_show_umap_labels_switch",
                            "scope": "main"},
                        label="番号",
                        value=False,
                        className="mb-0",
                    ),
                    _size_toolbar(
                        label_id="lv_umap_label_size",
                        panel_id="lv_umap_panel_size",
                        init_label=umap_init_label,
                        init_panel=umap_init_panel,
                    ),
                ],
            ),
            html.Div(
                id="lv_umap_container",
                children=per_sample_umap_grid,
            ),
            html.Div(
                style={"display": "flex", "alignItems": "center",
                       "gap": "16px", "marginTop": "1rem",
                       "flexWrap": "wrap"},
                children=[
                    html.H6("Per-sample Spatial Mapping",
                            className="text-muted small mb-0"),
                    dbc.Switch(
                        id={"type": "lv_show_labels_switch",
                            "scope": "main"},
                        label="番号",
                        value=False,
                        className="mb-0",
                    ),
                    _size_toolbar(
                        label_id="lv_spatial_label_size",
                        panel_id="lv_spatial_panel_size",
                        init_label=spatial_init_label,
                        init_panel=spatial_init_panel,
                    ),
                ],
            ),
            html.Div(
                id="lv_spatial_container",
                children=spatial_grid,
            ),
            dbc.Row([
                dbc.Col([
                    html.H6("Cluster Statistics",
                            className="text-muted small mt-4"),
                    stats_table,
                ], lg=6, md=12, className="mb-3"),
                dbc.Col([
                    html.H6("Cluster Ratio",
                            className="text-muted small mt-4"),
                    dcc.Graph(figure=pie_fig,
                              config={"displayModeBar": False}),
                ], lg=6, md=12, className="mb-3"),
            ]),
        ],
    )


def _build_per_sample_umap_grid(df_plot, color_map, cluster_name_map=None,
                                  sample_name_map=None,
                                  saved_positions_per_sample=None,
                                  panel_height=340,
                                  umap_display=None,
                                  show_labels=None,
                                  label_size_override=None):
    """サンプル別 Cluster 色分け UMAP グリッド（画像2 相当）

    show_labels は明示指定があればそれを優先、None なら既存通り
    umap_display.show_labels をフォールバック (簡易ビューアー上部の
    「番号」Switch から動的に切替えるために引数として受ける)。
    label_size_override: 軽量ビューア側 UI で変更した値があれば
      umap_display.label_size より優先 (ver3.6)。
    """
    if "Sample" not in df_plot.columns:
        return html.Div("Sample 列なし", className="text-muted small")
    samples = sorted(df_plot["Sample"].unique())
    if not samples:
        return html.Div("サンプルなし", className="text-muted small")

    saved_positions_per_sample = saved_positions_per_sample or {}
    umap_display = umap_display or {}
    marker_size = umap_display.get("marker_size", 2) or 2
    label_size = umap_display.get("label_size", 11) or 11
    # ver3.6: 軽量ビューア側 UI でユーザーが変更した値を優先
    if label_size_override is not None:
        try:
            label_size = int(label_size_override)
        except (TypeError, ValueError):
            pass
    if show_labels is None:
        show_labels = bool(umap_display.get("show_labels", False))
    else:
        show_labels = bool(show_labels)
    columns_per_row = umap_display.get("columns_per_row", 0) or 0
    col_lg = _calc_col_lg_width(columns_per_row, default_lg=6)
    # ver3.5: インタラクティブで除外したクラスタ・凡例表示設定を反映
    exclude_clusters = umap_display.get("exclude_cluster") or []
    show_legend_um = umap_display.get("show_legend")
    if show_legend_um is None:
        show_legend_um = True

    cols = []
    for s in samples:
        df_s = df_plot[df_plot["Sample"] == s]
        sample_pos = saved_positions_per_sample.get(s)
        title = _resolve_sample_label(s, sample_name_map)
        fig = _build_umap_integrated_fig(
            df_s, color_by="Cluster", highlight_clusters=None,
            show_legend=show_legend_um, show_labels=show_labels,
            marker_size=marker_size, label_size=label_size,
            exclude_clusters=exclude_clusters,
            custom_colors=color_map,
            cluster_name_map=cluster_name_map,
            saved_positions=sample_pos,
            title=title,
        )
        fig.update_layout(
            height=panel_height,
            margin=dict(l=10, r=10, t=30, b=10),
        )
        cols.append(dbc.Col(
            dcc.Graph(figure=fig, config={"displayModeBar": False},
                      style={"height": f"{panel_height}px"},
                      responsive=True),
            lg=col_lg, md=12, className="mb-2",
        ))
    return dbc.Row(cols, className="g-2")


def _calc_col_lg_width(columns_per_row, default_lg=6):
    """columns_per_row 値から dbc.Col の lg 幅(1-12)を算出。0 はデフォルト。"""
    if not columns_per_row or columns_per_row <= 0:
        return default_lg
    return max(1, 12 // columns_per_row)


def _build_per_sample_spatial(df_plot, color_map, highlight_clusters,
                              cluster_name_map=None, sample_name_map=None,
                              spatial_rotation=None,
                              saved_positions_per_sample=None,
                              show_labels=False,
                              panel_height=250,
                              spatial_display=None,
                              label_size_override=None):
    """各サンプル 1 パネルの Spatial グリッド（横並び・改行可）

    メイン解析で保存された rotation/flip/ラベル位置/サンプル名/クラスタ名/
    ラベルサイズ・マーカーサイズを反映する。
    spatial_display: interactive_settings.json の spatial_display dict。
    label_size_override: 軽量ビューア側 UI で変更した値があれば
      spatial_display.label_size より優先 (ver3.6)。
    """
    if "Sample" not in df_plot.columns:
        return html.Div("Spatial データなし",
                        className="text-muted small")
    samples = sorted(df_plot["Sample"].unique())
    if not samples:
        return html.Div("サンプルなし", className="text-muted small")

    spatial_rotation = spatial_rotation or {}
    saved_positions_per_sample = saved_positions_per_sample or {}
    spatial_display = spatial_display or {}
    # インタラクティブ側で設定された値を反映 (未設定なら従来デフォルト)
    sp_label_size = spatial_display.get("label_size") or 10
    # ver3.6: 軽量ビューア側 UI でユーザーが変更した値を優先
    if label_size_override is not None:
        try:
            sp_label_size = int(label_size_override)
        except (TypeError, ValueError):
            pass
    sp_marker_size = spatial_display.get("marker_size")
    if sp_marker_size is None:
        sp_marker_size = 0  # 0 = 自動計算
    # ver3.5: インタラクティブで除外したクラスタも軽量ビューアに反映
    sp_exclude = spatial_display.get("exclude_cluster") or []

    cols = []
    for s in samples:
        df_sample = df_plot[df_plot["Sample"] == s]
        rot = spatial_rotation.get(s, {}) or {}
        title = _resolve_sample_label(s, sample_name_map)
        # インタラクティブ側 (interactive_spatial.py:954-966) と引数を揃える:
        # label_size / marker_size / exclude_clusters はインタラクティブ側の
        # 設定値を尊重 (旧: hardcode。ver3.4 で label_size 修正、ver3.5 で
        # exclude_clusters を追加)。
        fig = _create_single_spatial_fig(
            df_sample, color_map, highlight_clusters,
            selected_cell_ids=None,
            rotation_deg=rot.get("angle", 0) or 0,
            flip_h=bool(rot.get("flip_h", False)),
            flip_v=bool(rot.get("flip_v", False)),
            show_labels=show_labels,
            cluster_name_map=cluster_name_map,
            saved_positions=saved_positions_per_sample.get(s),
            title=title,
            marker_size=sp_marker_size,
            label_size=sp_label_size,
            exclude_clusters=sp_exclude,
            embed_legend=True,
        )
        cols.append(
            dbc.Col(
                dcc.Graph(figure=fig,
                          config={"displayModeBar": False},
                          style={"height": f"{panel_height}px"},
                          responsive=True),
                lg=6, md=12, className="mb-2",
            )
        )
    return dbc.Row(cols, className="g-2")


def _build_per_sample_highlight_umap_grid(
    df_plot, color_map, highlight_cluster,
    cluster_name_map=None, sample_name_map=None,
    saved_positions_per_sample=None,
    bg_opacity=0.4, panel_height=300,
    umap_display=None,
):
    """サンプル別ハイライト UMAP グリッド（per-cluster カード用）

    background opacity を 0.4 に上げて、非ハイライト部分の輪郭が見える濃さにする。
    """
    if "Sample" not in df_plot.columns:
        return html.Div("Sample 列なし", className="text-muted small")
    samples = sorted(df_plot["Sample"].unique())
    if not samples:
        return html.Div("サンプルなし", className="text-muted small")

    saved_positions_per_sample = saved_positions_per_sample or {}
    umap_display = umap_display or {}
    marker_size = umap_display.get("marker_size", 2) or 2
    label_size = umap_display.get("label_size", 11) or 11
    show_labels = bool(umap_display.get("show_labels", False))
    columns_per_row = umap_display.get("columns_per_row", 0) or 0
    col_lg = _calc_col_lg_width(columns_per_row, default_lg=6)

    cols = []
    for s in samples:
        df_s = df_plot[df_plot["Sample"] == s]
        sample_pos = saved_positions_per_sample.get(s)
        title = _resolve_sample_label(s, sample_name_map)
        fig = _build_umap_integrated_fig(
            df_s, color_by="Cluster",
            highlight_clusters=[highlight_cluster],
            show_legend=False, show_labels=show_labels,
            marker_size=marker_size, label_size=label_size,
            custom_colors=color_map,
            cluster_name_map=cluster_name_map,
            saved_positions=sample_pos,
            bg_opacity=bg_opacity,
            title=title,
        )
        fig.update_layout(
            height=panel_height,
            margin=dict(l=10, r=10, t=30, b=10),
        )
        cols.append(dbc.Col(
            dcc.Graph(figure=fig, config={"displayModeBar": False},
                      style={"height": f"{panel_height}px"},
                      responsive=True),
            lg=col_lg, md=12, className="mb-1",
        ))
    return dbc.Row(cols, className="g-2")


def _build_cluster_stats_table(df_stats):
    """クラスタ統計テーブル"""
    if df_stats is None or df_stats.empty:
        return html.Div("統計データなし", className="text-muted small")
    return dash_table.DataTable(
        columns=[{"name": c, "id": c} for c in df_stats.columns],
        data=df_stats.to_dict("records"),
        style_cell={"fontSize": "13px", "padding": "6px",
                    "textAlign": "left"},
        style_header={"fontWeight": "bold", "backgroundColor": "#f1f3f5"},
        sort_action="native",
        page_action="none",
        style_table={"maxHeight": "400px", "overflowY": "auto"},
    )


def _build_cluster_ratio_pie(df_plot, color_map, cluster_name_map=None):
    """クラスタ構成比の Donut Pie"""
    if "Cluster" not in df_plot.columns:
        return go.Figure()
    counts = (
        df_plot["Cluster"].astype(str)
        .value_counts()
    )
    labels = sorted(counts.index, key=_cluster_sort_key)
    values = [int(counts[c]) for c in labels]
    colors = [color_map.get(c, "#888") for c in labels]
    cluster_name_map = cluster_name_map or {}
    display_labels = [
        cluster_name_map.get(c, f"Cluster {c}") for c in labels
    ]

    fig = go.Figure(data=[
        go.Pie(
            labels=display_labels,
            values=values,
            marker=dict(colors=colors),
            hole=0.3,
            sort=False,
        )
    ])
    fig.update_layout(
        height=400,
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=True,
        legend=dict(orientation="v", x=1.02, y=0.5),
    )
    return fig


def _build_per_cluster_cards(df_plot, deg_records, color_map,
                              cluster_name_map=None):
    """クラスタごとに 1 カード（軽量版）。

    カード詳細（UMAP/Spatial/Volcano）は遅延描画のため、ここでは
    ヘッダ + Top5 + トグルボタン + 空 Collapse のみ生成する。
    sample_name_map / spatial_rotation / saved_positions_all は
    toggle_cluster_card 側で _resolve_lite_data_for_target 経由で参照する。
    """
    if "Cluster" not in df_plot.columns:
        return html.Div()

    clusters = sorted(df_plot["Cluster"].astype(str).unique(),
                      key=_cluster_sort_key)
    n_total = len(df_plot)

    deg_by_cluster = {}
    for r in deg_records:
        c = str(r.get("cluster", ""))
        deg_by_cluster.setdefault(c, []).append(r)

    cards = []
    for c in clusters:
        df_c = df_plot[df_plot["Cluster"].astype(str) == c]
        n_c = len(df_c)
        pct = (n_c / n_total * 100) if n_total > 0 else 0.0
        color = color_map.get(c, "#888888")
        cards.append(
            _build_one_cluster_card(
                cluster_id=c,
                color=color,
                n_cells=n_c,
                pct=pct,
                deg_records=deg_by_cluster.get(c, []),
                cluster_name_map=cluster_name_map,
            )
        )

    return html.Div(
        className="per-cluster-section mb-5",
        children=[
            html.H4("Per-cluster Summary",
                    className="mb-3 border-bottom pb-2"),
            *cards,
        ],
    )


def _build_one_cluster_card(cluster_id, color, n_cells, pct,
                            deg_records,
                            cluster_name_map=None):
    """1 クラスタぶんのカード（軽量版: ヘッダ + Top5 + 詳細トグル）

    詳細パート（per-sample UMAP/Spatial/Volcano）はユーザーが「詳細を表示」を
    押すまで DOM に挿入されない。toggle_cluster_card callback が
    `lv_card_body` の children を差し込む。
    """
    cluster_key = str(cluster_id)
    cluster_display = (cluster_name_map or {}).get(
        cluster_key, f"Cluster {cluster_id}",
    )
    top_markers_view = _build_top_markers_table(deg_records, top_n=5)

    toggle_btn = dbc.Button(
        "▶ 詳細を表示 (UMAP / Spatial / Volcano)",
        id={"type": "lv_card_toggle", "cluster": cluster_key},
        size="sm", color="secondary", outline=True,
        className="mt-2",
        n_clicks=0,
    )
    collapse = dbc.Collapse(
        dcc.Loading(
            html.Div(
                id={"type": "lv_card_body", "cluster": cluster_key},
                children=[],
            ),
            type="default",
            color="#0d6efd",
        ),
        id={"type": "lv_card_collapse", "cluster": cluster_key},
        is_open=False,
    )

    return dbc.Card(
        className="cluster-card mb-3",
        children=dbc.CardBody([
            html.H5(
                [
                    html.Span(
                        "●",
                        style={"color": color, "marginRight": "8px",
                               "fontSize": "1.4em"},
                    ),
                    cluster_display,
                    html.Span(
                        f"  {n_cells:,} cells ({pct:.1f}%)",
                        style={"fontWeight": "normal", "color": "#666",
                               "marginLeft": "8px", "fontSize": "0.85em"},
                    ),
                ],
                className="cluster-card-header mb-3 pb-2 border-bottom",
            ),
            html.Div([
                html.H6(
                    "Top 5 Up-regulated Markers",
                    className="text-muted small",
                ),
                top_markers_view,
            ], className="mb-2"),
            toggle_btn,
            collapse,
        ]),
    )


def _build_cluster_card_expand_contents(cluster_id, df_plot, color_map,
                                         deg_records,
                                         cluster_name_map=None,
                                         sample_name_map=None,
                                         spatial_rotation=None,
                                         saved_positions_all=None,
                                         umap_display=None):
    """カード展開時に lv_card_body に差し込む重量パート。

    Per-sample Highlighted UMAP グリッド / Per-sample Spatial /
    Volcano (内側トグル付き) を返す children リスト。
    """
    saved_positions_all = saved_positions_all or {}
    umap_per_sample_pos = saved_positions_all.get("umap_per_sample") or {}
    spatial_pos = saved_positions_all.get("spatial") or {}

    hl_umap_grid = _build_per_sample_highlight_umap_grid(
        df_plot, color_map, highlight_cluster=cluster_id,
        cluster_name_map=cluster_name_map,
        sample_name_map=sample_name_map,
        saved_positions_per_sample=umap_per_sample_pos,
        bg_opacity=0.4,
        panel_height=280,
        umap_display=umap_display,
    )

    hl_spatial = _build_per_sample_spatial(
        df_plot, color_map, highlight_clusters=[cluster_id],
        cluster_name_map=cluster_name_map,
        sample_name_map=sample_name_map,
        spatial_rotation=spatial_rotation,
        saved_positions_per_sample=spatial_pos,
        show_labels=False,
        panel_height=280,
    )

    children = [
        html.H6("Highlighted UMAP (per sample)",
                className="text-muted small mt-3"),
        hl_umap_grid,
        html.H6("Highlighted Spatial (per sample)",
                className="text-muted small mt-3"),
        hl_spatial,
    ]

    if deg_records:
        volcano_fig = _build_volcano_fig(deg_records, cluster_id)
        cluster_key = str(cluster_id)
        children.extend([
            dbc.Button(
                "▼ Volcano Plot を表示",
                id={"type": "lv_volcano_toggle", "cluster": cluster_key},
                size="sm", color="secondary", outline=True,
                className="mt-2",
                n_clicks=0,
            ),
            dbc.Collapse(
                dcc.Graph(figure=volcano_fig,
                          config={"displayModeBar": True})
                if volcano_fig is not None
                else html.Div("Volcano 描画不可",
                              className="text-muted small"),
                id={"type": "lv_volcano_collapse", "cluster": cluster_key},
                is_open=False,
            ),
        ])

    return children


def _build_top_markers_table(deg_records, top_n=5):
    """このクラスタの DEG レコードから Up-regulated 上位 N 件のテーブル"""
    if not deg_records:
        return html.Div(
            "マーカーデータなし", className="text-muted small",
        )

    df = pd.DataFrame(deg_records)
    if "avg_log2FC" not in df.columns:
        return html.Div(
            "log2FC データなし", className="text-muted small",
        )
    df["avg_log2FC"] = pd.to_numeric(df["avg_log2FC"], errors="coerce")
    df = df.dropna(subset=["avg_log2FC"]).sort_values(
        "avg_log2FC", ascending=False,
    ).head(top_n)
    if df.empty:
        return html.Div(
            "上位マーカーなし", className="text-muted small",
        )

    # 表示カラム
    cols_order = []
    if "gene" in df.columns:
        cols_order.append("gene")
    if "annotation" in df.columns:
        cols_order.append("annotation")
    for c in ["avg_log2FC", "p_val", "p_val_adj"]:
        if c in df.columns:
            cols_order.append(c)

    # フォーマット
    df_view = df.copy()
    if "annotation" in df_view.columns and "gene" in df_view.columns:
        df_view["annotation"] = df_view.apply(
            lambda r: r["annotation"]
            if is_meaningful_annotation(
                r.get("annotation", ""), r.get("gene", "")
            ) else "",
            axis=1,
        )
    for c in ["avg_log2FC"]:
        if c in df_view.columns:
            df_view[c] = df_view[c].map(lambda v: f"{v:.2f}")
    for c in ["p_val", "p_val_adj"]:
        if c in df_view.columns:
            df_view[c] = pd.to_numeric(df_view[c], errors="coerce").map(
                lambda v: f"{v:.2e}" if pd.notna(v) else ""
            )

    return dash_table.DataTable(
        columns=[{"name": c, "id": c} for c in cols_order],
        data=df_view[cols_order].to_dict("records"),
        style_cell={"fontSize": "12px", "padding": "4px",
                    "textAlign": "left"},
        style_header={"fontWeight": "bold", "backgroundColor": "#f1f3f5",
                      "fontSize": "12px"},
        style_table={"overflowX": "auto"},
    )


def _build_volcano_fig(deg_records, cluster_id):
    """シンプルな Volcano Plot（このクラスタ分のみ）"""
    if not deg_records:
        return None
    df = pd.DataFrame(deg_records)
    if "cluster" in df.columns:
        df = df[df["cluster"].astype(str) == str(cluster_id)]
    if df.empty or "avg_log2FC" not in df.columns:
        return None

    if "p_val_adj_raw" in df.columns:
        df["p_num"] = pd.to_numeric(df["p_val_adj_raw"], errors="coerce")
    elif "p_val_adj" in df.columns:
        df["p_num"] = pd.to_numeric(df["p_val_adj"], errors="coerce")
    else:
        df["p_num"] = pd.to_numeric(df.get("p_val"), errors="coerce")
    df["avg_log2FC"] = pd.to_numeric(df["avg_log2FC"], errors="coerce")
    df = df.dropna(subset=["p_num", "avg_log2FC"])
    if df.empty:
        return None

    min_pos = df.loc[df["p_num"] > 0, "p_num"].min() if (df["p_num"] > 0).any() else 5e-324
    df["neg_log10_p"] = -np.log10(df["p_num"].clip(lower=min_pos))

    fc_thresh = 0.5
    p_thresh = 1.3  # -log10(0.05)

    df["category"] = "NS"
    df.loc[
        (df["avg_log2FC"] >= fc_thresh) & (df["neg_log10_p"] >= p_thresh),
        "category",
    ] = "Up"
    df.loc[
        (df["avg_log2FC"] <= -fc_thresh) & (df["neg_log10_p"] >= p_thresh),
        "category",
    ] = "Down"

    # hover テキスト
    if "gene" in df.columns and "annotation" in df.columns:
        df["hover"] = df.apply(
            lambda r: f"{r['gene']}<br>({r['annotation']})"
            if is_meaningful_annotation(
                r.get("annotation", ""), r.get("gene", "")
            ) else r["gene"],
            axis=1,
        )
    elif "gene" in df.columns:
        df["hover"] = df["gene"].astype(str)
    else:
        df["hover"] = ""

    fig = go.Figure()
    for cat, color in [
        ("NS", "#bbbbbb"),
        ("Up", "#FF2D2D"),
        ("Down", "#2D6FFF"),
    ]:
        d = df[df["category"] == cat]
        if d.empty:
            continue
        fig.add_trace(go.Scatter(
            x=d["avg_log2FC"],
            y=d["neg_log10_p"],
            mode="markers",
            marker=dict(size=6, color=color, opacity=0.7),
            name=cat,
            text=d["hover"],
            hoverinfo="text+x+y",
        ))
    fig.add_hline(y=p_thresh, line_dash="dash",
                  line_color="gray", opacity=0.5)
    fig.add_vline(x=fc_thresh, line_dash="dash",
                  line_color="gray", opacity=0.5)
    fig.add_vline(x=-fc_thresh, line_dash="dash",
                  line_color="gray", opacity=0.5)

    # 95 percentile auto cap: 極端に小さい p 値 (=非常に大きな -log10) で
    # y 軸が引き伸ばされ大多数の点が圧縮されるのを防ぐ
    if len(df) > 0:
        p95 = float(df["neg_log10_p"].quantile(0.95))
        y_max = max(p_thresh * 2, p95 * 1.1)
    else:
        y_max = 10.0

    fig.update_layout(
        xaxis_title="log2 Fold Change",
        yaxis_title="-log10(p-value)",
        yaxis=dict(range=[0, y_max]),
        height=400,
        margin=dict(l=50, r=10, t=10, b=40),
        showlegend=True,
        legend=dict(orientation="h", x=0, y=1.02),
    )
    return fig


def _build_heatmap_section(deg_records, top_n_per_cluster=3,
                            cluster_name_map=None):
    """全クラスタ × 各クラスタ Top N markers の Z-score ヒートマップ"""
    if not deg_records:
        return html.Div(
            className="heatmap-section mb-5",
            children=[
                html.H4("Cross-cluster Heatmap",
                        className="mb-3 border-bottom pb-2"),
                html.Div("DEG データがありません",
                         className="text-muted small"),
            ],
        )

    df = pd.DataFrame(deg_records)
    needed = {"cluster", "gene", "avg_log2FC"}
    if not needed.issubset(df.columns):
        return html.Div(
            className="heatmap-section mb-5",
            children=[
                html.H4("Cross-cluster Heatmap",
                        className="mb-3 border-bottom pb-2"),
                html.Div("ヒートマップ用列が不足しています",
                         className="text-muted small"),
            ],
        )

    df["avg_log2FC"] = pd.to_numeric(df["avg_log2FC"], errors="coerce")
    # gene / cluster は前後空白や型不整合で reindex / pivot が空になる事があるため
    # ここで明示的に文字列化 + strip して正規化する
    df["gene"] = df["gene"].astype(str).str.strip()
    df["cluster"] = df["cluster"].astype(str).str.strip()
    df = df.dropna(subset=["avg_log2FC", "cluster", "gene"])
    df = df[(df["gene"] != "") & (df["cluster"] != "")]

    clusters = sorted(df["cluster"].unique(), key=_cluster_sort_key)

    # 各クラスタ上位 N gene を集約
    top_genes = []
    seen = set()
    for c in clusters:
        df_c = df[df["cluster"] == c]
        for g in df_c.nlargest(top_n_per_cluster, "avg_log2FC")["gene"].tolist():
            if g not in seen:
                top_genes.append(g)
                seen.add(g)
    if not top_genes:
        return html.Div(
            className="heatmap-section mb-5",
            children=[
                html.H4("Cross-cluster Heatmap",
                        className="mb-3 border-bottom pb-2"),
                html.Div("上位マーカーが抽出できません",
                         className="text-muted small"),
            ],
        )

    # gene × cluster pivot
    pivot = df.pivot_table(
        index="gene", columns="cluster",
        values="avg_log2FC", aggfunc="mean",
    )
    pivot = pivot.reindex(top_genes)
    # reindex 後に全行 NaN になる事があり (型不一致や CSV 由来の空白で起きる)、
    # 残った有効行のみを使う。完全に空ならフォールバック表示。
    pivot = pivot.dropna(how="all")
    if pivot.empty:
        logger.warning(
            "heatmap pivot empty after reindex: deg_rows=%d clusters=%s "
            "top_genes_sample=%s",
            len(df), clusters[:5], top_genes[:5],
        )
        return html.Div(
            className="heatmap-section mb-5",
            children=[
                html.H4(
                    f"Cross-cluster Heatmap (Top {top_n_per_cluster} markers / cluster)",
                    className="mb-3 border-bottom pb-2",
                ),
                html.Div(
                    "ヒートマップ用データが生成できませんでした"
                    "（DEG と top markers の遺伝子名がマッチしません）",
                    className="text-muted small",
                ),
            ],
        )
    cluster_cols = [c for c in clusters if c in pivot.columns]
    pivot = pivot[cluster_cols].fillna(0.0)

    cluster_name_map = cluster_name_map or {}
    x_labels = [
        cluster_name_map.get(str(c), f"C{c}") for c in pivot.columns
    ]

    fig = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=x_labels,
        y=pivot.index.astype(str),
        colorscale="RdBu_r",
        zmid=0,
        colorbar=dict(title="log2FC"),
        hovertemplate="Cluster %{x}<br>Gene: %{y}<br>log2FC: %{z:.2f}<extra></extra>",
    ))
    fig.update_layout(
        height=max(300, 22 * len(pivot.index) + 80),
        margin=dict(l=140, r=20, t=20, b=60),
        xaxis=dict(tickangle=0),
    )
    return html.Div(
        className="heatmap-section mb-5",
        children=[
            html.H4(
                f"Cross-cluster Heatmap (Top {top_n_per_cluster} markers / cluster)",
                className="mb-3 border-bottom pb-2",
            ),
            dcc.Graph(figure=fig, config={"displayModeBar": True}),
        ],
    )


# =============================================================================
# 「軽量ビューアを開く」ボタン
# メイン解析画面（インタラクティブ解析タブ）から新タブで /lite/<pid>/<sid> を開く
# =============================================================================

# --- Server callback: ボタンクリックで現在の Store 値を JSON へ flush ---
#  flip/rotation・サンプル名・クラスタ名・カラーマップ など、UI 側で変更直後に
#  Dash の非同期更新と新タブ open の競合で「軽量ビューアが古い設定を読み込む」
#  問題を防ぐため、新タブを開く前にここで同期保存して signal を出す。
@callback(
    Output("lite_viewer_open_signal", "data"),
    Input("btn_open_lite_viewer", "n_clicks"),
    [State("spatial_rotation_store", "data"),
     State("sample_name_map_store", "data"),
     State("cluster_name_map_store", "data"),
     State("custom_color_map_store", "data"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def _flush_settings_before_lite_open(
    n_clicks, spatial_rotation, sample_name_map,
    cluster_name_map, custom_color_map, rds_path,
):
    if not n_clicks or not rds_path:
        return no_update
    from app.utils.label_persistence import save_interactive_settings
    try:
        if spatial_rotation:
            save_interactive_settings("spatial_rotation", spatial_rotation, rds_path)
        if sample_name_map:
            save_interactive_settings("sample_name_map", sample_name_map, rds_path)
        if cluster_name_map:
            save_interactive_settings("cluster_name_map", cluster_name_map, rds_path)
        if custom_color_map:
            save_interactive_settings("custom_color_map", custom_color_map, rds_path)
        logger.info(
            "lite_viewer pre-open flush done: rds_path=%s rotation_keys=%d "
            "sample_map_keys=%d cluster_map_keys=%d color_map_keys=%d",
            rds_path,
            len(spatial_rotation or {}), len(sample_name_map or {}),
            len(cluster_name_map or {}), len(custom_color_map or {}),
        )
    except Exception as e:
        logger.warning("lite_viewer pre-open flush failed: %s", e)
    # シグナルとして click ID を返す（変化を伝えれば良いので n_clicks を使う）
    import time
    return int(time.time() * 1000)


clientside_callback(
    """
    function(signal_ts, project_id, sub_project_id) {
        if (!signal_ts || !project_id || !sub_project_id) {
            return window.dash_clientside.no_update;
        }
        const url = `/lite/${encodeURIComponent(project_id)}/${encodeURIComponent(sub_project_id)}`;
        window.open(url, '_blank');
        return window.dash_clientside.no_update;
    }
    """,
    # NOTE: Output は btn_open_lite_viewer.n_clicks に書き戻すと、上の
    # server callback (n_clicks → signal) と合わせて 2 node の循環依存に
    # なり Dash が登録段階で reject する。Dash は no_update を返しても
    # 静的グラフ解析で循環を検出するため、ダミー Store を Output に使う。
    Output("lite_viewer_open_dummy", "data"),
    Input("lite_viewer_open_signal", "data"),
    [State("interactive_project_select", "value"),
     State("interactive_sub_project_select", "value")],
    prevent_initial_call=True,
)


# =============================================================================
# Plotly 強制リサイズ + autorange (clientside)
# =============================================================================
# 新規 mount された Plotly Graph は親要素サイズの取得タイミングによっては
# 内部レイアウトが height=0 のまま固まる (lazy rendering)。さらに
# _create_single_spatial_fig は xaxis.range を明示せず autorange に依存
# しているため、新規 mount で autorange 計算がスキップされると **データが
# 画面外** に出て空白に見える (UMAP は座標が小さく問題に出にくいが、
# Spatial はピクセル座標が大きく顕在化)。
#
# Plotly のツールバー左上「Autoscale/Reset axes」ボタンを押すと描画が走るのと
# 同じことを clientside callback で自動的に行う:
#   1. Plotly.Plots.resize(el)        — レイアウト再計算
#   2. Plotly.relayout(el, autorange) — axis range を data に合わせて再計算
#
# トリガー:
#   - {"type": "lv_card_collapse", "cluster": ALL}.is_open (カード展開)
#   - lv_show_labels_switch.value (Spatial 番号 Switch トグル)
#   - lv_show_umap_labels_switch.value (UMAP 番号 Switch トグル)
#   - lv_method_store.data (Harmony/RPCA 切替)
#   - lite_target_store.data (初回 URL ロード)
# 100ms / 350ms / 800ms / 1500ms と複数のタイミングで処理を呼ぶことで、
# dbc.Collapse のアニメーション完了や initialize_lite_view の重い構築完了
# 直後など、複数の遅延ケースをまとめてカバーする。
clientside_callback(
    """
    function(is_open_list, switch_value, umap_switch_value, method_data, target_data) {
        [100, 350, 800, 1500].forEach(function(delay) {
            setTimeout(function() {
                document.querySelectorAll('.js-plotly-plot').forEach(function(el) {
                    if (!window.Plotly || !el || !el.layout) return;
                    try {
                        window.Plotly.Plots.resize(el);
                    } catch (e) {}
                    try {
                        var update = {};
                        Object.keys(el.layout).forEach(function(key) {
                            if (key.indexOf('xaxis') === 0 || key.indexOf('yaxis') === 0) {
                                update[key + '.autorange'] = true;
                            }
                        });
                        if (Object.keys(update).length > 0) {
                            window.Plotly.relayout(el, update);
                        }
                    } catch (e) {}
                });
            }, delay);
        });
        return Date.now();
    }
    """,
    Output("lv_resize_trigger", "data"),
    [
        Input({"type": "lv_card_collapse", "cluster": ALL}, "is_open"),
        Input({"type": "lv_show_labels_switch", "scope": ALL}, "value"),
        Input({"type": "lv_show_umap_labels_switch", "scope": ALL}, "value"),
        Input("lv_method_store", "data"),
        Input("lite_target_store", "data"),
    ],
    prevent_initial_call=True,
)
