# =============================================================================
# MSI Analysis Application - Parquet 再パックコールバック
#
# モーダル parquet_maintenance_modal から App/tools/repack_parquet_rowgroups.py
# を実行し、ログ / プログレスバー / サマリを更新する Dash コールバック群。
#
# プロセス実体は本解析および RDS 軽量化と干渉しないよう、
# 別モジュールローカル変数で保持する。
# =============================================================================

import logging
import re
import sys
from pathlib import Path

import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, ctx, html, no_update

from app.config import APP_DIR, OTHER_DIR
from app.layouts.parquet_maintenance_modal import BAR_STYLE, LOG_STYLE
from app.services.analysis_runner import (
    check_process_completion,
    format_log_lines_styled,
    get_analysis_log,
    start_analysis_process,
    stop_analysis_process,
)

logger = logging.getLogger("msi.parquet_maintenance_callbacks")

_REPACK_SCRIPT = APP_DIR / "tools" / "repack_parquet_rowgroups.py"
_REPACK_LOG_DIR = OTHER_DIR / "logs" / "parquet_repack"
_DEFAULT_INCLUDE = "*.parquet"

# 本解析・RDS 軽量化の状態と干渉しないように独立した辞書を持つ
_repack_process_state: dict = {
    "process": None,
    "log_file": None,
    "status_file": None,
    "log_file_handle": None,
    "output_dir": None,
}

_VISIBLE_LOG_STYLE = {**LOG_STYLE, "display": "block"}
_VISIBLE_BAR_STYLE = {**BAR_STYLE, "display": "block"}

_NO_UPDATE_11 = (no_update,) * 11


# ---------------------------------------------------------------------------
# モーダル開閉
# ---------------------------------------------------------------------------

