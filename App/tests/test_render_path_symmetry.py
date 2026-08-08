"""同じ図を描く実装が 4 系統ある。その全部に設定が届くことを **全数照合** する (ver52.2)。

■ なぜこの番人なのか

ver51.0〜52.1 の欠陥 約 98 件を型に分類して「何版にまたがって再発したか」を数えた
ところ、**「経路の重複」が 7 版で最多**だった。にもかかわらずこの型には番人が
1 本も無い。番人は「列挙が安いところ」に作られていて、「再発が多いところ」には
作られていなかった。本テストはその最上位を埋める。

■ この型

同じ図を描く実装が **4 系統**ある:

    ① 画面   interactive_deg / interactive_umap / interactive_spatial
    ② PPTX   interactive_pptx
    ③ Lite   lite_view_callbacks
    ④ 共有   share_callbacks

このうち **import で共有されている役割**（統合 UMAP・Spatial）は、1 箇所直せば
全経路に効く。**経路ごとに再実装されている役割**（Volcano・Heatmap・Feature）は
N 回直さないと揃わない。

ver51.9 の B-2「Volcano が利用者の閾値を無視する」は、**監査が名指しした PPTX の
1 コピーだけ**を直した。実測では Lite と共有ビューに同じ欠陥が残っている:

    lite_view_callbacks.py:1406   fc_thresh = 0.5
    lite_view_callbacks.py:1407   p_thresh  = 1.3
    share_callbacks.py:798        fc_thresh, p_thresh = 0.5, 1.3

★ 「Lite / 共有を見ていなかった」のではない。直近 20 コミットで
  `lite_view_callbacks.py` は 9 回、`share_callbacks.py` は 6 回触っている。
  **開いていたのに隣のコピーを確認しなかった**。注意力では直らないので列挙で守る。

■ 登録簿が陳腐化する問題

素朴に作ると「役割 → 実装」の登録簿を手で持つことになるが、**その登録簿こそ
「宣言した対象が実在しない」型そのもの**になる（監査 R-01 と同じ形）。
`test_r_injection_completeness` で使った形で閉じる:

    ① `ROLES` / `NOT_RENDERERS` の対象が実在すること      → 登録簿の陳腐化を防ぐ
    ② 役割名に一致する関数が**漏れなく**どちらかに載ること ← ★ 本丸。
       新しいコピーを足した瞬間に落ちるので、コピーが黙って増えない
    ③ 各実装が設定を引数で受けること。受けないものは `NOT_PROPAGATED` に
       理由付きで登録必須（かつ増えない）
"""

import ast
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
CB = APP / "app" / "callbacks"

# 図を描く 4 系統。ここに無いモジュールは検査対象外。
RENDER_MODULES = (
    "interactive_deg",        # ① 画面
    "interactive_umap",       # ① 画面
    "interactive_spatial",    # ① 画面
    "interactive_pptx",       # ② PPTX
    "lite_view_callbacks",    # ③ Lite ビュー
    "share_callbacks",        # ④ 共有ビュー
)

# 役割名。この語を関数名に含むものは「役割に関わる関数」として分類必須になる。
ROLE_PATTERN = re.compile(r"volcano|heatmap|feature", re.IGNORECASE)


# --------------------------------------------------------------------------
# 登録簿 1: 図を実際に描く実装
# --------------------------------------------------------------------------
ROLES = {
    "volcano": [
        ("interactive_deg", "update_volcano_plot"),
        ("interactive_pptx", "_build_volcano_fig_for_cluster"),
        ("lite_view_callbacks", "_build_volcano_fig"),
        ("share_callbacks", "sv_update_volcano_plot"),
    ],
    "heatmap": [
        ("interactive_deg", "update_heatmap"),
        ("interactive_pptx", "_build_heatmap_for_cluster"),
        ("lite_view_callbacks", "_build_heatmap_section"),
    ],
    "feature": [
        ("interactive_deg", "_update_feature_plot_inner"),
        ("interactive_pptx", "_build_feature_plot_fig"),
        ("share_callbacks", "sv_update_feature_plot"),
    ],
}

