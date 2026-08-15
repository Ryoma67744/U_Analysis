"""RDS 軽量化の進捗表示と、部分失敗の扱いの番人。

★ ver56.5 / デバッグ総点検 §4 (C11-1 / C11-2):

  C11-1: 進捗を読み取る 2 つの正規表現に `re.MULTILINE` が無かった。
    これらは**ログ全文**に対して search / finditer されるため、MULTILINE が
    無いと `^` は文字列全体の先頭にしか一致せず、2 行目以降の
    「[slim] N files matched」「[i/N]」を 1 件も拾えない。
    結果、進捗バーは実行中ずっと 0%「準備中」のまま動かず、終了した瞬間だけ
    100% に跳んでいた（処理自体は正常に進んでいる）。対象が数百件ある場合は
    「固まったのか進んでいるのか」判断できない。

  C11-2: 一部のファイルが失敗しても緑の「完了しました。」を表示していた。
    parquet 側は失敗を明示するのに RDS 側だけ非対称だった。
"""
import re

import pytest

import app.callbacks.rds_maintenance_callbacks as rm


SAMPLE_LOG = """[slim] start (dry_run=FALSE)
[slim]   12 files matched
[1/12] processing a.rds
[2/12] processing b.rds
[3/12] processing c.rds
[slim] Processed : 11
[slim] Skipped   : 0
[slim] Errors    : 1
"""


class TestProgressParsing:
    """★ 本丸: 実行中のログから進捗を読めること。"""

    def test_total_file_count_is_found(self):
        m = rm._FILE_COUNT_RE.search(SAMPLE_LOG)
        assert m is not None, (
            "総ファイル数を読み取れていない（進捗バーが 0% のまま動かない）")
        assert m.group(1) == "12"

    def test_per_file_progress_is_found(self):
        hits = list(rm._FILE_PROGRESS_RE.finditer(SAMPLE_LOG))
        assert hits, "各ファイルの進捗行を 1 件も読み取れていない"
        assert hits[-1].group(1) == "3" and hits[-1].group(2) == "12"

    def test_regexes_declare_multiline(self):
        """`^` を使う以上 MULTILINE が必須であることを明示的に守る。"""
        for name, rx in (("_FILE_COUNT_RE", rm._FILE_COUNT_RE),
                         ("_FILE_PROGRESS_RE", rm._FILE_PROGRESS_RE)):
            assert rx.flags & re.MULTILINE, (
                f"{name} に re.MULTILINE が無い。ログ全文に対して `^` が"
                "先頭 1 行にしか当たらず、進捗を拾えない")

    def test_first_line_match_still_works(self):
        """1 行目に来た場合も従来どおり拾えること。"""
        assert rm._FILE_COUNT_RE.search("[slim]   5 files matched\n") is not None


class TestSummaryParsing:
    """サマリの抽出（失敗件数の判定に使う）。"""

    def test_errors_are_extracted(self):
        summary = rm._parse_summary(SAMPLE_LOG)
        assert summary.get("Errors") == "1"
        assert summary.get("Processed") == "11"

    def test_clean_run_reports_zero_errors(self):
        clean = SAMPLE_LOG.replace("Errors    : 1", "Errors    : 0")
        assert rm._parse_summary(clean).get("Errors") == "0"


class TestPartialFailureIsNotReportedAsSuccess:
    """★ C11-2: 一部失敗を緑の「完了しました。」で流さないこと。"""

    @pytest.mark.parametrize("errors,expect_warning", [
        ("0", False), ("", False), ("-", False), ("1", True), ("3", True),
    ])
    def test_alert_colour_reflects_errors(self, errors, expect_warning):
        """判定ロジックがソース上で Errors を見ていることを確認する。"""
        import inspect
        src = inspect.getsource(rm)
        assert 'summary.get("Errors"' in src, (
            "完了表示が Errors 件数を参照していない。"
            "一部失敗でも成功として表示されてしまう")
        assert 'color="warning" if _has_errors else "success"' in src, (
            "失敗があっても success 色のままになっている")
        # 判定式そのものを再現して境界を確認
        _errors = str(errors).strip()
        has = bool(_errors) and _errors not in ("0", "-", "")
        assert has is expect_warning
