"""ユーザー向けエラーメッセージのサニタイズユーティリティ。

本番環境で内部パスやスタックトレース情報がユーザーに漏れないよう、
generic な日本語メッセージに変換する。logger には full trace を残し、
ユーザー UI には汎用的な原因とアクションのみ表示。

使い方:
    from app.services.error_messages import sanitize_error_for_user
    try:
        do_something()
    except Exception as e:
        msg = sanitize_error_for_user(e, default="解析中にエラーが発生しました")
        # msg は内部パスを含まない安全な文字列
        return msg
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

logger = logging.getLogger("msi.error")

# 内部パスを伏字化するパターン
_PATH_PATTERN = re.compile(
    r"(/[\w\-./]+|[A-Za-z]:\\[\w\-.\\]+)",
    re.UNICODE,
)


def _has_app_paths(s: str) -> bool:
    """文字列に明らかな内部パス (絶対パス / Windows パス) を含むか判定。"""
    if not s:
        return False
    return bool(_PATH_PATTERN.search(s))


def _sanitize_path_string(s: str) -> str:
    """文字列内の絶対パスを <internal-path> プレースホルダーに置換。"""
    if not s:
        return s
    # ファイル名のみ残す: /a/b/c.csv → c.csv
    def _basename_replace(m):
        path = m.group(0)
        try:
            base = os.path.basename(path.replace("\\", "/"))
            if base:
                return base
        except Exception:
            pass
        return "<internal-path>"
    return _PATH_PATTERN.sub(_basename_replace, s)


# エラー型 → ユーザー向けメッセージのマッピング
_ERROR_TYPE_MESSAGES: dict[str, str] = {
    "FileNotFoundError": "必要なファイルが見つかりませんでした。設定を確認してください。",
    "PermissionError": "アクセス権限がないファイルがあります。管理者に連絡してください。",
    "TimeoutError": "処理が時間切れになりました。データサイズを縮小して再試行してください。",
    "MemoryError": "メモリ不足で処理を中断しました。サイズを縮小して再試行してください。",
    "ValueError": "入力データの形式が不正です。",
    "KeyError": "必要な項目が見つかりません。データ形式を確認してください。",
}


def sanitize_error_for_user(
    e: BaseException,
    *,
    default: str = "予期しないエラーが発生しました。",
    include_type: bool = True,
    log_full: bool = True,
) -> str:
    """例外を「ユーザー向け表示用 generic メッセージ」に変換。

    Args:
        e: 例外オブジェクト
        default: マッピングにない例外型のフォールバック
        include_type: True なら "[FileNotFoundError]" 等の prefix を付ける
        log_full: True なら logger.exception で full trace を残す

    Returns:
        内部パスを含まない安全な日本語メッセージ
    """
    type_name = type(e).__name__
    raw_msg = str(e)

    if log_full:
        # 内部に full trace を残す (管理者デバッグ用)
        logger.exception("sanitized error: %s: %s", type_name, raw_msg)

    # 1. マッピングテーブル優先
    template = _ERROR_TYPE_MESSAGES.get(type_name, default)

    # 2. メッセージにパスが含まれていれば basename だけ残す
    if _has_app_paths(raw_msg):
        # ファイル名 (basename) のヒントは残してパス本体は隠す
        sanitized = _sanitize_path_string(raw_msg)
        # メッセージが長すぎる場合は切り詰め
        if len(sanitized) > 200:
            sanitized = sanitized[:200] + "..."
        hint = f"（詳細: {sanitized}）"
    else:
        if raw_msg and len(raw_msg) < 200:
            hint = f"（詳細: {raw_msg}）"
        else:
            hint = ""

    if include_type:
        return f"[{type_name}] {template} {hint}".strip()
    return f"{template} {hint}".strip()


def safe_log_path(path: Optional[str]) -> str:
    """ログ出力用に path の basename だけ取得 (ログ漏洩リスク低減)。

    Returns:
        path が None / 空なら "<unset>"、それ以外は basename。
    """
    if not path:
        return "<unset>"
    try:
        return os.path.basename(str(path).replace("\\", "/")) or "<root>"
    except Exception:
        return "<invalid>"
