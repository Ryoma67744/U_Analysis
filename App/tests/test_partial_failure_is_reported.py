"""集めて返す関数が、**取りこぼした要素を黙らせていない**ことを照合する (ver52.2)。

■ なぜこの番人なのか

ver51.0〜52.1 の欠陥を型に分類すると
**「部分的失敗を成功として報告 (T5)」が 17 件 / 6 版**にまたがって再発している。
残存する実害 8 件のうち **6 件がこの型**でもあり、件数でも再発回数でも最上位。
それでいて番人が 1 本も無かった。

■ この型

要素の集合を回して成果物を組み立てる。途中で 1 件失敗すると
`except … continue` / `except … pass` で飛ばす。
**飛ばしたことは呼び出し側に伝わらない**ので、欠けた成果物が
「完全な結果」として利用者に提示される。

    ver51.9 B-3  Excel シート名の衝突で先に書いたサンプルが消える → それでも成功扱い
    ver51.9 B-9  DESI で .txt が無いサンプルを無言で飛ばす → シート 0 個の「成功した」Excel
    ver52.1 API-01 一部不存在クラスタを一切通知しない

■ 正解はリポジトリの中にある

同じ形なのに**スキップを報告している**実装が既にある。5 ラウンドの成果:

    interactive_pptx.py:2001,2413    skipped_methods → 最終ステータスに「（スキップ: …）」
    hne_overlay_callbacks.py:867,951 skipped → msg に「（スキップ: …）」
    scils_converter.py:948           result.warnings に追記
    interactive_data_export.py:596   "Skipped" シートをブックに書き出す

実害 8 件との差は **報告があるかどうかだけ**。だから番人にできる。

■ 判定を正規表現に寄せない

最初は「関数内に skip/fail/error という名前があれば報告している」で数えたが、
**`except (ValueError, TypeError)` の例外クラス名自体が `error` に一致**して
握りつぶしを「報告あり」と誤判定した。`logger.warning` も一致するが、
ログは利用者に届かないので報告ではない。

近似した番人は、その型の別の現れ方を通す（ver51.6 の scipy 番人が
`setuptools.backends` を通したのと同じ）。そこで:

    **母数の列挙は機械的に**（AST で構造を見る）
    **1 件ごとの判断は明示的に登録する**（正規表現に推測させない）

登録は「例外の棚」ではなく **母数 26 件すべての分類**なので、
新しい実装が増えれば必ずここに現れる。
"""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"


# ===========================================================================
# 分類 1: スキップを利用者に報告している（正解例）
# ===========================================================================
REPORTS_SKIPS = {
    ("interactive_pptx.py", "cb_export_report"):
        "skipped_methods を最終ステータス文に「（スキップ: …）」として出す (:2413)",
    ("hne_overlay_callbacks.py", "hne_export_stage_b"):
        "skipped を msg に「（スキップ: …）」として出す (:951)",
}

# ===========================================================================
# 分類 2: 飛ばしても利用者の成果物に影響しない（基盤・列挙・後始末）
# ===========================================================================
SKIPS_ARE_IMMATERIAL = {
    ("data_management_callbacks.py", "render_backup_list"):
        "バックアップ一覧の表示。読めない項目は一覧に出ないだけ",
    ("file_browser_modal.py", "list_directory"):
        "ファイルブラウザの一覧。権限が無いものは表示されないだけ",
    ("data_browser.py", "_dir_stats"):
        "フォルダサイズの概算表示",
    ("export_progress.py", "sweep_old_files"):
        "古い一時ファイルの後始末。消せなければ次回消す",
    ("job_registry.py", "default_search_roots"):
        "探索候補ルートの列挙。読めないルートは候補から外れるだけ",
    ("molinfo_attach.py", "_target_dirs"):
        "書き込み先候補の列挙",
    ("receipt.py", "collect_python_versions"):
        "任意のバージョン情報。取れなければ receipt に載らない（載らないことは分かる）",
    ("gpt_api.py", "_list_exports"):
        "ディスク上のファイル列挙。stat できないものは出ないだけ",
    ("gpt_api.py", "_list_outputs"):
        "同上。★ 前回監査の『出力画像の重複』はここではなく解析側の出力が原因",
    ("project_manager.py", "scan_project_meta"):
        "プロジェクト一覧。壊れた meta は一覧に出ないだけ",
}

