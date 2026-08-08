"""資料に**印字する設定**と、図を**描くのに使った設定**が同一由来であることを照合する (ver52.2)。

■ なぜこの番人なのか

「表示 ≠ 計算」(T7) は ver51.1 / 51.3 / 51.7 / 51.8 / 51.9 / 52.0 / 52.1 の
**7 版**にまたがって再発している（T3・T8 と並んで最多）。にもかかわらず番人が無い。

    ver51.9 A-5   `poly3` と表示して 1 次で当てる。R² は 1.0 と出す
    ver51.9 B-2   Volcano が閾値を無視するのに、同じ資料の「解析条件」スライドには
                  利用者の閾値が印字される → **資料が自己矛盾する**
    ver51.9 B-6   Cluster 統計表は生の ID、各スライドの見出しは改名後
    ver52.0 17.1  符号を見ない並べ替えなのに「高発現の上位」と説明する

この型が厄介なのは、**利用者が矛盾を検算できない**こと。
図と説明文が同じ資料に載っているので、どちらが正しいか外から判断できない。

■ 何を検査するか

ver51.9 B-2 の修正で正しい構造は既にできている:

    「解析条件」スライドと図が **同じ 1 つの dict (`conditions`) を見る」
    → `_display_settings(conditions)` が両者の唯一の取り出し口

本テストは **その構造が崩れないこと**を守る。具体的には、
PPTX の図生成関数を呼ぶとき、設定引数に**数値リテラルを直書きしない**こと。

リテラルを書いた瞬間、その図だけが `conditions` から切り離され、
条件スライドとの整合が静かに壊れる。B-2 はまさにそれだった:

    _build_volcano_fig_for_cluster(deg_data, cl_str)   # 既定 0.5 / 1.3 のまま
    ↑ 引数を渡さないことも「リテラルを書いた」のと同じ結果になる

なので **引数の省略も検出する**。
"""

import ast
from pathlib import Path

PPTX = (Path(__file__).resolve().parent.parent
        / "app" / "callbacks" / "interactive_pptx.py")

# 図の生成関数と、「条件スライドと揃っていなければならない設定引数」。
# 値は `_display_settings()` の戻り値から取ること。
BUILDERS = {
    "_build_volcano_fig_for_cluster": {"fc_thresh", "p_thresh", "label_top_n"},
    "_build_heatmap_for_cluster": {"scale"},
    "_build_feature_plot_fig": {"intensity_min", "intensity_max"},
}

# 設定の唯一の取り出し口。ここ以外で既定値を作らない。
SETTINGS_SOURCE = "display_settings"


def _tree():
    return ast.parse(PPTX.read_text(encoding="utf-8"))


def _builder_calls():
    """BUILDERS の呼び出しを (関数名, 行, キーワード dict) で列挙する。"""
    out = []
    for node in ast.walk(_tree()):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in BUILDERS):
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            out.append((node.func.id, node.lineno, kw))
    return out


def _is_derived(value):
    """値が「取り出し口から来ている」形か。

    `display_settings["volcano_fc"]` や、そこから作った変数・式を許す。
    数値リテラルだけを拒否する。
    """
    if isinstance(value, ast.Constant):
        return not isinstance(value.value, (int, float)) or isinstance(value.value, bool)
    return True


def _derived_local_names():
    """`x = display_settings[...]` のように取り出し口から代入された名前。

    中間変数を 1 段たどるためのもの
    （`_hm_scale = display_settings["heatmap_scale"]` の形は正しい使い方）。
    """
    names = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(s, ast.Name) and s.id == SETTINGS_SOURCE
                   for s in ast.walk(node.value)):
            continue
        for t in node.targets:
            if isinstance(t, ast.Name):
                names.add(t.id)
    return names


def _mentions_settings_source(value, derived=None):
    derived = _derived_local_names() if derived is None else derived
    for sub in ast.walk(value):
        if isinstance(sub, ast.Name) and (sub.id == SETTINGS_SOURCE
                                          or sub.id in derived):
            return True
    return False


