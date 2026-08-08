"""ver52.3 ⑥: Lite ビューが利用者の表示設定で描くこと。

■ 何が起きていたか

    lite_view_callbacks.py:1406   fc_thresh = 0.5
    lite_view_callbacks.py:1407   p_thresh  = 1.3   # -log10(0.05)
    lite_view_callbacks.py:590    _build_heatmap_section(...)   # top_n を渡さず既定 3

利用者が閾値を変えて Lite のリンクを配ると、**受け取った側には既定値の
Volcano が出る**。破線の位置も凡例の Up/Down 判定も既定のまま。
画面と資料（PPTX）は正しいので、**送った側は気づけない**。
Heatmap の Top-N は既定 3 で、画面の既定 5 とすら食い違っていた。

ver51.9 B-2 は監査が名指しした PPTX だけを直しており、
同じ役割の他のコピーを探していなかった（T3 経路の重複）。

■ ★ この番人が捕まえた、別の弱点

`_build_heatmap_section` は **最初から `top_n_per_cluster` 引数を持っていた**。
番人 `test_render_path_symmetry` は「引数で受け取っているか」しか見ないので
**緑のまま**だったが、呼び出し側が一度も渡していなかった。
「受け口がある」ことと「値が渡る」ことは別。
`TestSettingsAreActuallyPassedNotJustAccepted` を足して塞いだ。

■ 直し方

読み出し口を 1 つにした（`app/utils/display_settings.read_display_settings`）。
画面 / PPTX / Lite は同じ `interactive_settings.json` を出典にしているのに、
読むコードが経路の数だけあったのが「片方だけ直す」の原因だった。
Lite 側の設定読み出しは 2 箇所に同じブロックがあったので、
足す前に `_read_lite_display_bundle` へ抽出している
（2 箇所に 2 行ずつ足すのは、T3 を自分の手で 1 つ増やすこと）。
"""

import ast
from pathlib import Path

import pytest

from app.callbacks import lite_view_callbacks as LV
from app.utils.display_settings import DISPLAY_DEFAULTS, read_display_settings

APP = Path(__file__).resolve().parent.parent / "app"


def _deg(n=6):
    """Up 側に log2FC 0.3〜0.8 を散らした DEG レコード。

    ★ 0.5 をまたぐように作る。またがないと「閾値 0.2 でも 0.5 でも同じ図」に
      なってしまい、テストが何も固定しない（症状だけ見るテストの失敗例）。
    """
    fcs = [0.3, 0.4, 0.55, 0.7, -0.35, -0.62]
    return [
        {"gene": f"mz_{100 + i}.0000", "cluster": "0",
         "avg_log2FC": fcs[i % len(fcs)], "p_val_adj": "1e-10",
         "annotation": ""}
        for i in range(n)
    ]


def _categories(fig):
    """Volcano figure の trace 名 → 点の数。"""
    return {t.name: len(t.x) for t in fig.data if getattr(t, "name", None)}


class TestVolcanoUsesTheUsersThresholds:

    def test_default_matches_the_declared_default(self):
        fig = LV._build_volcano_fig(_deg(), "0")
        assert fig is not None
        # 破線 (hline/vline) が既定値の位置に引かれていること
        ys = [s["y0"] for s in fig.layout.shapes if s["type"] == "line"
              and s["y0"] == s["y1"]]
        assert ys and ys[0] == DISPLAY_DEFAULTS["volcano_p"]

    def test_lower_fc_threshold_moves_points_into_up_and_down(self):
        """★ 本丸: 閾値を下げると Up/Down の点が増えること。

        修正前は引数を無視して 0.5 固定だったので、この 2 つは同じ答えになる。
        """
        strict = _categories(LV._build_volcano_fig(_deg(), "0", fc_thresh=0.5))
        loose = _categories(LV._build_volcano_fig(_deg(), "0", fc_thresh=0.2))
        assert loose.get("Up", 0) > strict.get("Up", 0), (
            f"FC 閾値を下げても Up の点数が変わらない: {strict} → {loose}。"
            "閾値が図に届いていない")
        assert loose.get("Down", 0) > strict.get("Down", 0)

    def test_dashed_lines_follow_the_threshold(self):
        """破線の位置＝凡例の判定基準。ずれると図が自己矛盾する。"""
        fig = LV._build_volcano_fig(_deg(), "0", fc_thresh=0.2, p_thresh=2.0)
        lines = fig.layout.shapes
        xs = sorted({s["x0"] for s in lines if s["x0"] == s["x1"]})
        ys = sorted({s["y0"] for s in lines if s["y0"] == s["y1"]})
        assert xs == [-0.2, 0.2], f"FC の破線が閾値と違う位置にある: {xs}"
        assert ys == [2.0], f"p の破線が閾値と違う位置にある: {ys}"

    def test_thresholds_come_from_the_settings_file_shape(self):
        """`interactive_settings.json` の形からそのまま読めること。"""
        settings = {"volcano_display": {"fc_threshold": 0.2, "p_threshold": 2.0}}
        d = read_display_settings(settings)
        fig = LV._build_volcano_fig(
            _deg(), "0", fc_thresh=d["volcano_fc"], p_thresh=d["volcano_p"])
        ys = sorted({s["y0"] for s in fig.layout.shapes if s["y0"] == s["y1"]})
        assert ys == [2.0]


