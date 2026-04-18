"""MSI Analysis Application 配布用 ZIP を作成するスクリプト。

使い方:
    cd App && python create_dist_zip.py

プロジェクトルート (UMAP-WebApp-ClaudeCode/) に
MSI_Analysis_App_v2.3.0.zip を生成します。

ZIP 構造:
    MSI_Analysis_App_v2.3.0/
        manual.html, SETUP_GUIDE.html, images/   ← ルート直下
        App/                                     ← 差し替え対象
            run_app.sh, setup.sh, ...

解凍後は App/ を既存の App/ に上書きするだけで更新完了です。
Data/ 配下のユーザデータ・キャッシュ・ログ等は含まれません。
"""

import os
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

VERSION = "2.3.0"
ZIP_ROOT = "App"  # ZIP 内のアプリフォルダ名 (差し替え対象)
OUTPUT_NAME = f"MSI_Analysis_App_v{VERSION}.zip"

# App/ フォルダ = このスクリプトがあるディレクトリ
APP_DIR = Path(__file__).parent
# プロジェクトルート (UMAP-WebApp-ClaudeCode/) に ZIP を出力
PROJECT_DIR = APP_DIR.parent

# App/ 配下で ZIP に含めるトップレベルファイル
INCLUDE_FILES = [
    "run_app.py",
    "install_r_packages.R",
    "pyproject.toml",
    "requirements.txt",
    ".env.example",
    "create_dist_zip.py",
]

# 実行ビット (0o755) を付与するファイル (macOS / Linux で展開時に chmod 不要にする)
EXECUTABLE_FILES = {
    "run_app.sh",
    "setup.sh",
}

# App/ 配下で ZIP に含めるディレクトリ (再帰的に全ファイル)
INCLUDE_DIRS = [
    "app",
    "Script",
    "DB",
    "docs",
    "tests",
]

# プロジェクトルート直下で ZIP に含めるファイル (App と同階層に展開)
ROOT_INCLUDE_FILES = [
    "manual.html",
    "SETUP_GUIDE.html",
    "run_app.bat",
    "run_app.sh",
    "setup.bat",
    "setup.sh",
]

# プロジェクトルート直下で ZIP に含めるディレクトリ (App と同階層に展開)
ROOT_INCLUDE_DIRS = [
    "images",
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

    # プロジェクトルート直下のドキュメント類 (App と同階層に配置)
    for fname in ROOT_INCLUDE_FILES:
        fpath = PROJECT_DIR / fname
        if fpath.exists():
            files.append((fpath, fname))

    for dir_rel in ROOT_INCLUDE_DIRS:
        dir_path = PROJECT_DIR / dir_rel
        if not dir_path.exists():
            continue
        for root, _dirs, filenames in os.walk(dir_path):
            for fname in filenames:
                fpath = Path(root) / fname
                rel = fpath.relative_to(PROJECT_DIR).as_posix()
                if not should_exclude(rel, fname):
                    files.append((fpath, rel))

    return files


def _is_executable(arcname: str) -> bool:
    """ZIP 内ファイルが実行ビット (0o755) を持つべきかを判定する。"""
    basename = arcname.rsplit("/", 1)[-1]
    return basename in EXECUTABLE_FILES


def create_zip(files: list[tuple[Path, str]]):
    """ZIP ファイルを生成する。"""
    output_path = PROJECT_DIR / OUTPUT_NAME

    print(f"Creating {OUTPUT_NAME} ...")
    print(f"  Files to include: {len(files)}")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath, arcname in files:
            # .sh 等は実行ビットを立てて書き込む (macOS/Linux 展開時 chmod 不要)
            if _is_executable(arcname):
                zi = zipfile.ZipInfo.from_file(fpath, arcname)
                zi.compress_type = zipfile.ZIP_DEFLATED
                # external_attr の上位 16bit に UNIX モード (regular file | 0o755) を格納
                # 0o100000 = regular file ビット、0o755 = rwxr-xr-x
                zi.external_attr = ((0o100000 | 0o755) << 16) | (zi.external_attr & 0xFFFF)
                with open(fpath, "rb") as fh:
                    zf.writestr(zi, fh.read())
            else:
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
