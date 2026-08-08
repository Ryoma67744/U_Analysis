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



def categorize_image(image_path: str) -> str:
    """画像カテゴリを判定"""
    filename = Path(image_path).name.lower()

    # ★ ver51.9 / C-7: 判定順が誤っていた。
    #     - "cluster" が "heatmap" より先だったので `cluster_heatmap.png` が
    #       Spatial に入る
    #     - "msi" が "cluster" より先だったので `Cluster_3_MSI.png`
    #       (クラスタ別の Spatial 図) が MSI に入る
    #   より具体的な語を先に見る。`Cluster_Top5_MSI_*` は "top5" で MSI に残す。
    if "umap" in filename:
        return "UMAP"
    if "volcano" in filename:
        return "Volcano"
    if "heatmap" in filename:
        return "Heatmap"
    if "top5" in filename:
        return "MSI"
    if "spatial" in filename or "cluster" in filename:
        return "Spatial"
    if "msi" in filename:
        return "MSI"
    if "tic" in filename:
        return "TIC"
    if "filter" in filename:
        return "Filtering"
    return "Other"



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
