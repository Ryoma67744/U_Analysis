# =============================================================================
# MSI Analysis Application - Interactive Analysis Tab UI
# インタラクティブ解析タブUI
# =============================================================================

#' インタラクティブ解析タブUIを生成
#' @return インタラクティブ解析タブ内容
create_interactive_tab_ui <- function() {
    div(
        class = "card", style = "margin-top: 15px;",
        h4(class = "card-title", icon("chart-scatter"), " インタラクティブUMAP解析"),

        # データ読み込みセクション
        h5(icon("database"), " データソース設定"),
        div(
            style = "background: #e9ecef; padding: 15px; border-radius: 5px; margin-bottom: 15px;",

            # 結果フォルダ選択（既存の解析結果を使用）
            fluidRow(
                column(
                    8,
                    h6(icon("folder"), " 解析結果フォルダ", style = "margin-bottom: 5px;"),
                    helpText("UMAPやHarmony解析済みの結果フォルダを選択してください", style = "margin-bottom: 5px;"),
                    div(
                        style = "display: flex; gap: 10px;",
                        textInput("interactive_result_folder", NULL,
                            placeholder = "解析結果フォルダのパス", width = "85%"
                        ),
                        actionButton("browse_interactive_result", "参照...",
                            class = "btn-sm btn-secondary"
                        )
                    )
                ),
                column(
                    4,
                    h6(" "),
                    actionButton("scan_result_folder", "フォルダをスキャン",
                        class = "btn-info btn-sm", icon = icon("search"),
                        style = "margin-top: 25px;"
                    )
                )
            ),

            # スキャン結果
            conditionalPanel(
                condition = "output.result_folder_scanned",
                div(
                    style = "margin-top: 10px;",
                    uiOutput("scanned_files_info")
                )
            ),
            hr(style = "margin: 15px 0;"),

            # RDSファイル選択
            fluidRow(
                column(
                    6,
                    h6(icon("file-code"), " RDSファイル (Seuratオブジェクト)"),
                    div(
                        style = "display: flex; gap: 10px;",
                        selectInput("interactive_rds_select", NULL, choices = NULL, width = "100%"),
                        actionButton("browse_interactive_rds", "手動選択",
                            class = "btn-sm btn-outline-secondary"
                        )
                    )
                ),
                column(
                    3,
                    h6(" "),
                    actionButton("load_interactive_data", "データ読み込み",
                        class = "btn-primary", icon = icon("upload"),
                        style = "margin-top: 22px; width: 100%;"
                    )
                )
            ),
            hr(style = "margin: 10px 0;"),

            # MSIデータフォルダ選択（解析設定と同様の形式）
            h6(icon("file-alt"), " MSIデータフォルダ (任意、空間マッピング用)"),
            fluidRow(
                column(
                    8,
                    div(
                        style = "display: flex; gap: 10px;",
                        textInput("interactive_msi_folder", NULL,
                            placeholder = "MSI生データ(.txt)が含まれるフォルダ", width = "85%"
                        ),
                        actionButton("browse_interactive_msi", "参照...",
                            class = "btn-sm btn-secondary"
                        )
                    )
                ),
                column(
                    4,
                    actionButton("scan_msi_folder", "フォルダをスキャン",
                        class = "btn-info btn-sm", icon = icon("search")
                    )
                )
            ),

            # MSIファイル選択（チェックボックス）
            conditionalPanel(
                condition = "output.msi_files_found",
                div(
                    style = "margin-top: 10px; max-height: 150px; overflow-y: auto; background: white; padding: 10px; border-radius: 5px;",
                    uiOutput("msi_sample_selector")
                )
            ),

            # サンプル選択
            fluidRow(
                column(
                    4,
                    selectInput("interactive_sample", "サンプル選択", choices = c("すべて" = "All"))
                ),
                column(
                    8,
                    helpText("RDSファイルにUMAP座標とクラスタ情報が含まれている必要があります",
                        style = "margin-top: 30px;"
                    )
                )
            )
        ),

        # データ情報
        conditionalPanel(
            condition = "output.interactive_data_loaded",
            div(
                class = "alert alert-success", style = "padding: 10px; margin-top: 10px;",
                icon("check-circle"),
                textOutput("interactive_data_info", inline = TRUE)
            )
        ),
        hr(),

        # メインビジュアライゼーション
        create_interactive_visualization_ui()
    )
}

#' インタラクティブビジュアライゼーションUIを生成
create_interactive_visualization_ui <- function() {
    fluidRow(
        # 左側：UMAPプロット
        column(
            7,
            h5(icon("project-diagram"), " インタラクティブUMAP"),
            div(
                style = "background: #f8f9fa; padding: 10px; border-radius: 5px;",
                # コントロール
                fluidRow(
                    column(
                        4,
                        selectInput("umap_color_by", "色分け",
                            choices = c("クラスタ" = "Cluster", "サンプル" = "Sample")
                        )
                    ),
                    column(
                        4,
                        selectInput("umap_highlight_cluster", "ハイライト",
                            choices = c("なし（すべて表示）" = ""),
                            multiple = TRUE
                        )
                    ),
                    column(
                        4,
                        checkboxInput("umap_show_legend", "凡例を表示", value = TRUE)
                    )
                ),
                # UMAPプロット
                plotly::plotlyOutput("interactive_umap_plot", height = "450px")
            ),

            # Feature Plot
            h5(icon("palette"), " Feature Plot", style = "margin-top: 20px;"),
            div(
                style = "background: #f8f9fa; padding: 10px; border-radius: 5px;",
                fluidRow(
                    column(
                        6,
                        selectInput("feature_select", "m/z値を選択", choices = NULL)
                    ),
                    column(
                        6,
                        actionButton("show_feature_plot", "表示",
                            class = "btn-sm btn-info",
                            style = "margin-top: 25px;"
                        )
                    )
                ),
                plotly::plotlyOutput("feature_plot", height = "350px")
            )
        ),

        # 右側：詳細情報
        column(
            5,
            # クラスタ情報
            h5(icon("info-circle"), " クラスタ情報"),
            div(
                style = "background: #f8f9fa; padding: 10px; border-radius: 5px; min-height: 200px;",
                verbatimTextOutput("cluster_info_text"),

                # クラスタ統計テーブル
                h6("クラスタ別ピクセル数", style = "margin-top: 15px;"),
                DTOutput("cluster_stats_table", height = "200px")
            ),

            # 空間マッピング
            h5(icon("map"), " 空間マッピング", style = "margin-top: 20px;"),
            div(
                style = "background: #f8f9fa; padding: 10px; border-radius: 5px;",
                helpText("クラスタをハイライトすると空間分布が表示されます"),
                plotly::plotlyOutput("spatial_mapping_plot", height = "350px")
            )
        )
    )
}
