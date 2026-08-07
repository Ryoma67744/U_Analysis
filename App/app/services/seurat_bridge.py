# =============================================================================
# MSI Analysis Application - Seurat Bridge
# Seurat RDS → Parquet/CSV 変換管理
# =============================================================================

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from app.config import R_HELPERS_DIR, RSCRIPT_PATH, SEURAT_CACHE_DIR

logger = logging.getLogger("msi.seurat_bridge")

# =============================================================================
# expression_matrix.parquet の読み出しキャッシュ (3 段)
# =============================================================================
# ① _PARQUET_SCHEMA_CACHE … 列名の set        (ver46.1)
# ② _PARQUET_FILE_CACHE   … ParquetFile ハンドル = 解析済みフッタ
# ③ _FEATURE_COL_CACHE    … 復号済みの feature 列そのもの
#
# ①だけでは足りなかった。列名の判定は速くなったが、その直後の
# `pd.read_parquet` がファイルを開き直して **約 18,000 列のフッタを毎回
# パースし直していた**。さらに 1 回の m/z 切替で update_feature_plot と
# update_feature_violin が独立に同じ列を読むので、その固定費を 2 回払っていた。
#
# ②でフッタ解析をキーにつき 1 回に、③で「一度見た m/z に戻る」を無料にする。
# ③には in-flight 共有を入れて、同時に走る複数コールバックが 1 回の読みを
# 分け合うようにする (同じ列を 2 本同時に読んでも中身は同一で、片方は丸ごと無駄)。
#
# キーはいずれも (path, mtime_ns, size) なのでファイル差し替えで自動失効する。
_PARQUET_SCHEMA_CACHE: "OrderedDict[tuple, set]" = OrderedDict()
_PARQUET_SCHEMA_CACHE_MAX = int(os.environ.get("PARQUET_SCHEMA_CACHE_MAX", 8))
_PARQUET_SCHEMA_LOCK = threading.Lock()

# ParquetFile は解析済みフッタを抱えるので上限は小さく。実運用で同時に見るのは
# 1 プロジェクト = 1 ファイル。
_PARQUET_FILE_CACHE: "OrderedDict[tuple, tuple]" = OrderedDict()
_PARQUET_FILE_CACHE_MAX = int(os.environ.get("PARQUET_FILE_CACHE_MAX", 4))
_PARQUET_FILE_LOCK = threading.Lock()

# 1 列 = 203k 行 × float64 ≈ 1.6MB。既定 16 枚で約 26MB。
# ワーカーは 1 プロセスなので (run_app.py:121-127)、ここは素直にプロセス内で持つ。
_FEATURE_COL_CACHE: "OrderedDict[tuple, pd.Series]" = OrderedDict()
_FEATURE_COL_CACHE_MAX = int(os.environ.get("FEATURE_COL_CACHE_MAX", 16))
_FEATURE_COL_LOCK = threading.Lock()
_FEATURE_COL_INFLIGHT: "dict[tuple, threading.Event]" = {}
# 先行の列読みを待つ上限。超えたら自分で読みに行く (無言で None を返すと
# 呼び出し元が R subprocess フォールバック 30〜300 秒に落ちてしまう)。
_FEATURE_COL_WAIT_SEC = float(os.environ.get("FEATURE_COL_WAIT_SEC", 120))


def _parquet_file_sig(path: Path) -> tuple:
    """(path, mtime_ns, size)。stat に失敗したら OSError を投げる。"""
    st = path.stat()
    return (str(path), st.st_mtime_ns, st.st_size)


def _get_parquet_handle(expr_path: Path, key: tuple) -> tuple:
    """(ParquetFile, RLock) を返す。フッタ解析はキーにつき 1 回だけ。

    pyarrow の ParquetFile は thread-safe を保証していないため、読み出しを
    直列化するための lock を handle と一緒に持たせる。waitress は 8 スレッド
    (run_app.py) なので、別々の列を同時に要求される経路が実在する。
    """
    with _PARQUET_FILE_LOCK:
        hit = _PARQUET_FILE_CACHE.get(key)
        if hit is not None:
            _PARQUET_FILE_CACHE.move_to_end(key)
            return hit

    # フッタ解析は数百 ms〜秒かかりうるので、グローバルロックの外で行う。
    # 競走して 2 本作られても、負けた方を捨てるだけで結果は変わらない。
    import pyarrow.parquet as pq
    entry = (pq.ParquetFile(str(expr_path)), threading.RLock())

    with _PARQUET_FILE_LOCK:
        exist = _PARQUET_FILE_CACHE.get(key)
        if exist is not None:
            _PARQUET_FILE_CACHE.move_to_end(key)
            return exist
        _PARQUET_FILE_CACHE[key] = entry
        _PARQUET_FILE_CACHE.move_to_end(key)
        while len(_PARQUET_FILE_CACHE) > _PARQUET_FILE_CACHE_MAX:
            _PARQUET_FILE_CACHE.popitem(last=False)
    return entry


def _read_parquet_columns(entry: tuple, columns: list) -> pd.DataFrame:
    """ハンドル経由で列を読む。`pd.read_parquet(path, columns=...)` と等価。

    ParquetFile.read() は dataset API を通らないので、列名が重複していても
    KeyError にならない (test_parquet_repack.py:82-85 が触れている pq.read_table
    の弱点を踏まない)。
    """
    pf, lock = entry
    with lock:
        table = pf.read(columns=list(columns))
    return table.to_pandas()


