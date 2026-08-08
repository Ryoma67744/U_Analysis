"""ver52.3 ④ 後半: 「捨てたのに黙っている」3 経路が報告するようになったこと。

番人 `test_partial_failure_is_reported` が未精査として挙げていた 11 件のうち、
実害と判定した 3 件（ピークリスト / レシートの成果物一覧 / Lite の QC 節）を固定する。

★ いずれも **修正前のコードでは落ちる**ことを `git stash` で確認済み。
  症状だけを見るテスト（「落ちた行は地図に無い」など）は修正前でも通ってしまうので、
  ここでは **利用者に届く出力に件数・理由が現れること**を検査する。
"""

import base64
import json
from pathlib import Path

import pytest

from app.services import receipt as RC
from app.services.scils_converter import _read_peaklist

# ★ `peaklist_skip_message` は ver52.3 ④ で足した関数なので、
#   モジュール先頭で import すると **修正前のコードでは収集ごと失敗**し、
#   「どのテストが振る舞いを固定しているか」が分からなくなる
#   （ImportError は『直っていない』ではなく『まだ無い』としか言わない）。
#   `git stash` で修正前と突き合わせられるよう、必要なテストの中で import する。


# ===========================================================================
# 1. peak-list: 捨てた行の内訳を返し、呼び出し側が利用者へ出せること
# ===========================================================================
def _write_peaklist(tmp_path, lines):
    p = tmp_path / "peaks.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def peaklist_with_broken_rows(tmp_path):
    """正常 2 行 + 壊れ 3 行（列不足 / 非数値 / NaN）。

    ★ ヘッダは `first_header_and_skipcount` が認識できる形にする。
      認識できないと `_read_peaklist` は ValueError で止まるので、
      「捨てた行を数える」検査そのものが成立しない。
    """
    return _write_peaklist(tmp_path, [
        "m/z,Interval,Color,Name,Spot 1",
        "100.5,0.01,#ffffff,Alpha,10",
        "200.7,0.01,#ffffff,Beta,20",
        "300.9,0.01,#ffffff",              # 列不足
        "abc,0.01,#ffffff,Gamma,30",       # 非数値
        "nan,0.01,#ffffff,Delta,40",       # NaN（float() は例外を出さない）
    ])


class TestPeaklistReportsWhatItDropped:

    def test_the_fixture_actually_parses(self, peaklist_with_broken_rows):
        """★ 前提の固定: 正常行が読めていること。

        ヘッダを認識できないと 0 行になり、下の検査が「全部捨てた」で
        通ってしまう（ver52.3 で一度これに引っかかっている）。
        """
        mz, names = _read_peaklist(peaklist_with_broken_rows)
        assert list(names) == ["Alpha", "Beta"], f"正常行が読めていない: {names}"
        assert len(mz) == 2

    def test_each_broken_shape_is_counted_separately(self, peaklist_with_broken_rows):
        """★ 本丸: 3 通りの壊れ方をそれぞれ数えること。"""
        mz, names, skipped = _read_peaklist(
            peaklist_with_broken_rows, return_skipped=True)
        assert skipped == {"short_row": 1, "non_numeric_mz": 1, "non_finite_mz": 1}, (
            f"捨てた行の内訳が合わない: {skipped}。"
            "落ちた行の化合物名は変換後 Parquet の列名に焼き込まれないので、"
            "あとから復元できない")

    def test_nan_is_not_silently_accepted(self, tmp_path):
        """`float("nan")` は例外を出さないので、素通りしていた形。

        NaN が地図に入っても最近傍探索の比較が常に偽になるため、
        そのピークはどの feature にも当たらず**消える**。
        「読めなかった」と同じ扱いにする。
        """
        p = _write_peaklist(tmp_path, [
            "m/z,Interval,Color,Name,Spot 1",
            "nan,0.01,#ffffff,OnlyOne,10",
        ])
        # ★ 既定の呼び方で検査する。新 API を使うと修正前のコードでは
        #   ImportError/TypeError になり、「まだ無い」としか分からない。
        #   既定の 2-tuple なら修正前でも呼べて、**assert で落ちる**。
        mz, names = _read_peaklist(p)
        assert len(mz) == 0, (
            f"NaN の m/z がそのまま配列に入っている: {mz}。"
            "最近傍探索の比較が常に偽になるので、このピークは消える")
        _, _, skipped = _read_peaklist(p, return_skipped=True)
        assert skipped["non_finite_mz"] == 1

    def test_message_is_empty_when_nothing_was_dropped(self, tmp_path):
        """★ 過剰報告の番人: 何も捨てていなければ何も言わない。"""
        p = _write_peaklist(tmp_path, [
            "m/z,Interval,Color,Name,Spot 1",
            "100.5,0.01,#ffffff,Alpha,10",
        ])
        from app.services.scils_converter import peaklist_skip_message
        _, _, skipped = _read_peaklist(p, return_skipped=True)
        assert peaklist_skip_message(skipped) == ""

    def test_message_names_every_nonzero_reason(self, peaklist_with_broken_rows):
        from app.services.scils_converter import peaklist_skip_message
        _, _, skipped = _read_peaklist(peaklist_with_broken_rows, return_skipped=True)
        msg = peaklist_skip_message(skipped)
        for fragment in ("列数が足りない行 1 件",
                         "m/z が数値でない行 1 件",
                         "m/z が NaN/Inf の行 1 件"):
            assert fragment in msg, f"{fragment!r} が文言に無い: {msg}"

    def test_default_call_shape_is_unchanged(self, peaklist_with_broken_rows):
        """★ 過剰修正の番人: 既存の呼び方（2-tuple）を壊していないこと。"""
        out = _read_peaklist(peaklist_with_broken_rows)
        assert len(out) == 2, "既定の戻り値が 3-tuple になっている（呼び出し側が壊れる）"


