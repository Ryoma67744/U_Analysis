"""解析画面へのディープリンク URL（`/open/<pid>/<sid>`）の組み立て・解釈。

ChatGPT が検索したプロジェクトのフル解析画面（インタラクティブ解析）を、
URL だけで直接開けるようにするための小さな純関数群。

- ブラウザ側ルーティング（`tab_url_routing.py`）が `parse_open_path` で URL を分解し、
  `interactive_*` ストアへ流し込んでフル解析画面を自動ロードする。
- ChatGPT 受付 API（`gpt_api.py`）が `open_view_path` で応答に添える相対パスを作る。

flask / dash に依存しない純関数のみ（テスト容易性のため）。
"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote

# /open/<project_id>/<sub_project_id> （末尾スラッシュ許容）
_OPEN_URL_RE = re.compile(r"^/open/([^/]+)/([^/]+)/?$")


def open_view_path(project_id, sub_project_id) -> str:
    """プロジェクト/サブプロジェクトの解析画面を開く相対パスを返す。

    例: ``open_view_path("p1", "s1") -> "/open/p1/s1"``。
    ID に含まれる特殊文字はパーセントエンコードする（URL 安全にする）。
    """
    pid = quote(str(project_id), safe="")
    sid = quote(str(sub_project_id), safe="")
    return f"/open/{pid}/{sid}"


def parse_open_path(pathname):
    """``/open/<pid>/<sid>`` を ``(project_id, sub_project_id)`` に分解する。

    マッチしなければ ``None`` を返す。パーセントエンコードは復号する。
    """
    if not pathname:
        return None
    m = _OPEN_URL_RE.match(pathname)
    if not m:
        return None
    return unquote(m.group(1)), unquote(m.group(2))
