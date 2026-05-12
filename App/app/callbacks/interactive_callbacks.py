# =============================================================================
# MSI Analysis Application - Interactive Analysis Callbacks
# インタラクティブ解析 コールバック
# =============================================================================

import base64
import io
import logging
from io import BytesIO
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("msi.interactive")

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import dash_bootstrap_components as dbc
from dash import (Input, Output, State, callback, ctx, no_update, html, dcc,
                  dash_table, ALL, clientside_callback, ClientsideFunction)
from dash.exceptions import PreventUpdate

from app.config import (
    CLUSTER_PRESET_COLORS, DESI_COLORS_50, HIGHLIGHT_GRAY,
    DEFAULT_ADDUCT_POSITIVE,
)
from app.services.seurat_bridge import SeuratBridge
from app.services.notify import warn_user
from app.utils.color_utils import (
    cluster_sort_key as _cluster_sort_key,
    get_cluster_color_map as _get_cluster_color_map,
    adjust_color_lightness as _adjust_color_lightness,
    get_merged_cluster_color_map as _get_merged_cluster_color_map,
    cluster_display_name as _cluster_display_name,
    get_sample_color_map as _get_sample_color_map,
    get_cluster_colorscale as _get_cluster_colorscale,
)
from app.utils.display_helpers import (
    display_name as _display_name,
    compact_sci as _compact_sci,
    format_plain_number as _format_plain_number,
    generate_umap_arrow_image as _generate_umap_arrow_image,
    add_umap_arrows as _add_umap_arrows,
)
from app.utils.deg_utils import (
    is_meaningful_annotation as _is_meaningful_annotation,
    extract_mz_numeric as _extract_mz_numeric,
    standardize_deg_df as _standardize_deg_df,
    read_deg_rds as _read_deg_rds,
    load_deg_results as _load_deg_results_util,
    get_top_n_features_for_cluster as _get_top_n_features_for_cluster,
)
from app.utils.label_persistence import (
    get_label_positions_path as _get_label_positions_path_util,
    load_label_positions as _load_label_positions_util,
    get_interactive_settings_path as _get_interactive_settings_path_util,
    load_interactive_settings as _load_interactive_settings_util,
    save_interactive_settings as _save_interactive_settings_util,
    extract_annotation_positions_by_name as _extract_annotation_positions_by_name,
    merge_label_positions as _merge_label_positions,
    compute_annotation_offsets as _compute_annotation_offsets,
)
from app.utils.pptx_helpers import (
    fig_to_png_bytes as _fig_to_png_bytes,
    pptx_add_title_bar as _pptx_add_title_bar,
    pptx_add_image as _pptx_add_image,
    pptx_add_image_preserve_ratio as _pptx_add_image_preserve_ratio,
    square_tile_dims as _square_tile_dims,
    build_cluster_legend_fig as _build_cluster_legend_fig,
    build_sample_legend_fig as _build_sample_legend_fig,
    pptx_add_sections as _pptx_add_sections,
)

# Seuratブリッジのシングルトン
_bridge = SeuratBridge()

# ---------------------------------------------------------------------------
# プロジェクト別データキャッシュ
# ---------------------------------------------------------------------------
# 旧: _interactive_data = {...} はプロセス内 1 個のグローバル dict だったため、
# 異プロジェクトを同時閲覧した際にラベル位置の保存先などが上書きされていた。
# 新: project_key (= RDS path) ごとに state を分離し、proxy 経由で透過アクセスする。
# ---------------------------------------------------------------------------

import contextvars
import os
import threading
import time
from collections import OrderedDict

# LRU 設定 (環境変数で調整可、デフォルトは 8 件 / 30 分)
_MAX_PROJECT_STATES = int(os.environ.get("MAX_PROJECT_STATES", 8))
_PROJECT_STATE_TTL_SEC = int(os.environ.get("PROJECT_STATE_TTL_SEC", 30 * 60))

