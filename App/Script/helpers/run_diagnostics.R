#!/usr/bin/env Rscript
# =============================================================================
# run_diagnostics.R  —  UMAP Preflight / Batch補正診断の CLI ドライバ（Phase 1）
#
# 既存 or ReductionReady の RDS（PCA/Harmony/RPCA 計算済み）を入力に、reduction 別に
#   ① 設計監査（batch×bio の交絡チェック）
#   ② 空間診断（バッチ混合 strata内 ＋ 構造保存 ＋ 参照あれば生物保存）
#   ③ Preflight（dims / n.neighbors）
# を実行し diagnostics.json を出力する。UMAP は実行しない。自動適用もしない。
#
# 使い方:
#   Rscript run_diagnostics.R --rds <path> [--rds <path> ...] --out <dir> \
#       [--batch-var sample] [--bio-var condition] [--strata-var slice_id] \
#       [--labels labels.csv] [--reduction auto] [--max-spots 20000] [--seed 42]
#
# 環境変数 R_HELPERS_DIR があれば embedding_diagnostics.R / rds_io.R をそこから source。
# 注: 本環境では R 実行検証は未実施（R 未インストール）。実行時に動作確認すること。
# =============================================================================

suppressWarnings(suppressMessages({
  ok_seurat <- requireNamespace("Seurat", quietly = TRUE)
}))

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0 || (length(a) == 1 && is.na(a))) b else a

# ---- 簡易引数パーサ ----------------------------------------------------------
parse_args <- function(a) {
  out <- list(rds = character(0), out = NULL, batch_var = "sample", bio_var = NA_character_,
              strata_var = NA_character_, labels = NA_character_, reduction = "auto",
              max_spots = 20000L, seed = 42L)
  i <- 1L
  while (i <= length(a)) {
    k <- a[i]
    val <- if (i < length(a)) a[i + 1L] else NA_character_
    switch(k,
      "--rds"        = { out$rds <- c(out$rds, val); i <- i + 2L },
      "--out"        = { out$out <- val; i <- i + 2L },
      "--batch-var"  = { out$batch_var <- val; i <- i + 2L },
      "--bio-var"    = { out$bio_var <- val; i <- i + 2L },
      "--strata-var" = { out$strata_var <- val; i <- i + 2L },
      "--labels"     = { out$labels <- val; i <- i + 2L },
      "--reduction"  = { out$reduction <- val; i <- i + 2L },
      "--max-spots"  = { out$max_spots <- as.integer(val); i <- i + 2L },
      "--seed"       = { out$seed <- as.integer(val); i <- i + 2L },
      { i <- i + 1L }
    )
  }
  out
}

# ---- helpers の source -------------------------------------------------------
source_helpers <- function() {
  hdir <- Sys.getenv("R_HELPERS_DIR", unset = NA)
  cand_dirs <- c(if (!is.na(hdir) && nzchar(hdir)) hdir,
                 dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1])))
  for (d in cand_dirs) {
    if (is.na(d) || !nzchar(d)) next
    f1 <- file.path(d, "embedding_diagnostics.R")
    f2 <- file.path(d, "rds_io.R")
    if (file.exists(f1)) source(f1)
    if (file.exists(f2)) suppressWarnings(try(source(f2), silent = TRUE))
  }
  if (!exists("preflight_diagnose")) stop("embedding_diagnostics.R を読み込めませんでした。R_HELPERS_DIR を確認してください。")
}

load_obj <- function(path) {
  if (exists("load_rds_compact")) {
    obj <- tryCatch(load_rds_compact(path), error = function(e) NULL)
    if (!is.null(obj)) return(obj)
  }
  readRDS(path)
}

# 一部のRDSは list(obj=..., reduction=...) 形式（TIMS Step2）
unwrap_obj <- function(x) if (is.list(x) && !is.null(x$obj)) x$obj else x

detect_reductions <- function(obj, want = "auto") {
  reds <- tryCatch(names(obj@reductions), error = function(e) character(0))
  reds <- setdiff(reds, c("umap", "tsne"))
  if (identical(want, "auto") || is.na(want) || !nzchar(want)) return(reds)
  intersect(want, reds)
}

