# ★ ver74.0: 5桁表示名をIDにすると6桁Parquetの別質量が合流するため、
# 新規読込のfeature IDは6桁へ統一する。旧RDSのIDは読み替えない。
ua_tims_feature_ids <- function(mz, allow_duplicates = FALSE) {
  mz <- suppressWarnings(as.numeric(mz))
  if (!length(mz) || any(!is.finite(mz)) || any(mz <= 0))
    stop("feature m/z は有限の正数である必要があります")
  ids <- sprintf("m/z %.6f", mz)
  if (!isTRUE(allow_duplicates) && anyDuplicated(ids))
    stop("m/zが小数6桁のfeature IDで衝突します。暗黙の統合は行いません")
  ids
}

# 校正の対象は再解析用一時名ではなく、保持した元キーで決定する。
ua_calibration_source_key <- function(metadata, coefficients, fallback_keys = character()) {
  saved <- if ("calibration_source_key" %in% names(metadata))
    unique(as.character(metadata$calibration_source_key)) else character()
  saved <- saved[!is.na(saved) & nzchar(saved)]
  if (length(saved) > 1L) stop("単一入力に複数の校正元キーがあります")
  if (length(saved)) {
    if (!saved %in% names(coefficients))
      stop("保存された校正元キーの係数がありません: ", saved)
    return(saved)
  }
  keys <- unique(as.character(fallback_keys))
  keys <- keys[!is.na(keys) & nzchar(keys) & keys %in% names(coefficients)]
  if (length(keys) > 1L && !all(vapply(keys[-1L], function(key)
      identical(as.numeric(coefficients[[key]]), as.numeric(coefficients[[keys[1L]]])), logical(1))))
    stop("入力に複数の異なる校正係数が対応しています")
  if (length(keys)) keys[1L] else ""
}

# ★ ver67.0: 名前は特徴量 ID と分離し、選択入力の SCiLS 名を優先する。
# 一部の名称競合で全分子が無名になる処理と、DB による既存名の上書きを防ぐ。
.naming_mz <- function(feature) {
  head <- trimws(strsplit(as.character(feature), "|", fixed = TRUE)[[1]][1])
  if (grepl("_[0-9]+[.][0-9]+$", head))
    return(suppressWarnings(as.numeric(sub("^.*_([0-9]+[.][0-9]+)$", "\\1", head))))
  suppressWarnings(as.numeric(sub("^(mz_|m/z[[:space:]]*)", "", head)))
}
.naming_embedded <- function(feature) {
  head <- trimws(strsplit(as.character(feature), "|", fixed = TRUE)[[1]][1])
  if (grepl("^.+_[0-9]+[.][0-9]+$", head)) {
    name <- sub("_[0-9]+[.][0-9]+$", "", head)
    if (!tolower(name) %in% c("no db hit", "mz", "nan", "na")) return(name)
  }
  ""
}
apply_feature_naming_policy <- function(markers, output_dir, db_enabled = FALSE) {
  if (is.null(markers) || !"gene" %in% names(markers)) return(markers)
  source_file <- file.path(output_dir, "feature_annotation_sources.csv")
  table <- if (file.exists(source_file)) read.csv(source_file, stringsAsFactors = FALSE, check.names = FALSE) else NULL
  if (!is.null(table) && all(c("mz", "compound") %in% names(table))) {
    table$mz <- suppressWarnings(as.numeric(table$mz))
    table <- table[is.finite(table$mz), , drop = FALSE]
  } else table <- NULL
  old <- if ("annotation" %in% names(markers)) as.character(markers$annotation) else as.character(markers$gene)
  genes <- unique(as.character(markers$gene))
  resolved <- setNames(character(length(genes)), genes)
  statuses <- setNames(rep("unannotated", length(genes)), genes)
  candidates <- setNames(character(length(genes)), genes)
  for (gene in genes) {
    mz <- .naming_mz(gene)
    rows <- NULL
    if (!is.null(table) && nrow(table) && is.finite(mz)) {
      source <- if ("source_file" %in% names(table)) as.character(table$source_file) else rep("source", nrow(table))
      for (ids in split(seq_len(nrow(table)), source)) {
        distances <- abs(table$mz[ids] - mz)
        if (min(distances) <= 0.005)
          rows <- rbind(rows, table[ids[abs(distances - min(distances)) <= 1e-10], , drop = FALSE])
      }
    }
    if (!is.null(rows)) {
      names_ok <- !is.na(rows$compound) & nzchar(trimws(rows$compound)) & !tolower(trimws(rows$compound)) %in% c("no db hit", "na", "nan", "none")
      rows <- rows[names_ok, , drop = FALSE]
    }
    if (!is.null(rows) && nrow(rows)) {
      names_found <- unique(trimws(rows$compound))
      adducts <- if ("adduct" %in% names(rows)) unique(na.omit(rows$adduct[nzchar(rows$adduct)])) else character()
      candidates[gene] <- paste(names_found, collapse = " | ")
      if (length(names_found) > 1 || length(adducts) > 1) statuses[gene] <- "conflict" else {
        resolved[gene] <- names_found[1]
        statuses[gene] <- "SCiLS"
      }
    } else {
      resolved[gene] <- .naming_embedded(gene)
      if (nzchar(resolved[gene])) statuses[gene] <- "embedded"
    }
  }
  annotations <- unname(resolved[as.character(markers$gene)])
  status <- unname(statuses[as.character(markers$gene)])
  db_allowed <- isTRUE(db_enabled) & status == "unannotated" & !is.na(old) & nzchar(old)
  annotations[db_allowed] <- old[db_allowed]
  status[db_allowed & old != as.character(markers$gene)] <- "DB"
  annotations[!nzchar(annotations)] <- as.character(markers$gene)[!nzchar(annotations)]
  markers$annotation <- annotations
  markers$annotation_source <- status
  markers$annotation_candidates <- unname(candidates[as.character(markers$gene)])
  markers
}