# OrderedDict で LRU: 最近アクセスされたエントリを末尾に維持し、
# サイズ超過時は先頭 (最古) を pop する。各エントリに last_access (epoch) を持たせ、
# TTL 超過分も明示的にクリーンアップ可能。
_project_states: "OrderedDict[str, dict]" = OrderedDict()
_state_access_time: dict[str, float] = {}
_state_lock = threading.RLock()
_DEFAULT_KEY = "__default__"
# 各リクエストスレッド / background_callback subprocess で独立した値を保持。
# 旧実装は _project_states["__active_key__"] という共有エントリを使っていたため、
# User A と User B が異なるプロジェクトを開くと衝突して片方のデータがもう片方に
# 漏れていた。ContextVar で per-request isolation を実現する。
_active_key_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "active_project_key", default=None
)


def _state_template() -> dict:
    """新規 state の初期値"""
    return {
        "plot_data": None,
        "cluster_stats": None,
        "features_list": None,
        "meta": None,
        "rds_path": None,
        "cache_dir": None,
        "deg_cache_key": None,
        "deg_cache_data": None,
        "method": None,
    }


def _evict_stale_states_unsafe() -> int:
    """ロック取得済みの状態で stale state を eviction する内部関数。

    1. TTL 超過エントリを優先的に削除 (_DEFAULT_KEY は除外)
    2. サイズ上限超過時は LRU (古い順) で削除
    Returns: 削除エントリ数
    """
    removed = 0
    now = time.time()
    # 1. TTL eviction
    expired = [
        k for k, ts in _state_access_time.items()
        if k != _DEFAULT_KEY and (now - ts) > _PROJECT_STATE_TTL_SEC
    ]
    for k in expired:
        _project_states.pop(k, None)
        _state_access_time.pop(k, None)
        removed += 1
    # 2. LRU eviction (上限超過分)
    while len(_project_states) > _MAX_PROJECT_STATES:
        k, _ = _project_states.popitem(last=False)  # 最古
        _state_access_time.pop(k, None)
        removed += 1
    return removed


def evict_stale_project_states() -> int:
    """外部 (heartbeat / 監視) から呼べる stale state クリーンアップ関数。

    Returns: 削除エントリ数
    """
    with _state_lock:
        return _evict_stale_states_unsafe()


def get_project_states_size() -> int:
    """現在保持中の state エントリ数 (監視用)。"""
    with _state_lock:
        return len(_project_states)


def _get_state(project_key: str | None = None) -> dict:
    """プロジェクト別 state を取得（なければ生成）。

    LRU: アクセス時に末尾へ移動。サイズ超過 or TTL 超過は自動 evict。

    Args:
        project_key: RDS path 等のキー。None ならアクティブ ContextVar or default。
    """
    key = project_key or _active_key_var.get() or _DEFAULT_KEY
    with _state_lock:
        if key in _project_states:
            # LRU update: 末尾へ移動
            _project_states.move_to_end(key)
        else:
            _project_states[key] = _state_template()
            # 新規追加時に上限チェック (古いエントリを evict)
            _evict_stale_states_unsafe()
        _state_access_time[key] = time.time()
        return _project_states[key]


def _set_active_key(project_key: str | None) -> None:
    """現在のリクエストスレッド / subprocess に対してのみアクティブキーを設定。
    他のユーザーのリクエストには影響しない（ContextVar 経由でスレッド毎に独立）。"""
    _active_key_var.set(project_key)


def _drop_state(project_key: str | None = None) -> None:
    """指定プロジェクトの state を破棄。None ならアクティブ state を破棄。"""
    key = project_key or _active_key_var.get()
    if not key:
        return
    with _state_lock:
        _project_states.pop(key, None)
    if _active_key_var.get() == key:
        _active_key_var.set(None)


class _InteractiveDataProxy:
    """既存の dict-like API を保持しつつ、内部でアクティブ state にルーティング。

    `_interactive_data["plot_data"]` / `_interactive_data.get(...)` /
    `_interactive_data["plot_data"] = ...` / `key in _interactive_data` 等の
    既存呼び出しがそのまま動作する。
    """
    def __getitem__(self, key):
        return _get_state()[key]

    def __setitem__(self, key, value):
        _get_state()[key] = value

    def get(self, key, default=None):
        return _get_state().get(key, default)

    def __contains__(self, key):
        return key in _get_state()

    def update(self, *args, **kwargs):
        _get_state().update(*args, **kwargs)

    def pop(self, key, *args):
        return _get_state().pop(key, *args)

    def setdefault(self, key, default=None):
        return _get_state().setdefault(key, default)

    def keys(self):
        return _get_state().keys()

    def values(self):
        return _get_state().values()

    def items(self):
        return _get_state().items()

    def __iter__(self):
        return iter(_get_state())

    def __len__(self):
        return len(_get_state())

    def __repr__(self):
        return f"<InteractiveDataProxy active={_project_states.get('__active_key__')!r}>"


