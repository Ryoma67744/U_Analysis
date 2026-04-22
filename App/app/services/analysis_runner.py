# =============================================================================
# MSI Analysis Application - Analysis Runner
# 解析実行エンジン
# パラメータ注入 + サブプロセス管理
# =============================================================================

import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import RSCRIPT_PATH
from app.services.path_resolver import resolve_data_path

logger = logging.getLogger("msi.analysis_runner")


def _resolve_or_raise(data_folder: str) -> str:
    """data_folder が存在しなければ DATA_CANDIDATES 配下で再解決する。

    別マシン由来の絶対パスが projects.json に残っている場合の救済。
    """
    if not data_folder:
        return data_folder
    if Path(data_folder).exists():
        return data_folder
    resolved = resolve_data_path(data_folder, modality="auto", is_file=False)
    if resolved:
        logger.warning(
            "data_folder が見つからないため自動補正: %s → %s",
            data_folder, resolved,
        )
        return str(resolved)
    raise FileNotFoundError(
        f"データフォルダが見つかりません: {data_folder}\n"
        f".env の DESI_DATA_DIR / TIMS_DATA_DIR を確認してください。"
    )


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
# m/z キャリブレーション回帰計算
# ---------------------------------------------------------------------------

def compute_calibration_coefficients(
    table_data: list,
    regression_mode: str,
    min_peaks: int = 2,
) -> Optional[dict]:
    """キャリブレーションテーブルから多項式回帰係数を計算。

    モデル: error(mz) = observed_mz - reference_mz
           error = c_n * mz^n + c_{n-1} * mz^(n-1) + ... + c_0
    補正:  corrected_mz = mz - polyval(coefficients, mz)

    Returns:
        dict: {coefficients, degree, r_squared, n_points, regression_mode}
        None: データ不足時
    """
    import numpy as np

    pairs = []
    for row in (table_data or []):
        if not row.get("use"):
            continue
        try:
            ref = float(row["ref_mz"])
            obs = float(row["obs_mz"])
            if ref > 0 and obs > 0:
                pairs.append((ref, obs))
        except (ValueError, TypeError, KeyError):
            continue

    if len(pairs) < min_peaks:
        return None

    refs = np.array([p[0] for p in pairs])
    obs = np.array([p[1] for p in pairs])
    errors = obs - refs

    degree = {"linear": 1, "poly2": 2, "poly3": 3}.get(regression_mode, 3)
    coefficients = np.polyfit(refs, errors, degree)  # 降順 [c_n, ..., c_0]

    predicted = np.polyval(coefficients, refs)
    ss_res = np.sum((errors - predicted) ** 2)
    ss_tot = np.sum((errors - np.mean(errors)) ** 2)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0

    return {
        "coefficients": coefficients.tolist(),
        "degree": degree,
        "r_squared": round(r_squared, 6),
        "n_points": len(pairs),
        "regression_mode": regression_mode,
    }


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

    # data_folder が存在しない場合 (別マシン由来の古いパス) は自動補正を試行
    resolved_data_folder = _resolve_or_raise(params["data_folder"])

    # パラメータを置換
    lines = _replace_assign(lines, "data_folder", _r_str(resolved_data_folder))
    lines = _replace_assign(lines, "output_dir", _r_str(output_dir))
    lines = _replace_assign(
        lines, "RESUME_FROM_RDS",
        "TRUE" if params.get("resume_from_rds") else "FALSE",
    )

    # 途中再開用のRDSディレクトリパス
    if params.get("resume_from_rds") and params.get("resume_rds_paths"):
        rds_dir = str(Path(params["resume_rds_paths"][0]).parent)
        lines = _replace_assign(lines, "RESUME_DIR_PATH", _r_str(rds_dir))

    if params.get("annotation_path"):
        lines = _replace_assign(lines, "MRM_FILE_PATH", _r_str(params["annotation_path"]))

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

    # --- Annotation Filter ---
    if params.get("annotation_filter"):
        filter_values = params["annotation_filter"]
        r_vec = "c(" + ", ".join(f'"{v}"' for v in filter_values) + ")"
        lines = _replace_assign(lines, "ANNOTATION_FILTER", r_vec)

    # --- m/z キャリブレーション ---
    if params.get("calibration_enable"):
        lines = _replace_assign(lines, "CALIBRATION_ENABLE", "TRUE")
        coefs = params["calibration_coefficients"]
        lines = _replace_assign(
            lines, "CALIBRATION_COEFFICIENTS",
            "c(" + ", ".join(str(c) for c in coefs) + ")",
        )
        # サンプル別キャリブレーション
        if params.get("calibration_by_sample"):
            by_sample = params["calibration_by_sample"]
            entries = []
            for sname, scoefs in by_sample.items():
                r_coefs = "c(" + ", ".join(str(c) for c in scoefs) + ")"
                entries.append(f'  "{sname}" = {r_coefs}')
            r_list = "list(\n" + ",\n".join(entries) + "\n)"
            lines = _replace_assign(lines, "CALIBRATION_BY_SAMPLE", r_list)

    # --- m/z アライメント (ppm) ---
    if params.get("mz_align_ppm"):
        lines = _replace_assign(lines, "MZ_ALIGN_PPM", str(params["mz_align_ppm"]))

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

    # --- クラスタソースの注入（TIMS: resolve_rds_path()用） ---
    if params.get("cluster_source"):
        lines = _replace_assign(
            lines, "CLUSTER_SOURCE", _r_str(params["cluster_source"])
        )

    # --- 再解析用アノテーションファイルの注入 ---
    if params.get("reanalysis_annotation_path"):
        ann_path = params["reanalysis_annotation_path"]
        # TIMS (.csv) → ANNOTATION_CSV_PATH
        if ann_path.lower().endswith(".csv"):
            lines = _replace_assign(
                lines, "ANNOTATION_CSV_PATH", _r_str(ann_path)
            )
        # DESI (.xlsx) → MRM_FILE_PATH
        else:
            lines = _replace_assign(
                lines, "MRM_FILE_PATH", _r_str(ann_path)
            )

    # --- マージスクリプトパスの注入（DESI/TIMS共通） ---
    if params.get("merge_script_path"):
        lines = _replace_assign(
            lines, "MERGE_SCRIPT_PATH", _r_str(params["merge_script_path"])
        )

    # --- 本体スクリプトパスの動的注入（TIMS: V13_SCRIPT_PATH, DESI: V8_SCRIPT_PATH） ---
    if params.get("main_analysis_script_path"):
        is_tims = "DBSCAN" in Path(template_path).stem or "tims" in Path(template_path).stem.lower()
        var_name = "V13_SCRIPT_PATH" if is_tims else "V8_SCRIPT_PATH"
        lines = _replace_assign(
            lines, var_name, _r_str(params["main_analysis_script_path"])
        )

    # --- m/z キャリブレーション（再解析） ---
    if params.get("calibration_enable"):
        lines = _replace_assign(lines, "V13_CALIBRATION_ENABLE", "TRUE")
        coefs = params["calibration_coefficients"]
        lines = _replace_assign(
            lines, "V13_CALIBRATION_COEFFICIENTS",
            "c(" + ", ".join(str(c) for c in coefs) + ")",
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
    extra_args=None,
) -> dict:
    """Rスクリプトを外部プロセスで非同期実行
    R版: start_analysis_process() in analysis_runner.R

    改善点:
    - .batファイルを経由せず subprocess.Popen で直接起動
    - PIDを正確に取得（R版はPID取得不可だった）
    - CREATE_NO_WINDOW でコンソール非表示

    Args:
        script_path: 実行する R スクリプトの絶対パス
        output_dir:  ログや進捗ファイルを書き出すディレクトリ
        extra_args:  R スクリプトに渡す追加コマンドライン引数 (list[str] | None)
                     例: ["/path/to/folder", "--dry-run", "--backup"]
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
        cmd = [rscript, "--vanilla", script_path] + [
            str(a) for a in (extra_args or [])
        ]
        process = subprocess.Popen(
            cmd,
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



def get_analysis_log(log_file: str, last_n: int = 50) -> str:
    """解析プロセスのログを取得（末尾N行）"""
    try:
        lines = Path(log_file).read_text(encoding="utf-8").splitlines()
        if len(lines) > last_n:
            lines = lines[-last_n:]
        return "\n".join(lines)
    except Exception:
        return ""


def get_analysis_log_full(log_file: str) -> str:
    """解析プロセスのログ全文を取得（行数制限なし）"""
    try:
        return Path(log_file).read_text(encoding="utf-8")
    except Exception:
        return ""


def format_log_lines_styled(log_text: str, search: str = "",
                             level: str = "all") -> list:
    """ログテキストを html.Span のリストに変換。

    エラー行は赤、警告行は黄色、完了行は緑でスタイリング。
    search / level フィルタ適用後の行のみ返す。
    """
    from dash import html

    lines = log_text.splitlines()
    result = []

    for line in lines:
        line_lower = line.lower()

        # レベルフィルタ
        is_error = ("error" in line_lower or "exception" in line_lower
                     or "fatal" in line_lower)
        is_warn = "warn" in line_lower
        if level == "error" and not is_error:
            continue
        if level == "warning" and not is_error and not is_warn:
            continue

        # 検索フィルタ
        if search and search.lower() not in line_lower:
            continue

        # スタイル決定
        style = {"display": "block", "whiteSpace": "pre-wrap"}
        if is_error:
            style["color"] = "#f44747"
            style["fontWeight"] = "bold"
        elif is_warn:
            style["color"] = "#cca700"
        elif "done" in line_lower or "finished" in line_lower or "complete" in line_lower:
            style["color"] = "#6a9955"

        result.append(html.Span(line, style=style))

    return result


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
    if process is None or process.poll() is None:
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
