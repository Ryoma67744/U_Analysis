# MSI Analysis Application - Analysis Runner
# 解析実行エンジン

#' v8 Templateスクリプト用の設定生成
#' @param params パラメータリスト
#' @return 生成したスクリプトパス
generate_v8_config <- function(params, output_dir) {
  template_path <- params$template_path
  if (!file.exists(template_path)) {
    stop("v8 Templateスクリプトが見つかりません: ", template_path)
  }
  
  # 元スクリプトを読み込み
  code <- readLines(template_path, warn = FALSE, encoding = "UTF-8")
  
  # パラメータを置換
  replace_assign <- function(code_vec, var, new_rhs) {
    pat <- paste0("^\\s*", var, "\\s*<-\\s*.*$")
    idx <- grep(pat, code_vec)
    if (length(idx) >= 1) {
      code_vec[idx[1]] <- paste0(var, " <- ", new_rhs)
    }
    code_vec
  }
  
  r_str <- function(x) paste0("\"", gsub("\\\\", "\\\\\\\\", x), "\"")
  
  # データフォルダとサンプル名の置換
  code <- replace_assign(code, "data_folder", r_str(params$data_folder))
  code <- replace_assign(code, "output_dir", r_str(output_dir))
  code <- replace_assign(code, "RESUME_FROM_RDS", if (isTRUE(params$resume_from_rds)) "TRUE" else "FALSE")
  
  # 途中再開用のRDSディレクトリパスを設定（複数ファイル対応）
  if (isTRUE(params$resume_from_rds) && length(params$resume_rds_paths) > 0) {
    # 最初のRDSファイルのディレクトリパスを取得
    rds_dir <- dirname(params$resume_rds_paths[1])
    code <- replace_assign(code, "RESUME_DIR_PATH", r_str(rds_dir))
  }
  
  if (!is.null(params$mrm_path) && params$mrm_path != "") {
    code <- replace_assign(code, "MRM_FILE_PATH", r_str(params$mrm_path))
  }
  
  if (!is.null(params$p_thresh)) {
    code <- replace_assign(code, "DEG_P_THRESH_VAL", as.character(params$p_thresh))
  }
  
  if (!is.null(params$logfc_thresh)) {
    code <- replace_assign(code, "DEG_LOGFC_TH_VAL", as.character(params$logfc_thresh))
  }
  
  # PROJECT_NAME_PREFIX を現在日時で設定
  project_prefix <- paste0("Analysis_", format(Sys.time(), "%Y%m%d_%H%M%S"), "_")
  code <- replace_assign(code, "PROJECT_NAME_PREFIX", r_str(project_prefix))
  
  # sample_names ブロックを置換
  start_pat <- "^\\s*sample_names\\s*<-\\s*c\\s*\\("
  start_idx <- grep(start_pat, code)
  
  if (length(start_idx) >= 1) {
    s <- start_idx[1]
    # 閉じ括弧を探す
    end_idx <- s
    for (i in (s+1):min(s+20, length(code))) {
      if (grepl("^\\s*\\)\\s*$", code[i])) {
        end_idx <- i
        break
      }
    }
    
    # 新しいsample_namesブロック
    sample_names_str <- paste0(
      "sample_names <- c(\n",
      paste0("  \"", params$sample_names, "\"", collapse = ",\n"),
      "\n)"
    )
    
    code <- c(code[1:(s-1)], sample_names_str, code[(end_idx+1):length(code)])
  }
  
  # 一時ファイルに保存
  config_path <- file.path(output_dir, paste0("v8_runtime_", format(Sys.time(), "%Y%m%d_%H%M%S"), ".R"))
  dir.create(dirname(config_path), recursive = TRUE, showWarnings = FALSE)
  writeLines(code, config_path, useBytes = TRUE)
  
  return(config_path)
}