# モジュール公開シンボル（既存 import 互換）
_interactive_data = _InteractiveDataProxy()


# キャリブレーション・アノテーション関連は interactive_calibration.py に分離済み
# 循環import回避のため、使用箇所で遅延importする


# ---------------------------------------------------------------------------
# Thin wrappers: label_persistence utils require rds_path parameter,
# but callers in this file expect no-arg versions using _interactive_data.
# ---------------------------------------------------------------------------

def _get_label_positions_path():
    """label_positions.json のパスを返す（RDSと同ディレクトリ、手法別ファイル対応）"""
    return _get_label_positions_path_util(
        _interactive_data.get("rds_path"),
        _interactive_data.get("method"))


def _load_label_positions():
    """label_positions.json を読み込んで dict を返す。ファイルなし or エラー時は空dict（手法別ファイル対応）"""
    return _load_label_positions_util(
        _interactive_data.get("rds_path"),
        _interactive_data.get("method"))


def _get_interactive_settings_path():
    """interactive_settings.json のパスを返す（RDSと同ディレクトリ）"""
    return _get_interactive_settings_path_util(_interactive_data.get("rds_path"))


def _load_interactive_settings():
    """interactive_settings.json を読み込み。ファイルなし/エラー時は空dict"""
    return _load_interactive_settings_util(_interactive_data.get("rds_path"))


def _save_interactive_settings(key, value):
    """interactive_settings.json の指定キーを更新して書き込む"""
    _save_interactive_settings_util(key, value, _interactive_data.get("rds_path"))


# ---------------------------------------------------------------------------
# 統合手法検出ヘルパー
# ---------------------------------------------------------------------------

def _detect_integration_methods(folder_path: str) -> dict:
    """結果フォルダ内のRDSファイルを検出し、統合手法→パスのマッピングを返す。

    Returns:
        {"Harmony": "path/to/seu_harmony.rds", "RPCA": "path/to/seu_rpca.rds", ...}
    """
    rds_map = {}
    base = Path(folder_path)
    if not base.is_dir():
        return rds_map

    # RDS_Files/ フォルダ内を検索
    rds_dir = base / "RDS_Files"
    search_dirs = [rds_dir, base] if rds_dir.is_dir() else [base]

    # data.frame 型 RDS を除外するプレフィックス
    _EXCLUDE_PREFIXES = ("umap_", "deg_", "plotdata_", "feature_")

    # 第1段階: TIMS ver13 の Step2/Step3 ファイルを優先マッチ
    for search_dir in search_dirs:
        for rds_file in search_dir.glob("*.rds"):
            name_lower = rds_file.name.lower()
            if "step2" in name_lower and "harmony" in name_lower:
                rds_map["Harmony"] = str(rds_file)
            elif "step3" in name_lower and "rpca" in name_lower:
                rds_map["RPCA"] = str(rds_file)
            elif "single" in name_lower and "PCA" not in rds_map:
                rds_map["PCA"] = str(rds_file)

    # 第2段階: 第1段階で見つからなかったキーのみ、従来マッチ（data.frame除外）
    for search_dir in search_dirs:
        for rds_file in search_dir.glob("*.rds"):
            name_lower = rds_file.name.lower()
            if any(name_lower.startswith(p) for p in _EXCLUDE_PREFIXES):
                continue
            if "harmony" in name_lower and "Harmony" not in rds_map:
                rds_map["Harmony"] = str(rds_file)
            elif "rpca" in name_lower and "RPCA" not in rds_map:
                rds_map["RPCA"] = str(rds_file)

    # rglob でサブフォルダも検索（上記で見つからない場合のフォールバック）
    if not rds_map:
        # 第1段階: Step2/Step3 優先
        for rds_file in base.rglob("*.rds"):
            name_lower = rds_file.name.lower()
            if "step2" in name_lower and "harmony" in name_lower:
                rds_map["Harmony"] = str(rds_file)
            elif "step3" in name_lower and "rpca" in name_lower:
                rds_map["RPCA"] = str(rds_file)
            elif "single" in name_lower and "PCA" not in rds_map:
                rds_map["PCA"] = str(rds_file)

        # 第2段階: 従来マッチ（data.frame除外）
        if "Harmony" not in rds_map or "RPCA" not in rds_map:
            for rds_file in base.rglob("*.rds"):
                name_lower = rds_file.name.lower()
                if any(name_lower.startswith(p) for p in _EXCLUDE_PREFIXES):
                    continue
                if "harmony" in name_lower and "Harmony" not in rds_map:
                    rds_map["Harmony"] = str(rds_file)
                elif "rpca" in name_lower and "RPCA" not in rds_map:
                    rds_map["RPCA"] = str(rds_file)

    # --- マージ済みRDS優先検出（無効化中）---
    # パフォーマンス問題のため無効化。有効化するとマージRDS（~200MB）が
    # Step2の代わりに読み込まれ、Cluster_merged / umap_merged が利用可能になる。
    # 有効化時はB〜H（トグルUI・コールバック・R抽出）が自動的に発動する。
    # for search_dir in search_dirs:
    #     for rds_file in search_dir.glob("*_merged_seurat.rds"):
    #         merged_path = str(rds_file)
    #         if "Harmony" in rds_map:
    #             rds_map["Harmony"] = merged_path
    #         elif "RPCA" in rds_map:
    #             rds_map["RPCA"] = merged_path
    #         break  # 最初の1つのみ使用

    return rds_map


