# ★ ver77.0: UMAP_1 の末尾を m/z=1 と誤認しない、解析出力の列役割契約。
ua_parquet_feature_columns <- function(schema) {
  raw <- schema$metadata[["ua_column_roles"]]
  if (is.null(raw)) return(NULL)
  fail <- function() stop("Parquet の列役割メタデータが不正です。推測で再入力できません。")
  obj <- tryCatch(jsonlite::fromJSON(raw, simplifyVector = FALSE), error = function(e) fail())
  if (!identical(as.integer(obj$schema_version), 1L) || !is.list(obj$columns)) fail()
  roles <- c("identity", "spatial", "annotation", "intensity", "cluster", "embedding", "quality", "roi", "statistic")
  names_seen <- character(); features <- character()
  for (entry in obj$columns) {
    if (!is.character(entry$name) || length(entry$name) != 1L ||
        !is.character(entry$role) || length(entry$role) != 1L ||
        !entry$role %in% roles || entry$name %in% names_seen) fail()
    names_seen <- c(names_seen, entry$name)
    if (entry$role == "intensity") features <- c(features, entry$name)
  }
  if (anyDuplicated(schema$names) || !setequal(names_seen, schema$names)) fail()
  features
}
