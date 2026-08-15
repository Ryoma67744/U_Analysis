# =============================================================================
# MSI Analysis Application - RDS Maintenance Callbacks
#
# モーダル rds_maintenance_modal から slim_existing_rds.R を実行し、
# ログ / プログレスバー / サマリを更新する Dash コールバック群。
#
# プロセス実体は本解析と干渉しないように別モジュールローカル変数で保持する。
# =============================================================================

import logging
import re
from pathlib import Path

import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, ctx, html, no_update

from app.config import OTHER_DIR, R_HELPERS_DIR
from app.services.analysis_runner import (
    check_process_completion,
    format_log_lines_styled,
    get_analysis_log,
    start_analysis_process,
    stop_analysis_process,
)

logger = logging.getLogger("msi.rds_maintenance_callbacks")

_SLIM_SCRIPT = R_HELPERS_DIR / "slim_existing_rds.R"
_SLIM_LOG_DIR = OTHER_DIR / "logs" / "rds_maint"
_DEFAULT_INCLUDE = "Step1*.rds,Step2*.rds,Step3*.rds,*_seurat*.rds"

# 本解析の _process_state と干渉しないように独立した辞書を持つ
_slim_process_state: dict = {
    "process": None,
    "log_file": None,
    "status_file": None,
    "log_file_handle": None,
    "output_dir": None,
}


# ---------------------------------------------------------------------------
# モーダル開閉
# ---------------------------------------------------------------------------

