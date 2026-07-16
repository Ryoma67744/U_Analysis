"""Tests for app.services.molinfo_attach（分子情報の後付け＝サイドカー生成）。

tmp に「分子情報なし」本体 parquet（mz_sorted メタ＋素 m/z 列）と SCiLS Static feature list 風 CSV を
作って、後付けでサイドカーが生成され、既存の annotation_inspect が annotated と認識するまでを検証する。
"""

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services.molinfo_attach import attach_molecular_info, _read_feature_mz
from app.services.annotation_inspect import inspect_annotations, has_compound_names
from app.services.data_manager import build_tims_input_paths
from app.services.peak_annotation import _TABLE_COLUMNS


# 3 つは命名済み、1 つ（500.0）は peak-list に無い
_MZ = [86.0956, 104.1059, 184.0713, 500.0]

_CSV = """# Exported with SCiLS Lab Version 14.01
# Object type: Static feature list
#
m/z;Interval Width (+/- Da);Color;Name;Intensity [Regions]
184.0713;0.00184;#ff7f00;Phosphocholine | methyl_donor | CE_MS_Common_Soga2003 | [M]+ | 0.85ppm | annotation_tol=10ppm | mz_window=10ppm | formula=C5H15NO4P | SMILES=NA;151673.3
104.1059;0.00104;#ffd700;Choline | HMDB | [M+H]+ | 0.34ppm | formula=C5H14NO | SMILES=NA;63614.2
86.0956;0.00086;#a6cee3;1-Methylpyrrolidine | HMDB | [M+H]+ | 0.54ppm | formula=C5H11N | SMILES=CN1CCCC1;85937.4
"""


def _write_main_parquet(folder, with_metadata=True, embed=False):
    """「分子情報なし」本体 parquet を書く（id/x/y + 素 m/z 列 + annotation）。"""
    if embed:
        cols = [f"cpd_{v:.4f}" for v in _MZ]        # 埋め込み風（末尾に _<m/z>）
    else:
        cols = [f"{v:.6f}" for v in _MZ]            # 素の数値列名
    md = {b"mz_sorted": ",".join(f"{v:.10g}" for v in _MZ).encode("utf-8")} if with_metadata else None
    schema = pa.schema(
        [("id", pa.int64()), ("x", pa.float64()), ("y", pa.float64())]
        + [(c, pa.float64()) for c in cols]
        + [("annotation", pa.string())],
        metadata=md,
    )
    arrays = [pa.array([1, 2]), pa.array([0.0, 1.0]), pa.array([0.0, 1.0])]
    arrays += [pa.array([0.1, 0.2]) for _ in cols]
    arrays.append(pa.array(["A", "A"]))
    pq.write_table(pa.Table.from_arrays(arrays, schema=schema), str(folder / "SAMPLE.parquet"))


def _write_csv(folder, text=_CSV):
    p = folder / "peaklist.csv"
    p.write_text(text, encoding="utf-8")
    return p


def _sub(folder):
    return {"data_folder": str(folder), "ms_instrument": "TIMS", "id": "s1"}


class TestAttach:
    def test_creates_sidecar_and_counts(self, tmp_path):
        _write_main_parquet(tmp_path)
        csv = _write_csv(tmp_path)
        r = attach_molecular_info(_sub(tmp_path), csv)
        assert r["status"] == "ok"
        assert r["n_features"] == 4
        assert r["n_peaklist"] == 3
        assert r["n_matched"] == 3
        sidecar = tmp_path / "SAMPLE_feature_annotations.parquet"
        assert sidecar.exists()
        side = pd.read_parquet(sidecar)
        assert list(side.columns) == _TABLE_COLUMNS
        assert len(side) == 4                        # 1 feature 1 行
        comps = set(side.loc[side["compound"] != "", "compound"])
        assert {"Phosphocholine", "Choline", "1-Methylpyrrolidine"} <= comps

    def test_inspect_sees_annotated_after_attach(self, tmp_path):
        _write_main_parquet(tmp_path)
        csv = _write_csv(tmp_path)
        sub = _sub(tmp_path)
        assert inspect_annotations(sub)["status"] == "none"   # 付与前
        attach_molecular_info(sub, csv)
        r = inspect_annotations(sub)                           # 付与後（mtime でキャッシュ無効化）
        assert r["status"] == "annotated"
        assert r["n_annotated"] == 3
        assert has_compound_names(sub) is True

    def test_dry_run_writes_nothing(self, tmp_path):
        _write_main_parquet(tmp_path)
        csv = _write_csv(tmp_path)
        r = attach_molecular_info(_sub(tmp_path), csv, dry_run=True)
        assert r["status"] == "preview"
        assert r["n_matched"] == 3
        assert not (tmp_path / "SAMPLE_feature_annotations.parquet").exists()

    def test_tol_boundary_no_match(self, tmp_path):
        _write_main_parquet(tmp_path)
        # 全ピークを 0.03 Da ずらす → 既定 tol=0.01 では 1 件もマッチしない
        shifted = _CSV.replace("184.0713;", "184.1013;").replace(
            "104.1059;", "104.1359;").replace("86.0956;", "86.1256;")
        csv = _write_csv(tmp_path, shifted)
        r = attach_molecular_info(_sub(tmp_path), csv)
        assert r["n_matched"] == 0

    def test_feature_mz_fallback_without_metadata(self, tmp_path):
        # mz_sorted メタが無くても列名から m/z を復元できる
        _write_main_parquet(tmp_path, with_metadata=False)
        mz = _read_feature_mz(str(tmp_path))
        assert sorted(round(v, 4) for v in mz) == sorted(_MZ)

    def test_sidecar_excluded_from_tims_inputs(self, tmp_path):
        _write_main_parquet(tmp_path)
        attach_molecular_info(_sub(tmp_path), _write_csv(tmp_path))
        # サイドカーはサンプル入力として拾われない
        inputs = [p for p in build_tims_input_paths(str(tmp_path))]
        assert any(p.endswith("SAMPLE.parquet") for p in inputs)
        assert not any(p.endswith("_feature_annotations.parquet") for p in inputs)
