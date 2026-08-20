#!/usr/bin/env Rscript
# =============================================================================
# MSI Analysis Application - Batch RDS slimmer
#
# 既存の .rds ファイルを一括で DietSeurat + qs 圧縮形式に変換する CLI ツール。
# 旧 saveRDS 形式の .rds を読み込み → Seurat の scale.data / graphs を削除 →
# qs でアトミックに上書き保存する。
#
# Usage:
#   Rscript slim_existing_rds.R <target_folder> [options]
#
# Options:
#   --dry-run            読み込み・判定のみ行い、書き込みしない
#   --include=<pattern>  対象ファイルの glob パターン (既定: Step1*.rds,
#                        Step2*.rds, Step3*.rds, *_seurat*.rds)
#                        カンマ区切りで複数指定可
#   --backup             処理前に <file>.rds.bak として元ファイルを残す
#   --keep-scale         DietSeurat で scale.data を残す (削減率低下)
#   --keep-graphs        Graphs を残す
#
# Examples:
#   # dry-run: どれだけ減るかだけ確認
#   Rscript slim_existing_rds.R /path/to/Data/TIMS/Data --dry-run
#
#   # 実行 (バックアップ有り):
#   Rscript slim_existing_rds.R /path/to/Data/TIMS/Data --backup
#
#   # Step2 のみ対象:
#   Rscript slim_existing_rds.R /path/to/Data --include="Step2*.rds"
#
# Safety:
#   - 書き込みは <file>.rds.tmp に行い、成功後に file.rename でアトミック置換
#   - 読み込み失敗時は一切書き換えない
#   - 既に qs 形式のファイルはスキップ (マジックバイト判定)
# =============================================================================

suppressPackageStartupMessages({
  library(Seurat)
})

# ---- このスクリプト自身のディレクトリを解決 -------------------------------
.this_dir <- local({
  args <- commandArgs(trailingOnly = FALSE)
  file_arg <- grep("^--file=", args, value = TRUE)
  if (length(file_arg) == 0) return(getwd())
  dirname(normalizePath(sub("^--file=", "", file_arg[1]), mustWork = FALSE))
})

# rds_io.R を読み込み
source(file.path(.this_dir, "rds_io.R"))

# ---- CLI 引数パース ---------------------------------------------------------
.parse_args <- function(argv) {
  target <- NULL
  dry_run <- FALSE
  backup <- FALSE
  keep_scale <- FALSE
  keep_graphs <- FALSE
  includes <- NULL

  for (a in argv) {
    if (a == "--dry-run") {
      dry_run <- TRUE
    } else if (a == "--backup") {
      backup <- TRUE
    } else if (a == "--keep-scale") {
      keep_scale <- TRUE
    } else if (a == "--keep-graphs") {
      keep_graphs <- TRUE
    } else if (startsWith(a, "--include=")) {
      val <- sub("^--include=", "", a)
      includes <- unlist(strsplit(val, ",", fixed = TRUE))
      includes <- trimws(includes)
      includes <- includes[nzchar(includes)]
    } else if (startsWith(a, "--")) {
      stop("[slim] 未知のオプション: ", a)
    } else {
      if (!is.null(target)) stop("[slim] target_folder が複数指定されています")
      target <- a
    }
  }
  if (is.null(target)) {
    stop("[slim] Usage: Rscript slim_existing_rds.R <target_folder> [options]")
  }
  if (is.null(includes)) {
    includes <- c("Step1*.rds", "Step2*.rds", "Step3*.rds", "*_seurat*.rds")
  }
  list(target = target, dry_run = dry_run, backup = backup,
       keep_scale = keep_scale, keep_graphs = keep_graphs,
       includes = includes)
}

.format_bytes <- function(n) {
  if (!is.finite(n) || n < 0) return("?")
  units <- c("B", "KB", "MB", "GB", "TB")
  i <- 1L
  x <- as.numeric(n)
  while (x >= 1024 && i < length(units)) { x <- x / 1024; i <- i + 1L }
  sprintf("%.2f %s", x, units[i])
}

