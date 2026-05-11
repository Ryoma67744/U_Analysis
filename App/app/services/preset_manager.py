# =============================================================================
# MSI Analysis Application - Preset Manager
# パラメータプリセット管理
# =============================================================================

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from filelock import FileLock

from app.config import PRESETS_DIR

PRESETS_FILE = PRESETS_DIR / "presets.json"

# プロセス横断の排他ロック（複数ユーザー同時保存対策）
_presets_lock = FileLock(str(PRESETS_FILE) + ".lock", timeout=30)

# プリセットに保存する解析パラメータキー（パスは除外）
PRESET_KEYS = [
    "analysis_method", "analysis_method_tims",
    "ion_mode", "tolerance_mz", "adduct_filter",
    "p_thresh", "logfc_thresh",
    "calibration_enable", "calibration_matrix",
    "calibration_search_window", "calibration_min_peaks",
    "calibration_regression_mode",
    "filter_mode", "target_clusters",
    "reanalysis_ion_mode", "reanalysis_tolerance_mz",
    "reanalysis_adduct_filter",
    "reanalysis_p_thresh", "reanalysis_logfc_thresh",
]


def _load_all() -> dict:
    """プリセットファイルを読み込み"""
    if not PRESETS_FILE.exists():
        return {"presets": []}
    try:
        with open(PRESETS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"presets": []}


def _save_all(data: dict) -> None:
    """プリセットファイルに原子的に書き出し（書き込み中断による破損を防止）"""
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(PRESETS_DIR), suffix=".tmp", prefix="presets_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(PRESETS_FILE))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def list_presets() -> list[dict]:
    """プリセット名一覧を返す（[{"name": ..., "created_at": ...}, ...]）"""
    data = _load_all()
    return [
        {"name": p["name"], "created_at": p.get("created_at", "")}
        for p in data.get("presets", [])
    ]


def save_preset(name: str, params: dict) -> None:
    """プリセットを保存（同名は上書き）"""
    # PRESET_KEYS のみ保存
    filtered = {k: params[k] for k in PRESET_KEYS if k in params}
    filtered["_saved_at"] = datetime.now().isoformat()

    entry = {
        "name": name,
        "params": filtered,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    with _presets_lock:
        data = _load_all()
        presets = data.get("presets", [])
        # 同名があれば上書き
        presets = [p for p in presets if p.get("name") != name]
        presets.append(entry)
        data["presets"] = presets
        _save_all(data)


def load_preset(name: str) -> dict | None:
    """プリセットのパラメータを取得（見つからなければ None）"""
    data = _load_all()
    for p in data.get("presets", []):
        if p.get("name") == name:
            return p.get("params", {})
    return None


def delete_preset(name: str) -> bool:
    """プリセットを削除（成功で True）"""
    with _presets_lock:
        data = _load_all()
        presets = data.get("presets", [])
        new_presets = [p for p in presets if p.get("name") != name]
        if len(new_presets) == len(presets):
            return False
        data["presets"] = new_presets
        _save_all(data)
        return True
