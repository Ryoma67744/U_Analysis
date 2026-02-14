# MSI Analysis Application - Results Viewer
# 結果可視化モジュール

#' 結果フォルダの構造を取得
#' @param result_dir 結果フォルダパス
#' @return フォルダ構造リスト
get_result_structure <- function(result_dir) {
  if (!dir.exists(result_dir)) {
    return(list())
  }
  
  structure <- list(
    root = result_dir,
    subdirs = list()
  )
  
  # 主要なサブディレクトリ
  key_dirs <- c("Harmony", "RPCA", "PCA", "Volcano_Plots", "Volcano_Plots_MRM", 
                "Cluster_Top5_MSI", "PerCluster_Highlight", "RDS_Files")
  
  for (subdir in key_dirs) {
    subdir_path <- file.path(result_dir, subdir)
    if (dir.exists(subdir_path)) {
      images <- list.files(subdir_path, pattern = "\\.(png|jpg|jpeg)$", 
                          full.names = TRUE, recursive = TRUE)
      structure$subdirs[[subdir]] <- list(
        path = subdir_path,
        image_count = length(images),
        images = images
      )
    }
  }
  
  # ルートディレクトリの画像
  root_images <- list.files(result_dir, pattern = "\\.(png|jpg|jpeg)$", 
                            full.names = TRUE, recursive = FALSE)
  structure$root_images <- root_images
  
  return(structure)
}

#' 画像カテゴリを判定
#' @param image_path 画像パス
#' @return カテゴリ名
categorize_image <- function(image_path) {
  filename <- tolower(basename(image_path))
  
  if (grepl("umap", filename)) return("UMAP")
  if (grepl("volcano", filename)) return("Volcano")
  if (grepl("msi|top5", filename)) return("MSI")
  if (grepl("spatial|cluster", filename)) return("Spatial")
  if (grepl("heatmap", filename)) return("Heatmap")
  if (grepl("tic", filename)) return("TIC")
  if (grepl("filter", filename)) return("Filtering")
  
  return("Other")
}

#' 画像一覧をカテゴリ別に整理
#' @param images 画像パスのベクトル
#' @return カテゴリ別リスト
organize_images_by_category <- function(images) {
  if (length(images) == 0) {
    return(list())
  }
  
  categories <- sapply(images, categorize_image)
  
  split(images, categories)
}

#' クラスタ番号を画像パスから抽出
#' @param image_path 画像パス
#' @return クラスタ番号（整数）またはNA
extract_cluster_number <- function(image_path) {
  filename <- basename(image_path)
  
  # パターン: Cluster_0, Cluster_10 など
  match <- regmatches(filename, regexpr("Cluster_\\d+", filename))
  if (length(match) > 0) {
    return(as.integer(gsub("Cluster_", "", match)))
  }
  
  # パターン: cluster_0, cluster_10 など
  match <- regmatches(filename, regexpr("cluster_\\d+", filename, ignore.case = TRUE))
  if (length(match) > 0) {
    return(as.integer(gsub("cluster_", "", match, ignore.case = TRUE)))
  }
  
  return(NA)
}

#' 結果フォルダ内の利用可能なクラスタ番号を取得
#' @param result_dir 結果フォルダパス
#' @return クラスタ番号のベクトル
get_available_clusters <- function(result_dir) {
  images <- list.files(result_dir, pattern = "\\.(png|jpg|jpeg)$", 
                       full.names = TRUE, recursive = TRUE)
  
  clusters <- unique(na.omit(sapply(images, extract_cluster_number)))
  sort(clusters)
}

#' サンプル名を画像パスから抽出
#' @param image_path 画像パス
#' @return サンプル名またはNA
extract_sample_name <- function(image_path) {
  filename <- basename(image_path)
  
  # 一般的なパターンを試行
  # 例: plot_cluster_harmony_251213_Kizu-Embryo-E16.png
  parts <- strsplit(filename, "_")[[1]]
  
  # 日付パターン（6桁の数字で始まる）を探す
  date_idx <- grep("^\\d{6}", parts)
  if (length(date_idx) > 0) {
    # 日付以降を結合してサンプル名とする
    sample_parts <- parts[date_idx[1]:length(parts)]
    sample_name <- paste(sample_parts, collapse = "_")
    sample_name <- gsub("\\.(png|jpg|jpeg)$", "", sample_name)
    return(sample_name)
  }
  
  return(NA)
}

#' 特定クラスタの画像をフィルタ
#' @param images 画像パスのベクトル
#' @param cluster_num クラスタ番号
#' @return フィルタされた画像パス
filter_images_by_cluster <- function(images, cluster_num) {
  if (is.null(cluster_num) || is.na(cluster_num)) {
    return(images)
  }
  
  cluster_nums <- sapply(images, extract_cluster_number)
  images[!is.na(cluster_nums) & cluster_nums == cluster_num]
}

#' Shiny用の画像ギャラリーデータを生成
#' @param images 画像パスのベクトル
#' @param max_per_page ページあたり最大画像数
#' @return ギャラリーデータ
create_gallery_data <- function(images, max_per_page = 20) {
  if (length(images) == 0) {
    return(list(
      images = character(),
      total = 0,
      pages = 0
    ))
  }
  
  list(
    images = images,
    total = length(images),
    pages = ceiling(length(images) / max_per_page),
    per_page = max_per_page
  )
}

#' 画像を更新日時順にソート（新しい順）
#' @param images 画像パスのベクトル
#' @return ソートされた画像パスのベクトル
sort_images_by_time <- function(images) {
  if (length(images) == 0) {
    return(images)
  }
  
  # 各ファイルの更新日時を取得
  file_info <- file.info(images)
  
  # 更新日時が取得できないファイルはスキップ
  valid_idx <- !is.na(file_info$mtime)
  
  if (sum(valid_idx) == 0) {
    return(images)
  }
  
  # 更新日時の新しい順にソート
  images[valid_idx][order(file_info$mtime[valid_idx], decreasing = TRUE)]
}
