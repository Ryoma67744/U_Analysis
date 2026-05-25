"""外部公開 URL (共有リンク等) のベースを組み立てるヘルパー。

SHARE_BASE_URL が未設定のときに、コンテナ内部 IP ではなく
「ブラウザが実際にアクセスしてきた公開ホスト」から URL を組み立てるために使う。
"""
from __future__ import annotations

from flask import has_request_context, request


def external_base_url(fallback_port: int) -> str:
    """外部からアクセス可能なベース URL (scheme://host) を返す。

    優先順位:
    1. Flask request context 内: X-Forwarded-Proto (Caddy 等のリバースプロキシ) または
       request.scheme + request.host (プロキシが Host を保持) → 例 https://133.167.73.188
    2. request 文脈外 (CLI/バックグラウンド): ホスト名から推定 (最終手段)。
       ※コンテナ内では内部 IP になり外部到達不可なので、あくまで保険。

    呼び出し側で SHARE_BASE_URL が優先されるため、本関数は未設定時のみ使われる。
    """
    if has_request_context():
        proto = request.headers.get("X-Forwarded-Proto", "").lower() or request.scheme
        host = request.host  # 例: 133.167.73.188 (Caddy が元の Host を保持)
        if host:
            return f"{proto}://{host}"
    import socket
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        ip = "127.0.0.1"
    return f"http://{ip}:{fallback_port}"
