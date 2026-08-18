# =============================================================================
# MSI Analysis Application - Compact RDS I/O helpers
#
# 役割:
#   Seurat / list(Seurat, ...) オブジェクトの保存と読み込みを、
#   情報損失なし で大幅軽量化する共通ヘルパー。
#   （DietSeurat により scale.data を落とし、qs2 (zstd) により高圧縮保存）
#
# 提供関数:
#   - save_rds_compact(obj, path, diet = TRUE, keep_scale = FALSE,
#                      keep_graphs = FALSE)
#   - load_rds_compact(path, ensure_scale = FALSE)
#   - diet_seurat_safe(obj, keep_scale = FALSE, keep_graphs = FALSE)
#
# 後方互換性:
#   load_rds_compact はファイル先頭のマジックバイトを判定し、
#   旧 saveRDS 形式 (gzip / xz / bzip2) と qs / qs2 形式のいずれも透過的に読む。
#   よって既存の .rds 資産は一切の変換なしでそのまま利用可能。
#
# 保存形式:
#   拡張子は .rds のまま維持する (Python / config 側の副作用を避けるため)。
#   qs2 バイナリを .rds という名前で保存し、読み込み側で自動判定する。
# =============================================================================

# ---- qs パッケージの可用性チェック (一度だけ) -----------------------------
# [ver50.1] qs が使えないことを必ずログに残す。
#   これまで requireNamespace が FALSE でも無言で圧縮フォールバックへ落ちていたため、
#   「R 4.6.1 へ上がって qs.so が undefined symbol: SET_CLOENV で読めなくなり、
#   全結果が最も展開の遅い xz で保存されていた」ことに数か月気づけなかった。
#   実測: 1.00GB の Step2 RDS を開くのに 118.7 秒（約 8.6 MB/s）。
.rds_io_has_qs <- function() {
  # メモ化 (同一セッション内で複数回問い合わせる場合)
  cached <- getOption("msi.rds_io.has_qs", default = NA)
  if (!is.na(cached)) return(isTRUE(cached))
  has_qs <- requireNamespace("qs", quietly = TRUE)
  options(msi.rds_io.has_qs = has_qs)
  if (!has_qs) {
    reason <- tryCatch({
      loadNamespace("qs"); ""
    }, error = function(e) paste0(" (", conditionMessage(e), ")"))
    # ★ ver57.1: 「gzip で保存します」という結論をここから外した。
    #   本関数は qs2 が使えないときの二番手判定としても、読み込み側の
    #   旧 qs ファイル対応としても呼ばれる。読み込み中に「保存します」と
    #   出るのは事実に反するので、結論は save_rds_compact 側で出す。
    cat(sprintf("[rds_io] 情報: 旧 qs パッケージは使えません%s\n", reason))
    flush(stdout())
  }
  has_qs
}

# ---- qs2 パッケージの可用性チェック (一度だけ) ----------------------------
# ★ ver57.1: 保存の第一候補を qs から qs2 へ移した。
#   qs 0.27.3 は R 4.6.1 で `undefined symbol: SET_CLOENV` により dlopen できない。
#   qs.so は 2025-03-12 ビルドで、R 4.6.1 (2026-06-24) が公開シンボルから外した
#   API を参照しているため。r2u の apt バイナリ r-cran-qs は 0.27.3 のまま
#   再ビルドされておらず（Candidate = Installed）、イメージを作り直しても直らない。
#   その結果 ver50.1 以降ずっと gzip の saveRDS に落ちていた。
#   実測（本番, 1.03GB の Step2）: 保存 162.8 秒 / 読込 29.1 秒。
#   後継の qs2 (0.2.2) は同じ zstd 系で R 4.6.1 でも読み込めることを実機で確認済み。
.rds_io_has_qs2 <- function() {
  cached <- getOption("msi.rds_io.has_qs2", default = NA)
  if (!is.na(cached)) return(isTRUE(cached))
  has_qs2 <- requireNamespace("qs2", quietly = TRUE)
  options(msi.rds_io.has_qs2 = has_qs2)
  if (!has_qs2) {
    reason <- tryCatch({
      loadNamespace("qs2"); ""
    }, error = function(e) paste0(" (", conditionMessage(e), ")"))
    cat(sprintf("[rds_io] 警告: qs2 パッケージが使えません%s\n", reason))
    flush(stdout())
  }
  has_qs2
}

