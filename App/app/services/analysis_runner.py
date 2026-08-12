# =============================================================================
# MSI Analysis Application - Analysis Runner
# 解析実行エンジン
# パラメータ注入 + サブプロセス管理
# =============================================================================

import logging
import os
import re
import shutil
import signal
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import R_HELPERS_DIR, RSCRIPT_PATH

# PR-H3 C2: R subprocess の wallclock timeout (秒)。0 で無効化。
# 巨大データロード時の OOM / 無限ループ等で 1 ユーザーが全リソースを
# 占有するのを防止。Docker mem_limit と組み合わせて安定運用。
R_ANALYSIS_TIMEOUT_SEC = int(os.environ.get("R_ANALYSIS_TIMEOUT_SEC", 0))

# R 内部メモリ上限 (GB)。R >= 3.5 の R_MAX_VSIZE で利用。
# 0 / 未設定なら制限なし (Docker mem_limit に委ねる)。
R_MAX_VSIZE_GB = int(os.environ.get("R_MAX_VSIZE_GB", 0))

# R_MAX_VSIZE_GB がコンテナ上限に対してこの割合を下回ると「低すぎ」と判定して警告する。
# (mem_limit=12g に対し 8g 設定で解析が Parquet 読込直後に落ちた事故の再発防止)
_VSIZE_WARN_RATIO = 0.75