def _build_msi_samples_ui(folder_path: str):
    """MSIフォルダ内のサンプル一覧UIを生成"""
    if not folder_path or not Path(folder_path).is_dir():
        return html.Div("MSIフォルダが見つかりません", className="text-muted")

    from app.services.data_manager import list_msi_files, list_tims_files
    samples = list_msi_files(folder_path)
    # .txtが見つからない場合はTIMS形式(.parquet等)を試行
    if not samples:
        samples = list_tims_files(folder_path)
    if not samples:
        return html.Div("MSIファイルが見つかりません", className="text-warning")

    import dash_bootstrap_components as dbc
    return dbc.Checklist(
        id="interactive_msi_sample_checks",
        options=[{"label": s, "value": s} for s in samples],
        value=samples,
    )


# ---------------------------------------------------------------------------
# 結果フォルダスキャン → 統合手法検出
# ---------------------------------------------------------------------------

@callback(
    [Output("interactive_integration_method", "options"),
     Output("interactive_integration_method", "value"),
     Output("interactive_rds_map", "data")],
    Input("scan_result_folder", "n_clicks"),
    State("interactive_result_folder", "value"),
    prevent_initial_call=True,
)
def scan_rds_files(n_clicks, folder_path):
    if not folder_path or not Path(folder_path).is_dir():
        return [], None, None

    rds_map = _detect_integration_methods(folder_path)
    if not rds_map:
        return [], None, None

    options = [{"label": k, "value": k} for k in rds_map.keys()]
    # Harmony を優先デフォルト、なければ最初の手法
    default = "Harmony" if "Harmony" in rds_map else list(rds_map.keys())[0]

    return options, default, rds_map


# ---------------------------------------------------------------------------
# 結果フォルダ変更時 → 自動スキャン
# ---------------------------------------------------------------------------

@callback(
    [Output("interactive_integration_method", "options", allow_duplicate=True),
     Output("interactive_integration_method", "value", allow_duplicate=True),
     Output("interactive_rds_map", "data", allow_duplicate=True)],
    Input("interactive_result_folder", "value"),
    prevent_initial_call=True,
)
def auto_scan_rds_files(folder_path):
    """結果フォルダのパスが設定された時、自動で統合手法を検出"""
    if not folder_path or not Path(folder_path).is_dir():
        return no_update, no_update, no_update

    rds_map = _detect_integration_methods(folder_path)
    if not rds_map:
        return no_update, no_update, no_update

    options = [{"label": k, "value": k} for k in rds_map.keys()]
    default = "Harmony" if "Harmony" in rds_map else list(rds_map.keys())[0]

    return options, default, rds_map


# ---------------------------------------------------------------------------
# MSIフォルダスキャン（ボタンクリック）
# ---------------------------------------------------------------------------

