"""Tests for app.services.annotation_inspect (生データを開かずに化合物名有無を要約)。

Dash 非依存。tmp にサイドカー parquet / フッタメタ parquet / DESI 風 .txt を作って検証する。
"""

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services.annotation_inspect import (
    has_compound_names,
    inspect_annotations,
    find_annotation_sidecar,
)


# ---- サイドカー生成ヘルパ ----

def _write_sidecar(folder, base="SAMPLE"):
    """compound 有 2 / No DB hit 1 / 空 1 / 数値のみ 1 の 5 行サイドカー。"""
    df = pd.DataFrame({
        "mz": [760.5851, 703.5749, 500.1, 600.2, 400.3],
        "compound": ["PI 38:4", "PC 34:1", "No DB hit", "", "400.3"],
        "display_name": ["PI 38:4_760.5851", "PC 34:1_703.5749",
                         "500.1000", "600.2000", "400.3000"],
        "adduct": ["[M-H]-", "[M+H]+", None, None, None],
        "formula": ["C47H83O13P", "C42H82NO8P", None, None, None],
    })
    p = folder / f"{base}_feature_annotations.parquet"
    df.to_parquet(str(p), index=False)
    return p


# ---- TIMS/SCiLS サイドカー ----

class TestSidecar:
    def test_annotated_status_and_coverage(self, tmp_path):
        _write_sidecar(tmp_path)
        r = inspect_annotations({"data_folder": str(tmp_path), "ms_instrument": "TIMS"})
        assert r["status"] == "annotated"
        assert r["n_total"] == 5
        assert r["n_annotated"] == 2          # No DB hit / 空 / 数値のみ を除外
        assert r["coverage_pct"] == 40.0
        assert r["source_kind"] == "sidecar"

    def test_examples_are_real_compounds_only(self, tmp_path):
        _write_sidecar(tmp_path)
        r = inspect_annotations({"data_folder": str(tmp_path), "ms_instrument": "TIMS"})
        comps = [e["compound"] for e in r["examples"]]
        assert comps == ["PI 38:4", "PC 34:1"]
        assert "No DB hit" not in comps and "" not in comps

    def test_has_compound_names_true(self, tmp_path):
        _write_sidecar(tmp_path)
        assert has_compound_names({"data_folder": str(tmp_path), "ms_instrument": "TIMS"}) is True

    def test_sidecar_found_in_subdir(self, tmp_path):
        # data_folder が親でも、直下の <BASE>_Transform/ 内サイドカーを見つける
        sub = tmp_path / "SAMPLE_Transform"
        sub.mkdir()
        _write_sidecar(sub)
        assert find_annotation_sidecar([tmp_path]) is not None
        r = inspect_annotations({"data_folder": str(tmp_path), "ms_instrument": "TIMS"})
        assert r["status"] == "annotated"

    def test_no_parent_walk_avoids_sibling(self, tmp_path):
        # 兄弟フォルダのサイドカーを親経由で誤検出しない
        ds = tmp_path / "dataset_a"
        ds.mkdir()
        sibling = tmp_path / "dataset_b"
        sibling.mkdir()
        _write_sidecar(sibling)
        assert find_annotation_sidecar([ds]) is None

    def test_only_no_db_hit_is_none(self, tmp_path):
        df = pd.DataFrame({"mz": [1.0, 2.0], "compound": ["No DB hit", ""],
                           "display_name": ["1.0", "2.0"]})
        df.to_parquet(str(tmp_path / "X_feature_annotations.parquet"), index=False)
        r = inspect_annotations({"data_folder": str(tmp_path), "ms_instrument": "TIMS"})
        assert r["status"] == "none"
        assert r["n_annotated"] == 0


# ---- フッタ b"peak_list" フォールバック ----

class TestParquetMetaFallback:
    def _write_main_parquet(self, folder, with_peaklist):
        md = {b"mz_sorted": b"100.0,200.0"}
        if with_peaklist:
            md[b"peak_list"] = b"peaks.csv"
        schema = pa.schema([("id", pa.int64()), ("100.0", pa.float64())], metadata=md)
        tbl = pa.Table.from_arrays(
            [pa.array([1, 2]), pa.array([0.1, 0.2])], schema=schema)
        pq.write_table(tbl, str(folder / "SAMPLE.parquet"))

    def test_peaklist_meta_without_sidecar(self, tmp_path):
        self._write_main_parquet(tmp_path, with_peaklist=True)
        r = inspect_annotations({"data_folder": str(tmp_path), "ms_instrument": "TIMS"})
        assert r["status"] == "annotated"
        assert r["source_kind"] == "parquet_meta"
        assert r["source_file"] == "peaks.csv"

    def test_no_peaklist_meta_is_none(self, tmp_path):
        self._write_main_parquet(tmp_path, with_peaklist=False)
        r = inspect_annotations({"data_folder": str(tmp_path), "ms_instrument": "TIMS"})
        assert r["status"] == "none"
        assert has_compound_names({"data_folder": str(tmp_path), "ms_instrument": "TIMS"}) is False


# ---- DESI ----

