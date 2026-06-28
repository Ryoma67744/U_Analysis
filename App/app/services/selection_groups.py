# =============================================================================
# MSI Analysis Application - Selection Groups (Phase 3)
# Loupe Browser の Groups/Filters 相当。lasso/box 選択を「名前付き永続オブジェクト」
# として保存・結合・改名・CSV 入出力し、下流 (選択統計・DE) の入力に再利用する。
#
# 永続化は RDS 隣の JSON sidecar (hne_persistence / label_persistence と同型、
# FileLock + atomic write)。純粋な CRUD 操作 (副作用なし) は単体テスト可能なように
# IO から分離し、config / dash / color_utils に依存しない。
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

logger = logging.getLogger("msi.selection_groups")

# config(dotenv) 依存を避けるための小さな内蔵パレット (色未指定時の自動割当)
_PALETTE = [
    "#E64B35", "#4DBBD5", "#00A087", "#3C5488", "#F39B7F", "#8491B4",
    "#91D1C2", "#DC0000", "#7E6148", "#B09C85", "#925E9F", "#FDAF91",
]

__all__ = [
    "groups_path", "load_groups", "save_groups",
    "empty_state", "add_group", "rename_group", "delete_group",
    "combine_groups", "groups_to_csv", "groups_from_csv",
]


# ---------------------------------------------------------------------------
# 純粋な状態操作 (副作用なし・IO なし)。state = {"groups": [ {...}, ... ]}
#   group = {"id": "g1", "name": str, "cell_ids": [str, ...], "color": "#rrggbb"}
# ---------------------------------------------------------------------------
def empty_state() -> dict:
    return {"groups": []}


def _next_id(state) -> str:
    nums = []
    for g in state.get("groups", []):
        m = re.match(r"g(\d+)$", str(g.get("id", "")))
        if m:
            nums.append(int(m.group(1)))
    return "g" + str((max(nums) + 1) if nums else 1)


def _next_color(state) -> str:
    return _PALETTE[len(state.get("groups", [])) % len(_PALETTE)]


def add_group(state, name, cell_ids, color=None) -> dict:
    """新しいグループを追加した新 state を返す。cell_ids は順序保持で重複除去。"""
    state = state or empty_state()
    groups = list(state.get("groups", []))
    seen, uniq = set(), []
    for c in (cell_ids or []):
        c = str(c)
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    gid = _next_id(state)
    nm = (str(name).strip() if name else "") or f"選択{len(groups) + 1}"
    groups.append({
        "id": gid, "name": nm, "cell_ids": uniq,
        "color": color or _next_color(state),
    })
    return {**state, "groups": groups}


def rename_group(state, gid, new_name) -> dict:
    groups = []
    for g in (state or {}).get("groups", []):
        if g.get("id") == gid:
            g = {**g, "name": (str(new_name).strip() or g.get("name"))}
        groups.append(g)
    return {**(state or empty_state()), "groups": groups}


def delete_group(state, gid) -> dict:
    groups = [g for g in (state or {}).get("groups", []) if g.get("id") != gid]
    return {**(state or empty_state()), "groups": groups}


def combine_groups(state, gids, new_name=None) -> dict:
    """指定 id 群の cell_ids を和集合した新グループを追加する (元グループは保持)。"""
    state = state or empty_state()
    gidset = {str(g) for g in (gids or [])}
    union, seen = [], set()
    picked = []
    for g in state.get("groups", []):
        if str(g.get("id")) in gidset:
            picked.append(g.get("name", ""))
            for c in g.get("cell_ids", []):
                c = str(c)
                if c not in seen:
                    seen.add(c)
                    union.append(c)
    if not union:
        return state
    nm = new_name or ("+".join([p for p in picked if p]) or "結合")
    return add_group(state, nm, union)


def groups_to_csv(state) -> str:
    """CellID,Group 形式の CSV 文字列を返す (各グループの各 cell を1行)。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["CellID", "Group"])
    for g in (state or {}).get("groups", []):
        name = g.get("name", "")
        for c in g.get("cell_ids", []):
            w.writerow([c, name])
    return buf.getvalue()


def groups_from_csv(text) -> dict:
    """CellID,Group の CSV テキストを state に変換する (Group 名ごとにまとめる)。"""
    state = empty_state()
    if not text:
        return state
    reader = csv.DictReader(io.StringIO(text))
    by_name: dict = {}
    order: list = []
    # ヘッダの大文字小文字や別名に寛容に
    for row in reader:
        keys = {k.lower().strip(): k for k in row.keys() if k}
        cid_k = keys.get("cellid") or keys.get("cell_id") or keys.get("barcode")
        grp_k = keys.get("group") or keys.get("cluster") or keys.get("name")
        if not cid_k or not grp_k:
            continue
        cid = str(row.get(cid_k, "")).strip()
        grp = str(row.get(grp_k, "")).strip()
        if not cid or not grp:
            continue
        if grp not in by_name:
            by_name[grp] = []
            order.append(grp)
        by_name[grp].append(cid)
    for grp in order:
        state = add_group(state, grp, by_name[grp])
    return state


# ---------------------------------------------------------------------------
# 永続化 (IO)
# ---------------------------------------------------------------------------
def groups_path(rds_path):
    if not rds_path:
        return None
    return Path(rds_path).parent / "selection_groups_state.json"


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


def load_groups(rds_path) -> dict:
    path = groups_path(rds_path)
    if path and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("groups"), list):
                return data
        except Exception as e:  # noqa: BLE001
            logger.warning("選択グループの読込に失敗: %s", e)
    return empty_state()


def save_groups(rds_path, state) -> None:
    path = groups_path(rds_path)
    if not path:
        return
    try:
        from app.utils.file_locks import get_or_create_lock
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = get_or_create_lock(path)
        with lock:
            data = {"groups": (state or {}).get("groups", []),
                    "_saved_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
            _atomic_write_json(path, data)
    except Exception as e:  # noqa: BLE001
        logger.warning("選択グループの保存に失敗: %s", e)
