 # -*- coding: UTF-8 -*-
# ---- PATCH HISTORY (auto-merged from our chat) ----
# [Top5 MSI (Volcano結果 → Cluster_Top5_MSI) のみ変更]
# - ループ主役を「分子(gene/mz)」にして、全サンプルMSIを横並びで結合 → 1分子ユニット化
# - レイアウト: UP Top5 を1行目、DOWN Top5 を2行目（2行×5列マトリクス）
# - タイトル:  {Compound} | {P threshold} | (UP/DOWN)
#     * UP/DOWN は avg_log2FC の符号で決定
#     * P threshold 表示: P < 0.05 / P < 0.01 / P < 0.001 / P ≥ 0.05
#     * タイトル色: P < 0.05=水色, P < 0.01=赤, P < 0.001=黄緑
# -----------------------------------------------

# Analyte 1.txt 等のDESI質量分析イメージング(DESI-MSI)データを Seurat object に変換し、log1p 正規化等の解析を行うスクリプト
# 
# 解析モードの自動判定:
#   - サンプルが1つの場合: "PCA" モードで解析
#   - サンプルが複数の場合: "Harmony" および "RPCA" (Reciprocal PCA) で統合解析を実行
#
# 機能概要 (Features):
#   - データの読み込みとSeuratオブジェクト作成
#   - 大津の二値化(Otsu thresholding)を用いた背景ノイズ(低シグナルスポット)の除去
#   - 空間的な平滑化 (Spatial Smoothing)
#   - log1pによる正規化
#   - 次元圧縮 (PCA/UMAP) とクラスタリング
#   - 可視化:
#       - UMAP (Cluster colored / Sample colored)
#       - Spatial Plot (Cluster map / TIC overlay)
#       - Volcano Plot (MRMリストとの照合機能付き)
#       - Cluster Top5 MSI (ヒートマップ/イオンイメージ)
#         -> 修正: 2行(UP/DOWN) x 5列(Top1-5) のマトリックス配置。各セルに全サンプルの画像を横並びで表示。
#   - 途中再開機能 (Resume from RDS)

# ---- ライブラリの読み込み ----
suppressPackageStartupMessages({
  library(Seurat)
  library(tidyverse)
  library(ggplot2)
  library(patchwork)
  library(Matrix)
  library(harmony)
  library(pheatmap)
  library(RColorBrewer)
  library(tools)    # file_path_sans_ext
  library(ggrepel)  # ラベルの重なりを防ぐ
  library(scales)   # データのスケール調整(squish等)
  
  library(readxl)   # MRM.xlsx 読み込み用
  library(data.table) # fread による高速ファイル読み込み
  # ggtext is optional: used for partial-colored titles/legend text (safe fallback if unavailable)
  if (requireNamespace("ggtext", quietly = TRUE)) library(ggtext)
})

# ---- パフォーマンス最適化: 並列化 & Leidenクラスタリング ----
if (!requireNamespace("future", quietly = TRUE)) install.packages("future", repos = "https://cran.rstudio.com/")
if (!requireNamespace("leiden", quietly = TRUE)) install.packages("leiden", repos = "https://cran.rstudio.com/")
library(future)
plan(multisession, workers = min(4, max(1, parallel::detectCores(logical = FALSE) - 1)))
options(future.globals.maxSize = 2 * 1024^3)  # 2GB制限

# ---- 設定: プロット設定 & 解析パラメータ & 途中再開設定 ----
PLOT_POINT_SIZE <- 1.6
PLOT_POINT_SHAPE <- 15  # 15: 四角形(塗りつぶしなしのピクセル表現に近い)

# 途中再開フラグ(TRUE/FALSE)
# TRUE : RESUME_DIR_PATH 内の .rds ファイルから解析を再開する
#        (時間がかかる前処理や統合処理をスキップする場合に使用)
# FALSE: 最初から解析を行う(RDSは読み込まない)
RESUME_FROM_RDS <- FALSE

# 途中再開時に読み込むRDSファイルのディレクトリパス(RESUME_FROM_RDS=TRUE の場合のみ有効)
# 例: "C:/Users/you/.../RDS_Files" または "C:\\Users\\you\\...\\RDS_Files"
RESUME_DIR_PATH <- "C:\\Users\\Cciia\\Biochem Dropbox\\Biochem's shared workspace\\Workspace\\UMAP\\DESI\\250924_Kizu_Dev_Brain\\250924_Kizu_Dev_Brain20251028"

# 統計解析・MSI抽出の閾値設定
DEG_P_THRESH_VAL <- 0.05  # 有意水準 (p-value)
DEG_LOGFC_TH_VAL <- 0.10  # Log2 Fold Change の閾値
VOLCANO_Y_CAP    <- 100   # Volcano PlotのY軸(-log10 p)の上限クリップ値
LABEL_TOP_N_EACH <- 5     # MRMマッチング等でラベル表示する上位遺伝子数

# ---- MSI 画像生成設定 (Top5 MSI の "コントラスト調整/飽和処理") ----
# 解説:
# - featureごとのシグナル強度の分布から p99.8 などを最大値(vmax)として設定し、外れ値による画像全体の暗化を防ぐ
# - "飽和(saturation)" 判定: vmaxを超える画素が一定割合(MSI_SAT_FRAC)以上ある場合は、閾値を緩和する処理が入る
MSI_USE_NONZERO_QUANTILE <- TRUE  # TRUE: 0より大きい値のみを使ってquantileを計算する(背景の影響を除外)
MSI_Q_LOW  <- 0.02                # vmin(下限値: p2)
MSI_Q_HIGH <- 0.998               # vmax(上限値: p99.8)
MSI_Q_HIGH_RELAX <- c(0.999, 0.9995, 0.9998, 0.9999)  # 飽和時に試行する緩和された上限quantile
MSI_SAT_FRAC <- 0.01              # "飽和"判定閾値: val > vmax となる画素の割合
MSI_TRANS <- "sqrt"               # 変換関数 ("sqrt", "log1p", "identity" 等)


# カラーパレット定義: UMAP等で使用する識別性の高い50色
# ============================================================
# ==== 固定カラーパレット (50色) ====
# ============================================================
# NOTE:
# - colorRampPalette で作成すると隣り合う色が似てしまうため、
# - 視覚的に区別しやすい色を手動または計算で選定したリスト
# - クラスター数が50を超える場合は、自動生成ロジックが使用される

UMAP_DISTINCT_COLORS_50 <- c(
  "#FF2D2D", "#1E5BFF", "#00A650", "#B000FF", "#FF8C00",
  "#00D5FF", "#A52A2A", "#FF1493", "#7A7A7A", "#00C27A",
  "#FFD400", "#2F4F4F", "#8B4513", "#00FF00", "#000000",
  "#FF00FF", "#00FFFF", "#800000", "#008000", "#000080",
  "#808000", "#800080", "#008080", "#FF4500", "#17BECF",
  "#BCBD22", "#9467BD", "#8C564B", "#2CA02C", "#1F77B4",
  "#D62728", "#AEC7E8", "#98DF8A", "#FF9896", "#C49C94",
  "#F7B6D2", "#C7C7C7", "#DBDB8D", "#9EDAE5", "#E41A1C",
  "#377EB8", "#4DAF4A", "#984EA3", "#FF7F00", "#FFFF33",
  "#A65628", "#F781BF", "#999999", "#66A61E", "#E6AB02"
)

# デフォルトのカラーパレットとして設定
my_colors <- UMAP_DISTINCT_COLORS_50


# ============================================================
# ==== Dynamic cluster colors (per-analysis; perceptually separated) ====
# ============================================================
# 目的:
# - クラスタ数が可変でも落ちない（固定色数制限を撤廃）
# - 補間(colorRampPalette)で似た色が増えるのを避け、視覚的に分離しやすい色を選ぶ
# 実装:
# - HCL空間で多数の候補色を生成
# - CIELABに変換し、既に選んだ色集合との「最小距離」が最大になる色を貪欲に選ぶ（max-min）

.make_separated_palette <- function(n,
                                    seed = 42,
                                    n_candidates = 12000,
                                    h_range = c(0, 360),
                                    c_range = c(35, 90),
                                    l_range = c(25, 80)) {
  n <- as.integer(n)
  if (n <= 0) return(character(0))
  if (n == 1) return("#E41A1C")
  set.seed(seed)
  h <- runif(n_candidates, min = h_range[1], max = h_range[2])
  c <- runif(n_candidates, min = c_range[1], max = c_range[2])
  l <- runif(n_candidates, min = l_range[1], max = l_range[2])
  cols <- grDevices::hcl(h = h, c = c, l = l, fixup = TRUE)
  cols <- unique(cols[!is.na(cols)])
  if (length(cols) < n) stop("Not enough candidate colors. Increase n_candidates or relax ranges.")
  rgb <- t(grDevices::col2rgb(cols)) / 255
  lab <- grDevices::convertColor(rgb, from = "sRGB", to = "Lab")
  pick <- integer(n)
  pick[1] <- which.max(lab[,2]^2 + lab[,3]^2)
  min_d2 <- rep(Inf, nrow(lab))
  for (i in 2:n) {
    last <- pick[i-1]
    d2 <- (lab[,1]-lab[last,1])^2 + (lab[,2]-lab[last,2])^2 + (lab[,3]-lab[last,3])^2
    min_d2 <- pmin(min_d2, d2)
    min_d2[pick[1:(i-1)]] <- -Inf
    pick[i] <- which.max(min_d2)
  }
  cols[pick]
}

.assign_cluster_colors <- function(obj, seed = 42) {
  cls <- levels(Idents(obj))
  cols <- .make_separated_palette(length(cls), seed = seed)
  names(cols) <- cls
  cols
}


# ============================================================
# ==== USER EDITABLE SETTINGS (change here only) ==============
# ============================================================ 
# [I/O] データフォルダと出力先の設定
data_folder <- "C:\\Users\\Cciia\\Biochem Dropbox\\Biochem's shared workspace\\Workspace\\UMAP\\DESI\\Data\\250622_Ohashi\\250621_Ohashi_GF-AAs"
output_dir  <- "C:\\Users\\Cciia\\Biochem Dropbox\\Biochem's shared workspace\\Workspace\\UMAP\\DESI\\Data\\250622_Ohashi\\250621_Ohashi_GF-AAs"

# サンプル名のリスト (data_folder内の .txt ファイル名に対応)
sample_names <- c(
  "250621_Ohashi_CV-AAs",
  "250621_Ohashi_GF-AAs"
)

# プロジェクト名の接頭辞 (出力フォルダ名に使用: Prefix + YYYYMMDD)
PROJECT_NAME_PREFIX <- "250621_Ohashi_GF-CV_1"

# MRMリストのファイルパス (化合物同定用)
MRM_FILE_PATH <- "C:\\Users\\Cciia\\Biochem Dropbox\\Biochem's shared workspace\\Workspace\\UMAP\\DESI\\MRM\\MRM.xlsx"

# ============================================================
# ==== USER EDITABLE SETTINGS END =============================
# ============================================================


# ---- I/O パス設定 (moved to USER EDITABLE SETTINGS) ----

today <- format(Sys.Date(), "%Y%m%d")

# 出力先 = output_dir 直下（アプリ側でフォルダを指定済み）
od <- output_dir
dir.create(od, recursive = TRUE, showWarnings = FALSE)

# RDS保存用ディレクトリ作成
rds_od <- file.path(od, "RDS_Files")
dir.create(rds_od, showWarnings = FALSE)

# ============================================================
# ==== 共通関数定義 (Plotting / Helper functions) ================
# ============================================================

# 1. グリッドステップの計算
# - 座標値の diff を計算し、最小の正の値をステップ幅として採用
#   geom_tile の width/height 設定に使用
# - median を採用することで外れ値の影響を軽減
.grid_step <- function(v) {
  vu <- sort(unique(v))
  if (length(vu) < 2) return(1)
  d <- diff(vu)
  d <- d[d > 0]
  if (length(d) == 0) return(1)
  stats::median(d, na.rm = TRUE)
}

# 2. MSIタイルのプロット (機能: コントラスト調整, タイル描画, 凡例制御)
plot_msi_tile <- function(obj, feature, title = NULL,
                          cells = NULL,
                          show_legend = TRUE,
                          title_size = 8,
                          legend_position = c("right", "none"),
                          tile_expand = 1.18,
                          panel_margin_mm = 0.4,
                          vmin_override = NULL,
                          vmax_override = NULL) {
  legend_position <- match.arg(legend_position)
  
  tryCatch({
    df <- FetchData(obj, vars = c("x_coord", "y_coord", feature), cells = cells)
  }, error = function(e) {
    feature_safe <- gsub("-", ".", feature)
    df <- FetchData(obj, vars = c("x_coord", "y_coord", feature_safe), cells = cells)
  })
  
  colnames(df) <- c("x", "y", "val")
  dx <- .grid_step(df$x)
  dy <- .grid_step(df$y)
  if (is.null(title)) title <- feature
  # ---- コントラスト調整 (論理: vmax 計算 + 0除去など) ----
  v_all <- df$val
  v_all <- v_all[is.finite(v_all)]
  if (length(v_all) == 0) v_all <- 0
  
  v_q <- v_all
  if (exists("MSI_USE_NONZERO_QUANTILE") && isTRUE(MSI_USE_NONZERO_QUANTILE)) {
    v_nz <- v_all[v_all > 0]
    if (length(v_nz) >= 20) v_q <- v_nz
  }
  
  q_low  <- if (exists("MSI_Q_LOW"))  MSI_Q_LOW  else 0.02
  q_high <- if (exists("MSI_Q_HIGH")) MSI_Q_HIGH else 0.998
  relax  <- if (exists("MSI_Q_HIGH_RELAX")) MSI_Q_HIGH_RELAX else c(0.999, 0.9995, 0.9998, 0.9999)
  sat_thr <- if (exists("MSI_SAT_FRAC")) MSI_SAT_FRAC else 0.01
  
  vmax <- suppressWarnings(as.numeric(quantile(v_q, probs = q_high, na.rm = TRUE, names = FALSE)))
  if (!is.finite(vmax) || vmax <= 0) vmax <- suppressWarnings(max(v_q, na.rm = TRUE))
  if (!is.finite(vmax) || vmax <= 0) vmax <- 1
  
  # "飽和判定": vmaxを超える画素が一定割合以上ある場合、上限を緩和する
  sat_frac <- suppressWarnings(mean(v_all > vmax, na.rm = TRUE))
  if (is.finite(sat_frac) && sat_frac > sat_thr) {
    for (qh in relax) {
      vmax2 <- suppressWarnings(as.numeric(quantile(v_q, probs = qh, na.rm = TRUE, names = FALSE)))
      if (is.finite(vmax2) && vmax2 > 0) {
        sat2 <- suppressWarnings(mean(v_all > vmax2, na.rm = TRUE))
        vmax <- vmax2
        sat_frac <- sat2
        if (!is.finite(sat_frac) || sat_frac <= sat_thr) break
      }
    }
    # それでも飽和する場合、最終的に max を使用
    if (is.finite(sat_frac) && sat_frac > sat_thr) {
      vmax <- suppressWarnings(max(v_all, na.rm = TRUE))
      if (!is.finite(vmax) || vmax <= 0) vmax <- 1
    }
  }
  
  vmin <- suppressWarnings(as.numeric(quantile(v_q, probs = q_low, na.rm = TRUE, names = FALSE)))
  if (!is.finite(vmin) || vmin < 0) vmin <- 0
  if (vmin >= vmax) vmin <- 0


  # ---- OPTIONAL: override vmin/vmax for shared color scale across panels ----
  if (!is.null(vmin_override) && is.finite(as.numeric(vmin_override))) vmin <- as.numeric(vmin_override)
  if (!is.null(vmax_override) && is.finite(as.numeric(vmax_override)) && as.numeric(vmax_override) > 0) vmax <- as.numeric(vmax_override)
  if (vmin >= vmax) vmin <- 0

  trans_val <- if (exists("MSI_TRANS")) MSI_TRANS else "sqrt"
  if (is.null(trans_val) || trans_val == "" || trans_val == "none") trans_val <- "identity"
  
  p <- ggplot(df, aes(x = x, y = y, fill = val)) +
    # タイル描画: グリッドサイズに合わせて描画 (tile_expandで隙間を調整)
    geom_tile(width = dx * tile_expand, height = dy * tile_expand) +
    scale_x_continuous(expand = c(0, 0)) +
    scale_y_continuous(expand = c(0, 0)) +
    scale_fill_viridis_c(option = "plasma", limits = c(vmin, vmax), oob = scales::squish, trans = trans_val, na.value = "white") +
    scale_y_reverse() +
    coord_fixed(expand = FALSE) +
    ggtitle(title) +
    theme_void() +
    theme(
      plot.background  = element_rect(fill = "white", colour = NA),
      panel.background = element_rect(fill = "white", colour = NA),
      plot.margin = margin(panel_margin_mm, panel_margin_mm, panel_margin_mm, panel_margin_mm, unit = "mm"),
      plot.title  = element_text(hjust = 0.5, size = title_size, face = "bold"),
      legend.position = if (show_legend) legend_position else "none",
      legend.title = element_blank(),
      legend.margin = margin(0, 0, 0, 0, unit = "mm"),
      legend.box.margin = margin(0, 0, 0, 0, unit = "mm")
    )
  
  if (!show_legend) p <- p + theme(legend.position = "none")
  return(p)
}

