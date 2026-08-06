# =============================================================================
# MSI Analysis Application - 解析ジョブ台帳
#
# 実行中の解析を「ブラウザの外」に記録する。
#
# なぜ必要か:
#   これまで実行中であることを示す情報は
#     - Popen オブジェクト   → Dash プロセスのモジュール変数（再起動で消える）
#     - dcc.Store(app_state) → ブラウザのメモリ（タブを閉じると消える）
#   の 2 か所にしか無く、`analysis_pid.txt` は書かれてはいたが**どこからも
#   読まれていなかった**。そのためブラウザを閉じるとアプリは解析を見失い、
#   完了処理（ステータス更新・結果のプロジェクト登録・レシート生成）が
#   永久に実行されなかった。
#
#   本モジュールは <output_dir>/log/analysis_job.json に最小限のジョブ情報を
#   書き、後から誰でも（別セッション・再起動後のアプリでも）拾えるようにする。
# =============================================================================

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("msi.job_registry")

JOB_FILE_NAME = "analysis_job.json"

# 台帳のスキーマ版。将来項目を増やしたときに古い台帳を判別できるようにする。
JOB_SCHEMA_VERSION = 1


def job_file_path(output_dir) -> Path:
    """<output_dir>/log/analysis_job.json のパス"""
    return Path(output_dir) / "log" / JOB_FILE_NAME


