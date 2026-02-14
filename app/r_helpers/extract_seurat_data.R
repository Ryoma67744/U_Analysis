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

obj <- readRDS(rds_path)

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

# --- Build plot_data ---
plot_data <- data.frame(
  CellID   = cell_ids,
  UMAP_1   = umap_coords[, 1],
  UMAP_2   = umap_coords[, 2],
  Cluster  = clusters,
  Sample   = as.character(meta$orig.ident),
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
expr_data <- GetAssayData(obj, slot = "data")
features <- rownames(expr_data)
writeLines(features, file.path(output_dir, "features_list.txt"))
cat("Wrote features_list.txt (", length(features), " features)\n")

# --- Metadata JSON ---
samples <- unique(as.character(meta$orig.ident))
meta_info <- list(
  n_cells    = nrow(plot_data),
  n_clusters = length(unique(clusters)),
  n_features = length(features),
  samples    = samples,
  has_umap   = has_umap,
  has_spatial = ("SpatialX" %in% colnames(plot_data))
)
jsonlite::write_json(meta_info, file.path(output_dir, "extraction_meta.json"),
                     auto_unbox = TRUE, pretty = TRUE)
cat("Wrote extraction_meta.json\n")

cat("Extraction complete.\n")
