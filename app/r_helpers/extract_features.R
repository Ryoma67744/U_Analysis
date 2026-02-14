# =============================================================================
# MSI Analysis Application - Feature Expression Extractor
# 単一 Feature の発現量を抽出
#
# Usage: Rscript extract_features.R <rds_path> <feature_name> <output_path>
# =============================================================================

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) {
  stop("Usage: Rscript extract_features.R <rds_path> <feature_name> <output_path>")
}

rds_path     <- args[1]
feature_name <- args[2]
output_path  <- args[3]

if (!file.exists(rds_path)) {
  stop("RDS file not found: ", rds_path)
}

suppressPackageStartupMessages(library(Seurat))

obj <- readRDS(rds_path)
expr_data <- GetAssayData(obj, slot = "data")

if (!(feature_name %in% rownames(expr_data))) {
  stop("Feature not found: ", feature_name)
}

values <- as.numeric(expr_data[feature_name, ])

dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)
write.csv(data.frame(expression = values), output_path,
          row.names = FALSE, col.names = FALSE)

cat("Extracted feature:", feature_name, "(", length(values), "values)\n")
