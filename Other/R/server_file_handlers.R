# =============================================================================
# MSI Analysis Application - File Handlers
# ファイル参照ボタンハンドラー
# =============================================================================
#
# 全てのファイル/フォルダ参照ボタンでWindowsネイティブダイアログを使用
# - フォルダ選択: choose.dir()
# - ファイル選択: file.choose()
#
# =============================================================================

#' ファイルブラウザのボリューム設定を取得（結果閲覧用に残す）
#' @return ボリュームリスト
get_file_volumes <- function() {
    volumes <- c(
        Home = "~",
        getVolumes()(),
        # 特別なフォルダを追加
        "App Folder" = APP_BASE_DIR,
        "Dropbox" = file.path(Sys.getenv("USERPROFILE"), "Dropbox"),
        "Biochem Dropbox" = "C:/Users/Cciia/Biochem Dropbox",
        "Desktop" = file.path(Sys.getenv("USERPROFILE"), "Desktop"),
        "Documents" = file.path(Sys.getenv("USERPROFILE"), "Documents"),
        "Downloads" = file.path(Sys.getenv("USERPROFILE"), "Downloads")
    )
    # 存在しないパスを除去
    volumes[sapply(volumes, function(v) dir.exists(v) || file.exists(v))]
}

#' Windowsネイティブフォルダ選択ダイアログを表示
#' @param current_path 現在のパス（デフォルトで開くフォルダ）
#' @param caption ダイアログのキャプション
#' @param fallback_path フォールバックパス（current_pathが無効な場合に使用）
#' @return 選択されたフォルダパス（キャンセル時はNA）
browse_folder_native <- function(current_path, caption = "フォルダを選択", fallback_path = APP_BASE_DIR) {
    # パスの正規化とチェック
    default_dir <- NULL

    # 1. 現在のパスを確認
    if (!is.null(current_path) && nchar(current_path) > 0) {
        # Windows形式に正規化
        normalized_path <- normalizePath(current_path, winslash = "\\", mustWork = FALSE)
        if (dir.exists(normalized_path)) {
            default_dir <- normalized_path
        }
    }

    # 2. フォールバックパスを確認
    if (is.null(default_dir) && !is.null(fallback_path) && nchar(fallback_path) > 0) {
        normalized_fallback <- normalizePath(fallback_path, winslash = "\\", mustWork = FALSE)
        if (dir.exists(normalized_fallback)) {
            default_dir <- normalized_fallback
        }
    }

    # 3. 最終フォールバック
    if (is.null(default_dir)) {
        default_dir <- normalizePath(APP_BASE_DIR, winslash = "\\", mustWork = FALSE)
    }

    choose.dir(default = default_dir, caption = caption)
}

#' Windowsネイティブファイル選択ダイアログを表示
#' @param current_path 現在のパス（デフォルトで開くフォルダ）
#' @return 選択されたファイルパス（キャンセル時はNA）
browse_file_native <- function(current_path = NULL) {
    # 現在のパスがある場合、そのディレクトリに移動してからダイアログを開く
    old_wd <- getwd()
    tryCatch({
        if (!is.null(current_path) && nchar(current_path) > 0) {
            if (file.exists(current_path)) {
                setwd(dirname(current_path))
            } else if (dir.exists(current_path)) {
                setwd(current_path)
            }
        }
        file.choose()
    }, finally = {
        setwd(old_wd)
    })
}

