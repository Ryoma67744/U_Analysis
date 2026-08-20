"""運用ツールが事実と違うことを言う (§7.7(b))。

ver57.x で別セッションが入れた「RDS 軽量化」と「解析状況の確認」に、
**利用者を誤らせる**振る舞いが残っている。解析の数値は変わらないが、
どちらも「困ったときに頼るもの」なので、嘘をつくと復旧を誤らせる。

1. 軽量化の増減表示が NA で例外を投げ、**軽量化そのものが途中で落ちる**。
   `file.info()$size` は取得できないと NA を返し、R では `if (NA >= 0)` が
   エラーになって停止する。
2. 同じ数字の符号が、個別行 (`-47.6%`) とサマリ (`Reduction : 47.6%`) で逆を向く。
3. 解析がエラーで終わっていても終了コードが 0 になり、
4. PowerShell がそれを **緑で「正常に終了済み」** と表示する。
5. 状況判定の優先順位がアプリ本体と逆（status ファイルを PID 生存より優先）で、
   同じ状況について 2 つのツールが逆の結論を出しうる。
6. アプリと確認ツールが別の場所（コンテナ内 / ホスト）で動いていると、
   PID は別の名前空間の番号なので生死を判断できないのに、断定してしまう。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
SLIM = APP / "Script" / "helpers" / "slim_existing_rds.R"
PS1 = APP.parent / "check_analysis.ps1"

_needs_r = pytest.mark.skipif(shutil.which("Rscript") is None, reason="R が無い環境")


def _run_r(code: str) -> str:
    out = subprocess.run(["Rscript", "-e", code], capture_output=True,
                         text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _format_delta_src() -> str:
    src = SLIM.read_text(encoding="utf-8")
    start = src.index(".format_delta <- function(delta)")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


# ---------------------------------------------------------------------------
# ① 軽量化が NA で落ちる
# ---------------------------------------------------------------------------

@_needs_r
def test_r_really_stops_on_a_na_condition():
    """前提の実証: R では `if (NA)` は警告ではなく **エラーで停止**する。"""
    out = subprocess.run(
        ["Rscript", "-e", 'r <- tryCatch({ if (NA >= 0) 1 else 2 },'
                          ' error = function(e) "STOPPED"); cat(r)'],
        capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "STOPPED", (
        f"NA 条件が停止しない: {out.stdout!r}")


@_needs_r
def test_the_size_delta_survives_a_missing_file():
    """★ ファイルサイズが取れなくても軽量化を止めないこと。"""
    body = _format_delta_src()
    out = _run_r(f'{body}\ncat(tryCatch(.format_delta(NA_real_),'
                 f' error = function(e) "STOPPED"))')
    assert out.strip() != "STOPPED", (
        "サイズが取れないだけで軽量化ループごと落ちる。"
        "file.info()$size は取得できないと NA を返す")
    assert "?" in out, f"不明であることを示していない: {out!r}"


@_needs_r
def test_the_normal_signs_are_unchanged():
    """既存の符号（ver57.3 で決めた「縮小＝マイナス」）は変えないこと。"""
    body = _format_delta_src()
    assert _run_r(f"{body}\ncat(.format_delta(47.6))").strip() == "-47.6%"
    assert _run_r(f"{body}\ncat(.format_delta(-4.4))").strip() == "+4.4%"


# ---------------------------------------------------------------------------
# ② 符号の向きがログの中で食い違う
# ---------------------------------------------------------------------------

def test_the_summary_uses_the_same_sign_convention():
    """★ 個別行とサマリで同じ数字の符号が逆を向かないこと。"""
    src = SLIM.read_text(encoding="utf-8")
    assert "Reduction  : %.1f%%" not in src, (
        "サマリだけが「削減率」（縮小＝プラス）で、個別行（縮小＝マイナス）と"
        "逆を向いている。同じログの中で同じ数字の符号が食い違う")
    i = src.index("Size after :")
    assert ".format_delta(" in src[i:i + 400], (
        "サマリが個別行と同じ書式関数を使っていない")


# ---------------------------------------------------------------------------
# ③ エラー終了が「正常」になる
# ---------------------------------------------------------------------------

def test_an_error_run_does_not_report_success():
    """★ エラーで終わった解析の終了コードが 0 でないこと。"""
    from tools import analysis_status_report as r

    assert r._EXIT_BY_VERDICT["error"] != r.EXIT_OK, (
        "エラーで終わっているのに確認ツールが成功 (0) を返す。"
        "呼び出し側は緑で「正常に終了済み」と表示してしまう")


def test_a_normal_run_still_reports_success():
    """止めすぎない: 実行中・完了・停止操作は従来どおり 0。"""
    from tools import analysis_status_report as r

    for v in ("running", "finished", "stopped"):
        assert r._EXIT_BY_VERDICT[v] == r.EXIT_OK, v


def test_the_powershell_has_a_branch_for_the_error_code():
    """★ PowerShell 側にもエラー終了の分岐があること。"""
    src = PS1.read_text(encoding="utf-8")
    from tools import analysis_status_report as r

    assert f"    {r.EXIT_ERROR} {{" in src, (
        f"exit={r.EXIT_ERROR}（エラーで終了）の分岐が無い。"
        "default に落ちて「確認に失敗しました」と出る")


def test_the_powershell_success_line_is_honest():
    """★ exit 0 の文言が「正常に終了済み」を無条件に名乗らないこと。"""
    src = PS1.read_text(encoding="utf-8")
    i = src.index("switch ($exitCode)")
    zero = src[i:i + 400]
    assert "正常に終了済み" not in zero or "エラー" in zero, (
        "エラー終了でも緑で「正常に終了済み」と読める文言のままになっている")


# ---------------------------------------------------------------------------
# ④ 状況判定の優先順位がアプリ本体と逆
# ---------------------------------------------------------------------------

def test_a_live_process_beats_a_stale_status_file():
    """★ プロセスが生きているなら、古い status ファイルより実態を優先すること。

    アプリ本体 (`job_registry`) は finalized と PID だけで判断し、
    status ファイルを読まない。確認ツールだけが status を先に見ていたため、
    **同じ状況で 2 つのツールが逆の結論を出しうる**。
    """
    from tools.analysis_status_report import classify

    v = classify(finalized=False, alive=True, status="finished",
                 idle_sec=1.0, stall_sec=1800.0)
    assert v == "running", (
        f"生きているプロセスを古い status ファイルで「完了」にしている: {v}")


def test_the_finalized_flag_is_still_authoritative():
    """アプリが後始末を終えた印 (finalized) は従来どおり優先すること。"""
    from tools.analysis_status_report import classify

    assert classify(finalized=True, alive=True, status="",
                    idle_sec=1.0, stall_sec=1800.0) == "finished"


def test_a_terminal_status_still_wins_when_the_process_is_gone():
    """プロセスが居ないなら status ファイルの終了理由を使うこと。"""
    from tools.analysis_status_report import classify

    assert classify(finalized=False, alive=False, status="error",
                    idle_sec=1.0, stall_sec=1800.0) == "error"


# ---------------------------------------------------------------------------
# ⑤ PID の名前空間が違うと生死は判断できない
# ---------------------------------------------------------------------------

def test_the_ledger_records_where_the_analysis_runs():
    """★ 台帳がどこで走っているかを残すこと（PID だけでは足りない）。"""
    from app.services import job_registry as jr

    assert hasattr(jr, "host_id"), "実行場所を示す関数が無い"
    assert jr.host_id(), "実行場所が空"


def test_the_reporter_does_not_guess_across_hosts():
    """★ 別の場所の PID について「停止しています」と断定しないこと。

    アプリが Docker コンテナ内、確認をホストで走らせると、台帳の PID は
    **別の名前空間の番号**になる。同じ番号のプロセスがホストに居るかどうかは
    まったくの偶然で、居なければ「停止」、居れば「実行中」と嘘をつく。
    """
    from tools.analysis_status_report import classify

    v = classify(finalized=False, alive=None, status="",
                 idle_sec=1.0, stall_sec=1800.0)
    assert v != "dead", (
        "生死が分からないのに「停止しています」と断定している")

    v2 = classify(finalized=False, alive=None, status="",
                  idle_sec=99999.0, stall_sec=1800.0)
    assert v2 == "stalled", (
        f"更新が止まっているのに何も言わない: {v2}")


# ---------------------------------------------------------------------------
# ⑥ Windows の生存確認は「取得失敗＝居る」側に倒す（説明と実装の一致）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod,path", [
    ("app.services.job_registry", "app/services/job_registry.py"),
    ("tools.analysis_status_report", "tools/analysis_status_report.py"),
])
def test_the_windows_check_falls_back_to_alive(mod, path):
    """取得に失敗しただけなら「居る」側に倒すこと（誤って停止と言わない）。

    ver56.6 で一度直した型なので、2 つの実装が同じ向きであることを固定する。
    """
    src = (APP / path).read_text(encoding="utf-8")
    i = src.index("def _pid_alive_windows")
    body = src[i:i + 1400]
    j = body.index("GetExitCodeProcess")
    after = body[j:j + 300]
    assert "return True" in after, (
        f"{path}: 取得に失敗したときに「居ない」側へ倒している。"
        "生きている解析を停止扱いすると二重起動を許す")
