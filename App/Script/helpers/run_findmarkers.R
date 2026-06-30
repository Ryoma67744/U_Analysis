# =============================================================================
# MSI Analysis Application - On-the-fly Differential Expression (FindMarkers)
#
# アプリ内で「選択範囲/群」を ident にして Seurat::FindMarkers を実行する。
# 事前計算済み DEG ではなく、ユーザーが指定した任意の対比をその場で検定する。
#
# Usage:
#   Rscript --vanilla run_findmarkers.R <rds_path> <groups_csv> <out_csv> <mode> \
#       [--assay <name>] [--min-pct 0.05] [--logfc 0.25] [--test wilcox]
#   groups_csv: 列 CellID,Group。Group は "A"(ident.1) / "B"(ident.2)。
#   mode      : "global" = A vs 残り全体 / "local" = A vs B のみ。
#   out_csv   : 列 gene,cluster,p_val,avg_log2FC,pct.1,pct.2,p_val_adj。
#
# 科学的同一性: 強度の母集団は extract_seurat_data.R / export_region_cluster_means.R
#   と同じ JoinLayers() の data layer。検定は Wilcoxon (presto があれば高速)、
#   多重比較補正は本体 pipeline と同じ BH。
# =============================================================================

args_all <- commandArgs(trailingOnly = TRUE)
if (length(args_all) < 4) {
  stop("Usage: Rscript run_findmarkers.R <rds_path> <groups_csv> <out_csv> <mode> [--assay N] [--min-pct V] [--logfc V] [--test T]")
}
rds_path   <- args_all[1]
groups_csv <- args_all[2]
out_csv    <- args_all[3]
mode       <- args_all[4]

get_opt <- function(flag, default = NULL) {
  i <- which(args_all == flag)
  if (length(i) > 0 && i[1] < length(args_all)) return(args_all[i[1] + 1])
  default
}
assay_arg <- get_opt("--assay", NULL)
min_pct   <- as.numeric(get_opt("--min-pct", "0.05"))
logfc_th  <- as.numeric(get_opt("--logfc", "0.25"))
test_use  <- get_opt("--test", "wilcox")

if (!file.exists(rds_path))   stop("RDS file not found: ", rds_path)
if (!file.exists(groups_csv)) stop("groups_csv not found: ", groups_csv)
if (!mode %in% c("global", "local")) stop("mode must be 'global' or 'local'")

suppressPackageStartupMessages({
  library(Seurat)
  library(Matrix)
})

# --- helpers ディレクトリ解決 + qs/旧RDS 両対応ロード（export_region_cluster_means.R と同一） ---
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

# data layer を統一（FindMarkers は data layer を使用）。v5 multi-layer 対策。
tryCatch({ obj <- JoinLayers(obj) }, error = function(e) NULL)

# --- groups (CellID, Group) 読込 ---
groups <- read.csv(groups_csv, stringsAsFactors = FALSE, check.names = FALSE,
                   colClasses = "character")
if (!all(c("CellID", "Group") %in% colnames(groups))) {
  stop("groups_csv must have columns: CellID, Group")
}

# --- CellID → cell 列に対応付けて Idents を設定（非対象は "__bg__"） ---
all_cells <- colnames(obj)
ident_vec <- rep("__bg__", length(all_cells))
names(ident_vec) <- all_cells
idx <- match(groups$CellID, all_cells)
keep <- !is.na(idx)
if (!any(keep)) stop("No matching CellIDs between groups_csv and RDS.")
ident_vec[idx[keep]] <- groups$Group[keep]
Idents(obj) <- factor(ident_vec)

n_a <- sum(ident_vec == "A")
if (n_a < 3) stop("ident.1 (A) has too few cells: ", n_a)

# --- 対比の実行 ---
if (mode == "local") {
  n_b <- sum(ident_vec == "B")
  if (n_b < 3) stop("ident.2 (B) has too few cells: ", n_b)
  markers <- FindMarkers(obj, ident.1 = "A", ident.2 = "B",
                         test.use = test_use, min.pct = min_pct,
                         logfc.threshold = logfc_th, only.pos = FALSE)
  cl_label <- "A_vs_B"
} else {
  # global: A vs それ以外すべて（ident.2 = NULL）
  markers <- FindMarkers(obj, ident.1 = "A", ident.2 = NULL,
                         test.use = test_use, min.pct = min_pct,
                         logfc.threshold = logfc_th, only.pos = FALSE)
  cl_label <- "A_vs_rest"
}

# BH 補正（FindMarkers 既定の Bonferroni を本体 pipeline と同じ BH に統一） ---
markers$p_val_adj <- p.adjust(markers$p_val, method = "BH")
markers$gene <- rownames(markers)
markers$cluster <- cl_label

# pixel 単位の探索的ランキング（空間自己相関未補正・群間検定ではない）である旨を明記
# （TIMS テンプレ markers_annotated.csv と同一の文言・列）。
markers$ranking_type   <- "exploratory_pixel_level"
markers$inference_note <- "Exploratory pixel-level ranking; spatial autocorrelation not modeled; NOT sample-level statistical inference"

want <- c("gene", "cluster", "p_val", "avg_log2FC", "pct.1", "pct.2", "p_val_adj",
          "ranking_type", "inference_note")
want <- want[want %in% colnames(markers)]
out <- markers[, want, drop = FALSE]

dir.create(dirname(out_csv), recursive = TRUE, showWarnings = FALSE)
write.csv(out, out_csv, row.names = FALSE)
cat("Wrote DE markers:", nrow(out), "features (mode=", mode, ", A=", n_a, ") ->",
    out_csv, "\n")
