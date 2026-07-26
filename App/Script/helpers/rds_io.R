# =============================================================================
# MSI Analysis Application - Compact RDS I/O helpers
#
# 役割:
#   Seurat / list(Seurat, ...) オブジェクトの保存と読み込みを、
#   情報損失なし で大幅軽量化する共通ヘルパー。
#   （DietSeurat により scale.data を落とし、qs により高圧縮保存）
#
# 提供関数:
#   - save_rds_compact(obj, path, diet = TRUE, keep_scale = FALSE,
#                      keep_graphs = FALSE)
#   - load_rds_compact(path, ensure_scale = FALSE)
#   - diet_seurat_safe(obj, keep_scale = FALSE, keep_graphs = FALSE)
#
# 後方互換性:
#   load_rds_compact はファイル先頭のマジックバイトを判定し、
#   旧 saveRDS 形式 (gzip / xz / bzip2) と qs 形式の両方を透過的に読む。
#   よって既存の .rds 資産は一切の変換なしでそのまま利用可能。
#
# 保存形式:
#   拡張子は .rds のまま維持する (Python / config 側の副作用を避けるため)。
#   qs バイナリを .rds という名前で保存し、読み込み側で自動判定する。
# =============================================================================

# ---- qs パッケージの可用性チェック (一度だけ) -----------------------------
.rds_io_has_qs <- function() {
  # メモ化 (同一セッション内で複数回問い合わせる場合)
  cached <- getOption("msi.rds_io.has_qs", default = NA)
  if (!is.na(cached)) return(isTRUE(cached))
  has_qs <- requireNamespace("qs", quietly = TRUE)
  options(msi.rds_io.has_qs = has_qs)
  has_qs
}

# ---- マジックバイトから qs フォーマットを判定 -----------------------------
.rds_io_is_qs_file <- function(path) {
  if (!file.exists(path) || file.info(path)$size < 4) return(FALSE)
  con <- file(path, "rb")
  on.exit(close(con), add = TRUE)
  bytes <- readBin(con, what = "raw", n = 4)
  # qs のシリアライズマジック: 最初の 4 バイトが 0x0B 0x0E 0x0A 0x0C 系
  # (qs v0.25+ は固定 "QSSO" 系ではなく独自ヘッダを持つため、readRDS で
  # 失敗するかどうかで判定するのが最も確実。よってここでは
  # readRDS ヘッダ (RDS v2/v3 = "X\n" / "B\n" / ASCII "RDX2") と
  # R の gzipped/xz/bz2 シグネチャを "旧形式" として扱い、それ以外は qs とみなす)
  # 旧 R saveRDS 形式の先頭バイト:
  #   gzip (default):  0x1F 0x8B
  #   bzip2:           0x42 0x5A (BZ)
  #   xz:              0xFD 0x37 0x7A 0x58
  #   uncompressed:    先頭 2 バイトが "X\n" (0x58 0x0A) or "B\n" or "A\n"
  b1 <- as.integer(bytes[1])
  b2 <- as.integer(bytes[2])
  b3 <- as.integer(bytes[3])
  b4 <- as.integer(bytes[4])
  is_gzip <- (b1 == 0x1F && b2 == 0x8B)
  is_bzip <- (b1 == 0x42 && b2 == 0x5A)
  is_xz   <- (b1 == 0xFD && b2 == 0x37 && b3 == 0x7A && b4 == 0x58)
  is_plain_rds <- (b1 %in% c(0x58, 0x42, 0x41) && b2 == 0x0A)
  is_legacy <- is_gzip || is_bzip || is_xz || is_plain_rds
  !is_legacy
}

# ---- Seurat を安全に Diet する ---------------------------------------------
#  obj: Seurat または list。list の場合は各要素に再帰適用。
#  keep_scale: TRUE なら scale.data を保持 (デフォルト: FALSE = 削除)
#  keep_graphs: TRUE なら Graphs を保持 (デフォルト: FALSE = 削除)
diet_seurat_safe <- function(obj, keep_scale = FALSE, keep_graphs = FALSE,
                             keep_counts = TRUE) {
  # list: 再帰適用 (ただし data.frame は list を継承するので除外)
  if (is.list(obj) && !inherits(obj, "Seurat") && !is.data.frame(obj)) {
    return(lapply(obj, diet_seurat_safe,
                  keep_scale = keep_scale, keep_graphs = keep_graphs,
                  keep_counts = keep_counts))
  }
  # Seurat 以外はそのまま返す
  if (!inherits(obj, "Seurat")) return(obj)

  # DietSeurat は Seurat パッケージに依存。失敗時は安全側で元を返す。
  # ver3.8: Seurat 5.0+ で counts/data/scale.data 引数は deprecated 警告を
  # 出すが、動作上は問題ない。警告がログを汚染するため suppressWarnings で
  # 抑制 (将来的には layers 引数に移行すべきだが、本タスクでは抑制のみ)。
  tryCatch({
    if (!requireNamespace("Seurat", quietly = TRUE)) return(obj)
    dimreducs <- names(obj@reductions)
    if (length(dimreducs) == 0) dimreducs <- NULL
    graphs <- if (keep_graphs) names(obj@graphs) else NULL
    suppressWarnings(Seurat::DietSeurat(
      obj,
      counts = keep_counts,
      data = TRUE,
      scale.data = keep_scale,
      dimreducs = dimreducs,
      graphs = graphs
    ))
  }, error = function(e) {
    message("[rds_io] diet_seurat_safe failed, returning original: ",
            conditionMessage(e))
    obj
  })
}

