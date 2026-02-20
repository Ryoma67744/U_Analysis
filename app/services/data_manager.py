# =============================================================================
# MSI Analysis Application - Data Manager
# データ管理モジュール
# =============================================================================

from pathlib import Path
from typing import Optional

import pandas as pd


def list_msi_files(data_folder: str) -> list[str]:
    """データフォルダ内のMSIファイル一覧を取得（拡張子なし）"""
    folder = Path(data_folder)
    if not folder.is_dir():
        return []
    return sorted([f.stem for f in folder.glob("*.txt")])


def list_tims_files(data_folder: str) -> list[str]:
    """データフォルダ内のTIMSファイル一覧を取得（拡張子なし）
    対応形式: .parquet, .pq, .csv, .tsv, .txt
    """
    folder = Path(data_folder)
    if not folder.is_dir():
        return []
    extensions = {".parquet", ".pq", ".csv", ".tsv", ".txt"}
    files = [f.stem for f in folder.iterdir() if f.suffix.lower() in extensions]
    return sorted(files)


def build_tims_input_paths(data_folder: str) -> list[str]:
    """データフォルダ内のTIMSファイルのフルパスリストを返す。
    Rスクリプトの INPUT_PATHS / ORIGINAL_INPUT_PATHS に対応。
    """
    folder = Path(data_folder)
    if not folder.is_dir():
        return []
    extensions = {".parquet", ".pq", ".csv", ".tsv", ".txt"}
    files = [str(f) for f in folder.iterdir() if f.suffix.lower() in extensions]
    return sorted(files)


def validate_msi_file(file_path: str) -> dict:
    """MSIファイルの妥当性チェック"""
    path = Path(file_path)
    if not path.exists():
        return {"valid": False, "message": "ファイルが見つかりません"}

    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [f.readline() for _ in range(5)]

        if len([l for l in lines if l]) < 5:
            return {"valid": False, "message": "ファイルの行数が不足しています (最低5行必要)"}

        # 5行目がタブ区切りデータかチェック
        data_fields = lines[4].strip().split("\t")
        if len(data_fields) < 4:
            return {"valid": False, "message": "データ形式が正しくありません"}

        return {"valid": True, "message": "OK"}
    except Exception as e:
        return {"valid": False, "message": f"読み込みエラー: {e}"}


def load_mrm_file(mrm_path: str) -> Optional[pd.DataFrame]:
    """MRMファイルの読み込み (xlsx/csv)"""
    if not mrm_path or not Path(mrm_path).exists():
        return None

    ext = Path(mrm_path).suffix.lower()

    try:
        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(mrm_path)
        else:
            df = pd.read_csv(mrm_path)

        if df.empty:
            return None
        return df
    except Exception as e:
        print(f"MRMファイル読み込みエラー: {e}")
        return None


def list_result_folders(base_dir: str) -> list[dict]:
    """出力結果フォルダの一覧を取得"""
    base = Path(base_dir)
    if not base.is_dir():
        return []

    results = []
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue

        # RDS_Filesサブフォルダがあれば解析結果フォルダと判定
        has_rds = (d / "RDS_Files").is_dir()
        has_outputs = any(d.glob("*.png")) or any(d.glob("*.csv"))

        if has_rds or has_outputs:
            import re
            date_match = re.search(r"\d{8}", d.name)
            if date_match:
                ds = date_match.group()
                date_str = f"{ds[:4]}/{ds[4:6]}/{ds[6:8]}"
            else:
                date_str = "Unknown"

            results.append({
                "name": d.name,
                "path": str(d),
                "date": date_str,
            })

    return results


def list_result_images(result_dir: str, subfolder: str = None) -> list[str]:
    """結果フォルダ内の画像ファイル一覧を取得"""
    target = Path(result_dir)
    if subfolder:
        target = target / subfolder

    if not target.is_dir():
        return []

    extensions = {".png", ".jpg", ".jpeg"}
    images = [
        str(f)
        for f in target.rglob("*")
        if f.suffix.lower() in extensions
    ]
    return sorted(images)
