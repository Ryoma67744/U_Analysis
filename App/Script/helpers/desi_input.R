# ★ ver74.0: DESIの空欄ヘッダを詰めると名称と強度の列対応がずれる。
# 通常解析・再解析が同じreaderを使い、列位置と末尾省略を明示的に扱う。
ua_desi_cells <- function(line) {
  # strsplitは末尾空欄を落とすため、番兵を添えて位置を保持する。
  cells <- strsplit(paste0(sub("\r$", "", line), "\t__ua_end__"), "\t", fixed = TRUE)[[1]]
  trimws(cells[-length(cells)])
}

ua_desi_is_data_line <- function(line) {
  cells <- ua_desi_cells(line)
  if (length(cells) < 3L || !grepl("^[+-]?[0-9]+$", cells[1L])) return(FALSE)
  all(is.finite(suppressWarnings(as.numeric(cells[seq_len(3L)]))))
}

ua_desi_header <- function(file_path) {
  probe <- readLines(file_path, n = 12L, warn = FALSE)
  hit <- which(vapply(probe, ua_desi_is_data_line, logical(1)))
  if (!length(hit) || hit[1L] < 2L) stop("DESIのデータ開始行を特定できません: ", file_path)
  n_header <- hit[1L] - 1L
  rows <- lapply(probe[seq_len(n_header)], function(line) {
    cells <- ua_desi_cells(line)
    if (length(cells) > 3L) cells[-seq_len(3L)] else character()
  })
  compounds <- numbers <- q1 <- q3 <- character()
  if (n_header >= 5L) {
    compounds <- rows[[n_header - 3L]]; numbers <- rows[[n_header - 2L]]
    q1 <- rows[[n_header - 1L]]; q3 <- rows[[n_header]]
  } else if (n_header == 4L && !any(nzchar(rows[[4L]]))) {
    numbers <- rows[[2L]]; compounds <- rows[[3L]]
  } else if (n_header == 4L) {
    numbers <- rows[[2L]]; q1 <- rows[[3L]]; q3 <- rows[[4L]]
  } else stop("DESIヘッダ形式を判定できません: ", file_path)
  last_value <- function(x) { at <- which(nzchar(x)); if (length(at)) max(at) else 0L }
  n_features <- max(vapply(list(compounds, numbers, q1, q3), last_value, integer(1)))
  if (n_features < 1L) stop("DESI特徴量ヘッダが空です: ", file_path)
  pad <- function(x) { length(x) <- n_features; x[is.na(x)] <- ""; x }
  compounds <- pad(compounds); numbers <- pad(numbers); q1 <- pad(q1); q3 <- pad(q3)
  names <- vapply(seq_len(n_features), function(i) {
    compound <- sub("_[0-9]+_[0-9]+$", "", compounds[i])
    transition <- if (nzchar(q1[i]) && nzchar(q3[i])) paste(q1[i], q3[i], sep = "-") else
      if (nzchar(q1[i])) q1[i] else if (nzchar(q3[i])) paste0("Q3=", q3[i]) else ""
    if (nzchar(compound) && nzchar(transition)) return(paste0(compound, " (", transition, ")"))
    if (nzchar(compound)) return(compound)
    if (nzchar(transition)) return(transition)
    paste0("feature-", if (nzchar(numbers[i])) numbers[i] else i)
  }, character(1))
  list(n_header = n_header, n_features = n_features, feature_names = make.unique(names))
}

ua_read_desi_data <- function(file_path, sample_prefix = NULL) {
  hdr <- ua_desi_header(file_path)
  # 列を全空という理由で削除すると後続MRM/ROIがずれるため、位置を保持する。
  data_df <- data.table::fread(file_path, sep = "\t", skip = hdr$n_header,
    header = FALSE, fill = TRUE, blank.lines.skip = TRUE, colClasses = "character",
    na.strings = NULL, data.table = FALSE, showProgress = FALSE)
  if (!nrow(data_df) || ncol(data_df) < 3L) stop("DESIのデータ行がありません: ", file_path)
  expected <- 3L + hdr$n_features
  original_width <- ncol(data_df)
  if (ncol(data_df) < expected) {
    for (j in seq.int(ncol(data_df) + 1L, expected)) data_df[[j]] <- ""
  }
  coords <- lapply(data_df[seq_len(3L)], function(x) suppressWarnings(as.numeric(x)))
  if (any(!is.finite(unlist(coords))) || any(coords[[1]] != floor(coords[[1]])) ||
      anyDuplicated(coords[[1]])) stop("DESIの画素ID/座標が欠損・不正・重複しています: ", file_path)
  mz <- as.matrix(data_df[seq.int(4L, expected)])
  blank <- is.na(mz) | !nzchar(trimws(mz))
  # Watersの末尾0省略だけを補う。ROIなどが後続する行の途中欠損は推測しない。
  has_extra <- if (original_width > expected) apply(data_df[seq.int(expected + 1L, original_width)], 1L,
    function(x) any(!is.na(x) & nzchar(trimws(x)))) else rep(FALSE, nrow(data_df))
  suffix_blank <- !has_extra
  for (j in rev(seq_len(hdr$n_features))) {
    suffix_blank <- suffix_blank & blank[, j]
    if (any(blank[, j] & !suffix_blank))
      stop("DESIの途中強度列に空欄があります（末尾0省略のみ補完できます）: ", file_path)
    mz[suffix_blank, j] <- "0"
  }
  values <- suppressWarnings(matrix(as.numeric(mz), nrow = nrow(mz), ncol = ncol(mz)))
  if (any(!is.finite(values))) stop("DESI強度に数値でない値または非有限値があります: ", file_path)
  spot_names <- paste0("Spot_", coords[[1L]])
  if (!is.null(sample_prefix)) {
    prefix <- gsub("[^A-Za-z0-9_\\-]", "_", sample_prefix)
    spot_names <- paste(prefix, spot_names, sep = "_")
  }
  coordinates <- data.frame(spot_index = coords[[1L]], x = coords[[2L]], y = coords[[3L]],
    spot_id = spot_names, row.names = spot_names, stringsAsFactors = FALSE)
  # ROI判定は強度列より後だけを対象にする（強度の不正値をROIへ変えない）。
  has_roi <- FALSE
  if (original_width > expected) for (ci in seq.int(original_width, expected + 1L)) {
    label <- trimws(as.character(data_df[[ci]]))
    nonempty <- label[!is.na(label) & nzchar(label)]
    if (!length(nonempty)) next
    nonnumeric <- sum(is.na(suppressWarnings(as.numeric(nonempty))))
    n_unique <- length(unique(nonempty))
    if (nonnumeric > length(nonempty) * 0.5 && n_unique <= 200L && n_unique < nrow(data_df)) {
      coordinates$ROI <- label; has_roi <- TRUE; break
    }
  }
  counts <- t(values)
  dimnames(counts) <- list(hdr$feature_names, spot_names)
  list(count_matrix = as(counts, "dgCMatrix"), coordinates = coordinates,
       metabolite_names = hdr$feature_names, has_roi = has_roi)
}
