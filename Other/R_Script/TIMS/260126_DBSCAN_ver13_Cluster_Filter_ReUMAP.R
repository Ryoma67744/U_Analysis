# -*- coding: UTF-8 -*-
# ============================================================
# ClusterFilter_ReUMAP for 260125_DBSCAN_With_cluster_ver13.R
#   (Seurat RDS -> cluster exclude/keep -> filtered inputs export -> (optional) re-run ver13)
#
# 目的:
#   1) ver13 の出力RDS（Step2/Step3）から seurat_clusters を使ってスポットを除外/抽出
#   2) 元入力（Parquet/CSV/TSV）を「同じ形式」でフィルタして新規入力として保存
#   3) （任意）ver13 スクリプトを“コピーして設定だけ差し替え”て Re-UMAP を回す
#
# 想定:
#   - ver13 Step1 で seu$sample / seu$x_coord / seu$y_coord / seu$spot_index が付与される
#   - Parquet は id,x,y,mz_*（＋ annotation 推奨）を持ち、id が spot_index として扱われる
#   - ver13 は annotation を slice_id にし、condition は slice_id と同一（あなたの最新仕様）
# ============================================================

message("=== RUNNING: ClusterFilter_ReUMAP for DBSCAN ver13 ===")

# ------------------------------------------------------------
# [USER SETTINGS] ここだけ編集
# ------------------------------------------------------------

# (A) ver13スクリプト（260125_DBSCAN_With_cluster_ver13.R）のパス
V13_SCRIPT_PATH <- "C:\\Users\\Cciia\\Downloads\\260125_DBSCAN_With_cluster_ver13.R"

# (B) ver13の出力RDSのパス（クラスタが入っているもの）
#   - Step2: OUTPUT_DIR/<PROJECT>_<YYYYMMDD>/RDS_Files/Step2_HarmonyPCA_Result.rds
#   - Step3: OUTPUT_DIR/<PROJECT>_<YYYYMMDD>/RDS_Files/Step3_RPCA_Result.rds
RDS_PATH <- "C:\\Users\\Cciia\\Biochem Dropbox\\Biochem's shared workspace\\Workspace\\UMAP\\TIMS\\Data\\test-250809_Kizu_H2-18O_Brain_Transform\\Annotation-test1__20260125\\RDS_Files\\Step2_HarmonyPCA_Result.rds"

# (C) 元の入力ファイル（ver13の INPUT_PATHS と同じもの）
#     Parquet(.parquet/.pq) / CSV(.csv) / TSV(.tsv/.txt) を混在させてもOK
ORIGINAL_INPUT_PATHS <- c(
  "C:\\Users\\Cciia\\Biochem Dropbox\\Biochem's shared workspace\\Workspace\\UMAP\\TIMS\\Data\\test-250809_Kizu_H2-18O_Brain_Transform\\test-250809_Kizu_H2-18O_Brain_Transform.parquet"
)

# (D) クラスタ抽出モード
#     "exclude" : TARGET_CLUSTERS を除外して残りで再解析
#     "keep"    : TARGET_CLUSTERS のみで再解析
FILTER_MODE <- "exclude"

# (E) 対象クラスタ（seurat_clusters の番号）
TARGET_CLUSTERS <- c(9)

# (F) フィルタ後入力の出力フォルダ
EXPORT_DATA_DIR <- "C:\\Users\\Cciia\\Biochem Dropbox\\Biochem's shared workspace\\Workspace\\UMAP\\TIMS\\Data\\test-250809_Kizu_H2-18O_Brain_Transform"
dir.create(EXPORT_DATA_DIR, recursive = TRUE, showWarnings = FALSE)

# (G) 新規入力を書き出した後に ver13 を自動実行するか（重いのでデフォルトFALSE推奨）
RUN_V13_AFTER_EXPORT <- TRUE

# (H) 自動実行する場合の ver13 側の出力先（OUTPUT_DIR）
V13_OUTPUT_DIR <- EXPORT_DATA_DIR

# (I) 自動実行する場合の ver13 側 PROJECT_LABEL（フォルダ名の先頭）
#     例: "MyProject_" のように末尾 "_" を付ける運用を推奨
V13_PROJECT_LABEL_PREFIX <- "ClusterFiltered_"

