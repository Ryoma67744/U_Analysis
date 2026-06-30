# =============================================================================
# MSI Analysis Application - クラスタ安定性診断（複数 seed 再クラスタリング）
# =============================================================================
# 既存の reduction（harmony/rpca/pca）上で FindNeighbors → FindClusters を
# 複数 seed で再計算し、CellID × seed のクラスタラベル行列を書き出す。
# Python 側 stability_runner.py が ARI / クラスタ別 Jaccard / 安定性フラグへ集約する。
#
# 設計:
#   - グラフベースのクラスタ安定性（seed 違い）を見る標準的手法。
#     参照ラベル ref は最初の seed の結果に統一し、方法を揃えて比較する。
#   - 本スクリプトは独立した追加ファイル。既存パイプラインからは呼ばれないため、
#     不具合があっても既存機能を壊さない（新しい安定性機能からのみ起動）。
#
# Usage:
#   Rscript --vanilla stability_diagnostics.R --rds <path> --out <dir> \
#       [--seeds 42,101,202,303,404] [--subsample 1.0] [--reduction auto]
# =============================================================================

suppressPackageStartupMessages({
  library(Seurat)
})

args <- commandArgs(trailingOnly = TRUE)
get_opt <- function(flag, default = NULL) {
  i <- which(args == flag)
  if (length(i) > 0 && i[1] < length(args)) args[i[1] + 1] else default
}

rds_path      <- get_opt("--rds")
out_dir       <- get_opt("--out")
seeds         <- as.integer(strsplit(get_opt("--seeds", "42,101,202,303,404"), ",")[[1]])
subsample     <- as.numeric(get_opt("--subsample", "1.0"))
reduction_opt <- get_opt("--reduction", "auto")
if (is.null(rds_path) || is.null(out_dir)) stop("--rds と --out は必須です")

# --- helpers/rds_io.R を探して compact RDS をロード ---
find_helpers <- function() {
  a <- commandArgs(trailingOnly = FALSE)
  fa <- a[grep("--file=", a)]
  if (length(fa) > 0) dirname(normalizePath(sub("--file=", "", fa[1]), mustWork = FALSE)) else ""
}
rio <- file.path(find_helpers(), "rds_io.R")
if (file.exists(rio)) source(rio)
obj <- if (exists("load_rds_compact")) load_rds_compact(rds_path) else readRDS(rds_path)
if (is.list(obj) && !inherits(obj, "Seurat") && "obj" %in% names(obj)) obj <- obj$obj
obj <- tryCatch(JoinLayers(obj), error = function(e) obj)

# --- 部分標本（任意） ---
if (is.finite(subsample) && subsample > 0 && subsample < 1) {
  set.seed(42)
  keep <- sample(colnames(obj), max(50L, floor(ncol(obj) * subsample)))
  obj <- subset(obj, cells = keep)
}

# --- reduction 選択（auto: harmony > rpca > pca） ---
reds <- names(obj@reductions)
if (length(reds) == 0) stop("reduction（pca 等）が見つかりません")
pick <- NULL
if (!is.null(reduction_opt) && reduction_opt != "auto" && reduction_opt %in% reds) {
  pick <- reduction_opt
} else {
  for (r in c("harmony", "rpca", "pca")) if (r %in% reds) { pick <- r; break }
  if (is.null(pick)) pick <- reds[1]
}
dims <- 1:min(30L, ncol(Embeddings(obj, pick)))

# --- 設定（object/グローバルにあれば踏襲、無ければ既定） ---
g <- function(n, d) { v <- tryCatch(get0(n, envir = .GlobalEnv, inherits = TRUE), error = function(e) NULL); if (is.null(v)) d else v }
res  <- as.numeric(g("CLUSTER_RESOLUTION", 0.5))
kp   <- as.integer(g("CLUSTER_K_PARAM", 20L))

# leiden(4) を試し、失敗時は louvain(1) にフォールバック
cluster_once <- function(o, seed) {
  for (algo in c(4L, 1L)) {
    res_labels <- tryCatch({
      o2 <- FindClusters(o, resolution = res, algorithm = algo,
                         random.seed = seed, verbose = FALSE)
      as.character(Idents(o2))
    }, error = function(e) NULL)
    if (!is.null(res_labels)) return(res_labels)
  }
  rep(NA_character_, ncol(o))
}

obj <- FindNeighbors(obj, reduction = pick, dims = dims, k.param = kp, verbose = FALSE)

cells <- colnames(obj)
mat <- data.frame(CellID = cells, stringsAsFactors = FALSE, check.names = FALSE)
labels_by_seed <- list()
for (s in seeds) labels_by_seed[[paste0("seed_", s)]] <- cluster_once(obj, s)

# 参照 ref = 最初の seed（方法を揃える）
mat$ref <- labels_by_seed[[1]]
for (nm in names(labels_by_seed)) mat[[nm]] <- labels_by_seed[[nm]]

dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
write.csv(mat, file.path(out_dir, "stability_labels.csv"), row.names = FALSE)
cat("Wrote stability_labels.csv:", nrow(mat), "cells x", length(seeds),
    "seeds (reduction=", pick, ", resolution=", res, ")\n")
