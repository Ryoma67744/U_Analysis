# MSI Analysis Application - Data Manager
# データ管理モジュール

#' データフォルダ内のMSIファイル一覧を取得
#' @param data_folder データフォルダパス
#' @return ファイル名のベクトル（拡張子なし）
list_msi_files <- function(data_folder) {
  if (!dir.exists(data_folder)) {
    return(character())
  }
  
  files <- list.files(data_folder, pattern = "\\.txt$", full.names = FALSE)
  # 拡張子を除去
  tools::file_path_sans_ext(files)
}

#' MSIファイルの妥当性チェック
#' @param file_path ファイルパス
#' @return TRUE/FALSE
validate_msi_file <- function(file_path) {
  if (!file.exists(file_path)) {
    return(list(valid = FALSE, message = "ファイルが見つかりません"))
  }
  
  tryCatch({
    lines <- readLines(file_path, n = 5, warn = FALSE)
    if (length(lines) < 5) {
      return(list(valid = FALSE, message = "ファイルの行数が不足しています (最低5行必要)"))
    }
    
    # 4行目以降がタブ区切りデータかチェック
    data_line <- strsplit(lines[5], "\t")[[1]]
    if (length(data_line) < 4) {
      return(list(valid = FALSE, message = "データ形式が正しくありません"))
    }
    
    return(list(valid = TRUE, message = "OK"))
  }, error = function(e) {
    return(list(valid = FALSE, message = paste("読み込みエラー:", e$message)))
  })
}

#' MRMファイルの読み込み
#' @param mrm_path MRMファイルパス（xlsx/csv）
#' @return MRMデータフレームまたはNULL
load_mrm_file <- function(mrm_path) {
  if (is.null(mrm_path) || mrm_path == "" || !file.exists(mrm_path)) {
    return(NULL)
  }
  
  ext <- tolower(tools::file_ext(mrm_path))
  
  tryCatch({
    if (ext %in% c("xlsx", "xls")) {
      if (requireNamespace("readxl", quietly = TRUE)) {
        df <- as.data.frame(readxl::read_excel(mrm_path))
      } else {
        warning("readxlパッケージがインストールされていません")
        return(NULL)
      }
    } else {
      df <- read.csv(mrm_path, stringsAsFactors = FALSE, check.names = FALSE)
    }
    
    if (nrow(df) == 0) return(NULL)
    
    return(df)
  }, error = function(e) {
    warning("MRMファイル読み込みエラー: ", e$message)
    return(NULL)
  })
}

#' 出力結果フォルダの一覧を取得
#' @param base_dir ベースディレクトリ
#' @return 結果フォルダ情報のデータフレーム
list_result_folders <- function(base_dir) {
  if (!dir.exists(base_dir)) {
    return(data.frame(
      name = character(),
      path = character(),
      date = character(),
      stringsAsFactors = FALSE
    ))
  }
  
  # フォルダ名パターン: *_YYYYMMDD または *_YYYYMMDD_Re-Analysis
  dirs <- list.dirs(base_dir, recursive = FALSE, full.names = TRUE)
  
  result_dirs <- lapply(dirs, function(d) {
    name <- basename(d)
    # RDS_Filesサブフォルダがあれば解析結果フォルダと判定
    if (dir.exists(file.path(d, "RDS_Files")) || 
        length(list.files(d, pattern = "\\.(png|csv)$")) > 0) {
      # 日付部分を抽出 (末尾の8桁数字)
      date_match <- regmatches(name, regexpr("\\d{8}", name))
      date_str <- if (length(date_match) > 0) {
        paste0(substr(date_match, 1, 4), "/", substr(date_match, 5, 6), "/", substr(date_match, 7, 8))
      } else {
        "Unknown"
      }
      return(data.frame(name = name, path = d, date = date_str, stringsAsFactors = FALSE))
    }
    return(NULL)
  })
  
  result_dirs <- result_dirs[!sapply(result_dirs, is.null)]
  if (length(result_dirs) == 0) {
    return(data.frame(name = character(), path = character(), date = character(), stringsAsFactors = FALSE))
  }
  
  do.call(rbind, result_dirs)
}

#' 結果フォルダ内の画像ファイル一覧を取得
#' @param result_dir 結果フォルダパス
#' @param subfolder サブフォルダ名（省略可）
#' @return 画像ファイルパスのベクトル
list_result_images <- function(result_dir, subfolder = NULL) {
  target_dir <- if (!is.null(subfolder)) {
    file.path(result_dir, subfolder)
  } else {
    result_dir
  }
  
  if (!dir.exists(target_dir)) {
    return(character())
  }
  
  list.files(target_dir, pattern = "\\.(png|jpg|jpeg)$", 
             full.names = TRUE, recursive = TRUE)
}