# (J) Re-UMAPでは新規入力から解析し直すので、Resumeは基本OFF推奨
V13_RESUME_FROM_RDS <- FALSE
V13_RESUME_DIR_PATH <- ""

# (K) ver13 側の ION_MODE を差し替える（"Positive" / "Negative"）
V13_ION_MODE <- "Positive"

# (L) ver13 側のアノテーションDB（TraceFinder export CSV）を差し替える
V13_ANNOTATION_CSV_PATH <- "C:\\Users\\Cciia\\Biochem Dropbox\\Biochem's shared workspace\\Workspace\\UMAP\\TIMS\\DB\\4500_endogenous_metabolites_mod.csv"

# (M) ver13 側の m/z 許容誤差を差し替える（数値, m/z 直照合）
V13_TOLERANCE_MZ <- 0.05

# (N) アノテーション有効/無効（TRUE/FALSE）を差し替える（必要なら）
V13_ANNOTATION_ENABLE <- TRUE

# (O) slice_id / condition を 1回目RDSから保存しておきたい場合（通常は不要）
#     ver13 は入力Parquetの annotation から slice_id/condition を再現できるため、
#     基本は FALSE 推奨です（Re-UMAP側でDBSCAN復元などはしない）。
SAVE_SLICE_MAP_FROM_FIRST_RUN <- FALSE


# ------------------------------------------------------------
# [UTILS] 以降は編集不要
# ------------------------------------------------------------
.stopif <- function(cond, msg) { if (!isTRUE(cond)) stop(msg, call. = FALSE) }

# どんな型でも数値IDへ寄せる（factor罠対策）
as_num_id <- function(x) {
  if (is.factor(x)) x <- as.character(x)
  if (is.character(x)) return(suppressWarnings(as.numeric(x)))
  suppressWarnings(as.numeric(x))
}

# rownames（例: "sample_Spot-123" / "sample_Spot_123"）から 123 を抜く
id_from_cellname <- function(cn) {
  if (is.factor(cn)) cn <- as.character(cn)
  # Spot-123 / Spot_123
  m <- regexpr("Spot[-_]\\d+", cn)
  out <- rep(NA_real_, length(cn))
  hit <- m != -1
  if (any(hit)) {
    s <- regmatches(cn, m)
    out[hit] <- suppressWarnings(as.numeric(gsub("\\D+", "", s)))
  } else {
    # 保険: 末尾の数字
    out <- suppressWarnings(as.numeric(gsub(".*?(\\d+)$", "\\1", cn)))
  }
  out
}

# ---------- Seuratメタ情報から「残すセル名」を決める ----------
get_cells_to_keep <- function(seu, filter_mode, target_clusters,
                              sample_col = "sample", cluster_col = "seurat_clusters") {
  md <- seu@meta.data
  .stopif(sample_col %in% colnames(md), paste0("meta.data に '", sample_col, "' がありません。"))
  .stopif(cluster_col %in% colnames(md), paste0("meta.data に '", cluster_col, "' がありません。"))

  cl <- as.character(md[[cluster_col]])
  target <- as.character(target_clusters)

  if (filter_mode == "exclude") {
    keep <- !(cl %in% target)
  } else if (filter_mode == "keep") {
    keep <- (cl %in% target)
  } else {
    stop("FILTER_MODE は 'exclude' か 'keep' を指定してください。", call. = FALSE)
  }
  rownames(md)[keep]
}

