# =============================================================================
# MSI Analysis Application - Results Viewer
# 結果可視化モジュール
# =============================================================================

import re
from pathlib import Path
from typing import Optional

# 結果フォルダの主要サブディレクトリ
KEY_SUBDIRS = [
    "Harmony", "RPCA", "PCA",
    "Volcano_Plots", "Volcano_Plots_MRM",
    "Cluster_Top5_MSI", "PerCluster_Highlight",
    "RDS_Files",
]

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def get_result_structure(result_dir: str) -> dict:
    """結果フォルダの構造を取得"""
    root = Path(result_dir)
    if not root.is_dir():
        return {"root": str(root), "subdirs": {}, "root_images": []}

    subdirs = {}
    for subdir_name in KEY_SUBDIRS:
        subdir_path = root / subdir_name
        if subdir_path.is_dir():
            images = [
                str(f) for f in subdir_path.rglob("*")
                if f.suffix.lower() in IMAGE_EXTENSIONS
            ]
            subdirs[subdir_name] = {
                "path": str(subdir_path),
                "image_count": len(images),
                "images": images,
            }

    root_images = [
        str(f) for f in root.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    ]

    return {
        "root": str(root),
        "subdirs": subdirs,
        "root_images": root_images,
    }


def categorize_image(image_path: str) -> str:
    """画像カテゴリを判定"""
    filename = Path(image_path).name.lower()

    if "umap" in filename:
        return "UMAP"
    if "volcano" in filename:
        return "Volcano"
    if "msi" in filename or "top5" in filename:
        return "MSI"
    if "spatial" in filename or "cluster" in filename:
        return "Spatial"
    if "heatmap" in filename:
        return "Heatmap"
    if "tic" in filename:
        return "TIC"
    if "filter" in filename:
        return "Filtering"
    return "Other"


def organize_images_by_category(images: list[str]) -> dict[str, list[str]]:
    """画像一覧をカテゴリ別に整理"""
    if not images:
        return {}

    result: dict[str, list[str]] = {}
    for img in images:
        cat = categorize_image(img)
        result.setdefault(cat, []).append(img)
    return result


def extract_cluster_number(image_path: str) -> Optional[int]:
    """クラスタ番号を画像パスから抽出"""
    filename = Path(image_path).name

    # パターン: Cluster_0, Cluster_10 など（大文字小文字不問）
    match = re.search(r"[Cc]luster_(\d+)", filename)
    if match:
        return int(match.group(1))
    return None


def get_available_clusters(result_dir: str) -> list[int]:
    """結果フォルダ内の利用可能なクラスタ番号を取得"""
    root = Path(result_dir)
    if not root.is_dir():
        return []

    clusters = set()
    for f in root.rglob("*"):
        if f.suffix.lower() in IMAGE_EXTENSIONS:
            num = extract_cluster_number(str(f))
            if num is not None:
                clusters.add(num)
    return sorted(clusters)


def extract_sample_name(image_path: str) -> Optional[str]:
    """サンプル名を画像パスから抽出"""
    filename = Path(image_path).stem
    parts = filename.split("_")

    # 日付パターン（6桁の数字で始まる）を探す
    for i, part in enumerate(parts):
        if re.match(r"^\d{6}", part):
            return "_".join(parts[i:])
    return None


def filter_images_by_cluster(
    images: list[str], cluster_num: Optional[int]
) -> list[str]:
    """特定クラスタの画像をフィルタ"""
    if cluster_num is None:
        return images
    return [
        img for img in images
        if extract_cluster_number(img) == cluster_num
    ]


def create_gallery_data(
    images: list[str], max_per_page: int = 20
) -> dict:
    """画像ギャラリーデータを生成"""
    if not images:
        return {"images": [], "total": 0, "pages": 0, "per_page": max_per_page}

    import math
    return {
        "images": images,
        "total": len(images),
        "pages": math.ceil(len(images) / max_per_page),
        "per_page": max_per_page,
    }


def sort_images_by_time(images: list[str]) -> list[str]:
    """画像を更新日時順にソート（新しい順）"""
    if not images:
        return images

    def get_mtime(img_path: str) -> float:
        try:
            return Path(img_path).stat().st_mtime
        except OSError:
            return 0.0

    return sorted(images, key=get_mtime, reverse=True)
