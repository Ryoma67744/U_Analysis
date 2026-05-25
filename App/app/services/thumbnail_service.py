# =============================================================================
# MSI Analysis Application - Thumbnail Service
# プロジェクトサムネの生成 + ディスクキャッシュ
#
# 役割:
#   - プロジェクト一覧画面のカードに表示するサムネ画像を、source 画像から
#     60x60 JPG にリサイズして cache する
#   - Flask route (/api/project_thumb/<project_id>) から呼ばれて即時返却
#
# キャッシュ戦略:
#   - キャッシュキー: project_id + source 画像の mtime (epoch sec)
#   - ファイル名: {project_id}_{mtime}.jpg
#   - source が更新されると mtime が変わり、新規キャッシュ生成 + 旧 cache 削除
# =============================================================================

import logging
from pathlib import Path
from typing import Optional

from app.config import OTHER_DIR

logger = logging.getLogger(__name__)

THUMB_CACHE_DIR = OTHER_DIR / "cache" / "thumbnails"
# ver3.14: 150x150 表示 + DPR=2 で sharp に見えるよう 300x300 までキャッシュ
THUMB_SIZE = (300, 300)
# ver3.14: 横長 (R の UMAP_per_sample_..._ALLclusters.png のような複数切片
# 連結画像) は最左端の正方形領域だけ切り出してサムネ化する。
# aspect (width / height) がこの値を超えると wide とみなす。
_WIDE_ASPECT_RATIO = 1.4

# 自動検出時の候補パス (relative to result_dir)
_AUTO_CANDIDATES = (
    ("Harmony", "UMAP_per_sample_harmony_ALLclusters.png"),
    ("RPCA", "UMAP_per_sample_rpca_ALLclusters.png"),
    ("PCA", "UMAP_per_sample_pca_ALLclusters.png"),
)


def resolve_thumbnail_source(project: dict) -> Optional[str]:
    """thumbnail_source が指定されていればそれを、無ければ自動検出する。

    自動検出ルール:
      1. 最新 (last_modified 降順) sub_project の last_result_dir を起点
      2. Harmony / RPCA / PCA の UMAP png を順に探索
      3. それでも見つからなければ rglob で *UMAP*.png → *spatial*.png を 1 枚
    """
    explicit = (project or {}).get("thumbnail_source") or ""
    if explicit:
        p = Path(explicit)
        if p.exists() and p.is_file():
            return str(p)
        # 指定はあるがファイルが存在しない → 自動検出にフォールバック
        logger.debug("thumbnail_source not found, fallback to auto: %s", explicit)

    subs = list((project or {}).get("sub_projects", []) or [])
    subs.sort(key=lambda s: s.get("last_modified", ""), reverse=True)
    for sub in subs:
        result_dir = sub.get("last_result_dir") or sub.get("output_dir", "")
        if not result_dir:
            continue
        root = Path(result_dir)
        if not root.is_dir():
            continue
        for subdir, filename in _AUTO_CANDIDATES:
            cand = root / subdir / filename
            if cand.exists():
                return str(cand)
        # rglob fallback
        try:
            for f in root.rglob("*UMAP*.png"):
                return str(f)
            for f in root.rglob("*spatial*.png"):
                return str(f)
        except OSError:
            continue
    return None


def get_thumbnail_path(project_id: str, source_path: str) -> Optional[Path]:
    """source 画像のサムネを生成 (キャッシュあり)、cache ファイルパスを返す。

    cache hit なら Pillow 呼出しを skip して即時 return。
    miss なら Pillow で resize → JPG 保存。失敗時は None。
    """
    if not source_path:
        return None
    src = Path(source_path)
    if not src.exists() or not src.is_file():
        return None
    try:
        src_mtime = int(src.stat().st_mtime)
    except OSError:
        return None

    # キャッシュキー: project_id + mtime (整数秒) + THUMB_SIZE
    # ver3.11: ファイル名に解像度を含め、THUMB_SIZE 変更で自動再生成
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_"
                      for c in (project_id or ""))
    size_tag = f"{THUMB_SIZE[0]}x{THUMB_SIZE[1]}"
    cache_name = f"{safe_id}_{src_mtime}_{size_tag}.jpg"
    cache_path = THUMB_CACHE_DIR / cache_name
    if cache_path.exists():
        return cache_path

    # cache miss: 同 project_id の古い cache (旧 mtime / 旧 size 含む) を掃除
    THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for old in THUMB_CACHE_DIR.glob(f"{safe_id}_*.jpg"):
        try:
            old.unlink()
        except OSError:
            pass

    try:
        from PIL import Image
        img = Image.open(src)

        # ver3.14: 横長画像は最左端の正方形だけ切り出す
        # (R の UMAP_per_sample_..._ALLclusters.png のように複数切片を
        # 横一列に連結した画像 → 1 枚目だけのサムネにする)
        w, h = img.size
        cropped = False
        if h > 0 and w / h > _WIDE_ASPECT_RATIO:
            crop_size = h
            img = img.crop((0, 0, crop_size, crop_size))
            cropped = True

        img.thumbnail(THUMB_SIZE, Image.LANCZOS)
        # PNG の alpha channel を白背景に合成して JPG 保存可能に
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            img_rgba = img.convert("RGBA")
            bg.paste(img_rgba, mask=img_rgba.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
        img.save(cache_path, "JPEG", quality=80, optimize=True)
        logger.info(
            "thumbnail generated: project=%s src=%s -> %s (cropped=%s, src_size=%dx%d)",
            project_id, src.name, cache_path.name, cropped, w, h,
        )
        return cache_path
    except Exception as e:
        logger.warning("thumbnail generation failed (project=%s, src=%s): %s",
                       project_id, source_path, e)
        return None
