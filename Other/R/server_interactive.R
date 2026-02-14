# =============================================================================
# MSI Analysis Application - Interactive Analysis Server Handlers
# インタラクティブ解析サーバーハンドラー
# =============================================================================

#' インタラクティブ解析ハンドラーを登録
#' @param input Shiny input
#' @param output Shiny output
#' @param session Shiny session
#' @param volumes ボリュームリスト
register_interactive_handlers <- function(input, output, session, volumes) {
    # 結果フォルダ参照（Windowsネイティブダイアログ）
    observeEvent(input$browse_interactive_result, {
        folder <- browse_folder_native(input$interactive_result_folder, "解析結果フォルダを選択", APP_BASE_DIR)
        if (!is.na(folder) && dir.exists(folder)) {
            updateTextInput(session, "interactive_result_folder", value = folder)
        }
    })

    # スキャン結果用リアクティブ値
    scan_rv <- reactiveValues(
        scanned = FALSE,
        rds_files = character(),
        msi_folder = NULL
    )

    # フォルダスキャン
    observeEvent(input$scan_result_folder, {
        req(input$interactive_result_folder)

        folder <- input$interactive_result_folder
        if (!dir.exists(folder)) {
            showNotification("フォルダが見つかりません", type = "error")
            return()
        }

        # RDSファイルを検索
        rds_files <- list.files(folder,
            pattern = "\\.rds$", recursive = TRUE,
            full.names = TRUE, ignore.case = TRUE
        )

        # Harmonyファイルを優先
        harmony_files <- rds_files[grepl("harmony", rds_files, ignore.case = TRUE)]
        if (length(harmony_files) > 0) {
            rds_files <- c(harmony_files, rds_files[!rds_files %in% harmony_files])
        }

        scan_rv$rds_files <- rds_files
        scan_rv$scanned <- TRUE

        # RDS選択肢を更新
        if (length(rds_files) > 0) {
            choices <- setNames(rds_files, basename(rds_files))
            updateSelectInput(session, "interactive_rds_select",
                choices = choices,
                selected = rds_files[1]
            )
        } else {
            updateSelectInput(session, "interactive_rds_select", choices = NULL)
        }

        # MSIフォルダを推定（親フォルダを確認）
        parent_folder <- dirname(folder)
        msi_txt_files <- list.files(parent_folder, pattern = "\\.txt$", full.names = FALSE)
        if (length(msi_txt_files) > 0) {
            updateTextInput(session, "interactive_msi_folder", value = parent_folder)
            scan_rv$msi_folder <- parent_folder
        }

        showNotification(paste("スキャン完了:", length(rds_files), "個のRDSファイルを発見"), type = "message")
    })

    # スキャン状態
    output$result_folder_scanned <- reactive({
        scan_rv$scanned && length(scan_rv$rds_files) > 0
    })
    outputOptions(output, "result_folder_scanned", suspendWhenHidden = FALSE)

    # スキャン結果表示
    output$scanned_files_info <- renderUI({
        req(scan_rv$scanned)

        rds_count <- length(scan_rv$rds_files)

        div(
            class = "alert alert-info", style = "padding: 8px; margin: 0;",
            icon("info-circle"),
            sprintf(" %d個のRDSファイルを発見しました", rds_count)
        )
    })

    # RDSファイル手動参照（Windowsネイティブダイアログ）
    observeEvent(input$browse_interactive_rds, {
        file <- browse_file_native(input$interactive_result_folder)
        if (!is.na(file) && file.exists(file)) {
            new_path <- file
            current_choices <- scan_rv$rds_files
            if (!(new_path %in% current_choices)) {
                scan_rv$rds_files <- c(new_path, current_choices)
            }
            choices <- setNames(scan_rv$rds_files, basename(scan_rv$rds_files))
            updateSelectInput(session, "interactive_rds_select",
                choices = choices,
                selected = new_path
            )
        }
    })

    # MSIデータフォルダ参照（Windowsネイティブダイアログ）
    observeEvent(input$browse_interactive_msi, {
        folder <- browse_folder_native(input$interactive_msi_folder, "MSIデータフォルダを選択", APP_BASE_DIR)
        if (!is.na(folder) && dir.exists(folder)) {
            updateTextInput(session, "interactive_msi_folder", value = folder)
        }
    })

    # MSIファイルスキャン用リアクティブ値
    msi_scan_rv <- reactiveValues(
        scanned = FALSE,
        msi_files = character()
    )

    # MSIフォルダスキャン
    observeEvent(input$scan_msi_folder, {
        req(input$interactive_msi_folder)

        folder <- input$interactive_msi_folder
        if (!dir.exists(folder)) {
            showNotification("フォルダが見つかりません", type = "error")
            return()
        }

        msi_files <- list_msi_files(folder)

        msi_scan_rv$msi_files <- msi_files
        msi_scan_rv$scanned <- TRUE

        if (length(msi_files) > 0) {
            showNotification(paste("スキャン完了:", length(msi_files), "個のMSIファイルを発見"), type = "message")
        } else {
            showNotification("MSIファイル(.txt)が見つかりませんでした", type = "warning")
        }
    })

    # MSIファイル発見状態
    output$msi_files_found <- reactive({
        msi_scan_rv$scanned && length(msi_scan_rv$msi_files) > 0
    })
    outputOptions(output, "msi_files_found", suspendWhenHidden = FALSE)

    # MSIサンプル選択UI（チェックボックス）
    output$msi_sample_selector <- renderUI({
        req(msi_scan_rv$scanned)
        msi_files <- msi_scan_rv$msi_files

        if (length(msi_files) == 0) {
            return(helpText("MSIファイルが見つかりませんでした"))
        }

        checkboxGroupInput("interactive_msi_samples",
            label = paste("MSIサンプル選択 (", length(msi_files), "件)"),
            choices = msi_files,
            selected = msi_files
        )
    })

    # インタラクティブデータ用リアクティブ値
    interactive_rv <- reactiveValues(
        data = NULL,
        seurat_obj = NULL,
        selected_cluster = NULL,
        msi_folder = NULL,
        msi_samples = NULL
    )

    # データ読み込み
    observeEvent(input$load_interactive_data, {
        rds_path <- input$interactive_rds_select
        req(rds_path)

        if (!file.exists(rds_path)) {
            showNotification("ファイルが見つかりません", type = "error")
            return()
        }

        if (!is.null(input$interactive_msi_folder) && input$interactive_msi_folder != "") {
            interactive_rv$msi_folder <- input$interactive_msi_folder
        }
        if (!is.null(input$interactive_msi_samples) && length(input$interactive_msi_samples) > 0) {
            interactive_rv$msi_samples <- input$interactive_msi_samples
        }

        withProgress(message = "データを読み込み中...", {
            tryCatch(
                {
                    setProgress(0.3, detail = "RDSファイルを読み込み中...")
                    seurat_obj <- readRDS(rds_path)

                    setProgress(0.7, detail = "データを抽出中...")
                    interactive_data <- extract_interactive_data(seurat_obj)

                    if (!is.null(interactive_data$error)) {
                        showNotification(interactive_data$error, type = "error")
                        return()
                    }

                    interactive_rv$data <- interactive_data
                    interactive_rv$seurat_obj <- seurat_obj

                    samples <- c("すべて" = "All", setNames(interactive_data$samples, interactive_data$samples))
                    updateSelectInput(session, "interactive_sample", choices = samples)

                    clusters <- as.character(sort(unique(interactive_data$plot_data$Cluster)))
                    updateSelectInput(session, "umap_highlight_cluster",
                        choices = c("なし（すべて表示）" = "", setNames(clusters, paste("Cluster", clusters)))
                    )

                    features <- get_available_features(seurat_obj)
                    if (length(features) > 0) {
                        updateSelectInput(session, "feature_select",
                            choices = setNames(features, features),
                            selected = features[1]
                        )
                    }

                    setProgress(1, detail = "完了")
                    showNotification("データを読み込みました", type = "message")
                },
                error = function(e) {
                    showNotification(paste("読み込みエラー:", e$message), type = "error")
                }
            )
        })
    })

    # データ読み込み状態
    output$interactive_data_loaded <- reactive({
        !is.null(interactive_rv$data)
    })
    outputOptions(output, "interactive_data_loaded", suspendWhenHidden = FALSE)

    # データ情報表示
    output$interactive_data_info <- renderText({
        req(interactive_rv$data)
        data <- interactive_rv$data
        paste0(
            "読み込み完了: ", data$n_cells, " ピクセル, ",
            data$n_clusters, " クラスタ, ",
            length(data$samples), " サンプル"
        )
    })

    # インタラクティブUMAPプロット
    output$interactive_umap_plot <- plotly::renderPlotly({
        req(interactive_rv$data)

        highlight <- input$umap_highlight_cluster
        if (is.null(highlight) || all(highlight == "")) {
            highlight <- NULL
        }

        create_interactive_umap(
            interactive_rv$data$plot_data,
            highlight_clusters = highlight,
            color_by = input$umap_color_by
        )
    })

    # UMAPクリックイベント
    observeEvent(plotly::event_data("plotly_click", source = "umap_plot"), {
        click_data <- plotly::event_data("plotly_click", source = "umap_plot")
        if (!is.null(click_data)) {
            req(interactive_rv$data)
            plot_data <- interactive_rv$data$plot_data

            if (!is.null(click_data$key)) {
                cell_id <- click_data$key
                cluster <- as.character(plot_data$Cluster[plot_data$CellID == cell_id])
                if (length(cluster) > 0) {
                    interactive_rv$selected_cluster <- cluster[1]
                }
            }
        }
    })

    # クラスタ情報テキスト
    output$cluster_info_text <- renderText({
        cluster <- interactive_rv$selected_cluster
        if (is.null(cluster)) {
            cluster <- input$umap_highlight_cluster
            if (is.null(cluster) || all(cluster == "")) {
                return("クラスタを選択またはハイライトしてください")
            }
            cluster <- cluster[1]
        }

        get_cluster_summary(interactive_rv$data, cluster)
    })

    # クラスタ統計テーブル
    output$cluster_stats_table <- renderDT({
        req(interactive_rv$data)

        stats <- interactive_rv$data$cluster_stats
        colnames(stats) <- c("クラスタ", "ピクセル数")

        datatable(
            stats,
            selection = "single",
            options = list(
                pageLength = 10,
                dom = "t",
                scrollY = "150px",
                scrollCollapse = TRUE
            ),
            rownames = FALSE
        )
    })

    # クラスタテーブル選択時
    observeEvent(input$cluster_stats_table_rows_selected, {
        req(interactive_rv$data)
        selected_row <- input$cluster_stats_table_rows_selected
        if (length(selected_row) > 0) {
            cluster <- as.character(interactive_rv$data$cluster_stats$Cluster[selected_row])
            interactive_rv$selected_cluster <- cluster

            updateSelectInput(session, "umap_highlight_cluster", selected = cluster)
        }
    })

    # 空間マッピングプロット
    output$spatial_mapping_plot <- plotly::renderPlotly({
        req(interactive_rv$data)

        highlight <- input$umap_highlight_cluster
        if (is.null(highlight) || all(highlight == "")) {
            highlight <- NULL
        }

        selected_sample <- input$interactive_sample

        plot <- create_spatial_plot(
            interactive_rv$data$plot_data,
            selected_clusters = highlight,
            selected_sample = selected_sample
        )

        if (is.null(plot)) {
            plotly::plot_ly() %>%
                plotly::layout(
                    annotations = list(
                        x = 0.5, y = 0.5,
                        text = "空間座標データがありません\nMSIデータとの紐付けが必要です",
                        showarrow = FALSE,
                        font = list(size = 14, color = "gray")
                    ),
                    xaxis = list(visible = FALSE),
                    yaxis = list(visible = FALSE)
                )
        } else {
            plot
        }
    })

    # Feature Plot
    observeEvent(input$show_feature_plot, {
        req(interactive_rv$seurat_obj)
        req(input$feature_select)

        output$feature_plot <- plotly::renderPlotly({
            feature_data <- prepare_feature_plot_data(
                interactive_rv$seurat_obj,
                input$feature_select,
                interactive_rv$data$plot_data
            )

            if (is.null(feature_data)) {
                plotly::plot_ly() %>%
                    plotly::layout(
                        annotations = list(
                            x = 0.5, y = 0.5,
                            text = "フィーチャーデータがありません",
                            showarrow = FALSE
                        )
                    )
            } else {
                create_feature_plot(feature_data, input$feature_select)
            }
        })
    })
}
