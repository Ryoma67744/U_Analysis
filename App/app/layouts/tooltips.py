# =============================================================================
# MSI Analysis Application - Tooltip Definitions
# ツールチップ定義（初心者向けヘルプ）
# =============================================================================

import dash_bootstrap_components as dbc

# 統一スタイル設定
_TOOLTIP_PROPS = {
    "placement": "auto",
    "delay": {"show": 500, "hide": 100},
}


def help_badge(target_id):
    """Tooltip存在を示す「?」バッジアイコンを返す（バッジIDがTooltipのtargetになる）"""
    return dbc.Badge(
        "?", id=f"{target_id}_help_badge",
        pill=True, color="info",
        style={"fontSize": "10px", "marginLeft": "4px", "cursor": "help",
               "verticalAlign": "middle"},
    )


def get_sidebar_tooltips():
    """サイドバー用ツールチップ群を返す"""
    return [
        dbc.Tooltip(
            "全サンプルのMSIデータからUMAPクラスタリングを新規実行します。"
            "初回解析時に選択してください。",
            target="analysis_method", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "スクリプトファイルのパスをインストール時のデフォルトに戻します。",
            target="reset_script_paths", **_TOOLTIP_PROPS,
        ),
    ]


def get_settings_tooltips():
    """解析設定タブ用ツールチップ群を返す"""
    return [
        dbc.Tooltip(
            "入力が SCiLS RMS 等で正規化済みなら OFF（二重正規化を回避）。"
            "生データなら ON（LogNormalize=TIC正規化+log を実行）。",
            target="normalize_input_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "Harmony/RPCA は段階をまたぐ共通構造を見るための統合。"
            "各条件1切片(交絡)では生物差も一緒に除去されるため、未補正(PCA)も併用を。",
            target="cluster_source_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "切片アノテーションが何を表すかでシナリオを選択。"
            "同一切片のクラスタ/Ctrl vs KO比較=無補正、"
            "連続切片(技術反復)=RPCA統合、測定バッチ補正=Harmony(過補正注意・非推奨)。"
            "条件比較＋技術差補正=Harmony と RPCA を両方適用し測定差を補正(交絡下では条件差も縮小。"
            "条件をまたぐ共有埋め込み/クラスタを得たいとき。条件間DEGは独立算出で不変)。",
            target="tims_scenario_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "再解析(exclusion/inclusion)の解析シナリオ。既定は初回解析の選択を引き継ぎます。"
            "意味は初回と同じ（無補正/RPCA統合/Harmony補正/Harmony＋RPCA両方）。",
            target="reanalysis_tims_scenario_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "m/zの一致判定に使う許容誤差（Da単位）。"
            "デフォルト0.01は高分解能MSに適した値です。",
            target="tolerance_mz_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "アノテーション検索に使用するアダクトイオン種を選択します。"
            "選択したアダクトのみが検索対象になります。",
            target="adduct_filter_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "統計的有意性の閾値です。この値より小さいp値を持つ"
            "マーカーのみが有意と判定されます。一般的には0.05を使用します。",
            target="p_thresh_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "発現変動の最小閾値（log2スケール）。"
            "例: 0.5 = 約1.4倍以上の発現差を検出します。",
            target="logfc_thresh_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "exclude: 指定クラスタを除外して再解析します。"
            "keep: 指定クラスタのみを残して再解析します。",
            target="filter_mode_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "測定時のイオン化モードを選択します。"
            "Positive: 陽イオンモード、Negative: 陰イオンモード。",
            target="ion_mode_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "過去の解析で保存したRDSファイルから途中再開できます。"
            "クラスタリング結果を再利用して時間を短縮します。",
            target="resume_rds_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "出力フォルダ名（タイムスタンプ付き）。"
            "出力先ディレクトリの下にこの名前のサブフォルダが作成されます。",
            target="output_subfolder_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "マトリクスの既知ピーク（リファレンスm/z）と実測ピークを対応付けて"
            "m/z値の回帰補正を行います。補正後のm/zでアノテーションを再検索します。",
            target="calibration_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "線形(1次): ppmずれがm/zに比例する場合（通常はこれで十分）。"
            "多項式(2次/3次): 線形で5ppm以上のずれが残る場合に使用。",
            target="calibration_regression_mode", **_TOOLTIP_PROPS,
        ),
    ]


