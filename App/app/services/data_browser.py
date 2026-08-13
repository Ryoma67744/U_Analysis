# =============================================================================
# MSI Analysis Application - Data Browser Service
# データ管理サブタブが利用するサービス層
# DESI/TIMS生データ、解析出力、アプリ内部データの4箇所を統一して扱う
# =============================================================================

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import (
    DESI_DATA_DIR,
    TIMS_DATA_DIR,
    OUTPUT_DATA_DIR,
    OTHER_DIR,
)
from app.layouts.file_browser_modal import list_directory
from app.services.project_manager import (
    get_project,
    get_sub_project,
    list_projects,
    restore_projects_from_meta,
    scan_project_meta,
)
from app.services.backup_manager import list_backups

logger = logging.getLogger("msi.data_browser")


@dataclass(frozen=True)
class DataLocation:
    key: str
    label: str
    root: Path
    env_var: Optional[str]
    description: str


DATA_LOCATIONS: dict[str, DataLocation] = {
    "desi": DataLocation(
        key="desi",
        label="DESI生データ",
        root=DESI_DATA_DIR,
        env_var="DESI_DATA_DIR",
        description="DESI法による質量分析の生データ",
    ),
    "tims": DataLocation(
        key="tims",
        label="TIMS生データ",
        root=TIMS_DATA_DIR,
        env_var="TIMS_DATA_DIR",
        description="TIMS法による質量分析の生データ",
    ),
    "output": DataLocation(
        key="output",
        label="解析出力",
        root=OUTPUT_DATA_DIR,
        env_var="OUTPUT_DATA_DIR",
        description="アプリが生成した解析結果と _project_meta.json",
    ),
    "internal": DataLocation(
        key="internal",
        label="アプリ内部データ",
        root=OTHER_DIR,
        env_var=None,
        description="セッション・プロジェクト・プリセット・共有・キャッシュ",
    ),
}


def get_location(key: str) -> Optional[DataLocation]:
    return DATA_LOCATIONS.get(key)


def get_location_root(key: str) -> Optional[Path]:
    loc = DATA_LOCATIONS.get(key)
    return loc.root if loc else None


def get_layout_summary() -> list[dict]:
    """データ管理サブタブの配置サマリー表示用"""
    rows = []
    for loc in DATA_LOCATIONS.values():
        path = loc.root
        env_value = os.environ.get(loc.env_var) if loc.env_var else None
        rows.append({
            "key": loc.key,
            "label": loc.label,
            "path": str(path),
            "exists": path.is_dir(),
            "env_var": loc.env_var,
            "env_value": env_value,
            "description": loc.description,
        })
    return rows


def _safe_resolve(root: Path, subpath: str) -> Path:
    """サブパスを root 配下に制限して解決（root 脱出禁止）"""
    target = Path(subpath) if subpath else root
    if not target.is_absolute():
        target = (root / target).resolve()
    else:
        target = target.resolve()
    try:
        root_resolved = root.resolve()
    except OSError:
        return target
    try:
        target.relative_to(root_resolved)
    except ValueError:
        return root_resolved
    return target


def _build_breadcrumb(root: Path, target: Path) -> list[dict]:
    """root から target までのパンくず階層を生成"""
    try:
        rel = target.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return [{"name": target.name or str(target), "path": str(target)}]
    crumbs = [{"name": root.name or str(root), "path": str(root)}]
    current = root
    for part in rel.parts:
        current = current / part
        crumbs.append({"name": part, "path": str(current)})
    return crumbs


def get_directory_listing(key: str, subpath: str = "") -> dict:
    """指定場所のサブパスを list_directory で列挙

    Returns
    -------
    dict
        {"current_dir": str, "items": list, "exists": bool,
         "breadcrumb": list, "root": str}
    """
    root = get_location_root(key)
    if root is None:
        return {
            "current_dir": "", "items": [], "exists": False,
            "breadcrumb": [], "root": "",
        }
    target = _safe_resolve(root, subpath)
    items = list_directory(str(target), show_files=True) if target.is_dir() else []
    breadcrumb = _build_breadcrumb(root, target)
    return {
        "current_dir": str(target),
        "items": items,
        "exists": target.is_dir(),
        "breadcrumb": breadcrumb,
        "root": str(root),
    }


def _dir_stats(path: Path, max_items: int = 50000) -> tuple[int, int]:
    """フォルダ配下のファイル数と合計サイズを集計（上限あり）"""
    count = 0
    total = 0
    if not path.is_dir():
        return 0, 0
    try:
        for entry in path.rglob("*"):
            if count >= max_items:
                break
            try:
                if entry.is_file():
                    total += entry.stat().st_size
                    count += 1
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        pass
    return count, total


