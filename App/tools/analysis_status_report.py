#!/usr/bin/env python3
# =============================================================================
# MSI Analysis Application - 解析の生存確認レポート
#
# 「解析が止まってしまったのか、それともまだ計算中なのか」を
# アプリ（ブラウザ）の外から判定する。
#
# なぜ必要か:
#   解析が進んでいるかどうかは、これまで画面の進捗バーでしか分からなかった。
#   しかし止まり方には画面から見えないものがある:
#     - コンテナ再起動 / OOM kill  → R が SIGKILL され、アプリも一緒に消える。
#       台帳(analysis_job.json)は running のまま残る
#     - R が重い工程(RPCA/UMAP)に入っている → ログが数十分伸びないが正常
#   この 2 つは「画面が動かない」という同じ見え方をするのに、対処は正反対
#   （前者は再実行が必要、後者は待つのが正解）。取り違えると、完走間近の
#   2 時間の解析を自分で潰すことになる。
#
#   本スクリプトは判断材料を 1 か所に集めて区別する:
#     1. PID が生きているか            → 消えていれば「停止」で確定
#     2. プロセスツリーの CPU 使用率    → 0% が続くなら本当のハングを疑う
#     3. ログ・出力ファイルの最終更新   → 動いていれば計算中
#
# 使い方:
#   python3 App/tools/analysis_status_report.py            # 人が読む形式
#   python3 App/tools/analysis_status_report.py --json     # 機械可読
#   docker exec msi-analysis-app python3 /app/App/tools/analysis_status_report.py
#
#   Windows からは同梱の check_analysis.ps1 を使うと、実行場所
#   （ローカル / コンテナ内）の判断まで含めて自動でやる。
#
# 終了コード（PowerShell 側の色分け・監視に使う）:
#   0 = 実行中（正常に進んでいる） / 完了
#   3 = 停止している（プロセス消失・未完了）
#   4 = 停滞の疑い（プロセスは居るが更新が止まっている）
#   5 = 実行中の解析が 1 件も見つからない
#   1 = レポート自体の失敗
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# App/tools/ から App/ を import path に載せる（アプリ本体のロジックを再利用する。
# 判定を二重実装すると、アプリの「実行中」判定とここの判定がずれる）。
_APP_DIR = Path(__file__).resolve().parent.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DEAD = 3
EXIT_STALLED = 4
EXIT_NONE = 5

# 終了状態として台帳・ステータスファイルに書かれる値（analysis_finalizer と同じ）
TERMINAL_STATUSES = ("finished", "error", "stopped")

# 出力フォルダの最終更新を調べるときに走査するファイル数の上限。
# 結果フォルダは PNG が数千枚に及ぶことがあり、全走査すると確認自体が重くなる。
_SCAN_FILE_LIMIT = 20000


# ---------------------------------------------------------------------------
# プロセスの生存確認
# ---------------------------------------------------------------------------

