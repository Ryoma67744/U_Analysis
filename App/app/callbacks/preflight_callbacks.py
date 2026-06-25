# =============================================================================
# MSI Analysis Application - PreFlight 診断 Callbacks
#
# 完了済み解析の reduction RDS（Harmony/RPCA/PCA 等）に run_diagnostics.R を
# 実行し、推奨 dims / n.neighbors・許容域・推奨度・警告・設計交絡を画面表示する。
# 「推奨値を入力欄へ反映」で最有力の推奨値を UMAP ハイパラ入力欄に転記できる
# （提案のみ。自動適用・自動再解析はしない）。
#
# 本解析や RDS 軽量化と干渉しないように、独立したモジュールローカル辞書で
# プロセスを保持する（start_analysis_process 自体が同時 Rscript 実行を弾くため、
# 解析中は診断を起動できない＝想定どおり）。
# =============================================================================

import json
import logging
from pathlib import Path

import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dash_table, html, no_update

from app.config import RUN_DIAGNOSTICS_PATH
from app.services.analysis_runner import (
    check_process_completion,
    start_analysis_process,
)
from app.services.project_manager import get_sub_project

logger = logging.getLogger("msi.preflight_callbacks")

# 本解析の _process_state と干渉しないよう独立したプロセス状態を保持
_preflight_process_state: dict = {
    "process": None,
    "log_file": None,
    "status_file": None,
    "log_file_handle": None,
    "output_dir": None,
}


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _resolve_result_dir(selected_project, current_sub_project_id) -> str:
    """選択中サブプロジェクトの結果フォルダを解決（last_result_dir 優先）。"""
    if not selected_project or not current_sub_project_id:
        return ""
    project_id = (
        selected_project.get("id", "")
        if isinstance(selected_project, dict) else ""
    )
    if not project_id:
        return ""
    sub = get_sub_project(project_id, current_sub_project_id)
    if not sub:
        return ""
    return sub.get("last_result_dir") or sub.get("output_dir", "")


def _fmt_num(x, nd: int = 3):
    """数値を丸めて返す。変換不能なら None。"""
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