def get_storage_stats() -> list[dict]:
    """各場所のディスク使用量とディスク全体の空き容量"""
    rows = []
    for loc in DATA_LOCATIONS.values():
        path = loc.root
        if not path.is_dir():
            rows.append({
                "key": loc.key,
                "label": loc.label,
                "path": str(path),
                "exists": False,
                "file_count": 0,
                "used_bytes": 0,
                "disk_total_bytes": 0,
                "disk_free_bytes": 0,
            })
            continue
        count, used = _dir_stats(path)
        try:
            usage = shutil.disk_usage(path)
            disk_total = usage.total
            disk_free = usage.free
        except OSError:
            disk_total = 0
            disk_free = 0
        rows.append({
            "key": loc.key,
            "label": loc.label,
            "path": str(path),
            "exists": True,
            "file_count": count,
            "used_bytes": used,
            "disk_total_bytes": disk_total,
            "disk_free_bytes": disk_free,
        })
    return rows


# ---------------------------------------------------------------------------
# フォルダ移動
# ---------------------------------------------------------------------------
# 出力先の既定値が長らく /app（コンテナの書き込み層）だったため、結果フォルダが
# 永続化されない場所に残っている環境がある。そこは SFTP からも見えず、
# コンテナを作り直すと消えるので、UI から永続化先へ退避できるようにする。
#
# 移動先は DATA_LOCATIONS 配下に限定する。移動元は限定しない —— 退避したい
# /app/Analysis_* は 4 つのルートのどれにも属さず、縛ると目的を達せないため。


def _resolved_location_roots() -> list[tuple[str, str, Path]]:
    """DATA_LOCATIONS のルートを解決して (key, label, path) の一覧で返す。

    解決できないルートは飛ばす。実体は config 由来の固定パスで、非 strict な
    `resolve()` が失敗するのは symlink ループ等の異常時だけなので、そのときは
    「その場所は無い」として扱ってよい（判定はどちらも安全側に倒れる。
    `is_persistent_path` は「非永続」と答え、`preview_move` はそのルートを
    移動先として認めなくなるが、そもそも解決できないルートは既に壊れている）。
    """
    out: list[tuple[str, str, Path]] = []
    for loc in DATA_LOCATIONS.values():
        try:
            out.append((loc.key, loc.label, loc.root.resolve()))
        except OSError:
            continue
    return out


def location_labels() -> str:
    """DATA_LOCATIONS の表示名を「A / B / C」形式で返す（エラー文言・説明用）。"""
    return " / ".join(loc.label for loc in DATA_LOCATIONS.values())


def _location_for(path: Path) -> Optional[tuple[str, str]]:
    """`path` を含む DATA_LOCATION の (key, label)。どこにも属さなければ None。"""
    for key, label, root in _resolved_location_roots():
        if path.is_relative_to(root):
            return key, label
    return None


def is_persistent_path(path) -> bool:
    """DATA_LOCATIONS のいずれかの配下か（＝コンテナ再作成で消えないか）。"""
    if not path or not str(path).strip():
        return False
    try:
        target = Path(str(path).strip()).resolve()
    except OSError:
        return False
    return _location_for(target) is not None


def _running_analysis_block() -> str:
    """解析実行中なら拒否理由を返す。実行中でなければ空文字。

    解析の起動ガード (`analysis_runner._find_running_job_for_guard`) をそのまま
    使う。探索に失敗したときは「実行中は無い」ではなく拒否側に倒れる設計なので、
    出力先を書き換えている最中に移動してしまう事故を防げる。
    """
    try:
        from app.services.analysis_runner import _find_running_job_for_guard
        busy = _find_running_job_for_guard()
    except Exception as exc:  # noqa: BLE001
        logger.warning("実行中ジョブの確認に失敗、移動を拒否: %s", exc)
        return "実行中の解析を確認できませんでした。安全のため移動を見送ります。"
    if busy is None:
        return ""
    if busy.get("_scan_failed"):
        return "実行中の解析を確認できませんでした。安全のため移動を見送ります。"
    owner = (busy.get("analyst") or "").strip()
    who = f"{owner} さんの解析" if owner else "別の解析"
    return f"{who}が実行中です。完了してから移動してください。"