#' Cluster Filterスクリプト用の設定生成
#' @param params パラメータリスト
#' @return 生成したスクリプトパス
generate_cluster_filter_config <- function(params, output_dir) {
  template_path <- params$template_path
  if (!file.exists(template_path)) {
    stop("Cluster Filterスクリプトが見つかりません: ", template_path)
  }
  
  code <- readLines(template_path, warn = FALSE, encoding = "UTF-8")
  
  replace_assign <- function(code_vec, var, new_rhs) {
    pat <- paste0("^\\s*", var, "\\s*<-\\s*.*$")
    idx <- grep(pat, code_vec)
    if (length(idx) >= 1) {
      code_vec[idx[1]] <- paste0(var, " <- ", new_rhs)
    }
    code_vec
  }
  
  r_str <- function(x) paste0("\"", gsub("\\\\", "\\\\\\\\", x), "\"")
  
  code <- replace_assign(code, "RDS_PATH", r_str(params$rds_path))
  code <- replace_assign(code, "ORIGINAL_DATA_FOLDER", r_str(params$original_data_folder))
  code <- replace_assign(code, "FILTER_MODE", r_str(params$filter_mode))
  code <- replace_assign(code, "EXPORT_TXT_DIR", r_str(output_dir))
  code <- replace_assign(code, "V8_OUTPUT_DIR", r_str(output_dir))
  
  # TARGET_CLUSTERSの置換
  target_cl <- paste0("c(", paste(params$target_clusters, collapse = ","), ")")
  code <- replace_assign(code, "TARGET_CLUSTERS", target_cl)
  
  # SAMPLE_NAMESの置換（ブロック形式）
  start_pat <- "^\\s*SAMPLE_NAMES\\s*<-\\s*c\\s*\\("
  start_idx <- grep(start_pat, code)
  
  if (length(start_idx) >= 1 && !is.null(params$sample_names)) {
    s <- start_idx[1]
    end_idx <- s
    for (i in (s+1):min(s+20, length(code))) {
      if (grepl("^\\s*\\)\\s*$", code[i])) {
        end_idx <- i
        break
      }
    }
    
    sample_names_str <- paste0(
      "SAMPLE_NAMES <- c(\n",
      paste0("  \"", params$sample_names, "\"", collapse = ",\n"),
      "\n)"
    )
    
    code <- c(code[1:(s-1)], sample_names_str, code[(end_idx+1):length(code)])
  }
  
  config_path <- file.path(output_dir, paste0("cluster_filter_runtime_", format(Sys.time(), "%Y%m%d_%H%M%S"), ".R"))
  dir.create(dirname(config_path), recursive = TRUE, showWarnings = FALSE)
  writeLines(code, config_path, useBytes = TRUE)
  
  return(config_path)
}

#' Rスクリプトを外部プロセスで実行（非同期）
#' @param script_path 実行するスクリプトパス
#' @param log_callback ログ出力用コールバック関数
#' @param progress_file 進捗ファイルパス
#' @return 実行結果リスト（プロセスオブジェクト含む）
run_r_script <- function(script_path, log_callback = NULL, progress_file = NULL) {
  if (!file.exists(script_path)) {
    return(list(success = FALSE, message = "スクリプトが見つかりません", process = NULL))
  }
  
  start_time <- Sys.time()
  
  if (!is.null(log_callback)) {
    log_callback(paste0("[", format(start_time, "%H:%M:%S"), "] 解析開始: ", basename(script_path)))
  }
  
  result <- tryCatch({
    # sourceで実行（同期）
    source(script_path, encoding = "UTF-8", local = new.env())
    
    end_time <- Sys.time()
    elapsed <- as.numeric(difftime(end_time, start_time, units = "mins"))
    
    if (!is.null(log_callback)) {
      log_callback(paste0("[", format(end_time, "%H:%M:%S"), "] 解析完了 (", round(elapsed, 1), "分)"))
    }
    
    list(success = TRUE, message = "解析が完了しました", elapsed_mins = elapsed, process = NULL)
  }, error = function(e) {
    if (!is.null(log_callback)) {
      log_callback(paste0("[ERROR] ", e$message))
    }
    list(success = FALSE, message = paste("エラー:", e$message), process = NULL)
  })
  
  return(result)
}

