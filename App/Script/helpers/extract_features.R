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

# ver3.8: save_rds_compact が qs 形式で保存した RDS を読めるよう
# load_rds_compact (旧 saveRDS / 新 qs を自動判定) を使用。
# 旧来の readRDS() だと qs バイナリで失敗していた。
.find_helpers_dir <- function() {
  # Rscript --file=... 経由時にスクリプトパスを取得
  args <- commandArgs(trailingOnly = FALSE)
  file_arg <- args[grep("--file=", args)]
  if (length(file_arg) > 0) {
    script_path <- sub("--file=", "", file_arg[1])
    return(dirname(normalizePath(script_path, mustWork = FALSE)))
  }
  # source() 経由時の fallback
  ofile <- tryCatch(sys.frame(1)$ofile, error = function(e) NULL)
  if (!is.null(ofile) && nzchar(ofile)) {
    return(dirname(normalizePath(ofile, mustWork = FALSE)))
  }
  return("")
}
.rds_io_path <- file.path(.find_helpers_dir(), "rds_io.R")
if (file.exists(.rds_io_path)) {
  source(.rds_io_path)
  obj <- load_rds_compact(rds_path)
} else {
  # フォールバック: helpers が見つからなければ従来通り readRDS
  obj <- readRDS(rds_path)
}

# Seurat v5 では JoinLayers() が必要（複数レイヤー対応）
tryCatch({
  obj <- JoinLayers(obj)
}, error = function(e) NULL)
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

if (!(feature_name %in% rownames(expr_data))) {
  stop("Feature not found: ", feature_name)
}

values <- as.numeric(expr_data[feature_name, ])

dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)
write.csv(data.frame(expression = values), output_path,
          row.names = FALSE, col.names = FALSE)

cat("Extracted feature:", feature_name, "(", length(values), "values)\n")
