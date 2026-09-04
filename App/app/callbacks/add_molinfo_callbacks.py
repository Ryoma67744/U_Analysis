# =============================================================================
# MSI Analysis Application - Add Molecular Info Callbacks
# サブプロジェクトカードの「分子情報を登録」ボタン → SCiLS feature-list
# (CSV / `.sef`) をアップロードし、本体を書き換えずにサイドカー（化合物名注釈）を
# 後付け生成する。
# =============================================================================

import base64
import logging
import os
import tempfile
from pathlib import Path

from dash import (
    Input, Output, State, callback, ctx, no_update, html, ALL,
)
import dash_bootstrap_components as dbc

from app.services.project_manager import get_sub_project
from app.services.molinfo_attach import attach_molecular_info

logger = logging.getLogger("msi.add_molinfo")


# アップロードを受け付ける拡張子。ここに無いものは `.csv` として扱う（従来動作）。
_ALLOWED_SUFFIXES = (".csv", ".sef")


def _decode_to_temp(contents: str, filename) -> str:
    """`data:...;base64,<payload>` を復号し一時ファイルに書き出してパスを返す。

    ★ ver63.0: 拡張子が `suffix=".csv"` で**決め打ち**だった。読み口が CSV 専用
      だった頃は無害だったが、`.sef` を受け付ける今は致命的で、`.sef` を上げても
      一時ファイルが `molinfo_xxx.csv` になる。読み口 (`read_peaklist_any`) は
      拡張子で振り分けるので、**JSON を CSV パーサへ渡す**ことになり
      「m/z 列がありません」で落ちるか、最悪は黙って別物として読まれる。
      元のファイル名から拡張子を採る。
    """
    _ctype, b64 = str(contents).split(",", 1)
    data = base64.b64decode(b64)
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        suffix = ".csv"
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="molinfo_")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


def _safe_unlink(path) -> None:
    try:
        if path:
            os.unlink(path)
    except OSError:
        pass


def _danger(msg: str):
    return dbc.Alert(msg, color="danger", className="mb-0 py-2")


@callback(
    [Output("add_molinfo_modal", "is_open", allow_duplicate=True),
     Output("add_molinfo_target", "data"),
     Output("add_molinfo_body", "children", allow_duplicate=True),
     Output("add_molinfo_confirm_btn", "disabled", allow_duplicate=True),
     Output("add_molinfo_upload", "contents", allow_duplicate=True)],
    Input({"type": "sub_action_add_molinfo", "index": ALL}, "n_clicks"),
    State("selected_project", "data"),
    prevent_initial_call=True,
)
def open_add_molinfo(clicks, project):
    """「分子情報を登録」ボタン → モーダルを開き、対象を Store に積む（アップロード状態を初期化）。"""
    if not ctx.triggered_id or not any(c for c in (clicks or []) if c):
        return no_update, no_update, no_update, no_update, no_update
    sub_id = ctx.triggered_id["index"]
    project_id = project.get("id", "") if project else ""
    nonce = ctx.triggered[0].get("value") if ctx.triggered else None
    body = html.Div("SCiLS の「Static feature list」(CSV / .sef) を"
                    "アップロードしてください。",
                    className="text-muted")
    # contents=None で前回アップロードをクリア。confirm は無効化して開く。
    return True, {"project_id": project_id, "sub_id": sub_id, "nonce": nonce}, body, True, None