def get_interactive_tooltips():
    """インタラクティブ解析タブ用ツールチップ群を返す"""
    return [
        dbc.Tooltip(
            "表示する解析手法を選択。未補正(PCA)=段階差(技術+生物・交絡)、"
            "Harmony/RPCA=共通構造(段階差は除去)。PCAは未補正PCAのUMAP(比較基準)で、"
            "専用RDSが無い既存結果でもHarmonyから自動生成して選択可。読み方は隣の📚ガイド参照。",
            target="interactive_integration_method_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "統合: 全サンプルを1つのUMAPに重ねて表示。"
            "サンプル別: サンプルごとに個別のUMAPを並べて表示。",
            target="umap_display_mode_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "Cluster: クラスタ番号で色分け。"
            "Sample: サンプル名で色分け。",
            target="umap_color_by_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "選択したクラスタを強調表示し、他のクラスタを薄く表示します。"
            "複数選択可能です。",
            target="umap_highlight_cluster_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "選択したクラスタをプロットから完全に除去します。"
            "ノイズクラスタの非表示に便利です。",
            target="umap_exclude_cluster_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "表示するサンプルを選択します。"
            "空にすると全サンプルを並べて表示します。",
            target="interactive_sample_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "選択したクラスタを強調表示し、他のクラスタを薄く表示します。"
            "複数選択可能です。",
            target="spatial_highlight_cluster_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "選択したクラスタをプロットから完全に除去します。"
            "ノイズクラスタの非表示に便利です。",
            target="spatial_exclude_cluster_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "Spatial Mappingの点サイズ。"
            "0=自動（ピクセル間隔から最適サイズを計算）。"
            "手動で調整する場合は1以上を設定してください。",
            target="spatial_marker_size_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "プロットをフルスクリーンで拡大表示します。"
            "詳細な観察やスクリーンショット撮影に便利です。",
            target="expand_umap_btn", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "プロットをフルスクリーンで拡大表示します。",
            target="expand_spatial_btn", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "プロットをフルスクリーンで拡大表示します。",
            target="expand_feature_btn", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "プロットをフルスクリーンで拡大表示します。",
            target="expand_deg_btn", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "m/z値で検索してFeature Plotに表示するイオンを選択します。"
            "DEGテーブルの行クリックでも選択できます。",
            target="feature_select", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "Volcano Plotで有意とみなす発現変動量（log2FC）の閾値線。"
            "この値を超える点が色付きで表示されます。",
            target="volcano_fc_threshold_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "Volcano Plotで有意とみなす-log10(p値)の閾値線。"
            "デフォルト1.3はp=0.05に相当します。",
            target="volcano_p_threshold_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "Z-score: クラスタ間の相対的な発現パターンを比較（推奨）。"
            "Raw: 生の発現値をそのまま表示。",
            target="heatmap_scale_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "ONにすると、m/z値の隣にアノテーションDBから一致した"
            "化合物名を表示します。",
            target="heatmap_annotation_switch_help_badge", **_TOOLTIP_PROPS,
        ),
    ]


def get_results_tooltips():
    """結果閲覧タブ用ツールチップ群を返す"""
    return [
        dbc.Tooltip(
            "解析結果が保存されているフォルダを選択します。"
            "プロジェクト選択で自動設定されます。",
            target="result_folder_selector_help_badge", **_TOOLTIP_PROPS,
        ),
        dbc.Tooltip(
            "画像の種類で絞り込みます。"
            "UMAP、Volcano、Spatial等のカテゴリで分類されています。",
            target="image_category_help_badge", **_TOOLTIP_PROPS,
        ),
    ]
