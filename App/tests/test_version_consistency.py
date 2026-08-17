"""バージョン採番の番人（ver56.9）。

ver56.8 のリリース時、PR #154 と PR #155 が同じ main（ver56.3）から分岐して
**両方が独立に ver56.4 を名乗る**という衝突が起きた。#155 を先にマージしたため
main は ver56.8 になり、#154（APP_VERSION = 56.7）は「マージするとバージョンが
後退する」状態のまま残った。

これは初めてではない。過去にも同じ番号が別の変更に割り当てられている:

    ver38.1  main: 強度/発現の下流を Spatial から読む
             ブランチ: MetaboAnalyst UI 明記 (PR #103)
    ver43.0  main: 化合物名表示を全表示面に統一
             ブランチ: ChatGPT view_url 追加 (PR #113)

原因は、複数のセッションが並行して main から分岐し、それぞれ**作業開始時点の
main のバージョン**を見て採番していること。規約は CHANGELOG.md 冒頭と
App/app/version.py の docstring に文章で書かれているだけで、機械的な検査が無かった。

本ファイルはマージ**後**の不整合を検出する。他 PR との衝突をマージ**前**に
捕まえるのは 1 リポジトリ内の pytest では原理的に不可能なので、そちらは
.github/workflows/version-guard.yml が base ブランチと比較して担当する。

app.version を import せずファイルを正規表現で読むのは、重い依存
(numpy / pandas / dash …) を持ち込まずに CI の軽量ジョブで動かすため。
"""

import re
from pathlib import Path

import pytest

# App/tests/test_version_consistency.py → リポジトリルート
_REPO_ROOT = Path(__file__).resolve().parents[2]
_VERSION_PY = _REPO_ROOT / "App" / "app" / "version.py"
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"

# CHANGELOG の見出しは全 197 件がこの形式で統一されている（ver56.9 時点で確認）
_HEADING_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})_ver(\d+\.\d+)$", re.MULTILINE)


def _version_key(v: str) -> tuple[int, ...]:
    """'56.10' > '56.9' となるよう数値タプルで比較する（文字列比較では逆になる）。"""
    return tuple(int(x) for x in v.split("."))


def _read_app_version() -> tuple[str, str]:
    """version.py から (APP_VERSION, RELEASE_DATE) を読む。"""
    text = _VERSION_PY.read_text(encoding="utf-8")
    version = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    date = re.search(r'^RELEASE_DATE\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert version and date, f"{_VERSION_PY} から APP_VERSION / RELEASE_DATE を読めません"
    return version.group(1), date.group(1)


def _read_changelog_headings() -> list[tuple[str, str]]:
    """CHANGELOG の見出しを上（新しい）から順に [(日付, バージョン), ...] で返す。"""
    return _HEADING_RE.findall(_CHANGELOG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def headings() -> list[tuple[str, str]]:
    heads = _read_changelog_headings()
    assert heads, "CHANGELOG.md からバージョン見出しを 1 件も抽出できません（書式が変わった可能性）"
    return heads


def test_app_version_matches_latest_changelog_entry(headings):
    """APP_VERSION / RELEASE_DATE が CHANGELOG 先頭の見出しと一致する。

    ★ これが ver56.8 の事故を捕まえる不変条件。番号の古い PR を後からマージすると
    「APP_VERSION は 56.7 なのに CHANGELOG 先頭は 56.8」というズレが必ず生じ、
    画面右上の表示とアプリの実体が食い違う。
    """
    app_version, release_date = _read_app_version()
    latest_date, latest_version = headings[0]
    assert (app_version, release_date) == (latest_version, latest_date), (
        f"version.py が {release_date}_ver{app_version} なのに "
        f"CHANGELOG 先頭は {latest_date}_ver{latest_version} です。\n"
        "他の PR が先にマージされた可能性があります。main を取り込んだうえで "
        "version.py・CHANGELOG.md・コミットタイトルの 3 点を採り直してください。"
    )


def test_no_duplicate_versions(headings):
    """同じバージョン番号の見出しが 2 つ以上ない。

    ver38.1 / ver43.0 は実際に「別の変更が同じ番号を名乗る」状態になっている
    （main と未マージブランチの間）。それらの PR をそのままマージすると
    CHANGELOG 内で重複するので、ここで止める。
    """
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for date, version in headings:
        if version in seen:
            duplicates.append(f"ver{version}（{seen[version]} と {date}）")
        else:
            seen[version] = date
    assert not duplicates, (
        "CHANGELOG に重複したバージョン番号があります: " + " / ".join(duplicates) + "\n"
        "並行して開発された変更が同じ番号を名乗っています。後発側を採り直してください。"
    )


def test_versions_are_strictly_descending(headings):
    """見出しがバージョンの降順に並ぶ（新しいものが上）。

    番号の後退や、古い番号のエントリを先頭に差し込む事故を検出する。
    """
    violations = [
        f"{headings[i][0]}_ver{headings[i][1]} の下に "
        f"{headings[i + 1][0]}_ver{headings[i + 1][1]}"
        for i in range(len(headings) - 1)
        if _version_key(headings[i][1]) <= _version_key(headings[i + 1][1])
    ]
    assert not violations, (
        "CHANGELOG の見出しがバージョン降順になっていません:\n  "
        + "\n  ".join(violations)
    )
