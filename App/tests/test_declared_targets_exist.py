"""「宣言した対象が実在すること」を層をまたいで **全数照合** する (ver52.2)。

■ この型

コードが「この名前のものを探す/書き換える」と宣言しているのに、その名前が
**どこにも存在しない**。探索は 0 件で終わり、例外も警告も出ない。
結果、**利用者が指定した設定が黙って捨てられる**か、**証跡が黙って欠ける**。

ver51.0〜52.1 の分類では
「宣言した対象が実在しない (T1)」が 5 版、
「宣言はあるが述語が常に偽 (T8)」が **7 版**にまたがって再発している。
T8 は最多タイの再発回数を持ちながら、番人が 1 本も無かった。

■ 同じ型が 4 つの層に出ている（実測）

| 層 | 宣言している対象 | 実際 |
|---|---|---|
| Python→R 注入 | `ANNOTATION_CSV_PATH` | テンプレの変数は `V13_…` |
| R→R の patch anchor | `seu_list[[ii]] <- …` | v16 は `[[length(seu_list)+1]] <- …` |
| Dash の入力検証 | `PARAM_BOUNDS["umap_n_neighbors"]` | 画面 id は `umap_n_neighbors_input` |
| **証跡の収集** | glob `v8_runtime_*.R` | 再解析は `cluster_filter_runtime_*.R` |

第 1 層は `test_r_injection_completeness.py` (ver52.1)、
第 3 層は `test_numeric_input_bounds.py` (ver52.2)、
第 2 層は `test_r_patch_anchors.py` (ver52.2) が担当する。
**本テストは第 4 層と、同じ形の Dash 側 (アコーディオン節 id) を担当する。**

■ なぜ既存の節 id 検査では足りないか

`test_misc_silent_wrongs.py::TestAccordionSectionIds` は同じことを見ているが、
**呼び出し側を 4 ファイル、宣言側を 1 ファイルにハードコード**している。
5 つ目のファイルから呼んでも、別のレイアウトで宣言しても検出できない。

これは ver51.9 で自ら批判した形（ver51.8 の「書き込み側 7 箇所」parametrize が
読み出し側を漏らして A-3 を通した）と同じなので、本テストで全域に広げる。
"""

import ast
import fnmatch
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"


def _trees():
    """app/ 配下の全 .py を (相対パス, AST) で返す。"""
    out = []
    for path in sorted(APP.rglob("*.py")):
        try:
            out.append((path.relative_to(APP.parent),
                        ast.parse(path.read_text(encoding="utf-8"))))
        except SyntaxError as e:
            pytest.fail(f"{path} が構文エラー: {e}")
    return out


# ===========================================================================
# 第 4 層: 実行スクリプトの証跡
# ===========================================================================
def _written_runtime_scripts():
    """`analysis_runner` が書き出す実行スクリプト名を fnmatch パターンで返す。

    `config_filename = f"v8_runtime_{timestamp}.R"` → `v8_runtime_*.R`
    """
    src = (APP / "services" / "analysis_runner.py").read_text(encoding="utf-8")
    out = {}
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "config_filename"):
            continue
        v = node.value
        if isinstance(v, ast.JoinedStr):
            pat = "".join(
                p.value if isinstance(p, ast.Constant) else "*"
                for p in v.values)
        elif isinstance(v, ast.Constant) and isinstance(v.value, str):
            pat = v.value
        else:
            continue
        out[pat] = f"analysis_runner.py:{node.lineno}"
    return out


def _runtime_script_globs():
    """実行スクリプトを拾う glob パターンを (パス, 行, パターン) で返す。"""
    out = []
    for rel, tree in _trees():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "glob"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and "runtime" in node.args[0].value):
                out.append((str(rel), node.lineno, node.args[0].value))
    return out


# ★ 収集されないと分かっているが ver52.2 では直さない（ver52.3 の対象）。
#   ver52.2 は「番人だけを入れて母数を測る版」。
KNOWN_UNCOLLECTED = {
    "cluster_filter_runtime_*.R": (
        "再解析 (analysis_runner.py:754) が書くファイル名。"
        "収集側は provenance.py:194 と receipt.py:320 の **2 箇所とも** "
        "`v8_runtime_*.R` しか見ないので、再解析の結果には実行スクリプトの"
        "証跡が 1 件も付かない。"
        "★ 「黙って間違った値を報告する」ではなく「値を報告できない」。"
        "  `recover_conditions` は復元値に `_sources=runtime_script` の印を付け、"
        "  Methods は 黒(記録済)/青(復元)/赤(未記録) で塗り分けるので、"
        "  証跡が無ければ赤になる。誤報ではないぶん 1 段軽い"),
}


