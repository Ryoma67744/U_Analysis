# =============================================================================
# MSI Analysis Application - Feature Lists (Phase 5)
# Loupe Browser の feature list / 共発現解析 相当。複数の m/z (feature) を
# 名前付きリストとして保存・改名・削除し、CSV 入出力する。2 リストの集約強度を
# 散布図にして共局在 (co-localization) を探す。
#
# 永続化は RDS 隣の JSON sidecar (selection_groups と同型、FileLock + atomic)。
# 純粋な CRUD は IO から分離し config/dash 非依存 (単体テスト可能)。
# =============================================================================

import csv
import io
import json
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("msi.feature_lists")

__all__ = [
    "lists_path", "load_lists", "save_lists",
    "empty_state", "add_list", "rename_list", "delete_list",
    "lists_to_csv", "lists_from_csv",
]


def empty_state() -> dict:
    return {"lists": []}


def _next_id(state) -> str:
    nums = []
    for g in state.get("lists", []):
        m = re.match(r"l(\d+)$", str(g.get("id", "")))
        if m:
            nums.append(int(m.group(1)))
    return "l" + str((max(nums) + 1) if nums else 1)


def add_list(state, name, features) -> dict:
    """新しい feature リストを追加した新 state を返す。features は順序保持で重複除去。"""
    state = state or empty_state()
    lists = list(state.get("lists", []))
    seen, uniq = set(), []
    for f in (features or []):
        f = str(f)
        if f and f not in seen:
            seen.add(f)
            uniq.append(f)
    nm = (str(name).strip() if name else "") or f"リスト{len(lists) + 1}"
    lists.append({"id": _next_id(state), "name": nm, "features": uniq})
    return {**state, "lists": lists}


def rename_list(state, lid, new_name) -> dict:
    lists = []
    for g in (state or {}).get("lists", []):
        if g.get("id") == lid:
            g = {**g, "name": (str(new_name).strip() or g.get("name"))}
        lists.append(g)
    return {**(state or empty_state()), "lists": lists}


def delete_list(state, lid) -> dict:
    lists = [g for g in (state or {}).get("lists", []) if g.get("id") != lid]
    return {**(state or empty_state()), "lists": lists}


def lists_to_csv(state) -> str:
    """Feature,List 形式の CSV 文字列を返す。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Feature", "List"])
    for g in (state or {}).get("lists", []):
        name = g.get("name", "")
        for f in g.get("features", []):
            w.writerow([f, name])
    return buf.getvalue()


def lists_from_csv(text) -> dict:
    """Feature,List の CSV テキストを state に変換 (List 名ごとにまとめる)。"""
    state = empty_state()
    if not text:
        return state
    reader = csv.DictReader(io.StringIO(text))
    by_name: dict = {}
    order: list = []
    for row in reader:
        keys = {k.lower().strip(): k for k in row.keys() if k}
        feat_k = (keys.get("feature") or keys.get("mz") or keys.get("m/z")
                  or keys.get("compound") or keys.get("name"))
        list_k = keys.get("list") or keys.get("group")
        if not feat_k:
            continue
        feat = str(row.get(feat_k, "")).strip()
        if not feat:
            continue
        lname = str(row.get(list_k, "")).strip() if list_k else "取込リスト"
        lname = lname or "取込リスト"
        if lname not in by_name:
            by_name[lname] = []
            order.append(lname)
        by_name[lname].append(feat)
    for lname in order:
        state = add_list(state, lname, by_name[lname])
    return state


# ---------------------------------------------------------------------------
# 永続化 (IO)
# ---------------------------------------------------------------------------
def lists_path(rds_path):
    if not rds_path:
        return None
    return Path(rds_path).parent / "feature_lists_state.json"


def _atomic_write_json(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp",
                               prefix=path.stem + "_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def load_lists(rds_path) -> dict:
    path = lists_path(rds_path)
    if path and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("lists"), list):
                return data
        except Exception as e:  # noqa: BLE001
            logger.warning("feature リストの読込に失敗: %s", e)
    return empty_state()


def save_lists(rds_path, state) -> None:
    path = lists_path(rds_path)
    if not path:
        return
    try:
        from app.utils.file_locks import get_or_create_lock
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = get_or_create_lock(path)
        with lock:
            data = {"lists": (state or {}).get("lists", []),
                    "_saved_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
            _atomic_write_json(path, data)
    except Exception as e:  # noqa: BLE001
        logger.warning("feature リストの保存に失敗: %s", e)