@callback(
    Output("interactive_msi_samples", "children"),
    Input("scan_msi_folder", "n_clicks"),
    State("interactive_msi_folder", "value"),
    prevent_initial_call=True,
)
def scan_msi_files(n_clicks, folder_path):
    return _build_msi_samples_ui(folder_path)


# ---------------------------------------------------------------------------
# MSIフォルダ変更時 → 自動スキャン
# ---------------------------------------------------------------------------

@callback(
    Output("interactive_msi_samples", "children", allow_duplicate=True),
    Input("interactive_msi_folder", "value"),
    prevent_initial_call=True,
)
def auto_scan_msi_files(folder_path):
    """MSIフォルダのパスが設定された時、自動でサンプル一覧を表示"""
    if not folder_path or not Path(folder_path).is_dir():
        return no_update
    return _build_msi_samples_ui(folder_path)


# ---------------------------------------------------------------------------
# 解析手法セクション 展開/折りたたみ
# ---------------------------------------------------------------------------

@callback(
    [Output("integration_method_collapse", "is_open"),
     Output("toggle_integration_method", "children")],
    Input("toggle_integration_method", "n_clicks"),
    State("integration_method_collapse", "is_open"),
    prevent_initial_call=True,
)
def toggle_integration_method(n, is_open):
    """解析手法セクションの展開/折りたたみを切り替え"""
    new_state = not is_open
    label = "解析手法 \u25bc" if new_state else "解析手法 \u25b6"
    return new_state, label


# ---------------------------------------------------------------------------
# データ読み込み（Seuratブリッジ経由）
# ---------------------------------------------------------------------------

