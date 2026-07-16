"""seurat_bridge._load_feature_annotations のキャッシュ自己修復（ver45.0）。

後付け/更新されたサイドカーが `cache_dir/feature_annotations.json` の古いキャッシュに阻まれず
反映されること（サイドカーがキャッシュより新しければ作り直す）を検証する。
"""

import os

import pandas as pd

from app.services.seurat_bridge import SeuratBridge


def _write_sidecar(folder):
    df = pd.DataFrame({
        "mz": [184.0713],
        "compound": ["Phosphocholine"],
        "lipid_class": [None],
        "database": ["CE_MS"],
        "adduct": ["[M]+"],
        "ppm": [0.85],
        "formula": ["C5H15NO4P"],
        "smiles": [None],
        "adduct_image": [None],
        "adduct_family": [None],
        "raw": ["Phosphocholine | ..."],
        "display_name": ["Phosphocholine_184.0713"],
    })
    p = folder / "SAMPLE_feature_annotations.parquet"
    df.to_parquet(str(p), index=False)
    return p


def test_cache_rebuilds_when_sidecar_is_newer(tmp_path):
    b = SeuratBridge()
    rds_dir = tmp_path / "result"
    rds_dir.mkdir()
    sidecar = _write_sidecar(rds_dir)
    rds_path = str(rds_dir / "data.rds")     # 実在不要（parent 探索のみ）
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    features_list = ["184.0713"]
    cache_file = cache_dir / "feature_annotations.json"

    # 1) キャッシュ無し → サイドカーから構築し、キャッシュを書く
    out = b._load_feature_annotations(cache_dir, rds_path, features_list)
    assert out.get("184.0713", {}).get("compound") == "Phosphocholine"
    assert cache_file.exists()

    st = sidecar.stat()

    # 2) 空の古いキャッシュ（サイドカーより古い）→ 無視して作り直す
    cache_file.write_text("{}", encoding="utf-8")
    os.utime(cache_file, ns=(st.st_mtime_ns - 10**9, st.st_mtime_ns - 10**9))
    out2 = b._load_feature_annotations(cache_dir, rds_path, features_list)
    assert out2.get("184.0713", {}).get("compound") == "Phosphocholine"

    # 3) 新しいキャッシュ（サイドカーより新しい）→ そのまま再利用（空のまま）
    cache_file.write_text("{}", encoding="utf-8")
    os.utime(cache_file, ns=(st.st_mtime_ns + 10**9, st.st_mtime_ns + 10**9))
    out3 = b._load_feature_annotations(cache_dir, rds_path, features_list)
    assert out3 == {}
