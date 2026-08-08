"""残りの「黙って間違う」 (ver51.9 / C-3, C-4, C-6, C-7)。

  C-3 存在しないアコーディオン節 id を見ている
      `accordion_toggle_is_noop("acc_umap_integrated", ...)` — 実在するのは
      `acc_umap`。`is_open` が常に False、記録も常に False なので
      `prev == is_open` が**常に True** = 「変化なし」と判定され、
      アコーディオン操作では UMAP を**一度も再描画しない**。
      UMAP を畳んだ状態で改名や色変更をしてから開き直すと古いまま。
      Spatial は正しく `acc_spatial` を渡している。

  C-4 戻り値タプルの並びが 1 つずれている
      `return (no_update,) * 6 + (False,)` は 7 出力のうち
      `sap_skip_reset` に `no_update`、`sap_btn_wrapper.style` に `False`
      （style として不正）を渡す。skip フラグが降りないので、
      **次のサブプロジェクト切替でもリセットが skip され**、
      フォルダ入力が前のサブプロジェクトを指したまま残る。

  C-6 再現性の警告を生の dict のまま書き出す
      `- {'code': 'cache_only_embedding', 'params': {}}` と出る。
      `_WARNING_TEXTS` は既にあるのに散文レンダラでしか使われていない。

  C-7 画像カテゴリの判定順が誤り
      `cluster_heatmap.png` が "Spatial"、`Cluster_3_MSI.png` が "MSI" に入る。
"""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"


# ---------------------------------------------------------------------------
# C-3 アコーディオン節 id
# ---------------------------------------------------------------------------

class TestAccordionSectionIds:
    """★ 実在する item_id だけを渡すこと。"""

    @staticmethod
    def _declared_ids():
        src = (APP / "layouts" / "interactive_tab.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        ids = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "item_id" and isinstance(kw.value, ast.Constant):
                        ids.add(kw.value.value)
        return ids

    @staticmethod
    def _used_ids():
        used = []
        for name in ("interactive_umap.py", "interactive_spatial.py",
                     "interactive_deg.py", "interactive_cluster.py"):
            path = APP / "callbacks" / name
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "accordion_toggle_is_noop"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)):
                    used.append((name, node.lineno, node.args[0].value))
        return used

    def test_layout_declares_the_ids_we_expect(self):
        """前提の固定。"""
        ids = self._declared_ids()
        assert "acc_umap" in ids and "acc_spatial" in ids, ids

    def test_every_used_section_id_exists(self):
        """★ 存在しない id を渡している箇所が無いこと。"""
        declared = self._declared_ids()
        used = self._used_ids()
        assert used, "accordion_toggle_is_noop の呼び出しが見つからない"
        bad = [f"{f}:{ln} {sec!r}" for f, ln, sec in used if sec not in declared]
        assert not bad, (
            "存在しないアコーディオン節 id を見ている。is_open が常に False に"
            "なり『変化なし』と判定されるので、その節は再描画されない:\n  "
            + "\n  ".join(bad) + f"\n  実在する id: {sorted(declared)}")

    def test_toggle_is_detected_as_a_change(self):
        """★ 挙動でも見る: 閉→開 が「変化あり」と判定されること。"""
        pytest.importorskip("dash")
        from app.callbacks.interactive_callbacks import accordion_toggle_is_noop

        args = ("acc_umap", "sess-c3", "/rds/a.rds")
        # 1 回目（記録なし）は必ず描画
        assert accordion_toggle_is_noop(*args, [], "interactive_accordion") is False
        # 閉じたまま再発火 → 変化なし
        assert accordion_toggle_is_noop(*args, [], "interactive_accordion") is True
        # 開いた → 変化あり（描画する）
        assert accordion_toggle_is_noop(
            *args, ["acc_umap"], "interactive_accordion") is False


# ---------------------------------------------------------------------------
# C-4 戻り値の並び
# ---------------------------------------------------------------------------

class TestSubProjectSkipResetTuple:
    """★ skip 経路の戻り値が Output の並びと一致すること。"""

    def test_skip_path_returns_the_right_shape(self):
        pytest.importorskip("dash")
        from dash import no_update
        from app.callbacks.interactive_project import (
            set_interactive_folders_from_sub_project)

        out = set_interactive_folders_from_sub_project(
            "sub1", "proj1", skip_reset=True)
        assert len(out) == 7, out

        # Output 並び: result_folder, msi_folder, data_info, ms_instrument,
        #              viz_container.style, sap_skip_reset, sap_btn_wrapper.style
        assert out[5] is False, (
            "sap_skip_reset に False が入っていない。skip フラグが降りないので "
            "次のサブプロジェクト切替でもリセットが skip され、"
            f"フォルダ入力が前のサブプロジェクトを指したまま残る: {out}")
        assert out[6] is no_update or isinstance(out[6], dict), (
            f"style に False のような不正値を渡している: {out[6]!r}")


# ---------------------------------------------------------------------------
# C-6 警告の文言化
# ---------------------------------------------------------------------------

class TestWarningsAreRendered:
    """★ 生の dict を出さないこと。"""

    @staticmethod
    def _md(lang="ja"):
        from app.services.methods_text import render_methods
        return render_methods(
            {"warnings": [{"code": "cache_only_embedding", "params": {}}]},
            lang=lang)

    def test_no_raw_dict_in_output(self):
        md = self._md()
        assert "'code'" not in md and "{'" not in md, (
            f"警告が生の dict のまま出ている:\n{md}")

    def test_known_code_becomes_prose(self):
        md = self._md()
        assert "一時キャッシュ" in md, md

    def test_english_uses_the_english_text(self):
        md = self._md(lang="en")
        assert "temporary cache" in md, md

    def test_unknown_code_still_says_something(self):
        """★ 過剰修正の番人: 知らないコードを黙って捨てない。"""
        from app.services.methods_text import render_methods
        md = render_methods({"warnings": [{"code": "brand_new_code"}]}, lang="ja")
        assert "brand_new_code" in md, (
            f"未知の警告コードが出力から消えた（黙って捨てている）:\n{md}")

    def test_plain_string_warnings_still_work(self):
        """ver47.0 の analysis_conditions.json は素の文字列を持つ。"""
        from app.services.methods_text import render_methods
        md = render_methods({"warnings": ["古い形式の警告"]}, lang="ja")
        assert "古い形式の警告" in md, md


# ---------------------------------------------------------------------------
# C-7 画像カテゴリの判定順
# ---------------------------------------------------------------------------

class TestImageCategories:
    @pytest.mark.parametrize("filename,expected", [
        ("cluster_heatmap.png", "Heatmap"),
        ("Cluster_3_MSI.png", "Spatial"),
        ("umap_integrated.png", "UMAP"),
        ("volcano_cluster_0.png", "Volcano"),
        ("Cluster_Top5_MSI_0.png", "MSI"),
        ("spatial_S1.png", "Spatial"),
        ("tic_S1.png", "TIC"),
        ("filtering_summary.png", "Filtering"),
        ("something_else.png", "Other"),
    ])
    def test_category(self, filename, expected):
        from app.services.results_viewer import categorize_image
        assert categorize_image(filename) == expected
