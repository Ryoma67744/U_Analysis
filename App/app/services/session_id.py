"""匿名 Cookie セッション ID 管理。

ログイン不要で、各ブラウザに UUID を発行して識別する。
複数ユーザーが同じ BasicAuth パスワードでアクセスしても、ブラウザ毎に
異なる session_id を持つため UI ロックの「誰が編集中」を判別可能。

使用例:
    sid = get_or_create_session_id()  # Flask request コンテキスト内で呼出
    display = short_display_id(sid)   # "User abc12"
"""
from __future__ import annotations

import uuid
from typing import Optional

from flask import after_this_request, request

COOKIE_NAME = "msi_session_id"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 日


def get_or_create_session_id() -> str:
    """現在のリクエストの session_id を取得。Cookie 未設定なら生成して set-cookie する。

    Flask の request context 内でのみ呼び出し可能。
    Returns:
        16 桁の hex 文字列 (uuid4.hex)
    """
    sid = request.cookies.get(COOKIE_NAME)
    if not sid:
        sid = uuid.uuid4().hex

        @after_this_request
        def _set_cookie(resp):
            resp.set_cookie(
                COOKIE_NAME, sid,
                max_age=COOKIE_MAX_AGE,
                httponly=False,  # clientside JS で読めるようにする (UI ロックで必要)
                samesite="Lax",
                # Note: HTTPS 終端 (Caddy) 配下なら secure=True が推奨。
                # ローカル開発 (HTTP) では secure=False のため、明示しない (Flask デフォルト)。
            )
            return resp
    return sid


def get_session_id_or_none() -> Optional[str]:
    """セッション ID を取得（無ければ None、新規作成しない）。

    Dash callback 内で「とりあえず ID 取れるなら取る」用途。
    Flask request context 外で呼ばれた場合も None を返す（例外を投げない）。
    """
    try:
        return request.cookies.get(COOKIE_NAME)
    except RuntimeError:
        return None


def short_display_id(sid: Optional[str]) -> str:
    """表示用の短縮 ID（例: 'User abc12'、None なら 'Unknown user'）"""
    if not sid or len(sid) < 5:
        return "Unknown user"
    return f"User {sid[:5]}"
