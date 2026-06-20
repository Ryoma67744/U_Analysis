#!/usr/bin/env Rscript
# =============================================================================
# regression_check.R  —  新版(v15/ver5) と 旧版(v14/ver4) の「結果が変わっていない」確認
#
# 目的:
#   設定値を「表に出して名前を付けた」だけの整理(Phase 0)で、計算結果が
#   以前と変わっていないことを自動で照合する。具体的には、同じデータ・同じ乱数で
#   旧版と新版を別々に走らせて作った 2 つの RDS を読み込み、
#     (1) クラスタの分かれ方（各スポットの所属クラスタ）が一致するか
#     (2) UMAP の地図座標が（誤差の範囲で）一致するか
#     (3) 参考: PCA / Harmony / RPCA の途中座標が一致するか
#   を比べて PASS / FAIL を表示する。
#
# 使い方:
#   # 2 つの出力 RDS を比較
#   Rscript regression_check.R --rds-a <旧版の出力.rds> --rds-b <新版の出力.rds> \
#       [--tol 1e-6] [--cluster-col seurat_clusters] [--out result.json]
#
#   # 参考: Seurat の既定値を表示（明示した定数が既定と同じかの目視確認用）
#   Rscript regression_check.R --print-seurat-defaults
#
# 環境変数 R_HELPERS_DIR があれば rds_io.R をそこから読み込む（qs 圧縮 RDS 対応）。
# 注: 本リポジトリの開発環境には R が無いため未実行。実行はあなたの解析環境で行うこと。
# =============================================================================

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0 || (length(a) == 1 && is.na(a))) b else a

# ---- 引数パーサ（run_diagnostics.R と同じ流儀）-------------------------------
parse_args <- function(a) {
  out <- list(rds_a = NA_character_, rds_b = NA_character_, tol = 1e-6,
              cluster_col = "seurat_clusters", out = NA_character_,
              print_defaults = FALSE)
  i <- 1L
  while (i <= length(a)) {
    k <- a[i]
    val <- if (i < length(a)) a[i + 1L] else NA_character_
    switch(k,
      "--rds-a"                 = { out$rds_a <- val; i <- i + 2L },
      "--rds-b"                 = { out$rds_b <- val; i <- i + 2L },
      "--tol"                   = { out$tol <- as.numeric(val); i <- i + 2L },
      "--cluster-col"           = { out$cluster_col <- val; i <- i + 2L },
      "--out"                   = { out$out <- val; i <- i + 2L },
      "--print-seurat-defaults" = { out$print_defaults <- TRUE; i <- i + 1L },
      { i <- i + 1L }
    )
  }
  out
}

# ---- helpers (rds_io.R) の source --------------------------------------------
source_helpers <- function() {
  hdir <- Sys.getenv("R_HELPERS_DIR", unset = NA)
  cand_dirs <- c(if (!is.na(hdir) && nzchar(hdir)) hdir,
                 dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1])))
  for (d in cand_dirs) {
    if (is.na(d) || !nzchar(d)) next
    f <- file.path(d, "rds_io.R")
    if (file.exists(f)) suppressWarnings(try(source(f), silent = TRUE))
  }
}

load_obj <- function(path) {
  if (exists("load_rds_compact")) {
    obj <- tryCatch(load_rds_compact(path), error = function(e) NULL)
    if (!is.null(obj)) return(obj)
  }
  readRDS(path)
}

# 一部の RDS は list(obj=..., reduction=...) 形式（TIMS Step2 など）
unwrap_obj <- function(x) if (is.list(x) && !is.null(x$obj)) x$obj else x