# --------------------------------------------------------------------------
class TestTheGuardIsNotInert:
    """★ 番人が空振りしていないこと（ver51.9 で 3 回空振りさせた反省）。"""

    def test_pptx_module_exists(self):
        assert PPTX.exists(), f"{PPTX} が無い"

    def test_builders_are_defined(self):
        defined = {n.name for n in ast.walk(_tree())
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        missing = sorted(set(BUILDERS) - defined)
        assert not missing, (
            f"BUILDERS に実在しない関数がある（名前が変わった？）: {missing}")

    def test_builder_calls_are_discoverable(self):
        calls = _builder_calls()
        assert len(calls) >= 3, (
            f"図の生成呼び出しが {len(calls)} 件しか見つからない。走査が壊れている疑い")

    def test_settings_extractor_exists(self):
        names = {n.name for n in ast.walk(_tree())
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert "_display_settings" in names, (
            "`_display_settings` が無い。条件スライドと図の唯一の取り出し口なので、"
            "消したなら本テストの前提ごと見直すこと")


class TestFiguresUseTheDeclaredSettings:
    """★ 本丸: 図が「解析条件」スライドと同じ設定を使っていること。"""

    def test_no_setting_is_passed_as_a_literal(self):
        bad = []
        for name, line, kw in _builder_calls():
            for setting in sorted(BUILDERS[name]):
                if setting in kw and not _is_derived(kw[setting]):
                    bad.append(
                        f"interactive_pptx.py:{line}  {name}({setting}="
                        f"{ast.unparse(kw[setting])})")
        assert not bad, (
            "図の設定に数値リテラルを直書きしている。\n"
            "その図だけが `conditions` から切り離され、"
            "**同じ資料の「解析条件」スライドと食い違う**（ver51.9 B-2 と同じ）:\n  "
            + "\n  ".join(bad))

    def test_no_setting_is_silently_omitted(self):
        """★ 引数を渡さないのはリテラルを書くのと同じ結果になる。

        B-2 はまさに「渡していない」形だった
        （`_build_volcano_fig_for_cluster(deg_data, cl_str)` で既定 0.5/1.3）。
        """
        bad = []
        for name, line, kw in _builder_calls():
            missing = sorted(BUILDERS[name] - set(kw))
            if missing:
                bad.append(
                    f"interactive_pptx.py:{line}  {name}() が "
                    f"{', '.join(missing)} を渡していない → 既定値で描かれる")
        assert not bad, (
            "図の設定を渡さずに呼んでいる。既定値で描かれるので、"
            "利用者が変えた設定が資料に反映されない:\n  " + "\n  ".join(bad))

    def test_settings_come_from_the_single_source(self):
        """★ 取り出し口が 1 つであること（構造で守る）。"""
        bad = []
        for name, line, kw in _builder_calls():
            for setting in sorted(BUILDERS[name] & set(kw)):
                if not _mentions_settings_source(kw[setting]):
                    bad.append(
                        f"interactive_pptx.py:{line}  {name}({setting}="
                        f"{ast.unparse(kw[setting])})")
        assert not bad, (
            f"設定が `{SETTINGS_SOURCE}` 由来になっていない。\n"
            "条件スライドと図が別の値を見ると、どちらが本当か資料から判断できない:\n  "
            + "\n  ".join(bad))


class TestDefaultsLiveInExactlyOnePlace:
    """★ 既定値が 1 箇所に集まっていること。

    既定値が散らばると「どれが効いているのか」が読めなくなり、
    ver51.9 A-5（poly3 と表示して 1 次で当てる）のような
    表示と計算の食い違いが生まれる土壌になる。
    """

    def test_display_defaults_table_exists(self):
        src = PPTX.read_text(encoding="utf-8")
        assert "_DISPLAY_DEFAULTS" in src, (
            "既定値表 `_DISPLAY_DEFAULTS` が無い。"
            "既定値を各所に散らすと表示と計算がずれる")

    def test_builder_signature_defaults_are_not_the_contract(self):
        """★ 関数シグネチャの既定値に頼らないこと。

        `def _build_volcano_fig_for_cluster(..., fc_thresh=0.5, p_thresh=1.3)`
        の既定値は「呼び忘れたときに従来と同じ絵が出る」ための保険であって、
        契約ではない。呼び出し側は必ず明示的に渡す
        （それを `test_no_setting_is_silently_omitted` が担保する）。
        ここではシグネチャ側の既定値が**数値である**ことだけ確認し、
        意味が変わったら気付けるようにする。
        """
        sig_defaults = {}
        for n in ast.walk(_tree()):
            if not (isinstance(n, ast.FunctionDef) and n.name in BUILDERS):
                continue
            args = [a.arg for a in n.args.args]
            offset = len(args) - len(n.args.defaults)
            for i, d in enumerate(n.args.defaults):
                key = args[offset + i]
                if key in BUILDERS[n.name] and isinstance(d, ast.Constant):
                    sig_defaults[(n.name, key)] = d.value
        assert sig_defaults, (
            "図の生成関数の設定引数に既定値が 1 つも無い。"
            "呼び忘れが TypeError になる設計へ変えたなら本テストを見直すこと")
