# =============================================================================
# MSI Analysis Application - Notification Utility
# 警告・エラー通知ヘルパー
# =============================================================================

import logging

logger = logging.getLogger("msi.notify")


def warn_user(msg: str):
    """警告をログに出力し、可能ならトースト通知も表示する。

    コールバック内で呼ぶとアプリ画面右上にトースト通知が表示される。
    サービス関数（コールバック外）から呼んだ場合はログ出力のみ。
    """
    logger.warning(msg)
    try:
        from dash import set_props
        set_props("notification_toast", {
            "children": msg,
            "is_open": True,
            "header": "警告",
            "icon": "warning",
        })
    except Exception:
        pass  # コールバック外 or set_props非対応ではログのみ
