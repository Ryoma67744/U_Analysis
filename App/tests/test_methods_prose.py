"""論文用 Methods 平文のテスト。

守りたいのは 1 点に尽きる: **本文が事実でないことを書かないこと。**
  - 記録が無い項目を既定値で埋めない（赤スロットになる）
  - 無効な設定を「実施した」と書かない（キャリブレーション等）
  - 表示用の閾値を統計的閾値として書かない
  - 実行していない解析の段落を出さない
"""
import json

from app.services import methods_text as mt
from app.services import provenance as pv

FULL = {
    "integration_method": "Harmony",
    "analysis": {
        "analysis_type": "tims_v8",
        "preprocessing": {"norm_mode": "log1p", "input_normalized": False,
                          "batch_correction": "sample",
                          "calibration_enable": True,
                          "calibration_regression_mode": "poly3",
                          "calibration_r_squared": 0.999},
        "umap": {"n_neighbors": 30, "min_dist": 0.3, "metric": "cosine",
                 "dims": 30, "seed": 42},
        "clustering": {"algorithm": "leiden", "resolution": 0.8, "k_param": 20,
                       "neighbor_metric": "euclidean"},
        "annotation": {"ion_mode": "Positive", "tolerance_mz": 0.01,
                       "adduct_filter": ["+H", "+Na"],
                       "annotation_csv": "/db/4500_metabolites.csv"},
        "thresholds": {"p": 0.05, "logfc": 0.25},
        "sample_selection": {"sample_names": ["S1", "S2"]},
        "mz_align_ppm": 5,
        "de": {"min_pct": 0.05},
    },
    "software": {"app_version": "2026-07-29_ver48.0", "r_version": "4.3.2",
                 "packages": {"r": {"Seurat": "5.0.1", "harmony": "1.2.0"}}},
    "pipeline": {"template_path": "/app/Script/TIMS/v6.R"},
    "interactive": {},
    "onthefly_de_fixed_params": {"test": "wilcox", "min_pct": 0.05,
                                 "logfc_threshold": 0.25, "p_adjust_method": "BH"},
    "batch_de_fixed_params": {"test": "wilcox", "min_pct": 0.05,
                              "p_adjust_method": "BH", "only_positive": False},
    "warnings": [],
    "_missing": [],
    "_sources": {},
}

EMPTY = {"analysis": {}, "software": {}, "pipeline": {}, "interactive": {},
         "onthefly_de_fixed_params": dict(FULL["onthefly_de_fixed_params"]),
         "batch_de_fixed_params": dict(FULL["batch_de_fixed_params"]),
         "_missing": [], "_sources": {}}


def _flat(conditions, lang="ja"):
    return mt.render_methods_prose(conditions, lang)


def _segs(conditions, lang="ja"):
    out = []
    for sec in mt.build_methods_prose(conditions, lang):
        for para in sec["paragraphs"]:
            out.extend(para)
    return out


def _fills(conditions, lang="ja"):
    return [s["v"] for s in _segs(conditions, lang) if s["t"] == mt.SEG_FILL]


def _headings(conditions, lang="ja"):
    return [s["heading"] for s in mt.build_methods_prose(conditions, lang)]


# ---------------------------------------------------------------------------
# 値を捏造しない
# ---------------------------------------------------------------------------

def test_empty_conditions_invent_no_values():
    """条件が空でも、それらしい既定値を書かない。"""
    ja = _flat(EMPTY, "ja")
    for bogus in ("30", "0.3", "cosine", "leiden", "log1p", "Harmony"):
        assert bogus not in ja, f"未記録なのに {bogus} が本文に出ている"


def test_empty_conditions_emit_no_none_or_placeholder_artifacts():
    for lang in ("ja", "en"):
        text = _flat(EMPTY, lang)
        for bad in ("None", "null", "nan", "{}", "[]", "()", "（）",
                    "未記録", "not recorded"):
            assert bad not in text, f"{lang}: 「{bad}」が本文に出ている"