# ---- 測定強度アッセイの選択 -------------------------------------------------
#  強度/発現の定量は「測定アッセイ」から読む。RPCA(v4 IntegrateData)の
#  "integrated" は バッチ補正後の再構成値（負値を取りうる）であり測定強度ではない
#  ため、統合手法に依存せず常に生アッセイ(既定 Spatial)を優先する。
#  UMAP座標/クラスタは reduction・Idents 由来で assay に依存しないため本選択の影響外。
#
#  assay_arg: 明示指定があれば最優先（後方互換 / 特殊用途）。
#  戻り値: 採用アッセイ名（Spatial > integrated/SCT 以外の生アッセイ > DefaultAssay）。
pick_measurement_assay <- function(obj, assay_arg = NULL) {
  if (!is.null(assay_arg) && nzchar(assay_arg)) return(assay_arg)
  avail <- tryCatch(Seurat::Assays(obj), error = function(e) character(0))
  if ("Spatial" %in% avail) return("Spatial")
  raw <- setdiff(avail, c("integrated", "SCT"))
  if (length(raw) >= 1) return(raw[1])
  tryCatch(Seurat::DefaultAssay(obj), error = function(e) "Spatial")  # 最後の手段
}

# ---- 圧縮保存 ---------------------------------------------------------------
#  path の拡張子は .rds のまま使う (qs バイナリでも名称は .rds)。
#  qs が使える環境では qs::qsave、使えない/失敗時は xz 圧縮 saveRDS に
#  自動フォールバック。
save_rds_compact <- function(obj, path,
                             diet = TRUE,
                             keep_scale = FALSE,
                             keep_graphs = FALSE,
                             keep_counts = TRUE) {
  if (isTRUE(diet)) {
    # [ver45.8] DietSeurat も候補のひとつなので、前後を残して切り分け可能にする。
    cat(sprintf("[rds_io] DietSeurat 開始: %s\n", basename(path))); flush(stdout())
    obj <- diet_seurat_safe(obj,
                            keep_scale = keep_scale,
                            keep_graphs = keep_graphs,
                            keep_counts = keep_counts)
    cat(sprintf("[rds_io] DietSeurat 完了: %s\n", basename(path))); flush(stdout())
  }
  # 書き込みはまず一時ファイルに行い、成功後に rename するアトミック更新
  tmp_path <- paste0(path, ".tmp")
  if (.rds_io_has_qs()) {
    tryCatch({
      # [ver45.8] スレッド数を環境変数 QS_NTHREADS で上書き可能にする（未設定なら従来どおり）。
      #   qs のマルチスレッド圧縮はネイティブコードで動くため、ここでのクラッシュは R の
      #   エラーメッセージを残さずプロセスごと落ちる（＝ログが途切れる）形になる。
      #   QS_NTHREADS=1 で単スレッドにすると、その切り分けができる。
      #   また detectCores はコンテナの CPU 制限ではなくホストのコア数を返すことがあり、
      #   割り当て以上のスレッドを立ててしまう点でも上書き手段があった方がよい。
      .qn <- suppressWarnings(as.integer(Sys.getenv("QS_NTHREADS", unset = "")))
      nthreads <- if (!is.na(.qn) && .qn >= 1L) .qn else
        max(1L, parallel::detectCores(logical = FALSE) - 1L)
      # 保存の開始/完了を必ず残す。開始だけ出て完了が出なければ保存中に落ちたと分かる。
      cat(sprintf("[rds_io] 保存開始: %s (qs, nthreads=%d)\n", basename(path), nthreads))
      flush(stdout())
      qs::qsave(obj, tmp_path, preset = "balanced", nthreads = nthreads)
      file.rename(tmp_path, path)
      cat(sprintf("[rds_io] 保存完了: %s (%.2f GB)\n", basename(path),
                  file.size(path) / 1024^3))
      flush(stdout())
      return(invisible(path))
    }, error = function(e) {
      message("[rds_io] qs::qsave failed, falling back to saveRDS: ",
              conditionMessage(e))
      if (file.exists(tmp_path)) try(file.remove(tmp_path), silent = TRUE)
    })
  }
  # フォールバック: saveRDS + xz
  saveRDS(obj, tmp_path, compress = "xz")
  file.rename(tmp_path, path)
  invisible(path)
}

