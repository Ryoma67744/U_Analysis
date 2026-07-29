"""Methods 文（日英）生成の単体テスト。

最重要は「値を捏造しない」こと。欠損は「未記録 / not recorded」と出て、
末尾の未記録一覧に載らなければならない。
"""
from app.services import methods_text as mt

FULL = {
    "generated_at": "2026-07-29T09:00:00",
    "generated_by": "tanaka",
    "result_dir": "/out/run1",
    "integration_method": "Harmony",
    "analysis": {
        "analysis_type": "tims_v8",
        "data_folder": "/data/TIMS",
        "started_at": "2026-07-28T10:00:00",
        "ended_at": "2026-07-28T11:00:00",
        "preprocessing": {"norm_mode": "log1p", "input_normalized": False,
                          "batch_correction": "harmony",
                          "calibration_enable": True,
                          "calibration_regression_mode": "poly3"},
        "umap": {"n_neighbors": 30, "min_dist": 0.3, "metric": "cosine",
                 "dims": 30, "seed": 42},
        "clustering": {"algorithm": "leiden", "resolution": 0.8, "k_param": 20},
        "annotation": {"ion_mode": "Positive", "tolerance_mz": 0.01,
                       "adduct_filter": ["+H", "+Na"],
                       "annotation_csv": "/db/metabolites.csv"},
        "thresholds": {"p": 0.05, "logfc": 0.25},
        "sample_selection": {"sample_names": ["S1", "S2"], "roi_filter": None,
                             "annotation_filter": None, "tims_scenario": "serial_section"},
        "mz_align_ppm": 5,
    },
    "software": {"app_version": "2026-07-28_ver47.0", "r_version": "4.3",
                 "packages": {"r": {"Seurat": "5.0.1"},
                              "python": {"python": "3.11.5", "numpy": "1.26"}}},
    "pipeline": {"template_path": "/app/Script/TIMS/tmpl_v6.R",
                 "runtime_script": "/out/run1/log/v8_runtime_20260728.R",
                 "runtime_script_sha256": "deadbeef"},
    "interactive": {
        "volcano_display": {"fc_threshold": 0.5, "p_threshold": 1.3},
        "heatmap_display": {"top_n": 5, "scale": "zscore"},
        "feature_display": {"colorscale": "Plasma", "intensity_min": 0,
                            "intensity_max": 100},
        "onthefly_de": {"mode": "global", "fc": 0.5, "p": 1.3},
        "selection_groups": [{"name": "Tumor", "n_cells": 1200}],
    },
    "onthefly_de_fixed_params": dict(mt_fixed := {
        "test": "wilcox", "min_pct": 0.05,
        "logfc_threshold": 0.25, "p_adjust_method": "BH",
    }),
    "warnings": [],
    "_missing": [],
}

EMPTY = {"analysis": {}, "software": {}, "pipeline": {}, "interactive": {},
         "onthefly_de_fixed_params": dict(mt_fixed),
         "_missing": ["analysis.umap.n_neighbors", "analysis.clustering.algorithm"]}


def test_renders_japanese():
    md = mt.render_methods(FULL, "ja")
    assert md.startswith("# 解析条件（Methods 下書き）")
    assert "1. 解析の概要" in md
    assert "| UMAP n_neighbors | 30 |" in md
    assert "| Random seed | 42 |" not in md  # 日本語ラベルであること
    assert "| 乱数 seed | 42 |" in md


def test_renders_english():
    md = mt.render_methods(FULL, "en")
    assert md.startswith("# Analysis conditions (Methods draft)")
    assert "1. Overview" in md
    assert "| UMAP n_neighbors | 30 |" in md
    assert "| Random seed | 42 |" in md


def test_both_languages_share_the_same_values():
    ja = mt.render_methods(FULL, "ja")
    en = mt.render_methods(FULL, "en")
    for token in ("leiden", "0.8", "cosine", "2026-07-28_ver47.0", "deadbeef"):
        assert token in ja and token in en