def test_missing_values_become_fill_slots():
    fills = _fills(EMPTY, "ja")
    joined = " ".join(fills)
    for expect in ("UMAP の n_neighbors", "UMAP の min_dist", "UMAP の距離尺度",
                   "乱数シード", "クラスタリング手法", "クラスタリングの resolution",
                   "正規化の方法", "有意水準 (p)", "log2 fold-change 閾値"):
        assert expect in joined, f"{expect} が赤スロットになっていない"


def test_fill_markers_have_a_findable_shape():
    """Word に貼ったあと Ctrl+F で探せる形であること。"""
    for label in _fills(EMPTY, "ja"):
        assert label.startswith("〔要記入: ") and label.endswith("〕")
    for label in _fills(EMPTY, "en"):
        assert label.startswith("[TO BE FILLED: ") and label.endswith("]")


def test_fill_labels_are_human_readable_not_dotted_paths():
    for label in _fills(EMPTY, "ja") + _fills(EMPTY, "en"):
        assert "analysis." not in label
        assert "_sources" not in label


def test_recorded_values_are_plain_text_segments():
    segs = _segs(FULL, "ja")
    values = {s["v"] for s in segs if s["t"] == mt.SEG_TEXT}
    assert "30" in values and "cosine" in values and "leiden" in values
    assert not _fills(FULL, "ja")


def test_recovered_values_are_marked_separately(tmp_path):
    """実行スクリプトから復元した値は青（recovered）になる。"""
    out = tmp_path / "run"
    (out / "log").mkdir(parents=True)
    (out / "RDS_Files").mkdir()
    (out / "analysis_params.json").write_text(json.dumps({"analysis_type": "tims_v8"}),
                                              encoding="utf-8")
    (out / "log" / "v8_runtime_1.R").write_text(
        'UMAP_N_NEIGHBORS <- 30L\nUMAP_METRIC <- "cosine"\n', encoding="utf-8")
    rds = out / "RDS_Files" / "a.rds"
    rds.write_bytes(b"x")
    c = pv.collect_conditions(rds_path=str(rds), integration_method="Harmony")
    kinds = {s["v"]: s["t"] for s in _segs(c, "ja")}
    assert kinds.get("30") == mt.SEG_RECOVERED
    assert kinds.get("cosine") == mt.SEG_RECOVERED


# ---------------------------------------------------------------------------
# 事実でないことを書かない（条件分岐）
# ---------------------------------------------------------------------------

def test_disabled_calibration_never_mentions_regression_mode():
    """本番で観測したケース: 無効なのに回帰モードが残っている。"""
    c = json.loads(json.dumps(FULL))
    c["analysis"]["preprocessing"]["calibration_enable"] = False
    for lang in ("ja", "en"):
        text = _flat(c, lang)
        assert "poly3" not in text
        assert "0.999" not in text
    assert "m/z キャリブレーション" not in _headings(c, "ja")


def test_enabled_calibration_does_mention_it():
    assert "m/z キャリブレーション" in _headings(FULL, "ja")
    assert "poly3" in _flat(FULL, "ja")


def test_display_thresholds_never_stated_as_statistical():
    """Volcano の閾値しか無いとき、それを統計閾値として書かない。"""
    c = json.loads(json.dumps(FULL))
    c["analysis"]["thresholds"] = {}
    c["interactive"]["volcano_display"] = {"fc_threshold": 0.5, "p_threshold": 1.3}
    ja = _flat(c, "ja")
    assert "有意水準 (p)" in " ".join(_fills(c, "ja"))
    assert "作図上の表示条件であって統計的な有意判定には用いていない" in ja


def test_onthefly_section_absent_when_not_run():
    """固定値の dict は常にあるが、それは実行した証拠ではない。"""
    assert FULL["onthefly_de_fixed_params"]
    assert "選択領域の差次発現解析" not in _headings(FULL, "ja")
    assert "投げ縄" not in _flat(FULL, "ja")