def preview_move(src: str, dest: str) -> dict:
    """移動の事前検証。実際には何も動かさない。

    Parameters
    ----------
    src : str
        移動元フォルダの絶対パス。コンテナ内ならどこでもよい。
    dest : str
        移動先フォルダの絶対パス。DATA_LOCATIONS のいずれかの配下に限る。
        存在しないフォルダは作らない（作成は SFTP 等の責務）。

    Returns
    -------
    dict
        {"ok", "msg", "src", "dest", "dest_key", "dest_label", "target",
         "file_count", "used_bytes", "same_fs"}
    """
    result = {
        "ok": False, "msg": "", "src": "", "dest": "",
        "dest_key": "", "dest_label": "",
        "target": "", "file_count": 0, "used_bytes": 0, "same_fs": False,
    }

    if not src or not str(src).strip():
        result["msg"] = "移動元が未入力です"
        return result
    src_path = Path(str(src).strip())
    if not src_path.is_absolute():
        result["msg"] = "移動元は絶対パスで指定してください"
        return result
    try:
        src_path = src_path.resolve()
    except OSError as exc:
        result["msg"] = f"移動元を解決できません: {exc}"
        return result
    if not src_path.is_dir():
        result["msg"] = "移動元フォルダが見つかりません"
        return result
    result["src"] = str(src_path)

    # マウントポイントそのものを動かすと復旧が面倒なので弾く
    for _key, label, root in _resolved_location_roots():
        if src_path == root:
            result["msg"] = f"「{label}」のルートフォルダは移動できません"
            return result

    if not dest or not str(dest).strip():
        result["msg"] = "移動先が未入力です"
        return result
    dest_dir = Path(str(dest).strip())
    if not dest_dir.is_absolute():
        result["msg"] = "移動先は絶対パスで指定してください"
        return result
    try:
        dest_dir = dest_dir.resolve()
    except OSError as exc:
        result["msg"] = f"移動先を解決できません: {exc}"
        return result
    result["dest"] = str(dest_dir)

    # 永続化されない場所へ「退避」しても意味が無いので、移動先は 4 か所に限る
    located = _location_for(dest_dir)
    if located is None:
        result["msg"] = (
            f"移動先は {location_labels()} の配下を指定してください"
        )
        return result
    result["dest_key"], result["dest_label"] = located

    if not dest_dir.is_dir():
        result["msg"] = f"移動先フォルダが見つかりません: {dest_dir}"
        return result

    target = dest_dir / src_path.name
    result["target"] = str(target)

    if dest_dir == src_path.parent:
        result["msg"] = "移動元は既に移動先の直下にあります"
        return result
    try:
        if dest_dir.is_relative_to(src_path):
            result["msg"] = "移動先が移動元の配下です"
            return result
    except ValueError:
        pass
    if target.exists():
        result["msg"] = f"移動先に同名の項目が既にあります: {target.name}"
        return result

    count, used = _dir_stats(src_path)
    result["file_count"] = count
    result["used_bytes"] = used

    # 同一ファイルシステムなら rename で済む。書き込み層 → ボリュームのように
    # 跨ぐ場合だけコピー＋削除になるので、そのときだけ空きを確認する。
    try:
        result["same_fs"] = src_path.stat().st_dev == dest_dir.stat().st_dev
    except OSError:
        result["same_fs"] = False
    if not result["same_fs"]:
        try:
            free = shutil.disk_usage(dest_dir).free
        except OSError:
            free = 0
        if free and used * 1.05 > free:
            result["msg"] = (
                f"移動先の空き容量が足りません "
                f"(必要 {format_bytes(used)} / 空き {format_bytes(free)})"
            )
            return result

    result["ok"] = True
    result["msg"] = "移動できます"
    return result


def _relink_projects(moved_dir: Path) -> list[str]:
    """移動先の `_project_meta.json` から projects.json のパスを貼り替える。

    既に登録済みのサブプロジェクトは `update_paths`、未登録なら `restore`。
    復元モーダル (project_callbacks.py) が既定を決めるのと同じ判定にしてある。
    """
    try:
        metas = scan_project_meta(str(moved_dir))
    except Exception as exc:  # noqa: BLE001
        logger.warning("メタデータのスキャンに失敗 (%s): %s", moved_dir, exc)
        return []
    if not metas:
        return []

    action_map: dict[str, str] = {}
    for meta in metas:
        proj_id = (meta.get("project") or {}).get("id", "")
        sub_id = (meta.get("sub_project") or {}).get("id", "")
        if not proj_id or not sub_id:
            continue
        if get_project(proj_id) and get_sub_project(proj_id, sub_id):
            action_map[sub_id] = "update_paths"
        else:
            action_map[sub_id] = "restore"
    if not action_map:
        return []

    try:
        return restore_projects_from_meta(metas, action_map)
    except Exception as exc:  # noqa: BLE001
        logger.warning("パス更新に失敗 (%s): %s", moved_dir, exc)
        return []