@callback(
    Output("parquet_maintenance_modal", "is_open"),
    Input("open_parquet_maintenance_modal", "n_clicks"),
    Input("open_parquet_maintenance_modal_landing", "n_clicks"),
    Input("parquet_maint_close_btn", "n_clicks"),
    State("parquet_maintenance_modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_parquet_maintenance_modal(open_sidebar, open_landing, close_clicks, is_open):
    trig = ctx.triggered_id
    if trig in ("open_parquet_maintenance_modal",
                "open_parquet_maintenance_modal_landing"):
        return True
    if trig == "parquet_maint_close_btn":
        return False
    return no_update


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------

@callback(
    Output("parquet_maint_alert", "children"),
    Output("parquet_maint_log", "children", allow_duplicate=True),
    Output("parquet_maint_log", "style", allow_duplicate=True),
    Output("parquet_maint_progress_bar", "style", allow_duplicate=True),
    Output("parquet_maint_progress_bar", "value", allow_duplicate=True),
    Output("parquet_maint_progress_bar", "label", allow_duplicate=True),
    Output("parquet_maint_summary", "children", allow_duplicate=True),
    Output("parquet_maint_interval", "disabled", allow_duplicate=True),
    Output("parquet_maint_state", "data", allow_duplicate=True),
    Output("parquet_maint_stop_btn", "disabled", allow_duplicate=True),
    Output("parquet_maint_run_btn", "disabled", allow_duplicate=True),
    Input("parquet_maint_run_btn", "n_clicks"),
    State("parquet_maint_folder", "value"),
    State("parquet_maint_dry_run", "value"),
    State("parquet_maint_backup", "value"),
    State("parquet_maint_include", "value"),
    State("parquet_maint_allow_split", "value"),
    State("parquet_maint_skip_verify", "value"),
    prevent_initial_call=True,
)
def run_parquet_repack(
    n_clicks, folder, dry_run, backup, include_pattern,
    allow_split, skip_verify,
):
    # 入力検証
    if not folder or not str(folder).strip():
        return (dbc.Alert("対象フォルダを指定してください。", color="warning"),
                *_NO_UPDATE_11[1:])
    folder_path = Path(str(folder).strip())
    if not folder_path.exists() or not folder_path.is_dir():
        return (dbc.Alert(f"フォルダが存在しません: {folder_path}", color="danger"),
                *_NO_UPDATE_11[1:])

    # 二重起動防止
    proc = _repack_process_state.get("process")
    if proc is not None and proc.poll() is None:
        return (dbc.Alert("既に Parquet 再パックが実行中です。", color="warning"),
                *_NO_UPDATE_11[1:])

    # CLI 引数の組立
    extra_args = [str(folder_path)]
    if dry_run:
        extra_args.append("--dry-run")
    if not backup:
        extra_args.append("--no-backup")
    if allow_split:
        extra_args.append("--allow-split")
    if skip_verify:
        extra_args.append("--skip-verify")
    inc = (include_pattern or "").strip() or _DEFAULT_INCLUDE
    extra_args.append(f"--include={inc}")

    _REPACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
    output_dir = str(_REPACK_LOG_DIR)

    # プロセス起動。
    # -u と PYTHONUNBUFFERED は必須: stdout がファイルだと CPython は
    # ブロックバッファになり、進捗が終了まで 1 行も出ずハングに見える。
    # ARROW_NUM_THREADS は env_extra 経由でしか効かない（Arrow は
    # OMP_NUM_THREADS を見ない）。スレッドごとに伸長バッファを持つため上限を掛ける。
    result = start_analysis_process(
        str(_REPACK_SCRIPT), output_dir,
        extra_args=extra_args,
        interpreter=[sys.executable or "python3", "-u"],
        env_extra={"PYTHONUNBUFFERED": "1", "ARROW_NUM_THREADS": "4"},
    )
    if not result.get("success"):
        return (
            dbc.Alert(
                f"プロセス起動に失敗しました: {result.get('message')}",
                color="danger",
            ),
            *_NO_UPDATE_11[1:],
        )

    _repack_process_state["process"] = result["process"]
    _repack_process_state["log_file"] = result["log_file"]
    _repack_process_state["status_file"] = result["status_file"]
    _repack_process_state["log_file_handle"] = result.get("log_file_handle")
    _repack_process_state["output_dir"] = output_dir

    store = {
        "log_file": result["log_file"],
        "status_file": result["status_file"],
        "output_dir": output_dir,
        "dry_run": bool(dry_run),
    }

    info_msg = (
        "Dry-run で対象と見積りを確認中です..." if dry_run
        else "Parquet 再パックを実行中です..."
    )
    return (
        dbc.Alert(info_msg, color="info"),
        "再パックプロセスを起動しました...\n",
        _VISIBLE_LOG_STYLE,
        _VISIBLE_BAR_STYLE,
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
    Output("parquet_maint_alert", "children", allow_duplicate=True),
    Output("parquet_maint_interval", "disabled", allow_duplicate=True),
    Output("parquet_maint_stop_btn", "disabled", allow_duplicate=True),
    Output("parquet_maint_run_btn", "disabled", allow_duplicate=True),
    Input("parquet_maint_stop_btn", "n_clicks"),
    prevent_initial_call=True,
)
def stop_parquet_repack(n_clicks):
    if not n_clicks:
        return no_update, no_update, no_update, no_update

    proc = _repack_process_state.get("process")
    out_dir = _repack_process_state.get("output_dir")
    log_fh = _repack_process_state.get("log_file_handle")

    ok = False
    if proc is not None and out_dir:
        try:
            ok = stop_analysis_process(proc, out_dir, log_fh)
        except Exception as e:
            logger.exception("stop_analysis_process failed: %s", e)

    _repack_process_state["process"] = None
    _repack_process_state["log_file_handle"] = None

    msg = (
        "停止要求を送信しました。書き込み途中のファイルは破棄され、"
        "元のファイルはそのまま残ります。"
        if ok else "プロセスが見つかりませんでした。"
    )
    return (
        dbc.Alert(msg, color="warning"),
        True,     # interval disable
        True,     # stop ボタン disable
        False,    # run ボタン enable
    )


# ---------------------------------------------------------------------------
# ログポーリング
# ---------------------------------------------------------------------------

# re.MULTILINE は必須。これが無いと `^` がログ全体の先頭にしかマッチせず、
# 進捗バーが実行中ずっと 0% のままになる（rds_maintenance_callbacks の既存不具合）。
_FILE_COUNT_RE = re.compile(r"^\s*\[repack\]\s+(\d+)\s+files matched", re.MULTILINE)
_FILE_PROGRESS_RE = re.compile(r"^\s*\[(\d+)/(\d+)\]", re.MULTILINE)


def _parse_summary(log_text: str):
    """repack_parquet_rowgroups.py のサマリブロックから主要項目を抽出"""
    summary = {}
    patterns = {
        "Processed":  r"\[repack\]\s+Processed\s+:\s+(.+)",
        "Skipped":    r"\[repack\]\s+Skipped\s+:\s+(.+)",
        "Errors":     r"\[repack\]\s+Errors\s+:\s+(.+)",
        "SizeBefore": r"\[repack\]\s+Size before:\s+(.+)",
        "SizeAfter":  r"\[repack\]\s+Size after\s*:\s+(.+)",
        "Reduction":  r"\[repack\]\s+Reduction\s+:\s+(.+)",
        "Elapsed":    r"\[repack\]\s+Elapsed\s+:\s+(.+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, log_text)
        if m:
            summary[key] = m.group(1).strip()
    return summary


def _summary_has_errors(summary: dict) -> bool:
    """サマリのエラー件数が 1 以上か（数値として読めないときは False）"""
    try:
        return int(str(summary.get("Errors", "0")).split()[0]) > 0
    except (ValueError, IndexError):
        return False


def _render_summary(summary: dict, dry_run: bool, status: str):
    """完了サマリを見やすい Alert にまとめる"""
    if not summary:
        return ""
    label = "Dry-run 結果" if dry_run else "実行結果"
    ok = status == "finished" and not _summary_has_errors(summary)
    color = "success" if ok else "warning"
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
    Output("parquet_maint_log", "children"),
    Output("parquet_maint_progress_bar", "value"),
    Output("parquet_maint_progress_bar", "label"),
    Output("parquet_maint_progress_bar", "animated"),
    Output("parquet_maint_summary", "children"),
    Output("parquet_maint_alert", "children", allow_duplicate=True),
    Output("parquet_maint_interval", "disabled"),
    Output("parquet_maint_stop_btn", "disabled"),
    Output("parquet_maint_run_btn", "disabled"),
    Input("parquet_maint_interval", "n_intervals"),
    State("parquet_maint_state", "data"),
    prevent_initial_call=True,
)
def poll_parquet_repack(n, store):
    if not store or not store.get("log_file"):
        return (no_update,) * 9

    log_file = store["log_file"]
    status_file = store["status_file"]
    dry_run = bool(store.get("dry_run"))

    log_text = get_analysis_log(log_file, last_n=200) or ""
    log_children = format_log_lines_styled(log_text)

    # 件数 / 進捗抽出
    total = 0
    current = 0
    m_total = _FILE_COUNT_RE.search(log_text)
    if m_total:
        total = int(m_total.group(1))
    for m in _FILE_PROGRESS_RE.finditer(log_text):
        # 末尾の値ではなく最大値を採る（ログの並びに依存しないように）
        current = max(current, int(m.group(1)))
        total = max(total, int(m.group(2)))
    pct = 0
    bar_label = "準備中"
    if total > 0:
        pct = int(min(100, round(100 * current / total)))
        bar_label = f"{current}/{total}"

    # 完了判定
    proc = _repack_process_state.get("process")
    log_fh = _repack_process_state.get("log_file_handle")
    status = None
    if proc is not None:
        try:
            status = check_process_completion(proc, status_file, log_fh)
        except Exception as e:
            logger.exception("check_process_completion failed: %s", e)

    if status in ("finished", "error"):
        _repack_process_state["process"] = None
        _repack_process_state["log_file_handle"] = None
        full_log = Path(log_file).read_text(encoding="utf-8", errors="replace")
        summary = _parse_summary(full_log)
        summary_node = _render_summary(summary, dry_run, status)
        has_err = _summary_has_errors(summary)
        if status == "finished" and not has_err:
            alert = dbc.Alert(
                "完了しました。" + ("（Dry-run のため書き込みはしていません）"
                                      if dry_run else ""),
                color="success",
            )
            pct = 100
            bar_label = bar_label if total > 0 else "完了"
        elif status == "finished" and has_err:
            # CLI は 1 件でも失敗すると exit 2 を返すので通常ここには来ないが、
            # 取りこぼしても緑の成功表示にならないよう二重に守る。
            alert = dbc.Alert(
                "一部のファイルで失敗しました。ログを確認してください。",
                color="warning",
            )
            pct = 100
        else:
            alert = dbc.Alert(
                "エラーで終了しました。ログを確認してください。"
                "元のファイルは変更されていません。",
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