def test_onthefly_section_present_when_run():
    c = json.loads(json.dumps(FULL))
    c["interactive"]["onthefly_de"] = {"mode": "local", "target_clusters": ["3"]}
    assert "選択領域の差次発現解析" in _headings(c, "ja")
    assert "wilcox" in _flat(c, "ja")


def test_roi_section_absent_without_export():
    assert "領域別解析と経路解析用出力" not in _headings(FULL, "ja")
    assert "MetaboAnalyst" not in _flat(FULL, "ja")


def test_roi_section_reports_intensity_representation():
    c = json.loads(json.dumps(FULL))
    c["interactive"]["hne_export_options"] = {"intensity_repr": "linear",
                                              "unit": "compound",
                                              "include_qea": True}
    ja = _flat(c, "ja")
    assert "対数変換を戻した線形強度" in ja
    assert "MetaboAnalyst" in ja


def test_reanalysis_uses_different_wording():
    c = json.loads(json.dumps(FULL))
    c["analysis"]["analysis_type"] = "desi_cluster_filter"
    c["analysis"]["filter_mode"] = "exclude"
    c["analysis"]["target_clusters"] = [3, 5]
    ja = _flat(c, "ja")
    assert "再解析" in ja
    assert "質量分析イメージング (MSI) により取得したデータ" not in ja


def test_dbscan_does_not_claim_resolution():
    c = json.loads(json.dumps(FULL))
    c["analysis"]["clustering"] = {"algorithm": "dbscan", "resolution": 0.8}
    ja = _flat(c, "ja")
    assert "DBSCAN" in ja
    assert "resolution" not in ja and "0.8" not in ja


def test_reduction_only_stage_suppresses_clustering_and_de():
    c = json.loads(json.dumps(FULL))
    c["pipeline"]["pipeline_stage"] = "reduction_only"
    heads = _headings(c, "ja")
    assert "クラスタリング" not in heads
    assert "差次発現解析" not in heads
    assert "別の実行で行っている" in _flat(c, "ja")


def test_pca_is_not_described_as_corrected():
    c = json.loads(json.dumps(FULL))
    c["integration_method"] = "PCA"
    ja = _flat(c, "ja")
    assert "バッチ補正は行わず" in ja
    assert "により補正した" not in ja


def test_zero_mz_align_is_a_recorded_value_not_a_gap():
    c = json.loads(json.dumps(FULL))
    c["analysis"]["mz_align_ppm"] = 0
    ja = _flat(c, "ja")
    assert "アライメントは行わなかった" in ja
    assert "m/z アライメント幅" not in " ".join(_fills(c, "ja"))


def test_input_already_normalized_does_not_claim_renormalization():
    c = json.loads(json.dumps(FULL))
    c["analysis"]["preprocessing"]["input_normalized"] = True
    ja = _flat(c, "ja")
    assert "追加の正規化を行わなかった" in ja
    assert "log1p により正規化した" not in ja


# ---------------------------------------------------------------------------
# 隠しパラメータを必ず書く
# ---------------------------------------------------------------------------

def test_batch_de_fixed_params_are_surfaced():
    """GUI に出ていない wilcox / min.pct / BH は Methods に必須。"""
    for lang in ("ja", "en"):
        text = _flat(FULL, lang)
        assert "wilcox" in text
        assert "BH" in text
        assert "0.05" in text


def test_de_fixed_params_come_from_conditions_not_hardcoded():
    c = json.loads(json.dumps(FULL))
    c["batch_de_fixed_params"]["test"] = "roc"
    c["analysis"]["de"]["min_pct"] = 0.11
    ja = _flat(c, "ja")
    assert "roc" in ja and "0.11" in ja


def test_neighbour_metric_is_not_confused_with_umap_metric():
    """UMAP は cosine、近傍探索は euclidean。取り違えると誤記になる。"""
    ja = _flat(FULL, "ja")
    assert "距離尺度 = cosine" in ja
    assert "近傍探索の距離尺度は euclidean" in ja


def test_pixel_level_caveat_quoted_verbatim():
    from app.services import caveats
    assert caveats.PIXEL_LEVEL_NOTE_JA in _flat(FULL, "ja")
    assert caveats.PIXEL_LEVEL_NOTE_EN in _flat(FULL, "en")