class TestDesi:
    def _write_named_txt(self, folder):
        # 正規化 .txt: 行1空 / 行2 番号 / 行3 化合物名 / 行4 空 / 行5+ データ
        lines = [
            "",
            "\t\t\t1\t2\t3",
            "\t\t\tAcetylcholine\tGSH\tDopamine",
            "",
            "1\t10\t20\t0.5\t0.6\t0.7",
        ]
        (folder / "sample.txt").write_text("\n".join(lines), encoding="utf-8")

    def _write_traditional_txt(self, folder):
        # 従来形式: 行4 が m/z（数値のみ）
        lines = [
            "",
            "header info",
            "\t\t\t1\t2\t3",
            "\t\t\t100.5\t200.6\t300.7",
            "\t\t\t0\t0\t0",
        ]
        (folder / "sample.txt").write_text("\n".join(lines), encoding="utf-8")

    def test_named_txt_detected(self, tmp_path):
        self._write_named_txt(tmp_path)
        r = inspect_annotations({"data_folder": str(tmp_path), "ms_instrument": "DESI"})
        assert r["status"] == "annotated"
        assert r["source_kind"] == "desi_header"
        names = [e["compound"] for e in r["examples"]]
        assert "Acetylcholine" in names and "Dopamine" in names
        assert has_compound_names({"data_folder": str(tmp_path), "ms_instrument": "DESI"}) is True

    def test_traditional_txt_is_none(self, tmp_path):
        self._write_traditional_txt(tmp_path)
        r = inspect_annotations({"data_folder": str(tmp_path), "ms_instrument": "DESI"})
        assert r["status"] == "none"
        assert has_compound_names({"data_folder": str(tmp_path), "ms_instrument": "DESI"}) is False

    def test_named_csv_header(self, tmp_path):
        (tmp_path / "raw.csv").write_text(
            "x,y,Acetylcholine_15_10,GSH_20_5\n0,0,1,2\n", encoding="utf-8")
        r = inspect_annotations({"data_folder": str(tmp_path), "ms_instrument": "DESI"})
        assert r["status"] == "annotated"
        assert "Acetylcholine" in [e["compound"] for e in r["examples"]]


# ---- 端条件 ----

class TestEdge:
    def test_no_data_folder(self):
        r = inspect_annotations({"ms_instrument": "TIMS"})
        assert r["status"] == "unknown"

    def test_empty_folder_is_none(self, tmp_path):
        r = inspect_annotations({"data_folder": str(tmp_path), "ms_instrument": "TIMS"})
        assert r["status"] == "none"
        assert has_compound_names({"data_folder": str(tmp_path)}) is False

    def test_none_sub(self):
        assert has_compound_names(None) is False


# ---- 性能: xlsx 高速読取 & キャッシュ（ver44.1） ----

class TestPerf:
    def test_xlsx_header_fast_matches_pandas(self, tmp_path):
        # 高速読取（openpyxl read_only 先頭1行）が pandas の read_excel と同じヘッダを返す
        from app.services.annotation_inspect import _xlsx_header_fast
        p = tmp_path / "h.xlsx"
        pd.DataFrame([[0, 0, 1, 2]],
                     columns=["x", "y", "Cpd_A_1", "Cpd_B_2"]).to_excel(p, index=False)
        assert _xlsx_header_fast(p) == list(pd.read_excel(p, nrows=0).columns)

    def test_named_xlsx_detected(self, tmp_path):
        # DESI named 形式 xlsx（x,y,<化合物名>_...）を高速読取経路で検出できる
        p = tmp_path / "raw.xlsx"
        pd.DataFrame(columns=["x", "y", "Acetylcholine_15_10", "GSH_20_5"]).to_excel(
            p, index=False)
        r = inspect_annotations({"data_folder": str(tmp_path),
                                 "ms_instrument": "DESI", "id": "xlsx1"})
        assert r["status"] == "annotated"
        comps = [e["compound"] for e in r["examples"]]
        assert "Acetylcholine" in comps and "GSH" in comps

    def test_inspect_annotations_is_cached(self, tmp_path, monkeypatch):
        from app.services import annotation_inspect as ai
        ai._INSPECT_CACHE.clear()
        _write_sidecar(tmp_path)
        calls = {"n": 0}
        real = ai._inspect_annotations_uncached

        def counting(sub, max_examples=200):
            calls["n"] += 1
            return real(sub, max_examples)

        monkeypatch.setattr(ai, "_inspect_annotations_uncached", counting)
        sub = {"data_folder": str(tmp_path), "ms_instrument": "TIMS", "id": "s1"}
        r1 = ai.inspect_annotations(sub)
        r2 = ai.inspect_annotations(sub)
        assert calls["n"] == 1                       # 2回目はキャッシュヒット
        assert r1["status"] == r2["status"] == "annotated"
        # 別サブプロジェクト（id違い）は署名が変わり再計算
        ai.inspect_annotations({**sub, "id": "s2"})
        assert calls["n"] == 2

    def test_cache_invalidates_on_folder_change(self, tmp_path, monkeypatch):
        import os
        from app.services import annotation_inspect as ai
        ai._INSPECT_CACHE.clear()
        _write_sidecar(tmp_path)
        calls = {"n": 0}
        real = ai._inspect_annotations_uncached

        def counting(sub, max_examples=200):
            calls["n"] += 1
            return real(sub, max_examples)

        monkeypatch.setattr(ai, "_inspect_annotations_uncached", counting)
        sub = {"data_folder": str(tmp_path), "ms_instrument": "TIMS", "id": "s1"}
        ai.inspect_annotations(sub)
        assert calls["n"] == 1
        # フォルダの mtime を進める → 署名が変わりキャッシュ無効化 → 再計算
        st = tmp_path.stat()
        os.utime(tmp_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        ai.inspect_annotations(sub)
        assert calls["n"] == 2