# ---- 増減率の表示 -----------------------------------------------------------
# ★ ver57.3: 書式に "-" を直書きしていたため、**ファイルが増えた場合に
#   `--4.4%` と二重マイナスになっていた**（delta は既に符号付き）。
#   増えるのは qs2 の既定圧縮レベルが gzip -6 より軽いためで、小さく圧縮の
#   効きにくいオブジェクトでは実際に起こる。**気づくべきなのはまさにその
#   ケース**なのに、表示が壊れていて読み取れなかった。
#   縮んだら "-47.6%"、増えたら "+4.4%" と読める形にする。
.format_delta <- function(delta) {
  # ★ ver58.2 (デバッグ総点検 §7.7(b)): NA を先に弾く。
  #   file.info()$size は取得できないと NA を返し、R では `if (NA >= 0)` が
  #   **警告ではなくエラーで停止する**。従来はここで軽量化のループごと落ちて
  #   いたので、1 ファイル分のサイズが取れないだけで残りが処理されなかった。
  if (length(delta) != 1L || !is.finite(delta)) return("?%")
  sprintf("%s%.1f%%", if (delta >= 0) "-" else "+", abs(delta))
}

.match_any <- function(fname, patterns) {
  for (p in patterns) {
    # glob -> regex (basic: * -> .*, ? -> .)
    rx <- utils::glob2rx(p)
    if (grepl(rx, fname, ignore.case = TRUE)) return(TRUE)
  }
  FALSE
}

