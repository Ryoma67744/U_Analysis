#!/usr/bin/env python3
"""PR の APP_VERSION が base ブランチより大きいことを検査する（ver56.9 で追加）。

ver56.8 のリリース時、PR #154 と PR #155 が同じ main（ver56.3）から分岐して
**両方が独立に ver56.4 を名乗る**衝突が起きた。#155 が先にマージされて main が
ver56.8 になった結果、#154（APP_VERSION = 56.7）は「マージするとバージョンが
56.8 → 56.7 に後退する」状態になった。

この種の衝突は 1 つのブランチの中身を見ても分からない（#154 単体では
version.py と CHANGELOG は整合している）。**base と比較して初めて**検出できるため、
リポジトリ内の pytest ではなく CI がこの役割を持つ。

ローカルでも実行できるようワークフローから分離してある:

    python3 .github/scripts/check_version_bump.py <base の version.py> <head の version.py>

終了コード 0 = OK / 1 = 番号が後退または同値。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_VERSION_RE = re.compile(r'^APP_VERSION\s*=\s*"([^"]+)"', re.MULTILINE)


def read_version(path: Path) -> str:
    """version.py 相当のファイルから APP_VERSION を読む。"""
    m = _VERSION_RE.search(path.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f"::error::{path} から APP_VERSION を読み取れませんでした")
    return m.group(1)


def version_key(v: str) -> tuple[int, ...]:
    """'56.10' > '56.9' となるよう数値タプルで比較する（文字列比較では逆になる）。"""
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        sys.exit(f"::error::APP_VERSION '{v}' が数値形式ではありません")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.exit(f"usage: {argv[0]} <base version.py> <head version.py>")

    base_v = read_version(Path(argv[1]))
    head_v = read_version(Path(argv[2]))

    if version_key(head_v) > version_key(base_v):
        print(f"OK: APP_VERSION {base_v} (base) → {head_v} (この PR)")
        return 0

    # GitHub Actions のアノテーション形式。PR の Files タブに赤く出る。
    print(
        f"::error::APP_VERSION が更新されていません（base: {base_v} / この PR: {head_v}）。%0A"
        f"base より大きい番号が必要です。%0A"
        f"他の PR が先にマージされて base が進んだ場合に起こります。"
        f"main を取り込んだうえで、version.py・CHANGELOG.md・"
        f"コミットタイトル末尾の [verX.Y] の 3 点を採り直してください。%0A"
        f"22 コミット級の大きい PR では rebase ではなく main を merge して解決すること"
        f"（force-push で他セッションの履歴を壊さないため）。"
    )
    print(f"\nNG: base = {base_v} / この PR = {head_v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