# ---- 汎用読み込み (旧 saveRDS 形式 / 新 qs 形式を自動判定) -----------------
#  ensure_scale = TRUE を渡すと、Seurat オブジェクトに対して
#  読み込み直後に ScaleData() を 1 回走らせ、scale.data を復元する。
load_rds_compact <- function(path, ensure_scale = FALSE) {
  if (!file.exists(path)) {
    stop("[rds_io] file not found: ", path)
  }
  is_qs <- .rds_io_is_qs_file(path)
  if (is_qs) {
    if (!.rds_io_has_qs()) {
      stop("[rds_io] ", path,
           " は qs 形式で保存されていますが qs パッケージが",
           " インストールされていません。",
           " install.packages('qs') を実行してください。")
    }
    obj <- qs::qread(path)
  } else {
    # 旧 saveRDS 形式 (gzip / xz / bzip2 / 無圧縮) は readRDS がそのまま読む
    obj <- readRDS(path)
  }
  if (isTRUE(ensure_scale)) {
    obj <- .rds_io_ensure_scale(obj)
  }
  obj
}

# ---- scale.data を on-demand で復元 ----------------------------------------
.rds_io_ensure_scale <- function(obj) {
  if (is.list(obj) && !inherits(obj, "Seurat") && !is.data.frame(obj)) {
    return(lapply(obj, .rds_io_ensure_scale))
  }
  if (!inherits(obj, "Seurat")) return(obj)
  if (!requireNamespace("Seurat", quietly = TRUE)) return(obj)
  tryCatch({
    # HVG 取得 (無ければ全 feature)
    hvf <- tryCatch(Seurat::VariableFeatures(obj), error = function(e) NULL)
    if (is.null(hvf) || length(hvf) == 0) hvf <- rownames(obj)
    Seurat::ScaleData(obj, features = hvf, verbose = FALSE)
  }, error = function(e) {
    message("[rds_io] ensure_scale failed, returning as-is: ",
            conditionMessage(e))
    obj
  })
}

# ---- 解析レシート用 R サイドカー -------------------------------------------
#  Python 側 receipt.py が読み込む analysis_receipt_r.json を結果フォルダへ書く。
#  R 版・乱数 seed・クラスタ/正規化/補正設定・主要パッケージ版を残す。
#  防御的に実装（変数は get0 で探索、出力先も既知の候補名から自動探索）し、
#  失敗しても解析本体を壊さない。呼び出しは try(write_receipt_sidecar()) 推奨。
write_receipt_sidecar <- function(output_dir = NULL) {
  g <- function(name, default = NULL) {
    v <- tryCatch(get0(name, envir = .GlobalEnv, inherits = TRUE),
                  error = function(e) NULL)
    if (is.null(v)) default else v
  }
  if (is.null(output_dir) || !nzchar(as.character(output_dir)[1])) {
    for (nm in c("OUTPUT_DIR", "V13_OUTPUT_DIR", "V8_OUTPUT_DIR",
                 "EXPORT_DATA_DIR", "EXPORT_TXT_DIR", "od")) {
      cand <- g(nm)
      if (!is.null(cand) && nzchar(as.character(cand)[1])) { output_dir <- cand; break }
    }
  }
  if (is.null(output_dir) || !nzchar(as.character(output_dir)[1])) return(invisible(NULL))
  if (!requireNamespace("jsonlite", quietly = TRUE)) return(invisible(NULL))

  pkgs <- c("Seurat", "Matrix", "harmony", "dbscan", "leiden", "leidenbase",
            "uwot", "data.table", "arrow", "qs", "presto", "aricode")
  pv <- list()
  for (p in pkgs) {
    v <- tryCatch(as.character(utils::packageVersion(p)),
                  error = function(e) NA_character_)
    if (!is.na(v)) pv[[p]] <- v
  }
  alg <- g("CLUSTER_ALGORITHM")
  alg_name <- if (is.null(alg)) {
    if (!is.null(g("DBSCAN_EPS")) || !is.null(g("DBSCAN_MINPTS"))) "dbscan" else NULL
  } else {
    switch(as.character(alg)[1], "1" = "louvain", "2" = "louvain_multilevel",
           "3" = "slm", "4" = "leiden", paste0("algorithm_", alg))
  }
  threads <- suppressWarnings(as.integer(Sys.getenv("OMP_NUM_THREADS", "")))
  if (length(threads) == 0 || is.na(threads)) threads <- NULL

  info <- list(
    r_version             = paste(R.version$major, R.version$minor, sep = "."),
    seed                  = g("GLOBAL_RANDOM_SEED", g("UMAP_SEED", g("RANDOM_SEED"))),
    clustering_algorithm  = alg_name,
    clustering_resolution = g("CLUSTER_RESOLUTION"),
    clustering_k          = g("CLUSTER_K_PARAM"),
    norm_mode             = g("NORM_MODE"),
    input_normalized      = g("INPUT_NORMALIZED"),
    batch_correction      = g("ANALYSIS_METHOD", g("BATCH_VAR")),
    threads               = threads,
    package_versions      = pv,
    written_at            = format(Sys.time(), "%Y-%m-%dT%H:%M:%S")
  )
  fp <- file.path(output_dir, "analysis_receipt_r.json")
  tryCatch(
    jsonlite::write_json(info, fp, auto_unbox = TRUE, null = "null", pretty = TRUE),
    error = function(e) message("[rds_io] receipt sidecar write failed: ",
                                conditionMessage(e))
  )
  invisible(fp)
}
