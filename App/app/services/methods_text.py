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
        # ver51.9 / C-6: 生の dict ではなく文言化して出す（散文側と同じ変換）
        for w in warnings:
            out.append(f"- {warning_text(w, lang)}")
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


# ===========================================================================
# 論文用の平文（prose）
# ===========================================================================
# 表形式は条件の点検には向くが、論文の Methods は連続した文章で書く。
# ここでは「骨格は黒、値は黒／青、埋める位置は赤」という 3 色の下書きを作る。
#
#   黒 (text)      … receipt.json / analysis_params.json に直接記録されていた値
#   青 (recovered) … log/v8_runtime_*.R から復元した値（事実だが出典が一段間接＝要確認）
#   赤 (fill)      … 未記録。著者が手で埋める位置
#
# セグメントは dict と list だけで構成し JSON 直列化可能にしてある
# （表示切替のため dcc.Store に載せる。Store はコンポーネントを持てない）。
#
# **文が事実でなくなる分岐は必ず切る。** 例: calibration_enable が偽なら
# calibration_regression_mode が残っていてもキャリブレーションの段落ごと出さない。
# ===========================================================================

SEG_TEXT = "text"
SEG_FILL = "fill"
SEG_RECOVERED = "recovered"

# 未記録スロットの見た目。日本語は〔〕、英語は[]。
_FILL_WRAP = {"ja": ("〔要記入: ", "〕"), "en": ("[TO BE FILLED: ", "]")}

# パス → 赤スロットに出す人間向けの項目名。ドットパスは出さない。
_SLOT_LABELS = {
    "analysis.sample_selection.sample_names": ("試料名", "sample names"),
    "analysis.preprocessing.norm_mode": ("正規化の方法", "normalization method"),
    "analysis.preprocessing.input_normalized": ("入力データの正規化状態",
                                                "normalization state of the input"),
    "analysis.preprocessing.batch_correction": ("バッチ補正の共変量",
                                                "batch-correction covariate"),
    "analysis.mz_align_ppm": ("m/z アライメント幅 (ppm)", "m/z alignment window (ppm)"),
    "analysis.annotation.ion_mode": ("イオン化モード", "ionization mode"),
    "analysis.annotation.tolerance_mz": ("m/z 許容誤差", "m/z tolerance"),
    "analysis.annotation.adduct_filter": ("対象アダクト", "adducts considered"),
    "analysis.annotation.annotation_csv": ("代謝物データベース", "metabolite database"),
    "analysis.umap.n_neighbors": ("UMAP の n_neighbors", "UMAP n_neighbors"),
    "analysis.umap.min_dist": ("UMAP の min_dist", "UMAP min_dist"),
    "analysis.umap.metric": ("UMAP の距離尺度", "UMAP distance metric"),
    "analysis.umap.dims": ("UMAP に用いた次元数", "number of dimensions used for UMAP"),
    "analysis.umap.seed": ("乱数シード", "random seed"),
    "analysis.clustering.algorithm": ("クラスタリング手法", "clustering algorithm"),
    "analysis.clustering.resolution": ("クラスタリングの resolution",
                                       "clustering resolution"),
    "analysis.clustering.k_param": ("近傍グラフの k", "k for the nearest-neighbour graph"),
    "analysis.thresholds.p": ("有意水準 (p)", "significance threshold (p)"),
    "analysis.thresholds.logfc": ("log2 fold-change 閾値", "log2 fold-change threshold"),
    "analysis.filter_mode": ("クラスタの抽出/除外の別", "whether clusters were kept or excluded"),
    "analysis.target_clusters": ("対象クラスタ", "target clusters"),
    "software.app_version": ("解析アプリのバージョン", "analysis application version"),
    "software.r_version": ("R のバージョン", "R version"),
}

# 警告コード → 日英の文言（provenance 側は code + params だけを持つ）
_WARNING_TEXTS = {
    "cache_only_embedding": (
        "この埋め込みは一時キャッシュにのみ存在し、結果フォルダに紐づいていません。"
        "キャッシュが破棄されると再現できません。",
        "This embedding exists only in a temporary cache and is not linked to a result "
        "folder; it cannot be reproduced once the cache is evicted.",
    ),
    "derived_pca_not_persisted": (
        "PCA (uncorrected) の UMAP 埋め込みは実行時に派生生成され、結果フォルダには"
        "保存されません。",
        "The PCA (uncorrected) UMAP embedding is derived at run time and is not stored "
        "in the result folder.",
    ),
}


def _seg(kind: str, value: str, path: str = None) -> dict:
    s = {"t": kind, "v": value}
    if path:
        s["p"] = path
    return s


def _fill_label(path: str, lang: str) -> str:
    ja, en = _SLOT_LABELS.get(path, (path, path))
    pre, post = _FILL_WRAP[lang]
    return f"{pre}{ja if lang == 'ja' else en}{post}"