def _get_feature_column(expr_path: Path, key: tuple, feature_name) -> pd.Series:
    """feature 列を LRU + in-flight 共有つきで読む。呼び出し元には複製を返す。

    複製を返すのは、呼び出し元が DataFrame に入れる前後で書き換えても
    キャッシュが汚れないようにするため (1.6MB の memcpy は parquet の
    復号に比べれば無視できる)。
    """
    ck = key + (feature_name,)

    with _FEATURE_COL_LOCK:
        hit = _FEATURE_COL_CACHE.get(ck)
        if hit is not None:
            _FEATURE_COL_CACHE.move_to_end(ck)
            return hit.copy()
        ev = _FEATURE_COL_INFLIGHT.get(ck)
        leader = ev is None
        if leader:
            ev = threading.Event()
            _FEATURE_COL_INFLIGHT[ck] = ev

    if not leader:
        # 同じ m/z 切替で走っている先行の読みに相乗りする。
        ev.wait(timeout=_FEATURE_COL_WAIT_SEC)
        with _FEATURE_COL_LOCK:
            hit = _FEATURE_COL_CACHE.get(ck)
            if hit is not None:
                _FEATURE_COL_CACHE.move_to_end(ck)
                return hit.copy()
        # 先行が失敗した / 間に合わなかった場合は自分で読む (稀)。
        return _read_parquet_columns(
            _get_parquet_handle(expr_path, key), [feature_name])[feature_name]

    try:
        series = _read_parquet_columns(
            _get_parquet_handle(expr_path, key), [feature_name])[feature_name]
        # ★ 待っている側が起きる前に載せる。順序を逆にすると、
        #   follower が空のキャッシュを見て全員で読み直すことになる。
        with _FEATURE_COL_LOCK:
            _FEATURE_COL_CACHE[ck] = series
            _FEATURE_COL_CACHE.move_to_end(ck)
            while len(_FEATURE_COL_CACHE) > _FEATURE_COL_CACHE_MAX:
                _FEATURE_COL_CACHE.popitem(last=False)
    finally:
        with _FEATURE_COL_LOCK:
            _FEATURE_COL_INFLIGHT.pop(ck, None)
        ev.set()
    return series.copy()


def clear_expression_caches() -> None:
    """expression_matrix.parquet 系のキャッシュを全部捨てる (テスト / 保守用)。"""
    with _PARQUET_SCHEMA_LOCK:
        _PARQUET_SCHEMA_CACHE.clear()
    with _PARQUET_FILE_LOCK:
        _PARQUET_FILE_CACHE.clear()
    with _FEATURE_COL_LOCK:
        _FEATURE_COL_CACHE.clear()


class ExtractionCancelled(Exception):
    """ユーザーが RDS 抽出をキャンセルしたときに送出される。"""


def _none_str(v):
    """NaN / None / 空 / "None" を None に正規化、それ以外は str を返す。"""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return None if (s == "" or s.lower() == "none") else s


