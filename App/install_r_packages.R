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
  "httpuv",   # Seurat -> shiny の間接依存。r2u 再ビルドで取りこぼされ Seurat ロード不可になる事故対策で明示
  "shiny",
  "miniUI",

  # データ操作・IO
  "tidyverse",
  "data.table",
  "readxl",
  "jsonlite",
  "arrow",
  # ver57.1: qs -> qs2。qs 0.27.3 は R 4.6.1 で `undefined symbol: SET_CLOENV`
  #   により dlopen できず（r2u の apt バイナリが 2025-03 ビルドのまま
  #   再ビルドされておらず、Candidate = Installed で更新も来ない）、
  #   ver50.1 以降ずっと gzip の saveRDS にフォールバックしていた。
  #   実測（本番, 1.03GB の Step2）: 保存 162.8 秒 / 読込 29.1 秒。
  "qs2",  # 高速・高圧縮 R オブジェクトシリアライザ (RDS 軽量化用)

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
  "RANN",        # 近傍探索（UMAP診断モジュール embedding_diagnostics.R で使用）
  "future",
  "leiden",
  "leidenbase",  # Seurat v5 の FindClusters(algorithm = 4) で必須
  "aricode",     # ARI/NMI（バッチ補正の生物保存評価, Phase 2）
  "cluster",     # silhouette (ASW)（バッチ補正診断）

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
    # ver3.8: 失敗時に低速 fallback の存在と対処を明示
    message("[警告] presto のインストールに失敗しました。",
            "\n  FindAllMarkers は標準の wilcox 実装にフォールバックします (低速)。",
            "\n  ネットワーク復旧後に Docker image を再ビルドして再試行してください。")
  } else {
    cat("presto のインストールが完了しました。\n")
  }
} else {
  cat("presto は既にインストールされています。\n")
}

# ============================================================
#  ★ ver57.2: 「入っているか」ではなく「ロードできるか」を検査する
#
#  これが無かったために、qs 0.27.3 が R 4.6.1 で
#  `undefined symbol: SET_CLOENV` により dyn.load できない状態に数か月
#  気づけなかった。qs は **確かにインストールされていた** ので、
#  上の installed.packages() ベースの検査はすべて通っていた。
#  壊れるのは実際に読み込む瞬間だけで、そこを誰も見ていなかった。
#
#  さらに、既にインストール済みのパッケージは to_install から除外されるため
#  再インストールも試みられない。つまり壊れたまま固定されていた。
#
#  requireNamespace は名前空間を実際にロードするので、この差を捕まえられる。
#  ここで quit(status = 1) すると Dockerfile の
#  `RUN Rscript /app/App/install_r_packages.R` が失敗し、**壊れたイメージが
#  出荷される前に**ビルドが止まる。
#
#  注意: このステップでビルドが落ちるようになった場合、それは検査が
#  仕事をしている。名指しされたパッケージが本当にロードできない状態なので、
#  検査を外すのではなくパッケージ側を直すこと。
# ============================================================
cat("\nパッケージが実際にロードできるか検査中...\n")
.load_ok <- vapply(packages, function(p) {
  isTRUE(tryCatch(requireNamespace(p, quietly = TRUE), error = function(e) FALSE))
}, logical(1))

if (any(!.load_ok)) {
  broken <- packages[!.load_ok]
  cat(sprintf("\n[エラー] インストールはされているがロードできないパッケージ: %s\n",
              paste(broken, collapse = ", ")))
  for (b in broken) {
    reason <- tryCatch({ loadNamespace(b); "" },
                       error = function(e) conditionMessage(e))
    cat(sprintf("  - %s: %s\n", b, reason))
  }
  cat("\n  `undefined symbol: ...` が出ている場合は R の ABI 不一致です。\n")
  cat("  そのパッケージの apt バイナリが現在の R より古いビルドである可能性が高く、\n")
  cat("  apt-cache policy r-cran-<name> で新しい候補が無いか確認してください。\n")
  quit(status = 1)
}
cat(sprintf("  %d 個すべてロードできました。\n", length(packages)))

cat("\nR パッケージのインストールが完了しました。\n")