# ---- コンテナに割り当てられた CPU 数 ----------------------------------------
# ★ ver57.4: cgroup の CPU クォータを読む。
#   `parallel::detectCores()` は **ホストの**コア数を返すため、コンテナでは
#   割り当てを超えるスレッドを立ててしまう。実際に本番（`cpus: '6'`）で
#   `nthreads=7` が採用され、cgroup にスロットリングされていた。
#   メモリ側は既に cgroup を読んでいる（analysis_runner._container_memory_limit_gb）
#   ので、CPU も同じ方針に揃える。
#   引数でパスを差し替えられるのは、テストが実ファイルなしで検証できるようにするため。
.rds_io_cpu_quota <- function(
    v2_path = "/sys/fs/cgroup/cpu.max",
    v1_quota = "/sys/fs/cgroup/cpu/cpu.cfs_quota_us",
    v1_period = "/sys/fs/cgroup/cpu/cpu.cfs_period_us") {
  .num <- function(x) suppressWarnings(as.numeric(x))
  # file.exists を先に見るのは、readLines が「ファイルが無い」を warning +
  # error の両方で出し、tryCatch では warning を止められないため。
  # cgroup の無い環境（開発機・素の Linux）で保存のたびに警告が出ると
  # 解析ログが汚れる。
  .first <- function(p) {
    if (!file.exists(p)) return(NA_character_)
    tryCatch(readLines(p, warn = FALSE)[1], error = function(e) NA_character_)
  }
  # cgroup v2: "<quota> <period>"。無制限なら quota が "max"。
  raw <- .first(v2_path)
  if (!is.na(raw) && nzchar(raw)) {
    parts <- strsplit(trimws(raw), "[[:space:]]+")[[1]]
    if (length(parts) >= 2 && !identical(parts[1], "max")) {
      q <- .num(parts[1]); p <- .num(parts[2])
      if (!is.na(q) && !is.na(p) && q > 0 && p > 0)
        return(max(1L, as.integer(floor(q / p))))
    }
    return(NA_integer_)   # "max" = 無制限
  }
  # cgroup v1: 無制限は quota が -1
  q <- .num(.first(v1_quota)); p <- .num(.first(v1_period))
  if (!is.na(q) && !is.na(p) && q > 0 && p > 0)
    return(max(1L, as.integer(floor(q / p))))
  NA_integer_
}

# ---- 圧縮スレッド数 ---------------------------------------------------------
# qs / qs2 で共通。QS_NTHREADS で上書きできる。
#   ネイティブコードのマルチスレッド圧縮は、落ちるときに R のエラーを残さず
#   プロセスごと終了する（＝ログが途切れる）。QS_NTHREADS=1 で切り分けられる。
# ★ ver57.4: cgroup のクォータでも頭打ちにする。UI と R の行列計算に 1 コア
#   残す意図で -1 しているのは従来どおりで、上限だけをホスト基準から
#   コンテナ基準に変えた（`cpus: '6'` なら 5 になる。OPENBLAS_NUM_THREADS と一致）。
.rds_io_nthreads <- function() {
  .qn <- suppressWarnings(as.integer(Sys.getenv("QS_NTHREADS", unset = "")))
  if (!is.na(.qn) && .qn >= 1L) return(.qn)
  n <- max(1L, parallel::detectCores(logical = FALSE) - 1L)
  quota <- .rds_io_cpu_quota()
  if (!is.na(quota)) n <- min(n, max(1L, quota - 1L))
  n
}

# ---- マジックバイトから「非 saveRDS 形式」を判定 ---------------------------
# 判定は消去法（既知の旧 saveRDS 形式でなければ qs 系）なので、qs2 形式の
# ファイルもそのまま TRUE になる。どちらであるかは .rds_io_read_qx が実際に
# 読んで確定させる（qs2 と旧 qs のマジックを個別に持たないのは、形式が増える
# たびにここを直す作りにすると、未知の形式を「旧 saveRDS」と誤判定して
# readRDS に渡し、意味の分からないエラーになるため）。
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