# ===========================================================================
# 2. レシート: 成果物一覧が不完全なら、レシート自身がそう書くこと
# ===========================================================================
_PARAMS = {"analysis_type": "DESI", "output_dir": "/tmp/x"}


class TestReceiptAdmitsAnIncompleteOutputList:

    def test_complete_list_says_nothing(self, tmp_path):
        """★ 過剰報告の番人: 全部列挙できたなら但し書きを出さない。"""
        (tmp_path / "markers_annotated.csv").write_text("a\n", encoding="utf-8")
        (tmp_path / "analysis_params.json").write_text(
            json.dumps(_PARAMS), encoding="utf-8")
        r = RC.finalize_receipt(tmp_path)
        assert r["result"]["outputs_incomplete"] == {}
        assert "不完全" not in RC.render_receipt_markdown(r)

    def test_truncation_is_recorded(self, tmp_path):
        """上限 200 件での打ち切りが無言だった形。

        ★ `_OUTPUT_GLOBS` のうち件数が伸びうるのは `RDS_Files/*.rds` だけ
          （他は完全名か 1 階層ぶんなので上限に届かない）。
          ここを別のパターンで書くとテストが空振りする。
        """
        rds = tmp_path / "RDS_Files"
        rds.mkdir()
        for i in range(RC._OUTPUT_LIMIT + 5):
            (rds / f"obj_{i:04d}.rds").write_text("a\n", encoding="utf-8")
        _paths, status = RC._collect_outputs(tmp_path, return_status=True)
        assert status["truncated"] is True
        assert len(_paths) == RC._OUTPUT_LIMIT

        receipt = RC.build_receipt(
            _PARAMS, outputs=[], outputs_status=status)
        assert receipt["result"]["outputs_incomplete"]["truncated_at"] == RC._OUTPUT_LIMIT
        md = RC.render_receipt_markdown(receipt)
        assert "成果物一覧は不完全" in md, (
            "RECEIPT.md は最後に『これ 1 つで再現できる』と書いているのに、"
            f"一覧が不完全であることを書いていない:\n{md}")

    def test_unscannable_pattern_is_recorded(self, tmp_path, monkeypatch):
        """`OSError` 1 つでパターンごと欠落していた形。"""
        real_glob = Path.glob

        def _boom(self, pattern):
            if pattern == "RDS_Files/*.rds":
                raise OSError("permission denied")
            return real_glob(self, pattern)

        monkeypatch.setattr(Path, "glob", _boom)
        _paths, status = RC._collect_outputs(tmp_path, return_status=True)
        assert status["failed_patterns"], "走査に失敗したパターンを記録していない"

        receipt = RC.build_receipt(_PARAMS, outputs=[], outputs_status=status)
        entries = receipt["result"]["outputs_incomplete"]["unscannable_patterns"]
        assert entries[0]["pattern"] == "RDS_Files/*.rds"
        md = RC.render_receipt_markdown(receipt)
        assert "走査できなかったパターン" in md

    def test_other_patterns_still_collected_after_a_failure(self, tmp_path, monkeypatch):
        """★ 過剰修正の番人: 1 パターンの失敗で全部やめないこと。"""
        (tmp_path / "markers_annotated.csv").write_text("a\n", encoding="utf-8")
        real_glob = Path.glob

        def _boom(self, pattern):
            if pattern == "RDS_Files/*.rds":
                raise OSError("permission denied")
            return real_glob(self, pattern)

        monkeypatch.setattr(Path, "glob", _boom)
        paths, status = RC._collect_outputs(tmp_path, return_status=True)
        assert [p.name for p in paths] == ["markers_annotated.csv"]
        assert status["truncated"] is False


