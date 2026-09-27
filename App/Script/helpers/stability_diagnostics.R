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

# ★ ver74.0: 最低50件の指定が母集団を超えるとsample()が停止していた。
# 小標本では全件を使い、無効な割合や近傍数は解析開始前に止める。
ua_stability_sample_size <- function(n, fraction) {
  if (length(n) != 1L || !is.finite(n) || n < 3L || n != floor(n))
    stop("安定性診断には3画素以上が必要です")
  if (length(fraction) != 1L || !is.finite(fraction) || fraction <= 0 || fraction > 1)
    stop("--subsample は0より大きく1以下にしてください")
  as.integer(min(n, max(50L, floor(n * fraction))))
}
ua_stability_k <- function(n, k) {
  if (length(k) != 1L || !is.finite(k) || k < 1L || k != floor(k))
    stop("CLUSTER_K_PARAM は正の整数にしてください")
  as.integer(min(k, n - 1L))
}

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
seeds         <- suppressWarnings(as.numeric(strsplit(get_opt("--seeds", "42,101,202,303,404"), ",")[[1]]))
subsample     <- as.numeric(get_opt("--subsample", "1.0"))
reduction_opt <- get_opt("--reduction", "auto")
if (is.null(rds_path) || is.null(out_dir)) stop("--rds と --out は必須です")
# ★ ver74.0: NA/重複seedのまま結果列を作ると欠落・上書きになるため先に検証。
if (!length(seeds) || any(!is.finite(seeds)) || any(seeds < 0 | seeds > .Machine$integer.max) ||
    any(seeds != floor(seeds)) || anyDuplicated(seeds)) stop("--seeds は重複のない非負整数を指定してください")
seeds <- as.integer(seeds)

# --- helpers/rds_io.R を探して compact RDS をロード ---
find_helpers <- function() {
  a <- commandArgs(trailingOnly = FALSE)
  fa <- a[grep("--file=", a)]
  if (length(fa) > 0) dirname(normalizePath(sub("--file=", "", fa[1]), mustWork = FALSE)) else ""
}
rio <- file.path(find_helpers(), "rds_io.R")
if (file.exists(rio)) source(rio)
obj <- if (exists("load_rds_compact")) load_rds_compact(rds_path) else readRDS(rds_path)
if (exists("ua_unwrap_seurat")) obj <- ua_unwrap_seurat(obj) else if (is.list(obj) && !inherits(obj, "Seurat") && "obj" %in% names(obj)) obj <- obj$obj
obj <- tryCatch(JoinLayers(obj), error = function(e) obj)

# --- 部分標本（任意） ---
sample_size <- ua_stability_sample_size(ncol(obj), subsample)
if (sample_size < ncol(obj)) {
  set.seed(42)
  keep <- sample(colnames(obj), sample_size)
  obj <- subset(obj, cells = keep)
}

# --- reduction 選択（auto: harmony > rpca > pca） ---
reds <- names(obj@reductions)
if (length(reds) == 0) stop("reduction（pca 等）が見つかりません")
pick <- NULL
# ★ ver74.0: 明示したreductionが無いと別手法へ無言で切り替わっていた。
if (!is.null(reduction_opt) && reduction_opt != "auto" && !reduction_opt %in% reds)
  stop("指定したreductionが見つかりません: ", reduction_opt)
if (!is.null(reduction_opt) && reduction_opt != "auto" && reduction_opt %in% reds) {
  pick <- reduction_opt
} else {
  for (r in c("harmony", "rpca", "pca")) if (r %in% reds) { pick <- r; break }
  if (is.null(pick)) pick <- reds[1]
}
n_dims <- ncol(Embeddings(obj, pick))
if (n_dims < 1L) stop("reductionに使用できる次元がありません")
dims <- seq_len(min(30L, n_dims))

# --- 設定（object/グローバルにあれば踏襲、無ければ既定） ---
g <- function(n, d) { v <- tryCatch(get0(n, envir = .GlobalEnv, inherits = TRUE), error = function(e) NULL); if (is.null(v)) d else v }
res  <- as.numeric(g("CLUSTER_RESOLUTION", 0.5))
kp   <- ua_stability_k(ncol(obj), as.numeric(g("CLUSTER_K_PARAM", 20L)))
if (length(res) != 1L || !is.finite(res) || res <= 0) stop("CLUSTER_RESOLUTION は正の数にしてください")

# leiden(4) を試し、失敗時は louvain(1) にフォールバック
cluster_once <- function(o, seed) {
  failures <- character()
  for (algo in c(4L, 1L)) {
    res_labels <- tryCatch({
      o2 <- FindClusters(o, resolution = res, algorithm = algo,
                         random.seed = seed, verbose = FALSE)
      as.character(Idents(o2))
    }, error = function(e) { failures <<- c(failures, conditionMessage(e)); NULL })
    if (!is.null(res_labels) && length(res_labels) == ncol(o) && !anyNA(res_labels)) return(res_labels)
  }
  # ★ ver74.0: 全アルゴリズム失敗をNAラベルの「成功CSV」に変換しない。
  stop("再クラスタリングに失敗しました (seed=", seed, "): ", paste(failures, collapse = "; "))
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