# ---- 旧形式の圧縮方式を名前で返す (ログ用) --------------------------------
# [ver50.1] 「実際にどの形式で保存されているか」をログに出すため。
#   xz が無言で使われ続けていたことに気づけなかった反省から追加。
.rds_io_legacy_format <- function(path) {
  fmt <- tryCatch({
    con <- file(path, "rb")
    on.exit(close(con), add = TRUE)
    b <- as.integer(readBin(con, what = "raw", n = 4))
    if (b[1] == 0x1F && b[2] == 0x8B) "gzip"
    else if (b[1] == 0x42 && b[2] == 0x5A) "bzip2"
    else if (b[1] == 0xFD && b[2] == 0x37 && b[3] == 0x7A && b[4] == 0x58) "xz"
    else if (b[1] %in% c(0x58, 0x42, 0x41) && b[2] == 0x0A) "無圧縮"
    else "不明"
  }, error = function(e) "不明")
  fmt
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

# ---- フォールバック時の圧縮方式 ---------------------------------------------
# [ver50.1] xz から gzip へ変更。
#   saveRDS が使えるのは gzip / bzip2 / xz / 無圧縮のみで zstd は無く、この 4 択なら
#   gzip が最良。xz は圧縮率は高いが**展開が常用形式で最も遅い**。
#   実測（1.00GB / 1,536 feature x 203,078 cell）: xz の展開に 118.7 秒 = 抽出全体の 51%。
#   環境変数 RDS_FALLBACK_COMPRESS で上書き可 ("gzip" / "bzip2" / "xz" / "none")。
.RDS_IO_FALLBACK_COMPRESS <- local({
  v <- tolower(trimws(Sys.getenv("RDS_FALLBACK_COMPRESS", unset = "gzip")))
  if (v %in% c("gzip", "bzip2", "xz", "none")) v else "gzip"
})

# saveRDS の compress 引数へ渡す値（"none" は論理値 FALSE）
.rds_io_compress_arg <- function() {
  if (identical(.RDS_IO_FALLBACK_COMPRESS, "none")) FALSE else .RDS_IO_FALLBACK_COMPRESS
}

# ---- 圧縮保存 ---------------------------------------------------------------
#  path の拡張子は .rds のまま使う (qs バイナリでも名称は .rds)。
#  qs が使える環境では qs::qsave、使えない/失敗時は gzip 圧縮 saveRDS に
#  自動フォールバック。
#  読み込み側 (load_rds_compact) はマジックバイト判定なので、
#  過去に xz で保存したファイルもそのまま読める。
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

  # ★ ver57.1: qs2 → 旧 qs → saveRDS の順に試す。
  #   第一候補を qs2 にした理由は .rds_io_has_qs2 のコメントを参照。
  #   旧 qs を残すのは、qs2 が入っていない環境（配布版の手動セットアップ等）で
  #   いきなり gzip まで落ちるのを避けるため。
  .writers <- list(
    list(name = "qs2", ok = .rds_io_has_qs2,
         fn = function(o, f, n) qs2::qs_save(o, f, nthreads = n)),
    list(name = "qs", ok = .rds_io_has_qs,
         fn = function(o, f, n) qs::qsave(o, f, preset = "balanced", nthreads = n))
  )
  for (.w in .writers) {
    if (!.w$ok()) next
    .done <- tryCatch({
      nthreads <- .rds_io_nthreads()
      # 保存の開始/完了を必ず残す。開始だけ出て完了が出なければ保存中に落ちたと分かる。
      cat(sprintf("[rds_io] 保存開始: %s (%s, nthreads=%d)\n",
                  basename(path), .w$name, nthreads))
      flush(stdout())
      .t0 <- Sys.time()
      .w$fn(obj, tmp_path, nthreads)
      file.rename(tmp_path, path)
      cat(sprintf("[rds_io] 保存完了: %s (%s, %.2f GB, %.1f 秒)\n",
                  basename(path), .w$name, file.size(path) / 1024^3,
                  as.numeric(difftime(Sys.time(), .t0, units = "secs"))))
      flush(stdout())
      TRUE
    }, error = function(e) {
      message("[rds_io] ", .w$name, " での保存に失敗、次の方式へ: ",
              conditionMessage(e))
      if (file.exists(tmp_path)) try(file.remove(tmp_path), silent = TRUE)
      FALSE
    })
    if (isTRUE(.done)) return(invisible(path))
  }

  # フォールバック: saveRDS (既定 gzip)。qs 分岐と同様に開始/完了を必ず残す。
  cat("[rds_io] 警告: qs2 も qs も使えないため saveRDS へフォールバックします。",
      "読み書きが数倍〜十数倍遅くなります (install.packages('qs2') を確認)。\n")
  cat(sprintf("[rds_io] 保存開始: %s (saveRDS, compress=%s)\n",
              basename(path), .RDS_IO_FALLBACK_COMPRESS))
  flush(stdout())
  .t0 <- Sys.time()
  saveRDS(obj, tmp_path, compress = .rds_io_compress_arg())
  file.rename(tmp_path, path)
  cat(sprintf("[rds_io] 保存完了: %s (saveRDS/%s, %.2f GB, %.1f 秒)\n",
              basename(path), .RDS_IO_FALLBACK_COMPRESS,
              file.size(path) / 1024^3,
              as.numeric(difftime(Sys.time(), .t0, units = "secs"))))
  flush(stdout())
  invisible(path)
}

