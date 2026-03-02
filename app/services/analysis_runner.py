# =============================================================================
# MSI Analysis Application - Analysis Runner
# 解析実行エンジン
# パラメータ注入 + サブプロセス管理
# =============================================================================

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import RSCRIPT_PATH


# ---------------------------------------------------------------------------
# パラメータ注入ユーティリティ
# ---------------------------------------------------------------------------

def _r_str(value: str) -> str:
    """PythonのstrをRの文字列リテラルに変換（バックスラッシュをエスケープ）
    R版: r_str <- function(x) paste0('"', gsub('\\\\', '\\\\\\\\', x), '"')
    """
    escaped = value.replace("\\", "\\\\")
    return f'"{escaped}"'


def _replace_assign(lines: list[str], var: str, new_rhs: str) -> list[str]:
    """R代入文の右辺を置換。最初のマッチのみ。
    R版の正規表現: paste0("^\\s*", var, "\\s*<-\\s*.*$")
    """
    pattern = re.compile(rf"^\s*{re.escape(var)}\s*<-\s*.*$")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{var} <- {new_rhs}"
            break
    return lines


def _replace_block_assign(lines: list[str], var: str, new_rhs: str) -> list[str]:
    """R代入文の右辺を置換（複数行 if/else ブロック対応）。
    ブレース {} の深さを追跡して、複数行ブロック全体を1行に置換する。
    """
    pattern = re.compile(rf"^\s*{re.escape(var)}\s*<-\s*")
    for i, line in enumerate(lines):
        if pattern.match(line):
            depth = line.count("{") - line.count("}")
            end_idx = i
            while depth > 0 and end_idx + 1 < len(lines):
                end_idx += 1
                depth += lines[end_idx].count("{") - lines[end_idx].count("}")
            lines = lines[:i] + [f"{var} <- {new_rhs}"] + lines[end_idx + 1:]
            break
    return lines


def _replace_sample_names_block(
    lines: list[str],
    var_name: str,
    sample_names: list[str],
) -> list[str]:
    """複数行の sample_names <- c(...) ブロックを置換。
    R版と同じロジック: 開始行を見つけ、最大20行先まで閉じ括弧を探す。
    """
    start_pattern = re.compile(
        rf"^\s*{re.escape(var_name)}\s*<-\s*c\s*\("
    )
    close_pattern = re.compile(r"^\s*\)\s*$")

    start_idx = None
    for i, line in enumerate(lines):
        if start_pattern.match(line):
            start_idx = i
            break

    if start_idx is None:
        return lines

    # 閉じ括弧を探す（最大20行先まで）
    end_idx = start_idx
    for i in range(start_idx + 1, min(start_idx + 21, len(lines))):
        if close_pattern.match(lines[i]):
            end_idx = i
            break

    # 新しいブロックを構築（バックスラッシュをエスケープ: \ → \\）
    quoted = [f'  "{name.replace(chr(92), chr(92)*2)}"' for name in sample_names]
    new_block = f"{var_name} <- c(\n" + ",\n".join(quoted) + "\n)"
    new_block_lines = new_block.split("\n")

    # ブロック置換
    lines = lines[:start_idx] + new_block_lines + lines[end_idx + 1:]
    return lines


# ---------------------------------------------------------------------------
# 設定生成
# ---------------------------------------------------------------------------

