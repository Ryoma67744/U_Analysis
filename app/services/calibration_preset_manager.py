# =============================================================================
# MSI Analysis Application - Calibration Preset Manager
# キャリブレーション設定のプリセット管理
# =============================================================================

import json
from datetime import datetime
from pathlib import Path

from app.config import PRESETS_DIR

CAL_PRESETS_FILE = PRESETS_DIR / "calibration_presets.json"


def _load_all() -> dict:
    """プリセットファイルを読み込み"""
    if not CAL_PRESETS_FILE.exists():
        return {"presets": []}
    try:
        with open(CAL_PRESETS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"presets": []}


def _save_all(data: dict) -> None:
    """プリセットファイルに書き出し"""
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    with open(CAL_PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def list_calibration_presets() -> list[dict]:
    """プリセット一覧 [{name, matrix, ion_mode, created_at}, ...]"""
    data = _load_all()
    return [
        {
            "name": p["name"],
            "matrix": p.get("params", {}).get("matrix", "?"),
            "ion_mode": p.get("params", {}).get("ion_mode", "?"),
            "created_at": p.get("created_at", ""),
        }
        for p in data.get("presets", [])
    ]


def save_calibration_preset(name: str, params: dict) -> None:
    """プリセットを保存（同名は上書き）"""
    data = _load_all()
    presets = data.get("presets", [])

    params["_saved_at"] = datetime.now().isoformat()

    entry = {
        "name": name,
        "params": params,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    # 同名があれば上書き
    presets = [p for p in presets if p.get("name") != name]
    presets.append(entry)
    data["presets"] = presets
    _save_all(data)


def load_calibration_preset(name: str) -> dict | None:
    """プリセットのパラメータを取得（見つからなければ None）"""
    data = _load_all()
    for p in data.get("presets", []):
        if p.get("name") == name:
            return p.get("params", {})
    return None


def delete_calibration_preset(name: str) -> bool:
    """プリセットを削除（成功で True）"""
    data = _load_all()
    presets = data.get("presets", [])
    new_presets = [p for p in presets if p.get("name") != name]
    if len(new_presets) == len(presets):
        return False
    data["presets"] = new_presets
    _save_all(data)
    return True