def _fmt_value(value, lang: str) -> str:
    """値を文中に置ける文字列にする（_v と違い「未記録」は返さない）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "、".join(str(x) for x in value) if lang == "ja" \
            else ", ".join(str(x) for x in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items())
    return str(value)


def _slot(c: dict, path: str, lang: str, fmt=None):
    """条件のパスを 1 セグメントにする。空なら赤スロット、復元値なら青。"""
    value = _get(c, path)
    if value is None or value == "" or value == [] or value == {}:
        return _seg(SEG_FILL, _fill_label(path, lang))
    text = fmt(value, lang) if fmt else _fmt_value(value, lang)
    source = (c.get("_sources") or {}).get(path)
    if source == "runtime_script":
        return _seg(SEG_RECOVERED, text, path)
    return _seg(SEG_TEXT, text, path)


def _para(*pieces) -> list:
    """文字列とセグメントを混ぜて 1 段落にする。"""
    out = []
    for p in pieces:
        if p is None:
            continue
        if isinstance(p, str):
            if p:
                out.append(_seg(SEG_TEXT, p))
        elif isinstance(p, list):
            out.extend(x for x in p if x)
        else:
            out.append(p)
    return out


def _has(c: dict, path: str) -> bool:
    v = _get(c, path)
    return v not in (None, "", [], {})


# ---------------------------------------------------------------------------
# 見出し
# ---------------------------------------------------------------------------

_PROSE_HEADINGS = {
    "samples": ("試料と測定データ", "Samples and data acquisition"),
    "preproc": ("前処理と正規化", "Preprocessing and normalization"),
    "calib": ("m/z キャリブレーション", "m/z calibration"),
    "annot": ("代謝物アノテーション", "Metabolite annotation"),
    "embed": ("バッチ統合と次元圧縮", "Batch integration and dimensionality reduction"),
    "cluster": ("クラスタリング", "Clustering"),
    "de": ("差次発現解析", "Differential analysis"),
    "onthefly": ("選択領域の差次発現解析", "Differential analysis of selected regions"),
    "roi": ("領域別解析と経路解析用出力", "Region-wise analysis and pathway-analysis export"),
    "viz": ("可視化", "Visualization"),
    "software": ("ソフトウェア", "Software"),
    "caveat": ("統計解釈上の注意", "Statistical note"),
    "todo": ("補うべき項目", "Items to be completed"),
    "warn": ("再現性に関する注意", "Reproducibility notes"),
}


def _h(key: str, lang: str) -> str:
    ja, en = _PROSE_HEADINGS[key]
    return ja if lang == "ja" else en


def _is_reanalysis(c: dict) -> bool:
    return str(_get(c, "analysis.analysis_type") or "").endswith("cluster_filter")


# ---------------------------------------------------------------------------
# 各段落
# ---------------------------------------------------------------------------

def _sec_samples(c, lang):
    ja = lang == "ja"
    paras = []
    names = _get(c, "analysis.sample_selection.sample_names")
    n = len(names) if isinstance(names, (list, tuple)) else None

    if _is_reanalysis(c):
        # 再解析: 新規測定ではなく一次解析の結果を絞り込んだもの
        if ja:
            paras.append(_para(
                "本解析は一次解析で得られたクラスタリング結果を対象とした再解析である。",
                "一次解析で得られたクラスタのうち ",
                _slot(c, "analysis.target_clusters", lang),
                " を ",
                _slot(c, "analysis.filter_mode", lang),
                " し、残った測定点を対象として再度次元圧縮とクラスタリングを行った。",
            ))
        else:
            paras.append(_para(
                "This analysis was a re-analysis of the clustering result obtained in the "
                "primary analysis. Clusters ",
                _slot(c, "analysis.target_clusters", lang),
                " were ",
                _slot(c, "analysis.filter_mode", lang),
                "d, and dimensionality reduction and clustering were repeated on the "
                "remaining pixels.",
            ))
    else:
        if ja:
            paras.append(_para(
                "質量分析イメージング (MSI) により取得したデータを解析対象とした。",
                f"解析には {n} 検体（" if n else "解析対象は ",
                _slot(c, "analysis.sample_selection.sample_names", lang),
                "）を用いた。" if n else " である。",
            ))
        else:
            paras.append(_para(
                "Mass spectrometry imaging (MSI) data were used for the analysis. ",
                f"The analysis included {n} samples (" if n else "The samples analysed were ",
                _slot(c, "analysis.sample_selection.sample_names", lang),
                ")." if n else ".",
            ))

    # ROI / セクション絞り込みは行った場合だけ書く
    if _has(c, "analysis.sample_selection.roi_filter"):
        paras.append(_para(
            "解析対象は関心領域 " if ja else "The analysis was restricted to the regions of interest ",
            _slot(c, "analysis.sample_selection.roi_filter", lang),
            " に限定した。" if ja else ".",
        ))
    if _has(c, "analysis.sample_selection.annotation_filter"):
        paras.append(_para(
            "対象セクションは " if ja else "The sections analysed were ",
            _slot(c, "analysis.sample_selection.annotation_filter", lang),
            " とした。" if ja else ".",
        ))
    return {"heading": _h("samples", lang), "paragraphs": paras}


def _sec_preproc(c, lang):
    ja = lang == "ja"
    already = _get(c, "analysis.preprocessing.input_normalized")
    paras = []

    if already is True:
        # 入力が正規化済み＝アプリ側では再正規化していない。ここを取り違えると嘘になる。
        paras.append(_para(
            "入力データは既に正規化されていたため、本解析では追加の正規化を行わなかった。"
            if ja else
            "The input data had already been normalized, so no further normalization was "
            "applied in this analysis."
        ))
    else:
        paras.append(_para(
            "各測定点のスペクトル強度を " if ja else "Spot-wise spectral intensities were normalized using ",
            _slot(c, "analysis.preprocessing.norm_mode", lang),
            " により正規化した。" if ja else ".",
        ))

    ppm = _get(c, "analysis.mz_align_ppm")
    if ppm in (0, "0", 0.0):
        # 0 は「記録された上でアライメントしない」という値。未記録とは違う。
        paras.append(_para(
            "試料間の m/z アライメントは行わなかった。" if ja else
            "No cross-sample m/z alignment was performed."
        ))
    elif ppm not in (None, ""):
        paras.append(_para(
            "試料間の m/z のずれは " if ja else "Between-sample m/z drift was aligned within ",
            _slot(c, "analysis.mz_align_ppm", lang),
            " ppm の範囲で整列した。" if ja else " ppm.",
        ))
    return {"heading": _h("preproc", lang), "paragraphs": paras}


def _sec_calib(c, lang):
    """キャリブレーションは有効だったときだけ段落を出す。

    calibration_enable が偽でも calibration_regression_mode に値が残っている
    ことがあるため（本番のスクリーンショットで確認）、ここを条件分岐しないと
    「poly3 で補正した」という事実でない文が出る。
    """
    if _get(c, "analysis.preprocessing.calibration_enable") is not True:
        return None
    ja = lang == "ja"
    paras = [_para(
        "m/z 軸は既知の参照イオンを用いて " if ja else "The m/z axis was calibrated against known reference ions using ",
        _slot(c, "analysis.preprocessing.calibration_regression_mode", lang),
        " 回帰により較正した。" if ja else " regression.",
    )]
    r2 = _get(c, "analysis.preprocessing.calibration_r_squared")
    if r2 not in (None, ""):
        paras.append(_para(
            "較正曲線の決定係数は " if ja else "The coefficient of determination of the calibration curve was ",
            _slot(c, "analysis.preprocessing.calibration_r_squared", lang),
            " であった。" if ja else ".",
        ))
    return {"heading": _h("calib", lang), "paragraphs": paras}


def _sec_annot(c, lang):
    ja = lang == "ja"
    db = _get(c, "analysis.annotation.annotation_csv")
    db_name = None
    if db:
        from pathlib import Path as _Path
        db_name = _Path(str(db)).name  # 絶対パスは Methods に出さない
    paras = [_para(
        "検出された m/z は " if ja else "Detected m/z values were annotated against ",
        (_seg(SEG_TEXT, db_name, "analysis.annotation.annotation_csv") if db_name
         else _slot(c, "analysis.annotation.annotation_csv", lang)),
        " を参照して代謝物に帰属した。イオン化モードは " if ja
        else ". Annotation was performed in ",
        _slot(c, "analysis.annotation.ion_mode", lang),
        "、質量許容誤差は " if ja else " ionization mode with a mass tolerance of ",
        _slot(c, "analysis.annotation.tolerance_mz", lang),
        " とした。" if ja else ".",
    )]
    if _has(c, "analysis.annotation.adduct_filter"):
        paras.append(_para(
            "考慮したアダクトは " if ja else "The adducts considered were ",
            _slot(c, "analysis.annotation.adduct_filter", lang),
            " である。" if ja else ".",
        ))
    return {"heading": _h("annot", lang), "paragraphs": paras}


# シナリオ → 補正方針の説明。settings_tab の tims_scenario と対応。
_SCENARIO_TEXT = {
    "within_slice": ("同一切片内の比較であり、バッチ補正は行わなかった",
                     "the comparison was within a single slice and no batch correction "
                     "was applied"),
    "condition_compare": ("条件間比較であり、バッチ補正は行わなかった",
                          "conditions were compared without batch correction"),
    "serial_section": ("連続切片を統合するため RPCA を用いた",
                       "serial sections were integrated using RPCA"),
    "batch_correct": ("切片間の技術的差異を Harmony により補正した",
                      "technical differences between slices were corrected with Harmony"),
    "integrate_correct": ("Harmony と RPCA の双方を切片間の統合に用いた",
                          "both Harmony and RPCA were applied to integrate slices"),
}


def _sec_embed(c, lang):
    ja = lang == "ja"
    paras = []
    method = c.get("integration_method")
    scenario = _get(c, "analysis.sample_selection.tims_scenario")

    # --- バッチ統合 ---
    if scenario and scenario in _SCENARIO_TEXT:
        txt = _SCENARIO_TEXT[scenario][0 if ja else 1]
        paras.append(_para(("試料間の統合方針として、" if ja else "For integration across samples, ")
                           + txt + ("。" if ja else ".")))
    elif method and str(method).upper().startswith("PCA"):
        # PCA = 未補正。「補正した」と書かないことが重要。
        paras.append(_para(
            "バッチ補正は行わず、主成分分析 (PCA) による埋め込みをそのまま用いた。"
            if ja else
            "No batch correction was applied; the embedding was derived directly from "
            "principal component analysis (PCA)."
        ))
    elif method:
        paras.append(_para(
            "試料間のバッチ効果は " if ja else "Batch effects across samples were corrected with ",
            _seg(SEG_TEXT, str(method)),
            " により補正した。" if ja else ".",
            "共変量には " if ja else " The covariate used was ",
            _slot(c, "analysis.preprocessing.batch_correction", lang),
            " を用いた。" if ja else ".",
        ))
    else:
        paras.append(_para(
            "試料間の統合手法は " if ja else "The integration method across samples was ",
            _seg(SEG_FILL, _FILL_WRAP[lang][0]
                 + ("統合手法" if ja else "integration method") + _FILL_WRAP[lang][1]),
            " である。" if ja else ".",
        ))

    # --- UMAP ---
    if ja:
        paras.append(_para(
            "次元圧縮には UMAP を用い、n_neighbors = ",
            _slot(c, "analysis.umap.n_neighbors", lang),
            "、min_dist = ", _slot(c, "analysis.umap.min_dist", lang),
            "、距離尺度 = ", _slot(c, "analysis.umap.metric", lang),
            "、入力次元数 = ", _slot(c, "analysis.umap.dims", lang),
            " とした。乱数シードは ", _slot(c, "analysis.umap.seed", lang),
            " に固定した。",
        ))
    else:
        paras.append(_para(
            "Dimensionality reduction was performed with UMAP using n_neighbors = ",
            _slot(c, "analysis.umap.n_neighbors", lang),
            ", min_dist = ", _slot(c, "analysis.umap.min_dist", lang),
            ", metric = ", _slot(c, "analysis.umap.metric", lang),
            ", and ", _slot(c, "analysis.umap.dims", lang),
            " input dimensions. The random seed was fixed at ",
            _slot(c, "analysis.umap.seed", lang), ".",
        ))
    return {"heading": _h("embed", lang), "paragraphs": paras}


def _sec_cluster(c, lang):
    ja = lang == "ja"
    algo = _get(c, "analysis.clustering.algorithm")
    paras = []
    if str(algo) == "dbscan":
        paras.append(_para(
            "クラスタリングには DBSCAN を用いた。" if ja
            else "Clustering was performed with DBSCAN.",
        ))
        for path, ja_l, en_l in (
            ("analysis.clustering.dbscan_eps", "eps は ", "with eps = "),
            ("analysis.clustering.dbscan_min_pts", "minPts は ", "and minPts = "),
        ):
            if _has(c, path):
                paras.append(_para(ja_l if ja else en_l, _slot(c, path, lang),
                                   " とした。" if ja else "."))
    else:
        if ja:
            paras.append(_para(
                "クラスタリングは ", _slot(c, "analysis.clustering.algorithm", lang),
                " 法により行い、resolution は ",
                _slot(c, "analysis.clustering.resolution", lang), " とした。",
            ))
        else:
            paras.append(_para(
                "Clustering was performed with the ",
                _slot(c, "analysis.clustering.algorithm", lang),
                " algorithm at a resolution of ",
                _slot(c, "analysis.clustering.resolution", lang), ".",
            ))
        if _has(c, "analysis.clustering.k_param"):
            # 近傍探索の距離尺度は UMAP の距離尺度（既定 cosine）とは別で、
            # Seurat FindNeighbors の annoy.metric（既定 euclidean）。混同させない。
            metric_seg = (_slot(c, "analysis.clustering.neighbor_metric", lang)
                          if _has(c, "analysis.clustering.neighbor_metric") else None)
            paras.append(_para(
                "共有最近傍グラフの構築には k = " if ja
                else "The shared nearest-neighbour graph was built with k = ",
                _slot(c, "analysis.clustering.k_param", lang),
                (" を用い、近傍探索の距離尺度は " if ja
                 else " neighbours, using ") if metric_seg else None,
                metric_seg,
                ((" とした。" if ja else " as the distance metric for neighbour search.")
                 if metric_seg else (" を用いた。" if ja else " neighbours.")),
            ))

    # クラスタ名を変更している場合は、図中のラベルと番号の対応を明示する
    inter = c.get("interactive") or {}
    name_maps = {k: v for k, v in inter.items()
                 if k == "cluster_name_map" or k.startswith("cluster_name_map::")}
    merged = {}
    for v in name_maps.values():
        if isinstance(v, dict):
            merged.update(v)
    if merged:
        pairs = "、".join(f"cluster{k}={v}" for k, v in merged.items()) if ja \
            else ", ".join(f"cluster{k} = {v}" for k, v in merged.items())
        paras.append(_para(
            "図表中のクラスタ名は次のとおり読み替えている: " if ja
            else "Clusters were renamed in the figures as follows: ",
            _seg(SEG_TEXT, pairs), "。" if ja else ".",
        ))
    return {"heading": _h("cluster", lang), "paragraphs": paras}


def _sec_de(c, lang):
    ja = lang == "ja"
    paras = []
    # 検定の条件は GUI に出ていない固定値。R テンプレの FindAllMarkers 呼び出しと
    # その直後の p.adjust(method="BH") に対応する（Seurat 既定の Bonferroni ではない）。
    fixed = c.get("batch_de_fixed_params") or {}
    test = fixed.get("test", "wilcox")
    padj = fixed.get("p_adjust_method", "BH")
    min_pct = _get(c, "analysis.de.min_pct")
    if min_pct in (None, ""):
        min_pct = fixed.get("min_pct")

    if ja:
        paras.append(_para(
            "各クラスタに特徴的な代謝物は、Seurat の FindAllMarkers により、"
            f"当該クラスタの測定点とそれ以外の全測定点を比較する {test} 検定で抽出した。"
            "上昇・低下の双方を対象とした。",
            (f"検定に先立ち、いずれかの群で {min_pct} 以上の測定点に検出される特徴量に"
             "限定した。" if min_pct is not None else ""),
            f"得られた p 値は {padj} 法により多重比較補正した。",
        ))
        paras.append(_para(
            "有意と判定する閾値は、補正後 p 値 < ",
            _slot(c, "analysis.thresholds.p", lang),
            " かつ |log2 fold-change| > ",
            _slot(c, "analysis.thresholds.logfc", lang), " とした。",
        ))
    else:
        paras.append(_para(
            "Metabolites characteristic of each cluster were identified with "
            f"FindAllMarkers in Seurat, using a {test} test comparing the pixels of each "
            "cluster against all remaining pixels. Both increased and decreased features "
            "were retained. ",
            (f"Prior to testing, features were restricted to those detected in at least "
             f"{min_pct} of pixels in either group. " if min_pct is not None else ""),
            f"The resulting p-values were adjusted by the {padj} procedure.",
        ))
        paras.append(_para(
            "Features were considered significant at an adjusted p-value < ",
            _slot(c, "analysis.thresholds.p", lang),
            " with an absolute log2 fold-change > ",
            _slot(c, "analysis.thresholds.logfc", lang), ".",
        ))

    # 表示専用の閾値を「検定に使った」と誤読させないための一文。
    # ここが Methods の誤記が最も起きやすい箇所。
    vol = (c.get("interactive") or {}).get("volcano_display") or {}
    hm = (c.get("interactive") or {}).get("heatmap_display") or {}
    if vol or hm:
        bits = []
        if vol.get("fc_threshold") is not None or vol.get("p_threshold") is not None:
            bits.append(
                (f"Volcano プロットの表示閾値は log2FC = {vol.get('fc_threshold')}、"
                 f"-log10(p) = {vol.get('p_threshold')}" if ja else
                 f"the volcano plots were drawn with display cut-offs of log2FC = "
                 f"{vol.get('fc_threshold')} and -log10(p) = {vol.get('p_threshold')}")
            )
        if hm.get("top_n") is not None:
            bits.append(
                (f"ヒートマップはクラスタあたり上位 {hm.get('top_n')} 特徴量" if ja else
                 f"heatmaps show the top {hm.get('top_n')} features per cluster")
            )
        if bits:
            paras.append(_para(
                ("なお、" + "、".join(bits) +
                 "であり、これらは作図上の表示条件であって統計的な有意判定には用いていない。"
                 "有意判定に用いた閾値は上記のとおりである。")
                if ja else
                ("Note that " + ", and ".join(bits) +
                 "; these are display settings for the figures and were not used for the "
                 "statistical test. The thresholds applied for significance are those "
                 "given above.")
            ))
    if str(hm.get("scale") or "").lower() in ("zscore", "z-score", "z"):
        paras.append(_para(
            "ヒートマップの値は特徴量ごとに z 化して示した。" if ja else
            "Heatmap values are shown as per-feature z-scores."
        ))
    return {"heading": _h("de", lang), "paragraphs": paras}


def _sec_onthefly(c, lang):
    """投げ縄選択に対する DE。実行していなければ段落ごと出さない。"""
    de = (c.get("interactive") or {}).get("onthefly_de")
    if not de:
        return None
    ja = lang == "ja"
    fixed = c.get("onthefly_de_fixed_params") or {}
    test = fixed.get("test", "wilcox")
    padj = fixed.get("p_adjust_method", "BH")
    min_pct = fixed.get("min_pct")
    logfc = fixed.get("logfc_threshold")
    mode = de.get("mode")
    targets = de.get("target_clusters")

    if ja:
        first = ("UMAP 上で手動選択した測定点群を対象に、追加の差次発現解析を行った。")
        if mode == "local" and targets:
            first += f"比較対象はクラスタ {_fmt_value(targets, lang)} とした。"
        elif mode == "global":
            first += "比較対象は選択領域以外の全測定点とした。"
        paras = [_para(first)]
        paras.append(_para(
            f"検定には Seurat の FindMarkers（{test} 検定）を用い、"
            f"多重比較は {padj} 法で補正した。検出条件は min.pct = {min_pct}、"
            f"logfc.threshold = {logfc} である。"
        ))
    else:
        first = ("An additional differential analysis was performed on a set of pixels "
                 "selected manually on the UMAP embedding. ")
        if mode == "local" and targets:
            first += f"These were compared against cluster(s) {_fmt_value(targets, lang)}. "
        elif mode == "global":
            first += "These were compared against all remaining pixels. "
        paras = [_para(first.rstrip())]
        paras.append(_para(
            f"Testing was performed with FindMarkers in Seurat ({test} test), with "
            f"p-values adjusted by the {padj} procedure, using min.pct = {min_pct} and "
            f"logfc.threshold = {logfc}."
        ))
    return {"heading": _h("onthefly", lang), "paragraphs": paras}


_INTENSITY_REPR_TEXT = {
    "linear": ("対数変換を戻した線形強度", "linearized (de-logged) intensities"),
    "counts": ("生の測定強度", "raw counts"),
    "data": ("対数変換後の強度", "log-transformed intensities"),
}


def _sec_roi(c, lang):
    """ROI 別集計と MetaboAnalyst 出力。出力していなければ段落を出さない。"""
    opt = (c.get("interactive") or {}).get("hne_export_options")
    if not opt:
        return None
    ja = lang == "ja"
    repr_key = str(opt.get("intensity_repr") or "")
    repr_txt = _INTENSITY_REPR_TEXT.get(repr_key, (repr_key, repr_key))[0 if ja else 1]
    unit = opt.get("unit")
    unit_txt = ("化合物単位" if unit == "compound" else "m/z 単位") if ja else \
               ("per compound" if unit == "compound" else "per m/z")
    paras = [_para(
        ("H&E 染色像を MSI 座標へ重ね合わせて関心領域を定義し、領域×クラスタごとに"
         f"平均強度を算出した。書き出した強度は{repr_txt}であり、{unit_txt}で集計した。")
        if ja else
        ("Regions of interest were defined by overlaying the H&E image onto the MSI "
         "coordinates, and mean intensities were computed for each region-by-cluster "
         f"combination. The exported intensities were {repr_txt}, aggregated {unit_txt}.")
    )]
    if opt.get("include_qea"):
        paras.append(_para(
            ("得られた濃度表は MetaboAnalyst の enrichment 解析 (QEA) に投入した。"
             "各行は切片×領域×クラスタの擬似バルクであり、生物学的反復ではないため"
             "結果は探索的である。")
            if ja else
            ("The resulting concentration tables were submitted to MetaboAnalyst for "
             "quantitative enrichment analysis (QEA). Each row is a pseudo-bulk of one "
             "slice-by-region-by-cluster combination rather than a biological replicate, "
             "so these results are exploratory.")
        ))
    return {"heading": _h("roi", lang), "paragraphs": paras}


def _sec_viz(c, lang):
    """解釈に影響する可視化設定だけを書く（マーカーサイズ等は Methods に不要）。"""
    ja = lang == "ja"
    inter = c.get("interactive") or {}
    feat = inter.get("feature_display") or {}
    paras = []

    lo, hi = feat.get("intensity_min"), feat.get("intensity_max")
    if lo is not None or hi is not None:
        # 色スケールのクリップは強度画像の見え方を変えるので必ず書く
        paras.append(_para(
            (f"強度分布図のカラースケールは、各特徴量の強度範囲の {lo}–{hi}% に"
             "クリップして表示した。")
            if ja else
            (f"Colour scales in the intensity maps were clipped to {lo}–{hi}% of the "
             "intensity range of each feature.")
        ))
    if feat.get("colorscale"):
        paras.append(_para(
            f"カラーマップには {feat['colorscale']} を用いた。" if ja else
            f"The {feat['colorscale']} colour map was used."
        ))
    if not paras:
        paras.append(_para(
            "可視化の設定のうち、解釈に影響する条件は記録されていない。" if ja else
            "No visualization settings affecting interpretation were recorded."
        ))
    return {"heading": _h("viz", lang), "paragraphs": paras}


# Methods に書く価値のある R パッケージ（版だけを列挙する。文献引用は入れない）
_R_PACKAGES_FOR_METHODS = ("Seurat", "harmony", "uwot", "leiden", "dbscan", "presto")


def _sec_software(c, lang):
    ja = lang == "ja"
    pkgs = _get(c, "software.packages") or {}
    r_pkgs = pkgs.get("r") or {}
    listed = [f"{n} v{r_pkgs[n]}" for n in _R_PACKAGES_FOR_METHODS if r_pkgs.get(n)]

    paras = []
    if ja:
        paras.append(_para(
            "解析は R ", _slot(c, "software.r_version", lang),
            " 上で行った。",
            (f"主要パッケージは {'、'.join(listed)} である。" if listed else ""),
        ))
        paras.append(_para(
            "一連の処理は社内解析アプリケーション ",
            _slot(c, "software.app_version", lang), " により実行した。",
        ))
    else:
        paras.append(_para(
            "All analyses were performed in R ", _slot(c, "software.r_version", lang),
            ". ",
            (f"The main packages used were {', '.join(listed)}." if listed else ""),
        ))
        paras.append(_para(
            "The full pipeline was executed with our in-house analysis application ",
            _slot(c, "software.app_version", lang), ".",
        ))
    return {"heading": _h("software", lang), "paragraphs": paras}


def _sec_caveat(c, lang):
    """pixel 単位の検定に関する注意。R テンプレの文言と一致させてあるので改変しない。"""
    return {"heading": _h("caveat", lang),
            "paragraphs": [_para(caveats.banner_text(lang))]}


def warning_text(w, lang: str = "ja") -> str:
    """再現性警告 1 件を人が読める文にする (ver51.9 / C-6)。

    ★ 従来は Markdown 側 (`render_methods`) がこの変換を通さず、
      `- {'code': 'cache_only_embedding', 'params': {}}` と **生の dict を
      そのまま**書き出していた。`_WARNING_TEXTS` は既にあるのに散文
      レンダラでしか使われていなかった。Methods は論文に貼る文章なので、
      内部表現がそのまま出ると気づかず提出しうる。

    ★ 未知のコードは **落とさず** コードそのものを出す。散文側は
      `if not texts: continue` で黙って捨てていたが、それでは
      「警告があったのに何も出ない」になる（警告の意味が無い）。
    """
    ja = lang == "ja"
    if isinstance(w, dict):
        texts = _WARNING_TEXTS.get(w.get("code"))
        if texts:
            return texts[0 if ja else 1]
        code = str(w.get("code") or "").strip()
        if code:
            return (f"未分類の警告コード: {code}" if ja
                    else f"Unclassified warning code: {code}")
        return str(w)
    # ver47.0 で書かれた analysis_conditions.json は素の文字列を持つ
    return str(w)


def _sec_warnings(c, lang):
    raw = c.get("warnings") or []
    if not raw:
        return None
    paras = [_para(warning_text(w, lang)) for w in raw]
    return {"heading": _h("warn", lang), "paragraphs": paras} if paras else None


def _sec_todo(sections, lang):
    """本文中に出た赤スロットを末尾にも一覧する（読み飛ばし対策）。"""
    labels = []
    for sec in sections:
        for para in sec["paragraphs"]:
            for seg in para:
                if seg.get("t") == SEG_FILL and seg["v"] not in labels:
                    labels.append(seg["v"])
    ja = lang == "ja"
    if not labels:
        return {"heading": _h("todo", lang), "paragraphs": [_para(
            "赤字の箇所はありません。すべての条件が記録されています。" if ja else
            "There are no highlighted gaps; all conditions were recorded."
        )]}
    intro = ("本文中の赤字は以下の項目です。投稿前に埋めてください。" if ja else
             "The highlighted items below appear in red in the text above. "
             "Please complete them before submission.")
    paras = [_para(intro)]
    for label in labels:
        paras.append([_seg(SEG_FILL, label)])
    return {"heading": _h("todo", lang), "paragraphs": paras}


def build_methods_prose(conditions: dict, lang: str = "ja") -> list:
    """conditions から論文用の平文（セクションのリスト）を組み立てる。

    戻り値は JSON 直列化可能な dict/list のみで構成される:
        [{"heading": str, "paragraphs": [[{"t": ..., "v": ...}, ...], ...]}, ...]

    値が無い項目は既定値で埋めず、必ず `fill` セグメント（赤）にする。
    """
    lang = _norm_lang(lang)
    c = conditions or {}
    ja = lang == "ja"

    # pipeline_stage は「この実行で何をやったか」を決める。reduction_only の実行は
    # クラスタリングも DEG も回していないので、その段落を出すと嘘になる。
    stage = _get(c, "pipeline.pipeline_stage")
    builders = [_sec_samples, _sec_preproc, _sec_calib, _sec_annot, _sec_embed,
                _sec_cluster, _sec_de, _sec_onthefly, _sec_roi, _sec_viz,
                _sec_software, _sec_caveat]
    stage_note = None
    if stage == "reduction_only":
        for b in (_sec_cluster, _sec_de, _sec_onthefly):
            builders.remove(b)
        stage_note = (
            "この実行では次元圧縮までを行っており、クラスタリングおよび差次発現解析は"
            "別の実行で行っている。" if ja else
            "This run performed dimensionality reduction only; clustering and "
            "differential analysis were carried out in a separate run."
        )
    elif stage == "downstream_from_reduction":
        stage_note = (
            "低次元表現は先行する実行の結果を再利用した。" if ja else
            "The low-dimensional embedding was reused from a preceding run."
        )

    sections = []
    for build in builders:
        sec = build(c, lang)
        if sec and sec.get("paragraphs"):
            if stage_note and build is _sec_embed:
                sec["paragraphs"].append(_para(stage_note))
            sections.append(sec)
    warn = _sec_warnings(c, lang)
    if warn:
        sections.append(warn)
    sections.append(_sec_todo(sections, lang))
    return sections


# ---------------------------------------------------------------------------
# バックエンド（同じセグメント列を 3 通りに描く）
# ---------------------------------------------------------------------------
# 3 つとも **同じ文字列**を出す。違うのは色の付け方だけ。
# こうしておくと、プレーンテキストで貼り直しても赤スロットの目印が残る。

def prose_to_text(sections, lang: str = "ja") -> str:
    """平文（Markdown）。色は付かないので、赤スロットは記号のまま残る。"""
    lang = _norm_lang(lang)
    out = []
    for sec in sections or []:
        out.append(f"## {sec['heading']}")
        out.append("")
        for para in sec["paragraphs"]:
            out.append("".join(seg.get("v", "") for seg in para))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def prose_to_html(sections, lang: str = "ja") -> str:
    """色付き HTML（ダウンロード用）。値は必ずエスケープする。

    conditions にはユーザー入力由来の文字列（クラスタ名・サンプル名・パス）が
    含まれるので、エスケープを飛ばすと HTML 注入になる。
    """
    import html as _html

    lang = _norm_lang(lang)
    style_fill = "color:#d32f2f;font-weight:600"
    style_rec = "color:#1565c0"
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{_html.escape(_L[lang]['title'])}</title>",
        "<style>body{font-family:sans-serif;line-height:1.9;max-width:46em;"
        "margin:2em auto;padding:0 1em}h2{font-size:1.1em;margin-top:2em}"
        "p{text-align:justify}</style></head><body>",
    ]
    for sec in sections or []:
        parts.append(f"<h2>{_html.escape(sec['heading'])}</h2>")
        for para in sec["paragraphs"]:
            buf = []
            for seg in para:
                text = _html.escape(str(seg.get("v", "")))
                kind = seg.get("t")
                if kind == SEG_FILL:
                    buf.append(f"<span style='{style_fill}'>{text}</span>")
                elif kind == SEG_RECOVERED:
                    buf.append(f"<span style='{style_rec}'>{text}</span>")
                else:
                    buf.append(text)
            parts.append("<p>" + "".join(buf) + "</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


def prose_to_dash(sections, lang: str = "ja"):
    """Dash コンポーネント列（モーダル表示用）。

    HTML 文字列を組まずコンポーネントで返すので、Dash 側でエスケープされる。
    色は CSS クラス（.methods-fill / .methods-recovered）で当てる。
    画面から選択してコピーすると、色が付いたまま Word 等へ貼れる。
    """
    from dash import html as _h

    lang = _norm_lang(lang)
    children = []
    for sec in sections or []:
        children.append(_h.H5(sec["heading"], className="mt-4 mb-2"))
        for para in sec["paragraphs"]:
            spans = []
            for seg in para:
                text = str(seg.get("v", ""))
                kind = seg.get("t")
                if kind == SEG_FILL:
                    spans.append(_h.Span(text, className="methods-fill"))
                elif kind == SEG_RECOVERED:
                    spans.append(_h.Span(text, className="methods-recovered"))
                else:
                    spans.append(text)
            children.append(_h.P(spans, className="mb-2"))
    return _h.Div(children, className="methods-prose")


def render_methods_prose(conditions: dict, lang: str = "ja") -> str:
    """平文の Methods 下書きを Markdown 文字列で返す（ファイル書き出し用）。"""
    return prose_to_text(build_methods_prose(conditions, lang), lang)


def render_methods_prose_html(conditions: dict, lang: str = "ja") -> str:
    """平文の Methods 下書きを色付き HTML で返す（ダウンロード用）。"""
    return prose_to_html(build_methods_prose(conditions, lang), lang)