#' Rスクリプトを外部プロセスで非同期実行
#' @param script_path 実行するスクリプトパス
#' @param output_dir 出力ディレクトリ（進捗ファイル保存用）
#' @return プロセス情報リスト
start_analysis_process <- function(script_path, output_dir) {
  if (!file.exists(script_path)) {
    return(list(success = FALSE, message = "スクリプトが見つかりません", pid = NULL))
  }
  
  # 進捗ファイルとログファイルのパス
  progress_file <- file.path(output_dir, "analysis_progress.txt")
  log_file <- file.path(output_dir, "analysis_log.txt")
  pid_file <- file.path(output_dir, "analysis_pid.txt")
  status_file <- file.path(output_dir, "analysis_status.txt")
  
  # 初期化
  writeLines("0|準備中|0|1", progress_file)
  writeLines("解析を開始しています...", log_file)
  writeLines("running", status_file)
  
  # Rscriptで外部実行
  # Windowsの場合はshell、他はsystemを使用
  rscript_path <- file.path(R.home("bin"), "Rscript.exe")
  if (!file.exists(rscript_path)) {
    rscript_path <- "Rscript"
  }
  
  # バッチファイルを作成して実行
  batch_script <- file.path(output_dir, "run_analysis.bat")
  batch_content <- sprintf('@echo off
cd /d "%s"
"%s" --vanilla "%s" > "%s" 2>&1
echo finished > "%s"
',
    dirname(script_path),
    rscript_path,
    script_path,
    log_file,
    status_file
  )
  writeLines(batch_content, batch_script)
  
  # 非同期でバッチファイルを実行
  pid <- tryCatch({
    shell(sprintf('start /b cmd /c "%s"', batch_script), wait = FALSE, intern = FALSE)
    # PIDは取得困難なので、ステータスファイルで管理
    Sys.getpid()  # 親プロセスのPID（参考用）
  }, error = function(e) {
    return(NULL)
  })
  
  if (!is.null(pid)) {
    writeLines(as.character(pid), pid_file)
  }
  
  list(
    success = TRUE, 
    message = "解析プロセスを開始しました",
    progress_file = progress_file,
    log_file = log_file,
    status_file = status_file,
    batch_script = batch_script
  )
}

#' 解析プロセスの進捗を取得
#' @param progress_file 進捗ファイルパス
#' @return 進捗情報リスト
get_analysis_progress <- function(progress_file) {
  if (!file.exists(progress_file)) {
    return(list(percent = 0, step = "準備中", current = 0, total = 1))
  }
  
  tryCatch({
    content <- readLines(progress_file, warn = FALSE)
    if (length(content) > 0) {
      parts <- strsplit(content[length(content)], "\\|")[[1]]
      if (length(parts) >= 4) {
        return(list(
          percent = as.integer(parts[1]),
          step = parts[2],
          current = as.integer(parts[3]),
          total = as.integer(parts[4])
        ))
      }
    }
    list(percent = 0, step = "準備中", current = 0, total = 1)
  }, error = function(e) {
    list(percent = 0, step = "準備中", current = 0, total = 1)
  })
}

#' 解析プロセスのログを取得
#' @param log_file ログファイルパス
#' @param last_n 最後のn行
#' @return ログ文字列
get_analysis_log <- function(log_file, last_n = 50) {
  if (!file.exists(log_file)) {
    return("")
  }
  
  tryCatch({
    lines <- readLines(log_file, warn = FALSE, encoding = "UTF-8")
    if (length(lines) > last_n) {
      lines <- lines[(length(lines) - last_n + 1):length(lines)]
    }
    paste(lines, collapse = "\n")
  }, error = function(e) {
    ""
  })
}

#' 解析プロセスのステータスを取得
#' @param status_file ステータスファイルパス
#' @return ステータス文字列 ("running", "finished", "stopped")
get_analysis_status <- function(status_file) {
  if (!file.exists(status_file)) {
    return("unknown")
  }
  
  tryCatch({
    content <- readLines(status_file, warn = FALSE)
    if (length(content) > 0) {
      return(trimws(content[1]))
    }
    "unknown"
  }, error = function(e) {
    "unknown"
  })
}

#' 解析プロセスを停止
#' @param output_dir 出力ディレクトリ
#' @return 停止成功したかどうか
stop_analysis_process <- function(output_dir) {
  status_file <- file.path(output_dir, "analysis_status.txt")
  
  # ステータスファイルに停止フラグを書き込む
  writeLines("stopped", status_file)
  
  # Rscript.exeプロセスを強制終了（Windows）
  tryCatch({
    # taskkillでバッチから起動されたRscriptを停止
    system("taskkill /IM Rscript.exe /F", ignore.stdout = TRUE, ignore.stderr = TRUE)
    TRUE
  }, error = function(e) {
    FALSE
  })
}
