# =============================================================================
# MSI Analysis Application - Main UI
# メインUI定義
# =============================================================================

#' メインUIを生成
#' @return Shiny UI
create_main_ui <- function() {
    fluidPage(
        theme = bs_theme(
            version = 5,
            bootswatch = "flatly",
            primary = "#667eea"
        ),
        useShinyjs(),
        tags$head(
            tags$link(rel = "stylesheet", type = "text/css", href = "styles.css")
        ),

        # ヘッダー
        div(
            class = "app-header",
            h1(icon("microscope"), " MSI Analysis Application"),
            p(class = "subtitle", "質量分析イメージングデータ解析システム")
        ),

        # メインレイアウト
        fluidRow(
            # サイドバー
            column(
                3,
                create_sidebar_ui()
            ),

            # メインパネル
            column(
                9,
                tabsetPanel(
                    id = "main_tabs", type = "tabs",

                    # 解析設定タブ
                    tabPanel("解析設定",
                        icon = icon("cogs"), value = "settings",
                        create_settings_tab_ui()
                    ),

                    # 結果閲覧タブ
                    tabPanel("結果閲覧",
                        icon = icon("images"), value = "results",
                        create_results_tab_ui()
                    ),

                    # セッション履歴タブ
                    tabPanel("セッション履歴",
                        icon = icon("history"), value = "history",
                        create_history_tab_ui()
                    ),

                    # インタラクティブ解析タブ
                    tabPanel("インタラクティブ解析",
                        icon = icon("chart-scatter"), value = "interactive",
                        create_interactive_tab_ui()
                    )
                )
            )
        ),

        # 画像プレビューモーダル
        create_image_modal_ui()
    )
}

#' 画像プレビューモーダルUIを生成
create_image_modal_ui <- function() {
    tagList(
        div(
            id = "image_modal", class = "modal fade", tabindex = "-1",
            div(
                class = "modal-dialog modal-xl modal-dialog-centered", style = "max-width: 95vw;",
                div(
                    class = "modal-content",
                    div(
                        class = "modal-header py-2",
                        h5(class = "modal-title", "画像プレビュー"),
                        span(
                            id = "modal_filename", class = "ms-3 text-muted small",
                            style = "max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
                        ),
                        tags$button(
                            id = "copy_path_btn", type = "button",
                            class = "btn btn-sm btn-outline-secondary ms-2",
                            title = "パスをコピー",
                            onclick = "copyImagePath()",
                            icon("copy")
                        ),
                        span(
                            id = "modal_path", class = "ms-2 text-muted small",
                            style = "max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer;",
                            title = "クリックでコピー",
                            onclick = "copyImagePath()"
                        ),
                        tags$button(type = "button", class = "btn-close ms-auto", `data-bs-dismiss` = "modal")
                    ),
                    div(
                        class = "modal-body p-2",
                        uiOutput("modal_image")
                    )
                )
            )
        ),

        # パスコピー用JavaScript
        tags$script(HTML("
      var currentImagePath = '';
      function setImagePath(path) {
        currentImagePath = path;
      }
      function copyImagePath() {
        if (currentImagePath) {
          navigator.clipboard.writeText(currentImagePath).then(function() {
            Shiny.setInputValue('path_copied', Date.now());
          });
        }
      }
    "))
    )
}