# ===========================================================================
# 3. Lite ビュー: QC 画像が読めなかったことと、そもそも無いことを区別する
# ===========================================================================
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _flatten_text(component):
    """Dash コンポーネント木から文字列を全部拾う（節の中身を検査するため）。"""
    out = []
    stack = [component]
    while stack:
        n = stack.pop()
        if isinstance(n, str):
            out.append(n)
        elif isinstance(n, (list, tuple)):
            stack.extend(n)
        elif hasattr(n, "children"):
            stack.append(n.children)
    return " ".join(out)


class TestLiteQcSectionDistinguishesUnreadableFromAbsent:

    def test_absent_stays_absent(self, tmp_path):
        """QC 画像が無ければ従来どおり節を出さない（過剰修正の番人）。"""
        from app.callbacks.lite_view_callbacks import _build_spot_filtering_section
        assert _build_spot_filtering_section(str(tmp_path)) is None

    def test_readable_image_has_no_warning(self, tmp_path):
        from app.callbacks.lite_view_callbacks import _build_spot_filtering_section
        (tmp_path / "spot_filtering_S1.png").write_bytes(_PNG)
        sec = _build_spot_filtering_section(str(tmp_path))
        assert sec is not None
        assert "読み込めませんでした" not in _flatten_text(sec)

    def test_unreadable_image_still_produces_the_section(self, tmp_path, monkeypatch):
        """★ 本丸: 全部読めなくても節を出す。

        従来は節ごと消えたので、閲覧者からは
        「Otsu 除去をしていない解析」と区別が付かなかった。
        Lite ビューにはこの節の有無以外に手がかりが無い。
        """
        from app.callbacks import lite_view_callbacks as LV
        (tmp_path / "spot_filtering_S1.png").write_bytes(_PNG)

        real_open = open

        def _boom(path, *a, **kw):
            if str(path).endswith("spot_filtering_S1.png"):
                raise OSError("disk error")
            return real_open(path, *a, **kw)

        monkeypatch.setattr("builtins.open", _boom)
        sec = LV._build_spot_filtering_section(str(tmp_path))
        assert sec is not None, (
            "QC 画像が読めないと節ごと消えるため、"
            "「除去していない解析」と区別が付かない")
        text = _flatten_text(sec)
        assert "読み込めませんでした" in text and "spot_filtering_S1.png" in text, (
            f"どの画像が読めなかったのかが節に出ていない: {text}")

    def test_partially_unreadable_shows_both(self, tmp_path, monkeypatch):
        """1 枚読めて 1 枚読めない場合、画像と警告の両方を出すこと。"""
        from app.callbacks import lite_view_callbacks as LV
        (tmp_path / "spot_filtering_S1.png").write_bytes(_PNG)
        (tmp_path / "spot_filtering_S2.png").write_bytes(_PNG)

        real_open = open

        def _boom(path, *a, **kw):
            if str(path).endswith("spot_filtering_S2.png"):
                raise OSError("disk error")
            return real_open(path, *a, **kw)

        monkeypatch.setattr("builtins.open", _boom)
        text = _flatten_text(LV._build_spot_filtering_section(str(tmp_path)))
        assert "spot_filtering_S1.png" in text          # 出た画像
        assert "spot_filtering_S2.png" in text          # 出なかった画像
        assert "1 件を読み込めませんでした" in text


# ===========================================================================
# 4. 分子情報の後付け: プレビューが「読めなかった件数」を出すこと
# ===========================================================================
class TestMolinfoPreviewShowsDroppedRows:

    def test_result_dict_carries_the_count(self, tmp_path, monkeypatch):
        """`attach_molecular_info(dry_run=True)` が件数と文言を返すこと。"""
        import numpy as np
        from app.services import molinfo_attach as MA

        p = tmp_path / "peaks.csv"
        p.write_text(
            "m/z,Interval,Color,Name,Spot 1\n"
            "100.5,0.01,#ffffff,Alpha,10\n"
            "abc,0.01,#ffffff,Beta,20\n",
            encoding="utf-8")

        monkeypatch.setattr(MA, "_read_feature_mz",
                            lambda _d: np.array([100.5], dtype=float))
        monkeypatch.setattr(MA, "_main_parquet_base", lambda _d: "base")
        sub = {"data_folder": str(tmp_path)}

        r = MA.attach_molecular_info(sub, str(p), dry_run=True)
        assert r["n_peaklist_skipped"] == 1, (
            f"読めなかった行がプレビューに伝わっていない: {r}。"
            "従来は「CSV: N ピーク」としか出ず、N が壊れ行のぶん"
            "少ないことに気づけなかった")
        assert "m/z が数値でない行 1 件" in r["peaklist_skip_message"]