# 2b. MSI Top5 用の空白プロット関数
.msi_blank <- function() {
  ggplot() + theme_void() +
    theme(plot.background = element_rect(fill = "white", colour = NA),
          plot.margin = margin(0.4, 0.4, 0.4, 0.4, unit = "mm"))
}

# 2c. MSI Top5 パネル構築: UP/DOWN 各最大5つを並べて表示
# 修正: 入ってくるプロットリスト(up_plots/down_plots)は、既に「1つの分子の全サンプルの結合画像」である前提
.build_msi_panel_2x5 <- function(up_plots, down_plots, panel_title = NULL) {
  up_plots   <- if (length(up_plots)   > 0) up_plots   else list()
  down_plots <- if (length(down_plots) > 0) down_plots else list()
  
  # Top 5 に制限
  up_plots   <- up_plots[seq_len(min(5, length(up_plots)))]
  down_plots <- down_plots[seq_len(min(5, length(down_plots)))]
  
  # 5つに満たない場合は空白で埋める
  if (length(up_plots) < 5)   up_plots   <- c(up_plots,   rep(list(.msi_blank()), 5 - length(up_plots)))
  if (length(down_plots) < 5) down_plots <- c(down_plots, rep(list(.msi_blank()), 5 - length(down_plots)))
  
  # byrow=TRUE で 1行目=UP, 2行目=DOWN となるよう配置
  # ここで渡される各プロットは既に「横長の結合画像」になっているため、これを並べるだけで良い
  grid <- patchwork::wrap_plots(c(up_plots, down_plots), ncol = 5, nrow = 2, byrow = TRUE) +
    patchwork::plot_layout(widths = rep(1, 5), heights = rep(1, 2))
  
  if (!is.null(panel_title)) {
    grid <- grid + patchwork::plot_annotation(title = panel_title)
  }
  
  # テーマ適用
  grid <- grid & theme(
    plot.background = element_rect(fill = "white", colour = NA),
    plot.margin = margin(2, 2, 2, 2, unit = "mm"),
    plot.title = element_text(hjust = 0.5, face = "bold", size = 16),
    plot.title.position = "plot"
  )
  return(grid)
}

# 2d.
.msi_sep_line <- function() {
  ggplot() +
    geom_segment(aes(x = 0, xend = 1, y = 0.5, yend = 0.5), linetype = "dashed", linewidth = 0.8, color = "black") +
    coord_cartesian(xlim = c(0, 1), ylim = c(0, 1), expand = FALSE) +
    theme_void() +
    theme(plot.background = element_rect(fill = "white", colour = NA),
          plot.margin = margin(0, 0, 0, 0, unit = "mm"))
}

.feature_to_compound <- function(feature, mrm_df, tolerance = 0.1) {
  # feature 例: "146.1-102.0"
  if (is.null(mrm_df) || nrow(mrm_df) == 0) return(NA_character_)
  mapped <- match_mrm_compound(feature_names = c(feature), mrm_df = mrm_df, tolerance = tolerance)
  if (length(mapped) == 1 && !is.na(mapped[1]) && mapped[1] != feature) return(as.character(mapped[1]))
  return(NA_character_)
}

# .build_msi_grouped_by_feature は今回のレイアウトでは使用しないため削除または未使用とするが、
# 既存コードへの影響を避けるため残しておく

# ggsave の安全版 (limitsize=FALSE + bg指定 + ragg利用試行)
safe_ggsave <- function(filename, plot, width, height, dpi = 300, bg = "white") {
  # raggデバイスはピクセル上限があるため、必要に応じてdpiを下げる処理
  max_dim <- getOption("ragg.max_dim", 50000)
  max_dim_safe <- max(1000, max_dim - 200)
  
  px_w <- as.numeric(width) * as.numeric(dpi)
  px_h <- as.numeric(height) * as.numeric(dpi)
  
  if (is.finite(px_w) && is.finite(px_h)) {
    px_max <- max(px_w, px_h)
    if (px_max > max_dim_safe) {
      dpi_new <- floor(as.numeric(dpi) * (max_dim_safe / px_max))
      dpi_new <- max(72, dpi_new)
      message(sprintf(">> safe_ggsave: dpi %d -> %d (max %d px) : %s",
                      as.integer(dpi), as.integer(dpi_new), as.integer(max_dim_safe), basename(filename)))
      dpi <- dpi_new
    }
  }
  
  dev_fun <- NULL
  if (requireNamespace("ragg", quietly = TRUE)) {
    dev_fun <- ragg::agg_png
  }
  
  tryCatch({
    ggsave(
      filename = filename, plot = plot,
      width = width, height = height, units = "in",
      dpi = dpi, bg = bg, limitsize = FALSE,
      device = dev_fun
    )
  }, error = function(e) {
    message("!! safe_ggsave: ragg device failed; retry with base png. ", e$message)
    ggsave(
      filename = filename, plot = plot,
      width = width, height = height, units = "in",
      dpi = dpi, bg = bg, limitsize = FALSE,
      device = "png"
    )
  })
}


