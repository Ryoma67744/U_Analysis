"""Tests for app.services.parquet_repack (旧 .parquet の row group 再パック)"""

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services import parquet_repack as pr


# ---------------------------------------------------------------------------
# ファクトリ
# ---------------------------------------------------------------------------

def _write_legacy(
    path: Path,
    *,
    n_rows: int = 1000,
    n_mz: int = 20,
    rg_rows: int = 200,
    compression: str = "zstd",
    duplicate_names: bool = False,
    with_metadata: bool = True,
    with_peaklist: bool = True,
    use_dictionary=None,
) -> "pa.Table":
    """旧レイアウト (rg_rows 行/row group) の変換済み parquet を模して書く。

    病的な値（NaN / ±inf / ±0.0 / subnormal / float32 最大値）を必ず混ぜる。
    戻り値は書いた内容の Table（比較用）。

    `use_dictionary` は `pq.ParquetWriter` にそのまま渡す。既定 None は
    **pyarrow の既定（全列 True）**＝ ver49.0 以前の実出力に相当する。
    ver60.0 以降の変換器は `use_dictionary=["annotation"]` で書くので、
    その形の入力を作るときは明示的に渡すこと。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    mz = np.sort(rng.uniform(100.0, 900.0, n_mz))
    names = [f"化合物{i}_{m:.4f} | LipidMaps | [M+H]+" for i, m in enumerate(mz)]
    if duplicate_names and n_mz >= 2:
        names[1] = names[0]

    md = None
    if with_metadata:
        md = {
            b"mz_sorted": ",".join(f"{v:.10g}" for v in mz).encode("utf-8"),
            b"annotation_files": b"Brain_Annotation.csv;Heart_Annotation.csv",
        }
        if with_peaklist:
            md[b"peak_list"] = b"peaks.csv"

    schema = pa.schema(
        [("id", pa.int64()), ("x", pa.float64()), ("y", pa.float64())]
        + [(n, pa.float32()) for n in names]
        + [("annotation", pa.string())],
        metadata=md,
    )
    cols = [
        pa.array(np.arange(n_rows, dtype=np.int64)),
        pa.array(rng.uniform(0.0, 50.0, n_rows)),
        pa.array(rng.uniform(0.0, 50.0, n_rows)),
    ]
    for _ in range(n_mz):
        v = rng.uniform(0.0, 1e5, n_rows).astype(np.float32)
        v[0] = np.float32(np.nan)
        v[1] = np.float32(np.inf)
        v[2] = np.float32(-np.inf)
        v[3] = np.float32(0.0)
        v[4] = np.float32(-0.0)
        v[5] = np.float32(1.4e-45)          # subnormal 最小
        v[6] = np.float32(3.4028235e38)     # float32 最大
        cols.append(pa.array(v))
    cols.append(pa.array([f"tissue{i % 3}" for i in range(n_rows)]))

    table = pa.Table.from_arrays(cols, schema=schema)
    dict_kw = {} if use_dictionary is None else {"use_dictionary": use_dictionary}
    with pq.ParquetWriter(str(path), schema, compression=compression, **dict_kw) as w:
        for s in range(0, n_rows, rg_rows):
            w.write_table(table.slice(s, rg_rows), row_group_size=rg_rows)
    return table


def _read_all(path: Path) -> "pa.RecordBatch":
    """列名で引かずに全行を 1 バッチで読む。

    列名が重複しドットを含むと pq.read_table (dataset API) が
    "Dot path ... unterminated index" で落ちるため、iter_batches を使う。
    """
    pf = pq.ParquetFile(str(path), memory_map=False)
    try:
        return next(pf.iter_batches(batch_size=pf.metadata.num_rows))
    finally:
        pf.close()


def _dict_encoded_columns(path: Path) -> set:
    """辞書エンコードされている列名を返す（row group 0 で代表）。

    列名は**位置**で引く。このリポジトリの Parquet は列名が重複し得るため、
    名前で引くと壊れる。判定方法は test_scils_converter.py の
    `test_intensity_columns_are_not_dictionary_encoded` と同じ。
    """
    pf = pq.ParquetFile(str(path), memory_map=False)
    try:
        names = pf.schema_arrow.names
        rg = pf.metadata.row_group(0)
        return {
            names[i] for i in range(pf.metadata.num_columns)
            if i < len(names) and any("DICTIONARY" in str(e) for e in rg.column(i).encodings)
        }
    finally:
        pf.close()


def _bitwise_same(expected: "pa.Table", got: "pa.RecordBatch") -> bool:
    """全列を整数ビューで突き合わせる（NaN ペイロード / ±0.0 も見る）"""
    if expected.num_columns != got.num_columns:
        return False
    for i in range(expected.num_columns):
        a = expected.column(i).combine_chunks()
        b = got.column(i)
        na = a.to_numpy(zero_copy_only=False)
        nb = b.to_numpy(zero_copy_only=False)
        if na.dtype != nb.dtype:
            return False
        if na.dtype.kind == "f":
            view = {4: np.uint32, 8: np.uint64}[na.dtype.itemsize]
            if not np.array_equal(na.view(view), nb.view(view)):
                return False
        elif not np.array_equal(na, nb):
            return False
    return True


def _row_group_sizes(path: Path) -> list:
    md = pq.ParquetFile(str(path)).metadata
    return [md.row_group(i).num_rows for i in range(md.num_row_groups)]


# ---------------------------------------------------------------------------
# 基本動作
# ---------------------------------------------------------------------------

class TestRepackBasics:

    def test_produces_single_row_group(self, tmp_path):
        p = tmp_path / "sample.parquet"
        _write_legacy(p, n_rows=1000, rg_rows=200)
        assert len(_row_group_sizes(p)) == 5

        res = pr.repack_file(p, backup=False)

        assert res.status == "repacked", res.reason
        assert _row_group_sizes(p) == [1000]
        assert res.row_groups_before == 5
        assert res.row_groups_after == 1

    def test_preserves_values_bitwise(self, tmp_path):
        """NaN のペイロードや ±0.0 まで含めて 1 ビットも変わらないこと"""
        p = tmp_path / "sample.parquet"
        expected = _write_legacy(p, n_rows=1000, rg_rows=200)

        assert pr.repack_file(p, backup=False).status == "repacked"

        assert _bitwise_same(expected, _read_all(p))

    def test_preserves_schema_metadata(self, tmp_path):
        """mz_sorted / annotation_files / peak_list の 3 キーが完全一致すること"""
        p = tmp_path / "sample.parquet"
        _write_legacy(p)
        before = pq.ParquetFile(str(p)).schema_arrow.metadata

        assert pr.repack_file(p, backup=False).status == "repacked"

        after = pq.ParquetFile(str(p)).schema_arrow.metadata
        assert after == before
        assert set(after) == {b"mz_sorted", b"annotation_files", b"peak_list"}

    def test_preserves_compression(self, tmp_path):
        p = tmp_path / "sample.parquet"
        _write_legacy(p, compression="zstd")

        assert pr.repack_file(p, backup=False).status == "repacked"

        md = pq.ParquetFile(str(p)).metadata
        assert md.row_group(0).column(0).compression.upper() == "ZSTD"

    def test_plain_intensity_columns_stay_plain(self, tmp_path):
        """★ ver63.1: ver60.0 以降の出力（強度は PLAIN）を再パックしても
        辞書エンコードが復活しないこと。

        `pq.ParquetWriter` の `use_dictionary` 既定は**全列 True** なので、
        指定を忘れると変換器が ver60.0 で強度列から外した辞書が再パックで戻る
        （連続量への辞書は書き込みが 3.2 倍遅く、ファイルも大きくなる）。
        値は変わらないため `_verify_files` のビット比較も通ってしまい、
        このテストが無いと誰も気づけない。

        `repack_file` は単一 row group をスキップするので、実際に退行しうるのは
        メモリ不足で `single-fallback` に落ちた複数 row group の出力。
        ここでもその形（rg_rows=200）を作って再現している。
        """
        p = tmp_path / "sample.parquet"
        expected = _write_legacy(p, n_rows=1000, n_mz=5, rg_rows=200,
                                 use_dictionary=["annotation"])
        assert _dict_encoded_columns(p) == {"annotation"}, "前提: 入力は PLAIN"

        res = pr.repack_file(p, backup=False)

        assert res.status == "repacked", res.reason
        assert _dict_encoded_columns(p) == {"annotation"}, (
            "再パックで強度列に辞書エンコードが復活している"
        )
        assert _bitwise_same(expected, _read_all(p))

    def test_dictionary_encoded_input_stays_dictionary(self, tmp_path):
        """逆方向。入力が全列辞書（ver49.0 以前の実出力）ならそのまま維持する。

        「入力の選択を維持する」が仕様であって「常に PLAIN にする」ではない。
        `test_file_size_does_not_grow` がこの形の入力に依存しているので、
        ここで明示的に固定しておく。
        """
        p = tmp_path / "sample.parquet"
        expected = _write_legacy(p, n_rows=1000, n_mz=5, rg_rows=200)
        n_cols = pq.ParquetFile(str(p)).metadata.num_columns
        assert len(_dict_encoded_columns(p)) == n_cols, "前提: 入力は全列辞書"

        res = pr.repack_file(p, backup=False)

        assert res.status == "repacked", res.reason
        assert len(_dict_encoded_columns(p)) == n_cols, (
            "入力が全列辞書だったのに再パックで PLAIN に落ちている"
        )
        assert _bitwise_same(expected, _read_all(p))

    def test_uncompressed_input(self, tmp_path):
        """圧縮なしのファイル。`UNCOMPRESSED` をそのまま渡すと pyarrow が例外を出す"""
        p = tmp_path / "sample.parquet"
        expected = _write_legacy(p, compression="none")

        res = pr.repack_file(p, backup=False)

        assert res.status == "repacked", res.reason
        assert _row_group_sizes(p) == [1000]
        assert _bitwise_same(expected, _read_all(p))

    def test_duplicate_column_names(self, tmp_path):
        """列名が重複していても処理できること（列名で引く実装だと落ちる）"""
        p = tmp_path / "sample.parquet"
        expected = _write_legacy(p, duplicate_names=True)
        names = pq.ParquetFile(str(p)).schema_arrow.names
        assert len(set(names)) < len(names), "重複列名のファイルになっていない"

        res = pr.repack_file(p, backup=False)

        assert res.status == "repacked", res.reason
        assert _row_group_sizes(p) == [1000]
        assert _bitwise_same(expected, _read_all(p))

    def test_file_size_does_not_grow(self, tmp_path):
        p = tmp_path / "sample.parquet"
        _write_legacy(p, n_rows=2000, n_mz=40, rg_rows=200)
        before = p.stat().st_size

        res = pr.repack_file(p, backup=False)

        assert res.status == "repacked"
        assert p.stat().st_size <= before
        assert res.footer_after < res.footer_before


# ---------------------------------------------------------------------------
# スキップ判定（冪等・対象外の保護）
# ---------------------------------------------------------------------------

class TestSkip:

    def test_skips_single_row_group_and_is_idempotent(self, tmp_path):
        p = tmp_path / "sample.parquet"
        _write_legacy(p, n_rows=500, rg_rows=500)
        stat_before = (p.stat().st_size, p.stat().st_mtime_ns)

        res = pr.repack_file(p, backup=False)

        assert res.status == "skipped"
        assert "既に単一 row group" in res.reason
        assert (p.stat().st_size, p.stat().st_mtime_ns) == stat_before

    def test_repack_twice_is_noop(self, tmp_path):
        p = tmp_path / "sample.parquet"
        _write_legacy(p, n_rows=1000, rg_rows=200)
        assert pr.repack_file(p, backup=False).status == "repacked"
        stat_after_first = (p.stat().st_size, p.stat().st_mtime_ns)

        second = pr.repack_file(p, backup=False)

        assert second.status == "skipped"
        assert (p.stat().st_size, p.stat().st_mtime_ns) == stat_after_first

    def test_skips_parquet_without_mz_sorted(self, tmp_path):
        """サイドカーなど SCiLS 変換出力でない parquet は触らない"""
        p = tmp_path / "other.parquet"
        _write_legacy(p, with_metadata=False)
        stat_before = (p.stat().st_size, p.stat().st_mtime_ns)

        res = pr.repack_file(p, backup=False)

        assert res.status == "skipped"
        assert "mz_sorted" in res.reason
        assert (p.stat().st_size, p.stat().st_mtime_ns) == stat_before

    def test_skips_non_parquet(self, tmp_path):
        p = tmp_path / "notparquet.parquet"
        p.write_bytes(b"this is not a parquet file at all")

        res = pr.repack_file(p, backup=False)

        assert res.status == "skipped"
        assert "Parquet ではありません" in res.reason

    def test_peak_list_absent_is_fine(self, tmp_path):
        """peak_list が無いファイル（3 キーのうち 2 キーだけ）でも通ること"""
        p = tmp_path / "sample.parquet"
        _write_legacy(p, with_peaklist=False)

        assert pr.repack_file(p, backup=False).status == "repacked"

        md = pq.ParquetFile(str(p)).schema_arrow.metadata
        assert set(md) == {b"mz_sorted", b"annotation_files"}


# ---------------------------------------------------------------------------
# バックアップ・Dry-run・失敗時の安全性
# ---------------------------------------------------------------------------

class TestSafety:

    def test_backup_keeps_original_content(self, tmp_path):
        p = tmp_path / "sample.parquet"
        expected = _write_legacy(p, n_rows=1000, rg_rows=200)

        assert pr.repack_file(p, backup=True).status == "repacked"

        bak = Path(str(p) + ".bak")
        assert bak.exists()
        assert len(_row_group_sizes(bak)) == 5      # バックアップは旧レイアウトのまま
        assert _bitwise_same(expected, _read_all(bak))

    def test_no_backup_when_disabled(self, tmp_path):
        p = tmp_path / "sample.parquet"
        _write_legacy(p)

        assert pr.repack_file(p, backup=False).status == "repacked"

        assert not Path(str(p) + ".bak").exists()

    def test_dry_run_does_not_write(self, tmp_path):
        p = tmp_path / "sample.parquet"
        _write_legacy(p, n_rows=1000, rg_rows=200)
        stat_before = (p.stat().st_size, p.stat().st_mtime_ns)

        res = pr.repack_file(p, dry_run=True, backup=True)

        assert res.status == "dry-run"
        assert (p.stat().st_size, p.stat().st_mtime_ns) == stat_before
        assert not Path(str(p) + ".bak").exists()
        assert list(tmp_path.glob("*.repacking")) == []

    def test_write_failure_leaves_original_intact(self, tmp_path, monkeypatch):
        """書き込み途中で落ちても元ファイルは無傷、一時ファイルも残らない"""
        p = tmp_path / "sample.parquet"
        expected = _write_legacy(p, n_rows=1000, rg_rows=200)
        stat_before = (p.stat().st_size, p.stat().st_mtime_ns)

        real_writer = pr.pq.ParquetWriter

        class _BoomWriter(real_writer):
            def write_table(self, *a, **kw):
                raise RuntimeError("boom")

        monkeypatch.setattr(pr.pq, "ParquetWriter", _BoomWriter)
        res = pr.repack_file(p, backup=False)

        assert res.status == "error"
        assert "boom" in res.reason
        assert (p.stat().st_size, p.stat().st_mtime_ns) == stat_before
        assert list(tmp_path.glob("*.repacking")) == []
        monkeypatch.undo()
        assert _bitwise_same(expected, _read_all(p))

    def test_verification_failure_does_not_replace(self, tmp_path, monkeypatch):
        """検証が失敗したら置換しない"""
        p = tmp_path / "sample.parquet"
        _write_legacy(p, n_rows=1000, rg_rows=200)
        stat_before = (p.stat().st_size, p.stat().st_mtime_ns)

        monkeypatch.setattr(pr, "_verify_files", lambda *a, **kw: "わざと失敗")
        res = pr.repack_file(p, backup=False, verify=True)

        assert res.status == "error"
        assert "検証に失敗" in res.reason
        assert (p.stat().st_size, p.stat().st_mtime_ns) == stat_before
        assert list(tmp_path.glob("*.repacking")) == []

    def test_verification_detects_corruption(self, tmp_path):
        """検証が実際に値の違いを捕まえられること（比較器そのものの検査）"""
        a = pa.array([0.0, np.nan, 1.0], type=pa.float32())
        assert pr._arrays_bit_equal(a, a)
        # +0.0 と -0.0 は Arrow の等価比較では一致してしまうが、こちらは弾く
        b = pa.array([-0.0, np.nan, 1.0], type=pa.float32())
        assert not pr._arrays_bit_equal(a, b)
        # NaN 同士は Arrow の等価比較では不一致になるが、こちらは一致と判定する
        assert pr._arrays_bit_equal(
            pa.array([np.nan], type=pa.float32()),
            pa.array([np.nan], type=pa.float32()),
        )

    def test_sweep_removes_stale_temp(self, tmp_path):
        stale = tmp_path / "sample.parquet.repacking"
        stale.write_bytes(b"leftover")

        removed = pr.sweep_stale_temps(tmp_path)

        assert [r.name for r in removed] == ["sample.parquet.repacking"]
        assert not stale.exists()


# ---------------------------------------------------------------------------
# メモリ予算
# ---------------------------------------------------------------------------

class TestMemoryBudget:

    def test_skips_when_budget_insufficient(self, tmp_path):
        """予算不足ならスキップし、必要量を理由に含めること"""
        p = tmp_path / "sample.parquet"
        _write_legacy(p, n_rows=1000, rg_rows=200)
        stat_before = (p.stat().st_size, p.stat().st_mtime_ns)

        res = pr.repack_file(p, backup=False, _budget_override=1.0)

        assert res.status == "skipped"
        assert "メモリ不足" in res.reason
        assert (p.stat().st_size, p.stat().st_mtime_ns) == stat_before

    def test_allow_split_produces_multiple_row_groups(self, tmp_path):
        """--allow-split なら予算内で最大の row group にまとめる"""
        p = tmp_path / "sample.parquet"
        expected = _write_legacy(p, n_rows=100_000, n_mz=4, rg_rows=200)
        assert len(_row_group_sizes(p)) == 500
        # 全行 (100,000) には足りないが 30,000 行なら足りる予算を与える
        row_width = 8 + 8 + 8 + 4 * 4 + 64
        pf = pq.ParquetFile(str(p))
        footer_ram = pr._footer_ram_bytes(pf.metadata)
        pf.close()
        budget = footer_ram + pr._WRITER_MARGIN_BYTES + row_width * 30_000

        res = pr.repack_file(
            p, backup=False, allow_split=True, _budget_override=budget)

        assert res.status == "repacked", res.reason
        sizes = _row_group_sizes(p)
        assert 1 < len(sizes) < 500, sizes
        assert sum(sizes) == 100_000
        assert _bitwise_same(expected, _read_all(p))

    def test_budget_unknown_proceeds(self, tmp_path, monkeypatch):
        """空きメモリが判定できないときは止めずに進む"""
        p = tmp_path / "sample.parquet"
        _write_legacy(p, n_rows=1000, rg_rows=200)
        monkeypatch.setattr(pr, "_available_memory_gb", lambda: None)

        res = pr.repack_file(p, backup=False)

        assert res.status == "repacked", res.reason
        assert res.budget is None

    def test_estimate_uses_footer_and_data(self):
        peak = pr.estimate_peak_bytes(n_rows=1000, row_width=100, footer_ram=5_000)
        assert peak == 5_000 + 1000 * 100 + pr._WRITER_MARGIN_BYTES


# ---------------------------------------------------------------------------
# フォルダ一括処理 / CLI
# ---------------------------------------------------------------------------

class TestRepackFolder:

    def test_processes_and_reports(self, tmp_path):
        _write_legacy(tmp_path / "a.parquet", n_rows=600, rg_rows=200)
        _write_legacy(tmp_path / "b.parquet", n_rows=600, rg_rows=600)   # スキップ
        _write_legacy(tmp_path / "c_feature_annotations.parquet", n_rows=600,
                      rg_rows=200)                                       # サイドカー
        lines = []

        agg = pr.repack_folder(tmp_path, backup=False, emit=lines.append)

        assert agg.n_processed == 1
        assert agg.n_skipped == 1
        assert agg.n_error == 0
        # サイドカーは候補にすら入らない
        assert len(_row_group_sizes(tmp_path / "c_feature_annotations.parquet")) == 3
        text = "\n".join(lines)
        assert "[repack] 2 files matched." in text
        assert "[repack] Processed : 1" in text

    def test_summary_block_is_parseable(self, tmp_path):
        """GUI 側の _parse_summary が読める体裁になっていること"""
        from app.callbacks.parquet_maintenance_callbacks import _parse_summary

        _write_legacy(tmp_path / "a.parquet", n_rows=600, rg_rows=200)
        lines = []
        pr.repack_folder(tmp_path, backup=False, emit=lines.append)

        summary = _parse_summary("\n".join(lines))

        assert summary["Processed"] == "1"
        assert summary["Errors"] == "0"
        assert summary["SizeBefore"].endswith(("B", "KB", "MB", "GB"))
        assert summary["Elapsed"].endswith("sec")

    def test_progress_regex_matches_per_file_lines(self, tmp_path):
        """進捗抽出の正規表現が実際のログに当たること (re.MULTILINE 必須)"""
        from app.callbacks.parquet_maintenance_callbacks import (
            _FILE_COUNT_RE, _FILE_PROGRESS_RE,
        )

        _write_legacy(tmp_path / "a.parquet", n_rows=400, rg_rows=200)
        _write_legacy(tmp_path / "b.parquet", n_rows=400, rg_rows=200)
        lines = []
        pr.repack_folder(tmp_path, backup=False, emit=lines.append)
        text = "\n".join(lines)

        assert _FILE_COUNT_RE.search(text).group(1) == "2"
        found = [(int(m.group(1)), int(m.group(2)))
                 for m in _FILE_PROGRESS_RE.finditer(text)]
        assert found == [(1, 2), (2, 2)]

    def test_error_in_one_file_does_not_stop_others(self, tmp_path):
        _write_legacy(tmp_path / "a.parquet", n_rows=400, rg_rows=200)
        (tmp_path / "b.parquet").write_bytes(b"broken")
        _write_legacy(tmp_path / "c.parquet", n_rows=400, rg_rows=200)

        agg = pr.repack_folder(tmp_path, backup=False, emit=lambda s: None)

        assert agg.n_processed == 2
        assert _row_group_sizes(tmp_path / "a.parquet") == [400]
        assert _row_group_sizes(tmp_path / "c.parquet") == [400]

    def test_include_pattern_filters(self, tmp_path):
        _write_legacy(tmp_path / "keep.parquet", n_rows=400, rg_rows=200)
        _write_legacy(tmp_path / "other.parquet", n_rows=400, rg_rows=200)

        pr.repack_folder(tmp_path, patterns=["keep*.parquet"],
                         backup=False, emit=lambda s: None)

        assert _row_group_sizes(tmp_path / "keep.parquet") == [400]
        assert len(_row_group_sizes(tmp_path / "other.parquet")) == 2

    def test_dry_run_summary_omits_reduction(self, tmp_path):
        """dry-run では書いていないので「後サイズ」「削減率」を出さないこと

        0.0% と表示すると「効果が無い」と読めてしまう。
        """
        _write_legacy(tmp_path / "a.parquet", n_rows=600, rg_rows=200)
        lines = []

        pr.repack_folder(tmp_path, dry_run=True, emit=lines.append)

        text = "\n".join(lines)
        assert "[repack] Size before:" in text
        assert "Size after" not in text
        assert "Reduction" not in text

    def test_empty_folder(self, tmp_path):
        lines = []
        agg = pr.repack_folder(tmp_path, emit=lines.append)
        assert agg.n_processed == 0
        assert "[repack] 該当ファイルなし。" in "\n".join(lines)


class TestCli:

    def test_parse_args_defaults(self):
        from tools.repack_parquet_rowgroups import parse_args

        target, opts = parse_args(["/data"])

        assert target == "/data"
        assert opts["dry_run"] is False
        assert opts["backup"] is True
        assert opts["verify"] is True
        assert opts["allow_split"] is False
        assert opts["patterns"] == ["*.parquet"]

    def test_parse_args_flags(self):
        from tools.repack_parquet_rowgroups import parse_args

        _, opts = parse_args([
            "/data", "--dry-run", "--no-backup", "--skip-verify",
            "--allow-split", "--no-recursive", "--include=a*.parquet, b*.pq",
        ])

        assert opts["dry_run"] is True
        assert opts["backup"] is False
        assert opts["verify"] is False
        assert opts["allow_split"] is True
        assert opts["recursive"] is False
        assert opts["patterns"] == ["a*.parquet", "b*.pq"]

    def test_parse_args_rejects_unknown_option(self):
        from tools.repack_parquet_rowgroups import parse_args

        with pytest.raises(SystemExit):
            parse_args(["/data", "--nope"])

    def test_parse_args_requires_target(self):
        from tools.repack_parquet_rowgroups import parse_args

        with pytest.raises(SystemExit):
            parse_args(["--dry-run"])

    def test_main_returns_2_on_error(self, tmp_path):
        """1 件でも失敗したら非ゼロで返すこと（緑の成功表示にしないため）"""
        from tools.repack_parquet_rowgroups import main

        # 末尾は正しい Parquet の体裁（<footer_len><"PAR1"）だが中身が壊れており、
        # 末尾 8 バイトの事前判定は通過し ParquetFile() で失敗する
        broken = tmp_path / "broken.parquet"
        broken.write_bytes(b"PAR1" + b"\x00" * 64 + (32).to_bytes(4, "little") + b"PAR1")
        _write_legacy(tmp_path / "ok.parquet", n_rows=400, rg_rows=200)

        assert main([str(tmp_path), "--no-backup"]) == 2
        # 失敗しても他のファイルは処理される
        assert _row_group_sizes(tmp_path / "ok.parquet") == [400]

    def test_main_returns_0_on_success(self, tmp_path):
        from tools.repack_parquet_rowgroups import main

        _write_legacy(tmp_path / "ok.parquet", n_rows=400, rg_rows=200)

        assert main([str(tmp_path), "--no-backup"]) == 0
        assert _row_group_sizes(tmp_path / "ok.parquet") == [400]