# ---- メイン処理 -------------------------------------------------------------
main <- function() {
  argv <- commandArgs(trailingOnly = TRUE)
  opts <- .parse_args(argv)

  target <- normalizePath(opts$target, mustWork = FALSE)
  if (!dir.exists(target)) {
    stop("[slim] target_folder が存在しません: ", target)
  }
  cat(sprintf("[slim] Scanning %s\n", target))
  cat(sprintf("[slim] Include patterns: %s\n",
              paste(opts$includes, collapse = ", ")))
  cat(sprintf("[slim] Dry-run: %s | Backup: %s | keep_scale: %s | keep_graphs: %s\n\n",
              opts$dry_run, opts$backup, opts$keep_scale, opts$keep_graphs))

  all_rds <- list.files(target, pattern = "\\.rds$",
                        recursive = TRUE, full.names = TRUE,
                        ignore.case = TRUE)
  # フィルタ
  targets <- all_rds[vapply(all_rds, function(p)
    .match_any(basename(p), opts$includes), logical(1))]
  # 補助ファイルは除外 (tmp / bak)
  targets <- targets[!grepl("\\.tmp$|\\.bak$", targets, ignore.case = TRUE)]

  if (length(targets) == 0) {
    cat("[slim] 該当ファイルなし。\n")
    return(invisible(NULL))
  }
  cat(sprintf("[slim] %d files matched.\n\n", length(targets)))

  total_before <- 0
  total_after <- 0
  n_processed <- 0L
  n_skipped <- 0L
  n_error <- 0L
  errors <- character()
  t0 <- Sys.time()

  for (i in seq_along(targets)) {
    fp <- targets[i]
    size_before <- file.info(fp)$size
    total_before <- total_before + size_before
    tag <- sprintf("[%d/%d] %s (%s)",
                   i, length(targets), basename(fp), .format_bytes(size_before))

    # 既に qs / qs2 形式ならスキップ
    # ver57.1: 判定は消去法（旧 saveRDS 形式でなければ qs 系）なので qs2 も TRUE。
    #   どちらも zstd 系で既に軽いため、変換し直す意味がない。
    is_qs <- tryCatch(.rds_io_is_qs_file(fp), error = function(e) NA)
    if (isTRUE(is_qs)) {
      cat(sprintf("%s -> skip (既に qs/qs2 形式)\n", tag))
      n_skipped <- n_skipped + 1L
      total_after <- total_after + size_before
      next
    }

    # 読み込み
    obj <- tryCatch(load_rds_compact(fp), error = function(e) e)
    if (inherits(obj, "error")) {
      cat(sprintf("%s -> ERROR (読み込み失敗: %s)\n", tag,
                  conditionMessage(obj)))
      n_error <- n_error + 1L
      errors <- c(errors, paste0(fp, ": ", conditionMessage(obj)))
      total_after <- total_after + size_before
      next
    }

    if (opts$dry_run) {
      # サイズ見積もりのため一時的に diet して書き、サイズを測って捨てる
      tmp <- tempfile(fileext = ".rds")
      ok <- tryCatch({
        save_rds_compact(obj, tmp, diet = TRUE,
                         keep_scale = opts$keep_scale,
                         keep_graphs = opts$keep_graphs)
        TRUE
      }, error = function(e) {
        cat(sprintf("%s -> ERROR (dry-run write failed: %s)\n", tag,
                    conditionMessage(e)))
        FALSE
      })
      if (ok) {
        size_new <- file.info(tmp)$size
        delta <- 100 * (1 - size_new / size_before)
        cat(sprintf("%s -> %s (%s, dry-run)\n",
                    tag, .format_bytes(size_new), .format_delta(delta)))
        total_after <- total_after + size_new
        n_processed <- n_processed + 1L
      } else {
        total_after <- total_after + size_before
        n_error <- n_error + 1L
      }
      if (file.exists(tmp)) try(file.remove(tmp), silent = TRUE)
      next
    }

    # バックアップ
    if (opts$backup) {
      bak <- paste0(fp, ".bak")
      ok_bak <- tryCatch({ file.copy(fp, bak, overwrite = TRUE); TRUE },
                        error = function(e) FALSE)
      if (!ok_bak) {
        cat(sprintf("%s -> ERROR (backup 失敗)\n", tag))
        n_error <- n_error + 1L
        errors <- c(errors, paste0(fp, ": backup 作成失敗"))
        total_after <- total_after + size_before
        next
      }
    }

    # 上書き保存 (save_rds_compact 内部で <path>.tmp -> rename)
    ok <- tryCatch({
      save_rds_compact(obj, fp, diet = TRUE,
                       keep_scale = opts$keep_scale,
                       keep_graphs = opts$keep_graphs)
      TRUE
    }, error = function(e) {
      cat(sprintf("%s -> ERROR (書き込み失敗: %s)\n", tag,
                  conditionMessage(e)))
      FALSE
    })
    if (!ok) {
      n_error <- n_error + 1L
      errors <- c(errors, paste0(fp, ": write failed"))
      total_after <- total_after + size_before
      next
    }
    size_after <- file.info(fp)$size
    delta <- 100 * (1 - size_after / size_before)
    cat(sprintf("%s -> %s (%s)\n", tag, .format_bytes(size_after),
                .format_delta(delta)))
    total_after <- total_after + size_after
    n_processed <- n_processed + 1L

    # GC 明示 (大きな Seurat を開いた後)
    rm(obj); gc(verbose = FALSE)
  }

  dt <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
  cat("\n[slim] ============================================\n")
  cat(sprintf("[slim] Processed : %d\n", n_processed))
  cat(sprintf("[slim] Skipped   : %d (既に qs 形式)\n", n_skipped))
  cat(sprintf("[slim] Errors    : %d\n", n_error))
  cat(sprintf("[slim] Size before: %s\n", .format_bytes(total_before)))
  cat(sprintf("[slim] Size after : %s\n", .format_bytes(total_after)))
  if (total_before > 0) {
    # ★ ver58.2 (デバッグ総点検 §7.7(b)): 個別行と同じ書式に揃える。
    #   従来は "Reduction : 47.6%"（縮小＝プラス）で、個別行の "-47.6%"
    #   （縮小＝マイナス）と **同じログの中で同じ数字の符号が逆を向いて**いた。
    #   個別行の向き（ver57.3 で決めた「サイズの増減」）に合わせる。
    cat(sprintf("[slim] Size delta : %s\n",
                .format_delta(100 * (1 - total_after / total_before))))
  }
  cat(sprintf("[slim] Elapsed    : %.1f sec\n", dt))
  if (length(errors) > 0) {
    cat("\n[slim] Errors:\n")
    for (e in errors) cat("  - ", e, "\n", sep = "")
  }
  cat("[slim] ============================================\n")
}

main()