@callback(
    [Output("interactive_data_info", "children"),
     Output("interactive_viz_container", "style"),
     Output("umap_highlight_cluster", "options"),
     Output("interactive_sample", "options"),
     Output("feature_select", "options"),
     Output("seurat_rds_path_store", "data"),
     Output("seurat_cache_dir_store", "data"),
     Output("deg_data_store", "data", allow_duplicate=True),
     Output("deg_results_section", "style"),
     Output("spatial_exclude_cluster", "options"),
     Output("spatial_highlight_cluster", "options"),
     Output("umap_exclude_cluster", "options"),
     Output("feature_sample_select", "options"),
     Output("deg_no_data_message", "style"),
     Output("feature_cluster_filter", "options"),
     Output("sample_name_map_store", "data", allow_duplicate=True),
     Output("spatial_rotation_store", "data", allow_duplicate=True),
     Output("custom_color_map_store", "data", allow_duplicate=True),
     Output("feature_history_store", "data", allow_duplicate=True),
     # キャリブレーション設定復元 (11個)
     Output("int_cal_table_data", "data", allow_duplicate=True),
     Output("int_cal_enable", "value"),
     Output("int_cal_ion_mode", "value"),
     Output("int_cal_matrix", "value"),
     Output("int_cal_adduct_filter", "value", allow_duplicate=True),
     Output("int_cal_annotation_path", "value"),
     Output("int_cal_search_window", "value"),
     Output("int_cal_min_peaks", "value"),
     Output("int_cal_regression_mode", "value"),
     Output("int_cal_ms_instrument", "data", allow_duplicate=True),
     Output("int_cal_restore_pending", "data"),
     Output("sap_btn_wrapper", "style"),
     Output("accumulated_label_positions", "data", allow_duplicate=True)],
    Input("load_interactive_data", "n_clicks"),
    [State("interactive_integration_method", "value"),
     State("interactive_rds_map", "data"),
     State("interactive_result_folder", "value"),
     State("calibration_enable", "value"),
     State("calibration_matrix", "value"),
     State("calibration_table_data", "data"),
     State("calibration_search_window", "value"),
     State("calibration_min_peaks", "value"),
     State("calibration_regression_mode", "value"),
     State("ion_mode", "value"),
     State("annotation_path", "value"),
     State("tolerance_mz", "value"),
     State("adduct_filter", "value"),
     State("interactive_project_select", "value"),
     State("interactive_sub_project_select", "value"),
     State("default_annotation_csv", "value")],
    background=True,
    running=[
        (Output("load_interactive_data", "disabled"), True, False),
        (Output("load_progress_container", "style"),
         {"display": "block", "marginTop": "10px"},
         {"display": "none", "marginTop": "10px"}),
        (Output("btn_cancel_load", "style"),
         {"display": "inline-block"}, {"display": "none"}),
    ],
    progress=[
        Output("load_progress_bar", "value"),
        Output("load_progress_bar", "max"),
        Output("load_progress_label", "children"),
    ],
    cancel=[Input("btn_cancel_load", "n_clicks")],
    prevent_initial_call=True,
)
def load_interactive_data(set_progress, n_clicks, integration_method, rds_map, result_folder,
                          cal_enable, cal_matrix, cal_table_data,
                          cal_search_window, cal_min_peaks,
                          cal_regression_mode,
                          ion_mode, mrm_path, tolerance_mz,
                          adduct_filter, project_id, sub_project_id,
                          annotation_csv):
    from app.callbacks.interactive_calibration import (
        _build_feature_annotation_map, _calibrate_mz, _calibrate_mz_from_pairs,
        _reannotate_with_calibration,
    )
    set_progress((5, 100, "入力チェック中..."))
    _n_out = 32
    _no_cal = (no_update,) * 11  # キャリブレーション設定復元用
    _sap_hide = ({"display": "none"},)  # sap_btn_wrapper 非表示
    _no_label_clear = (no_update,)  # accumulated_label_positions 変更なし
    if not integration_method or not rds_map:
        return (
            "統合手法を選択してください（結果フォルダをスキャンしてください）",
            {"display": "none"}, [], [], [], None, None, None,
            {"display": "none"}, [], [], [], [],
            {"display": "none"}, [],
            no_update, no_update, no_update, no_update,
        ) + _no_cal + _sap_hide + _no_label_clear

    rds_path = rds_map.get(integration_method)
    if not rds_path or not Path(rds_path).exists():
        return (
            f"RDSファイルが見つかりません: {integration_method}",
            {"display": "none"}, [], [], [], None, None, None,
            {"display": "none"}, [], [], [], [],
            {"display": "none"}, [],
            no_update, no_update, no_update, no_update,
        ) + _no_cal + _sap_hide + _no_label_clear

    try:
        set_progress((15, 100, "RDS 抽出中（重い解析では数十秒かかります）..."))
        result = _bridge.extract_data(rds_path)
        set_progress((50, 100, "プロットデータ準備中..."))

        # rds_path をプロジェクトキーとして state を切替（複数プロジェクト同時閲覧対応）
        state = _get_state(rds_path)
        state["plot_data"] = result["plot_data"]
        state["cluster_stats"] = result["cluster_stats"]
        state["features_list"] = result["features_list"]
        state["meta"] = result["meta"]
        state["rds_path"] = rds_path
        state["cache_dir"] = result.get("cache_dir")
        state["method"] = integration_method
        # 後続コールバックがアクティブ state を参照できるようにキーを設定
        _set_active_key(rds_path)

        meta = result["meta"]
        info_text = (
            f"読み込み完了 [{integration_method}]: "
            f"{meta.get('n_cells', '?')} cells, "
            f"{meta.get('n_clusters', '?')} clusters, "
            f"samples: {', '.join(meta.get('samples', []))}"
        )

        # クラスタ選択肢
        clusters = sorted(_interactive_data["plot_data"]["Cluster"].unique(), key=_cluster_sort_key)
        cluster_options = [
            {"label": str(c), "value": str(c)} for c in clusters
        ]

        # サンプル選択肢
        samples = sorted(_interactive_data["plot_data"]["Sample"].unique())
        sample_options = [{"label": s, "value": s} for s in samples]

        # Feature選択肢: 初期は上位 500 件のみ（18k 件 eager 送信を回避）
        # 検索すると filter_features callback がサーバサイドで全 features から再フィルタする
        features = result["features_list"]
        feature_options = [{"label": f, "value": f} for f in features[:500]]

        # DEG 結果を探す（選択した統合手法のフォルダを優先）
        set_progress((65, 100, "DEG マーカー読込中..."))
        deg_data = None
        if result_folder:
            result_base = Path(result_folder)
            deg_data = _load_deg_results(result_base, integration_method)
        else:
            rds_dir = Path(rds_path).parent
            result_base = rds_dir.parent if rds_dir.name == "RDS_Files" else rds_dir
            deg_data = _load_deg_results(result_base, integration_method)

        # --- m/z キャリブレーション（有効時のみ） ---
        if cal_enable and deg_data and mrm_path and cal_table_data:
            set_progress((80, 100, "m/z キャリブレーション処理中..."))
            try:
                # テーブルから use="Yes" のペアを抽出
                matched_pairs = []
                ref_only_mz = []
                for row in cal_table_data:
                    if row.get("use") != "Yes":
                        continue
                    ref = row.get("ref_mz")
                    obs = row.get("obs_mz")
                    if ref and obs and str(ref).strip() and str(obs).strip():
                        ref_f = float(ref)
                        obs_f = float(obs)
                        ppm = (obs_f - ref_f) / ref_f * 1e6
                        matched_pairs.append({
                            "ref_mz": ref_f, "obs_mz": obs_f,
                            "ppm_drift": ppm,
                        })
                    elif ref and str(ref).strip():
                        ref_only_mz.append(float(ref))

                mp = int(cal_min_peaks or 2)
                reg_mode = cal_regression_mode or "linear"
                cal_result = None

                if len(matched_pairs) >= mp:
                    # 明示的ペアが十分 → 直接回帰
                    cal_result = _calibrate_mz_from_pairs(
                        result["features_list"], matched_pairs,
                        regression_mode=reg_mode,
                    )
                elif ref_only_mz:
                    # obs未入力行あり → 自動検出にフォールバック
                    # expression_matrix が無ければ on-demand 生成
                    try:
                        expr_path = _bridge.ensure_expression_matrix(rds_path)
                    except Exception:
                        expr_path = None
                    if expr_path and expr_path.exists():
                        expr_df = pd.read_parquet(expr_path)
                        sw = float(cal_search_window or 0.5)
                        cal_result = _calibrate_mz(
                            result["features_list"], expr_df, ref_only_mz,
                            search_window=sw, min_peaks=mp,
                            regression_mode=reg_mode,
                        )

                if cal_result and cal_result.get("calibrated"):
                    tol = float(tolerance_mz or 0.1)
                    deg_data = _reannotate_with_calibration(
                        deg_data, cal_result["corrected_mz_map"],
                        mrm_path, tolerance=tol,
                        annotation_csv_path=annotation_csv,
                        ion_mode=ion_mode,
                        adduct_patterns=adduct_filter,
                    )
                    _interactive_data["_calibration_result"] = cal_result
            except Exception:
                pass  # キャリブレーション失敗時はそのまま続行

        # DEGセクションは常に表示、データ有無でメッセージ切替
        deg_section_style = {}  # 常に表示
        deg_no_data_style = {"display": "none"} if deg_data else {}
        # クラスタフィルタ選択肢（DEGデータから生成）
        cluster_filter_opts = []
        if deg_data:
            deg_clusters = sorted(set(str(r.get("cluster", "")) for r in deg_data), key=_cluster_sort_key)
            cluster_filter_opts = [
                {"label": str(c), "value": c} for c in deg_clusters
            ]

        # 保存済み設定を読み込み（初回ロード時にStoreへ復元）
        saved = _load_interactive_settings()
        restored_name_map = saved.get("sample_name_map", {})
        restored_rotation = saved.get("spatial_rotation", {})
        restored_colors = saved.get("custom_color_map", {})
        restored_bookmarks = saved.get("feature_bookmarks", [])
        if restored_name_map:
            _interactive_data["_name_map"] = restored_name_map

        # --- キャリブレーション設定の復元 ---
        int_cal = saved.get("int_calibration", {})
        if int_cal:
            r_table = int_cal.get("table_data", [])
            r_enable = int_cal.get("enable", False)
            r_ion_mode = int_cal.get("ion_mode", ion_mode or "Positive")
            r_matrix = int_cal.get("matrix", cal_matrix or "DHB")
            r_adduct = int_cal.get("adduct_filter",
                                   adduct_filter or DEFAULT_ADDUCT_POSITIVE)
            r_mrm = int_cal.get("mrm_path", mrm_path or "")
            r_sw = int_cal.get("search_window", cal_search_window or 0.5)
            r_mp = int_cal.get("min_peaks", cal_min_peaks or 2)
            r_reg = int_cal.get("regression_mode", cal_regression_mode or "poly3")
        else:
            # フォールバック: 解析設定タブの値
            r_table = cal_table_data or []
            r_enable = cal_enable or False
            r_ion_mode = ion_mode or "Positive"
            r_matrix = cal_matrix or "DHB"
            r_adduct = adduct_filter or DEFAULT_ADDUCT_POSITIVE
            r_mrm = mrm_path or ""
            r_sw = cal_search_window or 0.5
            r_mp = cal_min_peaks or 2
            r_reg = cal_regression_mode or "poly3"

        # --- アノテーションマップの構築（Feature検索用） ---
        set_progress((90, 100, "アノテーション構築 / 設定復元中..."))
        try:
            _interactive_data["annotation_map"] = _build_feature_annotation_map(
                result["features_list"],
                annotation_csv_path=annotation_csv or "",
                ion_mode=ion_mode or "Positive",
                adduct_patterns=adduct_filter,
                tolerance=float(tolerance_mz or 0.01),
                deg_data=deg_data,
            )
        except Exception:
            _interactive_data["annotation_map"] = {}

        # ms_instrument をサブプロジェクトから取得
        r_instrument = "TIMS"
        if sub_project_id and project_id:
            try:
                from app.services.project_manager import get_sub_project
                sub = get_sub_project(project_id, sub_project_id)
                if sub:
                    r_instrument = sub.get("ms_instrument", "TIMS")
            except Exception as e:
                warn_user(f"サブプロジェクト情報の取得に失敗: {e}")

        set_progress((100, 100, "完了"))
        return (
            info_text,
            {},  # 可視化コンテナ表示
            cluster_options,
            sample_options,
            feature_options,
            rds_path,
            str(result.get("cache_dir", "")),
            deg_data,
            deg_section_style,
            cluster_options,  # spatial_exclude_cluster用
            cluster_options,  # spatial_highlight_cluster用
            cluster_options,  # umap_exclude_cluster用
            sample_options,   # feature_sample_select用
            deg_no_data_style,
            cluster_filter_opts,
            restored_name_map,
            restored_rotation,
            restored_colors,
            restored_bookmarks,
            # キャリブレーション設定復元 (11個)
            r_table, r_enable, r_ion_mode, r_matrix, r_adduct, r_mrm,
            r_sw, r_mp, r_reg, r_instrument, True,
            {},  # sap_btn_wrapper 表示
            {},  # accumulated_label_positions クリア（手法切替時にリセット）
        )

    except Exception as e:
        return (
            f"読み込みエラー: {e}",
            {"display": "none"}, [], [], [], None, None, None,
            {"display": "none"}, [], [], [], [],
            {"display": "none"}, [],
            no_update, no_update, no_update, no_update,
        ) + _no_cal + _sap_hide + _no_label_clear


