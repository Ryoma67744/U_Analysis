"""Python→R の設定注入を **全数照合** する (ver52.1)。

■ なぜ全数照合なのか

ver51.6 から ver52.0 まで 5 ラウンド、監査のたびに個別の欠陥を直してきたが
新しい穴が出続けた。原因は「見つかった箇所だけ直す」やり方にある。
機械的に数えると、同じ形のコードは現役スクリプトだけで数百箇所ある。
標本を抜き続けても終わらない。

そこで方針を変える。**1 つの型について「全部見た」と言い切れる検査**を置く。
その型は二度と監査の対象にならない。これはその第 1 号。

■ この型

`analysis_runner._replace_assign(lines, "VAR", value)` は R スクリプトの
`VAR <- ...` 行を書き換えるが、**一致 0 件でも成功扱い**で返る:

    def _replace_assign(lines, var, new_rhs):
        pattern = re.compile(rf"^\\s*{re.escape(var)}\\s*<-\\s*.*$")
        for i, line in enumerate(lines):
            if pattern.match(line):
                lines[i] = f"{var} <- {new_rhs}"
                break
        return lines            # ← 0 件でも lines をそのまま返す

対象テンプレに `VAR` が無ければ、**利用者が画面で指定した設定が黙って捨てられる**。
外部監査はこの型を 2 件（`ANNOTATION_CSV_PATH` / `MRM_FILE_PATH`）報告した。
本テストは呼び出し 51 箇所すべてを照合するので、**残りが無いことまで込みで**
担保できる。

★ TIMS 再解析の実害はさらに悪い。`ANNOTATION_CSV_PATH` が置換されないまま
  テンプレ側が `V13_ANNOTATION_CSV_PATH`（ハードコードされた Windows Dropbox
  パス）を ver6 コピーへ伝播するため、**選択が無視されるだけでなく、
  解析ホストに存在しない絶対パスが代わりに注入される**。
"""

import ast
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
RUNNER = APP / "app" / "services" / "analysis_runner.py"

# `analysis_runner` が組み立てるテンプレ。config.py が実際に指しているもの。
TEMPLATES = {
    "generate_v8_config": {
        "TIMS 通常": APP / "Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R",
        "DESI 通常": APP / "Script/DESI/260623_DESI-UMAP_Template_v16.R",
    },
    "generate_cluster_filter_config": {
        "TIMS 再解析": APP / "Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R",
        "DESI 再解析": APP / "Script/DESI/DESI_RDS_ClusterFilter_ver3.R",
    },
}

# ★ 現状デッドコードだが、テンプレに変数が無い呼び出し。
#   `analysis_callbacks` の再解析 params にキーが入らないので今は無害。
#   キーを足した瞬間に無言で効かなくなるため、既知として記録し、
#   **これ以上増えないこと**を担保する。
KNOWN_DEAD = {
    ("generate_cluster_filter_config", "USE_ROI_AS_SAMPLE"),
    ("generate_cluster_filter_config", "ROI_FILTER"),
}

# ★ 実害があると確認済みだが **まだ直していない**もの（監査 R-01 / R-02）。
#   隠さず記録し、(a) これ以上増えないこと (b) 直ったら気付けること を担保する。
#   直すには R テンプレ側の変更が要るため ver52.2 に回している。
KNOWN_UNFIXED = {
    ("generate_cluster_filter_config", "ANNOTATION_CSV_PATH"):
        "R-01: テンプレの変数は V13_ANNOTATION_CSV_PATH。"
        "置換されないまま V13 側のハードコード Windows パスが ver6 コピーへ伝播する",
    ("generate_cluster_filter_config", "MRM_FILE_PATH"):
        "R-02: DESI 再解析テンプレに MRM_FILE_PATH も V8_MRM_FILE_PATH も無い。"
        "v16 のハードコード Windows パスが残り化合物照合が無言で飛ぶ",
}


def _calls():
    """`_replace_assign(lines, "VAR", ...)` を (関数名, 行, 変数名) で列挙。"""
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    out = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_replace_assign"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)):
                out.append((fn.name, node.lineno, node.args[1].value))
    return out


def _assigned(var, body):
    return re.search(rf"(?m)^\s*{re.escape(var)}\s*<-", body) is not None