def _heatmap_title(component):
    texts = []
    stack = [component]
    while stack:
        n = stack.pop()
        if isinstance(n, str):
            texts.append(n)
        elif isinstance(n, (list, tuple)):
            stack.extend(n)
        elif hasattr(n, "children"):
            stack.append(n.children)
    return " ".join(texts)


class TestHeatmapUsesTheUsersTopN:

    def _deg_many(self):
        out = []
        for c in ("0", "1"):
            for i in range(8):
                out.append({"gene": f"c{c}_g{i}", "cluster": c,
                            "avg_log2FC": 2.0 - i * 0.1, "p_val_adj": "1e-9"})
        return out

    def test_default_matches_the_screen_default_not_three(self):
        """★ 既定値どうしの食い違いを潰す。

        従来は Lite の既定が 3、画面 (`PARAM_BOUNDS["heatmap_top_n"]`) が 5 で、
        利用者が何も設定していなくても違う図が出ていた。
        """
        assert DISPLAY_DEFAULTS["heatmap_top_n"] == 5
        sec = LV._build_heatmap_section(self._deg_many())
        assert "Top 5 markers" in _heatmap_title(sec), _heatmap_title(sec)

    def test_the_users_value_is_used(self):
        sec = LV._build_heatmap_section(self._deg_many(), top_n_per_cluster=2)
        assert "Top 2 markers" in _heatmap_title(sec)

    def test_settings_file_value_flows_through(self):
        d = read_display_settings({"heatmap_display": {"top_n": 4}})
        sec = LV._build_heatmap_section(
            self._deg_many(), top_n_per_cluster=d["heatmap_top_n"])
        assert "Top 4 markers" in _heatmap_title(sec)

    def test_title_no_longer_claims_z_score(self):
        """★ 表示 ≠ 計算 (T7) を持ち込まない。

        この節は DEG 表の avg_log2FC を pivot するだけで Z-score 変換をしない。
        見出しが「Z-score」と書いていたら、それ自体が嘘になる。
        """
        src = (APP / "callbacks/lite_view_callbacks.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_build_heatmap_section")
        doc = ast.get_docstring(fn) or ""
        assert "Z-score ヒートマップ" not in doc, (
            "Z-score 変換をしていないのに docstring がそう名乗っている")


class TestTheSettingsBlockIsNotDuplicated:
    """★ 「2 箇所に 2 行ずつ足す」をしていないこと。

    設定の読み出しと bundle 組み立ては `initialize_lite_view` と
    `_resolve_lite_data_for_target` に**完全に同じブロック**があった。
    ここへ足すのは T3 を自分の手で 1 つ増やすこと（ver51.9 B-8 / A-3 の形）。
    """

    def _funcs(self):
        tree = ast.parse(
            (APP / "callbacks/lite_view_callbacks.py").read_text(encoding="utf-8"))
        return {n.name: n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def test_the_helper_exists(self):
        assert "_read_lite_display_bundle" in self._funcs()

    @pytest.mark.parametrize("fn_name", [
        "initialize_lite_view", "_resolve_lite_data_for_target"])
    def test_both_paths_use_the_helper(self, fn_name):
        fn = self._funcs()[fn_name]
        calls = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "_read_lite_display_bundle" in calls, (
            f"{fn_name} が共通ヘルパを使っていない。"
            "設定項目が増えるたびに 2 箇所直すことになり、必ず片方を忘れる")

    def test_neither_path_reads_the_display_keys_directly(self):
        """個別に `settings.get("volcano_display")` を読み始めていないこと。"""
        funcs = self._funcs()
        for name in ("initialize_lite_view", "_resolve_lite_data_for_target"):
            for n in ast.walk(funcs[name]):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "get" and n.args
                        and isinstance(n.args[0], ast.Constant)
                        and str(n.args[0].value).endswith("_display")):
                    pytest.fail(
                        f"{name} が表示設定を直接読んでいる "
                        f"({n.args[0].value})。読み出し口が再び分かれている")


class TestOneSourceForDisplaySettings:
    """★ 画面 / PPTX / Lite が同じ読み出し口を使うこと。"""

    def test_pptx_delegates_to_the_shared_reader(self):
        src = (APP / "callbacks/interactive_pptx.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_display_settings")
        calls = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "read_display_settings" in calls, (
            "PPTX が共通の読み出し口を使っていない。"
            "同じ設定ファイルに 2 つの解釈が生まれる")

    def test_pptx_and_lite_agree_on_the_same_file(self):
        """同じ設定を渡したら同じ閾値が出ること（1 段の入れ子だけが違う）。"""
        from app.callbacks.interactive_pptx import _display_settings
        settings = {"volcano_display": {"fc_threshold": 0.9, "p_threshold": 3.0},
                    "heatmap_display": {"top_n": 7}}
        via_pptx = _display_settings({"interactive": settings})
        via_lite = read_display_settings(settings)
        assert via_pptx == via_lite

    def test_defaults_are_not_written_twice(self):
        """既定値が `PARAM_BOUNDS` 由来であること（数値の書き写しをしない）。"""
        from app.utils.validation import PARAM_BOUNDS
        assert DISPLAY_DEFAULTS["volcano_fc"] == PARAM_BOUNDS["volcano_fc_threshold"][2]
        assert DISPLAY_DEFAULTS["volcano_p"] == PARAM_BOUNDS["volcano_p_threshold"][2]
        assert DISPLAY_DEFAULTS["heatmap_top_n"] == PARAM_BOUNDS["heatmap_top_n"][2]