def move_entry(src: str, dest: str) -> dict:
    """`src` を `dest`（DATA_LOCATIONS 配下）へ移動し、パス参照も更新する。

    Returns
    -------
    dict
        {"ok", "msg", "old_path", "new_path", "path_updates"}
    """
    blocked = _running_analysis_block()
    if blocked:
        return {"ok": False, "msg": blocked, "old_path": "", "new_path": "",
                "path_updates": []}

    pre = preview_move(src, dest)
    if not pre["ok"]:
        return {"ok": False, "msg": pre["msg"], "old_path": "", "new_path": "",
                "path_updates": []}

    src_path = Path(pre["src"])
    target = Path(pre["target"])
    try:
        shutil.move(str(src_path), str(target))
    except (OSError, shutil.Error) as exc:
        logger.error("移動に失敗 (%s → %s): %s", src_path, target, exc)
        return {
            "ok": False,
            "msg": f"移動に失敗しました: {exc}",
            "old_path": "",
            "new_path": "",
            "path_updates": [],
        }

    logger.info("移動: %s → %s", src_path, target)
    return {
        "ok": True,
        "msg": f"移動しました: {target}",
        "old_path": str(src_path),
        "new_path": str(target),
        "path_updates": _relink_projects(target),
    }


# ---------------------------------------------------------------------------
# 結果フォルダの健全性チェック
# ---------------------------------------------------------------------------
# ver56.2: 出力先が /app 直下（コンテナの書き込み層）のまま解析した結果が
# `docker compose up -d --build` で消えた。厄介だったのは、消えた後も
# projects.json には登録が残り、結果フォルダ欄も古いパスを表示し続けるため
# **画面上どこにも異常が出なかった**こと。再ビルド前に気づける表を用意する。

RESULT_DIR_STATES = {
    "missing": "実体がありません。再解析が必要です",
    "volatile": "コンテナ内の一時領域です。次回の再ビルドで消えます",
}


def audit_result_dirs() -> list[dict]:
    """全サブプロジェクトの結果フォルダを調べ、危ないものだけ返す。

    Returns
    -------
    list[dict]
        `{"project_name", "sub_name", "path", "state"}` のリスト。
        `state` は `missing`（実体なし）か `volatile`（非永続な場所にある）。
        永続化された場所に実体があるものは返さない。
    """
    rows: list[dict] = []
    try:
        projects = list_projects()
    except Exception as exc:  # noqa: BLE001
        logger.warning("プロジェクト一覧の取得に失敗: %s", exc)
        return rows

    for proj in projects:
        for sub in proj.get("sub_projects", []):
            path = (sub.get("last_result_dir") or sub.get("output_dir") or "").strip()
            if not path:
                continue          # 未解析。まだ結果が無いだけなので警告しない
            try:
                exists = Path(path).is_dir()
            except OSError:
                exists = False
            if exists and is_persistent_path(path):
                continue
            rows.append({
                "project_name": proj.get("name", "(名称未設定)"),
                "sub_name": sub.get("name", "(名称未設定)"),
                "path": path,
                "state": "missing" if not exists else "volatile",
            })
    return rows


def find_meta_projects(key: str) -> list[dict]:
    """指定場所配下から _project_meta.json を持つフォルダを列挙

    Returns
    -------
    list[dict]
        scan_project_meta() の返り値そのまま
        (各要素は project/sub_project メタ + _found_dir キー)
    """
    root = get_location_root(key)
    if root is None or not root.is_dir():
        return []
    return scan_project_meta(str(root))


def find_meta_projects_everywhere() -> list[dict]:
    """DATA_LOCATIONS 4 か所すべてから `_project_meta.json` を集める。

    ver56.2: 従来は「解析出力」配下だけを見ていたが、実運用では結果を
    生データフォルダの隣（DESI/TIMS 生データ配下）に出しているため、
    **登録済み 33 件中 32 件が検出対象外**だった。

    「アプリ内部データ」(`Data/Other`) は「解析出力」(`Data/Other/output`) を
    含むので同じフォルダが 2 回拾われる。`_found_dir` で 1 つに畳み、
    先に見つかった側のラベルを `_found_location` に入れる。
    """
    seen: set[str] = set()
    out: list[dict] = []
    for key, label, _root in _resolved_location_roots():
        for meta in find_meta_projects(key):
            found = meta.get("_found_dir", "")
            if found in seen:
                continue
            seen.add(found)
            meta["_found_location"] = label
            out.append(meta)
    return out


def list_backup_generations(limit: int = 20) -> list[dict]:
    """起動時バックアップの世代一覧（新しい順、上限あり）"""
    try:
        all_backups = list_backups()
    except Exception:
        return []
    return all_backups[:limit] if limit else all_backups


def format_bytes(num) -> str:
    """バイト数を人間可読に整形"""
    if num is None:
        return "-"
    try:
        n = float(num)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} {unit}"
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
