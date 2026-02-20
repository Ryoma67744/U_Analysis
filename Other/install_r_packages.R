#!/usr/bin/env Rscript
# ============================================================
#  MSI Analysis Application - R パッケージ一括インストール
#  setup.bat から自動実行されます
# ============================================================

cat("R パッケージのインストールを開始します...\n\n")

# CRAN リポジトリ設定
repo <- "https://cloud.r-project.org"

# 必要なパッケージ一覧
packages <- c(
  # Seurat 関連
  "Seurat",
  "Matrix",
  "harmony",

  # データ操作・IO
  "tidyverse",
  "data.table",
  "readxl",
  "jsonlite",
  "arrow",

  # 可視化
  "ggplot2",
  "patchwork",
  "pheatmap",
  "RColorBrewer",
  "ggrepel",
  "ggtext",
  "scales",

  # 解析
  "dbscan",

  # ユーティリティ
  "tools",
  "tictoc"
)

# 未インストールのパッケージのみインストール
installed <- installed.packages()[, "Package"]
to_install <- packages[!packages %in% installed]

if (length(to_install) == 0) {
  cat("全てのパッケージは既にインストールされています。\n")
} else {
  cat(sprintf("%d 個のパッケージをインストールします: %s\n\n",
              length(to_install), paste(to_install, collapse = ", ")))

  install.packages(to_install, repos = repo, dependencies = TRUE)

  # インストール結果の確認
  still_missing <- to_install[!to_install %in% installed.packages()[, "Package"]]
  if (length(still_missing) > 0) {
    cat(sprintf("\n[警告] 以下のパッケージのインストールに失敗しました: %s\n",
                paste(still_missing, collapse = ", ")))
    quit(status = 1)
  }
}

cat("\nR パッケージのインストールが完了しました。\n")