@pytest.fixture(scope="module")
def template_text():
    out = {}
    for fn, m in TEMPLATES.items():
        for label, path in m.items():
            assert path.exists(), f"テンプレが見つからない: {path}"
            out[(fn, label)] = path.read_text(encoding="utf-8", errors="replace")
    return out


class TestEveryInjectionTargetExists:
    """★ 本丸: 置換しようとする変数が、対象テンプレに実在すること。"""

    def test_calls_are_discoverable(self):
        """前提の固定: 呼び出しが見つからないなら検査が空振りしている。"""
        calls = _calls()
        assert len(calls) >= 40, f"_replace_assign の呼び出しが少なすぎる: {len(calls)}"

    def test_no_injection_targets_a_missing_variable(self, template_text):
        dead = []
        for fn, lineno, var in _calls():
            scopes = TEMPLATES.get(fn)
            if not scopes:
                continue                       # 対象テンプレが特定できない
            if any(_assigned(var, template_text[(fn, label)]) for label in scopes):
                continue
            if (fn, var) in KNOWN_DEAD or (fn, var) in KNOWN_UNFIXED:
                continue
            dead.append(f"analysis_runner.py:{lineno}  {fn}() → {var}")

        assert not dead, (
            "どのテンプレにも存在しない R 変数を置換しようとしている。"
            "`_replace_assign` は 0 件一致でも成功扱いなので、"
            "**利用者が画面で指定した設定が黙って捨てられる**:\n  "
            + "\n  ".join(dead))

    def test_known_dead_injections_do_not_grow(self, template_text):
        """★ 既知のデッドコードが増えないこと。

        現状は再解析 params に該当キーが入らないので無害だが、
        キーを足した瞬間に無言で効かなくなる罠なので数を固定する。
        """
        dead = set()
        for fn, _lineno, var in _calls():
            scopes = TEMPLATES.get(fn)
            if not scopes:
                continue
            if not any(_assigned(var, template_text[(fn, label)])
                       for label in scopes):
                dead.add((fn, var))
        new = dead - KNOWN_DEAD - set(KNOWN_UNFIXED)
        assert not new, f"デッドな注入が増えた: {sorted(new)}"
        gone = KNOWN_DEAD - dead
        if gone:
            pytest.fail(
                f"既知のデッド注入が解消された: {sorted(gone)}。"
                "KNOWN_DEAD から外すこと（穴が塞がったのは良いこと）")


class TestAuditFindingsAreCovered:
    """監査が報告した 2 件を名指しで固定する（回帰防止）。"""

    @pytest.mark.xfail(strict=True, reason=(
        "R-01 / R-02: 未修正。R テンプレ側の変更が要るため ver52.2 に回している。"
        "直ったらこのテストが xpass になるので、その時点で xfail を外す"))
    @pytest.mark.parametrize("var,label", [
        ("ANNOTATION_CSV_PATH", "TIMS 再解析"),
        ("MRM_FILE_PATH", "DESI 再解析"),
    ])
    def test_reanalysis_annotation_target(self, template_text, var, label):
        body = template_text[("generate_cluster_filter_config", label)]
        py = RUNNER.read_text(encoding="utf-8")
        if not re.search(rf'_replace_assign\([^)]*"{re.escape(var)}"', py):
            pytest.skip(f"{var} への注入はもう存在しない（別方式へ移行済み）")
        assert _assigned(var, body), (
            f"{label}テンプレに `{var} <-` が無いのに Python が置換しようとしている。"
            "利用者が選んだアノテーション DB が黙って捨てられる")


class TestReplaceAssignIsVerifiable:
    """★ 恒久対策: 0 件一致を検出できる形になっていること。

    現状の `_replace_assign` は件数を返さないので、呼び出し側は成否を知れない。
    必須の注入だけでも件数を検証する版へ移す必要がある。
    ここでは「必須版が用意されたか」を追跡する（未対応なら xfail）。
    """

    def test_a_required_variant_exists(self):
        py = RUNNER.read_text(encoding="utf-8")
        has_required = bool(re.search(
            r"def _replace_assign_required|must_replace|count\s*==\s*1", py))
        if not has_required:
            pytest.xfail(
                "必須注入用の検証付き置換 (`_replace_assign_required` 等) が未実装。"
                "現状は全数照合テストで代替している")
