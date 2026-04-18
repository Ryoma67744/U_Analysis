"""MSI Analysis Application 配布用 ZIP を作成するスクリプト。

使い方:
    cd App && python create_dist_zip.py

プロジェクトルート (UMAP/) に MSI_Analysis_App_v2.1.0.zip を生成します。
ZIP 内には App/ フォルダ一式が含まれ、これを解凍して既存の App/ を
丸ごと差し替えるだけでアプリ更新が完了します。

Data/ 配下のユーザデータ・キャッシュ・ログ等は含まれません。
"""

import os
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

VERSION = "2.1.0"
ZIP_ROOT = "App"  # ZIP 内のルートフォルダ名 (差し替え対象)
OUTPUT_NAME = f"MSI_Analysis_App_v{VERSION}.zip"

# App/ フォルダ = このスクリプトがあるディレクトリ
APP_DIR = Path(__file__).parent
# プロジェクトルート (UMAP/) に ZIP を出力
PROJECT_DIR = APP_DIR.parent

# App/ 配下で ZIP に含めるトップレベルファイル
INCLUDE_FILES = [
    "run_app.py",
    "run_app.bat",
    "setup.bat",
    "install_r_packages.R",
    "pyproject.toml",
    "requirements.txt",
    ".env.example",
    "create_dist_zip.py",
]

# App/ 配下で ZIP に含めるディレクトリ (再帰的に全ファイル)
INCLUDE_DIRS = [
    "app",
    "Script",
    "DB",
    "docs",
    "tests",
]

# 除外パターン（パス内にこの文字列が含まれる場合は除外）
EXCLUDE_PATTERNS = [
    "__pycache__",
    ".pyc",
    # ドキュメントの一時ファイル
    ".tmp.",
    "take_screenshots",
    # OS / エディタ生成ファイル
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
]

# 除外するファイル名（完全一致）
EXCLUDE_FILENAMES = {
    ".Rhistory",
    "app_output.log",
}


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def should_exclude(rel_path: str, filename: str) -> bool:
    """除外すべきファイルかどうかを判定する。"""
    if filename in EXCLUDE_FILENAMES:
        return True
    normalized = rel_path.replace("\\", "/")
    for pattern in EXCLUDE_PATTERNS:
        if pattern in normalized:
            return True
    return False


def collect_files() -> list[tuple[Path, str]]:
    """ZIP に含めるファイルを収集する。

    Returns:
        list of (実パス, ZIP内の相対パス)
    """
    files = []

    # App/ 直下のファイル
    for fname in INCLUDE_FILES:
        fpath = APP_DIR / fname
        if fpath.exists():
            files.append((fpath, f"{ZIP_ROOT}/{fname}"))

    # App/ 配下のディレクトリ
    for dir_rel in INCLUDE_DIRS:
        dir_path = APP_DIR / dir_rel
        if not dir_path.exists():
            continue
        for root, _dirs, filenames in os.walk(dir_path):
            for fname in filenames:
                fpath = Path(root) / fname
                rel = fpath.relative_to(APP_DIR).as_posix()
                if not should_exclude(rel, fname):
                    files.append((fpath, f"{ZIP_ROOT}/{rel}"))

    return files


def create_zip(files: list[tuple[Path, str]]):
    """ZIP ファイルを生成する。"""
    output_path = PROJECT_DIR / OUTPUT_NAME

    print(f"Creating {OUTPUT_NAME} ...")
    print(f"  Files to include: {len(files)}")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath, arcname in files:
            zf.write(fpath, arcname)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  Output: {output_path}")
    print(f"  Size: {size_mb:.1f} MB")
    print("Done!")


def main():
    files = collect_files()
    if not files:
        print("Error: No files found to include.")
        return
    create_zip(files)


if __name__ == "__main__":
    main()