def write_job(output_dir, *, pid: int, analysis_type: str = "",
              project_id: str = "", sub_project_id: str = "",
              data_folder: str = "", script_path: str = "",
              analyst: str = "",
              started_at: Optional[str] = None) -> Optional[Path]:
    """解析の起動時にジョブ台帳を書く。失敗しても解析は止めない。

    analyst は起動した解析者名。[ver51.2] 進捗は誰でも見られるが停止は
    本人だけ、という判定に使う。未設定（単独運用・匿名）なら空文字。
    """
    path = job_file_path(output_dir)
    payload = {
        "schema": JOB_SCHEMA_VERSION,
        "pid": int(pid),
        "output_dir": str(output_dir),
        "analysis_type": analysis_type or "",
        "project_id": project_id or "",
        "sub_project_id": sub_project_id or "",
        "data_folder": data_folder or "",
        "script_path": script_path or "",
        "analyst": analyst or "",
        "started_at": started_at or datetime.now().isoformat(),
        "finalized": False,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from app.utils.file_locks import atomic_write_json
        atomic_write_json(payload, path)
        logger.info("ジョブ台帳を作成: pid=%s %s", pid, path)
        return path
    except Exception as e:  # noqa: BLE001
        logger.warning("ジョブ台帳の書き込みに失敗（解析は続行）: %s", e)
        return None


def read_job(output_dir) -> Optional[dict]:
    """ジョブ台帳を読む。無い/壊れているなら None。"""
    path = job_file_path(output_dir)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:  # noqa: BLE001
        logger.debug("ジョブ台帳の読み込みに失敗: %s (%s)", path, e)
        return None


def mark_finalized(output_dir) -> bool:
    """完了処理が済んだ印を付ける。二重実行を防ぐ。

    戻り値 True = このプロセスが印を付けた（＝完了処理を担当してよい）。
    False = 既に誰かが済ませていた、または台帳が無い。
    """
    path = job_file_path(output_dir)
    try:
        from app.utils.file_locks import atomic_write_json, get_or_create_lock
        lock = get_or_create_lock(path, timeout=30)
        with lock:
            data = read_job(output_dir)
            if data is None:
                return False
            if data.get("finalized"):
                return False
            data["finalized"] = True
            data["finalized_at"] = datetime.now().isoformat()
            atomic_write_json(data, path)
            return True
    except Exception as e:  # noqa: BLE001
        logger.warning("ジョブ台帳の更新に失敗: %s", e)
        return False


def may_stop(job: Optional[dict], analyst) -> bool:
    """この解析者が停止してよいか。

    [ver51.2] 進捗とログは誰でも見られるが、停止は起動した本人だけに許す。
    多人数運用で他人の解析を止められてしまうのを防ぐ。

    判定規則は project_manager.can_modify_project と同一に揃えてある:
      - 台帳が無い / analyst が空 → 全員可（単独運用、解析者未設定、
        ver51.1 以前に起動されたジョブ）
      - analyst が "Unknown user"（匿名） → 全員可
      - それ以外 → 表示名が一致した場合のみ可

    ここで後方互換を塞ぐと、単独運用の利用者や旧ジョブの持ち主が自分の
    解析を止められなくなる。

    Note:
        本関数はサーバ側ガード用。UI 側でも事前判定してボタンを無効化する
        こと（callback は直接叩けるので UI だけでは守れない）。
    """
    if not job:
        return True
    owner = (job.get("analyst") or "").strip()
    if not owner or owner == "Unknown user":
        return True
    return (analyst or "").strip() == owner


def is_pid_alive(pid) -> bool:
    """PID が生きているか。ゾンビは「死んでいる」と扱う。

    ゾンビ（親が回収していない終了済みプロセス）を「実行中」と誤判定すると、
    同時実行ガードや再接続処理が永久に詰まる。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        import psutil
        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return False
        return proc.status() != psutil.STATUS_ZOMBIE
    except ImportError:
        # psutil が無い環境向けのフォールバック（ゾンビは判別できない）
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
    except Exception as e:  # noqa: BLE001
        logger.debug("PID 生存確認に失敗: %s (%s)", pid, e)
        return False


# 台帳を探す深さ。結果フォルダは通常
#   <データルート>/<プロジェクト>/<出力>/log/  （＝深さ 2）
# だが、出力先はUIで自由に決められるため 2 階層固定だと取りこぼす。
# [ver51.1] 深さ 2 だけを見ていたため、出力先の置き方によっては再接続が
#   無言で起きなかった。0〜3 階層を見る。rglob は大きなデータツリーで
#   遅いので、深さを区切った glob を並べる方式は維持する。
_DEPTH_GLOBS = tuple("*/" * d + "log/" + JOB_FILE_NAME for d in range(0, 4))


def find_jobs(search_roots, *, depth_globs=None) -> list:
    """探索ルート配下からジョブ台帳を集める。

    同じ output_dir を複数の深さで拾わないよう重複を除く。
    """
    found = []
    seen = set()
    for root in search_roots or []:
        try:
            base = Path(root)
            if not base.is_dir():
                continue
            for pattern in (depth_globs or _DEPTH_GLOBS):
                for p in base.glob(pattern):
                    out_dir = p.parent.parent
                    key = str(out_dir.resolve())
                    if key in seen:
                        continue
                    seen.add(key)
                    data = read_job(out_dir)
                    if data:
                        found.append(data)
        except Exception as e:  # noqa: BLE001
            logger.debug("ジョブ台帳の探索に失敗: %s (%s)", root, e)
    return found


def find_running_job(search_roots) -> Optional[dict]:
    """「まだ生きている」解析を 1 件返す。無ければ None。

    同時実行は 1 本に制限されている（analysis_runner の同時実行ガード）ので、
    最も新しいものを 1 件返せば足りる。
    """
    alive = [j for j in find_jobs(search_roots)
             if not j.get("finalized") and is_pid_alive(j.get("pid"))]
    if not alive:
        return None
    alive.sort(key=lambda j: str(j.get("started_at") or ""), reverse=True)
    return alive[0]


def find_stale_jobs(search_roots) -> list:
    """「実行中の記録が残っているが、プロセスは既に居ない」ジョブ。

    アプリ再起動時の後始末に使う。コンテナ再起動で R ごと殺された場合、
    これを拾わないと analysis_status.txt が running のまま永久に残る。
    """
    return [j for j in find_jobs(search_roots)
            if not j.get("finalized") and not is_pid_alive(j.get("pid"))]