@callback(
    Output("rds_maintenance_modal", "is_open"),
    Input("open_rds_maintenance_modal", "n_clicks"),
    Input("open_rds_maintenance_modal_landing", "n_clicks"),
    Input("rds_maint_close_btn", "n_clicks"),
    State("rds_maintenance_modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_rds_maintenance_modal(open_sidebar, open_landing, close_clicks, is_open):
    trig = ctx.triggered_id
    if trig in ("open_rds_maintenance_modal", "open_rds_maintenance_modal_landing"):
        return True
    if trig == "rds_maint_close_btn":
        return False
    return no_update


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------

@callback(
    Output("rds_maint_alert", "children"),
    Output("rds_maint_log", "children", allow_duplicate=True),
    Output("rds_maint_log", "style", allow_duplicate=True),
    Output("rds_maint_progress_bar", "style", allow_duplicate=True),
    Output("rds_maint_progress_bar", "value", allow_duplicate=True),
    Output("rds_maint_progress_bar", "label", allow_duplicate=True),
    Output("rds_maint_summary", "children", allow_duplicate=True),
    Output("rds_maint_interval", "disabled", allow_duplicate=True),
    Output("rds_maint_state", "data", allow_duplicate=True),
    Output("rds_maint_stop_btn", "disabled", allow_duplicate=True),
    Output("rds_maint_run_btn", "disabled", allow_duplicate=True),
    Input("rds_maint_run_btn", "n_clicks"),
    State("rds_maint_folder", "value"),
    State("rds_maint_dry_run", "value"),
    State("rds_maint_backup", "value"),
    State("rds_maint_include", "value"),
    State("rds_maint_keep_scale", "value"),
    State("rds_maint_keep_graphs", "value"),
    prevent_initial_call=True,
)
def run_rds_slim(
    n_clicks, folder, dry_run, backup, include_pattern,
    keep_scale, keep_graphs,
):
    # 入力検証
    if not folder or not str(folder).strip():
        return (
            dbc.Alert("対象フォルダを指定してください。", color="warning"),
            no_update, no_update, no_update, no_update, no_update,
            no_update, no_update, no_update, no_update, no_update,
        )
    folder_path = Path(str(folder).strip())
    if not folder_path.exists() or not folder_path.is_dir():
        return (
            dbc.Alert(f"フォルダが存在しません: {folder_path}", color="danger"),
            no_update, no_update, no_update, no_update, no_update,
            no_update, no_update, no_update, no_update, no_update,
        )

    # 二重起動防止
    proc = _slim_process_state.get("process")
    if proc is not None and proc.poll() is None:
        return (
            dbc.Alert("既に RDS 軽量化が実行中です。", color="warning"),
            no_update, no_update, no_update, no_update, no_update,
            no_update, no_update, no_update, no_update, no_update,
        )

    # R スクリプト引数の組立
    extra_args = [str(folder_path)]
    if dry_run:
        extra_args.append("--dry-run")
    if backup:
        extra_args.append("--backup")
    if keep_scale:
        extra_args.append("--keep-scale")
    if keep_graphs:
        extra_args.append("--keep-graphs")
    inc = (include_pattern or "").strip() or _DEFAULT_INCLUDE
    extra_args.append(f"--include={inc}")

    # ログ / ステータスファイルの配置先
    _SLIM_LOG_DIR.mkdir(parents=True, exist_ok=True)
    output_dir = str(_SLIM_LOG_DIR)

    # プロセス起動
    result = start_analysis_process(
        str(_SLIM_SCRIPT), output_dir, extra_args=extra_args,
    )
    if not result.get("success"):
        return (
            dbc.Alert(
                f"プロセス起動に失敗しました: {result.get('message')}",
                color="danger",
            ),
            no_update, no_update, no_update, no_update, no_update,
            no_update, no_update, no_update, no_update, no_update,
        )

    # 状態保存
    _slim_process_state["process"] = result["process"]
    _slim_process_state["log_file"] = result["log_file"]
    _slim_process_state["status_file"] = result["status_file"]
    _slim_process_state["log_file_handle"] = result.get("log_file_handle")
    _slim_process_state["output_dir"] = output_dir

    store = {
        "log_file": result["log_file"],
        "status_file": result["status_file"],
        "output_dir": output_dir,
        "dry_run": bool(dry_run),
    }

    visible_style_log = {
        "backgroundColor": "#111",
        "color": "#d0d0d0",
        "padding": "8px",
        "borderRadius": "4px",
        "fontSize": "11px",
        "fontFamily": "Consolas, monospace",
        "maxHeight": "260px",
        "overflowY": "auto",
        "whiteSpace": "pre-wrap",
        "wordBreak": "break-all",
        "display": "block",
    }
    visible_style_bar = {"height": "18px", "display": "block"}

    info_msg = (
        "Dry-run で削減見込みを確認中です..." if dry_run
        else "RDS 軽量化を実行中です..."
    )
    return (
        dbc.Alert(info_msg, color="info"),
        "解析プロセスを起動しました...\n",
        visible_style_log,
        visible_style_bar,
        0, "準備中",
        "",                  # summary クリア
        False,               # interval enable
        store,
        False,               # stop ボタン enable
        True,                # run ボタン disable
    )


# ---------------------------------------------------------------------------
# 停止
# ---------------------------------------------------------------------------

@callback(
    Output("rds_maint_alert", "children", allow_duplicate=True),
    Output("rds_maint_interval", "disabled", allow_duplicate=True),
    Output("rds_maint_stop_btn", "disabled", allow_duplicate=True),
    Output("rds_maint_run_btn", "disabled", allow_duplicate=True),
    Input("rds_maint_stop_btn", "n_clicks"),
    prevent_initial_call=True,
)
def stop_rds_slim(n_clicks):
    if not n_clicks:
        return no_update, no_update, no_update, no_update

    proc = _slim_process_state.get("process")
    out_dir = _slim_process_state.get("output_dir")
    log_fh = _slim_process_state.get("log_file_handle")

    ok = False
    if proc is not None and out_dir:
        try:
            ok = stop_analysis_process(proc, out_dir, log_fh)
        except Exception as e:
            logger.exception("stop_analysis_process failed: %s", e)

    _slim_process_state["process"] = None
    _slim_process_state["log_file_handle"] = None

    msg = "停止要求を送信しました。" if ok else "プロセスが見つかりませんでした。"
    return (
        dbc.Alert(msg, color="warning"),
        True,     # interval disable
        True,     # stop ボタン disable
        False,    # run ボタン enable
    )


# ---------------------------------------------------------------------------
# ログポーリング
# ---------------------------------------------------------------------------

# ★ ver56.5 (デバッグ総点検 §4 / C11-1): `re.MULTILINE` が抜けていた。
#   これらは**ログ全文**（複数行の文字列）に対して search / finditer される。
#   MULTILINE が無いと `^` は文字列全体の先頭にしか一致しないため、2 行目以降の
#   「[slim] N files matched」「[i/N]」を **1 件も拾えない**。
#   その結果、総数 0 → 進捗バーは実行中ずっと 0%「準備中」のまま動かず、
#   終了した瞬間だけ 100% に跳ぶという表示になっていた（処理自体は正常）。
_FILE_COUNT_RE = re.compile(r"^\s*\[slim\]\s+(\d+)\s+files matched", re.MULTILINE)
_FILE_PROGRESS_RE = re.compile(r"^\s*\[(\d+)/(\d+)\]", re.MULTILINE)


def _parse_summary(log_text: str):
    """slim_existing_rds.R のサマリブロックから主要項目を抽出"""
    summary = {}
    patterns = {
        "Processed": r"\[slim\]\s+Processed\s+:\s+(.+)",
        "Skipped":   r"\[slim\]\s+Skipped\s+:\s+(.+)",
        "Errors":    r"\[slim\]\s+Errors\s+:\s+(.+)",
        "SizeBefore":r"\[slim\]\s+Size before:\s+(.+)",
        "SizeAfter": r"\[slim\]\s+Size after\s*:\s+(.+)",
        "Reduction": r"\[slim\]\s+Reduction\s+:\s+(.+)",
        "Elapsed":   r"\[slim\]\s+Elapsed\s+:\s+(.+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, log_text)
        if m:
            summary[key] = m.group(1).strip()
    return summary


def _render_summary(summary: dict, dry_run: bool, status: str):
    """完了サマリを見やすい Alert にまとめる"""
    if not summary:
        return ""
    label = "Dry-run 結果" if dry_run else "実行結果"
    color = "success" if status == "finished" else "warning"
    rows = []
    for key_label, key in [
        ("処理", "Processed"),
        ("スキップ", "Skipped"),
        ("エラー", "Errors"),
        ("元サイズ合計", "SizeBefore"),
        ("後サイズ合計", "SizeAfter"),
        ("削減率", "Reduction"),
        ("所要時間", "Elapsed"),
    ]:
        if key in summary:
            rows.append(html.Li(f"{key_label}: {summary[key]}"))
    return dbc.Alert([html.B(label), html.Ul(rows, className="mb-0 mt-1")], color=color)


@callback(
    Output("rds_maint_log", "children"),
    Output("rds_maint_progress_bar", "value"),
    Output("rds_maint_progress_bar", "label"),
    Output("rds_maint_progress_bar", "animated"),
    Output("rds_maint_summary", "children"),
    Output("rds_maint_alert", "children", allow_duplicate=True),
    Output("rds_maint_interval", "disabled"),
    Output("rds_maint_stop_btn", "disabled"),
    Output("rds_maint_run_btn", "disabled"),
    Input("rds_maint_interval", "n_intervals"),
    State("rds_maint_state", "data"),
    prevent_initial_call=True,
)
def poll_rds_slim(n, store):
    if not store or not store.get("log_file"):
        return no_update, no_update, no_update, no_update, no_update, \
               no_update, no_update, no_update, no_update

    log_file = store["log_file"]
    status_file = store["status_file"]
    dry_run = bool(store.get("dry_run"))

    # ログ末尾
    log_text = get_analysis_log(log_file, last_n=200) or ""
    log_children = format_log_lines_styled(log_text)

    # 件数 / 進捗抽出
    total = 0
    current = 0
    m_total = _FILE_COUNT_RE.search(log_text)
    if m_total:
        total = int(m_total.group(1))
    for m in _FILE_PROGRESS_RE.finditer(log_text):
        current = int(m.group(1))
        total = max(total, int(m.group(2)))
    pct = 0
    bar_label = "準備中"
    if total > 0:
        pct = int(min(100, round(100 * current / total)))
        bar_label = f"{current}/{total}"

    # 完了判定
    proc = _slim_process_state.get("process")
    log_fh = _slim_process_state.get("log_file_handle")
    status = None
    if proc is not None:
        try:
            status = check_process_completion(proc, status_file, log_fh)
        except Exception as e:
            logger.exception("check_process_completion failed: %s", e)

    if status in ("finished", "error"):
        _slim_process_state["process"] = None
        _slim_process_state["log_file_handle"] = None
        # サマリ抽出
        full_log = Path(log_file).read_text(encoding="utf-8", errors="replace")
        summary = _parse_summary(full_log)
        summary_node = _render_summary(summary, dry_run, status)
        if status == "finished":
            # ★ ver56.5 (§4 / C11-2): 一部のファイルが失敗していても緑の
            #   「完了しました。」を出していた（parquet 側は失敗を明示するのに
            #   RDS 側だけ非対称）。R が集計した Errors 件数を見て、
            #   0 でなければ警告色にし、件数を本文に出す。
            _errors = str(summary.get("Errors", "0")).strip()
            _has_errors = bool(_errors) and _errors not in ("0", "-", "")
            alert = dbc.Alert(
                ("完了しました（一部のファイルで失敗があります: "
                 f"Errors {_errors}）。下の詳細を確認してください。"
                 if _has_errors else "完了しました。")
                + ("（Dry-run のため書き込みはしていません）" if dry_run else ""),
                color="warning" if _has_errors else "success",
            )
            # 完了時は 100% を明示表示
            pct = 100
            bar_label = bar_label if total > 0 else "完了"
        else:
            alert = dbc.Alert(
                "エラーで終了しました。ログを確認してください。",
                color="danger",
            )
        return (
            log_children, pct, bar_label, False,  # animated off
            summary_node, alert,
            True,   # interval disable
            True,   # stop ボタン disable
            False,  # run ボタン enable
        )

    # まだ実行中
    return (
        log_children, pct, bar_label, True,
        no_update, no_update,
        False, False, True,
    )
