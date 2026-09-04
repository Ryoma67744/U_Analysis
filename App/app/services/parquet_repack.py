# =============================================================================
# MSI Analysis Application - Parquet 再パック (row group レイアウト作り直し)
#
# ver49.0 より前に変換した .parquet は 200 行/row group のままで、実データ規模
# (203,078 spot × 約 2,700 m/z) では 1,016 row group・フッタ 735MB になる。
# R 取り込みは 1 回で同じファイルを約 20 回開くため、取り込みごとに約 65 秒が
# フッタ解析だけに消える。
#
# 本モジュールは CSV から再変換せず「レイアウトだけ」作り直す。
# 値は 1 ビットも変えない（全列を整数ビューで突き合わせて確認してから置換する）。
# =============================================================================

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pyarrow as pa
import pyarrow.parquet as pq

from app.services.scils_converter import (
    _PER_CHUNK_META_BYTES, _available_memory_gb, _unique_path,
)

logger = logging.getLogger("msi.parquet_repack")

# 一時ファイルのサフィックス。**`.parquet` で終わらせてはいけない**。
# data_manager._filter_tims_candidates() は拡張子 .parquet/.pq のファイルを
# 解析サンプルとして列挙し、annotation_inspect も *.parquet のフッタを読むため、
# `sample.repacking.parquet` だと中断時の残骸がサンプル一覧に現れてしまう。
# slim_existing_rds.R が `paste0(path, ".tmp")` を使っているのと同じ理由。
_TMP_SUFFIX = ".repacking"

# ParquetWriter の符号化・圧縮バッファの実測余裕
_WRITER_MARGIN_BYTES = 320 * 1024 ** 2
# 可変長列 (string) 1 行あたりの Arrow 上の概算バイト数（強気側）
_VARLEN_BYTES_PER_ROW = 64
# 空きメモリから無条件に引く絶対余白（Python + pyarrow + numpy の常駐分）
_HEADROOM_GB = 1.0
# 空きのうち再パックに使ってよい割合。変換器の 0.6 より強気にできるのは、
# 再パックは Dash ワーカーとは別プロセスで走り、かつ start_analysis_process が
# 起動前に「空き 10GB 以上」を確認済みのため（二重ゲート）。
_AVAIL_FRACTION = 0.85
# column chunk 1 件あたりの解析済みフッタ常駐バイト数 (_PER_CHUNK_META_BYTES) は
# ★ ver56.8 で scils_converter へ移設した（変換側の Phase A フッタ見積りでも使うため）。
# 実測値の根拠は移設先のコメントを参照。ここでは import して従来どおり使う。
# フッタの on-disk サイズ → 解析後 RAM の膨張率。同じ実測で 8.25 / 7.64 / 7.57 倍。
# 実ファイルは列名がさらに長く bytes/chunk が約 2 倍のため、
# _PER_CHUNK_META_BYTES 側との max を採って安全側に倒す。
_FOOTER_INFLATE = 7.6
# 検証時に 1 度に読む行数の目標バイト数（新旧 2 本を同時に持つので控えめに）
_VERIFY_BLOCK_BYTES = 128 * 1024 ** 2
# 分割時の下限行数。これ以下に刻んでもフッタ側が支配的になり意味がない。
_MIN_SPLIT_ROWS = 8192

# ColumnChunkMetaData.compression は "UNCOMPRESSED" を返すが、
# pyarrow の compression 引数はそれを受け付けない（"none" でなければならない）。
# 素直に .lower() すると非圧縮ファイルで例外になる。
_CODEC_MAP = {
    "UNCOMPRESSED": "none", "NONE": "none", "SNAPPY": "snappy",
    "GZIP": "gzip", "BROTLI": "brotli", "LZ4": "lz4",
    "LZ4_RAW": "lz4", "ZSTD": "zstd",
}


@dataclass
class RepackResult:
    """1 ファイルの再パック結果"""
    path: Path
    status: str                     # "repacked" | "skipped" | "error" | "dry-run"
    reason: str = ""
    size_before: int = 0
    size_after: int = 0
    row_groups_before: int = 0
    row_groups_after: int = 0
    footer_before: int = 0
    footer_after: int = 0
    n_rows: int = 0
    elapsed_sec: float = 0.0
    estimated_peak: int = 0
    budget: Optional[int] = None


