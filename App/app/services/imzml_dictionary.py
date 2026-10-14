"""Bounded exact m/z union with balanced runs and blockwise external merge.

The builder only constructs the dictionary. It never merges source intensities
or decides which source spectra/peaks are valid. No operational thresholds are
written to analytical manifests: every policy produces the same sorted <f8 file.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import tempfile

import numpy as np

from app.services.input_preparation import check_cancel


DEFAULT_DICTIONARY_MEMORY_MB = 128


@dataclass(frozen=True)
class DictionaryPolicy:
    input_values: int
    retained_bytes: int
    merge_values: int

    @classmethod
    def from_bytes(cls, budget):
        if isinstance(budget, bool) or int(budget) != budget or budget < 256:
            raise ValueError("Dictionary working budget must be at least 256 bytes")
        # 128 MiB -> 16 MiB raw input, 32 MiB retained runs, 1 MiB read blocks.
        # Reserve the rest for NumPy sort/mask/output copies and merge work.
        return cls(max(1, min(16 * 1024 * 1024, budget // 8) // 8),
                   max(8, min(32 * 1024 * 1024, budget // 4)),
                   max(1, min(1024 * 1024, budget // 64) // 8))

    @classmethod
    def from_megabytes(cls, budget):
        try:
            value = int(budget)
            if isinstance(budget, bool) or value < 1 or float(budget) != value:
                raise ValueError
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Dictionary memory budget MiB must be a positive integer") from exc
        return cls.from_bytes(value * 1024 * 1024)


@dataclass
class _Run:
    count: int
    values: np.ndarray | None = None
    path: Path | None = None


@dataclass(frozen=True)
class DictionaryResult:
    path: Path
    count: int
    stats: dict


class _Reader:
    def __init__(self, run, stack, block_values, stats):
        self.run = run
        self.block_values = block_values
        self.offset = 0
        self.buffer = np.empty(0, dtype="<f8")
        self.handle = None
        if run.path is not None:
            self.handle = stack.enter_context(run.path.open("rb"))
            stats["file_opens"] += 1
        self.advance(0)

    def advance(self, count):
        self.buffer = self.buffer[count:]
        if len(self.buffer) or self.offset == self.run.count:
            return
        count = min(self.block_values, self.run.count - self.offset)
        if self.handle is None:
            self.buffer = self.run.values[self.offset:self.offset + count]
        else:
            self.buffer = np.fromfile(self.handle, dtype="<f8", count=count)
            if len(self.buffer) != count:
                raise OSError("Truncated intermediate m/z run")
        self.offset += count


class SortedUniqueBuilder:
    def __init__(self, root, *, memory_budget_mb=DEFAULT_DICTIONARY_MEMORY_MB,
                 cancel=None, policy=None):
        self.policy = policy or DictionaryPolicy.from_megabytes(memory_budget_mb)
        if min(self.policy.input_values, self.policy.retained_bytes, self.policy.merge_values) < 1:
            raise ValueError("Dictionary buffers must be positive")
        self.cancel = cancel
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.work = Path(tempfile.mkdtemp(prefix=".mz-runs-", dir=self.root))
        self.buffer = np.empty(self.policy.input_values, dtype="<f8")
        self.used = 0
        self.levels: dict[int, _Run] = {}
        self.finished = False
        self.sequence = 0
        self.stats = {"source_values": 0, "initial_runs": 0, "merges": 0,
                      "external_merges": 0, "spills": 0, "file_opens": 0,
                      "write_calls": 0, "merge_values_read": 0,
                      "max_retained_bytes": 0, "max_levels": 0}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.levels.clear()
        self.buffer = np.empty(0, dtype="<f8")
        shutil.rmtree(self.work, ignore_errors=True)

    def _path(self):
        self.sequence += 1
        return self.work / f"run-{self.sequence:08d}.bin"

    def _write(self, handle, values):
        np.asarray(values, dtype="<f8").tofile(handle)
        self.stats["write_calls"] += 1

    def _store(self, values):
        path = self._path()
        pending = path.with_suffix(".partial")
        try:
            with pending.open("xb") as handle:
                self.stats["file_opens"] += 1
                for start in range(0, len(values), self.policy.merge_values):
                    check_cancel(self.cancel)
                    self._write(handle, values[start:start + self.policy.merge_values])
            os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)
        return _Run(len(values), path=path)

    def _bound_retained(self, extra=()):
        runs = [*self.levels.values(), *extra]
        resident = sum(run.values.nbytes for run in runs if run.values is not None)
        for run in sorted(runs, key=lambda r: r.count, reverse=True):
            if resident <= self.policy.retained_bytes:
                break
            if run.values is not None:
                spilled = self._store(run.values)
                resident -= run.values.nbytes
                run.values = None
                run.path = spilled.path
                self.stats["spills"] += 1
        self.stats["max_retained_bytes"] = max(self.stats["max_retained_bytes"], resident)

    @staticmethod
    def _dispose(run):
        if run.path is not None:
            run.path.unlink(missing_ok=True)
        run.values = None

    def _merge(self, left, right):
        check_cancel(self.cancel)
        self.stats["merges"] += 1
        self.stats["merge_values_read"] += left.count + right.count
        # Small unions avoid disk I/O. Large unions use fixed-size external
        # blocks, never np.unique(concatenate(whole_run, whole_run)).
        size = (left.count + right.count) * 8
        if (left.values is not None and right.values is not None
                and size <= self.policy.input_values * 4):
            result = _Run(0, values=np.union1d(left.values, right.values))
            result.count = len(result.values)
        else:
            result = self._merge_external(left, right)
        # Inputs survive an incomplete merge; only dispose after success.
        self._dispose(left)
        self._dispose(right)
        return result

    def _merge_external(self, left, right):
        self.stats["external_merges"] += 1
        path = self._path()
        pending = path.with_suffix(".partial")
        count = 0
        previous = None
        try:
            with ExitStack() as stack:
                destination = stack.enter_context(pending.open("xb"))
                self.stats["file_opens"] += 1
                readers = [_Reader(run, stack, self.policy.merge_values, self.stats)
                           for run in (left, right)]
                while any(len(reader.buffer) for reader in readers):
                    check_cancel(self.cancel)
                    active = [reader for reader in readers if len(reader.buffer)]
                    boundary = min(reader.buffer[-1] for reader in active)
                    sizes = [int(np.searchsorted(reader.buffer, boundary, side="right"))
                             for reader in readers]
                    pieces = [reader.buffer[:take] for reader, take in zip(readers, sizes) if take]
                    values = np.unique(np.concatenate(pieces))
                    if previous is not None:
                        values = values[np.searchsorted(values, previous, side="right"):]
                    if len(values):
                        self._write(destination, values)
                        count += len(values)
                        previous = values[-1]
                    for reader, take in zip(readers, sizes):
                        reader.advance(take)
            os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)
        return _Run(count, path=path)

    def _flush(self):
        if not self.used:
            return
        check_cancel(self.cancel)
        values = np.unique(self.buffer[:self.used])
        self.used = 0
        self.stats["initial_runs"] += 1
        run = _Run(len(values), values=values)
        level = 0
        # The level counts input chunks, not the number of unique masses.
        # Repeated tiny dictionaries therefore cannot turn this into a repeated
        # merge of the accumulated global dictionary with every new chunk.
        while level in self.levels:
            left = self.levels.pop(level)
            self._bound_retained((left, run))
            run = self._merge(left, run)
            level += 1
        self._bound_retained((run,))
        self.levels[level] = run
        self.stats["max_levels"] = max(self.stats["max_levels"], len(self.levels))

    def add(self, values):
        if self.finished:
            raise RuntimeError("The m/z dictionary is already finished")
        values = np.asarray(values)
        if values.ndim != 1:
            raise ValueError("m/z dictionary inputs must be one-dimensional")
        position = 0
        while position < len(values):
            check_cancel(self.cancel)
            size = min(len(values) - position, len(self.buffer) - self.used)
            part = values[position:position + size]
            if not np.isfinite(part).all() or np.any(part <= 0):
                raise ValueError("m/z dictionary inputs must be positive finite values")
            self.buffer[self.used:self.used + size] = part
            self.used += size
            position += size
            self.stats["source_values"] += size
            if self.used == len(self.buffer):
                self._flush()

    def finish(self, output):
        if self.finished:
            raise RuntimeError("The m/z dictionary is already finished")
        output = Path(output)
        if output.exists():
            raise FileExistsError(f"The m/z dictionary already exists: {output}")
        self._flush()
        if not self.levels:
            raise ValueError("No m/z values were collected")
        run = None
        for level in sorted(self.levels):
            other = self.levels[level]
            run = other if run is None else self._merge(run, other)
        if run.values is not None:
            run = self._store(run.values)
        check_cancel(self.cancel)
        os.replace(run.path, output)
        self.finished = True
        return DictionaryResult(output, run.count, dict(self.stats))