# ---------- 元入力ファイルからフィルタして書き出す ----------
export_filtered_input <- function(in_path, out_path, id_keep, debug_tsv_path = NULL) {
  .stopif(file.exists(in_path), paste0("入力が見つかりません: ", in_path))
  id_keep <- unique(as_num_id(id_keep))
  id_keep <- id_keep[is.finite(id_keep)]
  .stopif(length(id_keep) > 0, "id_keep が数値として解釈できません。")

  ext <- tolower(tools::file_ext(in_path))

  # ---- Case A) Parquet ----
  if (ext %in% c("parquet", "pq")) {
    .stopif(requireNamespace("arrow", quietly = TRUE), "Parquet入出力に arrow が必要です。install.packages('arrow')")

    df <- arrow::read_parquet(in_path, as_data_frame = TRUE)
    .stopif("id" %in% colnames(df), paste0("Parquetに id 列がありません: ", in_path))

    id_num <- suppressWarnings(as.numeric(df$id))
    keep_flag <- id_num %in% id_keep
    df2 <- df[keep_flag, , drop = FALSE]

    if (nrow(df2) == 0) {
      if (!is.null(debug_tsv_path)) {
        dbg <- data.frame(
          input = basename(in_path),
          n_rows = nrow(df),
          n_keep = length(id_keep),
          keep_min = min(id_keep, na.rm = TRUE),
          keep_max = max(id_keep, na.rm = TRUE),
          id_min = suppressWarnings(min(id_num, na.rm = TRUE)),
          id_max = suppressWarnings(max(id_num, na.rm = TRUE)),
          overlap_n = sum(keep_flag, na.rm = TRUE),
          keep_head = paste(head(id_keep, 30), collapse = ","),
          id_head = paste(head(id_num, 30), collapse = ","),
          stringsAsFactors = FALSE
        )
        write.table(dbg, file = debug_tsv_path, sep = "\t", row.names = FALSE, quote = FALSE)
      }
      stop(paste0("フィルタ後のParquetが0行です: ", basename(in_path),
                  "\n  → Pixel/ID不一致の可能性（spot_index と parquet$id を確認）",
                  ifelse(is.null(debug_tsv_path), "", paste0("\n  debug: ", debug_tsv_path))),
           call. = FALSE)
    }

    arrow::write_parquet(df2, out_path)
    return(invisible(list(n_kept = nrow(df2), n_total = nrow(df))))
  }

  # ---- Case B) CSV/TSV/TXT (legacy SCiLS Transform) ----
  if (ext %in% c("csv", "tsv", "txt")) {
    .stopif(requireNamespace("data.table", quietly = TRUE), "CSV/TSV入出力に data.table が必要です。install.packages('data.table')")

    hdr <- readLines(in_path, n = 4, warn = FALSE)
    first_line <- readLines(in_path, n = 1, warn = FALSE)
    d <- if (length(first_line) && grepl(",", first_line, fixed = TRUE)) "," else "\t"

    dat <- data.table::fread(in_path, skip = 4, header = FALSE, sep = d,
                             colClasses = "numeric", fill = TRUE, showProgress = FALSE)
    dat <- as.data.frame(dat, stringsAsFactors = FALSE)

    spot <- suppressWarnings(as.numeric(dat[, 1]))
    keep_flag <- spot %in% id_keep
    dat2 <- dat[keep_flag, , drop = FALSE]

    if (nrow(dat2) == 0) {
      if (!is.null(debug_tsv_path)) {
        dbg <- data.frame(
          input = basename(in_path),
          n_rows = nrow(dat),
          n_keep = length(id_keep),
          keep_min = min(id_keep, na.rm = TRUE),
          keep_max = max(id_keep, na.rm = TRUE),
          spot_min = suppressWarnings(min(spot, na.rm = TRUE)),
          spot_max = suppressWarnings(max(spot, na.rm = TRUE)),
          overlap_n = sum(keep_flag, na.rm = TRUE),
          keep_head = paste(head(id_keep, 30), collapse = ","),
          spot_head = paste(head(spot, 30), collapse = ","),
          stringsAsFactors = FALSE
        )
        write.table(dbg, file = debug_tsv_path, sep = "\t", row.names = FALSE, quote = FALSE)
      }
      stop(paste0("フィルタ後のCSV/TSVが0行です: ", basename(in_path),
                  "\n  → Pixel/ID不一致の可能性（spot_index と 1列目のID を確認）",
                  ifelse(is.null(debug_tsv_path), "", paste0("\n  debug: ", debug_tsv_path))),
           call. = FALSE)
    }

    # 先頭4行を維持（ver13も skip=4 前提の reader を持つ）
    writeLines(hdr, con = out_path, useBytes = TRUE)
    write.table(dat2, file = out_path, sep = d, row.names = FALSE, col.names = FALSE, quote = FALSE, append = TRUE)
    return(invisible(list(n_kept = nrow(dat2), n_total = nrow(dat))))
  }

  stop("Unsupported input ext: ", ext, call. = FALSE)
}

