#!/usr/bin/env python3
# =============================================================================
# MSI Analysis Application - SCiLS 変換ベンチマーク CLI
#
# 合成 SCiLS フォルダ (Intensity CSV + Spot CSV) を作って変換し、
# Phase A / Phase B / 全体の所要秒数とピーク RSS を表に出す。
#
# Usage:
#   python -u App/tools/bench_scils_convert.py --spots 20000 --mz 1000
#
# Options:
#   --spots N        spot 数 (= Intensity CSV の列数 - 1)。既定 20000
#   --mz N           m/z 数 (= Intensity CSV の行数)。既定 1000
#   --spot-block N   convert_scils_to_parquet(spot_block=N)。既定は変換器の既定値
#   --float64        store_float32=False で変換する（既定は float32）
#   --no-polars      SCILS_NO_POLARS=1 で pyarrow フォールバック経路を測る
#   --repeat N       N 回変換して各回の秒数を出す。既定 1
#   --workdir DIR    合成データの置き場所。既定はテンポラリ（終了時に削除）
#   --keep           --workdir を消さずに残す
#   --verify-against FILE   出力を既存 Parquet と全列ビット比較する
#   --csv-only       CSV を生成するだけで変換しない（別プロセスで測りたいとき）
#
# 終了コード: 0 = 正常 / 1 = 引数エラー / 2 = 変換失敗 / 3 = 検証不一致
#
# なぜ要るか: このリポジトリには変換の性能テストも実データ fixture も無く
# （テストの最大でも 2,000 m/z × 6 spot）、Phase A / Phase B の退行を検出する
# 手段が一つも無い。ver49.0 の CHANGELOG が載せている表と同じ粒度を、
# 誰でも手元で再現できるようにする。
#
# 注意: GUI やファイルへリダイレクトして使う場合は `-u` を付けること
#       （repack_parquet_rowgroups.py と同じ理由。ブロックバッファで進捗が出ない）。
# =============================================================================

import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

# App/ を import path に載せる（App/tools/x.py → parents[1] == App/）。
# repack_parquet_rowgroups.py と同じ方式で、シェルからも GUI からも同じ挙動になる。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# 合成データ生成
# ---------------------------------------------------------------------------

def _value_pool(n: int) -> list[str]:
    """CSV セルに使う実測相当の桁数（約 11 バイト）の数値文字列を n 個作る。

    `_AVG_CSV_CELL_BYTES = 11` は変換器がメモリ見積りに使っている実測値なので、
    そこから外れる幅の値を使うと CSV サイズと n_mz 推定の関係が現実と変わる。
    """
    # 単調増加させて全要素を相異なる値にする（列方向の圧縮率が現実離れしないように）。
    return [f"{1000.0 + i * 0.0137:.4f}" for i in range(n)]


def write_synthetic_folder(folder: Path, n_spots: int, n_mz: int) -> Path:
    """Intensity CSV と Spot CSV を書き出し、Intensity のパスを返す。

    Intensity の 1 行を `pool[i:i+n_spots]` の join で作る。こうすると
    **列 c の値が行ごとに全部違う**（pool[i+c] が i で動く）ので、
    「同じ行を使い回して圧縮が非現実的に効く」ことを避けつつ、
    1 行あたり Python の join 1 回で済んで生成が速い。
    """
    folder.mkdir(parents=True, exist_ok=True)
    intensity = folder / "BENCH_Intensity.csv"
    spot = folder / "BENCH_Spot.csv"

    pool = _value_pool(n_spots + n_mz)

    header = "m/z," + ",".join(f"Spot {i + 1}" for i in range(n_spots))
    # m/z は昇順にしない。変換器の argsort / order_mz 経路を必ず通すため。
    mz_values = [200.0 + ((i * 7919) % max(1, n_mz)) * 0.01 for i in range(n_mz)]
    with intensity.open("w", encoding="utf-8", newline="") as f:
        f.write(header + "\n")
        for i in range(n_mz):
            f.write(f"{mz_values[i]:.6f}," + ",".join(pool[i:i + n_spots]) + "\n")

    # spot は正方形に近いグリッドに並べる（実データの走査順に近づける）。
    width = max(1, int(n_spots ** 0.5))
    with spot.open("w", encoding="utf-8", newline="") as f:
        f.write("SpotIndex,X,Y\n")
        for i in range(n_spots):
            f.write(f"{i + 1},{i % width},{i // width}\n")

    return intensity


# ---------------------------------------------------------------------------
# 計測
# ---------------------------------------------------------------------------

