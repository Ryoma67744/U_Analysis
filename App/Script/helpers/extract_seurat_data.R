# =============================================================================
# MSI Analysis Application - Seurat Data Extractor
# Seurat RDS → Parquet/CSV 変換ヘルパー
#
# Usage: Rscript extract_seurat_data.R <rds_path> <output_dir>
# =============================================================================

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) {
  stop("Usage: Rscript extract_seurat_data.R <rds_path> <output_dir>")
}

rds_path   <- args[1]
output_dir <- args[2]

if (!file.exists(rds_path)) {
  stop("RDS file not found: ", rds_path)
}

dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

cat("Loading Seurat object:", rds_path, "\n")
suppressPackageStartupMessages(library(Seurat))

# 共通 I/O ヘルパーを読み込み (slim qs 形式 / 旧 saveRDS 形式の両対応)
source(file.path(dirname(normalizePath(sub("^--file=", "",
        grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)[1]),
        mustWork = FALSE)), "rds_io.R"))

obj <- load_rds_compact(rds_path)

# TIMS ver13 互換: list(obj=seu, ...) 形式の場合、Seuratオブジェクトを取り出す
if (is.list(obj) && !inherits(obj, "Seurat") && "obj" %in% names(obj)) {
  cat("Detected list-wrapped Seurat object. Extracting $obj...\n")
  obj <- obj$obj
}

# --- UMAP coordinates ---
has_umap <- "umap" %in% names(obj@reductions)
if (has_umap) {
  umap_coords <- Embeddings(obj, "umap")
} else {
  # UMAP がなければ PCA の最初の2次元で代替
  umap_coords <- Embeddings(obj, "pca")[, 1:2]
  colnames(umap_coords) <- c("UMAP_1", "UMAP_2")
}

# --- Cluster IDs ---
clusters <- as.character(Idents(obj))

# --- Metadata ---
meta <- obj@meta.data
cell_ids <- rownames(meta)

# --- Sample identification ---
# 最もユニーク数が多い列を Sample として採用する
# 優先順: slice_id > condition > sample > orig.ident
sample_col <- as.character(meta$orig.ident)
best_n <- length(unique(sample_col))

candidate_cols <- c("sample", "condition", "slice_id")
for (cname in candidate_cols) {
  if (cname %in% colnames(meta)) {
    cand <- as.character(meta[[cname]])
    n_unique <- length(unique(cand[!is.na(cand) & nzchar(cand)]))
    if (n_unique > best_n) {
      sample_col <- cand
      best_n <- n_unique
      cat("Using meta$", cname, " for Sample column (", n_unique, " samples)\n", sep = "")
    }
  }
}

# --- Build plot_data ---
plot_data <- data.frame(
  CellID   = cell_ids,
  UMAP_1   = umap_coords[, 1],
  UMAP_2   = umap_coords[, 2],
  Cluster  = clusters,
  Sample   = sample_col,
  stringsAsFactors = FALSE
)

# Optional columns
if ("nCount_Spatial" %in% colnames(meta)) {
  plot_data$TotalCount <- meta$nCount_Spatial
}
if ("nFeature_Spatial" %in% colnames(meta)) {
  plot_data$nFeature <- meta$nFeature_Spatial
}

# Spatial coordinates (multiple possible column names)
x_col <- intersect(c("x", "x_coord", "X"), colnames(meta))
y_col <- intersect(c("y", "y_coord", "Y"), colnames(meta))
if (length(x_col) > 0 && length(y_col) > 0) {
  plot_data$SpatialX <- meta[[x_col[1]]]
  plot_data$SpatialY <- meta[[y_col[1]]]
}

# --- Write plot_data ---
# Try Parquet first (faster + smaller), fallback to CSV
tryCatch({
  suppressPackageStartupMessages(library(arrow))
  arrow::write_parquet(plot_data, file.path(output_dir, "plot_data.parquet"))
  cat("Wrote plot_data.parquet\n")
}, error = function(e) {
  cat("arrow not available, writing CSV instead\n")
  write.csv(plot_data, file.path(output_dir, "plot_data.csv"), row.names = FALSE)
})