@dataclass
class FolderResult:
    """フォルダ一括処理の集計"""
    results: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def n_processed(self) -> int:
        return sum(1 for r in self.results if r.status in ("repacked", "dry-run"))

    @property
    def n_skipped(self) -> int:
        return sum(1 for r in self.results if r.status == "skipped")

    @property
    def n_error(self) -> int:
        return sum(1 for r in self.results if r.status == "error")

    @property
    def size_before(self) -> int:
        return sum(r.size_before for r in self.results)

    @property
    def size_after(self) -> int:
        return sum(r.size_after or r.size_before for r in self.results)


# ---------------------------------------------------------------------------
# 見積り
# ---------------------------------------------------------------------------

def format_bytes(n: float) -> str:
    """人間可読なバイト表記。slim_existing_rds.R の .format_bytes と同じ体裁。"""
    if n is None or n < 0:
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    i, x = 0, float(n)
    while x >= 1024 and i < len(units) - 1:
        x /= 1024
        i += 1
    return f"{x:.2f} {units[i]}"


def peek_footer_bytes(path: Path) -> Optional[int]:
    """フッタ長をファイル末尾 8 バイトから直接読む（thrift 解析なし・確保ゼロ）。

    pq.ParquetFile() を呼ぶとフッタ全体が解析され、実データ規模では 5GB 超が
    常駐する。「開けるかどうか」の判定をそれより前に済ませるための関数。
    Parquet の末尾は <4byte little-endian footer length><"PAR1">。
    """
    try:
        if path.stat().st_size < 12:
            return None
        with open(path, "rb") as f:
            f.seek(-8, os.SEEK_END)
            tail = f.read(8)
        if len(tail) != 8 or tail[4:] != b"PAR1":
            return None
        return int.from_bytes(tail[:4], "little")
    except OSError:
        return None


def _row_width_bytes(schema: "pa.Schema") -> int:
    """1 行あたりの Arrow 上の概算バイト数"""
    width = 0
    for f in schema:
        try:
            width += f.type.bit_width // 8
        except (ValueError, AttributeError, TypeError):
            width += _VARLEN_BYTES_PER_ROW
    return width


def budget_bytes() -> Optional[float]:
    """再パックに使ってよいメモリ (bytes)。不明なら None。"""
    avail = _available_memory_gb()
    if avail is None:
        return None
    return max(0.0, avail - _HEADROOM_GB) * _AVAIL_FRACTION * 1024 ** 3


def estimate_peak_bytes(*, n_rows: int, row_width: int, footer_ram: float) -> float:
    """再パックのピークメモリ概算。

    peak = footer_ram + data + margin
      footer_ram : 解析済みフッタ。**どんな戦略でも削れない**支配項
      data       : 出力そのもの。1 row group にする以上、全行の materialize が必須
                   （write_batch を繰り返しても単一 row group にはならないことを実測で確認）
      margin     : writer の符号化・圧縮バッファ
    """
    return footer_ram + n_rows * row_width + _WRITER_MARGIN_BYTES


def _footer_ram_bytes(md) -> float:
    """解析済みフッタの常駐バイト数。2 つの推定の大きい方を採る。"""
    by_chunk = md.num_row_groups * md.num_columns * _PER_CHUNK_META_BYTES
    by_size = (md.serialized_size or 0) * _FOOTER_INFLATE
    return float(max(by_chunk, by_size))


def _detect_codec(md) -> str:
    """元ファイルの圧縮コーデックを検出（先頭 row group の先頭列で代表）

    **圧縮レベルは検出できない。** Parquet の column chunk メタデータが持つのは
    コーデック名だけで、`compression_level` はファイルに一切記録されない
    （書き手側のパラメータであってフォーマットの一部ではない）。したがって
    level を指定して書かれたファイルを再パックすると、値はビット一致のまま
    pyarrow 既定の level で書き直され、**サイズだけが変わる**。
    将来 `scils_converter` 側で level を明示するなら、`repack_file` にも
    同じ値を渡すオプションを同時に足すこと。
    """
    try:
        raw = md.row_group(0).column(0).compression
    except Exception:
        return "zstd"
    return _CODEC_MAP.get(str(raw).upper(), "zstd")


