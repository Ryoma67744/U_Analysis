# =============================================================================
# MSI Analysis Application - Analysis Server Handlers
# 解析実行・ログ監視サーバーハンドラー
# =============================================================================

#' 解析ハンドラーを登録
#' @param input Shiny input
#' @param output Shiny output
#' @param session Shiny session
#' @param rv リアクティブ値
register_analysis_handlers <- function(input, output, session, rv) {
    # プログレスバー更新関数（セクション進捗対応版）
    update_progress <- function(percent, msg = NULL, section = NULL, total = NULL) {
        # セクション情報が指定されている場合は更新
        if (!is.null(section) && !is.null(total)) {
            rv$current_section <- section
            rv$total_sections <- total
            runjs(sprintf('
        $("#section_progress_text").text("%d/%d セクション");
      ', section, total))
        }

        # 現セクションの％を更新
        runjs(sprintf('
      $("#analysis_progress_bar").css("width", "%d%%").text("%d%%");
    ', percent, percent))

        if (!is.null(msg)) {
            rv$analysis_log <- c(rv$analysis_log, msg)
        }
    }

    # サンプル一覧
    output$sample_selector <- renderUI({
        req(input$data_folder)
        samples <- list_msi_files(input$data_folder)

        if (length(samples) == 0) {
            return(helpText("データフォルダにMSIファイル(.txt)が見つかりません"))
        }

        checkboxGroupInput("selected_samples", NULL,
            choices = samples,
            selected = samples
        )
    })

    # 再解析用サンプル一覧
    output$sample_selector_reanalysis <- renderUI({
        req(input$data_folder)
        samples <- list_msi_files(input$data_folder)

        if (length(samples) == 0) {
            return(helpText("データフォルダにMSIファイル(.txt)が見つかりません"))
        }

        checkboxGroupInput("selected_samples_reanalysis", NULL,
            choices = samples,
            selected = samples
        )
    })

    # RDSファイル一覧（チェックボックス）
    output$rds_file_selector <- renderUI({
        req(input$rds_folder)

        if (!dir.exists(input$rds_folder)) {
            return(helpText("フォルダが見つかりません"))
        }

        rds_files <- list.files(input$rds_folder,
            pattern = "\\.rds$",
            full.names = FALSE, ignore.case = TRUE
        )

        if (length(rds_files) == 0) {
            return(helpText("RDSファイルが見つかりません"))
        }

        checkboxGroupInput("selected_rds_files", NULL,
            choices = rds_files,
            selected = rds_files
        )
    })

    # ----------------------
    # 解析実行
    # ----------------------
    observeEvent(input$run_analysis, {
        rv$is_running <- TRUE
        rv$stop_requested <- FALSE
        rv$current_section <- 0
        rv$total_sections <- 0
        rv$analysis_log <- c("[開始] 解析を開始します...")
        rv$analysis_start_time <- Sys.time() # 解析開始時刻を記録

        # 進捗表示UIを表示（shinyjsで即座に表示）
        shinyjs::show("stop_button_container")
        shinyjs::show("progress_container")
        shinyjs::show("log_container")

        tryCatch(
            {
                # 出力先とサブフォルダーを結合
                full_output_dir <- file.path(input$output_dir, input$output_subfolder)
                dir.create(full_output_dir, recursive = TRUE, showWarnings = FALSE)
                rv$full_output_dir <- full_output_dir

                # 解析タイプを取得（DESIまたはTIMS）
                analysis_type <- if (!is.null(input$analysis_method) && input$analysis_method != "") {
                    input$analysis_method
                } else {
                    input$analysis_method_tims
                }

                # ヘッダーが選択された場合はエラー
                if (is.null(analysis_type) || analysis_type %in% c("desi_header", "tims_header", "")) {
                    stop("解析手法を選択してください（UMAP解析または再解析）")
                }

                # UMAP解析（DESI または TIMS）
                if (analysis_type %in% c("desi_v8", "tims_v8")) {
                    req(input$selected_samples)

                    # セクション数はサンプル数に基づく
                    total_samples <- length(input$selected_samples)
                    rv$total_sections <- total_samples
                    rv$selected_samples_list <- input$selected_samples # サンプルリストを保存

                    # スクリプトパスを選択（DESI or TIMS）
                    script_path <- if (analysis_type == "desi_v8") {
                        input$desi_v8_script_path
                    } else {
                        input$tims_v8_script_path
                    }

                    params <- list(
                        template_path = script_path,
                        data_folder = input$data_folder,
                        sample_names = input$selected_samples,
                        mrm_path = input$mrm_path,
                        p_thresh = input$p_thresh,
                        logfc_thresh = input$logfc_thresh,
                        resume_from_rds = input$resume_rds,
                        rds_folder = input$rds_folder,
                        resume_rds_paths = if (!is.null(input$selected_rds_files) && length(input$selected_rds_files) > 0) {
                            file.path(input$rds_folder, input$selected_rds_files)
                        } else {
                            character()
                        }
                    )

                    method_name <- if (analysis_type == "desi_v8") "DESI" else "TIMS"
                    rv$analysis_log <- c(rv$analysis_log, paste0("[設定] ", method_name, " UMAP解析パラメータを生成中..."))
                    update_progress(10, "パラメータ生成中...", section = 0, total = total_samples)

                    config_path <- generate_v8_config(params, full_output_dir)
                    rv$analysis_log <- c(rv$analysis_log, paste("[設定] 設定ファイル生成完了:", basename(config_path)))
                    update_progress(20, "設定ファイル生成完了", section = 0, total = total_samples)

                    # 外部プロセスで解析を開始
                    rv$analysis_log <- c(rv$analysis_log, "[実行] 外部プロセスで解析を開始...")
                    process_info <- start_analysis_process(config_path, full_output_dir)

                    if (process_info$success) {
                        rv$log_file <- process_info$log_file
                        rv$status_file <- process_info$status_file
                        rv$analysis_log <- c(rv$analysis_log, "[実行] 解析プロセスを開始しました。ログを監視中...")
                        update_progress(30, "解析実行中（ログを監視中）...", section = 1, total = total_samples)
                    } else {
                        rv$analysis_log <- c(rv$analysis_log, paste("[エラー]", process_info$message))
                        showNotification(process_info$message, type = "error")
                        rv$is_running <- FALSE
                    }
                } else if (analysis_type %in% c("desi_cluster_filter", "tims_cluster_filter")) {
                    # Cluster Filter解析（DESI または TIMS）
                    req(input$rds_path)

                    target_clusters <- as.integer(trimws(strsplit(input$target_clusters, ",")[[1]]))
                    target_clusters <- target_clusters[!is.na(target_clusters)]

                    if (length(target_clusters) == 0) {
                        stop("対象クラスタを指定してください")
                    }

                    # 再解析のセクション数
                    total_samples <- length(input$selected_samples_reanalysis)
                    if (total_samples == 0) total_samples <- 1
                    rv$total_sections <- total_samples
                    rv$selected_samples_list <- input$selected_samples_reanalysis # サンプルリストを保存

                    # スクリプトパスを選択（DESI or TIMS）
                    script_path <- if (analysis_type == "desi_cluster_filter") {
                        input$desi_cluster_filter_script_path
                    } else {
                        input$tims_cluster_filter_script_path
                    }

                    params <- list(
                        template_path = script_path,
                        rds_path = input$rds_path,
                        original_data_folder = input$data_folder,
                        filter_mode = input$filter_mode,
                        target_clusters = target_clusters,
                        sample_names = input$selected_samples_reanalysis
                    )

                    method_name <- if (analysis_type == "desi_cluster_filter") "DESI" else "TIMS"
                    rv$analysis_log <- c(rv$analysis_log, paste0("[設定] ", method_name, " 再解析パラメータを生成中..."))
                    update_progress(10, "パラメータ生成中...", section = 0, total = total_samples)

                    config_path <- generate_cluster_filter_config(params, full_output_dir)
                    rv$analysis_log <- c(rv$analysis_log, paste("[設定] 設定ファイル生成完了:", basename(config_path)))
                    update_progress(20, "設定ファイル生成完了", section = 0, total = total_samples)

                    # 外部プロセスで解析を開始
                    rv$analysis_log <- c(rv$analysis_log, "[実行] 外部プロセスで解析を開始...")
                    process_info <- start_analysis_process(config_path, full_output_dir)

                    if (process_info$success) {
                        rv$log_file <- process_info$log_file
                        rv$status_file <- process_info$status_file
                        rv$analysis_log <- c(rv$analysis_log, "[実行] 解析プロセスを開始しました。ログを監視中...")
                        update_progress(30, "解析実行中（ログを監視中）...", section = 1, total = total_samples)
                    } else {
                        rv$analysis_log <- c(rv$analysis_log, paste("[エラー]", process_info$message))
                        showNotification(process_info$message, type = "error")
                        rv$is_running <- FALSE
                    }
                } else {
                    stop("解析手法が選択されていません")
                }
            },
            error = function(e) {
                rv$analysis_log <- c(rv$analysis_log, paste("[エラー]", e$message))
                showNotification(paste("エラー:", e$message), type = "error")
                rv$is_running <- FALSE
            }
        )
    })

    # ----------------------
    # ログ監視（定期的にログファイルを読み込んでUIを更新）
    # ----------------------
    observe({
        req(rv$is_running)
        req(rv$log_file)
        req(rv$status_file)

        # 2秒ごとに更新
        invalidateLater(2000, session)

        # ログファイルの内容を取得
        log_content <- get_analysis_log(rv$log_file, last_n = 100)
        log_lines <- if (nchar(log_content) > 0) strsplit(log_content, "\n")[[1]] else character()

        # 出力フォルダ内の生成ファイル数をカウント（より正確な進捗）
        generated_files <- 0
        if (!is.null(rv$full_output_dir) && dir.exists(rv$full_output_dir)) {
            # 画像ファイル（png, pdf, jpg）とRDSファイルをカウント
            image_files <- list.files(rv$full_output_dir,
                pattern = "\\.(png|pdf|jpg|rds)$",
                recursive = TRUE, ignore.case = TRUE
            )
            generated_files <- length(image_files)
        }

        # 経過時間の計算
        start_time <- rv$analysis_start_time
        if (is.null(start_time)) start_time <- Sys.time()
        elapsed_mins <- as.numeric(difftime(Sys.time(), start_time, units = "mins"))

        # ログからサンプル進捗を検出
        all_log_text <- paste(log_lines, collapse = " ")
        selected_samples <- rv$selected_samples_list # 選択されたサンプルリスト
        if (is.null(selected_samples)) selected_samples <- character()

        # 各サンプルの進捗を判定
        sample_progress <- ""
        if (length(selected_samples) > 0) {
            sample_progress <- "【セッション進捗】\n"

            # 処理ステップの定義
            step_keywords <- c(
                "Loading|読み込み|Read" = "読込",
                "Normalizing|SCTransform|正規化" = "正規化",
                "PCA|RunPCA" = "PCA",
                "UMAP|RunUMAP" = "UMAP",
                "Harmony|Integration" = "統合",
                "Cluster|FindNeighbors|FindClusters" = "クラスタ",
                "Marker|FindAllMarkers|DEG" = "マーカー",
                "Saving|save|ggsave|保存" = "保存"
            )

            completed_count <- 0
            current_sample <- NULL

            for (i in seq_along(selected_samples)) {
                sample_name <- selected_samples[i]

                # このサンプル関連のログ行を抽出
                sample_log_lines <- log_lines[grepl(sample_name, log_lines, fixed = TRUE)]
                sample_log_text <- paste(sample_log_lines, collapse = " ")

                # 出力フォルダにこのサンプルの専用ファイルがあるかチェック（より厳密に）
                sample_file_count <- 0
                if (!is.null(rv$full_output_dir) && dir.exists(rv$full_output_dir)) {
                    # サンプル名を含み、かつUMAP/Volcano/Spatialなどの結果ファイルをカウント
                    all_files <- list.files(rv$full_output_dir, recursive = TRUE, ignore.case = TRUE)
                    # サンプル名がファイル名に含まれ、かつ結果ファイルらしきもの
                    sample_pattern <- paste0(".*", sample_name, ".*(UMAP|Volcano|Spatial|cluster|harmony|Marker)")
                    sample_files <- all_files[grepl(sample_pattern, all_files, ignore.case = TRUE)]
                    sample_file_count <- length(sample_files)
                }

                # このサンプルの処理ステップを検出
                step_status <- sapply(seq_along(step_keywords), function(j) {
                    pattern <- names(step_keywords)[j]
                    if (grepl(pattern, sample_log_text, ignore.case = TRUE)) {
                        return("✓")
                    } else {
                        return("○")
                    }
                })
                step_names <- unname(step_keywords)

                # ステータス判定
                is_completed <- FALSE
                if (completed_count == i - 1) {
                    has_save_keyword <- grepl("Saving|saved|ggsave|saveRDS|保存", sample_log_text, ignore.case = TRUE)
                    if (sample_file_count >= 5 && has_save_keyword) {
                        is_completed <- TRUE
                    }
                }

                if (is_completed) {
                    status_icon <- "✓"
                    status_text <- "完了"
                    completed_count <- completed_count + 1

                    step_display <- paste(step_names, collapse = "✓→")
                    step_display <- paste0(step_display, "✓")

                    sample_progress <- paste0(
                        sample_progress,
                        sprintf("───────────────────────────────────────\n"),
                        sprintf("  %d. %s  [%s %s] (出力: %d件)\n", i, sample_name, status_icon, status_text, sample_file_count),
                        sprintf("     %s\n", step_display)
                    )
                } else if (grepl(sample_name, all_log_text, fixed = TRUE)) {
                    if (is.null(current_sample)) {
                        status_icon <- "▶"
                        status_text <- "処理中"
                        current_sample <- sample_name
                    } else {
                        status_icon <- "▶"
                        status_text <- "処理中"
                    }

                    step_display <- ""
                    for (j in seq_along(step_names)) {
                        if (step_status[j] == "✓") {
                            step_display <- paste0(step_display, step_names[j], "✓→")
                        } else if (j == 1 || step_status[j - 1] == "✓") {
                            step_display <- paste0(step_display, "[", step_names[j], "]")
                            break
                        }
                    }
                    if (step_display == "") step_display <- "[開始待ち]"

                    sample_progress <- paste0(
                        sample_progress,
                        sprintf("───────────────────────────────────────\n"),
                        sprintf("  %d. %s  [%s %s]\n", i, sample_name, status_icon, status_text),
                        sprintf("     進行: %s\n", step_display)
                    )
                } else {
                    status_icon <- "○"
                    status_text <- "待機中"

                    sample_progress <- paste0(
                        sample_progress,
                        sprintf("───────────────────────────────────────\n"),
                        sprintf("  %d. %s  [%s %s]\n", i, sample_name, status_icon, status_text)
                    )
                }
            }
            sample_progress <- paste0(sample_progress, "───────────────────────────────────────\n\n")
        }

        # 進捗表示を作成
        progress_display <- paste0(
            "═══════════════════════════════════════\n",
            "【解析状況】\n",
            "═══════════════════════════════════════\n",
            "サンプル数: ", rv$total_sections, "\n",
            "経過時間: ", sprintf("%.1f 分", elapsed_mins), "\n",
            "生成ファイル数: ", generated_files, " 件\n",
            "═══════════════════════════════════════\n\n",
            sample_progress,
            "【Rコンソール出力】\n",
            "───────────────────────────────────────\n",
            paste(tail(log_lines, 15), collapse = "\n"),
            "\n───────────────────────────────────────"
        )

        rv$analysis_log <- progress_display

        # 進捗バーをファイル数ベースで更新
        file_progress <- min(50, generated_files)
        time_progress <- min(50, elapsed_mins * 2)
        estimated_progress <- min(95, as.integer((file_progress + time_progress)))

        progress_text <- sprintf("生成: %dファイル / 経過: %.1f分", generated_files, elapsed_mins)

        runjs(sprintf('
      $("#analysis_progress_bar").css("width", "%d%%").text("%d%%");
      $("#section_progress_text").text("%s");
    ', estimated_progress, estimated_progress, progress_text))

        # ステータスファイルをチェック
        status <- get_analysis_status(rv$status_file)

        if (status == "finished") {
            rv$is_running <- FALSE
            rv$analysis_log <- paste0(rv$analysis_log, "\n\n✅ 解析が正常に完了しました")
            update_progress(100, "解析完了", section = generated_files, total = generated_files)
            showNotification("解析が完了しました", type = "message")

            shinyjs::delay(2000, {
                shinyjs::hide("stop_button_container")
                shinyjs::hide("progress_container")
            })
        } else if (status == "stopped") {
            rv$is_running <- FALSE
            rv$analysis_log <- paste0(rv$analysis_log, "\n\n⚠️ 解析が停止されました")
            showNotification("解析が停止されました", type = "warning")

            shinyjs::delay(1000, {
                shinyjs::hide("stop_button_container")
                shinyjs::hide("progress_container")
            })
        }
    })

    # ----------------------
    # 実行停止ボタン
    # ----------------------
    observeEvent(input$stop_analysis, {
        rv$stop_requested <- TRUE
        rv$analysis_log <- paste0(rv$analysis_log, "\n\n[停止要求] 解析の停止をリクエストしました...")
        showNotification("解析停止をリクエストしました。プロセスを終了しています...", type = "warning")

        tryCatch(
            {
                system("taskkill /F /IM Rscript.exe", wait = FALSE, show.output.on.console = FALSE)

                rv$analysis_log <- paste0(rv$analysis_log, "\n[停止] プロセスを停止しました")
                rv$is_running <- FALSE

                showNotification("解析を停止しました", type = "message")

                shinyjs::delay(1000, {
                    shinyjs::hide("stop_button_container")
                    shinyjs::hide("progress_container")
                })
            },
            error = function(e) {
                showNotification(paste("停止エラー:", e$message), type = "error")
            }
        )
    })

    output$is_running <- reactive({
        rv$is_running
    })
    outputOptions(output, "is_running", suspendWhenHidden = FALSE)

    output$analysis_log <- renderText({
        paste(rv$analysis_log, collapse = "\n")
    })
}