class TestRuntimeScriptEvidenceIsComplete:
    """★ 書き出す実行スクリプトが、証跡の収集側に必ず拾われること。

    `provenance.latest_runtime_script` が拾えないと、Methods の
    「実際に走った条件」の裏付けがその解析だけ丸ごと欠ける。
    黙って欠けるので、利用者は「記録されていない」ことに気付けない。
    """

    def test_the_guard_is_not_inert(self):
        written = _written_runtime_scripts()
        globs = _runtime_script_globs()
        assert written, "analysis_runner が書く実行スクリプト名を拾えない"
        assert globs, "実行スクリプトを拾う glob が見つからない"

    @staticmethod
    def _uncollected():
        globs = _runtime_script_globs()
        out = set()
        for pat in _written_runtime_scripts():
            sample = pat.replace("*", "20260808_120000")
            if not any(fnmatch.fnmatch(sample, g) for _f, _l, g in globs):
                out.add(pat)
        return out

    def test_every_written_script_is_collected_by_some_glob(self):
        written = _written_runtime_scripts()
        globs = sorted({g for _f, _l, g in _runtime_script_globs()})
        new = sorted(self._uncollected() - set(KNOWN_UNCOLLECTED))
        assert not new, (
            "書き出しているのに証跡として収集されない実行スクリプトが**新たに**増えた。\n"
            "その解析の Methods は「実際に走った条件」の裏付けを持てない:\n  "
            + "\n  ".join(f"{written[p]}  {p}" for p in new)
            + f"\n  収集側の glob: {', '.join(globs)}")

    def test_known_uncollected_does_not_shrink_silently(self):
        """★ 直ったら登録から外させる（記録が次の欠陥を隠す棚にならないように）。"""
        fixed = sorted(set(KNOWN_UNCOLLECTED) - self._uncollected())
        assert not fixed, (
            "KNOWN_UNCOLLECTED に載っているが実際には収集されるようになっている。"
            "直ったのは良いことなので登録から外すこと:\n  " + "\n  ".join(fixed))

    def test_known_uncollected_entries_are_still_written(self):
        """消えた書き出しの登録を残さない（登録簿の陳腐化を防ぐ）。"""
        gone = sorted(set(KNOWN_UNCOLLECTED) - set(_written_runtime_scripts()))
        assert not gone, (
            "KNOWN_UNCOLLECTED に、もう書き出されないファイル名が残っている。"
            "登録から外すこと:\n  " + "\n  ".join(gone))

    def test_every_glob_matches_something_we_write(self):
        """逆向き: 誰も書かないファイルを探し続けている glob が無いこと。"""
        written = _written_runtime_scripts()
        dead = []
        for f, ln, g in _runtime_script_globs():
            if not any(fnmatch.fnmatch(p.replace("*", "X"), g)
                       for p in written):
                dead.append(f"{f}:{ln}  glob({g!r})")
        assert not dead, (
            "どの書き出しにも一致しない glob がある。"
            "ファイル名を変えたときに収集側を直し忘れた可能性:\n  "
            + "\n  ".join(dead))


# ===========================================================================
# Dash 層: アコーディオン節 id（既存検査の全域化）
# ===========================================================================
def _declared_section_ids():
    """`item_id="..."` を app/ 全域から集める。"""
    ids = {}
    for rel, tree in _trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "item_id" and isinstance(kw.value, ast.Constant):
                    ids.setdefault(kw.value.value, f"{rel}:{node.lineno}")
    return ids


def _used_section_ids():
    """`accordion_toggle_is_noop("...", …)` を app/ 全域から集める。"""
    out = []
    for rel, tree in _trees():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "accordion_toggle_is_noop"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)):
                out.append((str(rel), node.lineno, node.args[0].value))
    return out


class TestAccordionSectionIdsRepoWide:
    """★ 存在しない節 id を見ていないこと（app/ 全域）。

    ver51.9 C-3: `acc_umap_integrated` / `acc_umap_facet` は実在せず
    （実在するのは `acc_umap`）、`is_open` が常に False になっていた。
    → UMAP を畳んだ状態で改名や色変更をしてから開き直すと、
      画面も一括保存もサムネも古いままになる。
    """

    def test_the_guard_is_not_inert(self):
        assert _declared_section_ids(), "item_id の宣言が 1 つも見つからない"
        assert _used_section_ids(), "accordion_toggle_is_noop の呼び出しが無い"

    def test_every_used_id_is_declared(self):
        declared = _declared_section_ids()
        bad = [f"{f}:{ln}  {sec!r}"
               for f, ln, sec in _used_section_ids() if sec not in declared]
        assert not bad, (
            "存在しないアコーディオン節 id を見ている。"
            "`is_open` が常に False になり『変化なし』と判定されるので、"
            "その節は再描画されない:\n  " + "\n  ".join(bad)
            + f"\n  実在する id: {sorted(declared)}")

    def test_scan_is_wider_than_the_old_hardcoded_list(self):
        """★ 全域化できていること。

        旧検査は呼び出し側 4 ファイル・宣言側 1 ファイル固定だった。
        走査対象が狭まったらここで気付く。
        """
        files = {f for f, _ln, _s in _used_section_ids()}
        assert len(files) >= 2, (
            f"呼び出し元が {len(files)} ファイルしか見つからない。走査が狭すぎる")


# ===========================================================================
# 横断: 「宣言した対象が実在する」型に番人が付いていること
# ===========================================================================
class TestEveryLayerOfThisTypeHasAGuard:
    """★ この型は 4 層に出ている。各層に番人が在ることを表明する。

    番人そのものが消えたり名前が変わったりしたら気付けるようにする
    （ver51.6 の scipy 番人のように、番人が形骸化するのが一番危ない）。
    """

    TESTS = Path(__file__).resolve().parent

    @pytest.mark.parametrize("layer,filename", [
        ("Python→R 注入", "test_r_injection_completeness.py"),
        ("R→R patch anchor", "test_r_patch_anchors.py"),
        ("Dash の入力検証", "test_numeric_input_bounds.py"),
    ])
    def test_sibling_guard_exists(self, layer, filename):
        assert (self.TESTS / filename).exists(), (
            f"{layer} の番人 {filename} が無い。"
            "この型は 4 層に出るので、1 層でも欠けるとそこから再発する")
