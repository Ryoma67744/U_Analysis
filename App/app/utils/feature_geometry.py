"""Feature 図専用の読込世代と、容量を限定した不変幾何スナップショット。"""

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import os
import threading
import uuid
from types import MappingProxyType
from weakref import WeakValueDictionary

import numpy as np
import pandas as pd


@dataclass(frozen=True, eq=False)
class FeatureDataset:
    """版と読込元を同時に公開する。解析用 DataFrame を変更しない。"""

    revision: str
    source: object
    columns: object


class FeatureGeometry:
    def __init__(self, dataset):
        self.dataset = dataset
        self.revision = dataset.revision
        self.columns = dataset.columns
        self.rows = {}
        # ★ ver66.3: 切片ごとの全行 astype/filter を各 m/z 操作で繰り返していた。
        #   元の行順を保つ位置索引を一度だけ作り、表示名とは区別する。
        groups = pd.Series(self.columns["Sample"], copy=False).groupby(
            pd.Series(self.columns["Sample"], copy=False), sort=False).indices
        for sample, positions in groups.items():
            positions = np.asarray(positions, dtype=np.intp)
            positions.flags.writeable = False
            self.rows[str(sample)] = positions
        self.grids = {}
        self.nbytes = sum(v.nbytes for v in self.rows.values())

    def frame(self):
        """内部配列を共有する読み取り用の枠。呼出側は列の上書きをしない。"""
        return pd.DataFrame(dict(self.columns), copy=False)


# ★ ver66.3: 全m/z行列は保持せず、全プロジェクトのsnapshot+幾何索引を制限する。
# 10万画素×20切片の対象5列約80MB+行索引約16MB+格子索引約32MB。
# 256MiB はこの検証規模を保持し、既定12GiBの約2.1%に収まる。
# 実データで余裕がない場合は 0 で再利用を止められる（従来計算へ戻る）。
_MAX_BYTES = max(0, int(os.environ.get("FEATURE_GEOMETRY_CACHE_BYTES", 256 * 1024**2)))
# 1データだけで全体予算を占有しないための上限。全体256MiBの内数。
_MAX_SNAPSHOT_BYTES = max(0, int(os.environ.get("FEATURE_GEOMETRY_SNAPSHOT_BYTES", 128 * 1024**2)))
_CACHE = OrderedDict()
_DATASETS = WeakValueDictionary()
_GEOMETRIES = WeakValueDictionary()
_LOCK = threading.RLock()


def new_feature_dataset(source):
    # ★ ver66.3: 初回m/zまでコピーを遅らせると、公開後に元DFが変更された際に
    #   「版は同じで中身だけ違う」状態になる。対象列は公開前に確定する。
    columns = {}
    if source is not None and {"Sample", "SpatialX", "SpatialY", "CellID"}.issubset(source.columns):
        wanted = [c for c in ("Sample", "SpatialX", "SpatialY", "CellID", "TotalCount")
                  if c in source.columns]
        size = sum(source[c].to_numpy(copy=False).nbytes for c in wanted)
        with _LOCK:
            if size <= _MAX_SNAPSHOT_BYTES and _trim(size):
                for column in wanted:
                    values = source[column].to_numpy(copy=True)
                    values.flags.writeable = False
                    columns[column] = values
            publication = FeatureDataset(uuid.uuid4().hex, source, MappingProxyType(columns))
            _DATASETS[publication.revision] = publication
            return publication
    return FeatureDataset(uuid.uuid4().hex, source, MappingProxyType(columns))


def feature_dataset_frame(dataset):
    return pd.DataFrame(dict(dataset.columns), copy=False) if dataset.columns else dataset.source


def forget_feature_geometry(dataset):
    if dataset is not None:
        with _LOCK:
            _CACHE.pop(dataset.revision, None)


def _trim(required, keep=None):
    while _CACHE and _retained_bytes() + required > _MAX_BYTES:
        victim = next((k for k in _CACHE if k != keep), None)
        if victim is None:
            return False
        _CACHE.pop(victim)
    return _retained_bytes() + required <= _MAX_BYTES


def _retained_bytes():
    # LRUを追放してもstate/進行中処理がsnapshotを持つ限り、その量を引かない。
    return (sum(sum(v.nbytes for v in d.columns.values()) for d in list(_DATASETS.values()))
            + sum(v.nbytes for v in list(_GEOMETRIES.values())))


def get_feature_geometry(dataset):
    if dataset is None or dataset.source is None:
        return None
    if not dataset.columns:
        return None
    with _LOCK:
        cached = _CACHE.get(dataset.revision)
        if cached is not None:
            _CACHE.move_to_end(dataset.revision)
            return cached
        estimated = len(dataset.columns["Sample"]) * np.dtype(np.intp).itemsize
        if estimated > _MAX_BYTES or not _trim(estimated):
            return None
        geometry = FeatureGeometry(dataset)
        # 文字列表現で衝突する実ID（例 1 と "1"）は従来経路へ戻す。
        if len(geometry.rows) != pd.Series(dataset.columns["Sample"]).nunique(dropna=True):
            return None
        _GEOMETRIES[id(geometry)] = geometry
        _CACHE[dataset.revision] = geometry
        return geometry


def cached_feature_grid(geometry, sample, transform_key, build):
    """None（不規則格子）も保持する。戻り値の添字配列は読み取り専用。"""
    key = (str(sample), transform_key)
    with _LOCK:
        if key in geometry.grids:
            return geometry.grids[key]
    grid = build()
    if grid is not None:
        grid = tuple(np.array(v, copy=True) for v in grid)
        for values in grid:
            values.flags.writeable = False
    size = sum(v.nbytes for v in grid) if grid is not None else 0
    with _LOCK:
        if (_CACHE.get(geometry.revision) is geometry
                and key not in geometry.grids and _trim(size, keep=geometry.revision)):
            # 1切片は現在の変換だけ残す。回転操作のたびにメモリが増えない。
            for old in [k for k in geometry.grids if k[0] == str(sample)]:
                prior = geometry.grids.pop(old)
                geometry.nbytes -= sum(v.nbytes for v in prior) if prior is not None else 0
            geometry.grids[key] = grid
            geometry.nbytes += size
    return grid


def feature_transform_key(rotation_store, raster_enabled):
    payload = json.dumps([rotation_store or {}, bool(raster_enabled)],
                         sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def feature_generation_prefix(dataset, rotation_store, raster_enabled):
    return f"{dataset.revision}:{feature_transform_key(rotation_store, raster_enabled)}:"


def feature_geometry_cache_info():
    with _LOCK:
        return {"entries": len(_CACHE), "bytes": sum(v.nbytes for v in _CACHE.values()),
                "retained_bytes": _retained_bytes(), "limit": _MAX_BYTES}
