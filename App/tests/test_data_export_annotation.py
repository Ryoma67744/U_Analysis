"""_apply_feature_annotation_columns（ver45.0）: エクスポート時のサイドカー列名変換。

素 m/z 列 → 埋め込み名（化合物名_<m/z> | …）へのリネーム、非特徴量列の保持、
サイドカー無しでの無変換、冪等性を検証する（本体 parquet は書き換えない）。
"""

import pandas as pd

from app.callbacks.interactive_data_export import _apply_feature_annotation_columns


def _write_sidecar(folder):
    side = pd.DataFrame({
        "mz": [104.1059, 184.0713],
        "raw": [
            "Choline | HMDB | [M+H]+ | 0.34ppm | formula=C5H14NO",
            "Phosphocholine | CE_MS | [M]+ | 0.85ppm | formula=C5H15NO4P",
        ],
    })
    side.to_parquet(str(folder / "SAMPLE_feature_annotations.parquet"), index=False)


def _df():
    return pd.DataFrame({
        "id": [1, 2], "x": [0.0, 1.0], "y": [0.0, 1.0],
        "184.071300": [0.1, 0.2],
        "104.105900": [0.3, 0.4],
        "annotation": ["A", "A"],
        "UMAP cluster": [1, 2],
    })


def test_renames_mz_columns_to_embedded(tmp_path):
    _write_sidecar(tmp_path)
    out = _apply_feature_annotation_columns(_df(), str(tmp_path))
    cols = list(out.columns)
    # m/z 列が化合物名付きの埋め込み名に変わる
    assert any(c.startswith("Phosphocholine_184.0713 |") for c in cols)
    assert any(c.startswith("Choline_104.1059 |") for c in cols)
    # 非特徴量列は不変
    for keep in ("id", "x", "y", "annotation", "UMAP cluster"):
        assert keep in cols


def test_no_sidecar_is_noop(tmp_path):
    before = _df()
    out = _apply_feature_annotation_columns(before.copy(), str(tmp_path))
    assert list(out.columns) == list(before.columns)


def test_idempotent_on_already_embedded(tmp_path):
    _write_sidecar(tmp_path)
    once = _apply_feature_annotation_columns(_df(), str(tmp_path))
    twice = _apply_feature_annotation_columns(once.copy(), str(tmp_path))
    assert list(once.columns) == list(twice.columns)
