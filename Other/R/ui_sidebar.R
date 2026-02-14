# =============================================================================
# MSI Analysis Application - Sidebar UI
# サイドバーUI
# =============================================================================

#' サイドバーUIを生成
#' @return サイドバーUI要素
create_sidebar_ui <- function() {
    div(
        class = "sidebar",
        # 解析手法選択（DESI/TIMSを1つのラジオボタングループで管理）
        h4(icon("flask"), " 解析手法"),
        # DESI セクションヘッダー
        tags$div(
            style = "color: #667eea; font-weight: bold; font-size: 14px; margin-bottom: 5px; border-bottom: 1px solid #667eea; padding-bottom: 2px;",
            "【DESI】"
        ),
        radioButtons("analysis_method", NULL,
            choiceNames = list(
                tags$span(style = "margin-left: 15px;", "UMAP解析"),
                tags$span(style = "margin-left: 15px;", "再解析")
            ),
            choiceValues = list(
                "desi_v8",
                "desi_cluster_filter"
            ),
            selected = "desi_v8"
        ),
        # TIMS セクションヘッダー
        tags$div(
            style = "color: #f093fb; font-weight: bold; font-size: 14px; margin-top: 10px; margin-bottom: 5px; border-bottom: 1px solid #f093fb; padding-bottom: 2px;",
            "【TIMS】"
        ),
        radioButtons("analysis_method_tims", NULL,
            choiceNames = list(
                tags$span(style = "margin-left: 15px;", "UMAP解析"),
                tags$span(style = "margin-left: 15px;", "再解析")
            ),
            choiceValues = list(
                "tims_v8",
                "tims_cluster_filter"
            ),
            selected = character(0) # デフォルトで選択なし
        ),

        # スクリプト設定（折りたたみ可能）
        create_script_settings_ui(),
        hr(),

        # DESI初期設定（折りたたみ可能）
        create_desi_settings_ui(),

        # TIMS初期設定（折りたたみ可能）
        create_tims_settings_ui(),

        # 出力設定初期設定（折りたたみ可能）
        create_output_settings_ui(),
        hr(),

        # セッション管理
        h4(icon("save"), " セッション"),
        div(
            style = "display: flex; gap: 10px;",
            actionButton("save_session", label = tagList(icon("save"), " 保存"), class = "btn-sm btn-success"),
            actionButton("load_session", label = tagList(icon("folder-open"), " 読込"), class = "btn-sm btn-info")
        )
    )
}

#' スクリプト設定UIを生成
create_script_settings_ui <- function() {
    tags$details(
        tags$summary(
            style = "cursor: pointer; color: #666; font-size: 12px; margin-top: 10px;",
            icon("cog"), " スクリプト設定"
        ),
        div(
            style = "background: #f8f9fa; padding: 10px; border-radius: 5px; margin-top: 5px;",

            # DESI セクション
            h5("DESI", style = "color: #667eea; margin-bottom: 8px; font-weight: bold; border-bottom: 1px solid #667eea; padding-bottom: 3px;"),

            # DESI UMAP解析スクリプト
            h6("UMAP解析 (v8 Template)"),
            div(
                style = "display: flex; gap: 5px;",
                textInput("desi_v8_script_path", NULL,
                    value = DESI_V8_TEMPLATE_PATH,
                    placeholder = "DESI v8スクリプトのパス", width = "80%"
                ),
                actionButton("browse_desi_v8_script", "...",
                    class = "btn-sm btn-outline-secondary"
                )
            ),

            # DESI 再解析スクリプト
            h6("再解析 (Cluster Filter)", style = "margin-top: 10px;"),
            div(
                style = "display: flex; gap: 5px;",
                textInput("desi_cluster_filter_script_path", NULL,
                    value = DESI_CLUSTER_FILTER_PATH,
                    placeholder = "DESI Cluster Filterスクリプトのパス", width = "80%"
                ),
                actionButton("browse_desi_cluster_script", "...",
                    class = "btn-sm btn-outline-secondary"
                )
            ),

            # TIMS セクション
            h5("TIMS", style = "color: #f093fb; margin-top: 15px; margin-bottom: 8px; font-weight: bold; border-bottom: 1px solid #f093fb; padding-bottom: 3px;"),

            # TIMS UMAP解析スクリプト
            h6("UMAP解析"),
            div(
                style = "display: flex; gap: 5px;",
                textInput("tims_v8_script_path", NULL,
                    value = TIMS_V8_TEMPLATE_PATH,
                    placeholder = "TIMS UMAPスクリプトのパス", width = "80%"
                ),
                actionButton("browse_tims_v8_script", "...",
                    class = "btn-sm btn-outline-secondary"
                )
            ),

            # TIMS 再解析スクリプト
            h6("再解析 (Cluster Filter)", style = "margin-top: 10px;"),
            div(
                style = "display: flex; gap: 5px;",
                textInput("tims_cluster_filter_script_path", NULL,
                    value = TIMS_CLUSTER_FILTER_PATH,
                    placeholder = "TIMS Cluster Filterスクリプトのパス", width = "80%"
                ),
                actionButton("browse_tims_cluster_script", "...",
                    class = "btn-sm btn-outline-secondary"
                )
            ),

            # デフォルトに戻すボタン
            div(
                style = "margin-top: 10px; text-align: right;",
                actionButton("reset_script_paths", "デフォルトに戻す",
                    class = "btn-xs btn-outline-secondary",
                    icon = icon("undo")
                )
            )
        )
    )
}

