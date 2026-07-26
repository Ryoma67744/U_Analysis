# -*- coding: UTF-8 -*-
# ============================================================
# Parquet 取り込み経路の等価性テスト (ver45.5)
#
# 目的:
#   ver45.5 で read_desi_data() の Parquet 分岐を
#   「全表を密 data.frame 化」→「m/z 列をブロック単位でスパース構築」へ置き換えた。
#   本スクリプトは *旧実装をこのファイル内に再現* し、新実装（本体スクリプト）の出力と
#   完全一致することを確認する。出力が 1 ビットでも変わっていないことが導入の前提条件。
#
# 使い方 (コンテナ内):
#   Rscript App/Script/helpers/test_parquet_ingest_equiv.R <target.parquet>
#
#   引数を省略した場合は、小さめの合成 Parquet を一時生成して検証する
#   （実データが無い環境でもロジックの等価性は確認できる）。
#
# 注意:
#   実データで試す場合はメモリに載る程度のサイズを選ぶこと。旧実装は全表を密に
#   展開するため、巨大ファイルではこのテスト自体が OOM する（それこそが修正の理由）。
# ============================================================

suppressPackageStartupMessages({
  library(arrow)
  library(Matrix)
})

`%||%` <- function(a, b) if (!is.null(a)) a else b

# 本体と同じ既定値（ANNOTATION_FILTER 未設定＝全行採用）
if (!exists("ANNOTATION_FILTER")) ANNOTATION_FILTER <- NULL

# ------------------------------------------------------------
# 旧実装（ver45.4 以前）の Parquet 取り込み。比較用に再現。
# ------------------------------------------------------------
read_parquet_legacy <- function(file_path, sample_prefix = NULL) {
  pf <- arrow::ParquetFileReader$create(file_path)
  all_names <- pf$GetSchema()$names
  mz_cols <- grep("^mz_", all_names, value = TRUE)
  is_bare_numeric <- FALSE
  is_annotated <- FALSE
  if (length(mz_cols) == 0) {
    non_meta <- setdiff(all_names, c("id", "x", "y", "annotation"))
    bare_num <- non_meta[!is.na(suppressWarnings(as.numeric(non_meta)))]
    if (length(bare_num) > 0) {
      mz_cols <- bare_num
      is_bare_numeric <- TRUE
    } else if (length(non_meta) > 0) {
      .head <- trimws(sub("\\s*\\|.*$", "", non_meta))
      .mz   <- suppressWarnings(as.numeric(sub("^.*_([0-9]+\\.?[0-9]*)$", "\\1", .head)))
      if (any(!is.na(.mz))) {
        mz_cols <- non_meta[!is.na(.mz)]
        is_annotated <- TRUE
      }
    }
  }
  need_cols <- c("id", "x", "y", mz_cols)
  if ("annotation" %in% all_names) need_cols <- c(need_cols, "annotation")
  df <- arrow::read_parquet(file_path, col_select = dplyr::all_of(need_cols),
                            as_data_frame = TRUE)

  if (is_annotated) {
    head_tok <- trimws(sub("\\s*\\|.*$", "", mz_cols))
    metabolite_names <- make.unique(head_tok)
  } else {
    mz_num <- if (is_bare_numeric) as.numeric(mz_cols) else
      suppressWarnings(as.numeric(sub("^mz_", "", mz_cols)))
    metabolite_names <- make.unique(sprintf("m/z %.5f", mz_num))
  }

  base_prefix <- gsub("[^A-Za-z0-9_-]", "_", sample_prefix %||% "Sample")
  raw_spot <- df$id
  raw_spot[is.na(raw_spot)] <- seq_len(sum(is.na(raw_spot)))
  spot_id <- paste0(base_prefix, "_Spot_", raw_spot)

  coordinates <- data.frame(
    spot_index = raw_spot,
    x = as.numeric(df$x),
    y = as.numeric(df$y),
    spot_id = spot_id,
    row.names = spot_id
  )
  if ("annotation" %in% colnames(df)) {
    coordinates$annotation <- as.character(df$annotation)
  }

  if (!is.null(ANNOTATION_FILTER) && length(ANNOTATION_FILTER) > 0 &&
      "annotation" %in% colnames(coordinates)) {
    mask <- coordinates$annotation %in% ANNOTATION_FILTER
    coordinates <- coordinates[mask, , drop = FALSE]
    df <- df[mask, , drop = FALSE]
    spot_id <- coordinates$spot_id
  }

  feat_mat <- as.matrix(df[, mz_cols, drop = FALSE])
  feat_mat[!is.finite(feat_mat)] <- 0
  count_matrix <- t(feat_mat)
  if (nrow(count_matrix) != length(metabolite_names)) {
    k <- min(nrow(count_matrix), length(metabolite_names))
    count_matrix <- count_matrix[seq_len(k), , drop = FALSE]
    metabolite_names <- metabolite_names[seq_len(k)]
  }
  rownames(count_matrix) <- metabolite_names
  colnames(count_matrix) <- spot_id
  list(count_matrix = as(count_matrix, "dgCMatrix"), coordinates = coordinates)
}