# ---------- ver13のコピーを作って I/O 設定を差し替え ----------
patch_v13_step2_pipeline <- function(code_vec) {
  # ver13 の run_pipeline / Retry Logic は ver12 と同形なので、ver12用の堅牢化パッチをそのまま適用
  count_fixed <- function(x, pat_fixed) {
    m <- gregexpr(pat_fixed, x, fixed = TRUE)[[1]]
    if (length(m) == 1 && m[1] == -1) return(0L)
    length(m)
  }

  # --- 1) run_pipeline 関数ブロックを置換 ---
  s <- grep("^\\s*run_pipeline\\s*<-\\s*function\\s*\\(", code_vec)
  if (length(s) >= 1) {
    s <- s[1]
    depth <- 0L
    e <- NA_integer_
    for (i in s:length(code_vec)) {
      ln <- code_vec[i]
      depth <- depth + count_fixed(ln, "{") - count_fixed(ln, "}")
      if (i > s && depth <= 0L) { e <- i; break }
    }
    if (!is.na(e)) {
      new_run <- c(
        '  run_pipeline <- function(use_harmony, cfg) {',
        '    # 1) Normalize -> HVF -> Scale(VarFeatures only) -> PCA(VarFeatures only)',
        '    s <- NormalizeData(seu_merged)',
        '    s <- FindVariableFeatures(s, nfeatures = cfg$n_var_features)',
        '    hvf <- VariableFeatures(s)',
        '    hvf <- unique(hvf[!is.na(hvf)])',
        '    # PCA/SVD が成立する条件を満たすように npcs を丸める（irlbaエラー回避）',
        '    n_cells <- ncol(s)',
        '    n_feat  <- length(hvf)',
        '    npcs_use <- min(cfg$max_pcs, n_feat - 1, n_cells - 1)',
        '    if (!is.finite(npcs_use) || npcs_use < 2) {',
        '      stop(paste0("Too few variable features/cells for PCA: hvf=", n_feat, " cells=", n_cells))',
        '    }',
        '    # 既存 scale.data のfeature不整合警告を抑えるため、scale.data をクリア',
        '    try({ DefaultAssay(s) <- DefaultAssay(seu_merged); s[[DefaultAssay(s)]]@scale.data <- matrix(nrow=0, ncol=0) }, silent = TRUE)',
        '    s <- ScaleData(s, features = hvf)',
        '    s <- RunPCA(s, npcs = npcs_use, features = hvf)',
        '',
        '    # 2) dims を「実際に存在する次元数」で丸める（UMAP/Neighborsの範囲外エラー回避）',
        '    dims_use <- 1:min(cfg$umap_dims, npcs_use)',
        '',
        '    if (use_harmony) {',
        '      s <- RunHarmony(s, group.by.vars = group_var)',
        '      nh <- ncol(Embeddings(s, "harmony"))',
        '      dims_h <- 1:min(cfg$umap_dims, nh)',
        '      s <- RunUMAP(s, reduction = "harmony", dims = dims_h) %>%',
        '        FindNeighbors(reduction = "harmony", dims = dims_h)',
        '    } else {',
        '      s <- RunUMAP(s, reduction = "pca", dims = dims_use) %>%',
        '        FindNeighbors(reduction = "pca", dims = dims_use)',
        '    }',
        '    FindClusters(s, resolution = CLUSTER_RESOLUTION)',
        '  }'
      )
      code_vec <- c(code_vec[1:(s-1)], new_run, code_vec[(e+1):length(code_vec)])
    }
  }

  # --- 2) Retry Logic ブロックを置換（エラー表示を追加） ---
  idx_retry_hdr <- grep("#\\s*Retry Logic", code_vec)
  if (length(idx_retry_hdr) >= 1) {
    hdr <- idx_retry_hdr[1]
    s2 <- grep("^\\s*for\\s*\\(cfg\\s+in\\s+HARMONY_RETRY_GRID\\)", code_vec[(hdr+1):length(code_vec)])
    if (length(s2) >= 1) {
      s2 <- s2[1] + hdr
      e2 <- grep('^\\s*if\\s*\\(is\\.null\\(seu_harmony\\)\\)\\s*stop\\("All pipelines failed\\."\\)', code_vec[s2:length(code_vec)])
      if (length(e2) >= 1) {
        e2 <- e2[1] + s2 - 1
        new_retry <- c(
          '  # Retry Logic (verbose)',
          '  for (cfg in HARMONY_RETRY_GRID) {',
          '    ok <- tryCatch({',
          '      seu_harmony <- run_pipeline(TRUE, cfg); TRUE',
          '    }, error = function(e) {',
          '      message("!! Harmony pipeline failed: ", e$message,',
          '              " | n_var_features=", cfg$n_var_features,',
          '              " max_pcs=", cfg$max_pcs,',
          '              " umap_dims=", cfg$umap_dims)',
          '      FALSE',
          '    })',
          '    if (ok) { REDUCTION_USED <- "harmony"; break }',
          '  }',
          '  if (is.null(seu_harmony)) {',
          '    for (cfg in PCA_RETRY_GRID) {',
          '      ok <- tryCatch({',
          '        seu_harmony <- run_pipeline(FALSE, cfg); TRUE',
          '      }, error = function(e) {',
          '        message("!! PCA pipeline failed: ", e$message,',
          '                " | n_var_features=", cfg$n_var_features,',
          '                " max_pcs=", cfg$max_pcs,',
          '                " umap_dims=", cfg$umap_dims)',
          '        FALSE',
          '      })',
          '      if (ok) { REDUCTION_USED <- "pca"; break }',
          '    }',
          '  }',
          '  if (is.null(seu_harmony)) stop("All pipelines failed.")'
        )
        code_vec <- c(code_vec[1:(s2-1)], new_retry, code_vec[(e2+1):length(code_vec)])
      }
    }
  }
  code_vec
}

