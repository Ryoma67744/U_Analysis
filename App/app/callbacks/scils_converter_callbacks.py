# =============================================================================
# MSI Analysis Application - SCiLS Converter Callbacks
# SCiLS 変換モーダルの開閉・変換実行ロジック
#
# ★ ver60.0: 変換を **サブプロセス + dcc.Interval ポーリング** に変えた。
#   それまでは Dash コールバック内で同期実行しており、次の 3 つが同時に起きていた:
#     1. Caddy の `read_timeout 600s` を超える変換が **HTTP 側で打ち切られる**
#        （変換自体は走り続けるので、利用者には「固まった」ようにしか見えない）
#     2. `convert_scils_to_parquet` は `progress_cb` を実装済みで `_report` を
#        7 箇所で呼んでいるのに、呼び出し側が一度も渡しておらず**死んでいた**
#        （ver4.22 で入れ、DiskcacheManager の expire=300 問題で ver4.23 に撤回）
#     3. 同時実行ガードが無く、1 変換あたり数 GB なので複数人が同時に押すと
#        12GB コンテナを圧迫した（CHANGELOG ver49.0 の「既知の課題」）
#   ver4.23 の CHANGELOG 自身が代替案として「サブプロセス＋ポーリング」を挙げており、
#   Parquet 再パックで既に動いている方式（parquet_maintenance_callbacks.py）を写した。
#   `start_analysis_process` に乗せることで、同時実行ブロック・空きメモリ/ディスク
#   チェック・ログ退避・watchdog がそのまま効く。
# =============================================================================

import json
import logging
import re
import sys
from pathlib import Path

import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, html, no_update

from app.config import APP_DIR, OTHER_DIR
from app.layouts.scils_converter_modal import BAR_STYLE, LOG_STYLE
from app.services.analysis_runner import (
    check_process_completion,
    get_analysis_log,
    start_analysis_process,
    stop_analysis_process,
)
from app.services.scils_converter import ConversionResult

logger = logging.getLogger("msi.scils_converter_callbacks")

_CONVERT_SCRIPT = APP_DIR / "tools" / "convert_scils_cli.py"
_CONVERT_LOG_DIR = OTHER_DIR / "logs" / "scils_convert"

# convert_scils_cli.py が出す進捗行。CLI 側の `_PROGRESS_PREFIX` と対。
# 片方だけ変えると進捗バーが黙って動かなくなる。
_PROGRESS_RE = re.compile(r"進捗: (\d+)% (.*)")

# 本解析・Parquet 再パックの状態と干渉しないよう独立した辞書を持つ
_convert_process_state: dict = {
    "process": None,
    "log_file": None,
    "status_file": None,
    "log_file_handle": None,
    "output_dir": None,
}

_VISIBLE_LOG_STYLE = {**LOG_STYLE, "display": "block"}
_VISIBLE_BAR_STYLE = {**BAR_STYLE, "display": "block"}

_RUN_OUTPUTS = 10       # run コールバックの Output 数


# ---------------------------------------------------------------------------
# モーダル開閉
# ---------------------------------------------------------------------------

