# =============================================================================
# MSI Analysis Application - Methods 文の自動生成（日本語 / English）
# =============================================================================
# provenance.collect_conditions() が集めた条件 dict から、論文の Methods に
# そのまま貼れる下書きを生成する。日英どちらも同じ conditions から作るので、
# 2 言語間で値がずれることはない。
#
# **絶対に守る原則: 値を捏造しない。**
#   取れなかった項目は「未記録」/ "not recorded" と明示し、末尾に一覧を出す。
#   もっともらしい既定値で埋めてしまうと、論文に嘘が載る。
#
# 意図的に強調している 2 点（Methods の誤記が最も起きやすいところ）:
#   1. Volcano / Heatmap の閾値は「表示用」であって統計的閾値ではない。
#      実際に検定へ渡ったのは解析設定タブの p_thresh / logfc_thresh。
#   2. on-the-fly DE は GUI に出ていない固定値（Wilcoxon, min.pct=0.05,
#      logfc.threshold=0.25, BH）で走っている。
#
# 依存は標準ライブラリ + app.services.caveats のみ。
# =============================================================================

from __future__ import annotations

from typing import Optional

from app.services import caveats

METHODS_VERSION = "1"

_NOT_RECORDED = {"ja": "未記録", "en": "not recorded"}

_L = {
    "ja": {
        "title": "解析条件（Methods 下書き）",
        "generated": "生成日時",
        "operator": "解析者",
        "source": "由来",
        "h_overview": "1. 解析の概要",
        "h_pre": "2. 前処理と正規化",
        "h_annot": "3. m/z キャリブレーションとアノテーション",
        "h_embed": "4. 次元圧縮とクラスタリング",
        "h_de": "5. 差次発現解析（DEG）",
        "h_interactive": "6. 可視化と対話的解析の設定",
        "h_soft": "7. ソフトウェアとバージョン",
        "h_caveat": "8. 統計解釈上の注意",
        "h_missing": "⚠ 未記録の項目（手で埋めてください）",
        "h_warn": "⚠ 再現性に関する警告",
        "no_missing": "必須項目はすべて記録されています。",
        "note_volcano": (
            "**重要**: Volcano プロットおよび Heatmap の閾値は表示・ラベル付けのための"
            "ものであり、検定そのものには使われていません。統計的な有意判定に用いた"
            "閾値は上記の p 値閾値・log2FC 閾値です。Methods にはこちらを記載してください。"
        ),
        "note_onthefly": (
            "対話画面の on-the-fly DE（投げ縄選択に対する差次発現解析）は、"
            "GUI に表示されない以下の固定パラメータで実行されています。"
        ),
        "footer": (
            "この下書きは解析レシート（receipt.json）と対話画面の設定から自動生成されました。"
            "投稿前に必ず内容を確認してください。"
        ),
    },
    "en": {
        "title": "Analysis conditions (Methods draft)",
        "generated": "Generated",
        "operator": "Operator",
        "source": "Source",
        "h_overview": "1. Overview",
        "h_pre": "2. Preprocessing and normalization",
        "h_annot": "3. m/z calibration and annotation",
        "h_embed": "4. Dimensionality reduction and clustering",
        "h_de": "5. Differential expression analysis",
        "h_interactive": "6. Visualization and interactive analysis settings",
        "h_soft": "7. Software and versions",
        "h_caveat": "8. Statistical caveat",
        "h_missing": "⚠ Not recorded (fill in manually)",
        "h_warn": "⚠ Reproducibility warnings",
        "no_missing": "All required items were recorded.",
        "note_volcano": (
            "**Important**: the volcano-plot and heatmap thresholds are display/labelling "
            "settings and were NOT used for the statistical test. The thresholds actually "
            "applied are the p-value and log2FC thresholds listed above; report those in "
            "the Methods section."
        ),
        "note_onthefly": (
            "The on-the-fly differential expression analysis in the interactive view "
            "(lasso-selected pixels) runs with the following fixed parameters, which are "
            "not exposed in the GUI."
        ),
        "footer": (
            "This draft was generated automatically from the analysis receipt "
            "(receipt.json) and the interactive-view settings. Review it before submission."
        ),
    },
}


def _norm_lang(lang: Optional[str]) -> str:
    return "en" if str(lang or "ja").lower().startswith("en") else "ja"


