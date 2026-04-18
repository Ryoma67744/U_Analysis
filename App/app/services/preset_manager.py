# =============================================================================
# MSI Analysis Application - Preset Manager
# パラメータプリセット管理
# =============================================================================

import json
from datetime import datetime
from pathlib import Path

from app.config import PRESETS_DIR

PRESETS_FILE = PRESETS_DIR / "presets.json"

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
    """プリセットファイルに書き出し"""
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def list_presets() -> list[dict]:
    """プリセット名一覧を返す（[{"name": ..., "created_at": ...}, ...]）"""
    data = _load_all()
    return [
        {"name": p["name"], "created_at": p.get("created_at", "")}
        for p in data.get("presets", [])
    ]


def save_preset(name: str, params: dict) -> None:
    """プリセットを保存（同名は上書き）"""
    data = _load_all()
    presets = data.get("presets", [])

    # PRESET_KEYS のみ保存
    filtered = {k: params[k] for k in PRESET_KEYS if k in params}
    filtered["_saved_at"] = datetime.now().isoformat()

    entry = {
        "name": name,
        "params": filtered,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

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
    data = _load_all()
    presets = data.get("presets", [])
    new_presets = [p for p in presets if p.get("name") != name]
    if len(new_presets) == len(presets):
        return False
    data["presets"] = new_presets
    _save_all(data)
    return True