# ------------------------------------------------------------
# 新実装（ver45.5 本体）を読み込む
#   本体スクリプトは source すると解析まで走ってしまうため、read_desi_data の
#   定義部分だけを切り出して評価する。
# ------------------------------------------------------------
load_new_reader <- function(base_script) {
  src <- readLines(base_script, warn = FALSE)
  s <- grep("^read_desi_data <- function\\(", src)
  if (length(s) != 1) stop("read_desi_data の定義が特定できません: ", base_script)
  # 関数ブロックの終端を波括弧の収支で探す
  depth <- 0L; e <- NA_integer_
  for (i in s:length(src)) {
    ln <- src[i]
    depth <- depth + lengths(regmatches(ln, gregexpr("{", ln, fixed = TRUE))) -
                     lengths(regmatches(ln, gregexpr("}", ln, fixed = TRUE)))
    if (i > s && depth <= 0L) { e <- i; break }
  }
  if (is.na(e)) stop("read_desi_data の終端が特定できません")
  env <- new.env(parent = globalenv())
  # 依存する小ヘルパも取り込む
  h <- grep("^\\.parse_feature_annotations <- function\\(", src)
  if (length(h) == 1) {
    hd <- 0L; he <- NA_integer_
    for (i in h:length(src)) {
      ln <- src[i]
      hd <- hd + lengths(regmatches(ln, gregexpr("{", ln, fixed = TRUE))) -
                 lengths(regmatches(ln, gregexpr("}", ln, fixed = TRUE)))
      if (i > h && hd <= 0L) { he <- i; break }
    }
    if (!is.na(he)) eval(parse(text = paste(src[h:he], collapse = "\n")), envir = env)
  }
  assign("%||%", `%||%`, envir = env)
  assign("ANNOTATION_FILTER", ANNOTATION_FILTER, envir = env)
  eval(parse(text = paste(src[s:e], collapse = "\n")), envir = env)
  get("read_desi_data", envir = env)
}

# ------------------------------------------------------------
# 合成 Parquet（引数省略時）
# ------------------------------------------------------------
make_synthetic <- function(path, n_spots = 500L, n_feat = 40L) {
  set.seed(42)
  vals <- matrix(round(runif(n_spots * n_feat) * 100, 4), nrow = n_spots)
  # 疎性・非有限値・NA を混ぜて境界条件も検証する
  vals[sample(length(vals), length(vals) %/% 3)] <- 0
  vals[sample(length(vals), 20)] <- NA_real_
  vals[sample(length(vals), 10)] <- Inf
  vals[sample(length(vals), 10)] <- -Inf
  mzs <- sprintf("%.6f", seq(100, by = 1.234, length.out = n_feat))
  df <- data.frame(id = seq_len(n_spots),
                   x = rep(seq_len(25), length.out = n_spots),
                   y = rep(seq_len(20), each = 25, length.out = n_spots))
  for (j in seq_len(n_feat)) df[[mzs[j]]] <- vals[, j]
  df$annotation <- rep(c("sliceA", "sliceB"), length.out = n_spots)
  arrow::write_parquet(df, path)
  path
}

