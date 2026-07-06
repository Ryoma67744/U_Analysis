# =============================================================================
# MSI Analysis Application - Region×Cluster Mean Exporter (MetaboAnalyst)
#
# H&E ROI×クラスタ群ごとの平均強度だけを、巨大な expression_matrix.parquet を
# 作らずに RDS から直接（同一 data layer・sparse のまま）計算して CSV 出力する。
#
# Usage:
#   Rscript --vanilla export_region_cluster_means.R \
#       <rds_path> <groups_csv> <out_csv> [--assay <name>] [--layer data]
#   groups_csv: 列 CellID,Group（ROI 割当済みのみ。Group=切片_ROI_クラスタ）
#   out_csv   : 行=Group, 列=feature(m/z) 平均（先頭列 Group）。
#               化合物名への列名置換は Python 側で行う（m/z 名のまま出力）。
#
# 科学的同一性: 強度の母集団は extract_seurat_data.R / extract_features.R と同じ
#   JoinLayers() -> LayerData(layer="data") （v4 fallback 付き・DefaultAssay）。
# =============================================================================

args_all <- commandArgs(trailingOnly = TRUE)
if (length(args_all) < 3) {
  stop("Usage: Rscript export_region_cluster_means.R <rds_path> <groups_csv> <out_csv> [--assay N] [--layer L] [--repr linear|counts|data]")
}
rds_path   <- args_all[1]
groups_csv <- args_all[2]
out_csv    <- args_all[3]

get_opt <- function(flag, default = NULL) {
  i <- which(args_all == flag)
  if (length(i) > 0 && i[1] < length(args_all)) return(args_all[i[1] + 1])
  default
}
assay_arg <- get_opt("--assay", NULL)
layer_arg <- get_opt("--layer", "data")
# 強度表現。linear=線形化(非log), counts=生, data=現状(log)。未指定なら --layer をそのまま使う（後方互換）。
repr_arg  <- get_opt("--repr", NA)

if (!file.exists(rds_path))   stop("RDS file not found: ", rds_path)
if (!file.exists(groups_csv)) stop("groups_csv not found: ", groups_csv)

suppressPackageStartupMessages({
  library(Seurat)
  library(Matrix)
})

# --- helpers ディレクトリ解決 ＋ qs/旧RDS 両対応ロード（extract_features.R と同一） ---
.find_helpers_dir <- function() {
  a <- commandArgs(trailingOnly = FALSE)
  fa <- a[grep("--file=", a)]
  if (length(fa) > 0) {
    return(dirname(normalizePath(sub("--file=", "", fa[1]), mustWork = FALSE)))
  }
  ""
}
.rds_io_path <- file.path(.find_helpers_dir(), "rds_io.R")
if (file.exists(.rds_io_path)) {
  source(.rds_io_path)
  obj <- load_rds_compact(rds_path)
} else {
  obj <- readRDS(rds_path)
}
# list-wrap 形式（TIMS ver13 等 list(obj=seu, ...)）の取り出し（extract_seurat_data.R と同一）
if (is.list(obj) && !inherits(obj, "Seurat") && "obj" %in% names(obj)) {
  obj <- obj$obj
}
# 強度は「測定アッセイ」から読む。RPCA(v4 IntegrateData)の integrated は補正値のため
# 定量に使わない（統合手法に依存させない）。明示 --assay があればそれを尊重。
if (exists("pick_measurement_assay", mode = "function")) {
  DefaultAssay(obj) <- pick_measurement_assay(obj, assay_arg)
} else if (!is.null(assay_arg) && nzchar(assay_arg)) {
  DefaultAssay(obj) <- assay_arg
}
assay_used <- tryCatch(DefaultAssay(obj), error = function(e) NA_character_)

# --- repr（強度表現）の解決 ---
if (is.na(repr_arg) || !nzchar(repr_arg)) {
  layer_to_read <- layer_arg; do_linearize <- FALSE       # 後方互換: --layer をそのまま
} else if (identical(repr_arg, "counts")) {
  layer_to_read <- "counts";  do_linearize <- FALSE
} else if (identical(repr_arg, "linear")) {
  layer_to_read <- "data";    do_linearize <- TRUE
} else {                                                    # "data" ほか
  layer_to_read <- "data";    do_linearize <- FALSE
}