class _PhaseLogCapture(logging.Handler):
    """変換器が既に出している Phase A / Phase B の秒数ログを拾う。

    変換器側に計測コードを足さずに済ませる。`scils_converter.py` は
    `"Phase A 完了: %.1f 秒"` / `"Phase B 完了: %.1f 秒"` を INFO で出している。
    """

    _PAT = re.compile(r"Phase ([AB]) 完了: ([0-9.]+) 秒")

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.phases: dict[str, float] = {}
        self.lines: list[str] = []

    def emit(self, record):
        msg = record.getMessage()
        self.lines.append(msg)
        m = self._PAT.search(msg)
        if m:
            self.phases[m.group(1)] = float(m.group(2))


class _RssSampler(threading.Thread):
    """/proc/self/status の VmRSS を一定間隔で見てピークを取る。

    `resource.getrusage` の ru_maxrss はプロセス寿命全体の最大なので、
    --repeat で 2 回目以降の実測にならない。
    """

    def __init__(self, interval: float = 0.05):
        super().__init__(daemon=True)
        self.interval = interval
        self.peak_kb = 0
        self._stop_evt = threading.Event()

    def _read(self) -> int:
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
        except OSError:
            pass
        return 0

    def run(self):
        while not self._stop_evt.is_set():
            self.peak_kb = max(self.peak_kb, self._read())
            self._stop_evt.wait(self.interval)
        self.peak_kb = max(self.peak_kb, self._read())

    def stop(self) -> float:
        self._stop_evt.set()
        self.join(timeout=2.0)
        return self.peak_kb / (1024 ** 2)   # GB


def compare_parquet_columns(path_a: Path, path_b: Path) -> list[str]:
    """2 つの Parquet を全列 1 本ずつ比較し、食い違いの説明を返す（空 = 一致）。

    `Table.equals` は使わない。IEEE 比較のため NaN を 1 つでも含むと必ず False に
    なるので「値は同じなのに不一致」と報告してしまう（ver49.0 の検証と同じ理由）。
    """
    import numpy as np
    import pyarrow.parquet as pq

    diffs: list[str] = []
    pa_f, pb_f = pq.ParquetFile(str(path_a)), pq.ParquetFile(str(path_b))
    names_a, names_b = pa_f.schema_arrow.names, pb_f.schema_arrow.names
    if names_a != names_b:
        only_a = [n for n in names_a if n not in set(names_b)][:5]
        only_b = [n for n in names_b if n not in set(names_a)][:5]
        diffs.append(f"列名が違う: {len(names_a)} 列 vs {len(names_b)} 列 / "
                     f"a のみ {only_a} / b のみ {only_b}")
        return diffs

    md_a, md_b = pa_f.schema_arrow.metadata or {}, pb_f.schema_arrow.metadata or {}
    for key in set(md_a) | set(md_b):
        if md_a.get(key) != md_b.get(key):
            diffs.append(f"スキーマメタデータ {key!r} が違う")

    for name in names_a:
        col_a = pa_f.read(columns=[name]).column(0).to_numpy(zero_copy_only=False)
        col_b = pb_f.read(columns=[name]).column(0).to_numpy(zero_copy_only=False)
        if col_a.dtype.kind in "fc" and col_b.dtype.kind in "fc":
            same = np.array_equal(col_a, col_b, equal_nan=True)
        else:
            same = np.array_equal(col_a, col_b)
        if not same:
            diffs.append(f"列 {name!r} の値が違う")
            if len(diffs) >= 10:
                diffs.append("... (10 件で打ち切り)")
                break
    return diffs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv):
    opts = {
        "spots": 20000, "mz": 1000, "spot_block": None, "float32": True,
        "no_polars": False, "repeat": 1, "workdir": None, "keep": False,
        "verify_against": None, "csv_only": False,
    }
    int_keys = {"--spots": "spots", "--mz": "mz",
                "--spot-block": "spot_block", "--repeat": "repeat"}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in int_keys:
            i += 1
            if i >= len(argv):
                raise SystemExit(f"{a} に値がありません")
            opts[int_keys[a]] = int(argv[i])
        elif a.startswith("--spots=") or a.startswith("--mz=") \
                or a.startswith("--spot-block=") or a.startswith("--repeat="):
            key, _, val = a.partition("=")
            opts[int_keys[key]] = int(val)
        elif a == "--float64":
            opts["float32"] = False
        elif a == "--no-polars":
            opts["no_polars"] = True
        elif a == "--keep":
            opts["keep"] = True
        elif a == "--csv-only":
            opts["csv_only"] = True
        elif a == "--workdir":
            i += 1
            opts["workdir"] = argv[i]
        elif a.startswith("--workdir="):
            opts["workdir"] = a.split("=", 1)[1]
        elif a == "--verify-against":
            i += 1
            opts["verify_against"] = argv[i]
        elif a.startswith("--verify-against="):
            opts["verify_against"] = a.split("=", 1)[1]
        elif a in ("-h", "--help"):
            print(__doc__ or "", file=sys.stderr)
            raise SystemExit(0)
        else:
            raise SystemExit(f"不明な引数: {a}")
        i += 1
    return opts