# クラスタ所属ラベルを取り出す（指定列が無ければ Idents で代替）
get_clusters <- function(obj, cluster_col) {
  meta <- tryCatch(obj@meta.data, error = function(e) data.frame())
  if (cluster_col %in% colnames(meta)) {
    v <- as.character(meta[[cluster_col]]); names(v) <- rownames(meta); return(v)
  }
  v <- tryCatch(as.character(Seurat::Idents(obj)), error = function(e) NULL)
  if (!is.null(v)) { names(v) <- colnames(obj); return(v) }
  stop(sprintf("クラスタ列 '%s' も Idents も見つかりません", cluster_col))
}

# UMAP に相当する reduction 名を見つける
find_umap_name <- function(obj) {
  reds <- tryCatch(names(obj@reductions), error = function(e) character(0))
  if ("umap" %in% reds) return("umap")
  hit <- grep("umap", reds, ignore.case = TRUE, value = TRUE)
  if (length(hit) > 0) hit[1] else NA_character_
}

get_emb <- function(obj, red) {
  if (is.na(red)) return(NULL)
  tryCatch(Seurat::Embeddings(obj, reduction = red), error = function(e) NULL)
}

# 2 つの行列を「共通の行名」で揃えて最大絶対差を返す
compare_embeddings <- function(ea, eb) {
  if (is.null(ea) || is.null(eb)) return(list(status = "skip", reason = "片方に存在せず"))
  common <- intersect(rownames(ea), rownames(eb))
  if (length(common) == 0) return(list(status = "skip", reason = "共通スポットなし"))
  nd <- min(ncol(ea), ncol(eb))
  da <- ea[common, seq_len(nd), drop = FALSE]
  db <- eb[common, seq_len(nd), drop = FALSE]
  max_abs <- max(abs(da - db))
  list(status = "ok", n_common = length(common), n_dims = nd, max_abs_diff = max_abs)
}

print_seurat_defaults <- function() {
  if (!requireNamespace("Seurat", quietly = TRUE)) {
    cat("Seurat が読み込めません。\n"); return(invisible())
  }
  show <- function(fn_name, fn, keys) {
    fm <- tryCatch(formals(fn), error = function(e) NULL)
    cat("\n[", fn_name, "] の既定値\n", sep = "")
    if (is.null(fm)) { cat("  (取得不可)\n"); return(invisible()) }
    for (k in keys) {
      if (k %in% names(fm)) cat(sprintf("  %-14s = %s\n", k, deparse(fm[[k]])))
      else                  cat(sprintf("  %-14s = (引数なし)\n", k))
    }
  }
  cat("=== Seurat 既定値（新版で明示した定数と一致するか目視確認用）===\n")
  show("RunUMAP.default", getFromNamespace("RunUMAP.default", "Seurat"),
       c("dims", "n.neighbors", "min.dist", "metric", "seed.use"))
  show("FindNeighbors.default", getFromNamespace("FindNeighbors.default", "Seurat"),
       c("k.param", "annoy.metric"))
  show("FindClusters", Seurat::FindClusters,
       c("resolution", "algorithm"))
}