# --------------------------------------------------------------------------
# 登録簿 2: 役割名を含むが図は描かない関数（ヘルパ・トグル・ブックマーク等）
#   ★ ここに入れるのは「分類した」という表明。放置ではない。
# --------------------------------------------------------------------------
NOT_RENDERERS = {
    # --- 検索・選択肢・ブックマーク（図を作らない） ---
    ("interactive_deg", "filter_features"): "候補の絞り込み",
    ("interactive_deg", "update_feature_options_on_mz_filter"): "ドロップダウンの選択肢",
    ("interactive_deg", "add_feature_bookmark"): "ブックマーク追加",
    ("interactive_deg", "remove_feature_bookmark"): "ブックマーク削除",
    ("interactive_deg", "bookmark_to_feature"): "ブックマーク → 選択",
    ("interactive_deg", "update_volcano_cluster_options"): "ドロップダウンの選択肢",
    ("share_callbacks", "sv_filter_features"): "候補の絞り込み",
    ("lite_view_callbacks", "toggle_volcano_section"): "節の開閉のみ",
    # --- 表示ヘルパ（親の描画関数から呼ばれる。設定は親が受ける） ---
    ("interactive_deg", "_feature_intensity_style"): "描画ヘルパ",
    ("interactive_deg", "_feature_display_range"): "描画ヘルパ",
    ("interactive_deg", "_feature_colorbar_ticktext"): "描画ヘルパ",
    ("interactive_deg", "_feature_graph_config"): "描画ヘルパ",
    ("interactive_deg", "_feature_heading"): "見出し文字列",
    ("interactive_deg", "_apply_feature_data_to_stored"): "保存済み figure の書き換え",
    ("interactive_spatial", "auto_feature_marker"): "マーカーサイズの自動調整のみ",
    # --- 薄いラッパ（実体は _update_feature_plot_inner） ---
    ("interactive_deg", "update_feature_plot"): "実体は _update_feature_plot_inner",
    ("interactive_deg", "patch_feature_intensity"): (
        "既存 figure への差分適用。intensity_min/max は受け取っている"),
    # --- 未使用 (dead) ---
    ("interactive_pptx", "_build_heatmap_for_pptx"): (
        "DEAD: 定義のみで呼び出し 0 件。_build_heatmap_for_cluster に置き換わった。"
        "★ 復活させるなら scale を受けること（この関数は Z-score を強制する）"),
}

# NOT_RENDERERS のうち「未使用だから対象外」と言っているもの。
# 本当に呼ばれていないことを検査する（呼ばれ始めたら分類が嘘になる）。
DEAD_ENTRIES = {("interactive_pptx", "_build_heatmap_for_pptx")}


# --------------------------------------------------------------------------
# 役割ごとの「利用者設定」。画面側 (= 正解) が受け取っている引数から取った。
#   値は「同じ意味で使われている引数名」の集合（経路ごとに名前が違うため）。
# --------------------------------------------------------------------------
SETTINGS = {
    "volcano": {
        "FC 閾値": {"fc_thresh", "fc_threshold"},
        "p 閾値": {"p_thresh", "p_threshold"},
        "ラベル Top-N": {"label_top_n", "volcano_label_top_n"},
    },
    "heatmap": {
        "Top-N": {"top_n", "top_n_per_cluster", "heatmap_top_n"},
        "スケール": {"scale", "heatmap_scale"},
    },
    "feature": {
        "強度下限": {"intensity_min"},
        "強度上限": {"intensity_max"},
    },
}

# --------------------------------------------------------------------------
# ★ 設定が届いていない実装。**実害があると確認済みだが ver52.2 では直さない。**
#   ver52.2 は番人だけを入れる版で、直すのは ver52.3（計画どおり）。
#   隠さず記録し、(a) これ以上増えないこと (b) 直ったら気付けること を担保する。
# --------------------------------------------------------------------------
NOT_PROPAGATED = {
    ("volcano", "lite_view_callbacks", "_build_volcano_fig", "FC 閾値"):
        "lite_view_callbacks.py:1406 で 0.5 固定。共有ペイロードに閾値が無い",
    ("volcano", "lite_view_callbacks", "_build_volcano_fig", "p 閾値"):
        "lite_view_callbacks.py:1407 で 1.3 固定",
    ("volcano", "lite_view_callbacks", "_build_volcano_fig", "ラベル Top-N"):
        "Lite ビューは自動ラベルを出さない",
    ("volcano", "share_callbacks", "sv_update_volcano_plot", "FC 閾値"):
        "share_callbacks.py:798 で 0.5 固定",
    ("volcano", "share_callbacks", "sv_update_volcano_plot", "p 閾値"):
        "share_callbacks.py:798 で 1.3 固定",
    ("volcano", "share_callbacks", "sv_update_volcano_plot", "ラベル Top-N"):
        "共有ビューは自動ラベルを出さない",
    ("heatmap", "lite_view_callbacks", "_build_heatmap_section", "スケール"):
        "引数が無く Z-score 固定。画面の heatmap_scale が届かない",
    ("feature", "share_callbacks", "sv_update_feature_plot", "強度下限"):
        "share_callbacks.py:641 は強度レンジを受け取らない",
    ("feature", "share_callbacks", "sv_update_feature_plot", "強度上限"):
        "同上",
}