# ===========================================================================
# 分類 3: ★ 報告すべきなのにしていない（実害を確認済み・ver52.3 で直す）
#   ver52.2 は番人だけを入れて母数を測る版なので、ここでは直さない。
# ===========================================================================
MUST_REPORT = {
    ("interactive_data_export.py", "_build_region_lookup"):
        "H-1: ROI 割当が例外だとそのスライスだけ領域名が空になる。"
        "利用者には『どの ROI にも入らなかった』（＝実データ上の所見）と読める。"
        "全サンプルで失敗すると列ごと消えて『ROI 未使用』と区別できない",
    ("analysis_runner.py", "compute_calibration_coefficients"):
        "H-3: 参照ピークの行が数値化できないと無言で捨てて残りで回帰する。"
        "点数が減ると次数も自動で下がるのに、点数が報告されない",
    ("deg_utils.py", "build_marker_rows"):
        "H-7: avg_log2FC が読めない record は Up にも Down にも入らず"
        "**両方の Top-N から消える**。落とした件数の報告が無い",
}

# ===========================================================================
# 分類 4: ★ まだ精査していない。ver52.3 で 1 件ずつ判断する。
#   「分類済み」を装わない。ここが空になるまで T5 は閉じていない。
# ===========================================================================
UNREVIEWED = {
    ("interactive_calibration.py", "_build_annotation_csv_map"):
        "化合物名の対応表。飛ばすと化合物名が欠ける可能性",
    ("interactive_calibration.py", "_build_mz_to_compound_map"):
        "同上",
    ("interactive_calibration.py", "_features_within_windows"):
        "キャリブレーション窓内の feature 抽出",
    ("interactive_calibration.py", "auto_detect_int_cal_peaks"):
        "参照ピークの自動検出。飛ばすとテーブルの行が欠ける",
    ("interactive_calibration.py", "recalculate_int_cal_ppm"):
        "ppm 列の再計算。表示専用の可能性が高いが未確認",
    ("analysis_callbacks.py", "auto_detect_observed_peaks"):
        "バッチ側の参照ピーク自動検出",
    ("scils_converter.py", "_read_peaklist"):
        "ピークリストの読み込み。飛ばすと注釈が欠ける",
    ("deg_utils.py", "load_deg_results"):
        "DEG 結果の読み込み。ver51.8/51.9 で手法スコープを直した経路",
    ("receipt.py", "_collect_outputs"):
        "receipt に載せる出力ファイルの収集（再現性の証跡）",
    ("lite_view_callbacks.py", "_build_spot_filtering_section"):
        "Lite ビューのスポット絞り込み表示",
    ("peak_annotation.py", "parse_scils_name"):
        "SCiLS 名の解析",
}

_ALL = {}
for _reg, _label in ((REPORTS_SKIPS, "REPORTS_SKIPS"),
                     (SKIPS_ARE_IMMATERIAL, "SKIPS_ARE_IMMATERIAL"),
                     (MUST_REPORT, "MUST_REPORT"),
                     (UNREVIEWED, "UNREVIEWED")):
    for _k in _reg:
        _ALL.setdefault(_k, []).append(_label)


# --------------------------------------------------------------------------
# 母数の列挙（機械的・構造で見る）
# --------------------------------------------------------------------------
def _swallows_per_item_failure(fn):
    """ループの中で `except → continue` / `except → pass` しているか。"""
    for loop in ast.walk(fn):
        if not isinstance(loop, (ast.For, ast.While)):
            continue
        for h in ast.walk(loop):
            if not isinstance(h, ast.ExceptHandler):
                continue
            if any(isinstance(s, ast.Continue) for s in ast.walk(h)):
                return True
            if len(h.body) == 1 and isinstance(h.body[0], ast.Pass):
                return True
    return False


def _accumulated_names(fn):
    """`x.append(...)` / `x += ...` / `x[k] = ...` で育てている名前。"""
    out = set()
    for n in ast.walk(fn):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("append", "add", "extend", "update")
                and isinstance(n.func.value, ast.Name)):
            out.add(n.func.value.id)
        if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                    out.add(t.value.id)
    return out


