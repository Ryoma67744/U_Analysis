# =============================================================================
# MSI Analysis Application - Results Tab UI
# 結果閲覧タブUI
# =============================================================================

#' 結果閲覧タブUIを生成
#' @return 結果閲覧タブ内容
create_results_tab_ui <- function() {
    div(
        class = "card", style = "margin-top: 15px;",
        fluidRow(
            column(
                4,
                h5(icon("folder"), " 結果フォルダ選択"),
                uiOutput("result_folder_selector"),
                div(
                    style = "margin-top: 10px;",
                    actionButton("browse_result_folder", "フォルダを参照...",
                        class = "btn-sm btn-secondary"
                    )
                )
            ),
            column(
                4,
                h5(icon("folder-tree"), " サブフォルダ"),
                uiOutput("subfolder_selector")
            ),
            column(
                4,
                h5(icon("filter"), " 画像タイプ"),
                selectInput("image_category", NULL,
                    choices = c(
                        "すべて" = "all", "UMAP" = "UMAP", "Volcano" = "Volcano",
                        "MSI" = "MSI", "Spatial" = "Spatial", "Heatmap" = "Heatmap"
                    ),
                    selected = "all"
                )
            )
        ),
        fluidRow(
            column(
                4,
                h5(icon("layer-group"), " クラスタ"),
                uiOutput("cluster_selector")
            )
        ),
        hr(),

        # 画像ギャラリー
        uiOutput("image_gallery"),

        # ページネーション
        uiOutput("gallery_pagination")
    )
}

#' セッション履歴タブUIを生成
#' @return セッション履歴タブ内容
create_history_tab_ui <- function() {
    div(
        class = "card", style = "margin-top: 15px;",
        h4(class = "card-title", icon("clock"), " 解析履歴"),
        DTOutput("session_history_table"),
        hr(),
        fluidRow(
            column(
                6,
                actionButton("reload_session",
                    label = tagList(icon("redo"), " 選択したセッションを再読込"),
                    class = "btn-info"
                )
            ),
            column(
                6,
                actionButton("delete_session",
                    label = tagList(icon("trash"), " 選択したセッションを削除"),
                    class = "btn-danger"
                )
            )
        )
    )
}
