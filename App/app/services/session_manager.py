# =============================================================================
# MSI Analysis Application - Session Manager
# セッション管理モジュール
# =============================================================================

import json
import os
import tempfile
from datetime import datetime

from filelock import FileLock

from app.config import SESSIONS_DIR


# ---------------------------------------------------------------------------
# 前回設定の自動保存・復元
# ---------------------------------------------------------------------------

_LAST_SETTINGS_FILE = SESSIONS_DIR / "last_settings.json"

# プロセス横断の排他ロック（複数ユーザー同時保存対策）
_last_settings_lock = FileLock(str(_LAST_SETTINGS_FILE) + ".lock", timeout=30)

# 自動保存対象のキー一覧
_AUTO_SAVE_KEYS = [
    "analysis_method", "analysis_method_tims",
    "data_folder", "annotation_path", "output_dir",
    "p_thresh", "logfc_thresh",
    "resume_rds", "rds_folder",
    "reanalysis_data_folder", "rds_path",
    "rds_folder_reanalysis", "cluster_source",
    "reanalysis_annotation_path",
    "filter_mode", "target_clusters",
    "ion_mode", "tolerance_mz",
    "reanalysis_ion_mode", "reanalysis_tolerance_mz",
    "reanalysis_p_thresh", "reanalysis_logfc_thresh",
    # ver56.5: 画面が `ls.get(...)` で復元しようとしているのに、ここに載って
    # いなかったため無音で捨てられていたキー。載っていないキーは例外も警告も
    # 出さずに落ちるので、渡した側も画面側も気づけない (R13-03/R13-05)。
    "adduct_filter", "reanalysis_adduct_filter",
    "mz_align_ppm", "use_annotation_check",
    "resume_reanalysis", "resume_reanalysis_dir",
    # 正規化設定は run_analysis が渡していたのに、ここに無いので毎回既定へ戻っていた
    "normalize_input", "norm_mode",
    "normalize_input_reanalysis", "norm_mode_reanalysis",
    # 解析シナリオ（切片アノテーションの意味 → 補正ポリシー）
    "tims_scenario", "reanalysis_tims_scenario",
    # サイドバー設定
    "desi_v8_script_path", "desi_cluster_filter_script_path",
    "tims_v8_script_path", "tims_cluster_filter_script_path",
    # DESI ROI 設定 (各 ROI を別サンプル化)
    "desi_use_roi_as_sample",
    "default_desi_data_folder", "default_annotation_file", "default_desi_output_dir",
    "default_tims_data_folder", "default_annotation_csv", "default_tims_output_dir",
    "default_output_dir",
    # キャリブレーション設定
    "calibration_table_data",
    "calibration_enable", "calibration_matrix",
    "calibration_search_window", "calibration_min_peaks",
    "calibration_regression_mode",
    # 再解析キャリブレーション
    "reanalysis_calibration_use_previous",
]


def save_last_settings(settings: dict) -> None:
    """前回の設定を自動保存（既存の保存済みデータとマージ・原子的書き込み）"""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    with _last_settings_lock:
        # 既存データを読み込んでマージ
        existing = load_last_settings()
        for k, v in settings.items():
            if k in _AUTO_SAVE_KEYS:
                existing[k] = v
        existing["_saved_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        # 一時ファイルに書いてから原子的に差し替え
        fd, tmp_path = tempfile.mkstemp(
            dir=str(SESSIONS_DIR), suffix=".tmp", prefix="last_settings_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, str(_LAST_SETTINGS_FILE))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def load_last_settings() -> dict:
    """前回の設定を読み込み（なければ空辞書）"""
    if not _LAST_SETTINGS_FILE.exists():
        return {}
    try:
        with open(_LAST_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
