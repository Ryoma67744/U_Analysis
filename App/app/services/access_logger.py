"""アクセスログ (監査用)。

解析者名 + tier + path を全リクエストに付与する Filter と、
専用ファイル (access.log) への出力ハンドラを提供。
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import has_request_context, request, session

ACCESS_LOGGER_NAME = "msi.access"


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


def should_log_request(path: str, status: int) -> bool:
    """このリクエストを access.log に残すか (flask 非依存・純関数)。

    Dash のコールバック (`/_dash-update-component`) は 1 画面で数十回、
    共有ページを開いている間は 60 秒ごとに飛ぶので、全部書くと access.log が
    埋まって監査に使えなくなる。ただし **除外は成功時だけ**にする:

    ★ エラー (4xx/5xx) は内部パスでも必ず残す。「うるさいから消す」で
      異常まで消えるのが、まさに ver52.7 で直している欠陥の形。
    """
    if status >= 400:
        return True
    if path in _NOISY_EXACT:
        return False
    return not path.startswith(_NOISY_PREFIXES)


def log_access(status: int) -> None:
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
        logging.getLogger(ACCESS_LOGGER_NAME).info(
            "path=%s method=%s status=%d ip=%s",
            request.path, request.method, status, ip,
        )
    except Exception:
        # アクセスログ失敗で本処理を止めない
        pass