@callback(
    [Output("add_molinfo_body", "children", allow_duplicate=True),
     Output("add_molinfo_confirm_btn", "disabled", allow_duplicate=True)],
    Input("add_molinfo_upload", "contents"),
    State("add_molinfo_upload", "filename"),
    State("add_molinfo_target", "data"),
    prevent_initial_call=True,
)
def preview_add_molinfo(contents, filename, target):
    """CSV アップロード → ドライランでマッチ件数をプレビュー（まだ書き込まない）。"""
    if not contents or not target:
        return no_update, no_update
    sub = get_sub_project(target.get("project_id", ""), target.get("sub_id"))
    if not sub:
        return _danger("サブプロジェクトが見つかりません。"), True
    csv_path = None
    try:
        csv_path = _decode_to_temp(contents, filename)
        r = attach_molecular_info(sub, csv_path, dry_run=True)
    except Exception as e:  # noqa: BLE001
        logger.exception("molinfo プレビュー失敗")
        return _danger(f"peak-list の解析に失敗しました: {e}"), True
    finally:
        _safe_unlink(csv_path)

    # ★ ver52.3 (T5): 読めずに捨てた行を必ず併記する。従来は「N ピーク」としか
    #   出ないので、N が壊れ行のぶん少ないことに気づけなかった。マッチ 0 件の
    #   分岐にも出す（原因が「別データセット」ではなく「読めなかった」ことがある）。
    skip_msg = r.get("peaklist_skip_message") or ""
    skip_note = [html.Div(f"⚠ {skip_msg}", className="small text-warning")] if skip_msg else []

    if r["n_matched"] == 0:
        return dbc.Alert(
            [html.B("マッチ 0 件です。"),
             html.Div("peak-list の m/z とデータセットの特徴量 m/z が一致しません"
                      "（別データセットの feature list の可能性）。ファイルをご確認ください。",
                      className="small"),
             *skip_note],
            color="warning", className="mb-0 py-2"), True

    body = dbc.Alert([
        html.Div(f"peak-list: {r['n_peaklist']:,} ピーク ／ "
                 f"データセット特徴量 {r['n_features']:,} 件"),
        html.Div([html.B(f"{r['n_matched']:,} 件"),
                  " の特徴量に化合物名がマッチします。"
                  "「この内容で登録」で確定してください。"]),
        *skip_note,
    ], color="info", className="mb-0 py-2")
    return body, False


@callback(
    [Output("add_molinfo_body", "children", allow_duplicate=True),
     Output("add_molinfo_confirm_btn", "disabled", allow_duplicate=True),
     Output("sub_project_list_refresh", "data", allow_duplicate=True)],
    Input("add_molinfo_confirm_btn", "n_clicks"),
    [State("add_molinfo_upload", "contents"),
     State("add_molinfo_upload", "filename"),
     State("add_molinfo_target", "data"),
     State("sub_project_list_refresh", "data")],
    prevent_initial_call=True,
)
def confirm_add_molinfo(n_clicks, contents, filename, target, refresh):
    """「この内容で登録」→ サイドカーを生成・保存し、カード（化合物名バッジ）を更新。"""
    if not n_clicks or not contents or not target:
        return no_update, no_update, no_update
    sub = get_sub_project(target.get("project_id", ""), target.get("sub_id"))
    if not sub:
        return _danger("サブプロジェクトが見つかりません。"), True, no_update
    csv_path = None
    try:
        csv_path = _decode_to_temp(contents, filename)
        r = attach_molecular_info(sub, csv_path)
    except Exception as e:  # noqa: BLE001
        logger.exception("molinfo 登録失敗")
        return _danger(f"分子情報の登録に失敗しました: {e}"), False, no_update
    finally:
        _safe_unlink(csv_path)

    msg = dbc.Alert([
        html.B("分子情報を登録しました。"),
        html.Div(f"{r['n_matched']:,} / {r['n_features']:,} 特徴量に化合物名を付与しました。"),
        html.Div(f"サイドカー保存: {len(r['sidecar_paths'])} 箇所",
                 className="small text-muted"),
        html.Div("インタラクティブ解析・データ出力にも反映されます。",
                 className="small text-muted"),
    ], color="success", className="mb-0 py-2")
    # 化合物名バッジを更新するためカード一覧を再描画
    return msg, True, (refresh or 0) + 1


@callback(
    Output("add_molinfo_modal", "is_open", allow_duplicate=True),
    Input("add_molinfo_close_btn", "n_clicks"),
    prevent_initial_call=True,
)
def close_add_molinfo(n_clicks):
    """「閉じる」→ モーダルを閉じる。"""
    if not n_clicks:
        return no_update
    return False