def _detect_dictionary_columns(md, schema):
    """入力の辞書エンコードを検出し、`use_dictionary` に渡せる形で返す。

    戻り値は `True`（全列）/ `False`（無し）/ 列名のリスト（一部の列だけ）。

    ★ ver63.1: ここが無いと書き込み側の `use_dictionary` が未指定になり、
      pyarrow 既定の**全列 True** が適用されていた。変換器は ver60.0 で
      `use_dictionary=["annotation"]` に絞っている（強度のような連続量に辞書を
      掛けると値ごとにハッシュを引くので書き込みが遅く、辞書本体とインデックスを
      両方持つのでファイルも大きくなる）のに、**再パックを通すとその選択が
      黙って捨てられ、強度列に辞書が戻っていた**。
      コーデックは `_detect_codec` で維持しているのに、エンコードだけ維持して
      いないという非対称が本体。値は変わらないので `_verify_files` のビット比較も
      通ってしまい、気づける手段が無かった。

    **検出**は列名ではなく位置で行う。このリポジトリの Parquet は列名が重複し得る
    ため（ver55.0 以前は化合物名を列名に焼き込んでいた）、名前で引くと壊れる。
    重複名があると `use_dictionary` の名前指定は同名の全列に効いてしまうが、
    重複名を持つ古いファイルは全列が辞書 ON なので下の `True` 分岐で拾われ、
    「一部だけ辞書 かつ 名前が重複」は実質発生しない。

    列名に `.` や `[]` が含まれていても `use_dictionary` へそのまま渡してよい
    （`化合物0_123.4567 | LipidMaps | [M+H]+` で pyarrow 25.0.1 実測。スキーマが
    フラットなのでネストしたパスとしての解釈は起きない）。`read_table` の
    dataset API が同じ名前で `Dot path ... unterminated index` を出すのとは別経路。
    """
    names = list(schema.names)
    try:
        rg = md.row_group(0)
        dict_idx = {
            i for i in range(md.num_columns)
            if any("DICTIONARY" in str(e) for e in rg.column(i).encodings)
        }
    except Exception:
        # エンコードが読めないときは従来の挙動（pyarrow 既定）に倒す。
        # ここで False にすると、辞書が正しく効いていたファイルを
        # 黙って PLAIN に落としてしまう。
        return True
    if not dict_idx:
        return False
    if len(dict_idx) >= md.num_columns:
        return True
    return sorted({names[i] for i in dict_idx if i < len(names)})


# ---------------------------------------------------------------------------
# 値の比較（NaN ペイロード・±0.0 まで見る）
# ---------------------------------------------------------------------------

def _arrays_bit_equal(a: "pa.Array", b: "pa.Array") -> bool:
    """2 つの Arrow 配列が値としてビット単位で同一か。

    pa.Array.equals() は使えない。実測で両方向に誤る:
      NaN == NaN   -> False (同一データを不一致と誤判定)
      +0.0 == -0.0 -> True  (符号の違いを見逃す)
    そこで浮動小数点列は整数ビューで比較する。
    """
    import numpy as np

    if a.type != b.type or len(a) != len(b) or a.null_count != b.null_count:
        return False
    if len(a) == 0:
        return True

    if not pa.types.is_floating(a.type):
        # int64 / string / bool は Arrow の等価比較で厳密（NaN 問題は浮動小数点型のみ）。
        # null 入り int64 を numpy 化すると float64 化して精度が落ちるため numpy は使わない。
        return a.equals(b)

    mask = None
    if a.null_count:
        va = a.is_valid().to_numpy(zero_copy_only=False)
        vb = b.is_valid().to_numpy(zero_copy_only=False)
        if not np.array_equal(va, vb):
            return False
        mask = va  # null スロットの値バイトは未定義なので比較から除く

    na = a.to_numpy(zero_copy_only=False)
    nb = b.to_numpy(zero_copy_only=False)
    if na.dtype != nb.dtype:
        return False
    view = {2: np.uint16, 4: np.uint32, 8: np.uint64}.get(na.dtype.itemsize)
    if view is None:
        return bool(np.array_equal(na, nb))
    ia, ib = na.view(view), nb.view(view)
    if mask is not None:
        ia, ib = ia[mask], ib[mask]
    return bool(np.array_equal(ia, ib))