# ---- 設計監査（交絡チェック）------------------------------------------------
diagnose_design <- function(meta, batch_var, bio_var) {
  if (is.na(bio_var) || !(bio_var %in% colnames(meta)) || !(batch_var %in% colnames(meta))) {
    return(list(status = "unknown", reason = "batch または bio 変数が無い"))
  }
  tb <- table(meta[[batch_var]], meta[[bio_var]])
  # 各 batch が単一 bio に偏っている（完全交絡）か
  per_batch_bio <- apply(tb, 1, function(r) sum(r > 0))
  fully_confounded <- all(per_batch_bio <= 1)
  if (fully_confounded) {
    return(list(status = "not_identifiable",
                reason = "BATCH_FULLY_CONFOUNDED_WITH_BIOLOGY",
                detail = "各バッチが単一の生物条件に偏っており、技術差と生物差を分離不能"))
  }
  list(status = "identifiable")
}

main <- function() {
  args <- parse_args(commandArgs(trailingOnly = TRUE))
  if (is.null(args$out) || length(args$rds) == 0) {
    stop("使用法: Rscript run_diagnostics.R --rds <path> [...] --out <dir> [--batch-var sample] ...")
  }
  dir.create(args$out, showWarnings = FALSE, recursive = TRUE)
  source_helpers()

  result <- list(
    schema = "umap_preflight+batch_diagnostics/v1",
    generated_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%S"),
    params = list(batch_var = args$batch_var, bio_var = args$bio_var,
                  strata_var = args$strata_var, max_spots = args$max_spots, seed = args$seed),
    inputs = list()
  )

  for (path in args$rds) {
    entry <- list(rds = path, reductions = list(), error = NULL)
    obj <- tryCatch(unwrap_obj(load_obj(path)), error = function(e) { entry$error <<- conditionMessage(e); NULL })
    if (is.null(obj)) { result$inputs[[length(result$inputs) + 1L]] <- entry; next }

    meta <- tryCatch(obj@meta.data, error = function(e) data.frame())
    n_all <- nrow(meta)
    set.seed(args$seed)
    keep <- if (n_all > args$max_spots) sort(sample.int(n_all, args$max_spots)) else seq_len(n_all)

    batch  <- if (args$batch_var %in% colnames(meta)) meta[[args$batch_var]][keep] else NULL
    strata <- if (!is.na(args$strata_var) && args$strata_var %in% colnames(meta)) meta[[args$strata_var]][keep] else NULL
    bio    <- if (!is.na(args$bio_var) && args$bio_var %in% colnames(meta)) meta[[args$bio_var]][keep] else NULL

    entry$design <- diagnose_design(meta[keep, , drop = FALSE], args$batch_var, args$bio_var)
    entry$n_spots_total <- n_all
    entry$n_spots_used  <- length(keep)

    reds <- detect_reductions(obj, args$reduction)
    for (red in reds) {
      emb <- tryCatch(Seurat::Embeddings(obj, reduction = red)[keep, , drop = FALSE],
                      error = function(e) NULL)
      if (is.null(emb) || ncol(emb) < 2) next
      rd <- list(reduction = red, n_dims = ncol(emb))
      rd$preflight <- tryCatch(preflight_diagnose(emb, seed = args$seed),
                               error = function(e) list(error = conditionMessage(e)))
      if (!is.null(batch) && length(unique(batch)) >= 2) {
        rd$space <- tryCatch(space_diagnose(emb, batch, strata = strata, ref_labels = bio, seed = args$seed),
                             error = function(e) list(error = conditionMessage(e)))
      } else {
        rd$space <- list(note = "batch 変数が無い/単一のため混合は評価せず")
      }
      entry$reductions[[red]] <- rd
    }
    result$inputs[[length(result$inputs) + 1L]] <- entry
  }

  out_path <- file.path(args$out, "diagnostics.json")
  if (requireNamespace("jsonlite", quietly = TRUE)) {
    writeLines(jsonlite::toJSON(result, auto_unbox = TRUE, pretty = TRUE, null = "null", na = "null"), out_path)
  } else {
    # 最小フォールバック（jsonlite 無し）
    writeLines(paste0("{\"error\":\"jsonlite unavailable\",\"n_inputs\":", length(result$inputs), "}"), out_path)
  }
  cat(">> diagnostics written:", out_path, "\n")
}

if (identical(environment(), globalenv())) {
  tryCatch(main(), error = function(e) { cat("ERROR:", conditionMessage(e), "\n"); quit(status = 1L) })
}