@callback(
    Output("scils_converter_modal", "is_open"),
    Output("scils_conversion_result", "children", allow_duplicate=True),
    Input("open_scils_converter_modal", "n_clicks"),
    Input("scils_cancel_btn", "n_clicks"),
    State("scils_converter_modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_scils_converter_modal(open_clicks, cancel_clicks, is_open):
    if not (open_clicks or cancel_clicks):
        return no_update, no_update
    if not is_open:
        return True, ""
    return False, no_update


# ---------------------------------------------------------------------------
# 変換実行
# ---------------------------------------------------------------------------

def _auto_sample_name(input_folder: str) -> str:
    """入力フォルダ名をベースにサンプル名を生成"""
    name = Path(input_folder).name or "converted_sample"
    for bad in ("/", "\\", ":", "*", "?", "\"", "<", ">", "|"):
        name = name.replace(bad, "_")
    return name


def _alert_only(node):
    """結果欄にだけ出して他は触らない戻り値（入力検証エラー用）"""
    return (node,) + (no_update,) * (_RUN_OUTPUTS - 1)


@callback(
    Output("scils_conversion_result", "children"),
    Output("scils_conversion_log", "children", allow_duplicate=True),
    Output("scils_conversion_log", "style", allow_duplicate=True),
    Output("scils_progress_bar", "style", allow_duplicate=True),
    Output("scils_progress_bar", "value", allow_duplicate=True),
    Output("scils_progress_bar", "label", allow_duplicate=True),
    Output("scils_progress_interval", "disabled", allow_duplicate=True),
    Output("scils_conversion_state", "data", allow_duplicate=True),
    Output("scils_stop_btn", "disabled", allow_duplicate=True),
    Output("scils_run_btn", "disabled", allow_duplicate=True),
    Input("scils_run_btn", "n_clicks"),
    State("scils_input_folder", "value"),
    State("scils_output_folder", "value"),
    State("scils_sample_name", "value"),
    State("scils_organize_check", "value"),
    State("scils_float32_check", "value"),
    State("scils_drop_uncovered_check", "value"),
    State("scils_spot_block", "value"),
    prevent_initial_call=True,
)
def run_scils_conversion(
    n_clicks, input_folder, output_folder, sample_name,
    organize_value, float32_value, drop_uncovered_value, spot_block_value,
):
    if not n_clicks:
        return (no_update,) * _RUN_OUTPUTS

    if not input_folder:
        return _alert_only(dbc.Alert("入力フォルダを指定してください。", color="warning"))
    if not output_folder:
        return _alert_only(dbc.Alert("出力先フォルダを指定してください。", color="warning"))

    # 二重起動防止。start_analysis_process 側のガードは「解析が保守ツールを弾く」
    # 向きしか効かない（台帳に載るのは解析だけ）ので、変換同士はここで止める。
    proc = _convert_process_state.get("process")
    if proc is not None and proc.poll() is None:
        return _alert_only(dbc.Alert("既に SCiLS 変換が実行中です。", color="warning"))

    sample = (sample_name or "").strip() or _auto_sample_name(input_folder)
    if not sample.lower().endswith(".parquet"):
        sample = f"{sample}.parquet"

    out_path = str(Path(output_folder) / sample)

    organize = bool(organize_value) and "on" in (organize_value or [])
    store_float32 = bool(float32_value) and "on" in (float32_value or [])
    drop_uncovered = "on" in (drop_uncovered_value or [])
    try:
        spot_block = int(spot_block_value) if spot_block_value else 200
    except (TypeError, ValueError):
        spot_block = 200

    _CONVERT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    output_dir = str(_CONVERT_LOG_DIR)
    result_json = str(_CONVERT_LOG_DIR / "log" / "scils_result.json")
    # 前回の結果が残っていると、起動に失敗したときに古い成功結果を出しかねない。
    try:
        Path(result_json).unlink(missing_ok=True)
    except OSError as e:
        logger.warning("前回の結果 JSON を消せませんでした: %s", e)

    extra_args = [input_folder, out_path, f"--spot-block={spot_block}",
                  f"--result-json={result_json}"]
    if not store_float32:
        extra_args.append("--float64")
    if not organize:
        extra_args.append("--no-organize")
    if drop_uncovered:
        extra_args.append("--drop-uncovered")

    # -u と PYTHONUNBUFFERED は必須: stdout がファイルだと CPython はブロック
    # バッファになり、進捗が終了まで 1 行も出ずハングに見える。
    result = start_analysis_process(
        str(_CONVERT_SCRIPT), output_dir,
        extra_args=extra_args,
        interpreter=[sys.executable or "python3", "-u"],
        env_extra={"PYTHONUNBUFFERED": "1"},
    )
    if not result.get("success"):
        return _alert_only(dbc.Alert(
            f"変換プロセスを起動できませんでした: {result.get('message')}",
            color="danger",
        ))

    _convert_process_state.update({
        "process": result["process"],
        "log_file": result["log_file"],
        "status_file": result["status_file"],
        "log_file_handle": result.get("log_file_handle"),
        "output_dir": output_dir,
    })

    store = {
        "log_file": result["log_file"],
        "status_file": result["status_file"],
        "output_dir": output_dir,
        "result_json": result_json,
    }
    return (
        dbc.Alert("変換を実行中です…", color="info", className="mb-2"),
        "変換プロセスを起動しました...\n",
        _VISIBLE_LOG_STYLE,
        _VISIBLE_BAR_STYLE,
        0, "準備中",
        False,      # interval enable
        store,
        False,      # stop ボタン enable
        True,       # run ボタン disable
    )


# ---------------------------------------------------------------------------
# 停止
# ---------------------------------------------------------------------------

@callback(
    Output("scils_conversion_result", "children", allow_duplicate=True),
    Output("scils_progress_interval", "disabled", allow_duplicate=True),
    Output("scils_stop_btn", "disabled", allow_duplicate=True),
    Output("scils_run_btn", "disabled", allow_duplicate=True),
    Input("scils_stop_btn", "n_clicks"),
    prevent_initial_call=True,
)
def stop_scils_conversion(n_clicks):
    if not n_clicks:
        return no_update, no_update, no_update, no_update

    proc = _convert_process_state.get("process")
    out_dir = _convert_process_state.get("output_dir")
    if proc is None or out_dir is None:
        return no_update, True, True, False

    stop_analysis_process(proc, out_dir, _convert_process_state.get("log_file_handle"))
    _convert_process_state["process"] = None
    _convert_process_state["log_file_handle"] = None
    return (
        dbc.Alert(
            "変換を停止しました。出力ファイルは書き込まれていません"
            "（変換器は一時パスへ書いてから差し替えるため、既存の出力は無傷です）。",
            color="warning",
        ),
        True, True, False,
    )


# ---------------------------------------------------------------------------
# 進捗ポーリング
# ---------------------------------------------------------------------------

@callback(
    Output("scils_conversion_log", "children"),
    Output("scils_progress_bar", "value"),
    Output("scils_progress_bar", "label"),
    Output("scils_progress_bar", "animated"),
    Output("scils_conversion_result", "children", allow_duplicate=True),
    Output("scils_progress_interval", "disabled"),
    Output("scils_stop_btn", "disabled"),
    Output("scils_run_btn", "disabled"),
    Input("scils_progress_interval", "n_intervals"),
    State("scils_conversion_state", "data"),
    prevent_initial_call=True,
)
def poll_scils_conversion(n, store):
    if not store or not store.get("log_file"):
        return (no_update,) * 8

    log_text = get_analysis_log(store["log_file"], last_n=200) or ""

    pct, bar_label = 0, "準備中"
    for m in _PROGRESS_RE.finditer(log_text):
        # ログの並びに依存しないよう最大値を採る
        v = int(m.group(1))
        if v >= pct:
            pct, bar_label = v, m.group(2)

    proc = _convert_process_state.get("process")
    if proc is None:
        # プロセスを見失っている。2 通りある:
        #   1. 直前の tick が完了処理を終えて None にした（interval を止める要求は
        #      出しているが、既に飛んでいた次の tick がここへ来る）
        #   2. アプリが変換中に再起動して、モジュール変数の状態が消えた
        # どちらも「実行中」と答えてはいけない。答えると interval が**再開**され、
        # 完了後も永久にポーリングし続ける（run ボタンも無効のまま戻らない）。
        # 結果欄は触らずに（1 で描いた成功パネルを消さないよう no_update）止める。
        return log_text, no_update, no_update, False, no_update, True, True, False

    status = None
    try:
        status = check_process_completion(
            proc, store["status_file"], _convert_process_state.get("log_file_handle"))
    except Exception as e:
        logger.exception("check_process_completion failed: %s", e)

    if status is None:
        return log_text, pct, bar_label, True, no_update, False, False, True

    # --- 終了した ---
    _convert_process_state["process"] = None
    _convert_process_state["log_file_handle"] = None

    if status == "finished":
        node = _render_from_result_json(store.get("result_json"), log_text)
        return log_text, 100, "完了", False, node, True, True, False

    if status == "stopped":
        return (log_text, pct, bar_label, False,
                dbc.Alert("変換を停止しました。", color="warning"), True, True, False)

    # エラー。CLI は入力不備の説明を丸ごとログへ出すので、そこを見せる。
    return (
        log_text, pct, bar_label, False,
        dbc.Alert(
            [html.Div("変換に失敗しました。下のログをご確認ください。"),
             html.Pre(_error_excerpt(log_text), className="mb-0 small mt-2")],
            color="danger",
        ),
        True, True, False,
    )


def _error_excerpt(log_text: str, max_lines: int = 12) -> str:
    """ログから利用者に見せるエラー本文を取り出す。

    CLI は `変換エラー:` に続けて例外メッセージを字下げして出す。変換器の
    ValueError には「SCiLS Lab で再エクスポートしてください」のような
    **利用者が直せる指示**が入っているので、traceback ではなくそこを見せる。
    """
    lines = log_text.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("変換エラー:"):
            continue
        # 字下げが続く間だけを本文とする。`start_analysis_process` は
        # stderr を stdout へ合流させる (`stderr=subprocess.STDOUT`) ので、
        # 本文の直後には traceback が続く。traceback のコード行も字下げされて
        # いるため「字下げ行を拾う」だけでは混ざる。**最初の非字下げ行で打ち切る**。
        body = []
        for ln in lines[i + 1:]:
            if not ln.startswith("  ") or ln.lstrip().startswith(("File \"", "Traceback")):
                break
            body.append(ln.strip())
            if len(body) >= max_lines:
                break
        if body:
            return "\n".join(body)
        break
    return "\n".join(lines[-max_lines:])


def _render_from_result_json(result_json, log_text: str):
    """CLI が書いた結果 JSON を ConversionResult に戻して成功パネルを描く。

    JSON が無い / 壊れているときも、Parquet 自体は書き終わっている可能性が高いので
    「失敗」とは表示しない（ver55.4 が organize の失敗で「成功した変換を失敗と
    誤認させる」欠陥を直したのと同じ判断）。
    """
    if not result_json:
        return dbc.Alert("変換が完了しました。", color="success")
    try:
        data = json.loads(Path(result_json).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("結果 JSON を読めませんでした: %s", e)
        return dbc.Alert(
            "変換は完了しましたが、詳細を読み取れませんでした。ログをご確認ください。",
            color="success",
        )
    if "error" in data:
        return dbc.Alert(
            [html.Div("変換に失敗しました。"),
             html.Pre(str(data["error"]), className="mb-0 small mt-2")],
            color="danger",
        )
    try:
        # 既知のフィールドだけ渡す。CLI 側が新しいフィールドを足しても
        # TypeError で成功表示が壊れないようにする。
        known = {f for f in ConversionResult.__dataclass_fields__}
        return _render_success(ConversionResult(
            **{k: v for k, v in data.items() if k in known}))
    except Exception as e:
        logger.exception("結果パネルの描画に失敗: %s", e)
        return dbc.Alert("変換が完了しました。", color="success")


def _render_success(result) -> html.Div:
    """ConversionResult を成功パネルとしてレンダリング"""
    warning_items = [html.Li(w) for w in result.warnings]
    # ★ ver55.0: 領域アノテーションを一枚も渡していないのに、Spot ファイル名から
    #   作ったラベルを「annotation ラベル」として出していた（「Annotation CSV: (なし)」
    #   と併記されるので矛盾して見える）。由来を明示して区別する。
    if getattr(result, "annotation_source", "none") == "csv":
        ann_labels = ", ".join(result.annotation_labels) or "(なし)"
    else:
        ann_labels = "(なし — 領域アノテーション CSV が無いため全 spot が Unannotated)"

    details = [
        (html.Dt("出力ファイル"), html.Dd(result.output_path)),
        (html.Dt("Intensity CSV"), html.Dd(Path(result.source_intensity).name)),
        (html.Dt("Spot CSV"), html.Dd(Path(result.source_spot).name)),
        (
            html.Dt("Annotation CSV"),
            html.Dd(
                ", ".join(Path(p).name for p in result.annotation_files)
                if result.annotation_files else "(なし)"
            ),
        ),
        (
            html.Dt("Peak-list (化合物名)"),
            html.Dd(
                f"{Path(result.peak_list_file).name}"
                f"（{result.n_annotated:,} / {result.n_mz_features:,} feature に登録）"
                if result.has_peak_list else "(なし)"
            ),
        ),
        (html.Dt("spot 数"), html.Dd(f"{result.n_spots:,}")),
        (html.Dt("m/z 列数"), html.Dd(f"{result.n_mz_features:,}")),
        (html.Dt("annotation ラベル"), html.Dd(ann_labels)),
        (html.Dt("所要時間"), html.Dd(f"{result.duration_sec:.1f} 秒")),
    ]
    if result.n_row_groups:
        layout_text = (
            f"{result.n_row_groups:,} 個 × {result.row_group_rows:,} 行"
            f"（フッタ {result.footer_bytes / (1024 ** 2):.2f} MB）"
        )
        if result.n_row_groups == 1:
            layout_text += " — 全行 1 row group"
        details.append((html.Dt("row group"), html.Dd(layout_text)))
    if result.organized:
        details.append((html.Dt("整理モード"), html.Dd("ON (元 CSV をサブフォルダに移動)")))

    dl_children = []
    for dt, dd in details:
        dl_children.append(dt)
        dl_children.append(dd)

    return html.Div([
        dbc.Alert("✅ 変換完了", color="success", className="mb-2"),
        html.Dl(className="mb-0", children=dl_children),
        html.Div(
            style={"marginTop": "8px"} if warning_items else {"display": "none"},
            children=[
                html.Strong("警告:"),
                html.Ul(warning_items),
            ],
        ),
        html.Small(
            "解析タブの「データフォルダ」に出力先を指定するとそのまま読込できます。",
            className="text-muted d-block mt-2",
        ),
        # ★ ver55.0: 化合物名を列名に焼き込まなくなったので、その旨と戻し方を出す。
        html.Small(
            "化合物名は Parquet の列名ではなくサイドカー "
            "（<出力名>_feature_annotations.parquet）に登録します。"
            "列名は m/z のままなので、解析設定の「変換元 CSV 由来の化合物名を使う」で"
            "いつでも表示を切り替えられます。"
            if result.has_peak_list else
            # ★ ver63.0: `.sef` も受け付けるようになったので文言に含める。
            "Feature list (peak-list) CSV / .sef が無かったため化合物名は登録していません。"
            "あとから「分子情報を登録」で追加できます。",
            className="text-muted d-block mt-1",
        ),
    ])