#' DESI初期設定UIを生成
create_desi_settings_ui <- function() {
    tags$details(
        tags$summary(
            style = "cursor: pointer; color: #666; font-size: 12px;",
            icon("cog"), " DESI初期設定"
        ),
        div(
            style = "background: #f8f9fa; padding: 10px; border-radius: 5px; margin-top: 5px;",
            h6("デフォルトデータフォルダ"),
            div(
                style = "display: flex; gap: 5px;",
                textInput("default_desi_data_folder", NULL,
                    value = DEFAULT_DESI_DATA_FOLDER,
                    placeholder = "DESIデータフォルダ", width = "80%"
                ),
                actionButton("browse_default_desi_folder", "...",
                    class = "btn-sm btn-outline-secondary"
                )
            ),
            h6("MRMファイル (.xlsx)", style = "margin-top: 10px;"),
            div(
                style = "display: flex; gap: 5px;",
                textInput("default_mrm_file", NULL,
                    value = DEFAULT_MRM_FILE_PATH,
                    placeholder = "MRMファイルのパス", width = "80%"
                ),
                actionButton("browse_default_mrm", "...",
                    class = "btn-sm btn-outline-secondary"
                )
            ),
            h6("デフォルト出力先", style = "margin-top: 10px;"),
            div(
                style = "display: flex; gap: 5px;",
                textInput("default_desi_output_dir", NULL,
                    value = DESI_DATA_DIR,
                    placeholder = "DESI出力先フォルダ", width = "80%"
                ),
                actionButton("browse_default_desi_output", "...",
                    class = "btn-sm btn-outline-secondary"
                )
            ),
            div(
                style = "margin-top: 10px; text-align: right;",
                actionButton("reset_desi_defaults", "リセット",
                    class = "btn-xs btn-outline-secondary", icon = icon("undo")
                ),
                actionButton("apply_desi_defaults", "適用",
                    class = "btn-xs btn-primary", icon = icon("check"),
                    style = "margin-left: 5px;"
                )
            )
        )
    )
}

#' TIMS初期設定UIを生成
create_tims_settings_ui <- function() {
    tags$details(
        tags$summary(
            style = "cursor: pointer; color: #666; font-size: 12px; margin-top: 10px;",
            icon("cog"), " TIMS初期設定"
        ),
        div(
            style = "background: #f8f9fa; padding: 10px; border-radius: 5px; margin-top: 5px;",
            h6("デフォルトデータフォルダ"),
            div(
                style = "display: flex; gap: 5px;",
                textInput("default_tims_data_folder", NULL,
                    value = DEFAULT_TIMS_DATA_FOLDER,
                    placeholder = "TIMSデータフォルダ", width = "80%"
                ),
                actionButton("browse_default_tims_folder", "...",
                    class = "btn-sm btn-outline-secondary"
                )
            ),
            h6("アノテーションファイル (.csv)", style = "margin-top: 10px;"),
            div(
                style = "display: flex; gap: 5px;",
                textInput("default_annotation_csv", NULL,
                    value = DEFAULT_ANNOTATION_CSV_PATH,
                    placeholder = "アノテーションファイルのパス", width = "80%"
                ),
                actionButton("browse_default_annotation", "...",
                    class = "btn-sm btn-outline-secondary"
                )
            ),
            h6("デフォルト出力先", style = "margin-top: 10px;"),
            div(
                style = "display: flex; gap: 5px;",
                textInput("default_tims_output_dir", NULL,
                    value = TIMS_DATA_DIR,
                    placeholder = "TIMS出力先フォルダ", width = "80%"
                ),
                actionButton("browse_default_tims_output", "...",
                    class = "btn-sm btn-outline-secondary"
                )
            ),
            div(
                style = "margin-top: 10px; text-align: right;",
                actionButton("reset_tims_defaults", "リセット",
                    class = "btn-xs btn-outline-secondary", icon = icon("undo")
                ),
                actionButton("apply_tims_defaults", "適用",
                    class = "btn-xs btn-primary", icon = icon("check"),
                    style = "margin-left: 5px;"
                )
            )
        )
    )
}

#' 出力設定UIを生成
create_output_settings_ui <- function() {
    tags$details(
        tags$summary(
            style = "cursor: pointer; color: #666; font-size: 12px; margin-top: 10px;",
            icon("folder-plus"), " 出力設定"
        ),
        div(
            style = "background: #f8f9fa; padding: 10px; border-radius: 5px; margin-top: 5px;",
            h6("デフォルト出力先"),
            div(
                style = "display: flex; gap: 5px;",
                textInput("default_output_dir", NULL,
                    value = APP_BASE_DIR,
                    placeholder = "出力先フォルダのパス", width = "80%"
                ),
                actionButton("browse_default_output", "...",
                    class = "btn-sm btn-outline-secondary"
                )
            ),
            div(
                style = "margin-top: 10px; text-align: right;",
                actionButton("reset_output_defaults", "リセット",
                    class = "btn-xs btn-outline-secondary", icon = icon("undo")
                ),
                actionButton("apply_output_defaults", "適用",
                    class = "btn-xs btn-primary", icon = icon("check"),
                    style = "margin-left: 5px;"
                )
            )
        )
    )
}
