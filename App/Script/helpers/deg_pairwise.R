# =============================================================================
# MSI Analysis Application - Pairwise DEG (cluster A vs cluster B)
#
# 2つの群(ident1, ident2)間の発現差を Seurat FindMarkers(wilcox) で計算し CSV 出力。
# 群の割当は groups_csv(CellID,Group) で渡す（Seurat クラスタでも手動クラスタでも可）。
# 全再実行は不要：キャッシュ済 RDS の data layer に対する FindMarkers のみ実行する。
#
# Usage:
#   Rscript --vanilla deg_pairwise.R \
#       <rds_path> <groups_csv> <ident1> <ident2> <out_csv> \
#       [--logfc 0.1] [--minpct 0.1] [--assay N] [--layer data]
#   groups_csv: 列 CellID,Group（Group が ident ラベル。ident1/ident2 はその値）
#   out_csv   : 列 gene,p_val,avg_log2FC,pct.1,pct.2,p_val_adj,cluster
#
# 科学的同一性: extract_seurat_data.R / export_region_cluster_means.R と同じ
#   JoinLayers() + DefaultAssay + data layer 上で FindMarkers を実行する。
# =============================================================================

args_all <- commandArgs(trailingOnly = TRUE)
if (length(args_all) < 5) {
  stop("Usage: Rscript deg_pairwise.R <rds_path> <groups_csv> <ident1> <ident2> <out_csv> [--logfc X] [--minpct Y] [--assay N] [--layer L]")
}
rds_path   <- args_all[1]
groups_csv <- args_all[2]
ident1     <- args_all[3]
ident2     <- args_all[4]
out_csv    <- args_all[5]

get_opt <- function(flag, default = NULL) {
  i <- which(args_all == flag)
  if (length(i) > 0 && i[1] < length(args_all)) return(args_all[i[1] + 1])
  default
}
logfc_th  <- as.numeric(get_opt("--logfc", "0.1"))
min_pct   <- as.numeric(get_opt("--minpct", "0.1"))
assay_arg <- get_opt("--assay", NULL)
layer_arg <- get_opt("--layer", "data")

if (!file.exists(rds_path))   stop("RDS file not found: ", rds_path)
if (!file.exists(groups_csv)) stop("groups_csv not found: ", groups_csv)

suppressPackageStartupMessages({
  library(Seurat)
  library(Matrix)
})

# --- helpers ディレクトリ解決 ＋ qs/旧RDS 両対応ロード（export_region_cluster_means.R と同一） ---
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
# list-wrap 形式（TIMS ver13 等 list(obj=seu, ...)）の取り出し
if (is.list(obj) && !inherits(obj, "Seurat") && "obj" %in% names(obj)) {
  obj <- obj$obj
}
if (!is.null(assay_arg) && nzchar(assay_arg)) {
  DefaultAssay(obj) <- assay_arg
}
# Seurat v5: 分割レイヤを統合（data layer を単一化）
tryCatch({ obj <- JoinLayers(obj) }, error = function(e) NULL)

# --- groups (CellID, Group) 読込 → ident 割当 ---
groups <- read.csv(groups_csv, stringsAsFactors = FALSE, check.names = FALSE,
                   colClasses = "character")
if (!all(c("CellID", "Group") %in% colnames(groups))) {
  stop("groups_csv must have columns: CellID, Group")
}
g <- setNames(groups$Group, groups$CellID)
common <- intersect(names(g), colnames(obj))
if (length(common) == 0) stop("No matching CellIDs between groups_csv and RDS.")
obj <- subset(obj, cells = common)
Idents(obj) <- g[colnames(obj)]

n1 <- sum(Idents(obj) == ident1)
n2 <- sum(Idents(obj) == ident2)
if (n1 < 3 || n2 < 3) {
  stop(sprintf("Too few cells for FindMarkers: ident1(%s)=%d, ident2(%s)=%d",
               ident1, n1, ident2, n2))
}

# --- A vs B 発現差（Wilcoxon, extract と同一 data layer 上） ---
mk <- FindMarkers(obj, ident.1 = ident1, ident.2 = ident2,
                  test.use = "wilcox",
                  logfc.threshold = logfc_th, min.pct = min_pct)
mk$gene    <- rownames(mk)
mk$cluster <- paste0(ident1, " vs ", ident2)
keep_cols <- c("gene", "p_val", "avg_log2FC", "pct.1", "pct.2",
               "p_val_adj", "cluster")
keep_cols <- keep_cols[keep_cols %in% colnames(mk)]
out <- mk[, keep_cols, drop = FALSE]

dir.create(dirname(out_csv), recursive = TRUE, showWarnings = FALSE)
write.csv(out, out_csv, row.names = FALSE)
cat("Wrote pairwise DEG:", nrow(out), "features for", ident1, "vs", ident2,
    "->", out_csv, "\n")