def test_missing_values_are_marked_not_invented():
    """欠損時にもっともらしい既定値を書かない。

    on-the-fly DE の固定値（wilcox / 0.05 / 0.25）はコード上の実測値なので
    出てよい。捏造してはいけないのは「ユーザーが選んだはずの設定」のほう。
    """
    ja = mt.render_methods(EMPTY, "ja")
    en = mt.render_methods(EMPTY, "en")
    assert "未記録" in ja
    assert "not recorded" in en
    for row in ("| UMAP n_neighbors | 未記録 |",
                "| UMAP min_dist | 未記録 |",
                "| UMAP metric | 未記録 |",
                "| 乱数 seed | 未記録 |",
                "| クラスタリング手法 | 未記録 |",
                "| 解像度 (resolution) | 未記録 |",
                "| p 値閾値（統計判定） | 未記録 |",
                "| log2FC 閾値（統計判定） | 未記録 |",
                "| 正規化モード | 未記録 |"):
        assert row in ja


def test_missing_section_lists_each_missing_path():
    ja = mt.render_methods(EMPTY, "ja")
    assert "⚠ 未記録の項目" in ja
    assert "`analysis.umap.n_neighbors`" in ja
    assert "`analysis.clustering.algorithm`" in ja


def test_no_missing_section_says_so():
    ja = mt.render_methods(FULL, "ja")
    assert "必須項目はすべて記録されています。" in ja


def test_volcano_thresholds_flagged_as_display_only():
    """Methods の誤記が最も起きやすい点なので、両言語で明示されること。"""
    ja = mt.render_methods(FULL, "ja")
    en = mt.render_methods(FULL, "en")
    assert "検定そのものには使われていません" in ja
    assert "NOT used for the statistical test" in en
    # 統計的閾値のほうがラベル付きで出ている
    assert "p 値閾値（統計判定）" in ja
    assert "p-value threshold (statistical)" in en


def test_hidden_onthefly_de_params_are_surfaced():
    """GUI に出ていない固定値（wilcox / min.pct / logfc）を必ず書く。"""
    for lang in ("ja", "en"):
        md = mt.render_methods(FULL, lang)
        assert "wilcox" in md
        assert "0.05" in md
        assert "0.25" in md
        assert "BH" in md


def test_pixel_level_caveat_included():
    ja = mt.render_methods(FULL, "ja")
    en = mt.render_methods(FULL, "en")
    assert "空間自己相関" in ja
    assert "spatial autocorrelation not modeled" in en


def test_warnings_section_rendered():
    c = dict(FULL)
    c["warnings"] = ["キャッシュにしか存在しません"]
    md = mt.render_methods(c, "ja")
    assert "⚠ 再現性に関する警告" in md
    assert "キャッシュにしか存在しません" in md


def test_warnings_section_absent_when_empty():
    assert "⚠ 再現性に関する警告" not in mt.render_methods(FULL, "ja")


def test_interactive_settings_are_reported():
    md = mt.render_methods(FULL, "ja")
    assert "Volcano" in md
    assert "zscore" in md          # heatmap_scale はデータ変換なので必須
    assert "Plasma" in md          # colorscale
    assert "Tumor" in md           # selection group 名


def test_lang_defaults_to_japanese_on_unknown():
    assert mt.render_methods(FULL, "fr").startswith("# 解析条件")
    assert mt.render_methods(FULL, None).startswith("# 解析条件")


def test_render_conditions_rows_for_pptx():
    rows = mt.render_conditions_rows(FULL, "ja")
    assert all(len(r) == 2 for r in rows)
    labels = [r[0] for r in rows]
    assert "統合手法" in labels
    assert "統計閾値 p / log2FC" in labels
    assert "Volcano 閾値（表示用）" in labels
    values = dict(rows)
    assert values["統合手法"] == "Harmony"
    assert values["未記録項目数"] == "0"


def test_render_conditions_rows_handles_missing():
    rows = mt.render_conditions_rows(EMPTY, "en")
    values = dict(rows)
    assert values["Integration method"] == "not recorded"
    assert values["Not-recorded items"] == "2"


def test_bool_values_are_not_dropped():
    """False は「値なし」ではない。未記録と混同しないこと。"""
    md = mt.render_methods(FULL, "en")
    assert "| Input already normalized | false |" in md