# --- data/counts layer の取得（extract_seurat_data.R:124-139 と同規約） ---
tryCatch({ obj <- JoinLayers(obj) }, error = function(e) NULL)
expr_data <- tryCatch({
  LayerData(obj, layer = layer_to_read)
}, error = function(e) {
  tryCatch({
    GetAssayData(obj, layer = layer_to_read)
  }, error = function(e2) {
    GetAssayData(obj, slot = layer_to_read)
  })
})
# expr_data: features (rows) × cells (cols)。dgCMatrix（sparse）想定。

# --- 前処理手法タグ（線形化の逆変換／来歴表示に使用） ---
prep_method <- tryCatch({
  m <- obj@misc$preprocessing_method
  if (is.null(m) || length(m) == 0) NA_character_ else as.character(m)[1]
}, error = function(e) NA_character_)

# --- linear: preprocessing_method に応じて data を線形へ逆変換（spot 単位・sparse 維持） ---
if (do_linearize) {
  meth <- if (is.na(prep_method) || !nzchar(prep_method)) "LogNormalize" else prep_method
  ml <- tolower(meth)
  if (grepl("sqrt", ml)) {
    expr_data <- expr_data^2                 # sqrt の逆変換（0^2=0 で sparse 維持）
  } else if (grepl("none", ml)) {
    # 恒等（変換なし）
  } else {
    expr_data <- expm1(expr_data)            # log1p/LogNormalize/不明 → expm1（expm1(0)=0）
  }
}

# --- groups (CellID, Group) 読込 ---
groups <- read.csv(groups_csv, stringsAsFactors = FALSE, check.names = FALSE,
                   colClasses = "character")
if (!all(c("CellID", "Group") %in% colnames(groups))) {
  stop("groups_csv must have columns: CellID, Group")
}

# --- 対象 cell を expr_data の列に対応付け（見つからない cell は除外） ---
col_idx <- match(groups$CellID, colnames(expr_data))
keep <- !is.na(col_idx)
if (!any(keep)) stop("No matching CellIDs between groups_csv and RDS.")
groups  <- groups[keep, , drop = FALSE]
col_idx <- col_idx[keep]

sub <- expr_data[, col_idx, drop = FALSE]    # features × n_cells（sparse のまま）

# --- Group ごとの列平均を sparse 行列演算で（巨大 dense 化を回避） ---
grp_levels <- sort(unique(groups$Group))
grp_factor <- factor(groups$Group, levels = grp_levels)
G <- Matrix::sparseMatrix(                   # n_cells × n_groups の 0/1 指示行列
  i = seq_along(grp_factor),
  j = as.integer(grp_factor),
  x = 1,
  dims = c(length(grp_factor), length(grp_levels))
)
colsum  <- sub %*% G                         # features × n_groups（各群の合計, sparse%*%sparse）
n_per_g <- Matrix::colSums(G)                # 各群の cell 数
means   <- sweep(as.matrix(colsum), 2, n_per_g, "/")  # features × n_groups（平均）
means_t <- t(means)                          # n_groups × features
colnames(means_t) <- rownames(expr_data)     # m/z feature 名（parquet と同一）

out <- cbind(data.frame(Group = grp_levels, stringsAsFactors = FALSE),
             as.data.frame(means_t, check.names = FALSE))

dir.create(dirname(out_csv), recursive = TRUE, showWarnings = FALSE)
write.csv(out, out_csv, row.names = FALSE)
cat("Wrote region x cluster means:", nrow(out), "groups x",
    ncol(out) - 1, "features ->", out_csv, "\n")
# 機械可読の来歴（Python 側が stdout から回収）
cat(sprintf("REPR=%s\n",
            if (is.na(repr_arg) || !nzchar(repr_arg)) layer_arg else repr_arg))
cat(sprintf("PREPROCESSING_METHOD=%s\n",
            if (is.na(prep_method)) "" else prep_method))
cat(sprintf("ASSAY_USED=%s\n",
            if (is.na(assay_used)) "" else assay_used))