def _popen_with_cancel(cmd, cancel_event, timeout=600, creationflags=0):
    """cmd を Popen で起動し、0.3 秒ごとに cancel_event を監視する。

    cancel_event がセットされたらサブプロセスを kill し ExtractionCancelled を
    送出する。timeout 超過時は RuntimeError("__TIMEOUT__") を送出。
    Returns: (returncode, stdout_bytes, stderr_bytes)

    [ver50.1] stdout も返すようにした。R 側が各段の所要時間を [extract] 行として
    stdout に出すため、捨てているとキャンセル可能パスだけ内訳が追えなくなる。
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    start = time.monotonic()
    while True:
        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=0.3)
            return proc.returncode, stdout_bytes, stderr_bytes
        except subprocess.TimeoutExpired:
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                raise ExtractionCancelled()
            if time.monotonic() - start > timeout:
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
                raise RuntimeError("__TIMEOUT__")

# Seurat キャッシュの LRU 上限。新規エントリ追加時に超過分は古い順に削除。
# /tmp/msi_seurat_cache が無制限に膨らむのを防ぐ。
SEURAT_CACHE_MAX_ENTRIES = int(os.environ.get("SEURAT_CACHE_MAX_ENTRIES", 12))


def _evict_seurat_cache_lru(cache_base: Path, max_entries: int) -> int:
    """SEURAT_CACHE_DIR 内のサブディレクトリを mtime 降順で max_entries 件まで残し、
    それより古い (mtime 小) サブディレクトリを物理削除する。

    Returns: 削除したサブディレクトリ数
    """
    try:
        if not cache_base.exists():
            return 0
        sub_dirs = []
        for child in cache_base.iterdir():
            if child.is_dir():
                try:
                    mt = child.stat().st_mtime
                except OSError:
                    mt = 0
                sub_dirs.append((mt, child))
        if len(sub_dirs) <= max_entries:
            return 0
        # mtime 昇順 (古いものから) で並べる
        sub_dirs.sort(key=lambda t: t[0])
        to_evict = sub_dirs[: len(sub_dirs) - max_entries]
        removed = 0
        for mt, path in to_evict:
            try:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
                logger.info("Evicted stale Seurat cache: %s (age=%.0fs)",
                            path.name, time.time() - mt)
            except Exception as e:
                logger.warning("Failed to evict %s: %s", path.name, e)
        return removed
    except Exception as e:
        logger.warning("LRU eviction error: %s", e)
        return 0


class SeuratBridge:
    """Seurat RDS ファイルを R ヘルパースクリプト経由で
    Parquet/CSV に変換し、pandas で読み込む。
    """

    def __init__(self):
        self._cache_base = SEURAT_CACHE_DIR

    def _get_cache_key(self, rds_path: str) -> str:
        """RDSファイルパス + 更新日時 + Rスクリプト更新日時からキャッシュキーを生成"""
        p = Path(rds_path)
        mtime = p.stat().st_mtime if p.exists() else 0
        # Rスクリプト更新時にもキャッシュを再生成するため、スクリプトのmtimeも含める
        r_script = R_HELPERS_DIR / "extract_seurat_data.R"
        r_mtime = r_script.stat().st_mtime if r_script.exists() else 0
        raw = f"{rds_path}|{mtime}|{r_mtime}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    def _get_cache_dir(self, rds_path: str) -> Path:
        key = self._get_cache_key(rds_path)
        cache_dir = self._cache_base / key
        is_new = not cache_dir.exists()
        cache_dir.mkdir(parents=True, exist_ok=True)
        # 新規キャッシュ作成時に LRU evict をトリガー (頻繁すぎず適度)
        if is_new and SEURAT_CACHE_MAX_ENTRIES > 0:
            try:
                _evict_seurat_cache_lru(self._cache_base, SEURAT_CACHE_MAX_ENTRIES)
            except Exception as e:
                logger.debug("LRU eviction failed (non-critical): %s", e)
        return cache_dir

    def get_cache_dir(self, rds_path: str) -> Path:
        """外部からキャッシュディレクトリを参照（Parquet直接読み込み用）"""
        return self._get_cache_dir(rds_path)

    def _is_cached(self, cache_dir: Path) -> bool:
        """キャッシュ済みかチェック（必須ファイルが全て揃っているか）"""
        required = ["extraction_meta.json", "cluster_stats.csv"]
        # plot_data は parquet or csv のどちらか
        has_plot = (cache_dir / "plot_data.parquet").exists() or (cache_dir / "plot_data.csv").exists()
        return has_plot and all((cache_dir / f).exists() for f in required)

    def extract_data(self, rds_path: str, with_expression: bool = False,
                     cancel_event=None) -> dict:
        """Seurat RDS からデータを抽出。キャッシュがあればそれを使用。

        Args:
            rds_path: RDSファイルパス
            with_expression: True なら expression_matrix.parquet も生成
                （初回データロードでは省略推奨、Feature plot/m/z キャリブで必要時のみ True）

                [ver50.1] 実測 (203,078 cell x 1,536 feature / コンテナ 12GB):
                抽出全体で 233.7 秒。内訳は RDS 展開 118.7 秒（xz。qs が使えれば
                5〜15 秒の見込み）、発現行列の生成 87.4 秒、その他 27.6 秒。
                旧コメントの「20-60 秒」は実測と 4〜11 倍乖離していた。
                各段の秒数は `[extract]` 行としてアプリログに出る。

        Returns:
            {
                "plot_data": pd.DataFrame,
                "cluster_stats": pd.DataFrame,
                "features_list": list[str],
                "meta": dict,
                "cache_dir": Path,
            }
        """
        from app.utils.file_locks import get_or_create_lock
        cache_dir = self._get_cache_dir(rds_path)

        # ver4.4: 同一 RDS への同時初回アクセス (受信者オープン + 共有生成時の
        # プリウォーム等) で R 抽出が二重に走らないよう排他。ロック取得後に
        # 再チェックし、先行プロセスが既に抽出済みならスキップする。
        if not self._is_cached(cache_dir):
            lock = get_or_create_lock(cache_dir / "extract", timeout=600)
            with lock:
                if not self._is_cached(cache_dir):
                    self._run_extraction(rds_path, cache_dir, with_expression=with_expression,
                                         cancel_event=cancel_event)

        try:
            result = self._load_extracted_data(cache_dir)
        except Exception:
            # キャッシュ破損の可能性 → 削除して再抽出
            shutil.rmtree(cache_dir, ignore_errors=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._run_extraction(rds_path, cache_dir, with_expression=with_expression,
                                 cancel_event=cancel_event)
            result = self._load_extracted_data(cache_dir)

        result["cache_dir"] = cache_dir
        result["feature_annotations"] = self._load_feature_annotations(
            cache_dir, rds_path, result.get("features_list") or []
        )
        return result

    def ensure_expression_matrix(self, rds_path: str) -> Path:
        """expression_matrix.parquet を必要時に生成して Path を返す。

        既に存在する（過去セッションで生成済み or 明示的に with_expression=True で
        生成済み）場合は即座に返す。不在の場合は R 抽出を再実行して生成する。

        Feature plot や m/z キャリブレーション callback の頭で呼び出すと、
        初回の重いコストを「ユーザーがその機能を使ったとき」に限定できる。

        複数ユーザーが同一 RDS の Feature plot を同時に初めて開いても、
        FileLock により R 抽出は 1 回のみ実行され、後発プロセスは生成完了を待つ。
        """
        from app.utils.file_locks import get_or_create_lock
        cache_dir = self._get_cache_dir(rds_path)
        parquet_path = cache_dir / "expression_matrix.parquet"
        if parquet_path.exists():
            logger.debug("expression_matrix キャッシュヒット: %s", cache_dir.name)
            return parquet_path
        # 不在 → 排他取得して生成（R 抽出最大 10 分 → timeout=900）
        logger.info(
            "expression_matrix 不在のため生成します: %s (数分かかります)",
            Path(rds_path).name,
        )
        lock = get_or_create_lock(parquet_path, timeout=900)
        with lock:
            # ロック取得後に再チェック（先行プロセスが既に生成完了している可能性）
            if parquet_path.exists():
                logger.info("他プロセスが生成を完了していました: %s", cache_dir.name)
                return parquet_path
            self._run_extraction(rds_path, cache_dir, with_expression=True)
        if not parquet_path.exists():
            raise RuntimeError(
                f"expression_matrix.parquet の生成に失敗しました: {parquet_path}"
            )
        return parquet_path

    def get_feature_expression(
        self, rds_path: str, feature_name: str
    ) -> pd.Series:
        """単一 Feature の発現量を取得（R subprocess fallback）"""
        cache_dir = self._get_cache_dir(rds_path)
        feature_file = cache_dir / f"feature_{feature_name}.csv"

        if not feature_file.exists():
            self._run_feature_extraction(rds_path, feature_name, feature_file)

        df = pd.read_csv(feature_file, header=None)
        return df.iloc[:, 0]

    @staticmethod
    def _parquet_column_names(expr_path: Path):
        """parquet の列名 set を (path, mtime, size) キーでキャッシュして返す。

        ver46.1: expression_matrix.parquet は約 18,000 列あり、フッタの
        パースだけで無視できないコストになる。ファイルが差し替われば
        mtime/size が変わるのでキャッシュは自動的に無効化される。
        失敗時 None（呼び出し元は「判定不能」として通常経路を続ける）。
        """
        try:
            key = _parquet_file_sig(expr_path)
        except OSError:
            return None
        with _PARQUET_SCHEMA_LOCK:
            hit = _PARQUET_SCHEMA_CACHE.get(key)
            if hit is not None:
                _PARQUET_SCHEMA_CACHE.move_to_end(key)
                return hit
        try:
            # ver51.3: ここで開いたハンドルは列読みでも使い回す。従来は
            # スキーマ用に 1 回、直後の read_parquet でもう 1 回フッタを
            # パースしていた。
            names = set(_get_parquet_handle(expr_path, key)[0].schema.names)
        except Exception as e:  # noqa: BLE001
            logger.debug("parquet スキーマ読取に失敗: %s", e)
            return None
        with _PARQUET_SCHEMA_LOCK:
            _PARQUET_SCHEMA_CACHE[key] = names
            _PARQUET_SCHEMA_CACHE.move_to_end(key)
            while len(_PARQUET_SCHEMA_CACHE) > _PARQUET_SCHEMA_CACHE_MAX:
                _PARQUET_SCHEMA_CACHE.popitem(last=False)
        return names

    def get_feature_expression_fast(
        self, cache_dir: Path, feature_name: str
    ) -> Optional[pd.Series]:
        """Parquet 発現量マトリクスから単一 Feature を高速取得。

        expression_matrix.parquet が存在する場合、指定カラムのみ読み込む。
        存在しない場合は None を返す（呼び出し元で R fallback を使用）。

        ver46.1: 列名の有無を **キャッシュ済みスキーマ** で先に判定する。
        expression_matrix.parquet は約 18,000 列あり、`pd.read_parquet` は
        1 回の呼び出しごとに全列ぶんのフッタ (schema + column chunk metadata) を
        パースする。Feature を切り替えるたびに 3 つのコールバックが独立に
        読んでいたため、ここが数百 ms〜数秒の固定費になっていた。

        ver51.3: そのフッタ再パースを ParquetFile ハンドルの保持で消し、
        復号済みの列そのものも LRU に載せる。同じ m/z へ戻る操作が
        ファイル I/O ゼロになり、同時に走る複数コールバックは in-flight
        共有で 1 回の読みを分け合う。
        """
        expr_path = Path(cache_dir) / "expression_matrix.parquet"
        if not expr_path.exists():
            return None

        # 列名の不一致は「無い」ことを先に判定する。以前は read_parquet に投げて
        # 例外で握りつぶしていたため、呼び出し元が R subprocess の
        # フォールバック (30〜300 秒) に落ちてもログに何も残らなかった。
        names = self._parquet_column_names(expr_path)
        if names is not None and str(feature_name) not in names:
            logger.warning(
                "expression_matrix.parquet に列 %r が無い (R フォールバックへ): %s",
                feature_name, expr_path)
            return None

        try:
            key = _parquet_file_sig(expr_path)
        except OSError:
            # stat できない = キャッシュキーを作れない。従来経路で読む。
            key = None
        try:
            if key is None:
                return pd.read_parquet(
                    expr_path, columns=[feature_name])[feature_name]
            return _get_feature_column(expr_path, key, feature_name)
        except Exception as e:  # noqa: BLE001
            logger.warning("Feature %r の parquet 読込に失敗 (R フォールバックへ): %s",
                           feature_name, e)
            return None

    def get_feature_means(self, cache_dir, feature_names):
        """指定した feature 列**だけ**を読んで列平均を返す (ver51.5)。

        キャリブレーションは全 feature の平均スペクトルを作っていたが、実際に
        参照するのは参照 m/z の ±search_window 内にある列だけだった
        (`interactive_calibration._calibrate_mz` の `within_window` ループ)。
        にもかかわらず `pd.read_parquet(expr_path)` を列指定なしで呼んでおり、
        実データ規模 (203,078 行 × 1,536 列 float64) で **1 回 2.32GB**、
        18,000 列なら 29.2GB で 12GB コンテナでは OOM していた。

        窓内の数十列だけ読めば同じ答えが出る。列名は `_parquet_column_names` の
        キャッシュから引けるので、どの列が窓内かの判定に I/O は要らない。

        Returns: {feature_name: mean}。parquet 不在などで読めなければ None。
        """
        expr_path = Path(cache_dir) / "expression_matrix.parquet"
        if not expr_path.exists():
            return None
        wanted = [str(f) for f in (feature_names or [])]
        if not wanted:
            return {}
        try:
            schema_names = self._parquet_column_names(expr_path)
            if schema_names is not None:
                wanted = [f for f in wanted if f in schema_names]
            if not wanted:
                return {}
            df = _read_parquet_columns(
                _get_parquet_handle(expr_path, _parquet_file_sig(expr_path)), wanted)
            return {c: float(df[c].mean()) for c in wanted if c in df.columns}
        except Exception as e:  # noqa: BLE001
            logger.warning("列平均の取得に失敗 (%d 列): %s", len(wanted), e)
            return None

    def get_features_matrix(self, cache_dir, feature_names):
        """expression_matrix.parquet から複数 feature 列をまとめて読む (共発現用)。

        存在する列のみ取得する。Returns: (DataFrame, present_list) または
        (None, []) (parquet 不在 / 一致 0)。"""
        expr_path = Path(cache_dir) / "expression_matrix.parquet"
        if not expr_path.exists():
            return None, []
        try:
            # ver46.1: スキーマはキャッシュから引く（従来はここで ParquetFile を
            # 開き、直後の read_parquet でもう一度フッタを読んでいた）。
            schema_names = self._parquet_column_names(expr_path)
            if schema_names is None:
                return None, []
            present = [str(f) for f in (feature_names or [])
                       if str(f) in schema_names]
            if not present:
                return None, []
            # ver51.3: 保持済みハンドルで読む (フッタ再パースを挟まない)。
            return _read_parquet_columns(
                _get_parquet_handle(expr_path, _parquet_file_sig(expr_path)),
                present), present
        except Exception:  # noqa: BLE001
            return None, []

    def _run_extraction(self, rds_path: str, output_dir: Path,
                        with_expression: bool = False, cancel_event=None):
        """R ヘルパースクリプトで Seurat データを抽出

        with_expression=True で expression_matrix.parquet も生成（重い処理）。

        [ver50.1] 所要時間を必ずログに残す。これが無かったため「抽出が遅い」の
        内訳を手作業で測るまで特定できず、RDS が最も展開の遅い xz で保存されて
        いたことに数か月気づけなかった。R 側の各段は `[extract]` 行として
        この関数のログの後に stdout へ出る。
        """
        _t0 = time.monotonic()
        logger.info(
            "Seurat 抽出開始: %s (with_expression=%s) → %s",
            Path(rds_path).name, with_expression, output_dir.name,
        )
        script = R_HELPERS_DIR / "extract_seurat_data.R"
        rscript = str(RSCRIPT_PATH)
        if not Path(rscript).exists():
            rscript = "Rscript"

        cmd = [
            rscript, "--vanilla",
            str(script), rds_path, str(output_dir),
        ]
        if with_expression:
            cmd.append("--with-expression")
        # ver3.7: subprocess.run は timeout 時に内部で kill するため zombie の
        # 心配は無いが、TimeoutExpired を捕まえてユーザー向けエラーに整形
        if cancel_event is None:
            # 通常パス（キャンセル不要）: 既存どおり subprocess.run
            try:
                result = subprocess.run(
                    cmd, capture_output=True, timeout=600,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as e:
                if output_dir.exists():
                    shutil.rmtree(output_dir, ignore_errors=True)
                raise RuntimeError(
                    f"Seurat extraction timed out (10min): rds={rds_path}"
                ) from e
            returncode, stdout_bytes, stderr_bytes = (
                result.returncode, result.stdout, result.stderr)
        else:
            # キャンセル可能パス: Popen + cancel_event 監視。kill 時は部分キャッシュを掃除。
            try:
                returncode, stdout_bytes, stderr_bytes = _popen_with_cancel(
                    cmd, cancel_event, timeout=600,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except ExtractionCancelled:
                if output_dir.exists():
                    shutil.rmtree(output_dir, ignore_errors=True)
                raise
            except RuntimeError as e:
                if str(e) == "__TIMEOUT__":
                    if output_dir.exists():
                        shutil.rmtree(output_dir, ignore_errors=True)
                    raise RuntimeError(
                        f"Seurat extraction timed out (10min): rds={rds_path}"
                    ) from e
                raise
        if returncode != 0:
            # 不完全なキャッシュファイルを削除
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            logger.warning(
                "Seurat 抽出失敗: %s (%.1f 秒, rc=%s)",
                Path(rds_path).name, time.monotonic() - _t0, returncode,
            )
            raise RuntimeError(
                f"Seurat extraction failed:\n{stderr_text[:2000]}"
            )

        # R 側が出した [extract] 行（各段の秒数）をアプリのログにも残す。
        # 個別に測り直さなくても内訳が追えるようにするのが狙い。
        try:
            for line in (stdout_bytes or b"").decode("utf-8", errors="replace").splitlines():
                if line.startswith("[extract]") or line.startswith("[rds_io]"):
                    logger.info("R %s", line)
        except Exception as e:  # noqa: BLE001
            logger.debug("R 出力のログ転記に失敗（非重大）: %s", e)

        logger.info(
            "Seurat 抽出完了: %s (with_expression=%s) %.1f 秒",
            Path(rds_path).name, with_expression, time.monotonic() - _t0,
        )

    def derive_uncorrected_pca(self, src_rds_path: str, out_rds_path: str,
                               cancel_event=None) -> str:
        """Harmony RDS 内の未補正 pca 次元から UMAP を計算した派生RDSを生成して返す。

        既存結果でも「PCA（未補正）」を Harmony/RPCA と同じ UMAP 形式で比較表示するため。
        冪等: 出力が既にあれば再生成しない。書込先は SEURAT_CACHE_DIR 配下を想定し、
        結果フォルダを汚さない（読み取り専用/共有結果でも安全）。
        """
        out_path = Path(out_rds_path)
        if out_path.exists():
            return str(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        script = R_HELPERS_DIR / "derive_uncorrected_pca.R"
        rscript = str(RSCRIPT_PATH)
        if not Path(rscript).exists():
            rscript = "Rscript"
        cmd = [rscript, "--vanilla", str(script), str(src_rds_path), str(out_path)]

        if cancel_event is None:
            try:
                result = subprocess.run(
                    cmd, capture_output=True, timeout=600,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(
                    f"PCA derivation timed out (10min): rds={src_rds_path}"
                ) from e
            returncode, stderr_bytes = result.returncode, result.stderr
        else:
            try:
                returncode, _stdout_bytes, stderr_bytes = _popen_with_cancel(
                    cmd, cancel_event, timeout=600,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except ExtractionCancelled:
                raise
            except RuntimeError as e:
                if str(e) == "__TIMEOUT__":
                    raise RuntimeError(
                        f"PCA derivation timed out (10min): rds={src_rds_path}"
                    ) from e
                raise
        if returncode != 0:
            if out_path.exists():
                try:
                    out_path.unlink()
                except OSError:
                    pass
            stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            raise RuntimeError(f"PCA derivation failed:\n{stderr_text[:2000]}")
        return str(out_path)

    def _run_feature_extraction(
        self, rds_path: str, feature_name: str, output_path: Path
    ):
        """R ヘルパースクリプトで単一 Feature を抽出"""
        script = R_HELPERS_DIR / "extract_features.R"
        rscript = str(RSCRIPT_PATH)
        if not Path(rscript).exists():
            rscript = "Rscript"

        cmd = [
            rscript, "--vanilla",
            str(script), rds_path, feature_name, str(output_path),
        ]
        # ver3.7: TimeoutExpired を捕まえユーザー向けエラーに整形
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"Feature extraction timed out (5min): "
                f"feature={feature_name}"
            ) from e
        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            raise RuntimeError(
                f"Feature extraction failed:\n{stderr_text[:2000]}"
            )

    def export_region_cluster_means(self, rds_path, groups_df, out_csv_path=None,
                                    assay=None, layer="data",
                                    intensity_repr="linear", timeout=600):
        """ROI×クラスタ群ごとの平均強度を R 側で直接計算し DataFrame を返す（B経路）。

        巨大な expression_matrix.parquet を作らず、RDS から対象 cell のみ sparse 集計する。
        intensity_repr: "linear"（既定・data を preprocessing_method で線形化）/ "counts"（生）/
            "data"（現状の log）。列は m/z（feature_id, 一意）のまま返す（化合物名にリネームしない）。
        groups_df: 列 [CellID, Group]（ROI 割当済みのみ）。
        Returns: pd.DataFrame（先頭列 Group, 以降 feature(m/z) 平均）。
            df.attrs["repr"] / df.attrs["preprocessing_method"] に来歴を格納。
        """
        from app.utils.file_locks import get_or_create_lock
        cache_dir = self._get_cache_dir(rds_path)
        cache_dir.mkdir(parents=True, exist_ok=True)
        out_csv_path = Path(out_csv_path) if out_csv_path else (
            cache_dir / "metaboanalyst_region_cluster_means.csv")
        groups_csv = cache_dir / "_hne_groups_tmp.csv"

        script = R_HELPERS_DIR / "export_region_cluster_means.R"
        if not Path(script).exists():
            raise RuntimeError(f"R script not found: {script}")
        rscript = str(RSCRIPT_PATH)
        if not Path(rscript).exists():
            rscript = "Rscript"

        lock = get_or_create_lock(out_csv_path, timeout=timeout)
        with lock:
            groups_df.to_csv(groups_csv, index=False, encoding="utf-8")
            cmd = [rscript, "--vanilla", str(script), str(rds_path),
                   str(groups_csv), str(out_csv_path)]
            if assay:
                cmd += ["--assay", str(assay)]
            if layer:
                cmd += ["--layer", str(layer)]
            if intensity_repr:
                cmd += ["--repr", str(intensity_repr)]
            try:
                result = subprocess.run(
                    cmd, capture_output=True, timeout=timeout,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(
                    f"Region×cluster export timed out: rds={rds_path}") from e
            finally:
                try:
                    groups_csv.unlink()
                except OSError:
                    pass
            if result.returncode != 0:
                stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
                raise RuntimeError(
                    f"Region×cluster export failed:\n{stderr_text[:2000]}")
            if not out_csv_path.exists():
                raise RuntimeError(
                    f"Region×cluster export produced no output: {out_csv_path}")
            df = pd.read_csv(out_csv_path)
            # 来歴を stdout から回収して attrs に格納（明記用）
            import re as _re
            _out = (result.stdout.decode("utf-8", errors="replace")
                    if result.stdout else "")
            _m_repr = _re.search(r"^REPR=(.*)$", _out, _re.M)
            _m_prep = _re.search(r"^PREPROCESSING_METHOD=(.*)$", _out, _re.M)
            _m_assay = _re.search(r"^ASSAY_USED=(.*)$", _out, _re.M)
            df.attrs["repr"] = (_m_repr.group(1).strip()
                                if _m_repr else str(intensity_repr))
            df.attrs["preprocessing_method"] = (
                _m_prep.group(1).strip() if _m_prep else "")
            # 強度を読んだアッセイ（測定強度＝Spatial であることの来歴表示用）
            df.attrs["assay_used"] = (
                _m_assay.group(1).strip() if _m_assay else "")
            return df

    def run_differential_expression(self, rds_path, ident1_ids, ident2_ids=None,
                                    mode="global", min_pct=0.05, logfc=0.25,
                                    test_use="wilcox", assay=None,
                                    cancel_event=None, timeout=600):
        """選択範囲/群の on-the-fly DE を R (FindMarkers wilcox + BH) で実行する。

        mode="global": ident1_ids (選択) vs 残り全体（ident2 無視）。
        mode="local" : ident1_ids (選択) vs ident2_ids (指定群) のみ。
        CellID は plot_data の CellID（= colnames(obj)）。

        Returns: pd.DataFrame[gene,cluster,p_val,avg_log2FC,pct.1,pct.2,p_val_adj]。
        結果は (mode, ids, params) のハッシュで cache_dir にキャッシュし再実行で即返す。
        export_region_cluster_means と同じ subprocess/FileLock パターン。
        """
        from app.utils.file_locks import get_or_create_lock
        ident1_ids = [str(c) for c in (ident1_ids or [])]
        ident2_ids = [str(c) for c in (ident2_ids or [])] if mode == "local" else []
        if len(ident1_ids) < 3:
            raise RuntimeError("選択範囲が小さすぎます (3 ピクセル以上を選択してください)")
        if mode == "local" and len(ident2_ids) < 3:
            raise RuntimeError("比較対象の群が小さすぎます (3 ピクセル以上)")

        cache_dir = self._get_cache_dir(rds_path)
        cache_dir.mkdir(parents=True, exist_ok=True)
        h = hashlib.md5("|".join([
            mode,
            ",".join(sorted(ident1_ids)),
            ",".join(sorted(ident2_ids)),
            str(min_pct), str(logfc), test_use,
        ]).encode()).hexdigest()[:16]
        out_csv = cache_dir / f"de_{h}.csv"
        groups_csv = cache_dir / f"de_groups_{h}.csv"

        script = R_HELPERS_DIR / "run_findmarkers.R"
        if not Path(script).exists():
            raise RuntimeError(f"R script not found: {script}")
        rscript = str(RSCRIPT_PATH)
        if not Path(rscript).exists():
            rscript = "Rscript"

        lock = get_or_create_lock(out_csv, timeout=timeout)
        with lock:
            if out_csv.exists():
                return pd.read_csv(out_csv)
            rows_id = ident1_ids + ident2_ids
            rows_grp = ["A"] * len(ident1_ids) + ["B"] * len(ident2_ids)
            pd.DataFrame({"CellID": rows_id, "Group": rows_grp}).to_csv(
                groups_csv, index=False, encoding="utf-8")
            cmd = [rscript, "--vanilla", str(script), str(rds_path),
                   str(groups_csv), str(out_csv), mode,
                   "--min-pct", str(min_pct), "--logfc", str(logfc),
                   "--test", test_use]
            if assay:
                cmd += ["--assay", str(assay)]
            try:
                if cancel_event is None:
                    result = subprocess.run(
                        cmd, capture_output=True, timeout=timeout,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    returncode, stderr_bytes = result.returncode, result.stderr
                else:
                    returncode, _stdout_bytes, stderr_bytes = _popen_with_cancel(
                        cmd, cancel_event, timeout=timeout,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(f"DE timed out: rds={rds_path}") from e
            except RuntimeError as e:
                if str(e) == "__TIMEOUT__":
                    raise RuntimeError(f"DE timed out: rds={rds_path}") from e
                raise
            finally:
                try:
                    groups_csv.unlink()
                except OSError:
                    pass
            if returncode != 0:
                stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
                raise RuntimeError(f"DE failed:\n{stderr_text[:2000]}")
            if not out_csv.exists():
                raise RuntimeError(f"DE produced no output: {out_csv}")
            return pd.read_csv(out_csv)

    def _load_extracted_data(self, cache_dir: Path) -> dict:
        """キャッシュディレクトリからデータを読み込み"""
        # plot_data: Parquet優先、CSV fallback
        plot_parquet = cache_dir / "plot_data.parquet"
        plot_csv = cache_dir / "plot_data.csv"
        if plot_parquet.exists():
            plot_data = pd.read_parquet(plot_parquet)
        elif plot_csv.exists():
            plot_data = pd.read_csv(plot_csv)
        else:
            raise FileNotFoundError(f"plot_data が見つかりません: {cache_dir}")

        # cluster_stats
        cs_path = cache_dir / "cluster_stats.csv"
        if not cs_path.exists():
            raise FileNotFoundError(f"cluster_stats.csv が見つかりません: {cache_dir}")
        cluster_stats = pd.read_csv(cs_path)

        # features_list（任意 — なくても空リストで続行）
        features_file = cache_dir / "features_list.txt"
        features_list = []
        if features_file.exists():
            features_list = features_file.read_text(encoding="utf-8").strip().splitlines()

        # meta
        meta_file = cache_dir / "extraction_meta.json"
        if not meta_file.exists():
            raise FileNotFoundError(f"extraction_meta.json が見つかりません: {cache_dir}")
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)

        return {
            "plot_data": plot_data,
            "cluster_stats": cluster_stats,
            "features_list": features_list,
            "meta": meta,
        }

    # --- 外部アノテーション（SCiLS peak Name 由来）のサイドカー結合 (Q2) ---
    def _find_feature_annotation_sidecar(self, rds_path) -> Optional[Path]:
        """rds_path 近傍から `*_feature_annotations.parquet` を探す。"""
        p = Path(rds_path).resolve()
        bases = [p.parent, p.parent.parent, p.parent.parent.parent]
        seen = set()
        for base in bases:
            if base is None or str(base) in seen or not base.is_dir():
                continue
            seen.add(str(base))
            hits = sorted(base.glob("*_feature_annotations.parquet"))
            if hits:
                return hits[0]
            sub_hits = sorted(base.glob("*/*_feature_annotations.parquet"))
            if sub_hits:
                return sub_hits[0]
        return None

    def _load_feature_annotations(self, cache_dir: Path, rds_path,
                                  features_list: list) -> dict:
        """サイドカーを features_list に数値 m/z で join し {feature_str: record} を返す。

        キャッシュ済み（cache_dir/feature_annotations.json）があれば再利用。
        サイドカー無し / 候補なし feature はキーに含めない（= m/z 表示のまま）。
        """
        cache_file = cache_dir / "feature_annotations.json"
        sidecar = self._find_feature_annotation_sidecar(rds_path)
        # キャッシュがサイドカー以降に作られていれば再利用。サイドカーが後から
        # 付与/更新された（= サイドカーの方が新しい）場合はキャッシュを捨てて作り直す。
        if cache_file.exists():
            cache_fresh = True
            try:
                if sidecar is not None and (
                    sidecar.stat().st_mtime > cache_file.stat().st_mtime
                ):
                    cache_fresh = False
            except OSError:
                cache_fresh = True
            if cache_fresh:
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
        if not features_list:
            return {}
        if sidecar is None:
            return {}
        try:
            from app.utils.deg_utils import extract_mz_numeric as _extract_mz_numeric
            side = pd.read_parquet(sidecar)
            side_mz = side["mz"].to_numpy(dtype=float)
            out: dict = {}
            tol = 0.005
            for feat in features_list:
                mz = _extract_mz_numeric(feat)
                if mz is None or mz == float("inf"):
                    continue
                j = int(np.argmin(np.abs(side_mz - mz)))
                if abs(side_mz[j] - mz) > tol:
                    continue
                row = side.iloc[j]
                comp = _none_str(row.get("compound"))
                if not comp:
                    continue  # No DB hit 等は m/z 表示のまま
                out[feat] = {
                    "display_name": _none_str(row.get("display_name")) or comp,
                    "compound": comp,
                    "lipid_class": _none_str(row.get("lipid_class")),
                    "database": _none_str(row.get("database")),
                    "adduct": _none_str(row.get("adduct")),
                    "ppm": (float(row["ppm"]) if pd.notna(row.get("ppm")) else None),
                    "formula": _none_str(row.get("formula")),
                    "smiles": _none_str(row.get("smiles")),
                    "adduct_image": _none_str(row.get("adduct_image")),
                    "adduct_family": _none_str(row.get("adduct_family")),
                    "mz": float(row["mz"]),
                }
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False)
            except Exception:
                pass
            return out
        except Exception as e:
            logger.warning("feature annotation の join に失敗: %s", e)
            return {}
