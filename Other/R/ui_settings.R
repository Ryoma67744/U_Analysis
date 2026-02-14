# =============================================================================
# MSI Analysis Application - Settings Tab UI
# 解析設定タブUI
# =============================================================================

#' 解析設定タブUIを生成
#' @return 解析設定タブ内容
create_settings_tab_ui <- function() {
    div(
        class = "card", style = "margin-top: 15px;",

        # UMAP解析設定（DESI/TIMS共通）
        conditionalPanel(
            "input.analysis_method == 'desi_v8' || input.analysis_method_tims == 'tims_v8'",
            h4(class = "card-title", icon("chart-line"), " UMAP解析設定"),

            # ① データフォルダとサンプル選択を1つにまとめる
            fluidRow(
                column(
                    6,
                    div(
                        class = "param-group",
                        h5("データフォルダ・サンプル選択"),
                        div(
                            style = "display: flex; gap: 5px; margin-bottom: 10px;",
                            textInput("data_folder", NULL,
                                value = DEFAULT_DESI_DATA_FOLDER,
                                placeholder = "データフォルダのパス"
                            ),
                            actionButton("browse_folder", "参照...",
                                class = "btn-sm btn-secondary"
                            )
                        ),
                        uiOutput("sample_selector"),
                        helpText("チェックを入れたサンプルが解析対象になります")
                    )
                ),
                column(
                    6,
                    div(
                        class = "param-group",
                        h5("MRMファイル (オプション)"),
                        textInput("mrm_path", NULL, placeholder = "MRM.xlsx のパス"),
                        actionButton("browse_mrm", "参照...", class = "btn-sm btn-secondary")
                    ),
                    # ② TIMSのイオンモードを解析設定内に配置
                    conditionalPanel(
                        "input.analysis_method_tims == 'tims_v8'",
                        div(
                            class = "param-group",
                            style = "margin-top: 15px;",
                            h5("イオンモード"),
                            radioButtons("ion_mode", NULL,
                                choices = c("Positive" = "Positive", "Negative" = "Negative"),
                                selected = "Positive", inline = TRUE
                            ),
                            h5("m/z許容誤差", style = "margin-top: 10px;"),
                            numericInput("tolerance_mz", NULL,
                                value = 0.01, min = 0, step = 0.001, width = "50%"
                            ),
                            h5("Adductフィルター", style = "margin-top: 10px;"),
                            checkboxGroupInput("adduct_filter", NULL,
                                choices = c("+H" = "+H", "+Na" = "+Na", "+NH4" = "+NH4", "-H" = "-H"),
                                selected = c("+H", "+Na", "+NH4"), inline = TRUE
                            )
                        )
                    )
                )
            ),

            # ③ p値閾値とlog2FC閾値を折りたたみ設定にする
            tags$details(
                tags$summary(
                    style = "cursor: pointer; color: #666; font-size: 13px; margin-top: 10px;",
                    icon("sliders-h"), " 詳細設定（p値閾値・log2FC閾値）"
                ),
                div(
                    style = "background: #f8f9fa; padding: 15px; border-radius: 5px; margin-top: 5px;",
                    fluidRow(
                        column(
                            6,
                            numericInput("p_thresh", "p値閾値", value = 0.05, min = 0, max = 1, step = 0.01)
                        ),
                        column(
                            6,
                            numericInput("logfc_thresh", "log2FC閾値", value = 0.10, min = 0, step = 0.05)
                        )
                    )
                )
            ),
            fluidRow(
                column(
                    12,
                    div(
                        class = "param-group",
                        h5("RDSファイル"),
                        checkboxInput("resume_rds", "途中再開 (RDSから)", value = FALSE),
                        conditionalPanel(
                            "input.resume_rds == true",
                            textInput("rds_folder", NULL,
                                placeholder = "RDSファイルが入っているフォルダ",
                                width = "100%"
                            ),
                            actionButton("browse_rds_folder", "参照...", class = "btn-sm btn-secondary"),
                            div(
                                style = "margin-top: 10px;",
                                h6("RDSファイル選択"),
                                uiOutput("rds_file_selector"),
                                helpText("チェックを入れたRDSファイルを使用します")
                            )
                        )
                    )
                )
            )
        ),

        # 再解析設定（DESI/TIMS共通）
        conditionalPanel(
            "input.analysis_method == 'desi_cluster_filter' || input.analysis_method_tims == 'tims_cluster_filter'",
            h4(class = "card-title", icon("filter"), " 再解析設定"),

            # ① 再解析にもデータフォルダを追加
            fluidRow(
                column(
                    6,
                    div(
                        class = "param-group",
                        h5("データフォルダ・サンプル選択"),
                        div(
                            style = "display: flex; gap: 5px; margin-bottom: 10px;",
                            textInput("reanalysis_data_folder", NULL,
                                value = DEFAULT_DESI_DATA_FOLDER,
                                placeholder = "データフォルダのパス"
                            ),
                            actionButton("browse_reanalysis_folder", "参照...",
                                class = "btn-sm btn-secondary"
                            )
                        ),
                        uiOutput("sample_selector_reanalysis"),
                        helpText("チェックを入れたサンプルが再解析対象になります")
                    )
                ),
                column(
                    6,
                    div(
                        class = "param-group",
                        h5("RDSファイル"),
                        textInput("rds_path", NULL, placeholder = "解析済みRDSファイルのパス"),
                        actionButton("browse_rds", "参照...", class = "btn-sm btn-secondary")
                    ),
                    # ② 再解析にもイオンモードを追加
                    conditionalPanel(
                        "input.analysis_method_tims == 'tims_cluster_filter'",
                        div(
                            class = "param-group",
                            style = "margin-top: 15px;",
                            h5("イオンモード"),
                            radioButtons("reanalysis_ion_mode", NULL,
                                choices = c("Positive" = "Positive", "Negative" = "Negative"),
                                selected = "Positive", inline = TRUE
                            ),
                            h5("m/z許容誤差", style = "margin-top: 10px;"),
                            numericInput("reanalysis_tolerance_mz", NULL,
                                value = 0.01, min = 0, step = 0.001, width = "50%"
                            ),
                            h5("Adductフィルター", style = "margin-top: 10px;"),
                            checkboxGroupInput("reanalysis_adduct_filter", NULL,
                                choices = c("+H" = "+H", "+Na" = "+Na", "+NH4" = "+NH4", "-H" = "-H"),
                                selected = c("+H", "+Na", "+NH4"), inline = TRUE
                            )
                        )
                    )
                )
            ),
            fluidRow(
                column(
                    6,
                    div(
                        class = "param-group",
                        h5("フィルタモード"),
                        radioButtons("filter_mode", NULL,
                            choices = c("除外 (exclude)" = "exclude", "抽出 (keep)" = "keep"),
                            selected = "exclude", inline = TRUE
                        )
                    )
                ),
                column(
                    6,
                    div(
                        class = "param-group",
                        h5("対象クラスタ"),
                        textInput("target_clusters", NULL,
                            placeholder = "例: 0, 1, 5, 7",
                            value = ""
                        ),
                        helpText("カンマ区切りでクラスタ番号を入力")
                    )
                )
            ),

            # ① 再解析にも詳細設定（p値閾値・log2FC閾値）を追加
            tags$details(
                tags$summary(
                    style = "cursor: pointer; color: #666; font-size: 13px; margin-top: 10px;",
                    icon("sliders-h"), " 詳細設定（p値閾値・log2FC閾値）"
                ),
                div(
                    style = "background: #f8f9fa; padding: 15px; border-radius: 5px; margin-top: 5px;",
                    fluidRow(
                        column(
                            6,
                            numericInput("reanalysis_p_thresh", "p値閾値", value = 0.05, min = 0, max = 1, step = 0.01)
                        ),
                        column(
                            6,
                            numericInput("reanalysis_logfc_thresh", "log2FC閾値", value = 0.10, min = 0, step = 0.05)
                        )
                    )
                )
            )
        ),
        hr(),

        # 出力設定
        h5(icon("folder-plus"), " 出力設定"),
        fluidRow(
            column(
                4,
                h6("出力フォルダー"),
                textInput("output_subfolder", NULL,
                    value = format(Sys.time(), "Analysis_%Y%m%d_%H%M%S"),
                    placeholder = "例: Analysis_20260109",
                    width = "100%"
                )
            ),
            column(
                4,
                h6("出力先"),
                textInput("output_dir", NULL, value = APP_BASE_DIR, width = "100%")
            ),
            column(
                4,
                actionButton("browse_output", "参照...",
                    class = "btn-sm btn-secondary", style = "margin-top: 25px;"
                )
            )
        ),
        helpText("出力先の下にサブフォルダーとして作成されます"),
        hr(),

        # 実行ボタンエリア
        create_run_button_ui()
    )
}

