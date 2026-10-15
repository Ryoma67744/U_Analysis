# =============================================================================
# MSI Analysis Application - File Browser Modal
# Web内蔵ファイルブラウザ モーダルダイアログ
# =============================================================================

import logging
import os
import string

logger = logging.getLogger(__name__)
from pathlib import Path, PurePosixPath

from dash import html, dcc
import dash_bootstrap_components as dbc

from app.config import BROWSER_SHORTCUTS
from app.services.public_paths import get_path_mappings, to_display_path, to_internal_path, virtual_children


def create_file_browser_modal():
    """ファイルブラウザモーダルのレイアウト"""
    return dbc.Modal(
        id="file_browser_modal",
        size="xl",
        centered=True,
        # 変換などのモーダルから開く場合も、その前面で操作できるようにする。
        zindex=1070,
        children=[
            dbc.ModalHeader(dbc.ModalTitle("ファイル / フォルダ選択")),
            dbc.ModalBody([
                # ドライブ選択 + パス直接入力
                dbc.Row(className="mb-2", children=[
                    dbc.Col(width=3, children=[
                        dcc.Dropdown(id="fb_drive_selector", placeholder="ドライブ"),
                    ]),
                    dbc.Col(width=9, children=[
                        dbc.InputGroup([
                            dbc.Input(id="fb_path_input", placeholder="パスを入力..."),
                            dbc.Button("移動", id="fb_go_btn", color="primary", size="sm"),
                        ]),
                    ]),
                ]),
                # ショートカットバー: サーバー上の主要パスへワンクリック移動
                dbc.Row(className="mb-2", children=[
                    dbc.Col(width=12, children=[
                        html.Small("ショートカット:", className="text-muted me-2"),
                        *[
                            dbc.Button(
                                sc["label"],
                                id={"type": "fb_shortcut", "path": sc["path"]},
                                color="info",
                                outline=True,
                                size="sm",
                                className="me-1",
                            )
                            for sc in BROWSER_SHORTCUTS
                        ],
                    ]),
                ]),
                # パンくずナビ
                html.Div(id="fb_breadcrumb", className="file-browser-breadcrumb"),
                # ファイルリスト
                html.Div(
                    id="fb_file_list",
                    className="file-browser-list",
                    children=[html.Div("フォルダを選択してください", style={"padding": "20px", "color": "#999"})],
                ),
                # 選択されたパス表示
                html.Div(className="mt-2", children=[
                    dbc.Label("選択中:"),
                    html.Span(id="fb_selected_path", className="ms-2 text-primary fw-bold"),
                ]),
            ]),
            dbc.ModalFooter([
                dbc.Button("キャンセル", id="fb_cancel_btn", color="secondary"),
                dbc.Button("選択", id="fb_select_btn", color="primary"),
            ]),
        ],
    )


# ---------------------------------------------------------------------------
# サーバーサイドユーティリティ
# ---------------------------------------------------------------------------

def get_available_drives() -> list[dict]:
    """利用可能なドライブ一覧を返す（Windows用）"""
    mappings = get_path_mappings()
    if mappings:
        return [{"label": public, "value": public} for _, public in mappings]
    drives = []
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if Path(drive).exists():
                drives.append({"label": drive, "value": drive})
    else:
        drives.append({"label": "/", "value": "/"})
    return drives


def browser_directory(dir_path, show_files=True):
    """Public ancestors list configured mounts; physical I/O uses internal paths."""
    virtual = virtual_children(str(dir_path or ""))
    if virtual is not None:
        return str(dir_path), virtual
    internal = to_internal_path(str(dir_path or ""))
    if not internal or not Path(internal).is_dir():
        raise ValueError("サーバー上の有効なフォルダを指定してください。")
    return internal, list_directory(internal, show_files=show_files)


def browser_parent(dir_path):
    public = to_display_path(str(dir_path))
    if public.startswith("/") or public != str(dir_path) or virtual_children(public) is not None:
        parent = str(PurePosixPath(public).parent) if public.startswith("/") else str(Path(public).parent)
        return to_internal_path(parent)
    return str(Path(dir_path).parent)


def list_directory(dir_path: str, show_files: bool = True) -> list[dict]:
    """ディレクトリの内容を一覧で返す"""
    items = []
    target = Path(dir_path)

    if not target.is_dir():
        return items

    try:
        for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            # 隠しファイル/システムファイルをスキップ
            if entry.name.startswith(".") or entry.name.startswith("$"):
                continue
            try:
                items.append({
                    "name": entry.name,
                    "path": str(entry),
                    "is_dir": entry.is_dir(),
                    "icon": "📁" if entry.is_dir() else "📄",
                })
            except PermissionError as e:
                # ver3.7: entry レベルの権限エラーを情報ログに残す
                logger.info("PermissionError on %s: %s", entry, e)
                continue
    except PermissionError as e:
        # ver3.7: ディレクトリ全体への権限エラーも記録
        logger.info("PermissionError on %s: %s", target, e)

    if not show_files:
        items = [i for i in items if i["is_dir"]]

    return items


def build_breadcrumb_parts(dir_path: str) -> list[dict]:
    """パンくずナビ用のパーツを返す"""
    path = PurePosixPath(dir_path) if str(dir_path).startswith("/") else Path(dir_path)
    parts = []
    current = path
    while True:
        parts.append({"name": current.name or str(current), "path": str(current)})
        parent = current.parent
        if parent == current:
            break
        current = parent
    parts.reverse()
    return parts
