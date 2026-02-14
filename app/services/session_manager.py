# =============================================================================
# MSI Analysis Application - Session Manager
# セッション管理モジュール
# =============================================================================

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


def save_session(
    session_data: dict,
    output_dir: str,
    session_name: Optional[str] = None,
) -> str:
    """セッションデータをJSONとして保存"""
    if session_name is None:
        session_name = datetime.now().strftime("session_%Y%m%d_%H%M%S")

    # メタデータ追加
    session_data["meta"] = {
        "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "app_version": "2.0.0",
    }

    sessions_dir = Path(output_dir) / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    file_path = sessions_dir / f"{session_name}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=2, ensure_ascii=False)

    return str(file_path)


def load_session(session_path: str) -> dict:
    """セッションデータを読み込み"""
    path = Path(session_path)
    if not path.exists():
        raise FileNotFoundError(f"Session file not found: {session_path}")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_sessions(output_dir: str) -> list[dict]:
    """保存されたセッション一覧を取得"""
    sessions_dir = Path(output_dir) / "sessions"
    if not sessions_dir.is_dir():
        return []

    sessions = []
    for f in sorted(sessions_dir.glob("*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            created_at = (data.get("meta") or {}).get("created_at", "Unknown")
        except Exception:
            created_at = "Error"

        sessions.append({
            "name": f.stem,
            "path": str(f),
            "created_at": created_at,
        })

    return sessions


def delete_session(session_path: str) -> bool:
    """セッションを削除"""
    path = Path(session_path)
    if path.exists():
        path.unlink()
        return True
    return False
