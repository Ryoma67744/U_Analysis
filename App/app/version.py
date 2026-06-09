"""Application version string.

修正をリリースするたびに APP_VERSION を更新する。
- バグ修正のみ: パッチ番号を +0.1 (例: 1.0 → 1.1)
- 機能追加: メジャー番号を +1.0 (例: 1.5 → 2.0)
- 表示は `<日付>_ver<番号>` 形式で簡易ビューアー右上に出る。
- 更新時は CHANGELOG.md とコミットメッセージ末尾 `[verX.Y]` も同期する。
"""

APP_VERSION = "4.26"
RELEASE_DATE = "2026-06-06"


def version_label() -> str:
    """画面表示用のラベル文字列を返す。例: '2026-05-22_ver1.0'"""
    return f"{RELEASE_DATE}_ver{APP_VERSION}"