make_v13_copy_with_settings <- function(v13_path, out_path,
                                        input_paths, output_dir, project_label,
                                        resume_from_rds = FALSE, resume_dir_path = "") {
  .stopif(file.exists(v13_path), paste0("ver13スクリプトが見つかりません: ", v13_path))
  code <- readLines(v13_path, warn = FALSE)

  r_str <- function(x) paste0("\"", gsub("\\\\", "\\\\\\\\", x), "\"")

  replace_assign_line <- function(code_vec, var, new_rhs, multiple = FALSE) {
    pat <- paste0("^\\s*", var, "\\s*<-\\s*.*$")
    idx <- grep(pat, code_vec)
    if (length(idx) < 1) {
      # ION_MODE はコメント/空白違いで見つからない場合があるのでフォールバック
      if (identical(var, "ION_MODE")) {
        idx2 <- grep("^\\s*ION_MODE\\s*<-", code_vec)
        if (length(idx2) >= 1) {
          code_vec[idx2[1]] <- paste0("ION_MODE <- ", new_rhs)
          return(code_vec)
        }
      }
      stop(paste0("ver13内で ", var, " の代入行が見つかりません（パターン不一致）"), call. = FALSE)
    }
    if (isTRUE(multiple)) {
      code_vec[idx] <- paste0(var, " <- ", new_rhs)
    } else {
      code_vec[idx[1]] <- paste0(var, " <- ", new_rhs)
    }
    code_vec
  }

  # OUTPUT_DIR / PROJECT_LABEL / RESUME
  code <- replace_assign_line(code, "OUTPUT_DIR",   r_str(output_dir))
  code <- replace_assign_line(code, "PROJECT_LABEL", r_str(project_label))

  # ION_MODE ("Positive" or "Negative")
  if (exists("V13_ION_MODE") && nzchar(V13_ION_MODE)) {
    code <- replace_assign_line(code, "ION_MODE", r_str(V13_ION_MODE))
  }
  # ANNOTATION_CSV_PATH (TraceFinder export CSV)
  if (exists("V13_ANNOTATION_CSV_PATH") && nzchar(V13_ANNOTATION_CSV_PATH)) {
    code <- replace_assign_line(code, "ANNOTATION_CSV_PATH", r_str(V13_ANNOTATION_CSV_PATH), multiple = TRUE)
  }
  # m/z tolerance (set both DEFAULT_TOLERANCE_MZ and TOLERANCE_MZ)
  if (exists("V13_TOLERANCE_MZ") && is.finite(V13_TOLERANCE_MZ)) {
    code <- replace_assign_line(code, "DEFAULT_TOLERANCE_MZ", as.character(V13_TOLERANCE_MZ), multiple = TRUE)
    code <- replace_assign_line(code, "TOLERANCE_MZ", as.character(V13_TOLERANCE_MZ), multiple = TRUE)
  }
  # Annotation enable switch
  if (exists("V13_ANNOTATION_ENABLE") && !is.na(V13_ANNOTATION_ENABLE)) {
    code <- replace_assign_line(code, "ANNOTATION_ENABLE", if (isTRUE(V13_ANNOTATION_ENABLE)) "TRUE" else "FALSE", multiple = TRUE)
  }

  code <- replace_assign_line(code, "RESUME_FROM_RDS", if (isTRUE(resume_from_rds)) "TRUE" else "FALSE")
  code <- replace_assign_line(code, "RESUME_DIR_PATH", r_str(resume_dir_path))

  # INPUT_PATHS ブロック差し替え
  start_pat <- "^\\s*INPUT_PATHS\\s*<-\\s*c\\s*\\("
  start_idx <- grep(start_pat, code)
  .stopif(length(start_idx) >= 1, "ver13内で INPUT_PATHS <- c( の開始行が見つかりません。")
  s <- start_idx[1]
  end_rel <- which(grepl("^\\s*\\)\\s*$", code[(s+1):length(code)]))
  .stopif(length(end_rel) >= 1, "ver13内で INPUT_PATHS の閉じ括弧行が見つかりません。")
  e <- s + end_rel[1]

  ip_lines <- c(
    "INPUT_PATHS <- c(",
    paste0("  ", vapply(input_paths, r_str, character(1)), collapse = ",\n"),
    ")"
  )
  code <- c(code[1:(s-1)], ip_lines, code[(e+1):length(code)])

  # ---- Step2 の run_pipeline / Retry Logic を堅牢化パッチ ----
  code <- patch_v13_step2_pipeline(code)

  writeLines(code, con = out_path, useBytes = TRUE)
  invisible(out_path)
}