def _returned_names(fn):
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Return) and n.value is not None:
            for s in ast.walk(n.value):
                if isinstance(s, ast.Name):
                    out.add(s.id)
    return out


def population():
    """「要素を集めて返す」かつ「item 失敗を握りつぶす」関数を列挙する。

    この 2 条件が揃うと、**欠けた成果物が完全な結果として返る**。
    """
    out = {}
    for path in sorted(APP.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            pytest.fail(f"{path} が構文エラー: {e}")
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _swallows_per_item_failure(fn):
                continue
            if not (_accumulated_names(fn) & _returned_names(fn)):
                continue          # 成果物を組み立てて返していない
            out[(path.name, fn.name)] = f"{path.relative_to(APP.parent)}:{fn.lineno}"
    return out


# --------------------------------------------------------------------------
class TestTheGuardIsNotInert:
    """★ 番人が空振りしていないこと（ver51.9 で 3 回空振りさせた反省）。"""

    def test_population_is_discoverable(self):
        pop = population()
        assert len(pop) >= 20, (
            f"母数が {len(pop)} 件しか見つからない。走査が壊れている疑い")

    def test_the_known_good_examples_are_in_the_population(self):
        """正解例が母数に入っていること（＝走査がこの形を拾えている証明）。"""
        pop = set(population())
        for key in REPORTS_SKIPS:
            assert key in pop, (
                f"正解例 {key} が母数に入っていない。列挙条件が狭すぎる")


class TestEverySiteIsClassified:
    """★★ 本丸: 母数の全件が、いずれか 1 つに分類されていること。"""

    def test_no_unclassified_site(self):
        pop = population()
        new = sorted(set(pop) - set(_ALL))
        assert not new, (
            "要素を集めて返しながら item 失敗を握りつぶす関数が**新たに**増えた。\n"
            "飛ばした要素が呼び出し側に伝わらないので、"
            "**欠けた成果物が完全な結果として利用者に出る**。\n"
            "スキップを報告するようにするか、影響しないなら理由付きで登録すること:\n  "
            + "\n  ".join(f"{f}::{n}  ({pop[(f, n)]})" for f, n in new))

    def test_no_site_is_classified_twice(self):
        dup = sorted(k for k, v in _ALL.items() if len(v) > 1)
        assert not dup, (
            "2 つ以上の分類に登録されている:\n  "
            + "\n  ".join(f"{f}::{n} → {_ALL[(f, n)]}" for f, n in dup))

    def test_registry_has_no_stale_entries(self):
        """消えた関数の登録を残さない（登録簿の陳腐化を防ぐ）。"""
        pop = set(population())
        stale = sorted(set(_ALL) - pop)
        assert not stale, (
            "登録簿に、もう母数に無い関数が残っている。"
            "直った／消えたなら登録から外すこと:\n  "
            + "\n  ".join(f"{f}::{n} ({'/'.join(_ALL[(f, n)])})" for f, n in stale))


class TestKnownDefectsAreTracked:
    """★ 実害として登録したものが、直ったら気付けること。"""

    def test_must_report_entries_are_still_in_the_population(self):
        pop = set(population())
        gone = sorted(set(MUST_REPORT) - pop)
        assert not gone, (
            "MUST_REPORT の関数が母数から外れた。"
            "握りつぶしを直したなら登録から外すこと（良いこと）:\n  "
            + "\n  ".join(f"{f}::{n}" for f, n in gone))

    def test_unreviewed_does_not_grow(self):
        """★ 未精査は増やさない。ver52.3 で減らして 0 にする。"""
        assert len(UNREVIEWED) <= 11, (
            f"未精査が {len(UNREVIEWED)} 件に増えている。"
            "新しい実装は精査してから REPORTS_SKIPS / SKIPS_ARE_IMMATERIAL / "
            "MUST_REPORT のいずれかに入れること")

    def test_unreviewed_is_declared_not_hidden(self):
        """★ 未精査があること自体を可視にする。

        この型はまだ閉じていない。UNREVIEWED が空になって初めて
        「T5 を全数見た」と言える。緑だから終わり、ではない。
        """
        if UNREVIEWED:
            pytest.xfail(
                f"T5 はまだ閉じていない: 未精査 {len(UNREVIEWED)} 件 / "
                f"母数 {len(population())} 件。ver52.3 で 1 件ずつ判断する")