def _render_diagnostics_table(data: dict, rds_methods: dict):
    """diagnostics.json を DataTable に整形し、(node, recommended) を返す。

    recommended は「推奨値を入力欄へ反映」用に、各手法の推奨の最大値（max 集約;
    n.neighbors は全手法の許容上限内にクランプ）を {n_neighbors, dims, min_dist,
    metric, source} で返す。min_dist/metric は既定固定（0.3 / cosine）。
    実スキーマ: inputs[].reductions[<red>].preflight.{dims.recommended,
    n_neighbors.recommended, n_neighbors.allowed_range, confidence, warnings}
    と inputs[].design.status / reductions[<red>].space.batch_mixing.ilisi。
    """
    # rds パス → 手法名 の逆引き（表示用ラベル）
    path_to_method = {str(v): k for k, v in (rds_methods or {}).items()}

    rows = []
    recommended = None          # apply 用（max 集約; ループ後に算出）
    rec_dims_all = []           # 各手法の推奨 dims
    rec_nn_all = []             # 各手法の推奨 n.neighbors
    allowed_upper_all = []      # 各手法の許容 n.neighbors 上限
    rec_src = []                # 反映元ラベル "method/reduction"

    for entry in (data.get("inputs") or []):
        rds_path = entry.get("rds", "")
        method = path_to_method.get(
            str(rds_path),
            Path(rds_path).stem if rds_path else "?",
        )
        design_status = (entry.get("design") or {}).get("status", "unknown")
        err = entry.get("error")
        reductions = entry.get("reductions") or {}

        if err:
            rows.append({
                "method": method, "reduction": "-", "rec_dims": "-",
                "rec_nn": "-", "allowed_nn": "-", "confidence": "-",
                "design": design_status, "ilisi": "-",
                "warnings": f"読込エラー: {err}",
            })
            continue
        if not reductions:
            # reduction が空＝その補正がスキップ/未実行。単一サンプルや生物群（群比較）では
            # RPCA/Harmony は「補正不要」のためスキップされ、空の RDS（list(obj=NULL)）が
            # 残ることがある（R 側 ver6/ver5 で今後は空RDS自体を作らないよう修正済み）。
            # これは正常な状態なので、警告ではなく中立表示にする（過去実行の空RDS対策）。
            rows.append({
                "method": method, "reduction": "(スキップ)", "rec_dims": "-",
                "rec_nn": "-", "allowed_nn": "-", "confidence": "-",
                "design": design_status, "ilisi": "-",
                "warnings": (f"{method} は未実行（単一サンプル/生物群では補正不要の"
                             "ためスキップ。問題ありません）"),
            })
            continue

        for red_name, rd in reductions.items():
            pf = (rd or {}).get("preflight") or {}
            if pf.get("error"):
                rows.append({
                    "method": method, "reduction": red_name, "rec_dims": "-",
                    "rec_nn": "-", "allowed_nn": "-", "confidence": "-",
                    "design": design_status, "ilisi": "-",
                    "warnings": f"診断エラー: {pf.get('error')}",
                })
                continue

            dims = pf.get("dims") or {}
            nn = pf.get("n_neighbors") or {}
            rec_dims = dims.get("recommended")
            rec_nn = nn.get("recommended")
            allowed = nn.get("allowed_range")
            if (isinstance(allowed, (list, tuple)) and len(allowed) == 2
                    and allowed[0] is not None):
                allowed_str = f"{allowed[0]}–{allowed[1]}"
            else:
                allowed_str = "-"
            conf = pf.get("confidence", "-")
            metric = pf.get("metric", "cosine")
            warns = pf.get("warnings") or []
            if isinstance(warns, str):
                warns = [warns]

            # バッチ混合 iLISI（strata 内平均 or global 参考値）
            bm = ((rd or {}).get("space") or {}).get("batch_mixing") or {}
            ilisi = _fmt_num(bm.get("ilisi"), 3)

            rows.append({
                "method": method, "reduction": red_name,
                "rec_dims": rec_dims if rec_dims is not None else "-",
                "rec_nn": rec_nn if rec_nn is not None else "-",
                "allowed_nn": allowed_str,
                "confidence": conf,
                "design": design_status,
                "ilisi": ilisi if ilisi is not None else "-",
                "warnings": "; ".join(str(w) for w in warns) if warns else "なし",
            })

            # apply 用: 各手法の推奨を集約（後で手法間の max を採用）
            if rec_dims is not None:
                rec_dims_all.append(rec_dims)
            if rec_nn is not None:
                rec_nn_all.append(rec_nn)
            if (isinstance(allowed, (list, tuple)) and len(allowed) == 2
                    and allowed[1] is not None):
                allowed_upper_all.append(allowed[1])
            if rec_dims is not None or rec_nn is not None:
                rec_src.append(f"{method}/{red_name}")

    if not rows:
        return (
            dbc.Alert("診断結果が空でした。ログを確認してください。",
                      color="warning"),
            None,
        )

    # 手法間 max 集約: 推奨は「安定/連結に必要な最小値」なので、全手法が満たす
    # 最小の共通値＝各手法の推奨の最大値を採用（n.neighbors は全手法の許容上限内に
    # クランプ）。単一手法のみ推奨ありなら max=その値（従来同等）。
    if rec_dims_all or rec_nn_all:
        agg_dims = max(rec_dims_all) if rec_dims_all else None
        agg_nn = None
        if rec_nn_all:
            agg_nn = max(rec_nn_all)
            if allowed_upper_all:
                agg_nn = min(agg_nn, min(allowed_upper_all))
        recommended = {
            "n_neighbors": agg_nn,
            "dims": agg_dims,
            "min_dist": 0.3,
            "metric": "cosine",
            "source": ("max: " + ", ".join(rec_src)) if rec_src else "max",
        }

    columns = [
        {"name": "手法", "id": "method"},
        {"name": "reduction", "id": "reduction"},
        {"name": "推奨dims", "id": "rec_dims"},
        {"name": "推奨n.neighbors", "id": "rec_nn"},
        {"name": "許容n.neighbors", "id": "allowed_nn"},
        {"name": "推奨度", "id": "confidence"},
        {"name": "設計(交絡)", "id": "design"},
        {"name": "iLISI", "id": "ilisi"},
        {"name": "警告", "id": "warnings"},
    ]
    table = dash_table.DataTable(
        columns=columns,
        data=rows,
        style_table={"overflowX": "auto"},
        style_cell={
            "fontSize": "0.8rem", "padding": "4px 8px", "textAlign": "left",
            "whiteSpace": "normal", "height": "auto", "fontFamily": "sans-serif",
        },
        style_header={"fontWeight": "600", "backgroundColor": "#f1f3f5"},
        style_data_conditional=[
            {"if": {"filter_query": '{confidence} = "high"',
                    "column_id": "confidence"},
             "backgroundColor": "#d3f9d8", "color": "#2b8a3e"},
            {"if": {"filter_query": '{confidence} = "medium"',
                    "column_id": "confidence"},
             "backgroundColor": "#fff3bf", "color": "#e67700"},
            {"if": {"filter_query": '{design} = "not_identifiable"',
                    "column_id": "design"},
             "backgroundColor": "#ffe3e3", "color": "#c92a2a"},
        ],
    )

    header = html.Div(
        [
            html.B("PreFlight 診断結果"),
            html.Span(
                f"  生成: {data.get('generated_at', '?')}",
                style={"fontSize": "0.75rem", "color": "#868e96",
                       "marginLeft": "6px"},
            ),
        ],
        style={"marginBottom": "6px"},
    )
    footer = dbc.FormText(
        "推奨度 high は dims・n.neighbors とも安定。iLISI は高いほどバッチ混合が"
        "良好（同一スポット数なら 1 付近＝完全混合）。設計(交絡)が "
        "not_identifiable の場合、技術差と生物差を分離できません。"
        "③反映は各手法の推奨の最大値を採用（全手法が安定・連結する最小の共通値、"
        "許容範囲内にクランプ）。min.dist・metric は自動推奨の対象外で既定値"
        "（0.3 / cosine）を使用します。"
        + (f"　反映値の元: {recommended['source']}" if recommended else "")
    )
    return html.Div([header, table, footer]), recommended