def pid_alive(pid) -> bool:
    """PID が生きているか。ゾンビは「死んでいる」と扱う。

    ★ ver57.0: os.kill(pid, 0) は使わない。Windows の CPython では
    シグナル 0 でも TerminateProcess が呼ばれ、**確認しただけで対象を殺す**。
    生存確認のつもりで解析を停止させては本末転倒なので、psutil が無い環境では
    OpenProcess による読み取り専用の確認にフォールバックする。
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
            return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
    except ImportError:
        pass

    if _is_windows():
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)   # POSIX ではシグナル 0 は本当に「送らない」
        return True
    except (OSError, ProcessLookupError):
        return False


def _is_windows() -> bool:
    """実行中の OS が Windows か（テストから差し替えられるよう関数にする）。"""
    return os.name == "nt"


def _pid_alive_windows(pid: int) -> bool:
    """psutil が無い Windows 向けの生存確認（対象を一切変更しない）。"""
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True   # 取れないだけなら「居る」側に倒す（誤って停止と言わない）
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return False


def process_tree_usage(pid, interval: float = 1.0) -> dict:
    """プロセスツリー（R 本体 + DEG の並列ワーカー）の CPU / メモリを測る。

    CPU が 0% のまま張り付いているかどうかが、「重い計算の最中」と
    「本当にハングしている」を分ける唯一の決め手になる。
    """
    info = {"cpu_percent": None, "rss_mb": None, "n_procs": None,
            "measured_sec": interval}
    try:
        import psutil
    except ImportError:
        info["note"] = "psutil が無いため CPU 測定は省略"
        return info

    try:
        parent = psutil.Process(int(pid))
        procs = [parent] + parent.children(recursive=True)
    except Exception:  # noqa: BLE001
        return info

    for p in procs:
        try:
            p.cpu_percent(None)   # 1 回目は基準取りなので戻り値は捨てる
        except Exception:  # noqa: BLE001
            pass
    time.sleep(interval)

    total_cpu = 0.0
    total_rss = 0
    n = 0
    for p in procs:
        try:
            total_cpu += p.cpu_percent(None)
            total_rss += p.memory_info().rss
            n += 1
        except Exception:  # noqa: BLE001
            continue

    info["cpu_percent"] = round(total_cpu, 1)
    info["rss_mb"] = round(total_rss / (1024 * 1024), 1)
    info["n_procs"] = n
    return info


# ---------------------------------------------------------------------------
# ファイルの更新状況
# ---------------------------------------------------------------------------

def _idle_sec(path: Path, now: float):
    """最終更新からの経過秒。ファイルが無ければ None。"""
    try:
        return max(0.0, now - path.stat().st_mtime)
    except OSError:
        return None


def newest_output_activity(output_dir: Path, now: float) -> dict:
    """log/ 以外の出力ファイルで、最後に書かれたものを探す。

    R は PNG や RDS を書くときにログを出さない工程があるため、ログだけを見ると
    「10 分更新なし＝停止」と誤診する。実ファイルの生成はログより確かな
    「生きている証拠」になる。
    """
    newest_path = None
    newest_mtime = None
    scanned = 0
    try:
        for root, dirs, files in os.walk(output_dir):
            if Path(root).name == "log":
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d != "log"]
            for name in files:
                scanned += 1
                if scanned > _SCAN_FILE_LIMIT:
                    break
                try:
                    m = os.stat(os.path.join(root, name)).st_mtime
                except OSError:
                    continue
                if newest_mtime is None or m > newest_mtime:
                    newest_mtime = m
                    newest_path = os.path.join(root, name)
            if scanned > _SCAN_FILE_LIMIT:
                break
    except OSError:
        pass

    return {
        "newest_file": newest_path,
        "idle_sec": None if newest_mtime is None else max(0.0, now - newest_mtime),
        "truncated": scanned > _SCAN_FILE_LIMIT,
    }


def tail_lines(path: Path, n: int) -> list:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-n:] if n > 0 else lines


def read_text_first_line(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return text.split("\n")[0].strip() if text else ""


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------

def classify(*, finalized: bool, alive: bool, status: str,
             idle_sec, stall_sec: float, cpu_percent=None,
             cpu_busy_percent: float = 5.0) -> str:
    """1 件のジョブの状態を決める。

    戻り値:
      finished / error / stopped … 終了済み
      dead    … 未完了なのにプロセスが居ない（＝止まっている）
      stalled … プロセスは居るが更新が止まっている（停滞の疑い）
      running … 進行中

    idle_sec は「ログと出力ファイルのうち、より最近に書かれた方」からの経過秒。
    どちらか一方でも動いていれば解析は進んでいる。
    """
    status = (status or "").strip()
    if finalized or status in TERMINAL_STATUSES:
        return status if status in TERMINAL_STATUSES else "finished"
    if not alive:
        return "dead"

    # CPU を食っているなら、ログが伸びていなくても計算中。
    # RPCA や UMAP は数十分ログを出さないので、ここで救わないと誤検出になる。
    if cpu_percent is not None and cpu_percent >= cpu_busy_percent:
        return "running"
    if idle_sec is not None and idle_sec >= stall_sec:
        return "stalled"
    return "running"


VERDICT_LABEL = {
    "running": "実行中（進行しています）",
    "stalled": "停滞の疑い（プロセスは居ますが更新が止まっています）",
    "dead": "停止しています（プロセスが居ません／未完了）",
    "finished": "完了",
    "error": "エラーで終了",
    "stopped": "利用者が停止",
}

VERDICT_ADVICE = {
    "running": "そのまま待ってください。",
    "stalled": ("重い工程（RPCA / UMAP / DEG）では数十分ログが伸びないことがあります。"
                "CPU 使用率が 0% のまま、かつメモリが上限付近なら"
                "スワップで極端に遅くなっている可能性があります。"),
    "dead": ("アプリを開き直すと error として記録されます（起動時の後始末）。"
             "コンテナ再起動や OOM kill が原因のことが多いので、"
             "下の [EXIT] 行と docker のイベントを確認してから再実行してください。"),
    "finished": "結果はプロジェクトに登録されています。",
    "error": "ログ末尾のエラー行を確認してください。",
    "stopped": "停止操作で終了しています。再実行してください。",
}

# 終了コードは「最も悪い状態」に合わせる
_EXIT_BY_VERDICT = {
    "dead": EXIT_DEAD,
    "stalled": EXIT_STALLED,
    "error": EXIT_OK,      # 終了済みなので確認としては正常応答
    "stopped": EXIT_OK,
    "finished": EXIT_OK,
    "running": EXIT_OK,
}


# ---------------------------------------------------------------------------
# 収集
# ---------------------------------------------------------------------------

def default_roots() -> list:
    """既定の探索ルート。アプリと同じ場所を見る。"""
    try:
        from app.services import job_registry
        roots = job_registry.default_search_roots()
        if roots:
            return roots
    except Exception:  # noqa: BLE001
        pass
    # アプリを import できない場所から実行された場合の保険
    base = _APP_DIR.parent / "Data"
    return [str(p) for p in (base / "Other" / "output",
                             base / "TIMS" / "Data",
                             base / "DESI" / "Data") if p.is_dir()]


def collect_jobs(roots: list) -> list:
    try:
        from app.services import job_registry
        return job_registry.find_jobs(roots)
    except Exception:  # noqa: BLE001
        # アプリを import できないときは台帳を直接探す（深さ 0〜3）
        jobs = []
        seen = set()
        for root in roots:
            base = Path(root)
            if not base.is_dir():
                continue
            for d in range(0, 4):
                for p in base.glob("*/" * d + "log/analysis_job.json"):
                    out = p.parent.parent
                    key = str(out.resolve())
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if isinstance(data, dict):
                        jobs.append(data)
        return jobs


def inspect_job(job: dict, *, stall_sec: float, tail: int,
                cpu_interval: float, scan_outputs: bool) -> dict:
    now = time.time()
    output_dir = Path(job.get("output_dir") or "")
    log_dir = output_dir / "log"
    log_file = log_dir / "analysis_log.txt"
    status_file = log_dir / "analysis_status.txt"

    pid = job.get("pid")
    alive = pid_alive(pid)
    finalized = bool(job.get("finalized"))
    status = read_text_first_line(status_file)

    log_idle = _idle_sec(log_file, now)
    out_activity = ({"newest_file": None, "idle_sec": None, "truncated": False}
                    if not scan_outputs else newest_output_activity(output_dir, now))

    # 「より最近に書かれた方」を活動の指標にする
    idles = [v for v in (log_idle, out_activity["idle_sec"]) if v is not None]
    idle_sec = min(idles) if idles else None

    usage = (process_tree_usage(pid, interval=cpu_interval)
             if alive and cpu_interval > 0 else
             {"cpu_percent": None, "rss_mb": None, "n_procs": None})

    started_at = job.get("started_at") or ""
    elapsed_sec = None
    try:
        elapsed_sec = max(0.0, now - datetime.fromisoformat(started_at).timestamp())
    except (TypeError, ValueError):
        pass

    lines = tail_lines(log_file, tail)
    exit_note = next((ln for ln in reversed(tail_lines(log_file, 200))
                      if ln.startswith("[EXIT]")), "")

    verdict = classify(
        finalized=finalized, alive=alive, status=status,
        idle_sec=idle_sec, stall_sec=stall_sec,
        cpu_percent=usage.get("cpu_percent"),
    )

    return {
        "output_dir": str(output_dir),
        "analysis_type": job.get("analysis_type") or "",
        "project_id": job.get("project_id") or "",
        "sub_project_id": job.get("sub_project_id") or "",
        "analyst": job.get("analyst") or "",
        "pid": pid,
        "pid_alive": alive,
        "finalized": finalized,
        "status_file": status or "(なし)",
        "started_at": started_at,
        "elapsed_sec": elapsed_sec,
        "log_file": str(log_file),
        "log_idle_sec": log_idle,
        "output_newest_file": out_activity["newest_file"],
        "output_idle_sec": out_activity["idle_sec"],
        "output_scan_truncated": out_activity["truncated"],
        "idle_sec": idle_sec,
        "cpu_percent": usage.get("cpu_percent"),
        "rss_mb": usage.get("rss_mb"),
        "n_procs": usage.get("n_procs"),
        "usage_note": usage.get("note", ""),
        "verdict": verdict,
        "exit_note": exit_note,
        "log_tail": lines,
    }


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------

def _fmt_duration(sec) -> str:
    if sec is None:
        return "不明"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}時間{m}分"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def render_text(report: dict) -> str:
    out = []
    out.append("=" * 68)
    out.append(f" 解析の生存確認  ({report['checked_at']})")
    out.append(f" 実行場所: {report['hostname']} ({report['platform']})")
    out.append("=" * 68)

    jobs = report["jobs"]
    if not jobs:
        out.append("")
        # ★ ver57.4: 「1 件も無い」と「絞り込んだ結果ゼロ」を区別する。
        #   --running-only で全部が完了済みだったときに「記録が見つかりません」
        #   と出していたため、探索パスが違うのかと疑わせていた（実際に迷った）。
        if report.get("running_only") and report.get("total_found"):
            out.append(f"実行中の解析はありません"
                       f"（完了済み {report['total_found']} 件）。")
            return "\n".join(out)
        out.append("解析ジョブの記録が見つかりませんでした。")
        out.append("探索したフォルダ:")
        for r in report["roots"]:
            out.append(f"  - {r}")
        out.append("")
        out.append("※ アプリが別の場所（例: Docker コンテナ内）で動いている場合、"
                   "そちらで実行する必要があります。")
        return "\n".join(out)

    for i, j in enumerate(jobs, 1):
        out.append("")
        out.append(f"[{i}/{len(jobs)}] {VERDICT_LABEL.get(j['verdict'], j['verdict'])}")
        out.append("-" * 68)
        out.append(f"  出力先        : {j['output_dir']}")
        if j["analysis_type"]:
            out.append(f"  解析種別      : {j['analysis_type']}")
        if j["analyst"]:
            out.append(f"  実行者        : {j['analyst']}")
        out.append(f"  開始          : {j['started_at'] or '不明'}"
                   f"（経過 {_fmt_duration(j['elapsed_sec'])}）")
        out.append(f"  PID           : {j['pid']}"
                   f"（{'生存しています' if j['pid_alive'] else '存在しません'}）")
        if j["cpu_percent"] is not None:
            out.append(f"  CPU / メモリ  : {j['cpu_percent']}% / "
                       f"{j['rss_mb']} MB（プロセス {j['n_procs']} 個の合計）")
        elif j["pid_alive"] and j.get("usage_note"):
            out.append(f"  CPU / メモリ  : 測定できません（{j['usage_note']}）")
        out.append(f"  ステータス    : {j['status_file']}"
                   f"（完了処理: {'済' if j['finalized'] else '未'}）")
        out.append(f"  ログ最終更新  : {_fmt_duration(j['log_idle_sec'])}前")
        if j["output_idle_sec"] is not None:
            name = Path(j["output_newest_file"] or "").name
            out.append(f"  出力最終更新  : {_fmt_duration(j['output_idle_sec'])}前"
                       f"（{name}）")
        if j["exit_note"]:
            out.append(f"  終了の記録    : {j['exit_note']}")
        out.append(f"  → {VERDICT_ADVICE.get(j['verdict'], '')}")

        if j["log_tail"]:
            out.append("")
            out.append("  --- ログ末尾 ---")
            for ln in j["log_tail"]:
                out.append(f"  | {ln}")

    out.append("")
    out.append("=" * 68)
    out.append(f" 総合判定: {VERDICT_LABEL.get(report['overall'], report['overall'])}")
    out.append("=" * 68)
    return "\n".join(out)


def overall_verdict(jobs: list) -> str:
    """全ジョブから 1 つの結論を出す。悪い状態を優先して見せる。"""
    if not jobs:
        return "none"
    order = ["dead", "stalled", "running", "error", "stopped", "finished"]
    verdicts = {j["verdict"] for j in jobs}
    for v in order:
        if v in verdicts:
            return v
    return "finished"


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def build_report(args) -> dict:
    import platform
    import socket

    roots = args.root or default_roots()
    jobs_raw = collect_jobs(roots)

    # 未完了 → 新しい順。完了済みは後ろへ回す（見たいのは走っているもの）
    jobs_raw.sort(key=lambda j: (bool(j.get("finalized")),
                                 str(j.get("started_at") or "")), reverse=False)
    jobs_raw.sort(key=lambda j: str(j.get("started_at") or ""), reverse=True)
    jobs_raw.sort(key=lambda j: bool(j.get("finalized")))

    total_found = len(jobs_raw)
    if args.running_only:
        jobs_raw = [j for j in jobs_raw if not j.get("finalized")]
    if args.limit > 0:
        jobs_raw = jobs_raw[:args.limit]

    jobs = [inspect_job(j, stall_sec=args.stall_minutes * 60.0, tail=args.tail,
                        cpu_interval=args.cpu_interval,
                        scan_outputs=not args.no_scan_outputs)
            for j in jobs_raw]

    return {
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "hostname": socket.gethostname(),
        "platform": platform.system(),
        "stall_minutes": args.stall_minutes,
        "roots": [str(r) for r in roots],
        "total_found": total_found,
        "running_only": bool(args.running_only),
        "jobs": jobs,
        "overall": overall_verdict(jobs),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="解析が止まっているかどうかを外から確認する")
    p.add_argument("--json", action="store_true", help="JSON で出力する")
    p.add_argument("--root", action="append", default=None,
                   help="探索ルート（複数指定可。既定はアプリ設定と同じ）")
    p.add_argument("--stall-minutes", type=float, default=30.0,
                   help="この分数だけ更新が無ければ停滞と判定する（既定 30）")
    p.add_argument("--tail", type=int, default=15, help="表示するログ行数（既定 15）")
    p.add_argument("--limit", type=int, default=5,
                   help="表示するジョブ数の上限（既定 5、0 で無制限）")
    p.add_argument("--running-only", action="store_true",
                   help="完了処理が済んでいないジョブだけを見る")
    p.add_argument("--cpu-interval", type=float, default=1.0,
                   help="CPU 測定の秒数（0 で測定しない）")
    p.add_argument("--no-scan-outputs", action="store_true",
                   help="出力ファイルの最終更新を調べない（巨大フォルダで高速化）")
    args = p.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    try:
        report = build_report(args)
    except Exception as e:  # noqa: BLE001
        print(f"確認に失敗しました: {e}", file=sys.stderr)
        return EXIT_FAILED

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_text(report))

    if report["overall"] == "none":
        return EXIT_NONE
    return _EXIT_BY_VERDICT.get(report["overall"], EXIT_OK)


if __name__ == "__main__":
    sys.exit(main())
