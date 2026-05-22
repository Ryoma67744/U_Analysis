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
  "qs",  # 高速・高圧縮 R オブジェクトシリアライザ (RDS 軽量化用)

  # 可視化
  "ggplot2",
  "patchwork",
  "pheatmap",
  "RColorBrewer",
  "ggrepel",
  "ggtext",
  "scales",
  "viridisLite",
  "ragg",

  # 解析
  "dbscan",
  "future",
  "leiden",
  "leidenbase",  # Seurat v5 の FindClusters(algorithm = 4) で必須

  # ユーティリティ
  "tools",
  "tictoc",
  "devtools"
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

# --- GitHub パッケージ (presto) ---
cat("\nGitHub パッケージの確認中...\n")
if (!requireNamespace("presto", quietly = TRUE)) {
  cat("presto をインストール中 (immunogenomics/presto)...\n")
  devtools::install_github("immunogenomics/presto", upgrade = "never")
  if (!requireNamespace("presto", quietly = TRUE)) {
    cat("[警告] presto のインストールに失敗しました。\n")
  } else {
    cat("presto のインストールが完了しました。\n")
  }
} else {
  cat("presto は既にインストールされています。\n")
}

cat("\nR パッケージのインストールが完了しました。\n")