#' ファイルハンドラーを登録
#' @param input Shiny input
#' @param output Shiny output
#' @param session Shiny session
#' @param volumes ボリュームリスト（結果閲覧用に残す）
register_file_handlers <- function(input, output, session, volumes) {
    # ==========================================================================
    # 解析設定タブ - フォルダ参照ボタン
    # ==========================================================================

    # データフォルダ参照
    observeEvent(input$browse_folder, {
        # フォールバック: DESI/TIMSデータフォルダ
        fallback <- if (!is.null(input$default_desi_data_folder) && nchar(input$default_desi_data_folder) > 0) {
            input$default_desi_data_folder
        } else if (!is.null(input$default_tims_data_folder) && nchar(input$default_tims_data_folder) > 0) {
            input$default_tims_data_folder
        } else {
            APP_BASE_DIR
        }
        folder <- browse_folder_native(input$data_folder, "データフォルダを選択", fallback)
        if (!is.na(folder) && dir.exists(folder)) {
            updateTextInput(session, "data_folder", value = folder)
        }
    })

    # 出力フォルダ参照
    observeEvent(input$browse_output, {
        folder <- browse_folder_native(input$output_dir, "出力フォルダを選択", APP_BASE_DIR)
        if (!is.na(folder) && dir.exists(folder)) {
            updateTextInput(session, "output_dir", value = folder)
        }
    })

    # MRMファイル参照
    observeEvent(input$browse_mrm, {
        file <- browse_file_native(input$mrm_path)
        if (!is.na(file) && file.exists(file)) {
            updateTextInput(session, "mrm_path", value = file)
        }
    })

    # RDSファイル参照
    observeEvent(input$browse_rds, {
        file <- browse_file_native(input$rds_path)
        if (!is.na(file) && file.exists(file)) {
            updateTextInput(session, "rds_path", value = file)
        }
    })

    # RDSフォルダ参照
    observeEvent(input$browse_rds_folder, {
        folder <- browse_folder_native(input$rds_folder, "RDSファイルが入っているフォルダを選択", APP_BASE_DIR)
        if (!is.na(folder) && dir.exists(folder)) {
            updateTextInput(session, "rds_folder", value = folder)
        }
    })

    # 再解析データフォルダ参照
    observeEvent(input$browse_reanalysis_folder, {
        folder <- browse_folder_native(input$reanalysis_data_folder, "再解析データフォルダを選択", APP_BASE_DIR)
        if (!is.na(folder) && dir.exists(folder)) {
            updateTextInput(session, "reanalysis_data_folder", value = folder)
        }
    })

    # ==========================================================================
    # DESI初期設定ハンドラー
    # ==========================================================================

    # DESIデフォルトデータフォルダ参照
    observeEvent(input$browse_default_desi_folder, {
        folder <- browse_folder_native(input$default_desi_data_folder, "DESIデータフォルダを選択", DESI_DIR)
        if (!is.na(folder) && dir.exists(folder)) {
            updateTextInput(session, "default_desi_data_folder", value = folder)
        }
    })

    # MRMファイル参照（DESI初期設定）
    observeEvent(input$browse_default_mrm, {
        file <- browse_file_native(input$default_mrm_file)
        if (!is.na(file) && file.exists(file)) {
            updateTextInput(session, "default_mrm_file", value = file)
        }
    })

    # DESI出力先参照
    observeEvent(input$browse_default_desi_output, {
        folder <- browse_folder_native(input$default_desi_output_dir, "DESI出力先フォルダを選択", DESI_DATA_DIR)
        if (!is.na(folder) && dir.exists(folder)) {
            updateTextInput(session, "default_desi_output_dir", value = folder)
        }
    })

    # DESIリセットボタン
    observeEvent(input$reset_desi_defaults, {
        updateTextInput(session, "default_desi_data_folder", value = DEFAULT_DESI_DATA_FOLDER)
        updateTextInput(session, "default_mrm_file", value = DEFAULT_MRM_FILE_PATH)
        updateTextInput(session, "default_desi_output_dir", value = DESI_DATA_DIR)
        showNotification("DESI初期設定をリセットしました", type = "message")
    })

    # DESI適用ボタン
    observeEvent(input$apply_desi_defaults, {
        # データフォルダに適用（DESI解析選択時）
        if (!is.null(input$analysis_method) && input$analysis_method %in% c("desi_v8", "desi_cluster_filter")) {
            new_folder <- input$default_desi_data_folder
            if (!is.null(new_folder) && nchar(new_folder) > 0) {
                updateTextInput(session, "data_folder", value = new_folder)
            }
        }
        # MRMファイルに適用
        new_mrm <- input$default_mrm_file
        if (!is.null(new_mrm) && nchar(new_mrm) > 0) {
            updateTextInput(session, "mrm_path", value = new_mrm)
        }
        showNotification("DESI設定を適用しました", type = "message")
    })

    # ==========================================================================
    # TIMS初期設定ハンドラー
    # ==========================================================================

    # TIMSデフォルトデータフォルダ参照
    observeEvent(input$browse_default_tims_folder, {
        folder <- browse_folder_native(input$default_tims_data_folder, "TIMSデータフォルダを選択", TIMS_DIR)
        if (!is.na(folder) && dir.exists(folder)) {
            updateTextInput(session, "default_tims_data_folder", value = folder)
        }
    })

    # アノテーションファイル参照
    observeEvent(input$browse_default_annotation, {
        file <- browse_file_native(input$default_annotation_csv)
        if (!is.na(file) && file.exists(file)) {
            updateTextInput(session, "default_annotation_csv", value = file)
        }
    })

    # TIMS出力先参照
    observeEvent(input$browse_default_tims_output, {
        folder <- browse_folder_native(input$default_tims_output_dir, "TIMS出力先フォルダを選択", TIMS_DATA_DIR)
        if (!is.na(folder) && dir.exists(folder)) {
            updateTextInput(session, "default_tims_output_dir", value = folder)
        }
    })

    # イオンモード変更時にAdductフィルターを自動選択
    observeEvent(input$default_ion_mode, {
        if (input$default_ion_mode == "Positive") {
            updateCheckboxGroupInput(session, "default_adduct_filter",
                selected = DEFAULT_ADDUCT_POSITIVE
            )
        } else {
            updateCheckboxGroupInput(session, "default_adduct_filter",
                selected = DEFAULT_ADDUCT_NEGATIVE
            )
        }
    })

    # 解析設定内のイオンモード変更時にAdductフィルターを自動選択
    observeEvent(input$ion_mode, {
        if (input$ion_mode == "Positive") {
            updateCheckboxGroupInput(session, "adduct_filter",
                selected = c("+H", "+Na", "+NH4")
            )
        } else {
            updateCheckboxGroupInput(session, "adduct_filter",
                selected = c("-H")
            )
        }
    })

    # 再解析イオンモード変更時にAdductフィルターを自動選択
    observeEvent(input$reanalysis_ion_mode, {
        if (input$reanalysis_ion_mode == "Positive") {
            updateCheckboxGroupInput(session, "reanalysis_adduct_filter",
                selected = c("+H", "+Na", "+NH4")
            )
        } else {
            updateCheckboxGroupInput(session, "reanalysis_adduct_filter",
                selected = c("-H")
            )
        }
    })

    # DESI/TIMSラジオボタンの相互排他性 + 出力先自動更新
    observeEvent(input$analysis_method, {
        if (!is.null(input$analysis_method) && input$analysis_method != "") {
            # DESI選択時、TIMSの選択をクリア
            updateRadioButtons(session, "analysis_method_tims", selected = character(0))
            # DESIの出力先を反映
            updateTextInput(session, "output_dir", value = input$default_desi_output_dir)
        }
    })

    observeEvent(input$analysis_method_tims, {
        if (!is.null(input$analysis_method_tims) && input$analysis_method_tims != "") {
            # TIMS選択時、DESIの選択をクリア
            updateRadioButtons(session, "analysis_method", selected = character(0))
            # TIMSの出力先を反映
            updateTextInput(session, "output_dir", value = input$default_tims_output_dir)
        }
    })

    # TIMSリセットボタン
    observeEvent(input$reset_tims_defaults, {
        updateTextInput(session, "default_tims_data_folder", value = DEFAULT_TIMS_DATA_FOLDER)
        updateTextInput(session, "default_annotation_csv", value = DEFAULT_ANNOTATION_CSV_PATH)
        updateTextInput(session, "default_tims_output_dir", value = TIMS_DATA_DIR)
        updateRadioButtons(session, "default_ion_mode", selected = DEFAULT_ION_MODE)
        updateNumericInput(session, "default_tolerance_mz", value = DEFAULT_TOLERANCE_MZ)
        updateCheckboxGroupInput(session, "default_adduct_filter", selected = DEFAULT_ADDUCT_POSITIVE)
        showNotification("TIMS初期設定をリセットしました", type = "message")
    })

    # TIMS適用ボタン
    observeEvent(input$apply_tims_defaults, {
        # データフォルダに適用（TIMS解析選択時）
        if (!is.null(input$analysis_method_tims) && input$analysis_method_tims %in% c("tims_v8", "tims_cluster_filter")) {
            new_folder <- input$default_tims_data_folder
            if (!is.null(new_folder) && nchar(new_folder) > 0) {
                updateTextInput(session, "data_folder", value = new_folder)
            }
        }
        showNotification("TIMS設定を適用しました", type = "message")
    })

    # 解析手法変更時にデータフォルダのデフォルト値を切り替え
    observeEvent(input$analysis_method,
        {
            if (!is.null(input$analysis_method) && input$analysis_method %in% c("desi_v8", "desi_cluster_filter")) {
                # DESI選択時
                updateTextInput(session, "data_folder", value = input$default_desi_data_folder)
            }
        },
        ignoreInit = TRUE
    )

    observeEvent(input$analysis_method_tims,
        {
            if (!is.null(input$analysis_method_tims) && input$analysis_method_tims %in% c("tims_v8", "tims_cluster_filter")) {
                # TIMS選択時
                updateTextInput(session, "data_folder", value = input$default_tims_data_folder)
            }
        },
        ignoreInit = TRUE
    )

    # ==========================================================================
    # 出力設定ハンドラー
    # ==========================================================================

    # デフォルト出力先参照
    observeEvent(input$browse_default_output, {
        folder <- browse_folder_native(input$default_output_dir, "デフォルト出力先フォルダを選択", APP_BASE_DIR)
        if (!is.na(folder) && dir.exists(folder)) {
            updateTextInput(session, "default_output_dir", value = folder)
        }
    })

    # 出力設定リセット
    observeEvent(input$reset_output_defaults, {
        updateTextInput(session, "default_output_dir", value = APP_BASE_DIR)
        showNotification("出力設定をリセットしました", type = "message")
    })

    # 出力設定適用
    observeEvent(input$apply_output_defaults, {
        new_output <- input$default_output_dir
        if (!is.null(new_output) && nchar(new_output) > 0) {
            updateTextInput(session, "output_dir", value = new_output)
            showNotification("出力設定を適用しました", type = "message")
        }
    })

    # ==========================================================================
    # スクリプト参照ボタン
    # ==========================================================================

    # DESI v8スクリプト参照
    observeEvent(input$browse_desi_v8_script, {
        file <- browse_file_native(input$desi_v8_script_path)
        if (!is.na(file) && file.exists(file)) {
            updateTextInput(session, "desi_v8_script_path", value = file)
        }
    })

    # DESI Cluster Filterスクリプト参照
    observeEvent(input$browse_desi_cluster_script, {
        file <- browse_file_native(input$desi_cluster_filter_script_path)
        if (!is.na(file) && file.exists(file)) {
            updateTextInput(session, "desi_cluster_filter_script_path", value = file)
        }
    })

    # TIMS v8スクリプト参照
    observeEvent(input$browse_tims_v8_script, {
        file <- browse_file_native(input$tims_v8_script_path)
        if (!is.na(file) && file.exists(file)) {
            updateTextInput(session, "tims_v8_script_path", value = file)
        }
    })

    # TIMS Cluster Filterスクリプト参照
    observeEvent(input$browse_tims_cluster_script, {
        file <- browse_file_native(input$tims_cluster_filter_script_path)
        if (!is.na(file) && file.exists(file)) {
            updateTextInput(session, "tims_cluster_filter_script_path", value = file)
        }
    })

    # デフォルトに戻す（全スクリプトパス）
    observeEvent(input$reset_script_paths, {
        updateTextInput(session, "desi_v8_script_path", value = DESI_V8_TEMPLATE_PATH)
        updateTextInput(session, "desi_cluster_filter_script_path", value = DESI_CLUSTER_FILTER_PATH)
        updateTextInput(session, "tims_v8_script_path", value = TIMS_V8_TEMPLATE_PATH)
        updateTextInput(session, "tims_cluster_filter_script_path", value = TIMS_CLUSTER_FILTER_PATH)
        showNotification("スクリプトパスをデフォルトに戻しました", type = "message")
    })
}