# ---------------------------------------------------------------------------
# 日英の同期
# ---------------------------------------------------------------------------

def test_same_sections_in_both_languages():
    for c in (FULL, EMPTY):
        assert len(mt.build_methods_prose(c, "ja")) == len(mt.build_methods_prose(c, "en"))


def test_same_number_of_fill_slots_in_both_languages():
    for c in (FULL, EMPTY):
        assert len(_fills(c, "ja")) == len(_fills(c, "en"))


def test_no_japanese_boilerplate_in_english_output():
    en = _flat(FULL, "en")
    assert "要記入" not in en
    assert "解析" not in en.replace(FULL["software"]["app_version"], "")


def test_shared_values_appear_in_both_languages():
    ja, en = _flat(FULL, "ja"), _flat(FULL, "en")
    for token in ("leiden", "0.8", "cosine", "42", "4.3.2", "Seurat v5.0.1"):
        assert token in ja and token in en


def test_warnings_are_localized_by_code():
    c = json.loads(json.dumps(FULL))
    c["warnings"] = [{"code": "cache_only_embedding", "params": {}}]
    assert "一時キャッシュ" in _flat(c, "ja")
    assert "temporary cache" in _flat(c, "en")


def test_legacy_string_warnings_still_render():
    """ver47.0 が書いた analysis_conditions.json は素の文字列を持つ。"""
    c = json.loads(json.dumps(FULL))
    c["warnings"] = ["古い形式の警告文"]
    assert "古い形式の警告文" in _flat(c, "ja")


# ---------------------------------------------------------------------------
# バックエンド
# ---------------------------------------------------------------------------

def test_all_backends_emit_the_same_text():
    import re

    sections = mt.build_methods_prose(FULL, "ja")
    plain = mt.prose_to_text(sections, "ja")
    html = mt.prose_to_html(sections, "ja")
    stripped = re.sub(r"<[^>]+>", "", html)
    for para in sections[0]["paragraphs"]:
        text = "".join(s["v"] for s in para)
        assert text in plain
        assert text in stripped


def test_html_backend_escapes_user_supplied_values():
    """クラスタ名などはユーザー入力。HTML 注入経路にしない。"""
    c = json.loads(json.dumps(FULL))
    c["analysis"]["sample_selection"]["sample_names"] = ["<script>alert(1)</script>"]
    html = mt.render_methods_prose_html(c, "ja")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_html_backend_colours_fills_and_recovered():
    html = mt.render_methods_prose_html(EMPTY, "ja")
    assert "color:#d32f2f" in html          # 赤 = 未記録


def test_dash_backend_returns_components_not_html_strings():
    from dash import html as dash_html

    div = mt.prose_to_dash(mt.build_methods_prose(EMPTY, "ja"), "ja")
    assert isinstance(div, dash_html.Div)
    # 赤スロットは Span + CSS クラスで表現される（生 HTML を作らない）
    found = []

    def walk(node):
        children = getattr(node, "children", None)
        if isinstance(children, (list, tuple)):
            for ch in children:
                walk(ch)
        elif children is not None:
            walk(children)
        if getattr(node, "className", None) == "methods-fill":
            found.append(node)

    walk(div)
    assert found, "赤スロットの Span が無い"


def test_todo_section_lists_every_fill():
    sections = mt.build_methods_prose(EMPTY, "ja")
    todo = sections[-1]
    assert todo["heading"] == "補うべき項目"
    listed = {s["v"] for para in todo["paragraphs"] for s in para
              if s["t"] == mt.SEG_FILL}
    body = {s["v"] for sec in sections[:-1] for para in sec["paragraphs"]
            for s in para if s["t"] == mt.SEG_FILL}
    assert body and body.issubset(listed)


def test_todo_section_says_so_when_nothing_missing():
    sections = mt.build_methods_prose(FULL, "ja")
    assert "赤字の箇所はありません" in mt.prose_to_text(sections[-1:], "ja")