def generate_v8_config(params: dict, output_dir: str) -> str:
    """v8 Templateスクリプト用の設定生成（DESI/TIMS共通）
    R版: generate_v8_config() in analysis_runner.R
    """
    template_path = params["template_path"]
    if not Path(template_path).exists():
        raise FileNotFoundError(
            f"v8 Templateスクリプトが見つかりません: {template_path}"
        )

    # 元スクリプトを読み込み
    with open(template_path, "r", encoding="utf-8") as f:
        lines = [line.rstrip("\n") for line in f.readlines()]

    # パラメータを置換
    lines = _replace_assign(lines, "data_folder", _r_str(params["data_folder"]))
    lines = _replace_assign(lines, "output_dir", _r_str(output_dir))
    lines = _replace_assign(
        lines, "RESUME_FROM_RDS",
        "TRUE" if params.get("resume_from_rds") else "FALSE",
    )

    # 途中再開用のRDSディレクトリパス
    if params.get("resume_from_rds") and params.get("resume_rds_paths"):
        rds_dir = str(Path(params["resume_rds_paths"][0]).parent)
        lines = _replace_assign(lines, "RESUME_DIR_PATH", _r_str(rds_dir))

    if params.get("mrm_path"):
        lines = _replace_assign(lines, "MRM_FILE_PATH", _r_str(params["mrm_path"]))

    if params.get("p_thresh") is not None:
        lines = _replace_assign(
            lines, "DEG_P_THRESH_VAL", str(params["p_thresh"])
        )

    if params.get("logfc_thresh") is not None:
        lines = _replace_assign(
            lines, "DEG_LOGFC_TH_VAL", str(params["logfc_thresh"])
        )

    # PROJECT_NAME_PREFIX を現在日時で設定
    prefix = f"Analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
    lines = _replace_assign(lines, "PROJECT_NAME_PREFIX", _r_str(prefix))

    # sample_names ブロック置換
    lines = _replace_sample_names_block(
        lines, "sample_names", params["sample_names"]
    )

    # --- TIMS固有パラメータ ---
    if params.get("input_paths"):
        # INPUT_PATHS ブロック置換 (TIMS ver13)
        lines = _replace_sample_names_block(
            lines, "INPUT_PATHS", params["input_paths"]
        )
    if params.get("output_dir_var") == "OUTPUT_DIR":
        lines = _replace_assign(lines, "OUTPUT_DIR", _r_str(output_dir))
    if params.get("project_label"):
        lines = _replace_assign(
            lines, "PROJECT_LABEL", _r_str(params["project_label"])
        )
    if params.get("annotation_csv_path"):
        lines = _replace_assign(
            lines, "ANNOTATION_CSV_PATH",
            _r_str(params["annotation_csv_path"]),
        )
    if params.get("ion_mode"):
        lines = _replace_assign(
            lines, "ION_MODE", _r_str(params["ion_mode"])
        )
    if params.get("tolerance_mz") is not None:
        lines = _replace_assign(
            lines, "DEFAULT_TOLERANCE_MZ", str(params["tolerance_mz"])
        )
    if params.get("adduct_patterns"):
        r_vec = "c(" + ", ".join(f'"{p}"' for p in params["adduct_patterns"]) + ")"
        lines = _replace_block_assign(lines, "ANNOT_ADDUCT_PATTERNS", r_vec)

    # 一時ファイルをlog/サブフォルダに保存
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    config_filename = f"v8_runtime_{timestamp}.R"
    log_dir = Path(output_dir) / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    config_path = log_dir / config_filename

    with open(config_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return str(config_path)


def generate_cluster_filter_config(params: dict, output_dir: str) -> str:
    """Cluster Filterスクリプト用の設定生成
    R版: generate_cluster_filter_config() in analysis_runner.R
    """
    template_path = params["template_path"]
    if not Path(template_path).exists():
        raise FileNotFoundError(
            f"Cluster Filterスクリプトが見つかりません: {template_path}"
        )

    with open(template_path, "r", encoding="utf-8") as f:
        lines = [line.rstrip("\n") for line in f.readlines()]

    lines = _replace_assign(lines, "RDS_PATH", _r_str(params["rds_path"]))
    lines = _replace_assign(
        lines, "ORIGINAL_DATA_FOLDER",
        _r_str(params["original_data_folder"]),
    )
    lines = _replace_assign(
        lines, "FILTER_MODE", _r_str(params["filter_mode"])
    )
    lines = _replace_assign(lines, "EXPORT_TXT_DIR", _r_str(output_dir))
    lines = _replace_assign(lines, "V8_OUTPUT_DIR", _r_str(output_dir))

    # TARGET_CLUSTERS の置換: c(0,1,5,7) 形式
    clusters_str = ",".join(str(c) for c in params["target_clusters"])
    lines = _replace_assign(lines, "TARGET_CLUSTERS", f"c({clusters_str})")

    # SAMPLE_NAMES ブロック置換
    if params.get("sample_names"):
        lines = _replace_sample_names_block(
            lines, "SAMPLE_NAMES", params["sample_names"]
        )

    # --- TIMS固有パラメータ ---
    if params.get("rds_run_dir"):
        lines = _replace_assign(
            lines, "RDS_RUN_DIR", _r_str(params["rds_run_dir"])
        )
    if params.get("original_input_paths"):
        lines = _replace_sample_names_block(
            lines, "ORIGINAL_INPUT_PATHS", params["original_input_paths"]
        )
    if params.get("export_data_dir"):
        lines = _replace_assign(
            lines, "EXPORT_DATA_DIR", _r_str(params["export_data_dir"])
        )

    # 一時ファイルをlog/サブフォルダに保存
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    config_filename = f"cluster_filter_runtime_{timestamp}.R"
    log_dir = Path(output_dir) / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    config_path = log_dir / config_filename

    with open(config_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return str(config_path)


# ---------------------------------------------------------------------------
# サブプロセス管理
# ---------------------------------------------------------------------------

def start_analysis_process(
    script_path: str,
    output_dir: str,
) -> dict:
    """Rスクリプトを外部プロセスで非同期実行
    R版: start_analysis_process() in analysis_runner.R

    改善点:
    - .batファイルを経由せず subprocess.Popen で直接起動
    - PIDを正確に取得（R版はPID取得不可だった）
    - CREATE_NO_WINDOW でコンソール非表示
    """
    if not Path(script_path).exists():
        return {
            "success": False,
            "message": "スクリプトが見つかりません",
            "process": None,
        }

    output_path = Path(output_dir)
    log_dir = output_path / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    progress_file = log_dir / "analysis_progress.txt"
    log_file = log_dir / "analysis_log.txt"
    pid_file = log_dir / "analysis_pid.txt"
    status_file = log_dir / "analysis_status.txt"

    # 初期化
    progress_file.write_text("0|準備中|0|1", encoding="utf-8")
    log_file.write_text("解析を開始しています...\n", encoding="utf-8")
    status_file.write_text("running", encoding="utf-8")

    # Rscript.exe のパス解決
    rscript = str(RSCRIPT_PATH)
    if not Path(rscript).exists():
        rscript = "Rscript"  # PATH上のRscriptにフォールバック

    # サブプロセスを起動（stdout/stderrをログファイルにリダイレクト）
    log_fh = None
    try:
        log_fh = open(log_file, "w", encoding="utf-8")
        process = subprocess.Popen(
            [rscript, "--vanilla", script_path],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(Path(script_path).parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        pid_file.write_text(str(process.pid), encoding="utf-8")
    except Exception as e:
        if log_fh:
            log_fh.close()
        return {
            "success": False,
            "message": f"プロセス起動エラー: {e}",
            "process": None,
        }

    return {
        "success": True,
        "message": "解析プロセスを開始しました",
        "process": process,
        "log_file_handle": log_fh,
        "pid": process.pid,
        "progress_file": str(progress_file),
        "log_file": str(log_file),
        "status_file": str(status_file),
    }


def get_analysis_progress(progress_file: str) -> dict:
    """解析プロセスの進捗を取得"""
    try:
        content = Path(progress_file).read_text(encoding="utf-8").strip()
        if not content:
            return {"percent": 0, "step": "準備中", "current": 0, "total": 1}
        # 最終行を使用
        last_line = content.strip().split("\n")[-1]
        parts = last_line.split("|")
        if len(parts) >= 4:
            return {
                "percent": int(parts[0]),
                "step": parts[1],
                "current": int(parts[2]),
                "total": int(parts[3]),
            }
    except Exception:
        pass
    return {"percent": 0, "step": "準備中", "current": 0, "total": 1}


def get_analysis_log(log_file: str, last_n: int = 50) -> str:
    """解析プロセスのログを取得（末尾N行）"""
    try:
        lines = Path(log_file).read_text(encoding="utf-8").splitlines()
        if len(lines) > last_n:
            lines = lines[-last_n:]
        return "\n".join(lines)
    except Exception:
        return ""


def get_analysis_status(status_file: str) -> str:
    """解析プロセスのステータスを取得"""
    try:
        content = Path(status_file).read_text(encoding="utf-8").strip()
        if content:
            return content.split("\n")[0].strip()
    except Exception:
        pass
    return "unknown"


def check_process_completion(
    process: subprocess.Popen,
    status_file: str,
    log_file_handle=None,
) -> Optional[str]:
    """プロセスの完了をチェックし、完了時にステータスを更新。
    戻り値: "finished", "error", または None（まだ実行中）
    """
    if process.poll() is None:
        return None  # まだ実行中

    # プロセス終了 → ログファイルハンドルを閉じる
    if log_file_handle:
        try:
            log_file_handle.close()
        except Exception:
            pass

    exit_code = process.returncode
    status = "finished" if exit_code == 0 else "error"
    Path(status_file).write_text(status, encoding="utf-8")
    return status


def stop_analysis_process(
    process: Optional[subprocess.Popen],
    output_dir: str,
    log_file_handle=None,
) -> bool:
    """解析プロセスを停止。
    改善点: psutilでプロセスツリーごと終了（R版の taskkill /IM Rscript.exe /F より安全）
    """
    status_file = Path(output_dir) / "log" / "analysis_status.txt"
    status_file.write_text("stopped", encoding="utf-8")

    if log_file_handle:
        try:
            log_file_handle.close()
        except Exception:
            pass

    if process is None:
        return False

    try:
        import psutil
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in children:
            child.terminate()
        parent.terminate()

        # 5秒待って、まだ生きていたら強制終了
        gone, alive = psutil.wait_procs(children + [parent], timeout=5)
        for p in alive:
            p.kill()
        return True
    except Exception:
        # psutilが使えない場合は subprocess 自体を終了
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            process.kill()
        return True