def _container_memory_limit_gb() -> Optional[float]:
    """コンテナ(cgroup)の物理メモリ上限を GB で返す。取得できなければ None。

    cgroup v2 は /sys/fs/cgroup/memory.max、v1 は memory/memory.limit_in_bytes。
    v2 は無制限時に "max" を返し、v1 は極端に大きい値を入れるため、いずれも
    「上限なし」とみなして None を返す。判定材料が無いときは呼び出し側で
    警告をスキップさせるため、例外は投げずに None に倒す。
    """
    for path in ("/sys/fs/cgroup/memory.max",
                 "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(path).read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            continue
        if not raw or raw == "max":
            continue
        try:
            limit = int(raw)
        except ValueError:
            continue
        # v1 の「無制限」は PAGE_SIZE 単位の巨大値。1PB 超は上限なしとみなす。
        if limit <= 0 or limit >= 1 << 50:
            continue
        return limit / (1024 ** 3)
    return None

# プロセスごとの watchdog timer 管理 (PID → Timer)。check_process_completion で
# 終了検知時に timer を cancel し、不要な kill を防ぐ。
_watchdog_timers: dict[int, threading.Timer] = {}
_watchdog_lock = threading.Lock()


def _schedule_watchdog(process: subprocess.Popen, log_file_handle=None) -> None:
    """R subprocess に対する wallclock timeout 監視を開始。

    R_ANALYSIS_TIMEOUT_SEC > 0 の場合のみ動作。N 秒経過してもプロセスが
    生きていれば SIGTERM → 5 秒後でも生きていれば SIGKILL を送る。

    ver45.8: kill の理由を解析ログにも書く。従来はアプリログ
    (Data/Other/logs/msi_app.log) にしか残らず、ユーザーが見る解析ログでは
    「ログが途中で途切れるだけ」に見えた。これが停止原因を長期にわたり
    メモリ不足と誤診する直接の原因になったため、ユーザーがエラーを見る場所に
    理由と対処を明記する。
    """
    if R_ANALYSIS_TIMEOUT_SEC <= 0:
        return

    def _kill_if_alive():
        try:
            if process.poll() is None:
                logger.warning(
                    "R subprocess pid=%s が R_ANALYSIS_TIMEOUT_SEC=%ds を超過、SIGTERM 送信",
                    process.pid, R_ANALYSIS_TIMEOUT_SEC,
                )
                if log_file_handle:
                    try:
                        log_file_handle.write(
                            f"\n[TIMEOUT] 実行時間が R_ANALYSIS_TIMEOUT_SEC="
                            f"{R_ANALYSIS_TIMEOUT_SEC}秒 "
                            f"({R_ANALYSIS_TIMEOUT_SEC / 60:.0f}分) を超過したため強制終了します。\n"
                            f"          解析自体は正常に進行していた可能性があります。"
                            f" .env の R_ANALYSIS_TIMEOUT_SEC を延長するか、"
                            f"0 を設定して無効化してください。\n"
                        )
                        log_file_handle.flush()
                    except Exception as e:
                        logger.debug(f"タイムアウト理由のログ追記に失敗（非重大）: {e}")
                process.terminate()
                # 5 秒待って強制 kill
                def _force_kill():
                    try:
                        if process.poll() is None:
                            logger.warning(
                                "R subprocess pid=%s が SIGTERM 後も生存、SIGKILL 送信",
                                process.pid,
                            )
                            process.kill()
                    except Exception:
                        pass
                t2 = threading.Timer(5.0, _force_kill)
                t2.daemon = True
                t2.start()
        except Exception:
            pass

    timer = threading.Timer(R_ANALYSIS_TIMEOUT_SEC, _kill_if_alive)
    timer.daemon = True
    timer.start()
    with _watchdog_lock:
        _watchdog_timers[process.pid] = timer


def _cancel_watchdog(pid: int) -> None:
    """プロセス正常終了時に watchdog を取り消す。"""
    with _watchdog_lock:
        timer = _watchdog_timers.pop(pid, None)
    if timer:
        timer.cancel()
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

    ★ ver55.0: 変数がテンプレートに無いとき、以前は**完全に無言で**素通りしていた。
    「注入したつもりが、実際はテンプレートのハードコード値がそのまま走っていた」
    という事故（再解析のアノテーション CSV 指定が破棄され、直書きの Dropbox パスが
    生き残る等）が、エラーもログも無いまま起き続けていた。見えるようにする。
    """
    pattern = re.compile(rf"^\s*{re.escape(var)}\s*<-\s*.*$")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{var} <- {new_rhs}"
            return lines
    logger.warning(
        "R テンプレートに変数 %r が無いため注入をスキップしました "
        "（テンプレート側の直書き値がそのまま使われます）", var
    )
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

    # ★ ver52.3: 使うと指定された行のうち、数値化できずに捨てた数を数える。
    #   従来は黙って捨てていたので、利用者がテーブルで 8 点を「使う」に
    #   していても、実際には 7 点で回帰されうる。点数が減ると下の
    #   `len(pairs) // 2 - 1` で **次数まで下がる**（poly3 → poly2）。
    #   利用者はテーブルを見て 8 点あると思っているので、
    #   なぜ次数が落ちたのかを知る手がかりが無かった。
    pairs = []
    unusable = 0
    for row in (table_data or []):
        if not row.get("use"):
            continue
        try:
            ref = float(row["ref_mz"])
            obs = float(row["obs_mz"])
        except (ValueError, TypeError, KeyError):
            unusable += 1
            continue
        if ref != ref or obs != obs:          # NaN も使えない
            unusable += 1
            continue
        if ref > 0 and obs > 0:
            pairs.append((ref, obs))
        else:
            unusable += 1

    if unusable:
        logger.warning(
            "キャリブレーション: 「使う」指定の %d 行を数値化できず除外した "
            "(有効 %d 点)。点数が減ると当てはめ次数も下がる",
            unusable, len(pairs))

    if len(pairs) < min_peaks:
        return None

    refs = np.array([p[0] for p in pairs])
    obs = np.array([p[1] for p in pairs])
    errors = obs - refs

    requested_degree = {"linear": 1, "poly2": 2, "poly3": 3}.get(regression_mode, 3)

    # ★ ver51.8: 次数を点数で抑える。
    #
    #   対話側 (interactive_calibration._calibrate_mz) には
    #   `degree = min(degree, len(obs_arr) - 1)  # 過学習防止` が元からあったが、
    #   こちらは抜けていた。ただし **n-1 では全く足りない**。n-1 は「ちょうど
    #   完全内挿できる」次数で、自由度ゼロ＝誤差を一切吸収しない。
    #
    #   実測 (DHB Positive の 4 点、真のドリフトは一定 +3 ppm、
    #   ピーク検出のばらつき ±0.5 mDa)。m/z 1000 での補正:
    #       次数 3 (自由度 0) : -20,807 ppm   ← 従来
    #       次数 2 (自由度 1) :   +114 ppm
    #       次数 1 (自由度 2) :   +0.2 ppm    ← 真値に一致
    #   高次項がピーク検出のばらつきを拾い、参照範囲 (m/z 136-379) の外へ
    #   外挿した瞬間に発散する。
    #
    #   そこで「パラメータ数の 2 倍以上の観測点を要求する」(n >= 2*(degree+1))。
    #   次数 1 なら 4 点、2 なら 6 点、3 なら 8 点。同梱リファレンスは 2〜4 点なので
    #   実質 linear に落ちる — これが m/z ドリフトの物理的にも妥当な既定。
    degree = max(1, min(requested_degree, len(pairs) // 2 - 1))

    coefficients = np.polyfit(refs, errors, degree)  # 降順 [c_n, ..., c_0]

    # ★ R² は自由度が足りないと **構造的に 1.0** になる (残差が定義上ゼロ)。
    #   従来はそれを「当てはまり完璧」として画面に出していたため、
    #   外挿で桁違いにずれていても利用者には気づく手段が無かった。
    #   評価できないときは None を返し、呼び出し側で「評価不能」と出す。
    #   ss_tot == 0 の既定も 1.0 ではなく 0.0 にする (対話側と同じ)。
    if len(pairs) <= degree + 1:
        r_squared = None
    else:
        predicted = np.polyval(coefficients, refs)
        ss_res = float(np.sum((errors - predicted) ** 2))
        ss_tot = float(np.sum((errors - np.mean(errors)) ** 2))
        r_squared = round(1.0 - (ss_res / ss_tot), 6) if ss_tot > 0 else 0.0

    return {
        "coefficients": coefficients.tolist(),
        "degree": degree,
        "requested_degree": requested_degree,
        "r_squared": r_squared,
        "n_points": len(pairs),
        # ★ ver52.3: 「使う」指定なのに数値化できず捨てた行数。
        #   利用者はテーブルの行数＝点数だと思っているので、食い違いを伝える。
        #   点数が減ると当てはめ次数も下がる (`len(pairs) // 2 - 1`) ため、
        #   「poly3 を選んだのに linear で当たっている」の理由がこれになりうる。
        "n_unusable": unusable,
        "regression_mode": regression_mode,
        # ★ 補正が保証されるのは参照ピークが張る範囲だけ。同梱リファレンスは
        #   m/z 136-379 しか無いのに補正対象は 1000 超まであり、外側は外挿になる。
        #   呼び出し側が警告を出せるよう範囲を返す。
        "ref_mz_min": float(np.min(refs)),
        "ref_mz_max": float(np.max(refs)),
    }


# ---------------------------------------------------------------------------
# 設定生成
# ---------------------------------------------------------------------------

def _copy_feature_annotation_sidecars(data_folders, output_dir) -> None:
    """入力データフォルダの `*_feature_annotations.parquet` を output_dir 直下へコピー。

    SCiLS 変換で生成した per-feature 注釈サイドカーを、インタラクティブ閲覧が
    結果フォルダ起点で見つけられるよう解析出力側へ運ぶ（Q2）。失敗は無害。
    """
    try:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        seen = set()
        for folder in data_folders:
            if not folder:
                continue
            d = Path(folder)
            if not d.is_dir() or str(d) in seen:
                continue
            seen.add(str(d))
            for sc in sorted(d.glob("*_feature_annotations.parquet")):
                dst = out / sc.name
                try:
                    # ★ ver51.8: 以前は `if not dst.exists()` で、同名が既にあると
                    #   **永久に更新されなかった**。ピークリストを直して再変換したり
                    #   molinfo_attach で化合物情報を付け直しても、結果フォルダ側は
                    #   古いサイドカーのまま。annotation_inspect は data_folder を
                    #   先に見るのに seurat_bridge は RDS 近傍（＝結果フォルダ）を
                    #   見るため、**プレビュー画面と本画面で違う化合物名が出る**。
                    src_st = sc.stat()
                    need_copy = True
                    if dst.exists():
                        dst_st = dst.stat()
                        need_copy = (src_st.st_mtime_ns > dst_st.st_mtime_ns
                                     or src_st.st_size != dst_st.st_size)
                    if need_copy:
                        shutil.copy2(str(sc), str(dst))
                        logger.info("注釈サイドカーをコピー: %s → %s", sc.name, out)
                except Exception as e:
                    logger.warning("サイドカーのコピーに失敗: %s", e)
    except Exception as e:
        logger.warning("サイドカーのコピー処理でエラー: %s", e)


def generate_v8_config(params: dict, output_dir: str) -> str:
    """v8 Templateスクリプト用の設定生成（DESI/TIMS共通）
    R版: generate_v8_config() in analysis_runner.R
    """
    template_path = params["template_path"]
    if not Path(template_path).exists():
        logger.error("v8 Template が見つかりません: %s", template_path)
        raise FileNotFoundError(
            f"v8 Template スクリプトが見つかりません: {Path(template_path).name}"
        )

    # 元スクリプトを読み込み
    with open(template_path, "r", encoding="utf-8") as f:
        lines = [line.rstrip("\n") for line in f.readlines()]

    # data_folder が存在しない場合 (別マシン由来の古いパス) は自動補正を試行
    resolved_data_folder = _resolve_or_raise(params["data_folder"])

    # DESI: Excel/CSV で登録されたサンプルを正規 .txt に変換してから R に渡す。
    # （R は data_folder/<sample>.txt を決め打ちで読むため。TIMS は input_paths を
    #   持つので除外。読取専用フォルダ時は staging に集約したパスへ差し替わる。）
    if not params.get("input_paths"):
        try:
            from app.services import desi_converter
            resolved_data_folder = desi_converter.prepare_desi_data_folder(
                resolved_data_folder, params.get("sample_names") or []
            )
        except Exception as e:
            logger.warning("DESI 入力の正規化(.txt変換)に失敗: %s", e)

    # パラメータを置換
    lines = _replace_assign(lines, "data_folder", _r_str(resolved_data_folder))
    lines = _replace_assign(lines, "output_dir", _r_str(output_dir))

    # ---- UMAP / クラスタリング ハイパーパラメータの注入（任意。提供時のみ上書き=未提供なら no-op）----
    # v15/ver5 テンプレで定数化済み。UI から渡された場合のみ上書きする。
    # _replace_assign はテンプレに無い変数名なら何もしない（DESI/TIMS 差異も安全）。
    _hp_int = {
        "umap_dims_n": "UMAP_DIMS_N", "umap_n_neighbors": "UMAP_N_NEIGHBORS",
        "cluster_dims_n": "CLUSTER_DIMS_N", "cluster_k_param": "CLUSTER_K_PARAM",
        "cluster_algorithm": "CLUSTER_ALGORITHM",
    }
    _hp_num = {
        "umap_min_dist": "UMAP_MIN_DIST",
        "cluster_resolution_single": "CLUSTER_RESOLUTION_SINGLE",
        "cluster_resolution_harmony": "CLUSTER_RESOLUTION_HARMONY",
        "cluster_resolution_rpca": "CLUSTER_RESOLUTION_RPCA",
        "cluster_resolution": "CLUSTER_RESOLUTION",
    }
    _hp_str = {
        "umap_metric": "UMAP_METRIC", "cluster_metric": "CLUSTER_METRIC",
        "pipeline_stage": "PIPELINE_STAGE",
    }
    for _k, _var in _hp_int.items():
        if params.get(_k) is not None:
            lines = _replace_assign(lines, _var, f"{int(params[_k])}L")
    for _k, _var in _hp_num.items():
        if params.get(_k) is not None:
            lines = _replace_assign(lines, _var, repr(float(params[_k])))
    for _k, _var in _hp_str.items():
        if params.get(_k) is not None:
            lines = _replace_assign(lines, _var, _r_str(str(params[_k])))

    # SCiLS 注釈サイドカーを結果フォルダへ運ぶ（Q2: インタラクティブ閲覧用）
    _copy_feature_annotation_sidecars(
        [resolved_data_folder]
        + [str(Path(p).parent) for p in (params.get("input_paths") or [])],
        output_dir,
    )
    lines = _replace_assign(
        lines, "RESUME_FROM_RDS",
        "TRUE" if params.get("resume_from_rds") else "FALSE",
    )

    # 途中再開用のRDSディレクトリパス
    if params.get("resume_from_rds") and params.get("resume_rds_paths"):
        rds_dir = str(Path(params["resume_rds_paths"][0]).parent)
        lines = _replace_assign(lines, "RESUME_DIR_PATH", _r_str(rds_dir))

    # DESI の化合物同定に使う MRM リスト。★ ver55.0: 条件付きで「注入しない」と
    #   テンプレート直書きのパスが生き残るため、選ばれていないときは明示的に空を入れる。
    #   （TIMS テンプレートは MRM_FILE_PATH を宣言していないので _replace_assign が
    #    警告ログを出して素通りする＝意図どおり。）
    if params.get("annotation_enable") and params.get("annotation_path"):
        lines = _replace_assign(lines, "MRM_FILE_PATH", _r_str(params["annotation_path"]))
    elif "MRM_FILE_PATH" in "\n".join(lines):
        lines = _replace_assign(lines, "MRM_FILE_PATH", _r_str(""))

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
    # ★ ver55.0: **無条件に**注入する。条件付きにすると「注入しない」がそのまま
    #   「テンプレートのハードコード値が生き残る」を意味してしまう。ver6.R は
    #   ANNOTATION_ENABLE <- TRUE と Windows の Dropbox パスを直書きしていたため、
    #   利用者が何も指定しなくても DB 照合が有効なまま走っていた。
    lines = _replace_assign(
        lines, "ANNOTATION_CSV_PATH",
        _r_str(params.get("annotation_csv_path") or ""),
    )
    lines = _replace_assign(
        lines, "ANNOTATION_ENABLE",
        "TRUE" if params.get("annotation_enable") else "FALSE",
    )
    lines = _replace_assign(
        lines, "USE_EMBEDDED_COMPOUND_NAMES",
        "TRUE" if params.get("use_embedded_annotation") else "FALSE",
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
    # --- 解析シナリオ → 補正ポリシー（ver6 の既存スイッチを注入。R本体は無改修） ---
    if params.get("annotation_role"):
        lines = _replace_assign(lines, "ANNOTATION_ROLE", _r_str(params["annotation_role"]))
    if params.get("batch_var"):
        lines = _replace_assign(lines, "BATCH_VAR", _r_str(params["batch_var"]))
    if "allow_condition_correction" in params:
        lines = _replace_assign(
            lines, "ALLOW_CONDITION_CORRECTION",
            "TRUE" if params["allow_condition_correction"] else "FALSE",
        )

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

    # --- 入力正規化ポリシー (二重正規化の回避: INPUT_NORMALIZED / NORM_MODE) ---
    # UIの「正規化 ON/OFF」を反映。OFF(=正規化済み入力)なら INPUT_NORMALIZED=TRUE で
    # LogNormalize をスキップし、NORM_MODE("none"/"sqrt"/"log1p")のみ適用する。
    if "input_normalized" in params:
        lines = _replace_assign(
            lines, "INPUT_NORMALIZED",
            "TRUE" if params["input_normalized"] else "FALSE",
        )
    if params.get("norm_mode"):
        lines = _replace_assign(lines, "NORM_MODE", _r_str(params["norm_mode"]))

    # --- DESI ROI 設定の注入 (USE_ROI_AS_SAMPLE / ROI_FILTER) ---
    # ROI 列があれば各 ROI を別サンプルとして Multi-sample mode (Harmony/RPCA) で
    # 統合解析する設定。analysis_callbacks.py で DESI 通常解析時のみセットされる。
    if "use_roi_as_sample" in params:
        flag = "TRUE" if params["use_roi_as_sample"] else "FALSE"
        lines = _replace_assign(lines, "USE_ROI_AS_SAMPLE", flag)
    if params.get("roi_filter"):
        roi_r = "c(" + ", ".join(_r_str(x) for x in params["roi_filter"]) + ")"
        lines = _replace_assign(lines, "ROI_FILTER", roi_r)

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
        logger.error("Cluster Filter テンプレが見つかりません: %s", template_path)
        raise FileNotFoundError(
            f"Cluster Filter スクリプトが見つかりません: {Path(template_path).name}"
        )

    with open(template_path, "r", encoding="utf-8") as f:
        lines = [line.rstrip("\n") for line in f.readlines()]

    lines = _replace_assign(lines, "RDS_PATH", _r_str(params["rds_path"]))

    # DESI 再解析: 元データが Excel/CSV 登録なら正規 .txt に変換してから R に渡す。
    # （TIMS は original_input_paths を持つため除外。）
    original_data_folder = params["original_data_folder"]
    if not params.get("original_input_paths"):
        try:
            from app.services import desi_converter
            original_data_folder = desi_converter.prepare_desi_data_folder(
                original_data_folder, params.get("sample_names") or []
            )
        except Exception as e:
            logger.warning("DESI 再解析入力の正規化(.txt変換)に失敗: %s", e)
    lines = _replace_assign(
        lines, "ORIGINAL_DATA_FOLDER",
        _r_str(original_data_folder),
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

    # --- ver46.0: 途中から再開（Step1/Step2 の中間結果を再利用） ---
    # 上の RDS_RUN_DIR は「どのクラスタリングの番号で除外するか」の参照元であり、
    # こちらは「どこまで計算済みの結果を再利用するか」。別の設定なので混同しないこと。
    # 再解析は完走に 2 時間超かかるため、Step3(RPCA) だけを検証したいときに使う。
    # 未指定なら V13_RESUME_FROM_RDS は FALSE のまま＝従来どおり最初から実行される。
    if params.get("resume_reanalysis") and params.get("resume_reanalysis_dir"):
        lines = _replace_assign(lines, "V13_RESUME_FROM_RDS", "TRUE")
        lines = _replace_assign(
            lines, "V13_RESUME_DIR_PATH", _r_str(params["resume_reanalysis_dir"])
        )

    # --- 再解析用アノテーションファイルの注入 ---
    # ★ ver55.0 (R-01): 置換先が `ANNOTATION_CSV_PATH` だったが、ReUMAP テンプレートが
    #   宣言しているのは **`V13_ANNOTATION_CSV_PATH`**。名前が一致しないので
    #   `_replace_assign` は無言で素通りし、UI で指定したアノテーション CSV は
    #   破棄され、テンプレート直書きの Windows Dropbox パスがそのまま走っていた。
    #   エラーも出ず解析は緑で完走するため、気づく手段が無かった。
    #   ReUMAP 側の規約: "" / NA は「ver13 側の設定を上書きしない」を意味する。
    ann_path = params.get("reanalysis_annotation_path") or ""
    if ann_path.lower().endswith(".csv"):
        # TIMS (.csv) → V13_ANNOTATION_CSV_PATH
        lines = _replace_assign(
            lines, "V13_ANNOTATION_CSV_PATH", _r_str(ann_path)
        )
        lines = _replace_assign(lines, "V13_ANNOTATION_ENABLE", "TRUE")
    elif ann_path:
        # DESI (.xlsx) → MRM_FILE_PATH
        lines = _replace_assign(lines, "MRM_FILE_PATH", _r_str(ann_path))
    else:
        # 指定なし = 化合物アノテーションを行わない。ここで明示的に空/FALSE を
        # 注入しないと、テンプレート直書きのパスが生き残る。
        lines = _replace_assign(lines, "V13_ANNOTATION_CSV_PATH", _r_str(""))
        lines = _replace_assign(lines, "V13_ANNOTATION_ENABLE", "FALSE")

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

    # --- DESI ROI 設定の注入 (USE_ROI_AS_SAMPLE / ROI_FILTER) ---
    # ROI 列があれば各 ROI を別サンプルとして Multi-sample mode (Harmony/RPCA) で
    # 統合解析する設定。analysis_callbacks.py で DESI 通常解析時のみセットされる。
    if "use_roi_as_sample" in params:
        flag = "TRUE" if params["use_roi_as_sample"] else "FALSE"
        lines = _replace_assign(lines, "USE_ROI_AS_SAMPLE", flag)
    if params.get("roi_filter"):
        roi_r = "c(" + ", ".join(_r_str(x) for x in params["roi_filter"]) + ")"
        lines = _replace_assign(lines, "ROI_FILTER", roi_r)

    # --- m/z キャリブレーション（再解析） ---
    if params.get("calibration_enable"):
        lines = _replace_assign(lines, "V13_CALIBRATION_ENABLE", "TRUE")
        coefs = params["calibration_coefficients"]
        lines = _replace_assign(
            lines, "V13_CALIBRATION_COEFFICIENTS",
            "c(" + ", ".join(str(c) for c in coefs) + ")",
        )

    # --- 入力正規化ポリシー（TIMS再解析: 二重正規化の回避。V13_* を ReUMAP.R が参照） ---
    if "input_normalized" in params:
        lines = _replace_assign(
            lines, "V13_INPUT_NORMALIZED",
            "TRUE" if params["input_normalized"] else "FALSE",
        )
    if params.get("norm_mode"):
        lines = _replace_assign(lines, "V13_NORM_MODE", _r_str(params["norm_mode"]))
    # --- 解析シナリオ → V13_ 経由で ver6 コピーへ伝播（make_v13_copy_with_settings） ---
    if params.get("v13_annotation_role"):
        lines = _replace_assign(lines, "V13_ANNOTATION_ROLE", _r_str(params["v13_annotation_role"]))
    if params.get("v13_batch_var"):
        lines = _replace_assign(lines, "V13_BATCH_VAR", _r_str(params["v13_batch_var"]))
    if "v13_allow_condition_correction" in params:
        lines = _replace_assign(
            lines, "V13_ALLOW_CONDITION_CORRECTION",
            "TRUE" if params["v13_allow_condition_correction"] else "FALSE",
        )

    # --- 再解析の DEG 閾値 / m/z アノテーション（フル解析と同じ値を再解析にも反映） ---
    #   DEG は両モード（TIMS=V13_DEG_*, DESI=V8_DEG_* 経由でメインテンプレ copy に伝播）。
    #   ion/tolerance は TIMS のみ（V13_ は既に伝播実装あり）。adduct は env 経路（ANNOT_ADDUCTS）。
    _is_tims_cf = ("DBSCAN" in Path(template_path).stem
                   or "tims" in Path(template_path).stem.lower())
    if params.get("p_thresh") is not None:
        lines = _replace_assign(
            lines,
            "V13_DEG_P_THRESH_VAL" if _is_tims_cf else "V8_DEG_P_THRESH_VAL",
            str(params["p_thresh"]),
        )
    if params.get("logfc_thresh") is not None:
        lines = _replace_assign(
            lines,
            "V13_DEG_LOGFC_TH_VAL" if _is_tims_cf else "V8_DEG_LOGFC_TH_VAL",
            str(params["logfc_thresh"]),
        )
    if _is_tims_cf and params.get("ion_mode"):
        lines = _replace_assign(lines, "V13_ION_MODE", _r_str(params["ion_mode"]))
    if _is_tims_cf and params.get("tolerance_mz") is not None:
        lines = _replace_assign(lines, "V13_TOLERANCE_MZ", str(params["tolerance_mz"]))

    # --- PreFlight: reduction_only 再解析（① 用）。クラスタフィルタ側の新定数
    #     RERUN_PIPELINE_STAGE を経由してメインテンプレ copy の PIPELINE_STAGE へ伝播。
    #     未指定なら "full"（従来の通常再解析）。DESI ver3 / TIMS ver18 が参照。 ---
    if params.get("pipeline_stage"):
        lines = _replace_assign(
            lines, "RERUN_PIPELINE_STAGE", _r_str(params["pipeline_stage"]),
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

# [ver51.5] 起動処理そのものを直列化する。
#   同時実行の確認から Popen・ジョブ台帳の書き込みまでの間にロックが無いと、
#   2 人が同時に実行ボタンを押したとき両方とも「実行中の解析は無い」と判定して
#   すり抜ける（run_app.py の waitress は既定 8 スレッド／1 プロセス）。
#   本関数は Popen したら待たずに返るので、保持時間は起動準備の間だけ。
_start_lock = threading.Lock()


def _find_running_job_for_guard():
    """起動ガード用に、生きている解析を 1 件返す。無ければ None。

    [ver51.5] 探索に失敗した場合は「実行中は無い」ではなく `_scan_failed` を
    立てた dict を返し、呼び出し側に起動を拒否させる（fail-closed）。
    ver51.4 までの psutil 版は例外時に running_r=[] として素通りしていた。
    """
    from app.services import job_registry
    try:
        return job_registry.find_running_job(job_registry.default_search_roots())
    except Exception as e:  # noqa: BLE001
        logger.warning("ジョブ台帳の探索に失敗、安全側に倒して起動を拒否: %s", e)
        return {"_scan_failed": True}


def start_analysis_process(
    script_path: str,
    output_dir: str,
    extra_args=None,
    env_extra: dict | None = None,
    interpreter: list[str] | None = None,
    job_meta: dict | None = None,
) -> dict:
    """解析プロセスを起動する（同時起動を直列化する薄い外皮）。

    実体は _start_analysis_process_locked。確認と起動の間に他スレッドが
    割り込めないよう、全体を 1 つのロックで囲む。
    """
    with _start_lock:
        return _start_analysis_process_locked(
            script_path, output_dir,
            extra_args=extra_args, env_extra=env_extra,
            interpreter=interpreter, job_meta=job_meta,
        )


def _start_analysis_process_locked(
    script_path: str,
    output_dir: str,
    extra_args=None,
    env_extra: dict | None = None,
    interpreter: list[str] | None = None,
    job_meta: dict | None = None,
) -> dict:
    """Rスクリプトを外部プロセスで非同期実行
    R版: start_analysis_process() in analysis_runner.R

    呼び出し元は start_analysis_process のみ（_start_lock 保持が前提）。

    改善点:
    - .batファイルを経由せず subprocess.Popen で直接起動
    - PIDを正確に取得（R版はPID取得不可だった）
    - CREATE_NO_WINDOW でコンソール非表示
    - 起動前に同時解析数と空きメモリ・ディスク容量をチェック（OOM/容量枯渇対策）

    Args:
        script_path: 実行する R スクリプトの絶対パス
        output_dir:  ログや進捗ファイルを書き出すディレクトリ
        extra_args:  R スクリプトに渡す追加コマンドライン引数 (list[str] | None)
                     例: ["/path/to/folder", "--dry-run", "--backup"]
        interpreter: 起動コマンドの先頭部分を差し替える (list[str] | None)。
                     None (既定) なら従来どおり [Rscript, "--vanilla"]。
                     Python 製の保守ツールを同じ枠組み（同時実行ブロック・
                     空きメモリ/ディスクチェック・ログ退避・watchdog）で
                     走らせるための口。例: [sys.executable, "-u"]
                     ※ -u は必須。Python は stdout がファイルだとブロック
                        バッファになり、進捗ログがプロセス終了まで出ない。
        job_meta:    ジョブ台帳に残す付帯情報 (dict | None)。
                     analysis_type / project_id / sub_project_id / data_folder。
                     [ver51.0] これを渡すと <output_dir>/log/analysis_job.json に
                     記録され、サーバ側ウォッチャーが終了を待って完了処理を行う。
                     ブラウザを閉じても結果がプロジェクトに登録される。
                     None（保守ツールなど）なら台帳もウォッチャーも作らず、
                     従来どおり呼び出し側のポーリングに委ねる。
    """
    if not Path(script_path).exists():
        return {
            "success": False,
            "message": "スクリプトが見つかりません",
            "process": None,
        }

    # --- 同時解析ブロック・空きメモリチェック（クラウド多人数運用対策） ---
    import psutil

    # [ver51.5] 判定源をプロセス名からジョブ台帳に変えた。
    #   ver51.4 まではここで psutil の名前に "rscript" が含まれるかを見ていたが、
    #   Unix の Rscript は最終的に $R_HOME/bin/exec/R へ exec するのでプロセス名は
    #   "R" になり、**Linux では一度も発動していなかった**（Windows の
    #   Rscript.exe でだけ効いていた）。台帳なら PID の生死で判定するので
    #   プラットフォームに依存せず、しかも誰が実行中かを利用者に伝えられる。
    #   保守ツール（job_meta is None）もここで弾く。preflight_callbacks.py の
    #   「解析中は診断を起動できない＝想定どおり」を保つため。逆向き（保守ツールが
    #   解析を弾く）は台帳に載らないので効かない。既知の穴。
    busy = _find_running_job_for_guard()
    if busy is not None:
        if busy.get("_scan_failed"):
            return {
                "success": False,
                "message": (
                    "実行中の解析を確認できませんでした。安全のため起動を見送ります。"
                    "しばらく待ってから再実行してください。"
                ),
                "process": None,
            }
        owner = (busy.get("analyst") or "").strip()
        who = f"{owner} さんの解析" if owner else "別の解析"
        return {
            "success": False,
            "message": (
                f"{who}が実行中です。完了してから再実行してください。"
            ),
            "process": None,
        }

    try:
        available_gb = psutil.virtual_memory().available / (1024 ** 3)
    except Exception as e:
        logger.warning(f"利用可能メモリ取得失敗、無制限と見做す: {e}")
        available_gb = float("inf")
    if available_gb < 10.0:
        return {
            "success": False,
            "message": (
                f"空きメモリが不足しています（残 {available_gb:.1f} GB）。"
                "他の解析の完了をお待ちください。"
            ),
            "process": None,
        }

    # ディスク空き容量チェック（output_dir 配下のディスク）
    # output_dir が未作成のことがあるため、存在する最も近い祖先で確認する
    try:
        check_path = Path(output_dir)
        while not check_path.exists() and check_path != check_path.parent:
            check_path = check_path.parent
        free_gb = psutil.disk_usage(str(check_path)).free / (1024 ** 3)
    except Exception as e:
        logger.warning(f"ディスク空き容量取得失敗、無制限と見做す: {e}")
        free_gb = float("inf")
    if free_gb < 10.0:
        return {
            "success": False,
            "message": (
                f"ディスク空き容量が不足しています（残 {free_gb:.1f} GB）。"
                "古いプロジェクトを整理するか、ストレージを拡張してください。"
            ),
            "process": None,
        }
    elif free_gb < 30.0:
        logger.warning(
            "ディスク空き容量が逼迫しています（残 %.1f GB）。近いうちに整理を推奨します。",
            free_gb,
        )
    # --- ここまで ---

    output_path = Path(output_dir)
    log_dir = output_path / "log"
    log_history_dir = log_dir / "history"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_history_dir.mkdir(parents=True, exist_ok=True)
    progress_file = log_dir / "analysis_progress.txt"
    log_file = log_dir / "analysis_log.txt"
    pid_file = log_dir / "analysis_pid.txt"
    status_file = log_dir / "analysis_status.txt"

    # 旧ログを history/ にタイムスタンプ付きで退避（並行解析時の上書き消失防止）
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for src, prefix in [
        (log_file, "analysis_log"),
        (progress_file, "analysis_progress"),
        (status_file, "analysis_status"),
        (pid_file, "analysis_pid"),
    ]:
        if src.exists():
            try:
                src.replace(log_history_dir / f"{prefix}_{ts}.txt")
            except OSError:
                pass

    # 履歴上限：各種類最新20件まで保持
    for prefix in ["analysis_log_", "analysis_progress_",
                   "analysis_status_", "analysis_pid_"]:
        files = sorted(log_history_dir.glob(f"{prefix}*"))
        for old in files[:-20]:
            try:
                old.unlink()
            except OSError:
                pass

    # 新規ログ初期化
    progress_file.write_text("0|準備中|0|1", encoding="utf-8")
    log_file.write_text("解析を開始しています...\n", encoding="utf-8")
    status_file.write_text("running", encoding="utf-8")

    # Rscript.exe のパス解決
    rscript = str(RSCRIPT_PATH)
    if not Path(rscript).exists():
        rscript = "Rscript"  # PATH上のRscriptにフォールバック

    # R内部の数値計算ライブラリのスレッド数を制限（CPU独占を防止）
    child_env = os.environ.copy()
    child_env.setdefault("OMP_NUM_THREADS", "4")
    child_env.setdefault("OPENBLAS_NUM_THREADS", "4")
    child_env.setdefault("MKL_NUM_THREADS", "4")

    # PR-H3 C2: R 内部メモリ上限 (R >= 3.5)。R_MAX_VSIZE_GB > 0 で有効化。
    # R が指定上限を超えると "Error: vector memory exhausted" で安全に終了し、
    # システム全体の OOM を回避できる。
    # ver45.4: 適用したこと自体をログ先頭に残す。無言で効くと「なぜ N GB で落ちたか」を
    # 解析ログだけから追えず、低すぎる設定が原因の停止を誤診しやすいため。
    log_notes: list[str] = []
    # ver45.8: 有効な実行制限は「すべて」開始時に明示する。
    # R_ANALYSIS_TIMEOUT_SEC はこれまで無言で適用されており、超過して kill されても
    # 解析ログにはログが途切れた形跡しか残らなかった。これが停止原因を長期にわたり
    # メモリ不足と誤診する原因になった。
    if R_ANALYSIS_TIMEOUT_SEC > 0:
        log_notes.append(
            f"[NOTE] 実行時間の上限 R_ANALYSIS_TIMEOUT_SEC={R_ANALYSIS_TIMEOUT_SEC}秒"
            f" ({R_ANALYSIS_TIMEOUT_SEC / 60:.0f}分) が有効です。"
            f" 超過すると解析途中でも強制終了されます"
            f"（.env で延長、0 で無効化）。"
        )
    if R_MAX_VSIZE_GB > 0:
        # R_MAX_VSIZE は "<n>Gb" 形式の文字列で受け取る
        child_env["R_MAX_VSIZE"] = f"{R_MAX_VSIZE_GB}Gb"
        log_notes.append(
            f"[NOTE] R メモリ上限 R_MAX_VSIZE_GB={R_MAX_VSIZE_GB}GB を適用しました。"
        )
        limit_gb = _container_memory_limit_gb()
        if limit_gb and R_MAX_VSIZE_GB < limit_gb * _VSIZE_WARN_RATIO:
            logger.warning(
                "R_MAX_VSIZE_GB=%dGB はコンテナ上限 %.1fGB に対して低すぎます。"
                "大規模解析が開始直後に 'vector memory limit ... reached' で失敗する恐れがあります。",
                R_MAX_VSIZE_GB, limit_gb,
            )
            log_notes.append(
                f"[WARN] この値はコンテナのメモリ上限 {limit_gb:.1f}GB に対して低すぎます。"
                " 解析が 'vector memory limit of N Gb reached' で早期終了する場合は、"
                " .env の R_MAX_VSIZE_GB を 0 (制限なし) にするか、"
                f" コンテナ上限の 9 割程度 ({round(limit_gb * 0.9)}) まで引き上げてください。"
            )

    # サブプロセスを起動（stdout/stderrをログファイルにリダイレクト）
    # R スクリプトが App/Script/helpers/rds_io.R を解決できるよう、
    # R_HELPERS_DIR を子プロセスの環境変数に必ず渡す。
    # （本解析はランタイムコピーを <output_dir>/log/*.R として生成して
    #   起動するため、R 側の相対探索 ../helpers/rds_io.R はヒットしない）
    child_env["R_HELPERS_DIR"] = str(R_HELPERS_DIR)

    # 呼び出し元が指定した追加環境変数（例: TIMS再解析の ANNOT_ADDUCTS）
    if env_extra:
        child_env.update({str(k): str(v) for k, v in env_extra.items()})

    log_fh = None
    try:
        log_fh = open(log_file, "w", encoding="utf-8")
        # R の出力より前にメモ書きを流し込む（Popen へ渡す前なので必ず先頭に来る）。
        # ユーザーがエラーを見る場所そのものに原因と対処を出すのが狙い。
        if log_notes:
            log_fh.write("\n".join(log_notes) + "\n")
            log_fh.flush()
        launcher = list(interpreter) if interpreter else [rscript, "--vanilla"]
        cmd = launcher + [script_path] + [
            str(a) for a in (extra_args or [])
        ]
        process = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(Path(script_path).parent),
            env=child_env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        pid_file.write_text(str(process.pid), encoding="utf-8")
        # PR-H3 C2: wallclock timeout 監視を開始
        # ver45.8: kill 理由を解析ログにも書けるようログハンドルを渡す
        _schedule_watchdog(process, log_fh)

        # [ver51.0] ジョブ台帳とサーバ側ウォッチャー。
        #   これが無いと、ブラウザを閉じた瞬間に完了処理の実行者がいなくなり、
        #   計算は完走するのに「実行中のまま・結果がプロジェクトに紐づかない・
        #   子プロセスがゾンビになる」状態になる。
        #   job_meta が None の保守ツール（RDS 軽量化 / Parquet 再パック）は
        #   従来どおり呼び出し側のポーリングに委ねる。
        if job_meta is not None:
            try:
                from app.services import job_registry, job_watcher
                job = {
                    "pid": process.pid,
                    "output_dir": str(output_dir),
                    "analysis_type": job_meta.get("analysis_type", ""),
                    "project_id": job_meta.get("project_id", ""),
                    "sub_project_id": job_meta.get("sub_project_id", ""),
                    "data_folder": job_meta.get("data_folder", ""),
                    "script_path": str(script_path),
                    "analyst": job_meta.get("analyst", ""),
                }
                job_registry.write_job(
                    output_dir, pid=process.pid,
                    analysis_type=job["analysis_type"],
                    project_id=job["project_id"],
                    sub_project_id=job["sub_project_id"],
                    data_folder=job["data_folder"],
                    script_path=job["script_path"],
                    analyst=job["analyst"],
                )
                job_watcher.watch(
                    process, output_dir,
                    status_file=str(status_file),
                    log_file_handle=log_fh,
                    job=job,
                )
                # [ver51.5] 実行ボタンの無効化表示が最大 TTL 秒ぶん遅れるのを防ぐ。
                job_registry.invalidate_scan_cache()
            except Exception as e:  # noqa: BLE001
                # 監視が付かないだけで解析自体は従来どおり動く
                logger.warning("ジョブ監視の設定に失敗（解析は続行）: %s", e)
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
    except Exception as e:
        logger.warning(f"ログ末尾読込失敗: {e}")
        return ""


def get_analysis_log_full(log_file: str) -> str:
    """解析プロセスのログ全文を取得（行数制限なし）"""
    try:
        return Path(log_file).read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"ログ全文読込失敗: {e}")
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

    # PR-H3 C2: 正常終了したので watchdog を取り消す
    try:
        _cancel_watchdog(process.pid)
    except Exception:
        pass

    exit_code = process.returncode
    status = "finished" if exit_code == 0 else "error"

    # [ver51.1] 利用者が停止した場合は "error" で上書きしない。
    #   stop_analysis_process が先に "stopped" を書いてから SIGTERM を送るため、
    #   ここでは負の終了コードになり、無条件に error として扱われていた。
    #   その結果「自分で止めたのに『解析でエラーが発生しました』」と出ていた。
    #   job_watcher._resolve_status は既に stopped を尊重しており、
    #   放置すると台帳は stopped・画面は error という食い違いになる。
    if status == "error" and get_analysis_status(status_file) == "stopped":
        status = "stopped"

    # [ver45.8] 終了コード/シグナルを必ず記録する。
    # R がエラーメッセージを出さずにログが途切れるケースでは、この値だけが原因を分ける:
    #   負値 = シグナルによる強制終了 (-9 SIGKILL: OOM killer や外部 kill /
    #          -11 SIGSEGV: ネイティブコードのクラッシュ / -15 SIGTERM: 停止要求)
    #   正値 = R 自身が異常終了 (通常はエラーメッセージがログに残る)
    # これが無かったため「無言終了 = OOM」と誤って推定していた。
    if exit_code is not None and exit_code < 0:
        try:
            signame = signal.Signals(-exit_code).name
        except (ValueError, AttributeError):
            signame = "UNKNOWN"
        detail = f"シグナル {signame}({-exit_code}) による強制終了"
    else:
        detail = f"終了コード {exit_code}"
    logger.info("R subprocess pid=%s 終了: %s (status=%s)", process.pid, detail, status)

    # 解析ログの末尾にも残す（ユーザーがエラーを見る場所そのものに出す）
    if log_file_handle and exit_code != 0:
        try:
            log_file_handle.write(f"\n[EXIT] R プロセスは {detail} で終了しました。\n")
            log_file_handle.flush()
        except Exception as e:
            logger.debug(f"終了コードのログ追記に失敗（非重大）: {e}")

    # プロセス終了 → ログファイルハンドルを閉じる
    if log_file_handle:
        try:
            log_file_handle.close()
        except Exception as e:
            logger.debug(f"ログハンドルクローズ失敗（非重大）: {e}")

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
        except Exception as e:
            logger.debug(f"ログハンドルクローズ失敗（非重大）: {e}")

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