# ------------------------------------------------------------
# 実行
# ------------------------------------------------------------
args <- commandArgs(trailingOnly = TRUE)
this_dir <- tryCatch({
  fa <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
  dirname(normalizePath(sub("^--file=", "", fa[1])))
}, error = function(e) ".")
base_script <- file.path(this_dir, "..", "TIMS",
                         "260623_DBSCAN_With_cluster_ver6_no-png_slim.R")
if (!file.exists(base_script)) stop("ベーススクリプトが見つかりません: ", base_script)

target <- if (length(args) >= 1) args[1] else
  make_synthetic(file.path(tempdir(), "equiv_test.parquet"))
cat("Target parquet:", target, "\n")

new_reader <- load_new_reader(base_script)

cat("--- 旧実装で読み込み（基準） ---\n")
old <- read_parquet_legacy(target, sample_prefix = "Sample")

ok <- TRUE
chk <- function(label, cond, extra = "") {
  cat(sprintf("  [%s] %s%s\n", if (isTRUE(cond)) "OK  " else "FAIL", label,
              if (nzchar(extra)) paste0(" -> ", extra) else ""))
  if (!isTRUE(cond)) ok <<- FALSE
}

# 1 回分の比較。新実装を指定のブロック予算で走らせ、旧実装の結果と突き合わせる。
compare_pass <- function(pass_label, block_mb) {
  cat(sprintf("\n=== %s (INGEST_BLOCK_MB=%s) ===\n", pass_label,
              if (is.null(block_mb)) "既定(256)" else as.character(block_mb)))
  if (is.null(block_mb)) {
    Sys.unsetenv("INGEST_BLOCK_MB")
  } else {
    Sys.setenv(INGEST_BLOCK_MB = as.character(block_mb))
  }
  new <- new_reader(target, sample_prefix = "Sample")
  Sys.unsetenv("INGEST_BLOCK_MB")

  chk("count_matrix の次元", identical(dim(old$count_matrix), dim(new$count_matrix)),
      sprintf("old=%s new=%s", paste(dim(old$count_matrix), collapse = "x"),
              paste(dim(new$count_matrix), collapse = "x")))
  chk("rownames (特徴量名)", identical(rownames(old$count_matrix), rownames(new$count_matrix)))
  chk("colnames (spot ID)", identical(colnames(old$count_matrix), colnames(new$count_matrix)))
  chk("クラス", identical(class(old$count_matrix), class(new$count_matrix)),
      sprintf("old=%s new=%s", class(old$count_matrix)[1], class(new$count_matrix)[1]))
  if (identical(dim(old$count_matrix), dim(new$count_matrix))) {
    d <- max(abs(old$count_matrix - new$count_matrix))
    chk("全要素の一致 (最大絶対差 0)", isTRUE(d == 0), sprintf("max|diff|=%g", d))
    chk("非ゼロ要素数", identical(Matrix::nnzero(old$count_matrix),
                                  Matrix::nnzero(new$count_matrix)))
  }
  chk("coordinates 完全一致", isTRUE(all.equal(old$coordinates, new$coordinates)))
  invisible(NULL)
}

# パス 1: 既定のブロック予算（小さなデータでは単一ブロックになる）
compare_pass("パス1: 既定ブロック幅", NULL)

# パス 2: ブロック予算を極小にして「複数ブロックの連結」経路を強制的に通す。
#   ここを通さないと do.call(rbind, blocks) が未検証のまま本番だけで走ることになり、
#   特徴量の順序ずれ（エラーにならず結果だけ間違う）を見逃す。
compare_pass("パス2: 複数ブロック強制", 0.05)

cat("\n")
if (ok) {
  cat("RESULT: PASS - 単一ブロック・複数ブロックの両経路で新旧の出力が一致しています。\n")
  cat("  ※ パス2 の [stream] 行が 2 ブロック以上になっていることを確認してください。\n")
  cat("     1 ブロックのままなら連結経路は未検証です。\n")
  quit(status = 0)
} else {
  cat("RESULT: FAIL - 出力に差異があります。導入しないでください。\n")
  quit(status = 1)
}