# ------------------------------------------------------------
# [MAIN]
# ------------------------------------------------------------
message(">> Loading Seurat RDS: ", RDS_PATH)
.stopif(file.exists(RDS_PATH), paste0("RDSが見つかりません: ", RDS_PATH))
rds_obj <- readRDS(RDS_PATH)

# Step2/Step3 は list(obj=..., reduction=...) の形式になっている場合がある
seu <- rds_obj
if (is.list(rds_obj) && !is.null(rds_obj$obj) && inherits(rds_obj$obj, "Seurat")) {
  seu <- rds_obj$obj
}

.stopif(inherits(seu, "Seurat"), "RDSからSeuratオブジェクトを取得できません（Step2/Step3のRDSを指定してください）。")

# seurat_clusters が無い場合、Identsから作る（念のため）
if (!("seurat_clusters" %in% colnames(seu@meta.data))) {
  message(">> meta.data に seurat_clusters が無いので Idents(seu) を seurat_clusters として追加します。")
  seu@meta.data$seurat_clusters <- as.character(Idents(seu))
}

cells_keep <- get_cells_to_keep(seu, FILTER_MODE, TARGET_CLUSTERS, sample_col = "sample", cluster_col = "seurat_clusters")
md_keep <- seu@meta.data[cells_keep, , drop = FALSE]

# ID候補: spot_index 優先。無ければ cellname から
id1 <- rep(NA_real_, nrow(md_keep))
if ("spot_index" %in% colnames(md_keep)) {
  id1 <- as_num_id(md_keep$spot_index)
} else {
  message(">> meta.data に spot_index がありません。rownamesからIDを抽出します。")
}
id2 <- id_from_cellname(rownames(md_keep))

use_id <- id1
na_rate <- mean(!is.finite(use_id))
if (is.na(na_rate) || na_rate > 0.5) {
  message(">> spot_index 由来IDが不安定なので rownames 由来を使用します。")
  use_id <- id2
}
md_keep$ID_for_export <- use_id
.stopif(any(is.finite(md_keep$ID_for_export)), "ID_for_export が作れません（spot_index/rownamesの形式を確認）。")

