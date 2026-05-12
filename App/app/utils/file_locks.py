# =============================================================================
# MSI Analysis Application - Dynamic File Lock Cache
# プロジェクト/RDS 配下のように、配置先がランタイムで決まる JSON ファイルに対する
# FileLock の動的キャッシュユーティリティ。
#
# 同一パスに対しては同一の FileLock インスタンスを返すため、
# 単一プロセス内のスレッド間でも共有されたロックとして機能する。
# =============================================================================

from __future__ import annotations

import threading
from pathlib import Path
from typing import Union

from filelock import FileLock

_LOCK_REGISTRY: dict[str, FileLock] = {}
_REGISTRY_LOCK = threading.Lock()
_MAX_CACHE_SIZE = 256


def get_or_create_lock(file_path: Union[str, Path], timeout: float = 30) -> FileLock:
    """ファイルパス用の FileLock を返す。同一パスは常に同じインスタンス。

    Args:
        file_path: ロック対象のファイルパス（絶対/相対どちらでも可）
        timeout: ロック取得タイムアウト秒

    Returns:
        FileLock インスタンス。ロックファイルは <file_path>.lock として作られる。

    Note:
        プロセス内のロック数が _MAX_CACHE_SIZE を超えた場合、
        最古のエントリが FIFO で削除される（メモリ圧迫防止）。
    """
    key = str(Path(file_path).resolve())
    with _REGISTRY_LOCK:
        if key not in _LOCK_REGISTRY:
            if len(_LOCK_REGISTRY) >= _MAX_CACHE_SIZE:
                _LOCK_REGISTRY.pop(next(iter(_LOCK_REGISTRY)))
            _LOCK_REGISTRY[key] = FileLock(f"{key}.lock", timeout=timeout)
        return _LOCK_REGISTRY[key]


def atomic_write_csv(
    df,
    file_path: Union[str, Path],
    *,
    index: bool = False,
    encoding: str = "utf-8",
    timeout: float = 30,
    **to_csv_kwargs,
) -> None:
    """DataFrame を atomic に CSV 保存。

    並行書き込み時のファイル破損を防ぐため:
    1. FileLock で排他制御 (同一プロセス内 + 別プロセス間)
    2. 同ディレクトリに tempfile 書込 → os.replace で原子的に差し替え

    途中で死んでも元ファイルは無傷。読込中のプロセスにも影響なし。

    Args:
        df: pandas.DataFrame
        file_path: 出力先 CSV パス
        index / encoding: pandas.to_csv() 引数
        timeout: FileLock 取得タイムアウト秒
        **to_csv_kwargs: 残りの to_csv 引数
    """
    import os
    import tempfile

    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = get_or_create_lock(path, timeout=timeout)
    with lock:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.stem}_", suffix=".csv.tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
                df.to_csv(f, index=index, **to_csv_kwargs)
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