def _verify_files(pf_src, dst: Path, schema, *, n_rows: int, row_width: int) -> Optional[str]:
    """新旧を突き合わせて検証。一致すれば None、しなければ理由の文字列。

    列は**位置で**取り出す。列名で引くと、名前が重複しているファイルで
    KeyError になる（実際に踏んだ）。iter_batches は行範囲が両者で一致するため、
    同じ batch_size で回せばそのまま対応が取れる。
    """
    pf_dst = pq.ParquetFile(str(dst), memory_map=False)
    try:
        md = pf_dst.metadata
        if md.num_rows != n_rows:
            return f"行数不一致: 期待 {n_rows:,} / 実際 {md.num_rows:,}"
        if not pf_dst.schema_arrow.equals(schema, check_metadata=True):
            # メタデータが落ちても data_manager は例外を出さず列名の正規表現パースへ
            # 静かに退避する（data_manager.py:157-171 → :173-184）ため、
            # ここで捕まえないと事故が無言で通過する。
            return "スキーマ/メタデータが一致しません（mz_sorted 等が落ちた可能性）"

        vk = max(1, min(n_rows, int(_VERIFY_BLOCK_BYTES // max(1, row_width))))
        seen = 0
        it_old = pf_src.iter_batches(batch_size=vk)
        it_new = pf_dst.iter_batches(batch_size=vk)
        for b_old, b_new in zip(it_old, it_new):
            if b_old.num_rows != b_new.num_rows:
                return f"バッチ行数不一致 (行 {seen:,} 付近)"
            if b_old.num_columns != b_new.num_columns:
                return f"列数不一致: {b_old.num_columns} / {b_new.num_columns}"
            for i in range(b_old.num_columns):
                if not _arrays_bit_equal(b_old.column(i), b_new.column(i)):
                    name = schema.field(i).name if i < len(schema.names) else f"#{i}"
                    return f"列 '{name}' の内容が一致しません (行 {seen:,} 付近)"
            seen += b_old.num_rows
            b_old = b_new = None
        if seen != n_rows:
            return f"検証できた行数が足りません: {seen:,} / {n_rows:,}"
        return None
    finally:
        pf_dst.close()


# ---------------------------------------------------------------------------
# 1 ファイルの再パック
# ---------------------------------------------------------------------------

def _make_backup(path: Path) -> Path:
    """`.bak` を作る。os.link ならディスク追加ゼロ・瞬時。

    os.replace は名前を張り替えるだけなので、ハードリンクで作った .bak は
    元ファイルの inode を指したまま残る。
    """
    bak = Path(str(path) + ".bak")
    if bak.exists():
        bak.unlink()
    try:
        os.link(str(path), str(bak))
    except OSError:
        # 別ファイルシステム・権限などでハードリンクが張れない場合のみコピー
        shutil.copy2(str(path), str(bak))
    return bak


def sweep_stale_temps(folder: Path, *, recursive: bool = True) -> list:
    """中断で残った `*.repacking` を掃除する。

    サフィックスが `.parquet` ではないので残骸自体は無害（サンプル一覧に出ない）。
    これは衛生であって安全上の必須処理ではない。
    """
    globber = folder.rglob if recursive else folder.glob
    removed = []
    for stale in sorted(globber(f"*{_TMP_SUFFIX}")) + sorted(globber(f"*{_TMP_SUFFIX}(*)")):
        try:
            stale.unlink()
            removed.append(stale)
        except OSError as e:
            logger.warning("一時ファイルの削除に失敗: %s (%s)", stale, e)
    return removed


def repack_file(
    path: Path,
    *,
    dry_run: bool = False,
    backup: bool = True,
    verify: bool = True,
    allow_split: bool = False,
    progress: Optional[Callable[[str], None]] = None,
    _budget_override: Optional[float] = None,
) -> RepackResult:
    """1 ファイルを「全行 1 row group」へ再パックする。

    値は 1 ビットも変えない。スキーマ (key-value メタデータ 3 キーを含む) は
    元の schema オブジェクトをそのまま writer へ渡すことで保持する。

    _budget_override はテスト用。メモリ予算 (bytes) を直接指定する。
    """
    t0 = time.time()
    res = RepackResult(path=path, status="error")
    try:
        res.size_before = path.stat().st_size
    except OSError as e:
        res.reason = f"stat 失敗: {e}"
        return res

    # --- 開く前のガード: フッタ長だけ末尾 8 バイトから読む ---
    footer_disk = peek_footer_bytes(path)
    if footer_disk is None:
        res.status = "skipped"
        res.reason = "Parquet ではありません"
        return res
    budget = budget_bytes() if _budget_override is None else float(_budget_override)
    res.budget = int(budget) if budget is not None else None
    pre_open_need = footer_disk * _FOOTER_INFLATE + _WRITER_MARGIN_BYTES
    if budget is not None and budget < pre_open_need:
        res.status = "skipped"
        res.reason = (
            f"メモリ不足: ファイルを開くだけで約 {format_bytes(pre_open_need)} 必要"
            f"（フッタ {format_bytes(footer_disk * _FOOTER_INFLATE)} ＋ "
            f"書き込みバッファ {format_bytes(_WRITER_MARGIN_BYTES)}）。"
            f"予算 {format_bytes(budget)}"
        )
        return res

    pf = None
    tmp = None
    try:
        pf = pq.ParquetFile(str(path), memory_map=False)
        # memory_map=True にすると読み出した配列が mmap 領域に依存し、
        # 置換前に元ファイルを掴んだままになる。必ず False。
        md = pf.metadata
        schema = pf.schema_arrow          # ★ メタデータ 3 キーはここに載っている
        res.n_rows = md.num_rows
        res.row_groups_before = md.num_row_groups
        res.footer_before = md.serialized_size or 0

        # --- スキップ判定（安全側・冪等） ---
        if md.num_row_groups <= 1:
            res.status = "skipped"
            res.reason = "既に単一 row group"
            res.row_groups_after = md.num_row_groups
            res.footer_after = res.footer_before
            res.size_after = res.size_before
            return res
        if b"mz_sorted" not in (schema.metadata or {}):
            res.status = "skipped"
            res.reason = "SCiLS 変換出力ではありません（mz_sorted なし）"
            res.size_after = res.size_before
            return res
        if md.num_rows == 0:
            res.status = "skipped"
            res.reason = "行がありません"
            res.size_after = res.size_before
            return res

        # --- メモリ判定 ---
        row_width = _row_width_bytes(schema)
        footer_ram = _footer_ram_bytes(md)
        rg_rows = md.num_rows
        peak = estimate_peak_bytes(
            n_rows=md.num_rows, row_width=row_width, footer_ram=footer_ram)
        res.estimated_peak = int(peak)

        if budget is not None and peak > budget:
            fit_rows = int((budget - footer_ram - _WRITER_MARGIN_BYTES) / max(1, row_width))
            if not allow_split:
                res.status = "skipped"
                if fit_rows >= _MIN_SPLIT_ROWS:
                    n_groups = -(-md.num_rows // fit_rows)
                    res.reason = (
                        f"メモリ不足: 推定 {format_bytes(peak)} / 予算 {format_bytes(budget)}"
                        f"\n      --allow-split を付けると {fit_rows:,} 行 × {n_groups} "
                        f"row group で処理できます"
                        f"（フッタ {format_bytes(res.footer_before)} → 約 "
                        f"{format_bytes(res.footer_before / max(1, md.num_row_groups) * n_groups)}）"
                    )
                else:
                    res.reason = (
                        f"メモリ不足: 推定 {format_bytes(peak)} / 予算 {format_bytes(budget)}。"
                        f"分割しても足りません（フッタだけで {format_bytes(footer_ram)}）"
                    )
                res.size_after = res.size_before
                return res
            if fit_rows < _MIN_SPLIT_ROWS:
                res.reason = (
                    f"メモリ不足: 分割しても足りません"
                    f"（フッタだけで {format_bytes(footer_ram)} / 予算 {format_bytes(budget)}）"
                )
                return res
            rg_rows = max(_MIN_SPLIT_ROWS, fit_rows)
            if progress:
                progress(
                    f"      メモリ予算のため {rg_rows:,} 行 × "
                    f"{-(-md.num_rows // rg_rows)} row group に分割します"
                )

        codec = _detect_codec(md)
        # ★ ver63.1: エンコードも入力から引き継ぐ。未指定だと pyarrow 既定の
        #   全列 True になり、変換器が ver60.0 で強度列から外した辞書エンコードが
        #   再パックで復活する（_detect_dictionary_columns の docstring 参照）。
        use_dict = _detect_dictionary_columns(md, schema)

        # --- dry-run はここまで（書き込まない） ---
        if dry_run:
            res.status = "dry-run"
            res.size_after = res.size_before
            res.row_groups_after = -(-md.num_rows // rg_rows)
            res.reason = (
                f"{md.num_row_groups} → {res.row_groups_after} row group "
                f"(推定ピーク {format_bytes(peak)}"
                + (f" / 予算 {format_bytes(budget)}" if budget is not None else "")
                + ")"
            )
            return res

        # --- 書き込み（iter_batches 単一経路） ---
        tmp = _unique_path(path.parent / (path.name + _TMP_SUFFIX))
        written = 0
        with pq.ParquetWriter(str(tmp), schema, compression=codec,
                              use_dictionary=use_dict) as writer:
            for batch in pf.iter_batches(batch_size=rg_rows):
                table = pa.Table.from_batches([batch], schema=schema)
                # row_group_size は必ず明示する。None 既定は 1,048,576 行で無言分割する。
                writer.write_table(table, row_group_size=table.num_rows)
                written += table.num_rows
                table = None
                batch = None
        if written != md.num_rows:
            res.reason = f"書き込み行数が一致しません（期待 {md.num_rows:,} / 実際 {written:,}）"
            return res

        # --- 検証（元ファイルを置換する前に） ---
        if verify:
            problem = _verify_files(
                pf, tmp, schema, n_rows=md.num_rows, row_width=row_width)
            if problem is not None:
                res.reason = f"検証に失敗: {problem}"
                return res

        dst_md = pq.ParquetFile(str(tmp), memory_map=False)
        try:
            res.row_groups_after = dst_md.metadata.num_row_groups
            res.footer_after = dst_md.metadata.serialized_size or 0
        finally:
            dst_md.close()

        # --- 置換 ---
        pf.close()          # Windows では開いたままだと置換できない
        pf = None
        if backup:
            _make_backup(path)
        os.replace(str(tmp), str(path))
        tmp = None

        res.size_after = path.stat().st_size
        res.status = "repacked"
        return res

    except Exception as e:
        logger.exception("再パックに失敗: %s", path)
        res.status = "error"
        res.reason = f"{type(e).__name__}: {e}"
        return res
    finally:
        res.elapsed_sec = time.time() - t0
        if pf is not None:
            try:
                pf.close()
            except Exception:
                pass
        if tmp is not None and tmp.exists():
            try:
                tmp.unlink()
            except OSError as e:
                logger.warning("書き込み中の一時ファイル削除に失敗: %s", e)


# ---------------------------------------------------------------------------
# フォルダ一括処理（CLI 本体）
# ---------------------------------------------------------------------------

DEFAULT_INCLUDE = "*.parquet"


def _match_any(name: str, patterns: list) -> bool:
    import fnmatch
    return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)


def find_targets(folder: Path, patterns: list, *, recursive: bool = True) -> list:
    """対象ファイルを探す。サイドカーと一時ファイルは常に除外。"""
    from app.services.data_manager import _SIDECAR_SUFFIX

    globber = folder.rglob if recursive else folder.glob
    out = []
    for p in globber("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".parquet", ".pq"):
            continue
        if p.name.endswith(_SIDECAR_SUFFIX):
            continue
        if not _match_any(p.name, patterns):
            continue
        out.append(p)
    return sorted(out)


def repack_folder(
    folder: Path,
    *,
    patterns: Optional[list] = None,
    dry_run: bool = False,
    backup: bool = True,
    verify: bool = True,
    allow_split: bool = False,
    recursive: bool = True,
    emit: Optional[Callable[[str], None]] = None,
) -> FolderResult:
    """フォルダ配下の .parquet を一括で再パックする。

    1 ファイルの失敗が全体を止めることはない（エラーを数えて次へ進む）。
    出力は rds_maintenance_callbacks の `_parse_summary` が読める体裁に揃える。
    """
    out = emit or (lambda s: print(s, flush=True))
    pats = patterns or [DEFAULT_INCLUDE]
    t0 = time.time()
    agg = FolderResult()

    out(f"[repack] Scanning {folder}")
    out(f"[repack] Include patterns: {','.join(pats)}")
    out(
        f"[repack] Dry-run: {dry_run} | Backup: {backup} | "
        f"Verify: {verify} | Allow-split: {allow_split}"
    )
    budget = budget_bytes()
    out(
        "[repack] Memory budget: "
        + (format_bytes(budget) if budget is not None else "不明（判定をスキップします）")
    )
    out("")

    for stale in sweep_stale_temps(folder, recursive=recursive):
        out(f"[repack] 前回の中断で残った一時ファイルを削除: {stale.name}")

    targets = find_targets(folder, pats, recursive=recursive)
    out(f"[repack] {len(targets)} files matched.")
    out("")
    if not targets:
        out("[repack] 該当ファイルなし。")
        return agg

    for i, fp in enumerate(targets, 1):
        try:
            size_before = fp.stat().st_size
        except OSError:
            size_before = 0
        tag = f"[{i}/{len(targets)}] {fp.name} ({format_bytes(size_before)})"
        res = repack_file(
            fp, dry_run=dry_run, backup=backup, verify=verify,
            allow_split=allow_split, progress=out,
        )
        agg.results.append(res)

        if res.status == "repacked":
            delta = 100 * (1 - res.size_after / res.size_before) if res.size_before else 0.0
            out(
                f"{tag} -> {format_bytes(res.size_after)} ({-delta:+.1f}%) "
                f"row group {res.row_groups_before} → {res.row_groups_after}, "
                f"フッタ {format_bytes(res.footer_before)} → "
                f"{format_bytes(res.footer_after)}, {res.elapsed_sec:.1f}s"
            )
        elif res.status == "dry-run":
            out(f"{tag} -> {res.reason} (dry-run)")
        elif res.status == "skipped":
            out(f"{tag} -> skip ({res.reason})")
        else:
            out(f"{tag} -> ERROR ({res.reason})")
            agg.errors.append(f"{fp}: {res.reason}")

    dt = time.time() - t0
    before, after = agg.size_before, agg.size_after
    out("")
    out("[repack] ============================================")
    out(f"[repack] Processed : {agg.n_processed}")
    out(f"[repack] Skipped   : {agg.n_skipped}")
    out(f"[repack] Errors    : {agg.n_error}")
    out(f"[repack] Size before: {format_bytes(before)}")
    # dry-run では書き込んでいないので「後サイズ」も「削減率」も出さない。
    # 0.0% と表示すると「効果が無い」と読めてしまう。
    if not dry_run:
        out(f"[repack] Size after : {format_bytes(after)}")
        if before > 0:
            out(f"[repack] Reduction  : {100 * (1 - after / before):.1f}%")
    out(f"[repack] Elapsed    : {dt:.1f} sec")
    if agg.errors:
        # 見出しはコロン前に空白を置かない。`Errors\s+:` の正規表現が
        # この行に誤ってマッチしないようにするため（slim と同じ非対称）。
        out("")
        out("[repack] Errors:")
        for e in agg.errors:
            out(f"  - {e}")
    out("[repack] ============================================")
    return agg