#' 実行ボタンエリアUIを生成
create_run_button_ui <- function() {
    tagList(
        div(
            class = "run-section", style = "margin-top: 20px; display: flex; align-items: center; gap: 20px;",
            div(
                style = "flex: 0 0 auto;",
                actionButton("run_analysis",
                    label = tagList(icon("play"), " 解析実行"),
                    class = "btn-lg btn-primary",
                    style = "padding: 15px 50px; font-size: 1.2rem;"
                )
            ),
            # 実行停止ボタン（初期状態非表示、shinyjsで制御）
            hidden(
                div(
                    id = "stop_button_container", style = "flex: 0 0 auto;",
                    actionButton("stop_analysis",
                        label = tagList(icon("stop"), " 実行停止"),
                        class = "btn-lg btn-danger",
                        style = "padding: 15px 30px; font-size: 1.2rem;"
                    )
                )
            ),
            # 進捗バー（初期状態非表示、shinyjsで制御）
            hidden(
                div(
                    id = "progress_container", style = "flex: 1;",
                    div(
                        style = "display: flex; justify-content: space-between; align-items: center;",
                        h6(style = "margin: 0;", "解析進捗"),
                        # セクション進捗表示（例: 2/5 セクション）
                        span(id = "section_progress_text", style = "font-weight: bold; font-size: 0.9rem;", "0/0 セクション")
                    ),
                    div(
                        class = "progress", style = "height: 25px; margin-top: 5px;",
                        div(
                            id = "analysis_progress_bar", class = "progress-bar progress-bar-striped progress-bar-animated",
                            role = "progressbar", style = "width: 0%;", "0%"
                        )
                    )
                )
            )
        ),

        # 進捗ログ表示（初期状態非表示、shinyjsで制御）
        hidden(
            div(
                id = "log_container", class = "progress-container", style = "margin-top: 20px;",
                h5(icon("spinner", class = "fa-spin"), " 解析中..."),
                verbatimTextOutput("analysis_log", placeholder = TRUE)
            )
        )
    )
}