def _v(value, lang: str) -> str:
    """値を文字列化。欠損は捏造せず「未記録 / not recorded」を返す。"""
    if value is None or value == "" or value == [] or value == {}:
        return _NOT_RECORDED[lang]
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(x) for x in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items())
    return str(value)


def _kv_table(rows, lang: str) -> str:
    """[(label, value)] を Markdown テーブルにする。"""
    head = "| 項目 | 値 |\n|---|---|" if lang == "ja" else "| Item | Value |\n|---|---|"
    body = "\n".join(f"| {label} | {_v(value, lang)} |" for label, value in rows)
    return f"{head}\n{body}\n"


def _get(d, dotted: str, default=None):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
    return cur if cur is not None else default


def render_methods(conditions: dict, lang: str = "ja") -> str:
    """conditions dict から Methods 下書き（Markdown）を返す。"""
    lang = _norm_lang(lang)
    t = _L[lang]
    c = conditions or {}
    out = [f"# {t['title']}", ""]

    out.append(f"- **{t['generated']}**: {_v(c.get('generated_at'), lang)}")
    out.append(f"- **{t['operator']}**: {_v(c.get('generated_by'), lang)}")
    out.append(f"- **{t['source']}**: {_v(c.get('result_dir'), lang)}")
    out.append("")

    # --- 1. 概要 ---
    out.append(f"## {t['h_overview']}")
    out.append("")
    out.append(_kv_table([
        ("analysis_type" if lang == "en" else "解析タイプ",
         _get(c, "analysis.analysis_type")),
        ("Integration method" if lang == "en" else "統合手法",
         c.get("integration_method")),
        ("Data folder" if lang == "en" else "データフォルダ",
         _get(c, "analysis.data_folder")),
        ("Samples" if lang == "en" else "対象サンプル",
         _get(c, "analysis.sample_selection.sample_names")),
        ("ROI filter" if lang == "en" else "ROI 絞り込み",
         _get(c, "analysis.sample_selection.roi_filter")),
        ("Section filter" if lang == "en" else "セクション絞り込み",
         _get(c, "analysis.sample_selection.annotation_filter")),
        ("Scenario" if lang == "en" else "解析シナリオ",
         _get(c, "analysis.sample_selection.tims_scenario")),
        ("Started" if lang == "en" else "解析開始",
         _get(c, "analysis.started_at")),
        ("Finished" if lang == "en" else "解析終了",
         _get(c, "analysis.ended_at")),
    ], lang))

    # --- 2. 前処理 ---
    out.append(f"## {t['h_pre']}")
    out.append("")
    out.append(_kv_table([
        ("Input already normalized" if lang == "en" else "入力が正規化済みか",
         _get(c, "analysis.preprocessing.input_normalized")),
        ("Normalization mode" if lang == "en" else "正規化モード",
         _get(c, "analysis.preprocessing.norm_mode")),
        ("Batch correction" if lang == "en" else "バッチ補正",
         _get(c, "analysis.preprocessing.batch_correction")),
        ("m/z alignment (ppm)" if lang == "en" else "m/z アライメント (ppm)",
         _get(c, "analysis.mz_align_ppm")),
    ], lang))

    # --- 3. キャリブレーション / アノテーション ---
    out.append(f"## {t['h_annot']}")
    out.append("")
    out.append(_kv_table([
        ("Calibration enabled" if lang == "en" else "キャリブレーション有効",
         _get(c, "analysis.preprocessing.calibration_enable")),
        ("Regression mode" if lang == "en" else "回帰モード",
         _get(c, "analysis.preprocessing.calibration_regression_mode")),
        ("Ion mode" if lang == "en" else "イオンモード",
         _get(c, "analysis.annotation.ion_mode")),
        ("Tolerance (m/z)" if lang == "en" else "許容誤差 (m/z)",
         _get(c, "analysis.annotation.tolerance_mz")),
        ("Adduct filter" if lang == "en" else "アダクトフィルタ",
         _get(c, "analysis.annotation.adduct_filter")),
        ("Annotation database" if lang == "en" else "アノテーション DB",
         _get(c, "analysis.annotation.annotation_csv")),
        ("Annotation sources" if lang == "en" else "アノテーション由来",
         _get(c, "analysis.annotation.sources")),
    ], lang))
    reann = _get(c, "interactive.reannotation")
    if reann:
        out.append(
            ("再アノテーションが対話画面で実行されました: " if lang == "ja"
             else "Re-annotation was performed in the interactive view: ")
            + _v(reann, lang)
        )
        out.append("")

    # --- 4. 埋め込みとクラスタリング ---
    out.append(f"## {t['h_embed']}")
    out.append("")
    out.append(_kv_table([
        ("UMAP n_neighbors", _get(c, "analysis.umap.n_neighbors")),
        ("UMAP min_dist", _get(c, "analysis.umap.min_dist")),
        ("UMAP metric", _get(c, "analysis.umap.metric")),
        ("UMAP dims", _get(c, "analysis.umap.dims")),
        ("Random seed" if lang == "en" else "乱数 seed", _get(c, "analysis.umap.seed")),
        ("Clustering algorithm" if lang == "en" else "クラスタリング手法",
         _get(c, "analysis.clustering.algorithm")),
        ("Resolution" if lang == "en" else "解像度 (resolution)",
         _get(c, "analysis.clustering.resolution")),
        ("k parameter" if lang == "en" else "k パラメータ",
         _get(c, "analysis.clustering.k_param")),
        ("Cluster filter mode" if lang == "en" else "クラスタ絞り込みモード",
         _get(c, "analysis.filter_mode")),
        ("Target clusters" if lang == "en" else "対象クラスタ",
         _get(c, "analysis.target_clusters")),
    ], lang))

    # --- 5. DEG ---
    out.append(f"## {t['h_de']}")
    out.append("")
    out.append(_kv_table([
        ("p-value threshold (statistical)" if lang == "en" else "p 値閾値（統計判定）",
         _get(c, "analysis.thresholds.p")),
        ("log2FC threshold (statistical)" if lang == "en" else "log2FC 閾値（統計判定）",
         _get(c, "analysis.thresholds.logfc")),
    ], lang))
    out.append(t["note_volcano"])
    out.append("")
    out.append(t["note_onthefly"])
    out.append("")
    fixed = c.get("onthefly_de_fixed_params") or {}
    out.append(_kv_table([(k, v) for k, v in fixed.items()], lang))
    onthefly = _get(c, "interactive.onthefly_de")
    if onthefly:
        out.append(("実行時の選択条件: " if lang == "ja"
                    else "User-selected settings at run time: ") + _v(onthefly, lang))
        out.append("")

    # --- 6. 対話画面の設定 ---
    out.append(f"## {t['h_interactive']}")
    out.append("")
    inter = c.get("interactive") or {}
    display_keys = [
        ("volcano_display", "Volcano"),
        ("heatmap_display", "Heatmap"),
        ("feature_display", "Feature plot"),
        ("umap_display", "UMAP display"),
        ("umap_view", "UMAP view"),
        ("spatial_display", "Spatial display"),
        ("spatial_view", "Spatial view"),
        ("hne_export_options", "H&E export"),
        ("export_options", "Export options"),
        ("cluster_name_map", "Cluster renaming"),
        ("custom_color_map", "Cluster colors"),
        ("sample_name_map", "Sample renaming"),
        ("selection_groups", "Selection groups"),
        ("feature_lists", "Feature lists"),
    ]
    rows = []
    for key, label in display_keys:
        if key in inter:
            rows.append((label, inter[key]))
    # cluster_name_map は手法別キー（cluster_name_map::Harmony）でも保存される
    for key in sorted(k for k in inter if k.startswith("cluster_name_map::")):
        rows.append((f"Cluster renaming ({key.split('::', 1)[1]})", inter[key]))
    if rows:
        out.append(_kv_table(rows, lang))
    else:
        out.append(_NOT_RECORDED[lang])
        out.append("")

    # --- 7. ソフトウェア ---
    out.append(f"## {t['h_soft']}")
    out.append("")
    pkgs = _get(c, "software.packages") or {}
    soft_rows = [
        ("Application" if lang == "en" else "アプリ", _get(c, "software.app_version")),
        ("R", _get(c, "software.r_version")),
        ("Python", (pkgs.get("python") or {}).get("python")),
        ("Analysis script" if lang == "en" else "解析スクリプト",
         _get(c, "pipeline.template_path")),
        ("Executed script" if lang == "en" else "実行スクリプト",
         _get(c, "pipeline.runtime_script")),
        ("Executed script SHA-256" if lang == "en" else "実行スクリプト SHA-256",
         _get(c, "pipeline.runtime_script_sha256")),
    ]
    out.append(_kv_table(soft_rows, lang))
    r_pkgs = pkgs.get("r") or {}
    if r_pkgs:
        out.append(("R パッケージ: " if lang == "ja" else "R packages: ")
                   + ", ".join(f"{k} {v}" for k, v in sorted(r_pkgs.items())))
        out.append("")
    py_pkgs = {k: v for k, v in (pkgs.get("python") or {}).items() if k != "python"}
    if py_pkgs:
        out.append(("Python パッケージ: " if lang == "ja" else "Python packages: ")
                   + ", ".join(f"{k} {v}" for k, v in sorted(py_pkgs.items())))
        out.append("")

    # --- 8. 統計解釈上の注意（既存の一元管理された文言をそのまま引用）---
    out.append(f"## {t['h_caveat']}")
    out.append("")
    out.append("> " + caveats.banner_text(lang))
    out.append("")

    # --- 警告 ---
    warnings = c.get("warnings") or []
    if warnings:
        out.append(f"## {t['h_warn']}")
        out.append("")
        for w in warnings:
            out.append(f"- {w}")
        out.append("")

    # --- 未記録項目 ---
    out.append(f"## {t['h_missing']}")
    out.append("")
    missing = c.get("_missing") or []
    if missing:
        for m in missing:
            out.append(f"- `{m}`")
    else:
        out.append(t["no_missing"])
    out.append("")

    out.append("---")
    out.append(t["footer"])
    return "\n".join(out) + "\n"


