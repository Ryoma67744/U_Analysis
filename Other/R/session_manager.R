# MSI Analysis Application - Session Manager
# セッション管理モジュール

library(jsonlite)

#' セッションデータを保存
#' @param session_data リスト形式のセッションデータ
#' @param output_dir 保存先ディレクトリ
#' @param session_name セッション名（省略時は日時）
#' @return 保存したファイルパス
save_session <- function(session_data, output_dir, session_name = NULL) {
  if (is.null(session_name)) {
    session_name <- format(Sys.time(), "session_%Y%m%d_%H%M%S")
  }
  
  # メタデータ追加
  session_data$meta <- list(
    created_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%S"),
    app_version = "1.0.0"
  )
  
  sessions_dir <- file.path(output_dir, "sessions")
  dir.create(sessions_dir, recursive = TRUE, showWarnings = FALSE)
  
  file_path <- file.path(sessions_dir, paste0(session_name, ".json"))
  write_json(session_data, file_path, pretty = TRUE, auto_unbox = TRUE)
  
  return(file_path)
}

#' セッションデータを読み込み
#' @param session_path JSONファイルパス
#' @return セッションデータ（リスト）
load_session <- function(session_path) {
  if (!file.exists(session_path)) {
    stop("Session file not found: ", session_path)
  }
  
  session_data <- read_json(session_path, simplifyVector = TRUE)
  return(session_data)
}

#' 保存されたセッション一覧を取得
#' @param output_dir 出力ディレクトリ
#' @return セッションファイルのデータフレーム
list_sessions <- function(output_dir) {
  sessions_dir <- file.path(output_dir, "sessions")
  if (!dir.exists(sessions_dir)) {
    return(data.frame(
      name = character(),
      path = character(),
      created_at = character(),
      stringsAsFactors = FALSE
    ))
  }
  
  files <- list.files(sessions_dir, pattern = "\\.json$", full.names = TRUE)
  if (length(files) == 0) {
    return(data.frame(
      name = character(),
      path = character(),
      created_at = character(),
      stringsAsFactors = FALSE
    ))
  }
  
  sessions <- lapply(files, function(f) {
    tryCatch({
      data <- read_json(f, simplifyVector = TRUE)
      list(
        name = tools::file_path_sans_ext(basename(f)),
        path = f,
        created_at = data$meta$created_at %||% "Unknown"
      )
    }, error = function(e) {
      list(name = basename(f), path = f, created_at = "Error")
    })
  })
  
  do.call(rbind, lapply(sessions, as.data.frame, stringsAsFactors = FALSE))
}

#' セッションを削除
#' @param session_path 削除するセッションのパス
delete_session <- function(session_path) {
  if (file.exists(session_path)) {
    file.remove(session_path)
    return(TRUE)
  }
  return(FALSE)
}

# NULL合体演算子
`%||%` <- function(x, y) if (is.null(x)) y else x