# --- Cluster stats ---
cluster_counts <- as.data.frame(table(clusters), stringsAsFactors = FALSE)
colnames(cluster_counts) <- c("Cluster", "Count")
write.csv(cluster_counts, file.path(output_dir, "cluster_stats.csv"), row.names = FALSE)
cat("Wrote cluster_stats.csv\n")

# --- Features list ---
# Seurat v5 では JoinLayers() が必要（複数レイヤー対応）
tryCatch({
  obj <- JoinLayers(obj)
}, error = function(e) {
  # v4 以前では JoinLayers が存在しないため無視
  NULL
})
# v5: LayerData()、v4 fallback: GetAssayData(layer=...)
expr_data <- tryCatch({
  LayerData(obj, layer = "data")
}, error = function(e) {
  tryCatch({
    GetAssayData(obj, layer = "data")
  }, error = function(e2) {
    GetAssayData(obj, slot = "data")
  })
})
features <- rownames(expr_data)
writeLines(features, file.path(output_dir, "features_list.txt"))
cat("Wrote features_list.txt (", length(features), " features)\n")

# --- Expression matrix (for fast Python-side feature queries) ---
tryCatch({
  suppressPackageStartupMessages(library(arrow))
  cat("Exporting expression matrix to Parquet...\n")
  expr_dense <- as.matrix(expr_data)
  expr_df <- as.data.frame(t(expr_dense), check.names = FALSE)
  expr_df$CellID <- cell_ids
  # CellID を先頭カラムに
  expr_df <- expr_df[, c("CellID", setdiff(names(expr_df), "CellID"))]
  arrow::write_parquet(expr_df, file.path(output_dir, "expression_matrix.parquet"))
  cat("Wrote expression_matrix.parquet (", ncol(expr_df) - 1, " features)\n")
}, error = function(e) {
  cat("Warning: expression matrix export failed:", conditionMessage(e), "\n")
  cat("Feature queries will fall back to R subprocess.\n")
})

# --- Merged cluster data (if available) ---
has_merged <- "seurat_clusters_merged" %in% colnames(obj@meta.data)
has_merged_umap <- "umap_merged" %in% names(obj@reductions)

if (has_merged && has_merged_umap) {
  cat("Detected merged cluster data. Exporting...\n")
  merged_umap <- Embeddings(obj, "umap_merged")
  plot_data$Cluster_merged <- as.character(obj@meta.data$seurat_clusters_merged)
  plot_data$UMAP_1_merged  <- merged_umap[, 1]
  plot_data$UMAP_2_merged  <- merged_umap[, 2]
  cat("Added Cluster_merged, UMAP_1_merged, UMAP_2_merged to plot_data\n")

  # plot_data を再書き出し（マージデータ含む）
  tryCatch({
    arrow::write_parquet(plot_data, file.path(output_dir, "plot_data.parquet"))
    cat("Re-wrote plot_data.parquet (with merged data)\n")
  }, error = function(e) {
    write.csv(plot_data, file.path(output_dir, "plot_data.csv"), row.names = FALSE)
    cat("Re-wrote plot_data.csv (with merged data)\n")
  })
} else {
  cat("No merged cluster data found (umap_merged / seurat_clusters_merged).\n")
}

# --- Metadata JSON ---
samples <- unique(sample_col)
meta_info <- list(
  n_cells    = nrow(plot_data),
  n_clusters = length(unique(clusters)),
  n_features = length(features),
  samples    = samples,
  has_umap   = has_umap,
  has_spatial = ("SpatialX" %in% colnames(plot_data)),
  has_merged_clusters = (has_merged && has_merged_umap)
)
jsonlite::write_json(meta_info, file.path(output_dir, "extraction_meta.json"),
                     auto_unbox = TRUE, pretty = TRUE)
cat("Wrote extraction_meta.json\n")

cat("Extraction complete.\n")