def render_conditions_rows(conditions: dict, lang: str = "ja") -> list:
    """PPTX の「解析条件」スライド用の [(項目, 値)] を返す。

    スライドに載る量に絞る（詳細は同梱の analysis_conditions.json 側で担保）。
    """
    lang = _norm_lang(lang)
    c = conditions or {}
    inter = c.get("interactive") or {}
    fixed = c.get("onthefly_de_fixed_params") or {}
    ja = lang == "ja"

    rows = [
        ("解析タイプ" if ja else "Analysis type", _get(c, "analysis.analysis_type")),
        ("統合手法" if ja else "Integration method", c.get("integration_method")),
        ("正規化" if ja else "Normalization", _get(c, "analysis.preprocessing.norm_mode")),
        ("バッチ補正" if ja else "Batch correction",
         _get(c, "analysis.preprocessing.batch_correction")),
        ("UMAP n_neighbors / min_dist",
         f"{_v(_get(c, 'analysis.umap.n_neighbors'), lang)} / "
         f"{_v(_get(c, 'analysis.umap.min_dist'), lang)}"),
        ("UMAP metric / dims",
         f"{_v(_get(c, 'analysis.umap.metric'), lang)} / "
         f"{_v(_get(c, 'analysis.umap.dims'), lang)}"),
        ("乱数 seed" if ja else "Random seed", _get(c, "analysis.umap.seed")),
        ("クラスタリング" if ja else "Clustering",
         f"{_v(_get(c, 'analysis.clustering.algorithm'), lang)} "
         f"(res={_v(_get(c, 'analysis.clustering.resolution'), lang)})"),
        ("統計閾値 p / log2FC" if ja else "Statistical thresholds p / log2FC",
         f"{_v(_get(c, 'analysis.thresholds.p'), lang)} / "
         f"{_v(_get(c, 'analysis.thresholds.logfc'), lang)}"),
        ("イオンモード / 許容誤差" if ja else "Ion mode / tolerance",
         f"{_v(_get(c, 'analysis.annotation.ion_mode'), lang)} / "
         f"{_v(_get(c, 'analysis.annotation.tolerance_mz'), lang)}"),
        ("Volcano 閾値（表示用）" if ja else "Volcano thresholds (display only)",
         inter.get("volcano_display")),
        ("Heatmap 設定" if ja else "Heatmap settings", inter.get("heatmap_display")),
        ("Feature plot 設定" if ja else "Feature plot settings",
         inter.get("feature_display")),
        ("on-the-fly DE 固定値" if ja else "on-the-fly DE fixed params",
         ", ".join(f"{k}={v}" for k, v in fixed.items())),
        ("アプリ / R" if ja else "App / R",
         f"{_v(_get(c, 'software.app_version'), lang)} / "
         f"{_v(_get(c, 'software.r_version'), lang)}"),
        ("実行スクリプト" if ja else "Executed script", _get(c, "pipeline.runtime_script")),
        ("未記録項目数" if ja else "Not-recorded items", len(c.get("_missing") or [])),
    ]
    return [(label, _v(value, lang)) for label, value in rows]
