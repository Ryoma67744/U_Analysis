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

# ★ ver74.0: TIMSのlist(obj=Seurat)保存形式にも単一feature抽出を対応させる。
if (!exists("ua_unwrap_seurat", mode = "function"))
  stop("rds_io.R の ua_unwrap_seurat が必要です")
obj <- ua_unwrap_seurat(obj)

# 発現は測定アッセイ(Spatial)から。RPCA(v4 IntegrateData)の integrated は補正値のため使わない。
if (exists("pick_measurement_assay", mode = "function")) {
  DefaultAssay(obj) <- pick_measurement_assay(obj)
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

# 強度行列と表示側metadataをcell IDで同じ順に固定する。
cell_ids <- colnames(obj)
if (anyDuplicated(colnames(expr_data)) || !setequal(cell_ids, colnames(expr_data)))
  stop("feature強度とSeuratのcell ID集合が一致しません")
values <- as.numeric(expr_data[feature_name, cell_ids])

dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)
write.csv(data.frame(expression = values), output_path,
          row.names = FALSE, col.names = FALSE)

cat("Extracted feature:", feature_name, "(", length(values), "values)\n")
