# =============================================================================
# MSI Analysis Application - Export progress registry
#
# インタラクティブ「データ出力」の進捗を、作業スレッドと UI ポーリング callback の
# 間で受け渡すためのインプロセス・ジョブレジストリ（Dash 非依存・スレッド安全）。
# サーバは単一プロセス・マルチスレッドのため、モジュールグローバルをロックで共有する。
# =============================================================================

import threading
import uuid
from typing import Optional

_JOBS: "dict[str, dict]" = {}
_LOCK = threading.Lock()
_MAX_JOBS = 32


def new_job() -> str:
    """新規ジョブを作成し job_id を返す。上限超過時は完了済みジョブを掃除。"""
    job_id = uuid.uuid4().hex
    with _LOCK:
        if len(_JOBS) >= _MAX_JOBS:
            stale = [k for k, v in _JOBS.items() if v.get("status") != "running"]
            for k in stale[: len(_JOBS) - _MAX_JOBS + 1]:
                _JOBS.pop(k, None)
        _JOBS[job_id] = {"pct": 0, "label": "準備中…", "status": "running",
                         "download": None, "msg": ""}
    return job_id


def update_job(job_id: str, pct: int, label: str = "") -> None:
    """進捗 %（0-99 にクランプ・単調増加）とラベルを更新（running のときのみ）。"""
    with _LOCK:
        j = _JOBS.get(job_id)
        if j and j["status"] == "running":
            j["pct"] = max(j["pct"], max(0, min(99, int(pct))))
            if label:
                j["label"] = label


def finish_job(job_id: str, download, msg: str) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if j:
            j.update(pct=100, label="完了", status="done", download=download, msg=msg)


def fail_job(job_id: str, msg: str) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if j:
            j.update(status="error", label="失敗", msg=msg)


def get_job(job_id: str) -> Optional[dict]:
    """ジョブのスナップショット（コピー）を返す。存在しなければ None。"""
    with _LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j else None


def pop_job(job_id: str) -> None:
    with _LOCK:
        _JOBS.pop(job_id, None)