# ---- qs2 / 旧 qs のどちらで書かれたかを、実際に読んで確定させる -------------
# ★ ver57.1: 戻り値 list(obj =, fmt = "qs2" | "qs")。
#   qs2 を先に試すのは、今後保存されるファイルが全部 qs2 になるため
#   （毎回 qs で失敗してから qs2 に回る無駄を避ける）。
#   どちらでも読めなかった場合は、両方の理由を添えて明示的に落とす。無言で
#   readRDS に渡すと「unknown input format」という原因の分からない例外になる。
.rds_io_read_qx <- function(path) {
  reasons <- character()
  readers <- list(
    list(name = "qs2", ok = .rds_io_has_qs2, fn = function(f) qs2::qs_read(f)),
    list(name = "qs",  ok = .rds_io_has_qs,  fn = function(f) qs::qread(f))
  )
  for (r in readers) {
    if (!r$ok()) {
      reasons <- c(reasons, paste0(r$name, ": 未インストール"))
      next
    }
    got <- tryCatch(list(obj = r$fn(path), fmt = r$name),
                    error = function(e) e)
    if (!inherits(got, "error")) return(got)
    reasons <- c(reasons, paste0(r$name, ": ", conditionMessage(got)))
  }
  stop("[rds_io] ", path,
       " は qs / qs2 形式ですが読み込めませんでした (",
       paste(reasons, collapse = " / "),
       ")。install.packages('qs2') を実行してください。")
}

# ---- 汎用読み込み (旧 saveRDS 形式 / qs / qs2 形式を自動判定) ---------------
#  ensure_scale = TRUE を渡すと、Seurat オブジェクトに対して
#  読み込み直後に ScaleData() を 1 回走らせ、scale.data を復元する。
load_rds_compact <- function(path, ensure_scale = FALSE) {
  if (!file.exists(path)) {
    stop("[rds_io] file not found: ", path)
  }
  is_qs <- .rds_io_is_qs_file(path)
  # [ver50.1] 読み込みの形式と所要時間を必ず残す。
  #   これが無かったため「抽出が遅い」の内訳を手作業で測るまで特定できなかった。
  .t0 <- Sys.time()
  if (is_qs) {
    # ★ ver57.1: qs2 と旧 qs のどちらで書かれたかはマジックバイトでは分けない。
    #   実際に読んでみて成功した方を採用する（.rds_io_read_qx）。
    .r <- .rds_io_read_qx(path)
    obj <- .r$obj
    .fmt <- .r$fmt
  } else {
    # 旧 saveRDS 形式 (gzip / xz / bzip2 / 無圧縮) は readRDS がそのまま読む
    .fmt <- .rds_io_legacy_format(path)
    obj <- readRDS(path)
  }
  .dt <- as.numeric(difftime(Sys.time(), .t0, units = "secs"))
  cat(sprintf("[rds_io] 読込完了: %s (%s, %.2f GB, %.1f 秒)\n",
              basename(path), .fmt, file.size(path) / 1024^3, .dt))
  if (identical(.fmt, "xz") && .dt > 30) {
    cat("[rds_io] 注意: xz は展開が遅い形式です。この RDS を保存し直すと",
        "読み込みが数倍速くなります（RDS 軽量化ツール）。\n")
  }
  flush(stdout())
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
    # [ver56.5] リトライで軽い設定へ落ちた場合の**実効値**(R3-RETRY)。
    #   構成値(UI で指定した値)ではなく、実際に計算に使われた値。1 段目で
    #   通ったときは構成値と同じになる。古い解析では変数が無く NULL になり、
    #   受領書側は従来どおり構成値へフォールバックする。
    n_var_features_effective = g("RETRY_N_VAR_FEATURES_EFFECTIVE"),
    max_pcs_effective     = g("RETRY_MAX_PCS_EFFECTIVE"),
    umap_dims_effective   = g("RETRY_UMAP_DIMS_EFFECTIVE"),
    retry_tier_used       = g("RETRY_TIER_USED"),
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