# ============================================================
# ==== Volcano + Top5 MSI (Unified; Harmony/RPCA/PCA対応) ====
# ============================================================
# 機能概要:
# - 各クラスターの Volcano Plot を生成し、有意な変動遺伝子を検出
# - 上位の変動遺伝子について MSI (イオンイメージ) を出力
# - MRMリストがある場合は化合物名を表示
# - 修正: 2行(UP/DOWN) x 5列(Top1-5) のレイアウトで、各セルに全サンプルの画像を横並びにする
run_volcano_and_msi <- function(seu_obj, deg_markers, method_tag, sample_names, od, mrm_df = NULL, method_outdir = NULL) {
  if (is.null(deg_markers) || !is.data.frame(deg_markers) || nrow(deg_markers) == 0) {
    message(">> Volcano/MSI skipped (", method_tag, "): deg_markers is empty.")
    return(invisible(FALSE))
  }
  if (!("cluster" %in% colnames(deg_markers)) || !("gene" %in% colnames(deg_markers))) {
    message(">> Volcano/MSI skipped (", method_tag, "): required columns (cluster,gene) missing.")
    return(invisible(FALSE))
  }
  
  # 出力ディレクトリ作成
  volcano_dir <- file.path(od, "Volcano_Plots", method_tag)
  volcano_labeled_dir <- file.path(od, "Volcano_Plots_MRM", method_tag)
  msi_dir <- file.path(od, "Cluster_Top5_MSI", method_tag)
  dir.create(volcano_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(volcano_labeled_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(msi_dir, recursive = TRUE, showWarnings = FALSE)
  
  # メソッド別のサブディレクトリにもコピーする場合
  if (!is.null(method_outdir)) {
    dir.create(file.path(method_outdir, "Volcano_Plots"), recursive = TRUE, showWarnings = FALSE)
    dir.create(file.path(method_outdir, "Cluster_Top5_MSI"), recursive = TRUE, showWarnings = FALSE)
  }
  
  # assay設定 (MSI抽出用)
  assay_choices <- Seurat::Assays(seu_obj)
  if ("Spatial" %in% assay_choices) {
    DefaultAssay(seu_obj) <- "Spatial"
  } else if ("RNA" %in% assay_choices) {
    DefaultAssay(seu_obj) <- "RNA"
  } else {
    DefaultAssay(seu_obj) <- assay_choices[1]
  }
  
  # サンプルカラムの特定
  sample_col <- .get_sample_col(seu_obj)
  if (is.na(sample_col)) {
    sample_col <- "sample"
  }
  
  # クラスターごとに処理
  for (cl in sort(unique(deg_markers$cluster))) {
    tryCatch({
      df_sub <- deg_markers[deg_markers$cluster == cl, , drop = FALSE]
      if (nrow(df_sub) == 0) return(invisible(NULL))
      
      # p_val_adj が0の場合の補正 (log計算のため)
      if ("p_val_adj" %in% colnames(df_sub)) {
        if (any(df_sub$p_val_adj == 0, na.rm = TRUE)) {
          min_nz <- suppressWarnings(min(df_sub$p_val_adj[df_sub$p_val_adj > 0], na.rm = TRUE))
          if (is.finite(min_nz)) df_sub$p_val_adj[df_sub$p_val_adj == 0] <- min_nz * 0.1
        }
        df_sub$log_p <- -log10(df_sub$p_val_adj)
      } else if ("p_val" %in% colnames(df_sub)) {
        df_sub$log_p <- -log10(df_sub$p_val)
      } else {
        df_sub$log_p <- NA_real_
      }
      
      # Volcanoプロット用グループ分け
      df_sub$color_group <- "NO"
      if ("p_val_adj" %in% colnames(df_sub) && "avg_log2FC" %in% colnames(df_sub)) {
        df_sub$color_group[df_sub$p_val_adj < DEG_P_THRESH_VAL & df_sub$avg_log2FC >  DEG_LOGFC_TH_VAL] <- "UP"
        df_sub$color_group[df_sub$p_val_adj < DEG_P_THRESH_VAL & df_sub$avg_log2FC < -DEG_LOGFC_TH_VAL] <- "DOWN"
      }
      
      # Top5 ヒットの抽出 (ラベル用)
      top_hits <- rbind(
        df_sub %>% dplyr::filter(color_group == "UP")   %>% dplyr::arrange(desc(avg_log2FC)) %>% head(LABEL_TOP_N_EACH),
        df_sub %>% dplyr::filter(color_group == "DOWN") %>% dplyr::arrange(avg_log2FC)       %>% head(LABEL_TOP_N_EACH)
      )
      df_sub$label_text <- ifelse(df_sub$gene %in% top_hits$gene, df_sub$gene, NA)
      
      max_log_p <- suppressWarnings(max(df_sub$log_p, na.rm = TRUE))
      if (!is.finite(max_log_p)) max_log_p <- 1
      y_limit_max <- max_log_p * 1.2
      
      # Volcano (m/z表示)
      p <- ggplot(df_sub, aes(x = avg_log2FC, y = log_p, col = color_group)) +
        geom_point(alpha = 0.6, size = 1.0) +
        scale_color_manual(values = c("UP" = "red", "DOWN" = "blue", "NO" = "gray")) +
        geom_vline(xintercept = c(-DEG_LOGFC_TH_VAL, DEG_LOGFC_TH_VAL), linetype = "dashed") +
        geom_hline(yintercept = -log10(DEG_P_THRESH_VAL), linetype = "dashed") +
        ggrepel::geom_text_repel(aes(label = label_text), size = 3.0, max.overlaps = 20) +
        theme_minimal() + labs(title = paste0(method_tag, " / Cluster ", cl)) +
        coord_cartesian(ylim = c(0, y_limit_max))
      # ggsave(file.path(volcano_dir, paste0("Volcano_Cluster_", cl, ".png")), p, width = 10, height = 8)

      # Volcano (MRM名表示)
      mrm_hits <- rbind(
        df_sub %>% dplyr::filter(color_group == "UP")   %>% dplyr::arrange(desc(avg_log2FC)) %>% head(LABEL_TOP_N_EACH),
        df_sub %>% dplyr::filter(color_group == "DOWN") %>% dplyr::arrange(avg_log2FC)       %>% head(LABEL_TOP_N_EACH)
      )
      if (!is.null(mrm_df) && nrow(mrm_df) > 0) {
        mapped_compounds <- match_mrm_compound(mrm_hits$gene, mrm_df, tolerance = 0.1)
        name_map <- setNames(mapped_compounds, mrm_hits$gene)
        df_sub$label_mrm <- ifelse(df_sub$gene %in% names(name_map), name_map[df_sub$gene], NA)
      } else {
        df_sub$label_mrm <- ifelse(df_sub$gene %in% mrm_hits$gene, df_sub$gene, NA)
      }
      
      p_mrm <- ggplot(df_sub, aes(x = avg_log2FC, y = log_p, col = color_group)) +
        geom_point(alpha = 0.6, size = 1.0) +
        scale_color_manual(values = c("UP" = "red", "DOWN" = "blue", "NO" = "gray")) +
        geom_vline(xintercept = c(-DEG_LOGFC_TH_VAL, DEG_LOGFC_TH_VAL), linetype = "dashed") +
        geom_hline(yintercept = -log10(DEG_P_THRESH_VAL), linetype = "dashed") +
        ggrepel::geom_text_repel(aes(label = label_mrm), size = 2.5, max.overlaps = 20, box.padding = 0.5, force = 2) +
        theme_minimal() + labs(title = paste0("Cluster ", cl, " (MRM Labeled)")) +
        coord_cartesian(ylim = c(0, y_limit_max))
      # ggsave(file.path(volcano_labeled_dir, paste0("Volcano_Cluster_", cl, "_MRM.png")), p_mrm, width = 12, height = 9)


      # ---- Top5 MSI (修正: 分子ごとに全サンプルを横並べ、それを2行5列に配置) ----
      
      # p_val_adjがない場合のフォールバックロジック
      if (!("p_val_adj" %in% colnames(df_sub))) df_sub$p_val_adj <- 1
      
      # UPターゲット抽出 (Top 5)
      up_targets <- df_sub %>% 
        dplyr::filter(p_val_adj < DEG_P_THRESH_VAL, avg_log2FC > 0) %>% 
        dplyr::arrange(desc(avg_log2FC)) %>% 
        head(LABEL_TOP_N_EACH)
      
      # フォールバック (有意なものがなければlogFCだけで抽出)
      if (nrow(up_targets) == 0) {
        up_targets <- df_sub %>% 
          dplyr::filter(avg_log2FC > 0) %>% 
          dplyr::arrange(desc(avg_log2FC)) %>% 
          head(LABEL_TOP_N_EACH)
      }
      
      # DOWNターゲット抽出 (Top 5)
      down_targets <- df_sub %>% 
        dplyr::filter(p_val_adj < DEG_P_THRESH_VAL, avg_log2FC < 0) %>% 
        dplyr::arrange(avg_log2FC) %>% 
        head(LABEL_TOP_N_EACH)
      if (nrow(down_targets) == 0) {
        down_targets <- df_sub %>% 
          dplyr::filter(avg_log2FC < 0) %>% 
          dplyr::arrange(avg_log2FC) %>% 
          head(LABEL_TOP_N_EACH)
      }
      
      # ターゲットが存在する場合のみ描画処理へ
      if (nrow(up_targets) + nrow(down_targets) > 0) {
        msi_cl_dir <- file.path(msi_dir, paste0("Cluster_", cl))
        dir.create(msi_cl_dir, showWarnings = FALSE)
        
        # 細胞リスト作成 (サンプルごと)
        cells_by_sample <- setNames(
          lapply(sample_names, function(sn) {
            if (!sample_col %in% colnames(seu_obj@meta.data)) return(character(0))
            colnames(seu_obj)[seu_obj@meta.data[[sample_col]] == sn]
          }),
          sample_names
        )
        # --- 内部関数: 1つの分子について全サンプルの横並びプロットを作成 ---
        # --- 内部関数: 1分子 = 全サンプル横並び MSI + 構造化タイトル（色付き） ---
        # 変更点:
        # (1) 各切片(サンプル)ごとのタイトル(m/z)を消し、横並び全体に1つのタイトルを付与
        # (2) 同一m/zの横並びでカラースケールを共有（共通vmin/vmax）し、凡例(カラーバー)も1つだけ表示
        create_combined_row_plot <- function(gene, pval, avg_log2FC) {

          # ---- 化合物名（MRM対応）----
          comp <- gene
          if (!is.null(mrm_df) && nrow(mrm_df) > 0) {
            mm <- match_mrm_compound(gene, mrm_df, tolerance = 0.1)
            if (length(mm) == 1 && !is.na(mm) && mm != gene) {
              comp <- as.character(mm)
            }
          }

          # ---- p値 → 表示テキスト & 色 ----
          if (is.na(pval)) {
            p_txt <- "P = NA"
            title_col <- "black"
          } else if (pval < 0.001) {
            p_txt <- "P < 0.001"
            title_col <- "#009E73"   # 黄緑
          } else if (pval < 0.01) {
            p_txt <- "P < 0.01"
            title_col <- "#D55E00"   # 赤
          } else if (pval < 0.05) {
            p_txt <- "P < 0.05"
            title_col <- "#56B4E9"   # 水色
          } else {
            p_txt <- "P ≥ 0.05"
            title_col <- "black"
          }

          # ---- avg_log2FC → UP / DOWN ----
          dir_txt <- if (is.finite(avg_log2FC) && avg_log2FC < 0) "(DOWN)" else "(UP)"

          # ---- 横並び全体に1つのタイトル（要求仕様）----
          title_str <- paste(comp, "|", p_txt, "|", dir_txt)

          # ---- 共有カラースケール（同一m/zでvmin/vmaxを統一）----
          all_cells <- unlist(cells_by_sample, use.names = FALSE)
          all_cells <- all_cells[!is.na(all_cells)]
          if (length(all_cells) == 0) all_cells <- colnames(seu_obj)

          df_all <- tryCatch({
            FetchData(seu_obj, vars = c(gene), cells = all_cells)
          }, error = function(e) {
            gene_safe <- gsub("-", ".", gene)
            FetchData(seu_obj, vars = c(gene_safe), cells = all_cells)
          })
          v_all <- as.numeric(df_all[, 1])
          v_all <- v_all[is.finite(v_all)]
          if (length(v_all) == 0) v_all <- 0

          v_q <- v_all
          if (exists("MSI_USE_NONZERO_QUANTILE") && isTRUE(MSI_USE_NONZERO_QUANTILE)) {
            v_nz <- v_all[v_all > 0]
            if (length(v_nz) >= 20) v_q <- v_nz
          }

          q_low  <- if (exists("MSI_Q_LOW"))  MSI_Q_LOW  else 0.02
          q_high <- if (exists("MSI_Q_HIGH")) MSI_Q_HIGH else 0.998
          relax  <- if (exists("MSI_Q_HIGH_RELAX")) MSI_Q_HIGH_RELAX else c(0.999, 0.9995, 0.9998, 0.9999)
          sat_thr <- if (exists("MSI_SAT_FRAC")) MSI_SAT_FRAC else 0.01

          vmax <- suppressWarnings(as.numeric(quantile(v_q, probs = q_high, na.rm = TRUE, names = FALSE)))
          if (!is.finite(vmax) || vmax <= 0) vmax <- suppressWarnings(max(v_q, na.rm = TRUE))
          if (!is.finite(vmax) || vmax <= 0) vmax <- 1

          sat_frac <- suppressWarnings(mean(v_all > vmax, na.rm = TRUE))
          if (is.finite(sat_frac) && sat_frac > sat_thr) {
            for (qh in relax) {
              vmax2 <- suppressWarnings(as.numeric(quantile(v_q, probs = qh, na.rm = TRUE, names = FALSE)))
              if (is.finite(vmax2) && vmax2 > 0) {
                sat2 <- suppressWarnings(mean(v_all > vmax2, na.rm = TRUE))
                vmax <- vmax2
                sat_frac <- sat2
                if (!is.finite(sat_frac) || sat_frac <= sat_thr) break
              }
            }
            if (is.finite(sat_frac) && sat_frac > sat_thr) {
              vmax <- suppressWarnings(max(v_all, na.rm = TRUE))
              if (!is.finite(vmax) || vmax <= 0) vmax <- 1
            }
          }

          vmin <- suppressWarnings(as.numeric(quantile(v_q, probs = q_low, na.rm = TRUE, names = FALSE)))
          if (!is.finite(vmin) || vmin < 0) vmin <- 0
          if (vmin >= vmax) vmin <- 0

          # ---- 各サンプルのMSI（同一vmin/vmaxで描画、凡例は最後の1枚だけ表示）----
          nS <- length(sample_names)
          plots_row <- lapply(seq_along(sample_names), function(j) {
            sn <- sample_names[[j]]
            cells_sn <- cells_by_sample[[sn]]
            if (is.null(cells_sn) || length(cells_sn) == 0) return(.msi_blank())

            plot_msi_tile(
              seu_obj, feature = gene,
              title = "",                    # 個別タイルのm/zタイトルを消す
              cells = cells_sn,
              show_legend = (j == nS),       # 横並びで凡例(カラーバー)は1つだけ
              legend_position = "right",
              title_size = 6,
              panel_margin_mm = 0.5,
              vmin_override = vmin,
              vmax_override = vmax
            )
          })

          # ---- タイトル（切片の直上）＋ P値色凡例（右上・横並び）----
          # 仕様:
          # (A) タイトルは切片（MSI行）の“すぐ上”に来るよう、余白と高さを極小化
          # (B) タイトル中の「P < ...」部分だけを色付け（ggtextがある場合）
          # (C) 右端切片の右上相当（= タイトル領域右上）に、P値色対応の凡例を横並びで1つだけ表示
          has_ggtext <- requireNamespace("ggtext", quietly = TRUE)

          # 凡例（横並び）
          legend_html <- paste0(
            "<span style='color:#56B4E9; font-weight:700;'>P < 0.05</span>",
            "&nbsp;&nbsp;&nbsp;",
            "<span style='color:#D55E00; font-weight:700;'>P < 0.01</span>",
            "&nbsp;&nbsp;&nbsp;",
            "<span style='color:#009E73; font-weight:700;'>P < 0.001</span>"
          )

          # タイトル（P値部分のみ色）
          if (has_ggtext) {
            title_html <- paste0(
              comp, " | ",
              "<span style='color:", title_col, "; font-weight:700;'>", p_txt, "</span>",
              " | ", dir_txt
            )

            legend_plot <- ggplot(data.frame(x=1, y=0, label=legend_html), aes(x=x, y=y, label=label)) +
              ggtext::geom_richtext(hjust = 1, vjust = 0.5, fill = NA, label.color = NA, size = 3.6) +
              coord_cartesian(xlim=c(0,1), ylim=c(-1,1), expand=FALSE) +
              theme_void() +
              theme(plot.margin = margin(0, 2, 0, 2, unit = "mm"),
                    plot.background = element_rect(fill="white", colour=NA))

            title_plot <- ggplot(data.frame(x=0, y=0, label=title_html), aes(x=x, y=y, label=label)) +
              ggtext::geom_richtext(hjust = 0, vjust = 0.5, fill = NA, label.color = NA, size = 3.9) +
              coord_cartesian(xlim=c(0,1), ylim=c(-1,1), expand=FALSE) +
              theme_void() +
              theme(plot.margin = margin(0, 2, 0, 2, unit = "mm"),
                    plot.background = element_rect(fill="white", colour=NA))
          } else {
            # fallback（ggtextが無い環境でも止まらない）
            legend_plot <- ggplot() +
              annotate("text", x=1, y=0, label="P < 0.05   P < 0.01   P < 0.001",
                       hjust=1, vjust=0.5, colour="black", fontface="bold", size=3.6) +
              coord_cartesian(xlim=c(0,1), ylim=c(-1,1), expand=FALSE) +
              theme_void() +
              theme(plot.margin = margin(0, 2, 0, 2, unit = "mm"),
                    plot.background = element_rect(fill="white", colour=NA))

            title_plot <- ggplot() +
              annotate("text", x=0, y=0, label=title_str,
                       hjust=0, vjust=0.5, colour="black", fontface="bold", size=4.0) +
              coord_cartesian(xlim=c(0,1), ylim=c(-1,1), expand=FALSE) +
              theme_void() +
              theme(plot.margin = margin(0, 2, 0, 2, unit = "mm"),
                    plot.background = element_rect(fill="white", colour=NA))
          }

          # ---- 各サンプルのMSI（同一vmin/vmaxで描画、凡例は最後の1枚だけ表示）----
          nS <- length(sample_names)
          plots_row <- lapply(seq_along(sample_names), function(j) {
            sn <- sample_names[[j]]
            cells_sn <- cells_by_sample[[sn]]
            if (is.null(cells_sn) || length(cells_sn) == 0) return(.msi_blank())

            plot_msi_tile(
              seu_obj, feature = gene,
              title = "",                    # 個別タイルのm/zタイトルを消す
              cells = cells_sn,
              show_legend = (j == nS),       # 横並びでカラーバーは1つだけ
              legend_position = "right",
              title_size = 6,
              panel_margin_mm = 0.1,         # ← タイル側の余白を減らして“直上”に見せる
              vmin_override = vmin,
              vmax_override = vmax
            )
          })

          row_plot <- patchwork::wrap_plots(plots_row, nrow = 1)

          # legend（最上段）→ title（その下）→ MSI行
          combined <- (legend_plot / title_plot / row_plot) +
            patchwork::plot_layout(heights = c(0.99, 0.999, 1))

          return(combined)
        }

        # --- UPリストの画像作成 ---
        up_plots_combined <- list()
        if (nrow(up_targets) > 0) {
          for (i in seq_len(nrow(up_targets))) {
            up_plots_combined[[i]] <- create_combined_row_plot(
              gene = up_targets$gene[i],
              pval = up_targets$p_val_adj[i],
              avg_log2FC = up_targets$avg_log2FC[i]
            )
          }
        }
        
        # --- DOWNリストの画像作成 ---
        down_plots_combined <- list()
        if (nrow(down_targets) > 0) {
          for (i in seq_len(nrow(down_targets))) {
            down_plots_combined[[i]] <- create_combined_row_plot(
              gene = down_targets$gene[i],
              pval = down_targets$p_val_adj[i],
              avg_log2FC = down_targets$avg_log2FC[i]
            )
          }
        }
        
        # --- 2行x5列のパネル作成 (各要素が既に横長画像) ---
        # 既存の .build_msi_panel_2x5 を利用 (中身が横長画像でも動作する)
        # 5つに満たない場合は、.build_msi_panel_2x5 内で .msi_blank() が補完される
        
        panel_title <- NULL
        p_panel <- .build_msi_panel_2x5(up_plots_combined, down_plots_combined, panel_title = panel_title)
        
        # --- 保存 ---
        # 横幅計算: 1つのセル(全サンプル結合)の幅 * 5列
        # 1つのセルの幅 = サンプル数 * 2.5インチ程度
        width_val <- length(sample_names) * 2.5 * 5
        # 高すぎると余白が大きくなるので調整
        width_val <- max(width_val, 15) 
        
        out_png <- file.path(msi_cl_dir, paste0("MSI_Cluster_", cl, "_Top5_Matrix.png"))
        # safe_ggsave(out_png, p_panel, width = width_val, height = 12, dpi = 400)
        
        # メソッド別ディレクトリへのコピー
        if (!is.null(method_outdir) && file.exists(out_png)) {
          to_dir <- file.path(method_outdir, "Cluster_Top5_MSI", paste0("Cluster_", cl))
          dir.create(to_dir, recursive = TRUE, showWarnings = FALSE)
          file.copy(out_png, file.path(to_dir, basename(out_png)), overwrite = TRUE)
        }
      }
    }, error = function(e) {
      message("!! Volcano/MSI failed (", method_tag, ") for cluster ", cl, ": ", e$message)
    })
  }
  
  return(invisible(TRUE))
}


# ---- COMBINED画像の生成: 複数サンプルを並べたプロット ----
.auto_text_sizes <- function(n, main_base = 16, sub_base = 12) {
  # n(プロット数)が増えると文字サイズを小さくする
  main <- max(11, main_base - 0.7 * (n - 1))
  sub  <- max(8,  sub_base  - 0.5 * (n - 1))
  list(main = main, sub = sub)
}

.save_combined_row <- function(plot_list, out_path, main_title,
                               base_w = 6.2, base_h = 6.2, extra_h = 1.0,
                               dpi = 300, bg = "white") {
  # 1行に並べてCOMBINED画像を保存
  n <- length(plot_list)
  if (n == 0) return(invisible(NULL))
  
  sz <- .auto_text_sizes(n)
  
  plots2 <- lapply(plot_list, function(p) {
    p + theme(
      plot.title = element_text(
        size = sz$sub, lineheight = 0.95,
        margin = margin(2, 2, 4, 2, unit = "mm")
      ),
      plot.margin = margin(4, 4, 4, 4, unit = "mm")
    )
  })
  
  comb <- patchwork::wrap_plots(plots2, nrow = 1) +
    patchwork::plot_annotation(
      title = main_title,
      theme = theme(
        plot.title = element_text(hjust = 0.5, face = "bold", size = sz$main,
                                  margin = margin(0, 0, 6, 0, unit = "mm")),
        plot.margin = margin(10, 10, 10, 10, unit = "mm")
      )
    )
  
  # safe_ggsave(out_path, comb, width = base_w * n, height = base_h + extra_h, dpi = dpi, bg = bg)
}


# 3. P値のスター表示 (*, **, ***, ****)
get_p_stars <- function(p) {
  sapply(p, function(x) {
    if (is.na(x)) return("ns")
    if (x < 0.0001) return("****")
    if (x < 0.001)  return("***")
    if (x < 0.01)   return("**")
    if (x < 0.05)   return("*")
    return("ns")
  })
}

# 4. ファイル名をタイトルに追加
add_filename_title <- function(p, obj, prefix_title = NULL) {
  label <- tryCatch(obj@misc$measurement_label, error = function(e) NULL)
  if (is.null(label) || length(label) == 0) {
    label <- tryCatch(unique(obj$sample)[1], error = function(e) "unknown_sample")
  }
  if (is.null(prefix_title)) {
    p + ggtitle(label) + theme(plot.title = element_text(hjust = 0.5, face = "bold"))
  } else {
    p + ggtitle(paste0(prefix_title, "\n", label)) +
      theme(plot.title = element_text(hjust = 0.5, face = "bold"))
  }
}

# ---- MRMリストの読み込みとマッチング処理 ----
# 機能: MRM.xlsx または csv を読み込み、カラム名を正規化する
.load_mrm_table <- function(path) {
  if (!file.exists(path)) return(NULL)
  
  ext <- tolower(tools::file_ext(path))
  df <- NULL
  
  if (ext %in% c("xlsx", "xls")) {
    df <- tryCatch({
      as.data.frame(readxl::read_excel(path))
    }, error = function(e) {
      warning("Failed to read excel MRM file: ", path, " / ", e$message)
      NULL
    })
  } else {
    df <- tryCatch({
      read.csv(path, stringsAsFactors = FALSE, check.names = FALSE)
    }, error = function(e) {
      warning("Failed to read csv MRM file: ", path, " / ", e$message)
      NULL
    })
  }
  
  if (is.null(df) || nrow(df) == 0) return(NULL)
  
  cn_raw <- colnames(df)
  cn <- make.names(cn_raw)
  colnames(df) <- cn
  
  pick_col <- function(cands) {
    hit <- cands[cands %in% cn]
    if (length(hit) > 0) return(hit[1])
    return(NA_character_)
  }
  
  col_comp <- pick_col(c("Compound", "compound", "Name", "Metabolite", "Metabolite.Name", "Analyte", "Analyte.Name"))
  col_p    <- pick_col(c("Parent.m.z", "Parent_m.z", "Parent.mz", "Parent", "Precursor", "Q1", "Q1.m.z", "Precursor.m.z", "Precursor_m.z"))
  col_d    <- pick_col(c("Daughter.m.z", "Daughter_m.z", "Daughter.mz", "Daughter", "Product", "Q3", "Q3.m.z", "Product.m.z", "Product_m.z"))
  
  if (is.na(col_p) || is.na(col_d)) {
    warning("MRM table does not contain usable Parent/Daughter m/z columns. Available columns: ",
            paste(cn_raw, collapse = ", "))
    return(NULL)
  }
  if (is.na(col_comp)) {
    df$Compound <- paste0(df[[col_p]], "-", df[[col_d]])
    col_comp <- "Compound"
  }
  
  out <- data.frame(
    Compound = as.character(df[[col_comp]]),
    Parent.m.z = suppressWarnings(as.numeric(df[[col_p]])),
    Daughter.m.z = suppressWarnings(as.numeric(df[[col_d]])),
    stringsAsFactors = FALSE
  )
  out <- out[!(is.na(out$Parent.m.z) | is.na(out$Daughter.m.z)), , drop = FALSE]
  out
}

# MRMマッチングロジック
match_mrm_compound <- function(feature_names, mrm_df, tolerance = 0.1) {
  if (is.null(mrm_df) || nrow(mrm_df) == 0) return(feature_names)
  # mrm_dfの確認
  required_cols <- c("Compound", "Parent.m.z", "Daughter.m.z")
  
  if (!all(required_cols %in% colnames(mrm_df))) {
    colnames(mrm_df) <- make.names(colnames(mrm_df))
  }
  
  if (!all(c("Parent.m.z", "Daughter.m.z", "Compound") %in% colnames(mrm_df))) {
    warning("MRM CSV does not contain required columns (Compound, Parent m/z, Daughter m/z). Skipping matching.")
    return(feature_names)
  }
  
  mapped_names <- sapply(feature_names, function(feat) {
    # feat形式: "146.1-102"
    parts <- strsplit(feat, "-")[[1]]
    if (length(parts) != 2) return(feat)
    
    p_mz <- as.numeric(parts[1])
    d_mz <- as.numeric(parts[2])
    if (is.na(p_mz) || is.na(d_mz)) return(feat)
    
    # 許容誤差範囲内でマッチング
    hits <- mrm_df %>%
      filter(abs(Parent.m.z - p_mz) < tolerance & abs(Daughter.m.z - d_mz) < tolerance)
    
    if (nrow(hits) > 0) {
      return(paste(unique(hits$Compound), collapse = "/"))
    } else {
      return(feat)
    }
  })
  return(mapped_names)
}


# ---- サンプル列名の取得 (sample / orig.ident) ----
.get_sample_col <- function(obj) {
  md <- obj@meta.data
  if ("sample" %in% colnames(md)) return("sample")
  if ("orig.ident" %in% colnames(md)) return("orig.ident")
  cand <- grep("sample", colnames(md), ignore.case = TRUE, value = TRUE)
  if (length(cand) > 0) return(cand[1])
  return(NA_character_)
}


# ---- Plot関数: UMAPのクラスター表示 ----
plot_umap_cluster_variants <- function(obj, prefix, outdir) {
  p_col <- DimPlot(obj, reduction = "umap", group.by = "seurat_clusters", cols = my_colors) +
    ggtitle(paste0("UMAP: Cluster colored (", prefix, ")")) +
    theme(plot.title = element_text(hjust = 0.5, face = "bold")) + Seurat::NoAxes()
  # ggsave(file.path(outdir, paste0("umap_cluster_colored_", prefix, ".png")),
  #        p_col, width = 7, height = 6, dpi = 300)

  emb <- Embeddings(obj, "umap"); stopifnot(!is.null(emb))
  umap_df <- as.data.frame(emb[, 1:2, drop = FALSE])
  colnames(umap_df)[1:2] <- c("UMAP_1", "UMAP_2")
  umap_df$cluster <- as.factor(Idents(obj))
  centers <- umap_df %>%
    dplyr::group_by(cluster) %>%
    dplyr::summarise(UMAP_1 = mean(UMAP_1), UMAP_2 = mean(UMAP_2), .groups = "drop")
  
  p_lab <- DimPlot(obj, reduction = "umap", group.by = "seurat_clusters", cols = my_colors) +
    geom_text(data = centers, aes(x = UMAP_1, y = UMAP_2, label = cluster),
              color = "black", size = 5, fontface = "bold") +
    ggtitle(paste0("UMAP: Cluster labeled (", prefix, ")")) +
    theme(plot.title = element_text(hjust = 0.5, face = "bold")) + Seurat::NoAxes()
  # ggsave(file.path(outdir, paste0("umap_cluster_with_labels_", prefix, ".png")),
  #        p_lab, width = 7, height = 6, dpi = 300)
}

# ---- Plot関数: UMAPのサンプル別表示 ----
plot_umap_per_sample <- function(obj, sample_names, prefix, outdir) {
  plot_list_col <- list()
  plot_list_lab <- list()
  
  sample_col <- .get_sample_col(obj)
  if (is.na(sample_col)) stop("No sample column found in obj@meta.data (expected 'sample' or 'orig.ident').")
  
  for (sn in sample_names) {
    cells_sn <- colnames(obj)[obj@meta.data[[sample_col]] == sn]
    if (length(cells_sn) == 0) next
    sub <- subset(obj, cells = cells_sn)
    safe_sn <- gsub("[^A-Za-z0-9_-]", "_", sn)
    
    # 1. Cluster Colored
    p_col <- DimPlot(sub, reduction = "umap", group.by = "seurat_clusters", cols = my_colors) +
      theme(plot.title = element_text(hjust = 0.5, face = "bold")) + Seurat::NoAxes()
    p_col <- add_filename_title(p_col, sub,
                                prefix_title = paste0("UMAP: Cluster colored (", prefix, " / ", sn, ")"))
    # ggsave(file.path(outdir, paste0("umap_cluster_colored_", prefix, "_", safe_sn, ".png")),
    #        p_col, width = 7, height = 6, dpi = 300)

    # 2. Cluster Labeled
    emb <- Embeddings(sub, "umap"); stopifnot(!is.null(emb))
    df <- data.frame(UMAP_1 = emb[,1], UMAP_2 = emb[,2], cluster = Idents(sub))
    centers <- df %>% dplyr::group_by(cluster) %>%
      dplyr::summarise(UMAP_1 = mean(UMAP_1), UMAP_2 = mean(UMAP_2), .groups = "drop")
    
    p_lab <- DimPlot(sub, reduction = "umap", group.by = "seurat_clusters", cols = my_colors) +
      geom_text(data = centers, aes(x = UMAP_1, y = UMAP_2, label = cluster),
                color = "black", size = 5, fontface = "bold") +
      theme(plot.title = element_text(hjust = 0.5, face = "bold")) + Seurat::NoAxes()
    p_lab <- add_filename_title(p_lab, sub,
                                prefix_title = paste0("UMAP: Cluster labeled (", prefix, " / ", sn, ")"))
    # ggsave(file.path(outdir, paste0("umap_cluster_with_labels_", prefix, "_", safe_sn, ".png")),
    #        p_lab, width = 7, height = 6, dpi = 300)

    plot_list_col[[sn]] <- p_col
    plot_list_lab[[sn]] <- p_lab
  }
  
  # COMBINED画像の生成
  if (length(plot_list_col) > 0) {
    n_plots <- length(plot_list_col)
    
    .save_combined_row(
      plot_list_col,
      out_path  = file.path(outdir, paste0("umap_cluster_colored_", prefix, "_COMBINED.png")),
      main_title = paste0("Combined UMAP: Cluster colored (", prefix, ")"),
      base_w = 6.2, base_h = 6.2, extra_h = 1.2,
      dpi = 300, bg = "white"
    )
    
    .save_combined_row(
      plot_list_lab,
      out_path  = file.path(outdir, paste0("umap_cluster_with_labels_", prefix, "_COMBINED.png")),
      main_title = paste0("Combined UMAP: Cluster labeled (", prefix, ")"),
      base_w = 6.2, base_h = 6.2, extra_h = 1.2,
      dpi = 300, bg = "white"
    )
  }
}

# ---- Plot関数: クラスターハイライト (UMAP + Spatial + TIC Overlay) ----
# 出力モード設定:
OUTPUT_TIC_ONLY    <- TRUE   # TICのみ画像
OUTPUT_TIC_OVERLAY <- TRUE   # TIC + ハイライトOverlay画像
OUTPUT_COLOR_ONLY  <- FALSE  # 色のみ(TICなし)

# Overlayスタイル設定
# - "tile"   : タイルとして塗りつぶし (半透明)
# - "outline": 輪郭のみ描画 (TICを見やすくする)
TIC_OVERLAY_STYLE <- "tile"

# TICのコントラスト調整パラメータ
TIC_Q_LOW  <- 0.02   # 下限(p2)
TIC_Q_HIGH <- 0.995  # 上限(p99.5)
TIC_TRANS  <- "identity" # 変換

TIC_GRAY_LOW  <- "white"
TIC_GRAY_HIGH <- "black"

TIC_MONO_ALPHA <- 0.30

.plot_spatial_tic_only <- function(md, point_size, point_shape, tic_col = "nCount_Spatial",
                                   raster_alpha = 1,
                                   title = NULL, show_legend = FALSE) {
  # TIC画像の描画
  if (!tic_col %in% colnames(md)) {
    cand <- c("nCount_Spatial", "nCount_RNA", "nCount_integrated")
    tic_col <- cand[cand %in% colnames(md)][1]
  }
  if (is.na(tic_col) || is.null(tic_col)) tic_col <- colnames(md)[1]
  
  df <- md %>%
    dplyr::select(x_coord, y_coord, tic = .data[[tic_col]]) %>%
    dplyr::mutate(tic = as.numeric(tic))
  
  tv <- df$tic
  tv <- tv[is.finite(tv)]
  if (length(tv) == 0) tv <- 0
  ql <- if (exists("TIC_Q_LOW"))  TIC_Q_LOW  else 0.02
  qh <- if (exists("TIC_Q_HIGH")) TIC_Q_HIGH else 0.995
  vmin <- suppressWarnings(as.numeric(quantile(tv, probs = ql, na.rm = TRUE, names = FALSE)))
  vmax <- suppressWarnings(as.numeric(quantile(tv, probs = qh, na.rm = TRUE, names = FALSE)))
  if (!is.finite(vmin)) vmin <- suppressWarnings(min(tv, na.rm = TRUE))
  if (!is.finite(vmax)) vmax <- suppressWarnings(max(tv, na.rm = TRUE))
  if (!is.finite(vmax) || vmax <= vmin) { vmin <- 0; vmax <- 1 }
  
  ttrans <- if (exists("TIC_TRANS")) TIC_TRANS else "sqrt"
  if (is.null(ttrans) || ttrans == "" || ttrans == "none") ttrans <- "identity"
  
  low_col <- if (exists("TIC_GRAY_LOW")) TIC_GRAY_LOW else "white"
  high_col <- if (exists("TIC_GRAY_HIGH")) TIC_GRAY_HIGH else "black"
  
  p <- ggplot(df, aes(x = x_coord, y = y_coord, fill = tic)) +
    geom_raster(interpolate = FALSE, alpha = raster_alpha) +
    scale_fill_gradient(
      low = low_col, high = high_col,
      limits = c(vmin, vmax),
      oob = scales::squish,
      trans = ttrans,
      na.value = "white",
      labels = scales::comma
    ) +
    scale_x_continuous(expand = c(0, 0)) +
    scale_y_continuous(expand = c(0, 0)) +
    scale_y_reverse() +
    coord_fixed(expand = FALSE) +
    theme_void() +
    theme(
      plot.background = element_rect(fill = "white", colour = NA),
      panel.background = element_rect(fill = "white", colour = NA),
      plot.margin = margin(6, 6, 6, 6, unit = "mm"),
      plot.title = element_text(hjust = 0.5, face = "bold", colour = "black"),
      legend.position = if (show_legend) "right" else "none",
      legend.title = element_blank()
    )
  
  if (!is.null(title)) p <- p + ggtitle(title)
  return(p)
}
.plot_spatial_tic_overlay <- function(md, cl, cl_color, point_size, point_shape,
                                      tic_col = "nCount_Spatial",
                                      alpha_hi = 0.55,
                                      title = NULL) {
  # Overlay描画: TICの上に特定のクラスターをハイライト
  if (!tic_col %in% colnames(md)) {
    cand <- c("nCount_Spatial", "nCount_RNA", "nCount_integrated")
    tic_col <- cand[cand %in% colnames(md)][1]
  }
  if (is.na(tic_col) || is.null(tic_col)) tic_col <- colnames(md)[1]
  
  df <- md %>%
    dplyr::select(x_coord, y_coord, seurat_clusters, tic = .data[[tic_col]]) %>%
    dplyr::mutate(tic = as.numeric(tic))
  
  df_hi <- df[as.character(df$seurat_clusters) == as.character(cl), , drop = FALSE]
  
  tv <- df$tic
  tv <- tv[is.finite(tv)]
  if (length(tv) == 0) tv <- 0
  ql <- if (exists("TIC_Q_LOW"))  TIC_Q_LOW  else 0.02
  qh <- if (exists("TIC_Q_HIGH")) TIC_Q_HIGH else 0.995
  vmin <- suppressWarnings(as.numeric(quantile(tv, probs = ql, na.rm = TRUE, names = FALSE)))
  vmax <- suppressWarnings(as.numeric(quantile(tv, probs = qh, na.rm = TRUE, names = FALSE)))
  if (!is.finite(vmin)) vmin <- suppressWarnings(min(tv, na.rm = TRUE))
  if (!is.finite(vmax)) vmax <- suppressWarnings(max(tv, na.rm = TRUE))
  if (!is.finite(vmax) || vmax <= vmin) { vmin <- 0; vmax <- 1 }
  
  ttrans <- if (exists("TIC_TRANS")) TIC_TRANS else "sqrt"
  if (is.null(ttrans) || ttrans == "" || ttrans == "none") ttrans <- "identity"
  
  low_col <- if (exists("TIC_GRAY_LOW")) TIC_GRAY_LOW else "white"
  high_col <- if (exists("TIC_GRAY_HIGH")) TIC_GRAY_HIGH else "black"
  
  p <- ggplot(df, aes(x = x_coord, y = y_coord)) +
    geom_raster(aes(fill = tic), interpolate = FALSE) +
    scale_fill_gradient(
      low = low_col, high = high_col,
      limits = c(vmin, vmax),
      oob = scales::squish,
      trans = ttrans,
      na.value = "white",
      labels = scales::comma
    ) +
    
    {
      ov_style <- if (exists("TIC_OVERLAY_STYLE")) TIC_OVERLAY_STYLE else "tile"
      if (is.null(ov_style) || ov_style == "") ov_style <- "tile"
      
      if (tolower(ov_style) == "outline") {
        geom_point(
          data = df_hi,
          aes(x = x_coord, y = y_coord),
          inherit.aes = FALSE,
          shape = 0,
          color = cl_color,
          size = 1.1,
          stroke = 0.45,
          alpha = 1
        )
      } else {
        geom_raster(
          data = df_hi,
          aes(x = x_coord, y = y_coord),
          inherit.aes = FALSE,
          fill = cl_color,
          alpha = alpha_hi,
          interpolate = FALSE
        )
      }
    } +
    scale_x_continuous(expand = c(0, 0)) +
    scale_y_continuous(expand = c(0, 0)) +
    scale_y_reverse() +
    coord_fixed(expand = FALSE) +
    theme_void() +
    theme(
      plot.background = element_rect(fill = "white", colour = NA),
      panel.background = element_rect(fill = "white", colour = NA),
      plot.margin = margin(6, 6, 6, 6, unit = "mm"),
      plot.title = element_text(hjust = 0.5, face = "bold", colour = "black"),
      legend.position = "none"
    )
  
  if (!is.null(title)) p <- p + ggtitle(title)
  return(p)
}
export_cluster_highlights <- function(obj, prefix, outdir, sample_names = NULL,
                                      grey_col = "grey85",
                                      point_size = PLOT_POINT_SIZE,
                                      point_shape = PLOT_POINT_SHAPE,
                                      OUTPUT_UMAP_HIGHLIGHT_ALLCLUSTERS = TRUE) {
  clusters <- levels(factor(obj$seurat_clusters))
  if (length(clusters) == 0) return(invisible(NULL))
  
  base_dir <- file.path(outdir, "PerCluster_Highlight", prefix)
  dir.create(base_dir, recursive = TRUE, showWarnings = FALSE)
  
  # COMBINED用のプロットリスト
  umap_hi_all <- list()                
  umap_per_sample_rows_all <- list()   
  umap_tic_pair_rows_all <- list()     
  
  for (cl in clusters) {
    cl_dir <- file.path(base_dir, paste0("Cluster_", cl))
    dir.create(cl_dir, showWarnings = FALSE)
    
    # -------------------------
    # (A) UMAP (全体): Highlight
    # -------------------------
    cells_hi <- WhichCells(obj, idents = cl)
    cells_hi_named <- setNames(list(cells_hi), as.character(cl))
    
    p_umap <- DimPlot(
      obj, reduction = "umap",
      cells.highlight = cells_hi_named,
      cols = grey_col,
      cols.highlight = unname(my_colors[as.character(cl)]),
      sizes.highlight = 1.2
    ) +
      ggtitle(paste0("UMAP Highlight: Cluster ", cl, " (", prefix, ")")) +
      scale_color_manual(
        values = c(
          "Unselected" = grey_col,
          "Group_1" = unname(my_colors[as.character(cl)]),
          setNames(unname(my_colors[as.character(cl)]), as.character(cl))
        ),
        labels = function(x) {
          ifelse(x == "Unselected", "Other Cluster", ifelse(x == "Group_1", as.character(cl), x))
        }
      ) +
      Seurat::NoAxes() +
      theme(
        plot.title = element_text(hjust = 0.5, face = "bold"),
        plot.margin = margin(8, 8, 8, 8, unit = "mm")
      )
    
    # safe_ggsave(file.path(cl_dir, paste0("umap_highlight_cluster_", cl, "_", prefix, ".png")),
    #             p_umap, width = 7.6, height = 6.6, dpi = 300)

    umap_hi_all[[as.character(cl)]] <- p_umap
    
    # -------------------------
    # (B) UMAP (サンプル別): Highlight
    # -------------------------
    sample_col <- .get_sample_col(obj)
    if (!is.null(sample_names) && length(sample_names) > 0 && !is.na(sample_col)) {
      umap_s_dir <- file.path(cl_dir, "UMAP_per_sample")
      dir.create(umap_s_dir, showWarnings = FALSE)
      
      pair_dir <- file.path(cl_dir, "UMAPxSpatial_per_sample")
      dir.create(pair_dir, showWarnings = FALSE)
      
      tic_only_list <- list()
      tic_overlay_list <- list()
      color_only_list <- list()
      umap_hi_list <- list()
      
      for (sn in sample_names) {
        cells_sn <- colnames(obj)[obj@meta.data[[sample_col]] == sn]
        if (length(cells_sn) == 0) next
        sub <- subset(obj, cells = cells_sn)
        if (ncol(sub) == 0) next
        
        # このサンプルに該当クラスターが含まれるか確認
        if (!any(as.character(sub$seurat_clusters) == as.character(cl))) next
        
        cells_hi_sub <- WhichCells(sub, idents = cl)
        cells_hi_sub_named <- setNames(list(cells_hi_sub), as.character(cl))
        p_umap_sub <- DimPlot(
          sub, reduction = "umap",
          cells.highlight = cells_hi_sub_named,
          cols = grey_col,
          cols.highlight = unname(my_colors[as.character(cl)]),
          sizes.highlight = 1.2
        ) +
          ggtitle(paste0("UMAP Highlight: Cluster ", cl, " / ", sn, " (", prefix, ")")) +
          scale_color_manual(
            values = c(
              "Unselected" = grey_col,
              "Group_1" = unname(my_colors[as.character(cl)]),
              setNames(unname(my_colors[as.character(cl)]), as.character(cl))
            ),
            labels = function(x) {
              ifelse(x == "Unselected", "Other Cluster", ifelse(x == "Group_1", as.character(cl), x))
            }
          ) +
          Seurat::NoAxes() +
          theme(
            plot.title = element_text(hjust = 0.5, face = "bold"),
            plot.margin = margin(8, 8, 8, 8, unit = "mm")
          )
        
        safe_sn <- gsub("[^A-Za-z0-9_-]", "_", sn)
        # safe_ggsave(file.path(umap_s_dir, paste0("umap_highlight_cluster_", cl, "_", prefix, "_", safe_sn, ".png")),
        #             p_umap_sub, width = 7.6, height = 6.6, dpi = 300)

        umap_hi_list[[sn]] <- p_umap_sub
        
        # -------------------------
        # (C) Spatial (空間分布): TICのみ / Overlay / 色のみ
        # -------------------------
        md <- sub@meta.data
        tic_col <- if ("nCount_Spatial" %in% colnames(md)) "nCount_Spatial" else NA_character_
        cl_color <- unname(my_colors[as.character(cl)])
        
        # TIC only
        if (isTRUE(OUTPUT_TIC_ONLY)) {
          p_tic <- .plot_spatial_tic_only(
            md, point_size = point_size, point_shape = point_shape, tic_col = tic_col,
            title = paste0("TIC only / ", sn, " (", prefix, ")"),
            show_legend = FALSE
          )
          # safe_ggsave(file.path(cl_dir, paste0("spatial_", prefix, "_", safe_sn, "_TIC_only.png")),
          #             p_tic, width = 6.8, height = 8.8, dpi = 600)
          tic_only_list[[sn]] <- p_tic
        }
        
        # TIC overlay (highlight cluster only)
        if (isTRUE(OUTPUT_TIC_OVERLAY)) {
          p_overlay <- .plot_spatial_tic_overlay(
            md, cl = cl, cl_color = cl_color,
            point_size = point_size, point_shape = point_shape, tic_col = tic_col,
            alpha_hi = 0.65,
            title = paste0("TIC + Cluster ", cl, " overlay / ", sn, " (", prefix, ")")
          )
          # safe_ggsave(file.path(cl_dir, paste0("spatial_highlight_cluster_", cl, "_", prefix, "_", safe_sn, "_TIC_overlay.png")),
          #             p_overlay, width = 6.8, height = 8.8, dpi = 600)
          tic_overlay_list[[sn]] <- p_overlay
          
          # UMAPとSpatialを横並び
          p_pair <- (p_umap_sub | p_overlay) +
            patchwork::plot_annotation(title = paste0("UMAP & Spatial (Cluster ", cl, ") / ", sn, " (", prefix, ")")) &
            theme(
              plot.title = element_text(hjust = 0.5, face = "bold"),
              plot.margin = margin(8, 8, 8, 8, unit = "mm")
            )
          # safe_ggsave(file.path(pair_dir, paste0("UMAPxSpatial_cluster_", cl, "_", prefix, "_", safe_sn, ".png")),
          #             p_pair, width = 14.6, height = 7.2, dpi = 300)
        }

        # Color only
        if (isTRUE(OUTPUT_COLOR_ONLY)) {
          md$.__hi__ <- ifelse(as.character(md$seurat_clusters) == as.character(cl), "HIGHLIGHT", "OTHER")
          pal <- c("OTHER" = grey_col, "HIGHLIGHT" = cl_color)
          
          p_sp <- ggplot(md, aes(x = x_coord, y = y_coord, color = .__hi__)) +
            geom_point(size = point_size, shape = point_shape, alpha = 1) +
            scale_color_manual(values = pal, breaks = c("HIGHLIGHT", "OTHER"),
                               labels = c(paste0("Cluster ", cl), "Other")) +
            scale_y_reverse() + coord_fixed(expand = FALSE) + theme_void() +
            ggtitle(paste0("Spatial Highlight (color-only): Cluster ", cl, " / ", sn, " (", prefix, ")")) +
            theme(
              plot.title = element_text(hjust = 0.5, face = "bold"),
              legend.position = "right",
              plot.margin = margin(8, 8, 8, 8, unit = "mm"),
              plot.background = element_rect(fill = "white", colour = NA)
            )
          
          # safe_ggsave(file.path(cl_dir, paste0("spatial_highlight_cluster_", cl, "_", prefix, "_", safe_sn, "_color_only.png")),
          #             p_sp, width = 6.8, height = 8.8, dpi = 600)
          color_only_list[[sn]] <- p_sp
        }
      }
      
      # ---- COMBINED: UMAP_per_sample (Cluster Highlight) ----
      if (length(umap_hi_list) > 0) {
        n_u <- length(umap_hi_list)
        sz <- .auto_text_sizes(n_u)
        umap_hi_list2 <- lapply(umap_hi_list, function(p) {
          p + theme(
            plot.title = element_text(size = sz$sub, lineheight = 0.95,
                                      margin = margin(2, 2, 4, 2, unit = "mm")),
            plot.margin = margin(4, 4, 4, 4, unit = "mm")
          )
        })
        
        p_umap_comb <- patchwork::wrap_plots(umap_hi_list2, nrow = 1) +
          patchwork::plot_annotation(
            title = paste0("Combined UMAP per-sample (Cluster ", cl, ") / ", prefix),
            theme = theme(
              plot.title = element_text(hjust = 0.5, face = "bold", size = sz$main,
                                        margin = margin(0, 0, 6, 0, unit = "mm")),
              plot.margin = margin(10, 10, 10, 10, unit = "mm")
            )
          )
        
        # safe_ggsave(
        #   file.path(umap_s_dir, paste0("umap_highlight_cluster_", cl, "_", prefix, "_COMBINED.png")),
        #   p_umap_comb,
        #   width = 6.2 * n_u, height = 7.4, dpi = 300
        # )

        umap_per_sample_rows_all[[as.character(cl)]] <- p_umap_comb
      }
      
      # ---- COMBINED: Spatial Plots ----
      if (length(tic_only_list) > 0 && isTRUE(OUTPUT_TIC_ONLY)) {
        n_plots <- length(tic_only_list)
        p_comb <- patchwork::wrap_plots(tic_only_list, nrow = 1) +
          patchwork::plot_annotation(title = paste0("Combined TIC only (Cluster ", cl, ") / ", prefix)) &
          theme(plot.title = element_text(hjust = 0.5, face = "bold"),
                plot.margin = margin(8, 8, 8, 8, unit = "mm"))
        # safe_ggsave(file.path(cl_dir, paste0("spatial_", prefix, "_COMBINED_TIC_only.png")),
        #             p_comb, width = 6.8 * n_plots, height = 8.8, dpi = 600)
      }

      if (length(tic_overlay_list) > 0 && isTRUE(OUTPUT_TIC_OVERLAY)) {
        n_plots <- length(tic_overlay_list)
        p_comb <- patchwork::wrap_plots(tic_overlay_list, nrow = 1) +
          patchwork::plot_annotation(title = paste0("Combined TIC overlay (Cluster ", cl, ") / ", prefix)) &
          theme(plot.title = element_text(hjust = 0.5, face = "bold"),
                plot.margin = margin(8, 8, 8, 8, unit = "mm"))
        # safe_ggsave(file.path(cl_dir, paste0("spatial_highlight_cluster_", cl, "_", prefix, "_COMBINED_TIC_overlay.png")),
        #             p_comb, width = 6.8 * n_plots, height = 8.8, dpi = 600)
      }

      # ---- COMBINED (2-row): UMAP (top) + TIC overlay (bottom) ----
      if (length(umap_hi_list) > 0 && length(tic_overlay_list) > 0 && isTRUE(OUTPUT_TIC_OVERLAY)) {
        keys <- sample_names[sample_names %in% names(umap_hi_list) & sample_names %in% names(tic_overlay_list)]
        if (length(keys) > 0) {
          n_plots <- length(keys)
          sz <- .auto_text_sizes(n_plots)
          
          top_plots <- lapply(keys, function(sn) {
            umap_hi_list[[sn]] + ggtitle(sn) +
              theme(
                plot.title = element_text(size = sz$sub, face = "bold", hjust = 0.5),
                plot.margin = margin(4, 4, 4, 4, unit = "mm")
              )
          })
          
          bottom_plots <- lapply(keys, function(sn) {
            tic_overlay_list[[sn]] + ggtitle(sn) +
              theme(
                plot.title = element_text(size = sz$sub, face = "bold", hjust = 0.5, colour = "black"),
                plot.margin = margin(4, 4, 4, 4, unit = "mm")
              )
          })
          
          p_top <- patchwork::wrap_plots(top_plots, nrow = 1)
          p_bottom <- patchwork::wrap_plots(bottom_plots, nrow = 1)
          
          p_grid <- (p_top / p_bottom) +
            patchwork::plot_layout(heights = c(1, 1)) +
            patchwork::plot_annotation(
              title = paste0("UMAP (top) + TIC overlay (bottom) / Cluster ", cl, " / ", prefix),
              theme = theme(
                plot.title = element_text(hjust = 0.5, face = "bold", size = sz$main,
                                          margin = margin(0, 0, 6, 0, unit = "mm")),
                plot.margin = margin(10, 10, 10, 10, unit = "mm")
              )
            )
          
          # safe_ggsave(
          #   file.path(cl_dir, paste0("UMAPtop_TICoverlaybottom_cluster_", cl, "_", prefix, "_COMBINED.png")),
          #   p_grid,
          #   width = 6.2 * n_plots, height = 13.6, dpi = 400
          # )
          umap_tic_pair_rows_all[[as.character(cl)]] <- p_grid
        }
      }
      
      if (length(color_only_list) > 0 && isTRUE(OUTPUT_COLOR_ONLY)) {
        n_plots <- length(color_only_list)
        p_comb <- patchwork::wrap_plots(color_only_list, nrow = 1) +
          patchwork::plot_annotation(title = paste0("Combined Spatial (color-only) (Cluster ", cl, ") / ", prefix)) &
          theme(plot.title = element_text(hjust = 0.5, face = "bold"),
                plot.margin = margin(8, 8, 8, 8, unit = "mm"))
        # safe_ggsave(file.path(cl_dir, paste0("spatial_highlight_cluster_", cl, "_", prefix, "_", safe_sn, "_color_only.png")),
        #             p_comb, width = 6.8 * n_plots, height = 8.8, dpi = 600)
      }
    }
  }
  
  # ---- SUMMARY: 全クラスターの一覧画像 ----
  if (isTRUE(OUTPUT_UMAP_HIGHLIGHT_ALLCLUSTERS) && length(umap_hi_all) > 0) {
    n_all <- length(umap_hi_all)
    ncol <- min(5, n_all)
    nrow <- ceiling(n_all / ncol)
    p_all <- patchwork::wrap_plots(umap_hi_all, ncol = ncol) +
      patchwork::plot_annotation(title = paste0("UMAP Highlight (all clusters) / ", prefix)) &
      theme(plot.title = element_text(hjust = 0.5, face = "bold"),
            plot.margin = margin(8, 8, 8, 8, unit = "mm"))
    # safe_ggsave(file.path(base_dir, paste0("UMAP_Highlight_", prefix, "_ALLclusters.png")),
    #             p_all, width = 6.2 * ncol, height = 5.8 * nrow, dpi = 300)
  }

  if (length(umap_per_sample_rows_all) > 0) {
    rows <- umap_per_sample_rows_all[clusters]
    rows <- rows[!sapply(rows, is.null)]
    if (length(rows) > 0) {
      p_big <- patchwork::wrap_plots(rows, ncol = 1) +
        patchwork::plot_annotation(title = paste0("UMAP per-sample (all clusters) / ", prefix)) &
        theme(plot.title = element_text(hjust = 0.5, face = "bold"),
              plot.margin = margin(8, 8, 8, 8, unit = "mm"))
      # safe_ggsave(file.path(base_dir, paste0("UMAP_per_sample_", prefix, "_ALLclusters.png")),
      #             p_big, width = 6.2 * max(1, length(sample_names)), height = 6.2 * length(rows), dpi = 300)
    }
  }

  if (length(umap_tic_pair_rows_all) > 0) {
    rows2 <- umap_tic_pair_rows_all[clusters]
    rows2 <- rows2[!sapply(rows2, is.null)]
    if (length(rows2) > 0) {
      p_big2 <- patchwork::wrap_plots(rows2, ncol = 1) +
        patchwork::plot_annotation(title = paste0("UMAP(top)+TIC overlay(bottom): all clusters / ", prefix)) &
        theme(plot.title = element_text(hjust = 0.5, face = "bold"),
              plot.margin = margin(8, 8, 8, 8, unit = "mm"))
      
      out_legacy <- file.path(base_dir, paste0("UMAPtop_TICoverlaybottom_", prefix, "_ALLclusters.png"))
      safe_ggsave(out_legacy,
                  p_big2,
                  width = 6.2 * max(1, length(sample_names)),
                  height = 13.6 * length(rows2),
                  dpi = 300)
    }
  }
  
  invisible(TRUE)
}


# ---- DESIデータの読み込み関数 ----
read_desi_data <- function(file_path, sample_prefix = NULL) {
  cat("Reading DESI data from:", file_path, "\n")
  # ヘッダー4行は従来通りreadLinesで取得
  header_lines <- readLines(file_path, n = 4, warn = FALSE)
  if (length(header_lines) < 4) stop("データファイルの行数が不足しています: ", file_path)
  metabolite_numbers <- trimws(strsplit(header_lines[2], "\t")[[1]])
  pre_masses <- trimws(strsplit(header_lines[3], "\t")[[1]])
  post_masses <- trimws(strsplit(header_lines[4], "\t")[[1]])

  metabolite_numbers <- metabolite_numbers[nzchar(metabolite_numbers)]
  pre_masses <- pre_masses[nzchar(pre_masses)]
  post_masses <- post_masses[nzchar(post_masses)]

  metabolite_names <- paste(pre_masses, post_masses, sep = "-")

  # [P5] データ部分はfreadで一括高速読込
  data_df <- data.table::fread(file_path, sep = "\t", skip = 4,
                                header = FALSE, fill = TRUE,
                                data.table = FALSE)
  if (nrow(data_df) == 0) stop("データ行が存在しません: ", file_path)

  # 空列を除去（freadがタブ区切りで余分な列を読む場合）
  data_df <- data_df[, colSums(!is.na(data_df) & data_df != "") > 0, drop = FALSE]

  data_df[, 1] <- as.numeric(data_df[, 1])
  data_df[, 2] <- as.numeric(data_df[, 2])
  data_df[, 3] <- as.numeric(data_df[, 3])

  metabolite_cols <- 4:(3 + length(metabolite_names))
  for(col in metabolite_cols) {
    if(col <= ncol(data_df)) data_df[, col] <- as.numeric(data_df[, col])
  }
  
  spot_names <- paste0("Spot_", data_df[, 1])
  if (!is.null(sample_prefix)) {
    safe_prefix <- gsub("[^A-Za-z0-9_\\-]", "_", sample_prefix)
    spot_names <- paste(safe_prefix, spot_names, sep = "_")
  }
  
  coordinates <- data.frame(
    spot_index = data_df[, 1],
    x = data_df[, 2],
    y = data_df[, 3],
    spot_id = spot_names,
    row.names = spot_names
  )
  
  count_data <- data_df[, metabolite_cols, drop = FALSE]
  count_data[is.na(count_data)] <- 0
  
  count_matrix <- t(as.matrix(count_data))
  colnames(count_matrix) <- spot_names
  rownames(count_matrix) <- metabolite_names[1:nrow(count_matrix)]
  rownames(count_matrix) <- make.unique(rownames(count_matrix))
  count_matrix <- as(count_matrix, "dgCMatrix")
  
  return(list(
    count_matrix = count_matrix,
    coordinates = coordinates,
    metabolite_names = metabolite_names[1:nrow(count_matrix)]
  ))
}

# ---- 空間平滑化 (Spatial Smoothing) ---- [P2: 距離行列事前計算で高速化]
spatial_smooth_seurat <- function(seurat_obj, radius, assay = "Spatial", layer = "data",
                                  method = "mean", weight_function = "uniform", sigma = NULL) {
  if (is.null(sigma)) sigma <- radius / 3
  coords <- data.frame(x = seurat_obj$x_coord, y = seurat_obj$y_coord, row.names = colnames(seurat_obj))
  count_data <- LayerData(seurat_obj[[assay]], layer = layer)

  n_spots <- ncol(count_data)
  n_features <- nrow(count_data)
  smoothed_data <- matrix(0, nrow = n_features, ncol = n_spots,
                          dimnames = list(rownames(count_data), colnames(count_data)))

  # 距離行列を1回だけ計算（50,000スポット以下: 高速、超過: メモリ節約のためスポットごと）
  use_dist_mat <- (n_spots <= 50000)
  if (use_dist_mat) {
    dist_mat <- as.matrix(dist(coords))
  }

  for (i in 1:n_spots) {
    if (use_dist_mat) {
      distances <- dist_mat[i, ]
    } else {
      distances <- sqrt((coords$x - coords$x[i])^2 + (coords$y - coords$y[i])^2)
    }
    within_radius <- which(distances <= radius)
    if (length(within_radius) == 0) next

    if (method == "mean") {
      weights <- rep(1, length(within_radius))
    } else {
      if (weight_function == "gaussian") {
        weights <- exp(-(distances[within_radius]^2) / (2 * sigma^2))
      } else if (weight_function == "inverse_distance") {
        weights <- 1 / (distances[within_radius] + 1e-10)
        weights[distances[within_radius] == 0] <- max(weights) * 10
      } else {
        weights <- rep(1, length(within_radius))
      }
    }

    weights <- weights / sum(weights)
    neighbor_data <- count_data[, within_radius, drop = FALSE]
    if (length(within_radius) == 1) {
      smoothed_data[, i] <- as.vector(neighbor_data)
    } else {
      smoothed_data[, i] <- as.vector(neighbor_data %*% weights)
    }
  }

  smoothed_seurat <- seurat_obj
  smoothed_data <- as(smoothed_data, "dgCMatrix")
  if (layer == "data") {
    smoothed_seurat@assays[[assay]]@layers$data <- smoothed_data
  } else {
    smoothed_seurat@assays[[assay]]@layers$counts <- smoothed_data
  }
  return(smoothed_seurat)
}

visualize_spatial_smoothing <- function(original_seurat, smoothed_seurat, features = NULL,
                                        assay = "Spatial", layer = "data", point_size = 0.3) {
  if (is.null(features)) features <- head(rownames(original_seurat), 4)
  coords <- data.frame(x = original_seurat$x_coord, y = original_seurat$y_coord)
  plots <- list()
  
  for (feature in features) {
    original_data <- LayerData(original_seurat[[assay]], layer = layer)[feature, ]
    smoothed_data <- LayerData(smoothed_seurat[[assay]], layer = layer)[feature, ]
    plot_df <- rbind(
      data.frame(x = coords$x, y = coords$y, count = as.numeric(original_data), type = "Original"),
      data.frame(x = coords$x, y = coords$y, count = as.numeric(smoothed_data), type = "Smoothed")
    )
    plots[[feature]] <- ggplot(plot_df, aes(x = x, y = y, color = count)) +
      geom_point(size = point_size, alpha = 0.8) +
      scale_color_viridis_c() +
      scale_y_reverse() +
      coord_fixed() +
      facet_wrap(~type, ncol = 2) +
      theme_minimal() +
      labs(title = feature) +
      theme(legend.position = "bottom")
  }
  return(wrap_plots(plots, ncol = 2))
}

# ---- 背景除去フィルタ (Otsu Thresholding) ----
calculate_otsu_threshold <- function(data, n_bins = 256) {
  if (length(na.omit(unique(data))) <= 1) {
    fallback <- median(data, na.rm = TRUE)
    return(list(threshold = fallback, threshold_index = NA, bin_centers = NA, sigma_between = NA, histogram = NA))
  }
  hist_result <- hist(data, breaks = n_bins, plot = FALSE)
  counts <- hist_result$counts
  breaks <- hist_result$breaks
  bin_centers <- (breaks[-1] + breaks[-length(breaks)]) / 2
  total_pixels <- sum(counts)
  p <- counts / total_pixels
  P1 <- cumsum(p); P2 <- 1 - P1
  mu <- sum(bin_centers * p)
  mu1 <- cumsum(bin_centers * p) / P1
  mu2 <- (mu - cumsum(bin_centers * p)) / P2
  mu1[is.na(mu1)] <- 0; mu2[is.na(mu2)] <- 0
  sigma_between <- P1 * P2 * (mu1 - mu2)^2
  optimal_index <- which.max(sigma_between)
  optimal_threshold <- bin_centers[optimal_index]
  return(list(
    threshold = optimal_threshold, threshold_index = optimal_index,
    bin_centers = bin_centers, sigma_between = sigma_between, histogram = hist_result
  ))
}

filter_low_count_spots <- function(seurat_obj, method = "otsu", manual_threshold = NULL,
                                   use_log_scale = TRUE, n_bins = 256, plot_results = TRUE, sample_name="", outdir = od) {
  cat("\n=== Spot Filtering: Removing Low Count Spots ===\n")
  total_counts <- colSums(GetAssayData(seurat_obj, layer = "counts"))
  
  if (use_log_scale) {
    data_for_threshold <- log10(total_counts + 1)
    scale_name <- "log10(counts + 1)"
  } else {
    data_for_threshold <- total_counts
    scale_name <- "raw counts"
  }
  
  if (method == "otsu") {
    otsu_result <- calculate_otsu_threshold(data_for_threshold, n_bins = n_bins)
    threshold <- otsu_result$threshold
    threshold_original <- if (use_log_scale) 10^threshold - 1 else threshold
  } else if (method == "manual" && !is.null(manual_threshold)) {
    if (use_log_scale) {
      threshold_original <- manual_threshold
      threshold <- log10(manual_threshold + 1)
    } else {
      threshold <- manual_threshold
      threshold_original <- threshold
    }
  } else {
    stop("Please specify either 'otsu' method or 'manual' method.")
  }
  
  spots_to_keep <- if (use_log_scale) total_counts > threshold_original else total_counts > threshold
  
  n_original <- length(total_counts)
  n_filtered <- sum(spots_to_keep)
  n_removed  <- n_original - n_filtered
  cat("Original spots:", n_original, "| Kept:", n_filtered, "| Removed:", n_removed, "\n")
  
  filtered_seurat <- seurat_obj[, spots_to_keep]
  
  if (plot_results) {
    plot_data <- data.frame(
      spot_id = names(total_counts), total_counts = total_counts,
      log_counts = log10(total_counts + 1), kept = spots_to_keep,
      x_coord = seurat_obj$x_coord, y_coord = seurat_obj$y_coord
    )
    if (use_log_scale) {
      plot_data$plot_counts <- plot_data$log_counts
      x_label <- "Log10(Total Counts + 1)"
      plot_threshold <- threshold
      subtitle_txt <- paste(round(threshold, 3), "(log),", round(threshold_original, 1), "(original)")
    } else {
      plot_data$plot_counts <- plot_data$total_counts
      x_label <- "Total Counts"
      plot_threshold <- threshold
      subtitle_txt <- as.character(round(threshold, 1))
    }
    
    p1 <- ggplot(plot_data, aes(x = plot_counts, fill = kept)) +
      geom_histogram(bins = 100, alpha = 0.7, position = "identity") +
      geom_vline(xintercept = plot_threshold, color = "red", linetype = "dashed", linewidth = 1) +
      scale_fill_manual(values = c("FALSE" = "gray", "TRUE" = "steelblue"), labels = c("FALSE" = "Removed", "TRUE" = "Kept")) +
      theme_minimal() +
      labs(title = paste("Spot Filtering -", stringr::str_to_title(method), "Threshold"),
           subtitle = paste("Threshold:", subtitle_txt), x = x_label, y = "Number of Spots", fill = "Status")
    
    p2 <- ggplot(plot_data, aes(x = x_coord, y = y_coord, color = kept)) +
      geom_point(size = 0.5, alpha = 0.7) +
      scale_color_manual(values = c("FALSE" = "red", "TRUE" = "blue"), labels = c("FALSE" = "Removed", "TRUE" = "Kept")) +
      scale_y_reverse() + coord_fixed() + theme_minimal() +
      labs(title = "Spatial Distribution - Filtering Result",
           subtitle = paste("Red: Removed (", n_removed, "), Blue: Kept (", n_filtered, ")"),
           x = "X Coordinate", y = "Y Coordinate", color = "Status")
    
    filtered_plot_data <- plot_data[plot_data$kept, ]
    p3 <- ggplot(filtered_plot_data, aes(x = x_coord, y = y_coord, color = total_counts)) +
      geom_point(size = 0.5, alpha = 0.7) +
      scale_color_gradient(low = "lightblue", high = "darkred", trans = "log10", labels = scales::comma) +
      scale_y_reverse() + coord_fixed() + theme_minimal() +
      labs(title = "Filtered Data", subtitle = paste("Remaining spots:", n_filtered),
           x = "X Coordinate", y = "Y Coordinate", color = "Total Counts")
    
    if (method == "otsu") {
      variance_data <- data.frame(threshold = otsu_result$bin_centers, variance = otsu_result$sigma_between)
      p4 <- ggplot(variance_data, aes(x = threshold, y = variance)) +
        geom_line(linewidth = 1) + geom_vline(xintercept = otsu_result$threshold, color = "red", linetype = "dashed") +
        theme_minimal() + labs(title = "Otsu Variance", x = x_label, y = "Between-Class Variance")
      combined_plot <- (p1 | p2) / (p3 | p4)
    } else {
      combined_plot <- (p1 | p2) / p3
    }
    
    title_txt <- paste0("Spot Filtering (", sample_name, ")\n", tryCatch(seurat_obj@misc$measurement_label, error=function(e) sample_name))
    combined_plot <- combined_plot + plot_annotation(title = title_txt) & theme(plot.title = element_text(hjust = 0.5, face = "bold"))
    
    # ggsave(file.path(outdir, paste0("spot_filtering_", sample_name, "_", ifelse(method == "otsu", "otsu", paste0("manual_", threshold_original)), ".png")),
    #        combined_plot, width = 16, height = 12, dpi = 300)
  }
  
  return(list(
    filtered_seurat = filtered_seurat,
    threshold = threshold, threshold_original = threshold_original,
    spots_kept = spots_to_keep,
    n_original = n_original, n_filtered = n_filtered, n_removed = n_removed
  ))
}

# =========================
# メイン処理実行
# =========================

# MRMリストの読み込み
if (file.exists(MRM_FILE_PATH)) {
  mrm_df <- .load_mrm_table(MRM_FILE_PATH)
  if (!is.null(mrm_df)) {
    cat("MRM list loaded:", nrow(mrm_df), "rows.\n")
  } else {
    warning("MRM file exists but could not be parsed into (Compound/Parent/Daughter). Compound matching will be skipped.")
  }
} else {
  warning("MRM file not found at:", MRM_FILE_PATH, "\nCompound matching will be skipped.")
  mrm_df <- NULL
}

# データの読み込み (RESUME_FROM_RDS=TRUE の場合は保存済みデータをロード)
rds_filename1 <- "DESI_SeuratList1_bgremoved.rds"
rds_path1_out <- file.path(rds_od, rds_filename1) # 出力先

rds_path1_in <- if (RESUME_FROM_RDS) file.path(RESUME_DIR_PATH, rds_filename1) else ""
if (RESUME_FROM_RDS && file.exists(rds_path1_in)) {
  message(">> RESUME: Loading existing RDS (1/2): ", rds_path1_in)
  seu_list <- readRDS(rds_path1_in)
  # 修正①: Resume時に既存RDSを出力先にもコピー
  file.copy(rds_path1_in, rds_path1_out, overwrite = TRUE)
} else {
  # 生データからの読み込み
  seu_list <- list()
  for(ii in seq_along(sample_names)){
    sample_name <- sample_names[ii]
    file_path <- file.path(data_folder, paste0(sample_name, ".txt"))
    desi_data <- read_desi_data(file_path, sample_prefix = sample_name)
    count_matrix <- desi_data$count_matrix
    coordinates_data <- desi_data$coordinates
    spatial_coords <- coordinates_data[, c("spot_index", "x", "y", "spot_id")]
    
    common_spots <- intersect(colnames(count_matrix), spatial_coords$spot_id)
    count_matrix <- count_matrix[, common_spots, drop = FALSE]
    spatial_coords <- spatial_coords[spatial_coords$spot_id %in% common_spots, ]
    spatial_coords <- spatial_coords[match(colnames(count_matrix), spatial_coords$spot_id), ]
    
    if(!inherits(count_matrix, "dgCMatrix")) count_matrix <- as(as.matrix(count_matrix), "dgCMatrix")
    spot_coords <- spatial_coords[, c("x", "y")]
    rownames(spot_coords) <- spatial_coords$spot_id
    
    seurat_obj <- CreateSeuratObject(counts = count_matrix, project = "DESI_MSI", assay = "Spatial")
    seurat_obj$sample <- sample_name
    seurat_obj@misc$measurement_label <- tools::file_path_sans_ext(basename(file_path))
    seurat_obj$x_coord <- spot_coords[colnames(seurat_obj), "x"]
    seurat_obj$y_coord <- spot_coords[colnames(seurat_obj), "y"]
    seurat_obj$spot_index <- spatial_coords[colnames(seurat_obj), "spot_index"]
    seurat_obj$nCount_Spatial <- colSums(count_matrix)
    seurat_obj$nFeature_Spatial <- colSums(count_matrix > 0)
    seurat_obj@misc$metabolite_names <- desi_data$metabolite_names
    seurat_obj@misc$file_path <- file_path
    
    filtering_result_otsu <- filter_low_count_spots(
      seurat_obj, method = "otsu", use_log_scale = TRUE,
      n_bins = 256, plot_results = TRUE, sample_name = sample_name, outdir = od
    )
    seu_list[[ii]] <- filtering_result_otsu$filtered_seurat
  }
  # RDS保存
  saveRDS(seu_list, rds_path1_out)
  gc()
}

# ---- 空間平滑化処理 (Spatial Smoothing) ----
SPATIAL_SMOOTH <- TRUE
if(SPATIAL_SMOOTH){
  rds_filename2 <- "DESI_SeuratList2_smoothed.rds"
  rds_path2_out <- file.path(rds_od, rds_filename2)
  
  rds_path2_in <- if (RESUME_FROM_RDS) file.path(RESUME_DIR_PATH, rds_filename2) else ""
  
  if (RESUME_FROM_RDS && file.exists(rds_path2_in)) {
    message(">> RESUME: Loading existing RDS (2/2): ", rds_path2_in)
    seu_list <- readRDS(rds_path2_in)
    # 修正①: Resume時に既存RDSを出力先にもコピー
    file.copy(rds_path2_in, rds_path2_out, overwrite = TRUE)
  } else {
    smooth_radius <- 0.1
    sigma <- 0.05
    for (ii in seq_along(seu_list)) {
      smoothed_seurat <- spatial_smooth_seurat(
        seu_list[[ii]], radius = smooth_radius, sigma = sigma,
        layer = "counts", method = "weighted_mean", weight_function = "gaussian"
      )
      p_smoothing <- visualize_spatial_smoothing(seu_list[[ii]], smoothed_seurat, layer = "counts")
      title_txt <- paste0("Spatial smoothing: ", seu_list[[ii]]$sample[1])
      p_smoothing <- p_smoothing + plot_annotation(title = title_txt) & theme(plot.title = element_text(hjust = 0.5, face = "bold"))
      # ggsave(file.path(od, paste0("spatial_smoothing_", seu_list[[ii]]$sample[1], "_r", smooth_radius, "_sigma", sigma, ".png")),
      #        p_smoothing, width = 10, height = 8, dpi = 300)
      seu_list[[ii]] <- smoothed_seurat
    }
    # RDS保存
    saveRDS(seu_list, rds_path2_out)
    gc()
  }
}

# ---- Log1p 正規化 ----
# (平滑化済みデータを対象に実施)
for(ii in seq_along(seu_list)){
  seurat_filtered <- seu_list[[ii]]
  counts_mat <- LayerData(seurat_filtered[["Spatial"]], layer = "counts")
  log1p_data <- log1p(counts_mat)
  seurat_filtered[["Spatial"]] <- SetAssayData(seurat_filtered[["Spatial"]], layer = "data", new.data = log1p_data)
  seurat_filtered@misc$preprocessing_method <- "log1p"
  VariableFeatures(seurat_filtered) <- rownames(seurat_filtered)
  seurat_filtered <- ScaleData(seurat_filtered, features = rownames(seurat_filtered))
  seu_list[[ii]] <- seurat_filtered
}

# =========================
# 解析実行 (PCA / Harmony / RPCA)
# =========================
if (length(seu_list) == 1) {
  # ---- Single Sample Mode (PCA) ----
  message("Single-sample mode: PCAを用いた解析を実行します")
  od_pca <- file.path(od, "PCA"); dir.create(od_pca, showWarnings = FALSE)
  rds_filename_single <- "DESI_Seurat_SingleSample.rds"
  rds_path_single_out <- file.path(rds_od, rds_filename_single)
  
  if (RESUME_FROM_RDS) {
    rds_path_single_in <- file.path(RESUME_DIR_PATH, rds_filename_single)
  } else {
    rds_path_single_in <- ""
  }
  
  if (RESUME_FROM_RDS && file.exists(rds_path_single_in)) {
    message(">> RESUME: Loading existing RDS (Single): ", rds_path_single_in)
    seu_single <- readRDS(rds_path_single_in)
    # 修正①: Resume時に既存RDSを出力先にもコピー
    file.copy(rds_path_single_in, rds_path_single_out, overwrite = TRUE)
  } else {
    seu_single <- seu_list[[1]]
    DefaultAssay(seu_single) <- "Spatial"
    seu_single <- NormalizeData(seu_single)
    seu_single <- FindVariableFeatures(seu_single)
    seu_single <- ScaleData(seu_single, features = VariableFeatures(seu_single))
    # ---- PATCH: dims auto-fix (available PCs) ----
    nfeat_single <- length(VariableFeatures(seu_single))
    if (is.null(nfeat_single) || nfeat_single <= 1) nfeat_single <- nrow(seu_single)
    ncell_single <- ncol(seu_single)
    npcs_single <- min(30, nfeat_single - 1, ncell_single - 1)
    npcs_single <- max(2, npcs_single)

    seu_single <- RunPCA(seu_single, npcs = npcs_single)
    pc_avail <- ncol(Embeddings(seu_single, "pca"))
    dims_use <- seq_len(min(30, pc_avail))
    seu_single <- RunUMAP(seu_single, reduction = "pca", dims = dims_use, seed.use = 42)
    seu_single <- FindNeighbors(seu_single, reduction = "pca", dims = dims_use)
    seu_single <- FindClusters(seu_single, resolution = 0.5, algorithm = 4)
    Idents(seu_single) <- seu_single$seurat_clusters
    
    saveRDS(seu_single, rds_path_single_out)
    gc()
  }
  
  # Color
  current_clusters <- levels(Idents(seu_single))
  my_colors <- .assign_cluster_colors(seu_single, seed = 42)
  
  
  # UMAP Output
  plot_umap_cluster_variants(seu_single, prefix = "single", outdir = od_pca)
  plot_umap_per_sample(seu_single, unique(seu_single$sample), prefix = "single", outdir = od_pca)
  
  export_cluster_highlights(seu_single, prefix = "pca", outdir = od, sample_names = unique(seu_single$sample))
  
  # 統合画像コピー
  try(suppressWarnings(file.copy(
    from = file.path(od, "PerCluster_Highlight", "pca", "UMAP_per_sample_pca_ALLclusters.png"),
    to   = file.path(od_pca, "UMAP_per_sample_pca_ALLclusters.png"),
    overwrite = TRUE
  )), silent = TRUE)
  try(suppressWarnings(file.copy(
    from = file.path(od, "PerCluster_Highlight", "pca", "UMAPtop_TICoverlaybottom_pca_ALLclusters.png"),
    to   = file.path(od_pca, "UMAPtop_TICoverlaybottom_pca_ALLclusters.png"),
    overwrite = TRUE
  )), silent = TRUE)
  
  # Spatial
  p_sp <- ggplot(seu_single@meta.data, aes(x = x_coord, y = y_coord, color = seurat_clusters)) +
    geom_point(size = PLOT_POINT_SIZE, shape = PLOT_POINT_SHAPE, alpha = 1) +
    scale_color_manual(values = my_colors) + scale_y_reverse() + coord_fixed() + theme_minimal() +
    labs(x = "X Coordinate", y = "Y Coordinate", color = "Cluster")
  p_sp <- add_filename_title(p_sp, seu_single, prefix_title = "Single sample")
  # ggsave(file.path(od_pca, paste0("plot_cluster_single_", seu_single$sample[1], ".png")), p_sp, width = 6, height = 8, dpi = 300, bg = "white")

  # DEG & Heatmap
  cat("DEG計算中...\n")
  deg_markers <- FindAllMarkers(seu_single, only.pos = FALSE, min.pct = 0.25, logfc.threshold = 0.25, test.use = "wilcox")
  # BH/FDR補正に置換（Seuratデフォルトの Bonferroni は探索的解析に保守的すぎるため）
  deg_markers$p_val_adj <- p.adjust(deg_markers$p_val, method = "BH")
  # p_val_adj=0 補正（double精度の限界で丸められた0をCSV出力前に補正）
  if (any(deg_markers$p_val_adj == 0, na.rm = TRUE)) {
    min_nz <- suppressWarnings(min(deg_markers$p_val_adj[deg_markers$p_val_adj > 0], na.rm = TRUE))
    if (is.finite(min_nz)) {
      deg_markers$p_val_adj[deg_markers$p_val_adj == 0] <- min_nz * 0.1
    } else {
      deg_markers$p_val_adj[deg_markers$p_val_adj == 0] <- .Machine$double.xmin
    }
  }
  write.csv(deg_markers, file.path(od_pca, "analysis_deg_all_markers_single.csv"), row.names = FALSE)
  
  top5_markers <- deg_markers %>% dplyr::group_by(cluster) %>% dplyr::top_n(n = 5, wt = avg_log2FC)
  top_genes <- unique(top5_markers$gene)
  
  if (length(top_genes) > 1) {
    sampled_cells <- c()
    for (cid in unique(Idents(seu_single))) {
      cc <- WhichCells(seu_single, idents = cid)
      if (length(cc) > 200) cc <- sample(cc, 200)
      sampled_cells <- c(sampled_cells, cc)
    }
    if (length(sampled_cells) > 1) {
      heatmap1 <- DoHeatmap(subset(seu_single, cells = sampled_cells), features = top_genes, group.by = "ident", assay = "Spatial") +
        scale_fill_gradientn(colors = c("blue", "white", "red")) + ggtitle("Top 5 Markers")
      
      # ヒートマップラベル (MRM対応)
      if (!is.null(mrm_df)) {
        mapped <- match_mrm_compound(top_genes, mrm_df, tolerance = 0.1)
        name_map <- setNames(mapped, top_genes)
        heatmap1 <- heatmap1 + scale_y_discrete(labels = function(x) {
          lab <- sapply(x, function(xx) {
            mm <- name_map[[xx]]
            if (!is.null(mm) && !is.na(mm) && mm != xx) {
              mm
            } else {
              xx
            }
          })
          make.unique(lab)
        })
      }
      # ggsave(file.path(od,"analysis_heatmap_top5_markers_pca.png"), heatmap1, width = 12, height = 8, dpi = 300)
    }
  }

  # Volcano & MSI
  if (nrow(deg_markers) > 0) {
    # PCAモードでのVolcano生成
    volcano_dir <- file.path(od, "Volcano_Plots", "pca"); dir.create(volcano_dir, recursive = TRUE, showWarnings = FALSE)
    volcano_labeled_dir <- file.path(od, "Volcano_Plots_MRM", "pca"); dir.create(volcano_labeled_dir, recursive = TRUE, showWarnings = FALSE)
    msi_dir <- file.path(od, "Cluster_Top5_MSI", "pca"); dir.create(msi_dir, recursive = TRUE, showWarnings = FALSE)
    all_clusters <- sort(unique(deg_markers$cluster))
    
    for (cl in all_clusters) {
      df_sub <- deg_markers[deg_markers$cluster == cl, ]
      if(any(df_sub$p_val_adj == 0)) {
        min_nz <- min(df_sub$p_val_adj[df_sub$p_val_adj > 0], na.rm = TRUE)
        df_sub$p_val_adj[df_sub$p_val_adj == 0] <- min_nz * 0.1
      }
      df_sub$log_p <- -log10(df_sub$p_val_adj)
      
      df_sub$color_group <- "NO"
      df_sub$color_group[df_sub$p_val_adj < DEG_P_THRESH_VAL & df_sub$avg_log2FC > DEG_LOGFC_TH_VAL] <- "UP"
      df_sub$color_group[df_sub$p_val_adj < DEG_P_THRESH_VAL & df_sub$avg_log2FC < -DEG_LOGFC_TH_VAL] <- "DOWN"
      
      top_hits <- rbind(df_sub %>% filter(color_group == "UP") %>% arrange(desc(avg_log2FC)) %>% head(LABEL_TOP_N_EACH),
                        df_sub %>% filter(color_group == "DOWN") %>% arrange(avg_log2FC) %>% head(LABEL_TOP_N_EACH))
      df_sub$label_text <- ifelse(df_sub$gene %in% top_hits$gene, df_sub$gene, NA)
      
      max_log_p <- max(df_sub$log_p, na.rm = TRUE)
      y_limit_max <- max_log_p * 1.2
      
      p <- ggplot(df_sub, aes(x = avg_log2FC, y = log_p, col = color_group)) +
        geom_point(alpha = 0.6, size = 1.0) +
        scale_color_manual(values = c("UP" = "red", "DOWN" = "blue", "NO" = "gray")) +
        geom_vline(xintercept = c(-DEG_LOGFC_TH_VAL, DEG_LOGFC_TH_VAL), linetype = "dashed") +
        geom_hline(yintercept = -log10(DEG_P_THRESH_VAL), linetype = "dashed") +
        ggrepel::geom_text_repel(aes(label = label_text), size = 3.0, max.overlaps = 20) +
        theme_minimal() + labs(title = paste0("Cluster ", cl)) +
        coord_cartesian(ylim = c(0, y_limit_max))

      # ggsave(file.path(volcano_dir, paste0("Volcano_Cluster_", cl, ".png")), p, width = 10, height = 8)

      # MRMラベルング
      mrm_hits <- rbind(df_sub %>% filter(color_group == "UP") %>% arrange(desc(avg_log2FC)) %>% head(LABEL_TOP_N_EACH),
                        df_sub %>% filter(color_group == "DOWN") %>% arrange(avg_log2FC) %>% head(LABEL_TOP_N_EACH))
      
      if (!is.null(mrm_df)) {
        mapped_compounds <- match_mrm_compound(mrm_hits$gene, mrm_df, tolerance = 0.1)
        name_map <- setNames(mapped_compounds, mrm_hits$gene)
        df_sub$label_mrm <- ifelse(df_sub$gene %in% names(name_map), name_map[df_sub$gene], NA)
      } else {
        df_sub$label_mrm <- ifelse(df_sub$gene %in% mrm_hits$gene, df_sub$gene, NA)
      }
      
      p_mrm <- ggplot(df_sub, aes(x = avg_log2FC, y = log_p, col = color_group)) +
        geom_point(alpha = 0.6, size = 1.0) +
        scale_color_manual(values = c("UP" = "red", "DOWN" = "blue", "NO" = "gray")) +
        geom_vline(xintercept = c(-DEG_LOGFC_TH_VAL, DEG_LOGFC_TH_VAL), linetype = "dashed") +
        geom_hline(yintercept = -log10(DEG_P_THRESH_VAL), linetype = "dashed") +
        ggrepel::geom_text_repel(aes(label = label_mrm), size = 2.5, max.overlaps = 20, box.padding = 0.5, force = 2) +
        theme_minimal() + labs(title = paste0("Cluster ", cl, " (MRM Labeled)")) +
        coord_cartesian(ylim = c(0, y_limit_max))

      # ggsave(file.path(volcano_labeled_dir, paste0("Volcano_Cluster_", cl, "_MRM.png")), p_mrm, width = 12, height = 9)


      # ---- Top5 MSI (修正: 分子ごとに全サンプルを横並べ、それを2行5列に配置) ----
      run_volcano_and_msi(seu_single, deg_markers, method_tag = "pca",
                          sample_names = unique(seu_single$sample), od = od, mrm_df = mrm_df, method_outdir = od_pca)
    }
  }
} else {
  # =========================
  # Multi-sample mode
  # =========================
  
  # ---- Harmony ----
  message("Multi-sample mode: Harmony...")
  od_harmony <- file.path(od, "Harmony"); dir.create(od_harmony, showWarnings = FALSE)
  rds_filename_harmony <- "DESI_SeuratCombined_harmony.rds"
  rds_path_harmony_out <- file.path(rds_od, rds_filename_harmony)
  
  rds_path_harmony_in <- if (RESUME_FROM_RDS) file.path(RESUME_DIR_PATH, rds_filename_harmony) else ""
  if (RESUME_FROM_RDS && file.exists(rds_path_harmony_in)) {
    message(">> RESUME: Loading existing RDS (Harmony): ", rds_path_harmony_in)
    seu_harmony <- readRDS(rds_path_harmony_in)
    # 修正①: Resume時に既存RDSを出力先にもコピー
    file.copy(rds_path_harmony_in, rds_path_harmony_out, overwrite = TRUE)
  } else {
    seu_harmony <- Reduce(function(x, y) merge(x, y, add.cell.ids = c(x$sample[1], y$sample[1])), seu_list)
    seu_harmony <- NormalizeData(seu_harmony)
    seu_harmony <- FindVariableFeatures(seu_harmony)
    seu_harmony <- ScaleData(seu_harmony)
    # ---- PATCH: dims auto-fix (available Harmony PCs) ----
    nfeat_h <- length(VariableFeatures(seu_harmony))
    if (is.null(nfeat_h) || nfeat_h <= 1) nfeat_h <- nrow(seu_harmony)
    ncell_h <- ncol(seu_harmony)
    npcs_h <- min(30, nfeat_h - 1, ncell_h - 1)
    npcs_h <- max(2, npcs_h)

    seu_harmony <- RunPCA(seu_harmony, npcs = npcs_h)
    seu_harmony <- RunHarmony(object = seu_harmony, group.by.vars = "sample")

    h_avail <- tryCatch(ncol(Embeddings(seu_harmony, "harmony")), error = function(e) NA_integer_)
    if (!is.finite(h_avail) || h_avail < 1) h_avail <- ncol(Embeddings(seu_harmony, "pca"))
    dims_use <- seq_len(min(30, h_avail))

    seu_harmony <- RunUMAP(seu_harmony, reduction = "harmony", dims = dims_use, seed.use = 42)
    seu_harmony <- FindNeighbors(seu_harmony, reduction = "harmony", dims = dims_use)
    seu_harmony <- FindClusters(seu_harmony, resolution = 0.5, algorithm = 4)
    Idents(seu_harmony) <- seu_harmony$seurat_clusters
    
    saveRDS(seu_harmony, rds_path_harmony_out)
    gc()
  }
  
  # Color
  current_clusters <- levels(Idents(seu_harmony))
  my_colors <- .assign_cluster_colors(seu_harmony, seed = 42)
  
  
  plot_umap_cluster_variants(seu_harmony, prefix = "harmony", outdir = od_harmony)
  plot_umap_per_sample(seu_harmony, sample_names, prefix = "harmony", outdir = od_harmony)
  
  export_cluster_highlights(seu_harmony, prefix = "harmony", outdir = od, sample_names = sample_names,
                            OUTPUT_UMAP_HIGHLIGHT_ALLCLUSTERS = FALSE)
  
  # 統合画像のコピー
  try(suppressWarnings(file.copy(
    from = file.path(od, "PerCluster_Highlight", "harmony", "UMAP_per_sample_harmony_ALLclusters.png"),
    to   = file.path(od_harmony, "UMAP_per_sample_harmony_ALLclusters.png"),
    overwrite = TRUE
  )), silent = TRUE)
  try(suppressWarnings(file.copy(
    from = file.path(od, "PerCluster_Highlight", "harmony", "UMAPtop_TICoverlaybottom_harmony_ALLclusters.png"),
    to   = file.path(od_harmony, "UMAPtop_TICoverlaybottom_harmony_ALLclusters.png"),
    overwrite = TRUE
  )), silent = TRUE)
  
  # Sample Coloring (Overlaid & Split)
  p_sample_overlaid <- DimPlot(seu_harmony, reduction = "umap", group.by = "sample") +
    ggtitle("UMAP: Sample colored (Harmony / Overlaid)") +
    theme(plot.title = element_text(hjust = 0.5, face = "bold"))
  # ggsave(file.path(od_harmony, "umap_sample_colored_harmony_OVERLAID.png"), p_sample_overlaid, width = 8, height = 6, dpi = 300)

  n_samples <- length(unique(seu_harmony$sample))
  p_sample_split <- DimPlot(seu_harmony, reduction = "umap", group.by = "sample", split.by = "sample", ncol = n_samples) +
    ggtitle("UMAP: Sample colored (Harmony / Split)") +
    theme(plot.title = element_text(hjust = 0.5, face = "bold"))
  # ggsave(file.path(od_harmony, "umap_sample_colored_harmony_SPLIT.png"), p_sample_split, width = 6 * n_samples, height = 6, dpi = 300)

  # ---- Harmony 空間分布プロット ----
  spatial_plots_col <- list()
  spatial_plots_lab <- list()
  
  for(ii in seq_along(sample_names)){
    sample_col_h <- .get_sample_col(seu_harmony)
    cells_sn_h <- colnames(seu_harmony)[seu_harmony@meta.data[[sample_col_h]] == sample_names[[ii]]]
    sub_seurat <- subset(seu_harmony, cells = cells_sn_h)
    
    p1 <- ggplot(sub_seurat@meta.data, aes(x = x_coord, y = y_coord, color = seurat_clusters)) +
      geom_point(size = PLOT_POINT_SIZE, shape = PLOT_POINT_SHAPE, alpha = 1) +
      scale_color_manual(values = my_colors) + scale_y_reverse() + coord_fixed(expand = FALSE) + theme_void() +
      labs(x = NULL, y = NULL, color = "Cluster")
    p1 <- add_filename_title(p1, sub_seurat, prefix_title = "Harmony")
    # ggsave(file.path(od_harmony, paste0("plot_cluster_harmony_", sample_names[[ii]], ".png")), p1, width = 6, height = 8, dpi = 300, bg = "white")

    cluster_centers <- sub_seurat@meta.data %>% dplyr::group_by(seurat_clusters) %>%
      dplyr::summarise(center_x = mean(x_coord, na.rm=T), center_y = mean(y_coord, na.rm=T), .groups='drop')
    
    p2 <- ggplot(sub_seurat@meta.data, aes(x = x_coord, y = y_coord, color = seurat_clusters)) +
      geom_point(size = PLOT_POINT_SIZE, shape = PLOT_POINT_SHAPE, alpha = 1) +
      geom_text(data = cluster_centers, aes(x = center_x, y = center_y, label = seurat_clusters), color = "black", size = 4, fontface = "bold") +
      scale_color_manual(values = my_colors) + scale_y_reverse() + coord_fixed(expand = FALSE) + theme_void() +
      labs(x = NULL, y = NULL, color = "Cluster")
    p2 <- add_filename_title(p2, sub_seurat, prefix_title = "Harmony (labeled)")
    # ggsave(file.path(od_harmony, paste0("plot_cluster_harmony_with_label_", sample_names[[ii]], ".png")), p2, width = 6, height = 8, dpi = 300, bg = "white")

    spatial_plots_col[[sample_names[[ii]]]] <- p1
    spatial_plots_lab[[sample_names[[ii]]]] <- p2
  }
  
  if (length(spatial_plots_col) > 0) {
    n_plots <- length(spatial_plots_col)
    combined_sp_col <- wrap_plots(spatial_plots_col, nrow = 1) + plot_annotation(title = "Combined Spatial: Harmony")
    # ggsave(file.path(od_harmony, "plot_cluster_harmony_COMBINED.png"), combined_sp_col, width = 6 * n_plots, height = 6, dpi = 300, bg = "white")

    combined_sp_lab <- wrap_plots(spatial_plots_lab, nrow = 1) + plot_annotation(title = "Combined Spatial Labeled: Harmony")
    # ggsave(file.path(od_harmony, "plot_cluster_harmony_with_label_COMBINED.png"), combined_sp_lab, width = 6 * n_plots, height = 6, dpi = 300, bg = "white")
  }
  
  
  
  
  # =========================
  # DEG & Heatmap (Harmony)
  # - Harmony / RPCA / PCA で共通の処理
  # =========================
  cat("DEG計算中 (Harmony)...\n")
  
  # Seurat v5 対応: layerを結合
  assay_hm_harmony <- if ("Spatial" %in% Seurat::Assays(seu_harmony)) "Spatial" else DefaultAssay(seu_harmony)
  DefaultAssay(seu_harmony) <- assay_hm_harmony
  seu_harmony <- tryCatch(JoinLayers(seu_harmony), error = function(e) {
    message("!! JoinLayers(Harmony) failed: ", e$message)
    seu_harmony
  })
  
  deg_markers_harmony <- tryCatch({
    FindAllMarkers(seu_harmony, only.pos = FALSE, min.pct = 0.25, logfc.threshold = 0.25, test.use = "wilcox")
  }, error = function(e) {
    message("!! DEG(Harmony) failed: ", e$message)
    NULL
  })
  
  if (is.null(deg_markers_harmony) || !is.data.frame(deg_markers_harmony) || nrow(deg_markers_harmony) == 0 || !("cluster" %in% colnames(deg_markers_harmony))) {
    message(">> DEG(Harmony) skipped: no valid marker table (check JoinLayers warning). Continue to RPCA.")
  } else {
    # BH/FDR補正に置換（Seuratデフォルトの Bonferroni は探索的解析に保守的すぎるため）
    deg_markers_harmony$p_val_adj <- p.adjust(deg_markers_harmony$p_val, method = "BH")
    write.csv(deg_markers_harmony, file.path(od_harmony, "analysis_deg_all_markers_harmony.csv"), row.names = FALSE)
    
    top5_markers_harmony <- deg_markers_harmony %>% dplyr::group_by(cluster) %>% dplyr::top_n(n = 5, wt = avg_log2FC)
    top_genes_harmony <- unique(top5_markers_harmony$gene)
    write.csv(top5_markers_harmony, file.path(od_harmony, "analysis_top5_markers_per_cluster_harmony.csv"), row.names = FALSE)
    
    if (length(top_genes_harmony) > 0) {
      sampled_cells <- c()
      for (cid in unique(Idents(seu_harmony))) {
        cc <- WhichCells(seu_harmony, idents = cid)
        if (length(cc) > 200) cc <- sample(cc, 200)
        sampled_cells <- c(sampled_cells, cc)
      }
      if (length(sampled_cells) > 0) {
        heatmap_harmony <- DoHeatmap(subset(seu_harmony, cells = sampled_cells), features = top_genes_harmony, group.by = "ident", assay = assay_hm_harmony) +
          scale_fill_gradientn(colors = c("blue", "white", "red")) + ggtitle("Top 5 Markers (Harmony)")
        
        # ヒートマップラベル (MRM対応)
        if (!is.null(mrm_df)) {
          mapped <- match_mrm_compound(top_genes_harmony, mrm_df, tolerance = 0.1)
          name_map <- setNames(mapped, top_genes_harmony)
          heatmap_harmony <- heatmap_harmony + scale_y_discrete(labels = function(x) {
            lab <- sapply(x, function(xx) {
              mm <- name_map[[xx]]
              if (!is.null(mm) && !is.na(mm) && mm != xx) mm else xx
            })
            make.unique(lab)
          })
        }
        # ggsave(file.path(od, "analysis_heatmap_top5_markers_harmony.png"), heatmap_harmony, width = 12, height = 8, dpi = 300)
      }
    }
  }
  # =========================
  # Volcano & Top5 MSI (Harmony)
  # =========================
  if (!is.null(deg_markers_harmony) && is.data.frame(deg_markers_harmony) && nrow(deg_markers_harmony) > 0 && ("cluster" %in% colnames(deg_markers_harmony))) {
    run_volcano_and_msi(seu_harmony, deg_markers_harmony, method_tag = "harmony",
                        sample_names = sample_names, od = od, mrm_df = mrm_df, method_outdir = od_harmony)
  }
  
  # ---- RPCA ----
  message("Multi-sample mode: RPCA...")
  od_rpca <- file.path(od, "RPCA"); dir.create(od_rpca, showWarnings = FALSE)
  rds_filename_rpca <- "DESI_SeuratCombined_RPCA.rds"
  rds_path_rpca_out <- file.path(rds_od, rds_filename_rpca)
  
  rds_path_rpca_in <- if (RESUME_FROM_RDS) file.path(RESUME_DIR_PATH, rds_filename_rpca) else ""
  
  if (RESUME_FROM_RDS && file.exists(rds_path_rpca_in)) {
    message(">> RESUME: Loading existing RDS (RPCA): ", rds_path_rpca_in)
    seu_rpca <- readRDS(rds_path_rpca_in)
    # 修正①: Resume時に既存RDSを出力先にもコピー
    file.copy(rds_path_rpca_in, rds_path_rpca_out, overwrite = TRUE)
  } else {
    seu_list_norm <- lapply(seu_list, function(x) { x <- NormalizeData(x); x <- FindVariableFeatures(x); x })
    features <- SelectIntegrationFeatures(object.list = seu_list_norm, nfeatures = 3000)
    seu_list_pca <- lapply(seu_list_norm, function(x) { x <- ScaleData(x, features = features); RunPCA(x, features = features, npcs = 30) })

# ------------------------------------------------------------
# [PATCH] RPCA dims safety:
#  - Some datasets may produce fewer than requested PCs (e.g., due to low spot/feature counts),
#    causing: Embeddings(...)[, dims] "subscript out of bounds" inside FindIntegrationAnchors.
#  - We cap dims to the minimum available PC dimension across objects in seu_list_pca.
# ------------------------------------------------------------
get_safe_dims_for_rpca <- function(obj_list, max_dims = 30, reduction = "pca") {
  pc_n <- suppressWarnings(sapply(obj_list, function(x) {
    if (!(reduction %in% names(x@reductions))) return(NA_integer_)
    emb <- tryCatch(Embeddings(x, reduction), error = function(e) NULL)
    if (is.null(emb)) return(NA_integer_)
    ncol(emb)
  }))
  pc_n <- pc_n[is.finite(pc_n)]
  if (length(pc_n) == 0) {
    message("!! [RPCA dims safety] Could not detect PCA dimensions; fallback to 1:", max_dims)
    return(1:max_dims)
  }
  dims_max <- min(max_dims, min(pc_n))
  if (dims_max < 2) dims_max <- 2
  message(sprintf(">> [RPCA dims safety] Using dims = 1:%d (min available PCs across samples)", dims_max))
  1:dims_max
}
dims_use_rpca <- get_safe_dims_for_rpca(seu_list_pca, max_dims = 30, reduction = "pca")

    anchors <- FindIntegrationAnchors(object.list = seu_list_pca, anchor.features = features, reduction = "rpca", dims = dims_use_rpca)
    
    seu_rpca <- IntegrateData(anchorset = anchors, dims = dims_use_rpca)
    DefaultAssay(seu_rpca) <- "integrated"
    seu_rpca <- ScaleData(seu_rpca)
    # ---- PATCH: dims auto-fix (available PCs) ----
    nfeat_r <- length(VariableFeatures(seu_rpca))
    if (is.null(nfeat_r) || nfeat_r <= 1) nfeat_r <- nrow(seu_rpca)
    ncell_r <- ncol(seu_rpca)
    npcs_r <- min(30, nfeat_r - 1, ncell_r - 1)
    npcs_r <- max(2, npcs_r)

    seu_rpca <- RunPCA(seu_rpca, npcs = npcs_r)
    pc_avail <- ncol(Embeddings(seu_rpca, "pca"))
    dims_use <- seq_len(min(30, pc_avail))
    seu_rpca <- RunUMAP(seu_rpca, reduction = "pca", dims = dims_use, seed.use = 42)
    seu_rpca <- FindNeighbors(seu_rpca, reduction = "pca", dims = dims_use)
    seu_rpca <- FindClusters(seu_rpca, algorithm = 4)
    Idents(seu_rpca) <- seu_rpca$seurat_clusters
    
    saveRDS(seu_rpca, rds_path_rpca_out)
    gc()
  }
  
  current_clusters <- levels(Idents(seu_rpca))
  my_colors <- .assign_cluster_colors(seu_rpca, seed = 42)
  
  
  plot_umap_cluster_variants(seu_rpca, prefix = "rpca", outdir = od_rpca)
  plot_umap_per_sample(seu_rpca, sample_names, prefix = "rpca", outdir = od_rpca)
  
  export_cluster_highlights(seu_rpca, prefix = "rpca", outdir = od, sample_names = sample_names,
                            OUTPUT_UMAP_HIGHLIGHT_ALLCLUSTERS = FALSE)
  
  # 統合画像のコピー
  try(suppressWarnings(file.copy(
    from = file.path(od, "PerCluster_Highlight", "rpca", "UMAP_per_sample_rpca_ALLclusters.png"),
    to   = file.path(od_rpca, "UMAP_per_sample_rpca_ALLclusters.png"),
    overwrite = TRUE
  )), silent = TRUE)
  try(suppressWarnings(file.copy(
    from = file.path(od, "PerCluster_Highlight", "rpca", "UMAPtop_TICoverlaybottom_rpca_ALLclusters.png"),
    to   = file.path(od_rpca, "UMAPtop_TICoverlaybottom_rpca_ALLclusters.png"),
    overwrite = TRUE
  )), silent = TRUE)
  
  # Sample Coloring (RPCA)
  p_sample_overlaid_rpca <- DimPlot(seu_rpca, reduction = "umap", group.by = "sample") +
    ggtitle("UMAP: Sample colored (RPCA / Overlaid)") +
    theme(plot.title = element_text(hjust = 0.5, face = "bold"))
  # ggsave(file.path(od_rpca, "umap_sample_colored_rpca_OVERLAID.png"), p_sample_overlaid_rpca, width = 8, height = 6, dpi = 300)

  p_sample_split_rpca <- DimPlot(seu_rpca, reduction = "umap", group.by = "sample", split.by = "sample", ncol = n_samples) +
    ggtitle("UMAP: Sample colored (RPCA / Split)") +
    theme(plot.title = element_text(hjust = 0.5, face = "bold"))
  # ggsave(file.path(od_rpca, "umap_sample_colored_rpca_SPLIT.png"), p_sample_split_rpca, width = 6 * n_samples, height = 6, dpi = 300)

  # ---- RPCA 空間分布プロット ----
  spatial_plots_col <- list()
  spatial_plots_lab <- list()
  
  for(ii in seq_along(sample_names)){
    sample_col_r <- .get_sample_col(seu_rpca)
    cells_sn_r <- colnames(seu_rpca)[seu_rpca@meta.data[[sample_col_r]] == sample_names[[ii]]]
    sub_seurat <- subset(seu_rpca, cells = cells_sn_r)
    
    p1 <- ggplot(sub_seurat@meta.data, aes(x = x_coord, y = y_coord, color = seurat_clusters)) +
      geom_point(size = PLOT_POINT_SIZE, shape = PLOT_POINT_SHAPE, alpha = 1) +
      scale_color_manual(values = my_colors) + scale_y_reverse() + coord_fixed(expand = FALSE) + theme_void() +
      labs(x = NULL, y = NULL, color = "Cluster")
    p1 <- add_filename_title(p1, sub_seurat, prefix_title = "RPCA")
    # ggsave(file.path(od_rpca, paste0("plot_cluster_rpca_", sample_names[[ii]], ".png")), p1, width = 6, height = 8, dpi = 300, bg = "white")

    cluster_centers <- sub_seurat@meta.data %>% dplyr::group_by(seurat_clusters) %>%
      dplyr::summarise(center_x = mean(x_coord, na.rm=T), center_y = mean(y_coord, na.rm=T), .groups='drop')
    
    p2 <- ggplot(sub_seurat@meta.data, aes(x = x_coord, y = y_coord, color = seurat_clusters)) +
      geom_point(size = PLOT_POINT_SIZE, shape = PLOT_POINT_SHAPE, alpha = 1) +
      geom_text(data = cluster_centers, aes(x = center_x, y = center_y, label = seurat_clusters), color = "black", size = 4, fontface = "bold") +
      scale_color_manual(values = my_colors) + scale_y_reverse() + coord_fixed(expand = FALSE) + theme_void() +
      labs(x = NULL, y = NULL, color = "Cluster")
    p2 <- add_filename_title(p2, sub_seurat, prefix_title = "RPCA (labeled)")
    # ggsave(file.path(od_rpca, paste0("plot_cluster_rpca_with_label_", sample_names[[ii]], ".png")), p2, width = 6, height = 8, dpi = 300, bg = "white")

    spatial_plots_col[[sample_names[[ii]]]] <- p1
    spatial_plots_lab[[sample_names[[ii]]]] <- p2
  }
  
  if (length(spatial_plots_col) > 0) {
    n_plots <- length(spatial_plots_col)
    combined_sp_col <- wrap_plots(spatial_plots_col, ncol = n_plots, nrow = 1) + plot_annotation(title = "Combined Spatial: RPCA")
    # ggsave(file.path(od_rpca, "plot_cluster_rpca_COMBINED.png"), combined_sp_col, width = 6 * n_plots, height = 6, dpi = 300, bg = "white")

    combined_sp_lab <- wrap_plots(spatial_plots_lab, ncol = n_plots, nrow = 1) + plot_annotation(title = "Combined Spatial Labeled: RPCA")
    # ggsave(file.path(od_rpca, "plot_cluster_rpca_with_label_COMBINED.png"), combined_sp_lab, width = 6 * n_plots, height = 6, dpi = 300, bg = "white")
  }
  
  # DEG & Heatmap (RPCA)
  deg_markers <- FindAllMarkers(seu_rpca, only.pos = FALSE, min.pct = 0.25, logfc.threshold = 0.25, test.use = "wilcox")
  # BH/FDR補正に置換（Seuratデフォルトの Bonferroni は探索的解析に保守的すぎるため）
  deg_markers$p_val_adj <- p.adjust(deg_markers$p_val, method = "BH")
  # p_val_adj=0 補正（double精度の限界で丸められた0をCSV出力前に補正）
  if (any(deg_markers$p_val_adj == 0, na.rm = TRUE)) {
    min_nz <- suppressWarnings(min(deg_markers$p_val_adj[deg_markers$p_val_adj > 0], na.rm = TRUE))
    if (is.finite(min_nz)) {
      deg_markers$p_val_adj[deg_markers$p_val_adj == 0] <- min_nz * 0.1
    } else {
      deg_markers$p_val_adj[deg_markers$p_val_adj == 0] <- .Machine$double.xmin
    }
  }
  top5_markers <- deg_markers %>% dplyr::group_by(cluster) %>% dplyr::top_n(n = 5, wt = avg_log2FC)
  top_genes <- unique(top5_markers$gene)
  
  if (length(top_genes) > 0) {
    sampled_cells <- c()
    for(cid in unique(Idents(seu_rpca))) {
      cc <- WhichCells(seu_rpca, idents = cid)
      if(length(cc) > 200) cc <- sample(cc, 200)
      sampled_cells <- c(sampled_cells, cc)
    }
    if (length(sampled_cells) > 0) {
      heatmap1 <- DoHeatmap(subset(seu_rpca, cells = sampled_cells), features = top_genes, group.by = "ident", assay = "integrated") +
        scale_fill_gradientn(colors = c("blue", "white", "red")) + ggtitle("Top 5 Markers")
      
      if (!is.null(mrm_df)) {
        mapped <- match_mrm_compound(top_genes, mrm_df, tolerance = 0.1)
        name_map <- setNames(mapped, top_genes)
        heatmap1 <- heatmap1 + scale_y_discrete(labels = function(x) {
          lab <- sapply(x, function(xx) {
            mm <- name_map[[xx]]
            if (!is.null(mm) && !is.na(mm) && mm != xx) {
              mm
            } else {
              xx
            }
          })
          make.unique(lab)
        })
      }
      # ggsave(file.path(od,"analysis_heatmap_top5_markers_rpca.png"), heatmap1, width = 12, height = 8, dpi = 300)
    }
  }
  write.csv(deg_markers, file.path(od_rpca, "analysis_deg_all_markers.csv"), row.names = FALSE)
  write.csv(top5_markers, file.path(od_rpca, "analysis_top5_markers_per_cluster.csv"), row.names = FALSE)
  
  # Volcano & MSI (RPCA)
  run_volcano_and_msi(seu_rpca, deg_markers, method_tag = "rpca",
                      sample_names = sample_names, od = od, mrm_df = mrm_df, method_outdir = od_rpca)
  
  # 個別のVolcano生成ループ (冗長だがレガシーコード維持)
  if (FALSE && nrow(deg_markers) > 0) {
    # 既存コードは残すが実行されないようにFALSE条件にしてある
  }
}

# ---- Cleanup: 中間RDS（解析完了後は不要） ----
for (rp in c(rds_path1_out, rds_path2_out)) {
  if (file.exists(rp)) {
    message(">> Cleanup: ", basename(rp), " を削除しました")
    file.remove(rp)
  }
}

# ---- 並列化終了: sequential に戻す ----
plan(sequential)