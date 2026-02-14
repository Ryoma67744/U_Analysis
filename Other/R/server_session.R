# =============================================================================
# MSI Analysis Application - Session Server Handlers
# セッション管理サーバーハンドラー
# =============================================================================

#' セッション管理ハンドラーを登録
#' @param input Shiny input
#' @param output Shiny output
#' @param session Shiny session
register_session_handlers <- function(input, output, session) {
    # ----------------------
    # セッション管理
    # ----------------------
    observeEvent(input$save_session, {
        session_data <- list(
            analysis_method = input$analysis_method,
            data_folder = input$data_folder,
            output_dir = input$output_dir,
            selected_samples = input$selected_samples,
            mrm_path = input$mrm_path,
            p_thresh = input$p_thresh,
            logfc_thresh = input$logfc_thresh,
            resume_rds = input$resume_rds,
            filter_mode = input$filter_mode,
            target_clusters = input$target_clusters,
            rds_path = input$rds_path
        )

        tryCatch(
            {
                save_path <- save_session(session_data, input$output_dir)
                showNotification(paste("セッションを保存しました:", basename(save_path)), type = "message")
            },
            error = function(e) {
                showNotification(paste("保存エラー:", e$message), type = "error")
            }
        )
    })

    observeEvent(input$load_session, {
        file <- file.choose()
        if (!is.na(file)) {
            tryCatch(
                {
                    data <- load_session(file)

                    # UI更新
                    updateRadioButtons(session, "analysis_method", selected = data$analysis_method)
                    updateTextInput(session, "data_folder", value = data$data_folder)
                    updateTextInput(session, "output_dir", value = data$output_dir)
                    updateTextInput(session, "mrm_path", value = data$mrm_path %||% "")
                    updateNumericInput(session, "p_thresh", value = data$p_thresh %||% 0.05)
                    updateNumericInput(session, "logfc_thresh", value = data$logfc_thresh %||% 0.10)
                    updateCheckboxInput(session, "resume_rds", value = data$resume_rds %||% FALSE)
                    updateRadioButtons(session, "filter_mode", selected = data$filter_mode %||% "exclude")
                    updateTextInput(session, "target_clusters", value = data$target_clusters %||% "")
                    updateTextInput(session, "rds_path", value = data$rds_path %||% "")

                    showNotification("セッションを読み込みました", type = "message")
                },
                error = function(e) {
                    showNotification(paste("読み込みエラー:", e$message), type = "error")
                }
            )
        }
    })

    # セッション履歴テーブル
    output$session_history_table <- renderDT({
        sessions <- list_sessions(input$output_dir)

        if (nrow(sessions) == 0) {
            return(data.frame(メッセージ = "保存されたセッションがありません"))
        }

        datatable(
            sessions[, c("name", "created_at")],
            colnames = c("セッション名", "作成日時"),
            selection = "single",
            options = list(pageLength = 10, dom = "tp")
        )
    })
}