main <- function() {
  args <- parse_args(commandArgs(trailingOnly = TRUE))

  if (isTRUE(args$print_defaults)) { print_seurat_defaults(); return(invisible()) }

  if (is.na(args$rds_a) || is.na(args$rds_b)) {
    stop("使用法: Rscript regression_check.R --rds-a <旧版.rds> --rds-b <新版.rds> [--tol 1e-6]")
  }
  source_helpers()

  oa <- unwrap_obj(load_obj(args$rds_a))
  ob <- unwrap_obj(load_obj(args$rds_b))

  # (1) クラスタ一致
  ca <- get_clusters(oa, args$cluster_col)
  cb <- get_clusters(ob, args$cluster_col)
  common_cells <- intersect(names(ca), names(cb))
  n_common <- length(common_cells)
  n_mismatch <- if (n_common > 0) sum(ca[common_cells] != cb[common_cells]) else NA_integer_
  cluster_exact <- isTRUE(n_common > 0 && n_mismatch == 0)
  ari <- NA_real_
  if (!cluster_exact && n_common > 0 && requireNamespace("aricode", quietly = TRUE)) {
    ari <- tryCatch(aricode::ARI(ca[common_cells], cb[common_cells]), error = function(e) NA_real_)
  }

  # (2) UMAP 座標一致
  ua <- get_emb(oa, find_umap_name(oa))
  ub <- get_emb(ob, find_umap_name(ob))
  umap_cmp <- compare_embeddings(ua, ub)
  umap_pass <- identical(umap_cmp$status, "ok") && umap_cmp$max_abs_diff <= args$tol

  # (3) 参考: PCA / Harmony / RPCA
  red_cmp <- list()
  for (red in c("pca", "harmony", "rpca")) {
    ra <- get_emb(oa, red); rb <- get_emb(ob, red)
    if (!is.null(ra) || !is.null(rb)) red_cmp[[red]] <- compare_embeddings(ra, rb)
  }

  # ---- 結果表示 --------------------------------------------------------------
  cat("================ 回帰チェック結果 ================\n")
  cat(sprintf("A (旧版): %s\n", args$rds_a))
  cat(sprintf("B (新版): %s\n", args$rds_b))
  cat(sprintf("共通スポット数: %d\n", n_common))
  cat("\n[1] クラスタの分かれ方\n")
  cat(sprintf("    不一致スポット数: %s / %d\n",
              ifelse(is.na(n_mismatch), "NA", as.character(n_mismatch)), n_common))
  if (!is.na(ari)) cat(sprintf("    （参考）調整ランド指数 ARI: %.6f（1.0 で完全一致）\n", ari))
  cat(sprintf("    => %s\n", ifelse(cluster_exact, "PASS（完全一致）", "FAIL（不一致あり）")))
  cat("\n[2] UMAP 地図座標\n")
  if (identical(umap_cmp$status, "ok")) {
    cat(sprintf("    最大絶対差: %.3e（許容 %.1e）\n", umap_cmp$max_abs_diff, args$tol))
    cat(sprintf("    => %s\n", ifelse(umap_pass, "PASS（許容内）", "FAIL（許容超過）")))
  } else {
    cat(sprintf("    => SKIP（%s）\n", umap_cmp$reason %||% "比較不可"))
  }
  if (length(red_cmp) > 0) {
    cat("\n[3] 参考: 途中座標（PCA / Harmony / RPCA）\n")
    for (nm in names(red_cmp)) {
      c3 <- red_cmp[[nm]]
      if (identical(c3$status, "ok"))
        cat(sprintf("    %-8s 最大絶対差: %.3e\n", nm, c3$max_abs_diff))
      else
        cat(sprintf("    %-8s SKIP（%s）\n", nm, c3$reason %||% ""))
    }
  }
  overall <- cluster_exact && (umap_pass || identical(umap_cmp$status, "skip"))
  cat("\n================================================\n")
  cat(sprintf("総合判定: %s\n", ifelse(overall, "PASS（挙動不変とみなせる）",
                                         "FAIL（差分あり・要確認）")))

  # ---- JSON 保存（任意）------------------------------------------------------
  if (!is.na(args$out)) {
    res <- list(rds_a = args$rds_a, rds_b = args$rds_b, n_common = n_common,
                cluster = list(n_mismatch = n_mismatch, exact = cluster_exact, ari = ari),
                umap = umap_cmp, umap_pass = umap_pass,
                reductions = red_cmp, tol = args$tol, overall_pass = overall)
    if (requireNamespace("jsonlite", quietly = TRUE)) {
      writeLines(jsonlite::toJSON(res, auto_unbox = TRUE, pretty = TRUE, null = "null", na = "null"),
                 args$out)
      cat(sprintf(">> 結果を保存: %s\n", args$out))
    }
  }

  if (!overall) quit(status = 2L)
}

if (identical(environment(), globalenv())) {
  tryCatch(main(), error = function(e) { cat("ERROR:", conditionMessage(e), "\n"); quit(status = 1L) })
}