def _load_saved_diagnostics(result_dir: str):
    """保存済み diagnostics.json を読み、(container, store) を返す（再計算なし）。

    返り値:
      - None                : 保存ファイルなし（呼び出し側で無反応 or 明示メッセージ）
      - (error_alert, None) : 読込/パース失敗（描画ノードのみ、store は更新しない）
      - (container, store)  : 成功（描画ノード＋復元ストア）
    """
    if not result_dir:
        return None
    diag = Path(result_dir) / "preflight" / "diagnostics.json"
    if not diag.exists():
        return None
    try:
        data = json.loads(diag.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("保存済み diagnostics.json 読込失敗: %s", e)
        return (
            dbc.Alert(f"保存済み診断結果の読み込みに失敗しました: {e}", color="danger"),
            None,
        )
    # 循環 import 回避のため遅延 import（既存パターン踏襲）
    from app.callbacks.interactive_callbacks import _detect_integration_methods
    rds_methods = {k: str(v) for k, v in _detect_integration_methods(result_dir).items()}
    node, recommended = _render_diagnostics_table(data, rds_methods)
    banner = dbc.Alert(
        "📂 保存済みの診断結果を表示中（再計算するには「② PreFlight 診断を実行」）。",
        color="light", className="py-1 px-2 mb-2 small",
    )
    container = html.Div([banner, node])
    store = {
        "out_dir": str(diag.parent),
        "status_file": None,
        "rds_methods": rds_methods,
        "status": "loaded",
        "recommended": recommended,
    }
    return (container, store)


# ---------------------------------------------------------------------------
# 診断 実行（ボタン）— 共有 Output は allow_duplicate（canonical は poll 側）
# ---------------------------------------------------------------------------

@callback(
    Output("preflight_results_container", "children", allow_duplicate=True),
    Output("preflight_store", "data", allow_duplicate=True),
    Output("preflight_poll", "disabled", allow_duplicate=True),
    Input("btn_preflight_run", "n_clicks"),
    State("selected_project", "data"),
    State("current_sub_project_id", "data"),
    prevent_initial_call=True,
)
def run_preflight(n_clicks, selected_project, current_sub_project_id):
    if not n_clicks:
        return no_update, no_update, no_update

    # 二重起動防止（本診断プロセス）
    proc = _preflight_process_state.get("process")
    if proc is not None and proc.poll() is None:
        return (
            dbc.Alert("PreFlight 診断は既に実行中です。", color="warning"),
            no_update, no_update,
        )

    # 結果フォルダ解決
    result_dir = _resolve_result_dir(selected_project, current_sub_project_id)
    if not result_dir:
        return (
            dbc.Alert(
                "プロジェクト／サブプロジェクトを選択し、完了済みの解析結果が"
                "あることを確認してください。",
                color="warning",
            ),
            no_update, True,
        )
    result_path = Path(result_dir)
    if not result_path.is_dir():
        return (
            dbc.Alert(f"結果フォルダが見つかりません: {result_dir}",
                      color="danger"),
            no_update, True,
        )

    # reduction RDS 検出（既存ロジックを流用。callback 間結合を避け遅延 import）
    from app.callbacks.interactive_callbacks import _detect_integration_methods
    rds_map = _detect_integration_methods(result_dir)
    if not rds_map:
        return (
            dbc.Alert(
                "診断対象の reduction RDS（Harmony/RPCA/PCA 等）が見つかりません。"
                "統合解析が完了した結果フォルダを選択してください。",
                color="warning",
            ),
            no_update, True,
        )

    # 出力先 <result_dir>/preflight、前回 diagnostics.json は退避
    out_dir = result_path / "preflight"
    out_dir.mkdir(parents=True, exist_ok=True)
    diag_json = out_dir / "diagnostics.json"
    if diag_json.exists():
        try:
            diag_json.unlink()
        except OSError as e:
            logger.warning("旧 diagnostics.json 削除失敗（非重大）: %s", e)

    # CLI 引数: --rds を RDS の数だけ繰り返す
    extra_args = []
    for rds_path in rds_map.values():
        extra_args += ["--rds", str(rds_path)]
    extra_args += [
        "--out", str(out_dir),
        "--batch-var", "sample",
        "--max-spots", "20000",
        "--seed", "42",
    ]

    result = start_analysis_process(
        str(RUN_DIAGNOSTICS_PATH), str(out_dir), extra_args=extra_args,
    )
    if not result.get("success"):
        return (
            dbc.Alert(
                f"診断プロセスを起動できませんでした: {result.get('message')}",
                color="danger",
            ),
            no_update, True,
        )

    _preflight_process_state["process"] = result["process"]
    _preflight_process_state["log_file"] = result["log_file"]
    _preflight_process_state["status_file"] = result["status_file"]
    _preflight_process_state["log_file_handle"] = result.get("log_file_handle")
    _preflight_process_state["output_dir"] = str(out_dir)

    store = {
        "out_dir": str(out_dir),
        "status_file": result["status_file"],
        "rds_methods": {k: str(v) for k, v in rds_map.items()},
        "status": "running",
        "recommended": None,
    }
    return (
        dbc.Alert(
            [
                dbc.Spinner(size="sm"),
                f"  PreFlight 診断を実行中です…（{len(rds_map)} 個の reduction）",
            ],
            color="info",
        ),
        store,
        False,   # poll 有効化
    )


# ---------------------------------------------------------------------------
# 結果ポーリング（canonical writer = allow_duplicate なし）
# ---------------------------------------------------------------------------

@callback(
    Output("preflight_results_container", "children"),
    Output("preflight_store", "data"),
    Output("preflight_poll", "disabled"),
    Input("preflight_poll", "n_intervals"),
    State("preflight_store", "data"),
    prevent_initial_call=True,
)
def poll_preflight(n_intervals, store):
    if not store or not store.get("out_dir"):
        return no_update, no_update, no_update

    proc = _preflight_process_state.get("process")
    log_fh = _preflight_process_state.get("log_file_handle")
    status_file = store.get("status_file")

    status = None
    if proc is not None:
        try:
            status = check_process_completion(proc, status_file, log_fh)
        except Exception as e:
            logger.exception("check_process_completion failed: %s", e)

    if status is None:
        # まだ実行中 → 次の interval を待つ
        return no_update, no_update, no_update

    # プロセス終了 → 状態クリア（interval も停止する）
    _preflight_process_state["process"] = None
    _preflight_process_state["log_file_handle"] = None

    diag_json = Path(store["out_dir"]) / "diagnostics.json"
    if not diag_json.exists():
        msg = ("診断がエラーで終了しました。ログを確認してください。"
               if status == "error"
               else "診断は終了しましたが結果ファイルが見つかりません。")
        return (
            dbc.Alert(msg, color="danger"),
            {**store, "status": "error"},
            True,
        )

    try:
        data = json.loads(diag_json.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("diagnostics.json 読込失敗: %s", e)
        return (
            dbc.Alert(f"結果ファイルの読み込みに失敗しました: {e}",
                      color="danger"),
            {**store, "status": "error"},
            True,
        )

    table, recommended = _render_diagnostics_table(
        data, store.get("rds_methods", {}),
    )
    return table, {**store, "status": "done", "recommended": recommended}, True


# ---------------------------------------------------------------------------
# 保存済み診断結果の再表示（再計算なし）— 自動＋ボタン
# ---------------------------------------------------------------------------

@callback(
    Output("preflight_results_container", "children", allow_duplicate=True),
    Output("preflight_store", "data", allow_duplicate=True),
    Output("preflight_poll", "disabled", allow_duplicate=True),
    Input("selected_project", "data"),
    Input("current_sub_project_id", "data"),
    prevent_initial_call=True,
)
def autoload_saved_diagnostics(selected_project, current_sub_project_id):
    """サブプロジェクト選択時、保存済み PreFlight 診断があれば自動で再表示（再計算なし）。"""
    # 実行中は表示を壊さない
    proc = _preflight_process_state.get("process")
    if proc is not None and proc.poll() is None:
        return no_update, no_update, no_update
    result_dir = _resolve_result_dir(selected_project, current_sub_project_id)
    if not result_dir:
        return no_update, no_update, no_update
    res = _load_saved_diagnostics(result_dir)
    if not res:
        return no_update, no_update, no_update          # 保存なし → 無反応
    container, store = res
    if store is None:
        return no_update, no_update, no_update          # パースエラー → 自動は silent
    return container, store, True


@callback(
    Output("preflight_results_container", "children", allow_duplicate=True),
    Output("preflight_store", "data", allow_duplicate=True),
    Output("preflight_poll", "disabled", allow_duplicate=True),
    Input("btn_preflight_load", "n_clicks"),
    State("selected_project", "data"),
    State("current_sub_project_id", "data"),
    prevent_initial_call=True,
)
def load_saved_diagnostics_button(n_clicks, selected_project, current_sub_project_id):
    """「📂 前回の診断を表示」: 保存済み diagnostics.json を再計算なしで再表示。"""
    if not n_clicks:
        return no_update, no_update, no_update
    proc = _preflight_process_state.get("process")
    if proc is not None and proc.poll() is None:
        return no_update, no_update, no_update
    result_dir = _resolve_result_dir(selected_project, current_sub_project_id)
    if not result_dir:
        return (
            dbc.Alert("プロジェクト／サブプロジェクトを選択してください。", color="warning"),
            no_update, no_update,
        )
    res = _load_saved_diagnostics(result_dir)
    if not res:
        return (
            dbc.Alert("保存された診断結果がありません（「② PreFlight 診断を実行」で作成してください）。",
                      color="info"),
            no_update, no_update,
        )
    container, store = res
    if store is None:
        return container, no_update, no_update          # パースエラー → エラー alert のみ
    return container, store, True


# ---------------------------------------------------------------------------
# 推奨値を入力欄へ反映（提案のみ）
# ---------------------------------------------------------------------------

@callback(
    Output("umap_n_neighbors_input", "value"),
    Output("umap_dims_input", "value"),
    Output("umap_min_dist_input", "value"),
    Output("umap_metric_input", "value"),
    Input("btn_preflight_apply", "n_clicks"),
    State("preflight_store", "data"),
    prevent_initial_call=True,
)
def apply_preflight_recommendation(n_clicks, store):
    if not n_clicks or not store:
        return no_update, no_update, no_update, no_update
    rec = (store or {}).get("recommended")
    if not rec:
        return no_update, no_update, no_update, no_update
    nn = rec.get("n_neighbors")
    dims = rec.get("dims")
    min_dist = rec.get("min_dist")
    metric = rec.get("metric")
    return (
        nn if nn is not None else no_update,
        dims if dims is not None else no_update,
        min_dist if min_dist is not None else no_update,
        metric if metric else no_update,
    )
