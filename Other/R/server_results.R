# =============================================================================
# MSI Analysis Application - Results Server Handlers
# 結果閲覧サーバーハンドラー
# =============================================================================

#' 結果閲覧ハンドラーを登録
#' @param input Shiny input
#' @param output Shiny output
#' @param session Shiny session
#' @param rv リアクティブ値
#' @param volumes ボリュームリスト
register_results_handlers <- function(input, output, session, rv, volumes) {
    # 出力ディレクトリが変更されたらリソースパスを追加
    observe({
        req(input$output_dir)
        if (dir.exists(input$output_dir)) {
            addResourcePath("results", input$output_dir)
        }
    })

    # ----------------------
    # 結果フォルダ選択
    # ----------------------
    output$result_folder_selector <- renderUI({
        folders <- list_result_folders(input$output_dir)

        # 手動選択フォルダがある場合はそれを優先
        if (!is.null(rv$manual_result_folder) && dir.exists(rv$manual_result_folder)) {
            manual_choice <- setNames(
                rv$manual_result_folder,
                paste("手動選択:", basename(rv$manual_result_folder))
            )
            if (nrow(folders) > 0) {
                auto_choices <- setNames(folders$path, paste(folders$name, "-", folders$date))
                all_choices <- c(manual_choice, auto_choices)
            } else {
                all_choices <- manual_choice
            }
            return(selectInput("selected_result_folder", NULL,
                choices = all_choices,
                selected = rv$manual_result_folder
            ))
        }

        if (nrow(folders) == 0) {
            return(helpText("解析結果フォルダが見つかりません。「フォルダを参照...」で選択してください。"))
        }

        choices <- setNames(folders$path, paste(folders$name, "-", folders$date))
        selectInput("selected_result_folder", NULL, choices = choices)
    })

    # 結果フォルダ参照ボタン（Windowsネイティブダイアログ使用）
    observeEvent(input$browse_result_folder, {
        folder <- browse_folder_native(input$output_dir, "結果フォルダを選択", APP_BASE_DIR)
        if (!is.na(folder) && dir.exists(folder)) {
            rv$manual_result_folder <- folder
            showNotification(
                paste("フォルダを選択しました:", folder),
                type = "message",
                duration = 5
            )
        }
    })

    # ----------------------
    # サブフォルダ選択（再帰的に取得）
    # ----------------------
    output$subfolder_selector <- renderUI({
        req(input$selected_result_folder)

        base_folder <- input$selected_result_folder
        if (!dir.exists(base_folder)) {
            return(helpText("フォルダが見つかりません"))
        }

        get_subfolders_recursive <- function(path, depth = 0, max_depth = 2) {
            if (depth >= max_depth) {
                return(character())
            }

            subdirs <- list.dirs(path, full.names = TRUE, recursive = FALSE)
            result <- subdirs

            for (subdir in subdirs) {
                result <- c(result, get_subfolders_recursive(subdir, depth + 1, max_depth))
            }

            return(result)
        }

        subfolders <- get_subfolders_recursive(base_folder)

        if (length(subfolders) == 0) {
            return(helpText("サブフォルダがありません（ルートフォルダを表示中）"))
        }

        display_names <- sapply(subfolders, function(sf) {
            rel_path <- sub(paste0("^", gsub("\\\\", "\\\\\\\\", base_folder), "[/\\\\]?"), "", sf)
            rel_path
        })

        choices <- c(
            "（ルートフォルダ）" = base_folder,
            setNames(subfolders, display_names)
        )

        selectInput("selected_subfolder", NULL, choices = choices)
    })

    # ----------------------
    # クラスタ選択
    # ----------------------
    output$cluster_selector <- renderUI({
        req(input$selected_result_folder)
        clusters <- get_available_clusters(input$selected_result_folder)

        if (length(clusters) == 0) {
            return(helpText("クラスタ情報がありません"))
        }

        choices <- c("すべて" = "all", setNames(as.character(clusters), paste("Cluster", clusters)))
        selectInput("selected_cluster", NULL, choices = choices)
    })

    # ----------------------
    # 画像ギャラリー
    # ----------------------
    output$image_gallery <- renderUI({
        req(input$selected_result_folder)

        # 解析中は5秒ごとに自動更新
        if (rv$is_running) {
            invalidateLater(5000, session)
        }

        target_folder <- if (!is.null(input$selected_subfolder) &&
            dir.exists(input$selected_subfolder)) {
            input$selected_subfolder
        } else {
            input$selected_result_folder
        }

        structure <- get_result_structure(target_folder)
        all_images <- c(structure$root_images, unlist(lapply(structure$subdirs, function(x) x$images)))

        if (length(all_images) == 0) {
            return(div(class = "alert alert-info", "画像ファイルが見つかりません"))
        }

        # カテゴリフィルタ
        if (!is.null(input$image_category) && input$image_category != "all") {
            categorized <- organize_images_by_category(all_images)
            all_images <- categorized[[input$image_category]]
            if (is.null(all_images)) all_images <- character()
        }

        # クラスタフィルタ
        if (!is.null(input$selected_cluster) && input$selected_cluster != "all") {
            all_images <- filter_images_by_cluster(all_images, as.integer(input$selected_cluster))
        }

        if (length(all_images) == 0) {
            return(div(class = "alert alert-warning", "フィルタ条件に一致する画像がありません"))
        }

        # 画像を更新日時順にソート
        all_images <- sort_images_by_time(all_images)

        # ページング（1ページ20枚）
        per_page <- 20
        total_pages <- ceiling(length(all_images) / per_page)
        current_page <- min(rv$gallery_page, total_pages)
        start_idx <- (current_page - 1) * per_page + 1
        end_idx <- min(current_page * per_page, length(all_images))
        page_images <- all_images[start_idx:end_idx]

        # 画像グリッド生成
        image_items <- lapply(seq_along(page_images), function(i) {
            img_path <- page_images[i]
            img_id <- paste0("img_", i)

            norm_img_path <- normalizePath(img_path, winslash = "/", mustWork = FALSE)
            img_dir <- dirname(norm_img_path)
            resource_name <- paste0("gallery_", digest::digest(img_dir, algo = "crc32"))

            tryCatch(
                {
                    addResourcePath(resource_name, img_dir)
                },
                error = function(e) {}
            )

            src_path <- paste0(resource_name, "/", basename(norm_img_path))

            div(
                class = "image-item",
                onclick = sprintf(
                    "Shiny.setInputValue('clicked_image', '%s', {priority: 'event'})",
                    gsub("'", "\\\\'", img_path)
                ),
                tags$img(
                    src = src_path,
                    alt = basename(img_path),
                    onerror = "this.style.display='none'; this.parentElement.querySelector('.caption').innerHTML='[読込エラー]';",
                    loading = "lazy"
                ),
                div(class = "caption", basename(img_path))
            )
        })

        tagList(
            div(
                class = "mb-2",
                sprintf("全 %d 件中 %d - %d 件を表示", length(all_images), start_idx, end_idx)
            ),
            div(class = "image-gallery", image_items),
            if (total_pages > 1) {
                div(
                    class = "d-flex justify-content-center align-items-center mt-3 gap-2",
                    actionButton("prev_page", icon("chevron-left"),
                        class = "btn btn-outline-primary",
                        disabled = current_page <= 1
                    ),
                    span(sprintf("ページ %d / %d", current_page, total_pages)),
                    actionButton("next_page", icon("chevron-right"),
                        class = "btn btn-outline-primary",
                        disabled = current_page >= total_pages
                    )
                )
            }
        )
    })

    # ページ切り替えハンドラー
    observeEvent(input$prev_page, {
        if (rv$gallery_page > 1) {
            rv$gallery_page <- rv$gallery_page - 1
        }
    })

    observeEvent(input$next_page, {
        rv$gallery_page <- rv$gallery_page + 1
    })

    # 画像クリック時
    observeEvent(input$clicked_image, {
        rv$selected_image <- input$clicked_image
        rv$image_zoom <- 1
        runjs("var modal = new bootstrap.Modal(document.getElementById('image_modal')); modal.show();")
    })

    # ズームイン
    observeEvent(input$zoom_in, {
        rv$image_zoom <- min(rv$image_zoom + 0.25, 3)
    })

    # ズームアウト
    observeEvent(input$zoom_out, {
        rv$image_zoom <- max(rv$image_zoom - 0.25, 0.5)
    })

    # ズームリセット
    observeEvent(input$zoom_reset, {
        rv$image_zoom <- 1
    })

    output$modal_image <- renderUI({
        req(rv$selected_image)

        norm_img_path <- normalizePath(rv$selected_image, winslash = "/", mustWork = FALSE)
        img_dir <- dirname(norm_img_path)
        resource_name <- paste0("modal_", digest::digest(img_dir, algo = "crc32"))

        tryCatch(
            {
                addResourcePath(resource_name, img_dir)
            },
            error = function(e) {}
        )

        src_path <- paste0(resource_name, "/", basename(norm_img_path))
        zoom_pct <- rv$image_zoom * 100
        filename <- basename(rv$selected_image)
        full_path <- gsub("\\\\", "/", rv$selected_image)

        runjs(sprintf('
      document.getElementById("modal_filename").innerText = "%s";
      document.getElementById("modal_path").innerText = "%s";
      document.getElementById("modal_path").title = "%s (クリックでコピー)";
      setImagePath("%s");
    ', filename, full_path, full_path, gsub('"', '\\\\"', full_path)))

        div(
            class = "d-flex",
            div(
                class = "d-flex flex-column align-items-center me-2", style = "min-width: 60px;",
                actionButton("zoom_in", icon("plus"), class = "btn btn-sm btn-outline-secondary mb-1"),
                span(sprintf("%.0f%%", zoom_pct), class = "small my-1"),
                actionButton("zoom_out", icon("minus"), class = "btn btn-sm btn-outline-secondary mb-1"),
                actionButton("zoom_reset", "リセット", class = "btn btn-sm btn-outline-secondary mt-2", style = "font-size: 0.75rem;")
            ),
            div(
                style = "flex: 1; overflow: auto; max-height: 80vh; text-align: center;",
                tags$img(
                    src = src_path,
                    alt = filename,
                    style = sprintf("transform: scale(%f); transform-origin: top center; max-width: 100%%; max-height: 78vh;", rv$image_zoom)
                )
            )
        )
    })

    # パスコピー通知
    observeEvent(input$path_copied, {
        showNotification("パスをクリップボードにコピーしました", type = "message", duration = 2)
    })
}