# --------------------------------------------------------------------------
# 走査
# --------------------------------------------------------------------------
def _tree(module):
    path = CB / f"{module}.py"
    assert path.exists(), f"検査対象モジュールが見つからない: {path}"
    return ast.parse(path.read_text(encoding="utf-8"))


def _top_level_functions(module):
    """モジュール直下の関数を {name: FunctionDef} で返す。"""
    return {
        n.name: n
        for n in _tree(module).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _role_named_functions():
    """役割名を含む関数を (module, name) で列挙する。"""
    out = set()
    for module in RENDER_MODULES:
        for name in _top_level_functions(module):
            if ROLE_PATTERN.search(name):
                out.add((module, name))
    return out


def _params(module, name):
    """関数の引数名を集合で返す（位置・キーワード専用・**kwargs も含む）。"""
    fn = _top_level_functions(module).get(name)
    if fn is None:
        return None
    a = fn.args
    names = {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    if a.kwarg is not None:
        names.add("**" + a.kwarg.arg)
    return names


def _registered():
    """ROLES に載っている (module, name) をすべて返す。"""
    return {(m, f) for impls in ROLES.values() for (m, f) in impls}


# --------------------------------------------------------------------------
class TestTheGuardIsNotInert:
    """★ 番人が空振りしていないこと。

    ver51.9 では新しく置いた番人が 3 回空振りしていた（対象を 1 つも拾えて
    いないのに緑になる）。走査が壊れたら**この検査が先に落ちる**ようにする。
    """

    def test_scanner_finds_role_functions(self):
        found = _role_named_functions()
        assert len(found) >= 20, (
            f"役割名を含む関数が少なすぎる: {len(found)} 件。走査が壊れている疑い")

    def test_every_render_module_is_parseable(self):
        for module in RENDER_MODULES:
            assert _top_level_functions(module), f"{module} から関数を 1 つも拾えない"

    def test_settings_cover_every_role(self):
        assert set(SETTINGS) == set(ROLES), (
            "SETTINGS と ROLES の役割が食い違っている: "
            f"{set(SETTINGS) ^ set(ROLES)}")


# --------------------------------------------------------------------------
class TestRegistryTargetsExist:
    """① 登録簿の対象が実在すること（登録簿自体の陳腐化を防ぐ）。"""

    def test_every_role_entry_resolves(self):
        missing = [
            f"{m}.{f}  (役割: {role})"
            for role, impls in ROLES.items()
            for (m, f) in impls
            if _params(m, f) is None
        ]
        assert not missing, (
            "ROLES に実在しない関数が登録されている。"
            "登録簿が陳腐化すると検査が黙って空振りする:\n  " + "\n  ".join(missing))

    def test_every_not_renderer_entry_resolves(self):
        missing = [
            f"{m}.{f}" for (m, f) in NOT_RENDERERS if _params(m, f) is None
        ]
        assert not missing, (
            "NOT_RENDERERS に実在しない関数が登録されている。"
            "消えた関数の登録は外すこと:\n  " + "\n  ".join(missing))

    def test_dead_entries_are_really_uncalled(self):
        """★ 「未使用だから対象外」と書いたものが本当に未使用であること。

        呼ばれ始めたら分類が嘘になるので、その時点で落とす。
        """
        called = []
        for (module, name) in DEAD_ENTRIES:
            hits = 0
            for other in RENDER_MODULES:
                for node in ast.walk(_tree(other)):
                    if (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == name):
                        hits += 1
            if hits:
                called.append(f"{module}.{name}  ({hits} 箇所から呼ばれている)")
        assert not called, (
            "DEAD として分類した関数が実際には呼ばれている。"
            "NOT_RENDERERS の分類を見直し、設定を受け取るようにすること:\n  "
            + "\n  ".join(called))


# --------------------------------------------------------------------------
class TestEveryCopyIsAccountedFor:
    """★★ 本丸: 役割名の関数が漏れなく分類されていること。

    新しいコピーを足した瞬間にここが落ちる。ver51.9 の B-2 が
    「PPTX だけ直して Lite と共有を見なかった」で済んでしまったのは、
    **コピーを列挙する仕組みが無かった**から。
    """

    def test_no_unclassified_role_function(self):
        classified = _registered() | set(NOT_RENDERERS)
        unclassified = sorted(_role_named_functions() - classified)
        assert not unclassified, (
            "役割名を含むのに分類されていない関数がある。\n"
            "図を描くなら ROLES に、描かないなら理由付きで NOT_RENDERERS に登録すること。\n"
            "★ 図を描くものを ROLES に入れ忘れると、"
            "**利用者の設定が届かないコピーが黙って増える**:\n  "
            + "\n  ".join(f"{m}.{f}" for m, f in unclassified))

    def test_registries_do_not_overlap(self):
        overlap = sorted(_registered() & set(NOT_RENDERERS))
        assert not overlap, (
            "ROLES と NOT_RENDERERS の両方に載っている関数がある:\n  "
            + "\n  ".join(f"{m}.{f}" for m, f in overlap))


# --------------------------------------------------------------------------
def _violations():
    """設定を受け取っていない (役割, module, 関数, 設定) を列挙する。"""
    out = set()
    for role, impls in ROLES.items():
        for (module, func) in impls:
            params = _params(module, func)
            if params is None:
                continue                      # 実在検査が別に落ちる
            if any(p.startswith("**") for p in params):
                continue                      # **kwargs は通す（判定不能）
            for label, aliases in SETTINGS[role].items():
                if not (params & aliases):
                    out.add((role, module, func, label))
    return out


class TestSettingsReachEveryCopy:
    """③ 各実装が利用者設定を引数で受け取ること。"""

    def test_no_new_setting_is_dropped(self):
        new = sorted(_violations() - set(NOT_PROPAGATED))
        assert not new, (
            "利用者の設定を受け取らない描画実装が**新たに**増えた。\n"
            "画面と出力／共有で違う図が出る（画面は正しいので送った側は気付けない）:\n  "
            + "\n  ".join(f"{role}: {m}.{f}  ← 「{s}」が届いていない"
                          for role, m, f, s in new))

    def test_known_gaps_do_not_shrink_silently(self):
        """★ 直ったら気付けること。

        `NOT_PROPAGATED` は「実害を認識したうえで ver52.3 に回す」という記録なので、
        直したら必ずここから外す。外し忘れると次の欠陥を隠す棚になる。
        """
        fixed = sorted(set(NOT_PROPAGATED) - _violations())
        assert not fixed, (
            "NOT_PROPAGATED に載っているが実際には設定が届くようになっている。"
            "直ったのは良いことなので、登録から外すこと:\n  "
            + "\n  ".join(f"{role}: {m}.{f} / {s}" for role, m, f, s in fixed))


# --------------------------------------------------------------------------
class TestSharedRolesStayShared:
    """★ 共有されている役割が、こっそり再実装されないこと。

    統合 UMAP と Spatial は 1 箇所で定義して PPTX が import している。
    そのおかげで ver51.9 C-2 の色修正は **両方に自動で効いた**。
    この形が崩れると Volcano と同じ道をたどる。
    """

    SHARED = [
        ("interactive_umap", "_build_umap_integrated_fig"),
        ("interactive_spatial", "_create_single_spatial_fig"),
    ]

    @pytest.mark.parametrize("module,func", SHARED)
    def test_definition_is_unique(self, module, func):
        owners = [m for m in RENDER_MODULES if func in _top_level_functions(m)]
        assert owners == [module], (
            f"{func} は {module} で 1 回だけ定義されているべきだが {owners} にある。"
            "経路ごとに再実装すると、修正が片側にしか効かなくなる")

    def test_pptx_imports_them_rather_than_reimplementing(self):
        imported = {
            alias.name
            for node in ast.walk(_tree("interactive_pptx"))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        for _module, func in self.SHARED:
            assert func in imported, (
                f"interactive_pptx が {func} を import しなくなっている。"
                "自前実装に切り替えたなら ROLES に登録して設定の受け渡しを表明すること")
