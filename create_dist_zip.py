"""MSI Analysis Application 配布用 ZIP を作成するスクリプト。

使い方:
    python create_dist_zip.py

プロジェクトルートに MSI_Analysis_App_v2.0.0.zip を生成します。
ユーザーデータ・キャッシュ・開発ファイルは除外されます。
"""

import os
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

VERSION = "2.1.0"
ZIP_ROOT = "MSI_Analysis_App"  # ZIP 内のルートフォルダ名
OUTPUT_NAME = f"MSI_Analysis_App_v{VERSION}.zip"

PROJECT_DIR = Path(__file__).parent

# 含めるトップレベルファイル
INCLUDE_FILES = [
    "run_app.py",
    "run_app.bat",
    "setup.bat",
    "install_r_packages.R",
    "pyproject.toml",
    "requirements.txt",
    ".env.example",
]

# 含めるディレクトリ（再帰的に全ファイルを含む）
INCLUDE_DIRS = [
    "app",
    "data/TIMS/Script",
    "data/TIMS/DB",
    "data/DESI/Script",
    "data/DESI/DB",
    "data/Common/Script",
    "data/Common_Script",
    "docs",
    "tests",
]

# 除外パターン（パス内にこの文字列が含まれる場合は除外）
EXCLUDE_PATTERNS = [
    "__pycache__",
    ".pyc",
    # ユーザー固有データ
    "app/sessions",
    "app/shares",
    "app/projects/backups",
    "app/projects/projects.json",
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
    # ファイル名の完全一致チェック
    if filename in EXCLUDE_FILENAMES:
        return True
    # パスパターンチェック
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

    # トップレベルファイル
    for fname in INCLUDE_FILES:
        fpath = PROJECT_DIR / fname
        if fpath.exists():
            files.append((fpath, f"{ZIP_ROOT}/{fname}"))

    # ディレクトリ
    for dir_rel in INCLUDE_DIRS:
        dir_path = PROJECT_DIR / dir_rel
        if not dir_path.exists():
            continue
        for root, _dirs, filenames in os.walk(dir_path):
            for fname in filenames:
                fpath = Path(root) / fname
                rel = fpath.relative_to(PROJECT_DIR).as_posix()
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

        # 空フォルダ用 .gitkeep
        zf.writestr(f"{ZIP_ROOT}/data/TIMS/Data/.gitkeep", "")
        zf.writestr(f"{ZIP_ROOT}/data/DESI/Data/.gitkeep", "")
        zf.writestr(f"{ZIP_ROOT}/app/sessions/.gitkeep", "")
        zf.writestr(f"{ZIP_ROOT}/app/shares/.gitkeep", "")
        zf.writestr(f"{ZIP_ROOT}/app/presets/.gitkeep", "")

        # 空の projects.json（初期状態）
        zf.writestr(f"{ZIP_ROOT}/app/projects/projects.json",
                     '{\n  "projects": []\n}')

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
