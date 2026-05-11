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
