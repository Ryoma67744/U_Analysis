"""アクセスログ (監査用)。

解析者名 + tier + path を全リクエストに付与する Filter と、
専用ファイル (access.log) への出力ハンドラを提供。
"""
from __future__ import annotations

import logging
import hashlib
import math
import os
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

from flask import g, has_request_context, request, session

ACCESS_LOGGER_NAME = "msi.access"
_CALLBACK_PATH = "/_dash-update-component"
_REQUEST_ID = re.compile(r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\Z")


def begin_request_diagnostics() -> None:
    """Start the application timer; the ID is for correlation, never authorization.

    Caddy replaces X-Request-ID at the edge. Direct callers may supply a valid
    UUID too, so this value must not be treated as proof of identity. No request
    body is read before authentication.
    """
    incoming = request.headers.get("X-Request-ID", "")
    g._msi_http_request_id = incoming.lower() if _REQUEST_ID.fullmatch(incoming) else str(uuid4())
    g._msi_http_started = time.perf_counter()
    g._msi_http_callback = "-"
    g._msi_http_trace = False


def identify_callback_request(callback_map) -> None:
    """Identify only a registered server callback, after authentication.

    The client-supplied output is used only as a lookup key. Log the registered
    Python function and a short key hash, never Input/State values or arbitrary
    client text (pattern IDs can contain private paths or sample names).
    """
    if request.path != _CALLBACK_PATH or request.method != "POST":
        return
    try:
        payload = request.get_json(silent=True)
        output = payload.get("output") if isinstance(payload, dict) else None
        entry = callback_map.get(output) if isinstance(output, str) else None
        func = entry.get("callback") if isinstance(entry, dict) else None
        label = "unregistered"
        if callable(func):
            name = f"{getattr(func, '__module__', '')}.{getattr(func, '__qualname__', '')}"
            name = re.sub(r"[^a-zA-Z0-9_.]", "_", name)[:200]
            label = f"{name}#{hashlib.sha256(output.encode('utf-8')).hexdigest()[:12]}"
        g._msi_http_callback = label
        g._msi_http_trace = os.environ.get("MSI_HTTP_TRACE_CALLBACKS", "0").lower() in ("1", "true", "yes")
        if g._msi_http_trace:
            logging.getLogger(ACCESS_LOGGER_NAME).info(
                "phase=start request_id=%s path=%s method=%s callback=%s",
                g._msi_http_request_id, request.path, request.method, label,
            )
    except Exception:
        # Diagnostics must not replace the callback's own validation or error.
        pass


def finish_request_diagnostics(response) -> float | None:
    """Attach the correlation ID and return time spent inside Flask, in seconds.

    This excludes Waitress queueing and proxy/network time; compare Caddy's
    duration separately. A proxy-generated 502 may have no matching completion.
    """
    identity = getattr(g, "_msi_http_request_id", None)
    if identity:
        response.headers["X-Request-ID"] = identity
    started = getattr(g, "_msi_http_started", None)
    return max(0.0, time.perf_counter() - started) if started is not None else None


def _slow_request_seconds() -> float:
    try:
        value = float(os.environ.get("MSI_HTTP_SLOW_REQUEST_SEC", "5"))
        if math.isfinite(value) and value > 0:
            return value
    except (TypeError, ValueError):
        pass
    return 5.0


class AnalystContextFilter(logging.Filter):
    """全ログレコードに analyst_name / access_tier を注入する Filter。

    Flask request context 外 (起動時など) では "system" / "-" を使う。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if has_request_context():
            try:
                record.analyst_name = session.get("analyst_name", "anonymous")
                record.access_tier = session.get("access_tier", "-")
            except RuntimeError:
                record.analyst_name = "system"
                record.access_tier = "-"
        else:
            record.analyst_name = "system"
            record.access_tier = "-"
        return True


def setup_access_logger(log_dir: Path) -> None:
    """msi.access logger を access.log に出力するハンドラを登録する。

    既に登録済みなら何もしない (リロード対応)。
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(ACCESS_LOGGER_NAME)
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    logger.propagate = False  # msi ルートに二重出力させない

    formatter = logging.Formatter(
        "%(asctime)s [ACCESS] analyst=%(analyst_name)s tier=%(access_tier)s "
        "%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler = RotatingFileHandler(
        log_dir / "access.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(formatter)
    handler.addFilter(AnalystContextFilter())
    logger.addHandler(handler)


# 常時ポーリングされる内部パス。**成功しているときだけ**除外する。
_NOISY_PREFIXES = ("/_dash-", "/assets/", "/_favicon")
_NOISY_EXACT = ("/healthz", "/healthz/ready", "/metrics")


def should_log_request(path: str, status: int, *, duration_seconds: float | None = None,
                       trace: bool = False) -> bool:
    """このリクエストを access.log に残すか (flask 非依存・純関数)。

    Dash のコールバック (`/_dash-update-component`) は 1 画面で数十回、
    共有ページを開いている間は 60 秒ごとに飛ぶので、全部書くと access.log が
    埋まって監査に使えなくなる。ただし **除外は成功時だけ**にする:

    ★ エラー (4xx/5xx) は内部パスでも必ず残す。「うるさいから消す」で
      異常まで消えるのが、まさに ver52.7 で直している欠陥の形。
    """
    if status >= 400:
        return True
    if path == _CALLBACK_PATH and (
        trace or (duration_seconds is not None and duration_seconds >= _slow_request_seconds())
    ):
        return True
    if path in _NOISY_EXACT:
        return False
    return not path.startswith(_NOISY_PREFIXES)


def log_access(status: int, *, duration_seconds: float | None = None) -> None:
    """after_request で呼び、現在のリクエストをアクセスログに記録する。

    ★ ver52.7: 従来この関数は**どこからも呼ばれていなかった**。docstring は
      「before_request で呼び」と宣言していたが結線が無く、`access.log` は
      作られるのにリクエストが 1 行も入らない状態だった。WSGI サーバ
      (waitress) もアクセスログを持たないため、**アプリ側に HTTP リクエストの
      記録が一切存在しなかった** (ChatGPT 連携の 401 を追えなかった原因)。

    ★ 呼ぶのは `after_request`。`status` は応答が決まるまで分からないので、
      before_request では正しい値を渡せない (旧 docstring はその意味でも誤り)。
    """
    if not has_request_context():
        return
    try:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "-")
        ip = ip.split(",")[0].strip() if ip else "-"
        duration = f"{duration_seconds * 1000:.3f}" if duration_seconds is not None else "-"
        logging.getLogger(ACCESS_LOGGER_NAME).info(
            "path=%s method=%s status=%d ip=%s phase=end request_id=%s callback=%s duration_ms=%s",
            request.path, request.method, status, ip,
            getattr(g, "_msi_http_request_id", "-"),
            getattr(g, "_msi_http_callback", "-"), duration,
        )
    except Exception:
        # アクセスログ失敗で本処理を止めない
        pass