def _load_deg_results(
    result_base: Path, integration_method: str | None = None
) -> list[dict] | None:
    """解析結果フォルダ内の DEG CSV / RDS を読み込む（キャッシュ付き）。
    Delegates to deg_utils.load_deg_results with _interactive_data as cache."""
    return _load_deg_results_util(
        result_base, integration_method, cache=_interactive_data,
    )


# ---------------------------------------------------------------------------
# PPTX (Google Slides) エクスポート — interactive_pptx.py に分離済み
# コールバック登録のため import する
# ---------------------------------------------------------------------------
import app.callbacks.interactive_pptx  # noqa: F401  — registers PPTX callbacks
import app.callbacks.interactive_calibration  # noqa: F401  — registers calibration callbacks

# ---------------------------------------------------------------------------
# UMAP/Spatial/DEG コールバック — 分離済み
# コールバック登録のため import する
# ---------------------------------------------------------------------------
import app.callbacks.interactive_umap  # noqa: F401  — registers UMAP callbacks
import app.callbacks.interactive_spatial  # noqa: F401  — registers Spatial callbacks
import app.callbacks.interactive_deg  # noqa: F401  — registers DEG/Volcano/Heatmap/Feature callbacks

# ---------------------------------------------------------------------------
# クラスタ管理コールバック — interactive_cluster.py に分離済み
# コールバック登録のため import する
# ---------------------------------------------------------------------------
import app.callbacks.interactive_cluster  # noqa: F401  — registers cluster callbacks
import app.callbacks.interactive_project  # noqa: F401  — registers project callbacks
import app.callbacks.interactive_fullscreen  # noqa: F401  — registers fullscreen/label callbacks