# ---- (OPTION) slice_id / condition を 1回目RDSから抽出して保存（通常は不要） ----
SLICE_MAP_CSV_PATH <- file.path(EXPORT_DATA_DIR, "slice_map_from_first_run.csv")
if (isTRUE(SAVE_SLICE_MAP_FROM_FIRST_RUN)) {
  md_all <- seu@meta.data
  need_cols <- c("spot_index", "slice_id")
  if (all(need_cols %in% colnames(md_all))) {
    sm <- md_all[, intersect(c("sample","spot_index","slice_id","condition"), colnames(md_all)), drop = FALSE]
    if ("spot_index" %in% colnames(sm)) {
      if (is.factor(sm$spot_index)) sm$spot_index <- as.character(sm$spot_index)
      sm$spot_index <- suppressWarnings(as.numeric(sm$spot_index))
    }
    sm <- sm[is.finite(sm$spot_index) & !is.na(sm$slice_id), , drop = FALSE]
    write.csv(sm, SLICE_MAP_CSV_PATH, row.names = FALSE)
    message(">> Saved slice map (from first-run RDS): ", SLICE_MAP_CSV_PATH, "  [n=", nrow(sm), "]")
  } else {
    message(">> NOTE: meta.data に spot_index/slice_id が無いため、slice map を保存できませんでした。")
  }
}

# 入力ファイルごとにフィルタして同形式で保存
exported <- character(0)

for (fp in ORIGINAL_INPUT_PATHS) {
  .stopif(file.exists(fp), paste0("元入力が見つかりません: ", fp))
  sn <- tools::file_path_sans_ext(basename(fp))

  rows_sn <- md_keep[as.character(md_keep$sample) == as.character(sn), , drop = FALSE]
  if (nrow(rows_sn) == 0) {
    message(". skip (no remaining spots for sample): ", sn)
    next
  }

  keep_ids <- unique(rows_sn$ID_for_export)
  keep_ids <- keep_ids[is.finite(keep_ids)]
  if (length(keep_ids) == 0) {
    message(". skip (no valid ID_for_export): ", sn)
    next
  }

  suffix <- if (FILTER_MODE == "exclude") {
    paste0("_EXCL_Cl_", paste(TARGET_CLUSTERS, collapse = "-"))
  } else {
    paste0("_KEEP_Cl_", paste(TARGET_CLUSTERS, collapse = "-"))
  }

  out_ext <- tolower(tools::file_ext(fp))
  out_name <- paste0(sn, suffix, ".", out_ext)
  out_fp <- file.path(EXPORT_DATA_DIR, out_name)

  dbg_fp <- file.path(EXPORT_DATA_DIR, paste0(sn, suffix, "_debug.tsv"))

  res <- export_filtered_input(fp, out_fp, keep_ids, debug_tsv_path = dbg_fp)
  message(sprintf(">> Exported: %s   (%d / %d rows)", basename(out_fp), res$n_kept, res$n_total))
  exported <- c(exported, out_fp)
}

.stopif(length(exported) > 0, "出力が0件です（sample名の不一致やクラスタ指定を確認）。")

message("=== Export finished ===")
message("Exported files:")
for (x in exported) message("  - ", x)

# ------------------------------------------------------------
# (OPTION) ver13 をコピーして設定だけ差し替えて自動実行
# ------------------------------------------------------------
if (isTRUE(RUN_V13_AFTER_EXPORT)) {
  suffix_run <- if (FILTER_MODE == "exclude") {
    paste0("exclude_", paste(TARGET_CLUSTERS, collapse = "-"))
  } else {
    paste0("keep_", paste(TARGET_CLUSTERS, collapse = "-"))
  }

  project_label <- paste0(V13_PROJECT_LABEL_PREFIX, suffix_run, "_")

  v13_copy_path <- file.path(EXPORT_DATA_DIR, paste0("ClusterFilter_ReUMAP_run_ver13_copy_", suffix_run, ".R"))

  make_v13_copy_with_settings(
    v13_path = V13_SCRIPT_PATH,
    out_path = v13_copy_path,
    input_paths = exported,
    output_dir = V13_OUTPUT_DIR,
    project_label = project_label,
    resume_from_rds = V13_RESUME_FROM_RDS,
    resume_dir_path = V13_RESUME_DIR_PATH
  )

  message(">> Running patched ver13 copy: ", v13_copy_path)
  source(v13_copy_path)

  message("=== ver13 re-run finished ===")
}

message("=== DONE: ClusterFilter_ReUMAP for DBSCAN ver13 ===")