def main(argv):
    try:
        opts = parse_args(argv)
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 1
        raise

    if opts["no_polars"]:
        os.environ["SCILS_NO_POLARS"] = "1"

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    cap = _PhaseLogCapture()
    logging.getLogger("msi.scils_converter").addHandler(cap)
    logging.getLogger("msi.scils_converter").setLevel(logging.INFO)
    # root へ伝播させない。ログが表に混ざると読めないので、捕捉ハンドラだけが受ける
    # （失敗時は cap.lines の末尾を出す）。
    logging.getLogger("msi.scils_converter").propagate = False

    tmp_created = opts["workdir"] is None
    workdir = Path(opts["workdir"]) if opts["workdir"] else Path(
        tempfile.mkdtemp(prefix="bench_scils_"))
    src = workdir / "src"

    try:
        print(f"合成データ生成: {opts['spots']:,} spot × {opts['mz']:,} m/z → {src}")
        t0 = time.perf_counter()
        intensity = write_synthetic_folder(src, opts["spots"], opts["mz"])
        csv_gb = intensity.stat().st_size / (1024 ** 3)
        print(f"  Intensity CSV {csv_gb:.3f} GB / 生成 {time.perf_counter() - t0:.1f} 秒")
        if opts["csv_only"]:
            print(f"--csv-only 指定のため変換しません。フォルダ: {src}")
            return 0

        from app.services.scils_converter import convert_scils_to_parquet

        engine = "pyarrow(fallback)" if opts["no_polars"] else "polars"
        dtype = "float32" if opts["float32"] else "float64"
        block = opts["spot_block"]
        print(f"エンジン={engine} / 保存={dtype} / spot_block="
              f"{block if block is not None else '既定'}")
        print()
        print(f"{'#':>3} {'Phase A':>10} {'Phase B':>10} {'合計':>10} {'ピーク RSS':>12}")
        print("-" * 50)

        out_path = None
        for run in range(1, opts["repeat"] + 1):
            cap.phases.clear()
            out_path = workdir / f"bench_{run}.parquet"
            kwargs = dict(store_float32=opts["float32"], organize=False)
            if block is not None:
                kwargs["spot_block"] = block
            sampler = _RssSampler()
            sampler.start()
            try:
                result = convert_scils_to_parquet(str(src), str(out_path), **kwargs)
            except Exception as exc:
                sampler.stop()
                print(f"変換に失敗しました: {exc}", file=sys.stderr)
                for line in cap.lines[-15:]:
                    print(f"  | {line}", file=sys.stderr)
                return 2
            peak_gb = sampler.stop()
            print(f"{run:>3} {cap.phases.get('A', float('nan')):>9.1f}s "
                  f"{cap.phases.get('B', float('nan')):>9.1f}s "
                  f"{result.duration_sec:>9.1f}s {peak_gb:>10.2f} GB")

        print()
        print(f"出力: {out_path}  "
              f"({out_path.stat().st_size / (1024 ** 2):.1f} MB / "
              f"{result.n_spots:,} spot × {result.n_mz_features:,} m/z / "
              f"row group {result.n_row_groups} × {result.row_group_rows:,} 行 / "
              f"フッタ {result.footer_bytes / (1024 ** 2):.2f} MB)")
        for w in result.warnings:
            print(f"  警告: {w}")

        if opts["verify_against"]:
            ref = Path(opts["verify_against"])
            print(f"\n全列ビット比較: {ref.name} と照合中…")
            diffs = compare_parquet_columns(ref, out_path)
            if diffs:
                print("不一致:", file=sys.stderr)
                for d in diffs:
                    print(f"  - {d}", file=sys.stderr)
                return 3
            print("  一致（全列・スキーマメタデータとも）")
        return 0
    finally:
        if tmp_created and not opts["keep"]:
            shutil.rmtree(workdir, ignore_errors=True)
        elif opts["keep"]:
            print(f"\n--keep 指定のため残しました: {workdir}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
