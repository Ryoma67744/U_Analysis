# -*- coding: UTF-8 -*-
# ============================================================
# DESI RDS → クラスター除外/抽出 → Waters形式.txt再出力 → v8と同一UMAP再解析
#  VERSION: ver2 (2026-03-05) — マージスクリプト自動呼出機能を追加
# ============================================================
# 目的:
#  1) v8スクリプトで出力された .rds (Seurat) からクラスタIDを使って
#     - 特定クラスタを除外して再解析
#     - 特定クラスタのみ抽出して再解析
#  2) 抽出/除外したスポット集合を、元のWaters txtと同じ構造の .txt として新規出力
#  3) 生成した .txt に対して、添付の v8 スクリプトと「同じ処理」をそのまま実行（設定だけ差し替え）
#
# v5で修正/強化した点（今回のエラー対策）:
#  - spot_index が factor の場合、as.integer() がレベル番号になって PixelID と一致しない「factor罠」を回避
#  - spot_index が信用できない/欠損が多い場合は rownames（例: *_Spot_123）から PixelID を抽出してフォールバック
#  - それでも0行になる場合、原因切り分け用の debug.tsv を自動出力（IDレンジ/overlap 等）
# ============================================================

message("=== RUNNING: DESI_RDS_ClusterFilter_ExportTxt_and_ReUMAP  [v5] ===")

# ---- 共通 RDS I/O ヘルパーの読み込み (slim RDS / 旧 RDS の両対応) ----
local({
  helper_path <- NULL
  env_dir <- Sys.getenv("R_HELPERS_DIR", unset = NA)
  if (!is.na(env_dir) && nzchar(env_dir)) {
    cand <- file.path(env_dir, "rds_io.R")
    if (file.exists(cand)) helper_path <- cand
  }
  if (is.null(helper_path)) {
    args <- commandArgs(trailingOnly = FALSE)
    file_arg <- grep("^--file=", args, value = TRUE)
    if (length(file_arg) > 0) {
      this_dir <- dirname(normalizePath(sub("^--file=", "", file_arg[1]),
                                        mustWork = FALSE))
      cand <- file.path(this_dir, "..", "helpers", "rds_io.R")
      if (file.exists(cand)) helper_path <- cand
    }
  }
  if (is.null(helper_path)) {
    stop("rds_io.R が見つかりません。",
         " 環境変数 R_HELPERS_DIR を設定するか、",
         " App/Script/helpers/rds_io.R の配置を確認してください。")
  }
  source(helper_path, local = FALSE)
})

# ------------------------------------------------------------
# [USER SETTINGS] ここだけ編集
# ------------------------------------------------------------

# (A) v8スクリプト（添付の 251219_DESI-UMAP_Template_v8.R）のパス
V8_SCRIPT_PATH <- "C:\\Users\\Cciia\\Biochem Dropbox\\木津亮馬\\UMAP_Claudecode\\data\\DESI\\Script\\260308_DESI-UMAP_Template_v10.R"

# (B) v8の出力 .rds のパス（解析に使うクラスタが入っているRDSを選ぶ）
#     例:
#       - Multi-sample Harmony: DESI_SeuratCombined_harmony.rds
#       - Multi-sample RPCA   : DESI_SeuratCombined_RPCA.rds
#       - Single sample       : DESI_Seurat_SingleSample.rds
RDS_PATH <- "C:\\Users\\Cciia\\Biochem Dropbox\\Biochem's shared workspace\\Workspace\\UMAP\\DESI\\Data\\250622_Ohashi\\250621_Ohashi_GF-AAs\\250621_Ohashi_GF-CV_120260130\\RDS_Files\\DESI_SeuratCombined_harmony.rds"

# (C) 元の .txt が置いてあるフォルダ（v8の data_folder と同じ考え方）
ORIGINAL_DATA_FOLDER <- "C:\\Users\\Cciia\\Biochem Dropbox\\Biochem's shared workspace\\Workspace\\UMAP\\DESI\\Data\\250622_Ohashi\\250621_Ohashi_GF-AAs"

# (D) 元の sample_names（v8で指定した txt ファイル名（拡張子なし））
#     ※ここに書いた順に .txt を読み、サンプル単位で新規txtを作ります
SAMPLE_NAMES <- c(
  "250621_Ohashi_CV-AAs",
  "250621_Ohashi_GF-AAs"
)

# (E) クラスタ抽出モード
#     "exclude" : TARGET_CLUSTERS を除外して残りで再解析
#     "keep"    : TARGET_CLUSTERS のみで再解析
FILTER_MODE <- "exclude"

# (F) 対象クラスタ（seurat_clusters の番号）
TARGET_CLUSTERS <- c(3,5)

# (G) 新規txtの出力先フォルダ
EXPORT_TXT_DIR <- file.path(ORIGINAL_DATA_FOLDER, "ClusterFiltered_Txt")
dir.create(EXPORT_TXT_DIR, recursive = TRUE, showWarnings = FALSE)

# (H) 新規txt出力後に v8 を「そのまま」走らせるか
RUN_V8_AFTER_EXPORT <- TRUE

# (I) v8側の出力フォルダ（output_dir）をどこにするか（新規txtと同じ場所にしたい等）
V8_OUTPUT_DIR <- EXPORT_TXT_DIR

# (J) v8のPROJECT_NAME_PREFIX（出力フォルダ名の先頭）
V8_PROJECT_PREFIX <- paste0("ClusterFiltered_", FILTER_MODE, "_", paste(TARGET_CLUSTERS, collapse = "-"), "_")

# (K) v8解析の途中再開は使わない（新規txtからやり直す想定）
V8_RESUME_FROM_RDS <- FALSE


# ========== マージ統合（ver2 追加） ==========
# クラスタ抽出後の再UMAPサブクラスタを元のUMAP空間にマッピングし、
# 統合ラベル（"3-a", "3-b" 等）を生成する。
#
# ENABLE_MERGE_CLUSTERS:
#   "auto" : FILTER_MODE=="keep" かつ RUN_V8_AFTER_EXPORT==TRUE のとき有効
#   TRUE   : 強制ON（keep以外では安全のため自動OFF）
#   FALSE  : 常にOFF
ENABLE_MERGE_CLUSTERS <- "auto"

# ========== PreFlight: reduction_only 再解析（ver3 追加） ==========
# RERUN_PIPELINE_STAGE: "full"(従来の通常再解析) / "reduction_only"(①診断用)。
#   アプリ(generate_cluster_filter_config)が注入。reduction_only のとき、下の
#   make_v8_copy_with_settings が v8(v15) copy の PIPELINE_STAGE へ伝播して UMAP 前で
#   停止させ、絞り込んだ部分集合の reduction RDS だけを作る（merge もスキップ）。
#   その RDS は last_result_dir に残り、PreFlight ②診断 / ④続き が再利用する。
RERUN_PIPELINE_STAGE <- "full"

# DEG 閾値（アプリの再解析設定から V8_ 経由で注入。未注入なら v16 既定）。
V8_DEG_P_THRESH_VAL <- 0.05
# ★ ver57.5: 既定を 0.10 → 0.25 に揃えた（v16 側と同じ理由）。
#   ver57.5 で v16 の検定がこの値を使うようになったため、再解析パネルの
#   log2FC 欄を空にしたときだけ閾値が 0.10 に下がる、という食い違いを防ぐ。
#   TIMS 側 (ver18 の V13_DEG_LOGFC_TH_VAL) は元から 0.25。
V8_DEG_LOGFC_TH_VAL <- 0.25

# 入力正規化ポリシー（★ ver58.0 / デバッグ総点検 A-6）
#   従来 DESI 再解析には受け手が無く、画面で「正規化 OFF（正規化済み入力）」を
#   選んでいても再解析だけ v16 既定（INPUT_NORMALIZED <- FALSE ＝ LogNormalize する）で
#   走っていた。正規化済みの入力に **二重に正規化がかかる**。
#   NA / "" なら上書きせず v16 既定を使う＝従来挙動。
V8_INPUT_NORMALIZED <- NA
V8_NORM_MODE <- ""

# UMAP 条件（★ ver58.0 / A-7）
#   PreFlight パネルは再解析中も画面に出ているのに受け手が無く、推奨値を入れても
#   常に v16 既定で計算されていた。NA / "" なら v16 既定＝従来挙動。
V8_UMAP_N_NEIGHBORS <- NA
V8_UMAP_MIN_DIST <- NA
V8_UMAP_METRIC <- ""
V8_UMAP_DIMS_N <- NA

# マージスクリプトのパス（Python側から自動注入）
MERGE_SCRIPT_PATH <- ""

# サブクラスタの命名ルール: "alpha" → "3-a","3-b" / "numeric" → "3-0","3-1"
MERGE_SUBCLUSTER_NAMING <- "alpha"

# マージ結果の出力先（空の場合は V8_OUTPUT_DIR を使用）
MERGE_OUT_DIR_OVERRIDE <- ""

# マージ結果のファイル名接頭辞
MERGE_OUT_PREFIX <- "UMAP_merged"


# ------------------------------------------------------------
# [UTILS] 以降は編集不要
# ------------------------------------------------------------
.stopif <- function(cond, msg) { if (!isTRUE(cond)) stop(msg, call. = FALSE) }

# どんな型でも PixelID を整数に寄せる（factor罠対策 + "Spot_123" 対応）
as_pixel_id_int <- function(x) {
  if (is.factor(x)) x <- as.character(x)
  if (is.character(x)) {
    y <- suppressWarnings(as.integer(x))
    if (all(is.na(y))) {
      # "Spot_123" など
      y <- suppressWarnings(as.integer(gsub(".*?(\\d+).*", "\\1", x)))
    }
    return(y)
  }
  suppressWarnings(as.integer(x))
}

# rownames（例: "251213_Kizu-Embryo-E14_Spot_123"）から 123 を抜く
pixel_from_cellname <- function(cn) {
  if (is.factor(cn)) cn <- as.character(cn)
  m <- regexpr("Spot_\\d+", cn)
  out <- rep(NA_integer_, length(cn))
  hit <- m != -1
  if (any(hit)) {
    s <- regmatches(cn, m)
    out[hit] <- suppressWarnings(as.integer(gsub("\\D+", "", s)))
  } else {
    # 保険: 末尾の数字
    out <- suppressWarnings(as.integer(gsub(".*?(\\d+)$", "\\1", cn)))
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

# ---------- 元txtから、指定PixelIDの行だけを抜き出してtxtを書き出す ----------
#   重要: ヘッダ(1〜4行)はそのままコピーし、データ行(5行目以降)のみフィルタ
export_filtered_txt_from_original <- function(original_txt_path, out_txt_path, pixel_ids_to_keep,
                                              debug_tsv_path = NULL) {
  .stopif(file.exists(original_txt_path), paste0("元txtが見つかりません: ", original_txt_path))

  lines <- readLines(original_txt_path, warn = FALSE)
  .stopif(length(lines) >= 5, paste0("txtの行数が不足しています: ", original_txt_path))

  # ---- ヘッダ行数を自動推定（先頭に空行が入るWaters txt対策）----
  # 期待: データ開始行は「PixelID(整数) \t X(数値) \t Y(数値) ...」となる
  is_data_line <- function(ln) {
    sp <- strsplit(ln, "\t", fixed = TRUE)[[1]]
    if (length(sp) < 3) return(FALSE)
    a <- trimws(sp[1]); b <- trimws(sp[2]); c <- trimws(sp[3])
    if (!nzchar(a)) return(FALSE)
    if (grepl("\\.", a)) return(FALSE)  # PixelIDは整数の想定（小数は除外）
    aid <- suppressWarnings(as.integer(a))
    bx  <- suppressWarnings(as.numeric(b))
    cy  <- suppressWarnings(as.numeric(c))
    is.finite(aid) && is.finite(bx) && is.finite(cy)
  }
  data_start <- which(vapply(lines, is_data_line, logical(1)))[1]
  .stopif(is.finite(data_start) && data_start >= 2,
          paste0("データ開始行が特定できません: ", original_txt_path,
                 "\n  → 先頭数行(ヘッダ形式)が想定と異なる可能性があります。"))

  header <- lines[1:(data_start - 1)]
  body   <- lines[data_start:length(lines)]

  # PixelID（データ行の第1列）を取得（タブ区切り1列目をそのまま整数化）
  pixel_first <- vapply(strsplit(body, "\t", fixed = TRUE), function(x) trimws(x[1]), character(1))
  pixel_first_num <- suppressWarnings(as.integer(pixel_first))

  keep_set <- unique(as_pixel_id_int(pixel_ids_to_keep))
  keep_set <- keep_set[is.finite(keep_set)]
  .stopif(length(keep_set) > 0, "pixel_ids_to_keep が数値として解釈できません。")

  keep_flag <- (!is.na(pixel_first_num)) & (pixel_first_num %in% keep_set)
  kept_body <- body[keep_flag]

  if (length(kept_body) == 0) {
    if (!is.null(debug_tsv_path)) {
      dbg <- data.frame(
        original_txt = basename(original_txt_path),
        n_body = length(body),
        n_keep_set = length(keep_set),
        keep_set_min = ifelse(length(keep_set) > 0, min(keep_set, na.rm=TRUE), NA),
        keep_set_max = ifelse(length(keep_set) > 0, max(keep_set, na.rm=TRUE), NA),
        txt_pixel_min = suppressWarnings(min(pixel_first_num, na.rm=TRUE)),
        txt_pixel_max = suppressWarnings(max(pixel_first_num, na.rm=TRUE)),
        overlap_n = sum(keep_flag, na.rm=TRUE),
        sample_pixel_head = paste(head(keep_set, 20), collapse = ","),
        txt_pixel_head = paste(head(pixel_first_num, 20), collapse = ","),
        stringsAsFactors = FALSE
      )
      write.table(dbg, file = debug_tsv_path, sep = "\t", row.names = FALSE, quote = FALSE)
    }
    stop(paste0("フィルタ後のデータ行が0です: ", basename(original_txt_path),
                "\n  → ", ifelse(is.null(debug_tsv_path), "debugなし", paste0("debug: ", debug_tsv_path)),
                "\n  原因候補: PixelID不一致（spot_index/rownames由来）・sample名不一致・RDSが別解析"),
         call. = FALSE)
  }

  writeLines(c(header, kept_body), con = out_txt_path, useBytes = TRUE)
  invisible(list(n_kept = length(kept_body), n_total = length(body)))
}

# ---------- v8の USER EDITABLE SETTINGS 部分（data_folder/output_dir/sample_names等）を差し替えたコピーを作る ----------
#    元のv8スクリプト自体は変更しない（コピーに対して置換）
make_v8_copy_with_settings <- function(v8_path, out_path,
                                      data_folder, output_dir, sample_names, project_prefix,
                                      resume_from_rds = FALSE, resume_dir_path = NULL) {

  .stopif(file.exists(v8_path), paste0("v8スクリプトが見つかりません: ", v8_path))
  code <- readLines(v8_path, warn = FALSE)
  # ----------------------------------------------------------
  # [PATCH] Otsu背景除去をスキップ（クラスタ除外/抽出後の再UMAPでは実行しない）
  #
  # ★ ver57.5 (デバッグ総点検 §5.2.1): ここは以前、v8 のブロックを
  #   文字列として探して置換する専用関数を持っていた。しかし終了側の目印
  #     seu_list[[ii]] <- filtering_result_otsu$filtered_seurat
  #   は、v16 が ROI 分割に対応して
  #     seu_list[[length(seu_list) + 1]] <- filtering_result_otsu$filtered_seurat
  #   へ変わった時点で**一致しなくなった**。開始側の目印は当たるため壊れて
  #   見えず、置換関数は無言で元コードを返していた（0 件で `return(code_vec)`）。
  #   その結果、背景除去済みのデータに Otsu 法がもう一度かかり、
  #   **残った中でさらに信号の弱い側が切り捨てられて**いた。
  #   Otsu は毎回その場のデータから閾値を引き直すため、脂肪・壊死部など
  #   信号の低い組織が選択的に失われる。警告は一切出ない。
  #
  #   置換文 `seu_list[[ii]] <- seurat_obj` 自体も、現在の v16 では
  #   **別の事故になる**。`ii` はファイルの番号なので、1 ファイルを複数 ROI に
  #   分ける内側ループが同じ位置を上書きし合い、ROI が 1 つしか残らない。
  #
  #   そこで v16 側に `SKIP_BACKGROUND_FILTER` を置き、ここは値を差し替える
  #   だけにした。`replace_assign_line` は対象行が 0 件なら `.stopif` で
  #   **停止する**ので、将来 v16 を書き換えても空振りに気づける。
  # ----------------------------------------------------------

  # ----------------------------------------------------------
  # [PATCH] Waters txt のデータ行が「末尾の0を省略」して短くなるケースに対応
  #   - v8 の read_desi_data() は data_lines の列数(max_cols)に依存しており、
  #     フィルタ後に「後半のMRMが全て0」の行だけ残ると ncol(data_df) が不足して
  #     data_df[, metabolite_cols] で "未定義の列" エラーになります。
  #   - 期待列数(= 3 + length(metabolite_names)) まで 0 でパディングして安定化します。
  # ----------------------------------------------------------
  patch_v8_pad_truncated_rows <- function(code_vec) {
    start_idx <- grep("data_list\\s*<-\\s*vector\\(\\\"list\\\",\\s*length\\(data_lines\\)\\)", code_vec)
    if (length(start_idx) == 0) return(code_vec)  # 見つからなければそのまま
    s <- start_idx[1]

    end_idx <- grep("data_df\\s*<-\\s*as\\.data\\.frame\\(data_matrix,\\s*stringsAsFactors\\s*=\\s*FALSE\\)", code_vec)
    end_idx <- end_idx[end_idx >= s]
    if (length(end_idx) == 0) return(code_vec)
    e <- end_idx[1]

    indent <- sub("^(\\s*).*$", "\\1", code_vec[s])
    repl <- c(
      paste0(indent, "## [PATCHED] Robust parser: pad truncated rows to expected width (Waters txt may omit trailing zeros)"),
      paste0(indent, "expected_cols <- 3 + length(metabolite_names)"),
      paste0(indent, "data_matrix <- matrix(\"0\", nrow = length(data_lines), ncol = expected_cols)"),
      paste0(indent, "for(i in seq_along(data_lines)) {"),
      paste0(indent, "  split_line <- strsplit(data_lines[i], \"\\t\", fixed = TRUE)[[1]]"),
      paste0(indent, "  split_line <- trimws(split_line)"),
      paste0(indent, "  if (length(split_line) < expected_cols) {"),
      paste0(indent, "    split_line <- c(split_line, rep(\"0\", expected_cols - length(split_line)))"),
      paste0(indent, "  }"),
      paste0(indent, "  data_matrix[i, 1:expected_cols] <- split_line[1:expected_cols]"),
      paste0(indent, "}"),
      paste0(indent, "data_df <- as.data.frame(data_matrix, stringsAsFactors = FALSE)")
    )

    c(code_vec[1:(s-1)], repl, code_vec[(e+1):length(code_vec)])
  }

replace_assign_line <- function(code_vec, var, new_rhs) {
    pat <- paste0("^\\s*", var, "\\s*<-\\s*.*$")
    idx <- grep(pat, code_vec)
    .stopif(length(idx) >= 1, paste0("v8内で ", var, " の代入行が見つかりません（パターン不一致）"))
    code_vec[idx[1]] <- paste0(var, " <- ", new_rhs)
    code_vec
  }

  r_str <- function(x) paste0("\"", gsub("\\\\", "\\\\\\\\", x), "\"")

  code <- replace_assign_line(code, "data_folder", r_str(data_folder))
  code <- replace_assign_line(code, "output_dir",  r_str(output_dir))
  code <- replace_assign_line(code, "PROJECT_NAME_PREFIX", r_str(project_prefix))
  code <- replace_assign_line(code, "RESUME_FROM_RDS", if (isTRUE(resume_from_rds)) "TRUE" else "FALSE")

  # ★ ver57.5: 背景除去(Otsu)は 1 回目の解析で適用済み。再解析では飛ばす。
  #   対象行が無ければ .stopif で停止する（無言の空振りを起こさない）。
  code <- replace_assign_line(code, "SKIP_BACKGROUND_FILTER", "TRUE")

  if (!is.null(resume_dir_path)) {
    code <- replace_assign_line(code, "RESUME_DIR_PATH", r_str(resume_dir_path))
  }

  # PreFlight: reduction_only 再解析なら v8(v15) copy の PIPELINE_STAGE を伝播。
  #   v15 の reduction_only ガードが UMAP/クラスタ/DEG/作図をスキップし、部分集合の
  #   reduction RDS だけを保存する。"full"(通常再解析)では置換しない（旧テンプレに
  #   PIPELINE_STAGE が無くてもエラーにならないよう後方互換を維持）。
  if (!identical(RERUN_PIPELINE_STAGE, "full")) {
    code <- replace_assign_line(code, "PIPELINE_STAGE", r_str(RERUN_PIPELINE_STAGE))
  }

  # DEG 閾値 → v16 copy へ伝播（再解析の p/logFC を反映。既定は v16 同等で実質no-op）
  code <- replace_assign_line(code, "DEG_P_THRESH_VAL", as.character(V8_DEG_P_THRESH_VAL))
  code <- replace_assign_line(code, "DEG_LOGFC_TH_VAL", as.character(V8_DEG_LOGFC_TH_VAL))

  # ★ ver58.0 (A-6): 正規化ポリシー → v16 copy へ伝播。
  #   replace_assign_line は .stopif で 0 件なら停止する（無言の空振りを起こさない）。
  if (exists("V8_INPUT_NORMALIZED") && !is.na(V8_INPUT_NORMALIZED)) {
    code <- replace_assign_line(code, "INPUT_NORMALIZED",
                                if (isTRUE(V8_INPUT_NORMALIZED)) "TRUE" else "FALSE")
  }
  if (exists("V8_NORM_MODE") && nzchar(V8_NORM_MODE)) {
    code <- replace_assign_line(code, "NORM_MODE", r_str(V8_NORM_MODE))
  }

  # ★ ver58.0 (A-7): UMAP 条件 → v16 copy へ伝播（未指定なら触らない＝v16 既定）
  if (exists("V8_UMAP_N_NEIGHBORS") && !is.na(V8_UMAP_N_NEIGHBORS)) {
    code <- replace_assign_line(code, "UMAP_N_NEIGHBORS",
                                paste0(as.integer(V8_UMAP_N_NEIGHBORS), "L"))
  }
  if (exists("V8_UMAP_MIN_DIST") && !is.na(V8_UMAP_MIN_DIST)) {
    code <- replace_assign_line(code, "UMAP_MIN_DIST", as.character(V8_UMAP_MIN_DIST))
  }
  if (exists("V8_UMAP_METRIC") && nzchar(V8_UMAP_METRIC)) {
    code <- replace_assign_line(code, "UMAP_METRIC", r_str(V8_UMAP_METRIC))
  }
  if (exists("V8_UMAP_DIMS_N") && !is.na(V8_UMAP_DIMS_N)) {
    code <- replace_assign_line(code, "UMAP_DIMS_N",
                                paste0(as.integer(V8_UMAP_DIMS_N), "L"))
  }

  # sample_names ブロック差し替え
  start_pat <- "^\\s*sample_names\\s*<-\\s*c\\s*\\("
  start_idx <- grep(start_pat, code)
  .stopif(length(start_idx) >= 1, "v8内で sample_names <- c( の開始行が見つかりません。")
  s <- start_idx[1]
  end_idx <- s + which(grepl("^\\s*\\)\\s*$", code[(s+1):length(code)]))[1]
  .stopif(is.finite(end_idx), "v8内で sample_names の閉じ括弧行が見つかりません。")

  sn_lines <- c(
    "sample_names <- c(",
    paste0("  ", vapply(sample_names, r_str, character(1)), collapse = ",\n"),
    ")"
  )
  code <- c(code[1:(s-1)], sn_lines, code[(end_idx+1):length(code)])

  code <- patch_v8_pad_truncated_rows(code)
  writeLines(code, con = out_path, useBytes = TRUE)
  invisible(out_path)
}

# ------------------------------------------------------------
# [MAIN]
# ------------------------------------------------------------
message(">> Loading Seurat RDS: ", RDS_PATH)
.stopif(file.exists(RDS_PATH), paste0("RDSが見つかりません: ", RDS_PATH))
seu <- load_rds_compact(RDS_PATH)

# seurat_clusters が無い場合、Identsから作る（念のため）
if (!("seurat_clusters" %in% colnames(seu@meta.data))) {
  message(">> meta.data に seurat_clusters が無いので Idents(seu) を seurat_clusters として追加します。")
  seu@meta.data$seurat_clusters <- as.character(Idents(seu))
}

cells_keep <- get_cells_to_keep(seu, FILTER_MODE, TARGET_CLUSTERS, sample_col = "sample", cluster_col = "seurat_clusters")
md_keep <- seu@meta.data[cells_keep, , drop = FALSE]

# PixelID候補を2系統で作る（spot_index優先、ダメならrownames）
pixel_from_spot_index <- rep(NA_integer_, nrow(md_keep))
if ("spot_index" %in% colnames(md_keep)) {
  pixel_from_spot_index <- as_pixel_id_int(md_keep$spot_index)
} else {
  message(">> meta.data に spot_index がありません。rownamesから PixelID を抽出します。")
}
pixel_from_rownames <- pixel_from_cellname(rownames(md_keep))

use_pixel <- pixel_from_spot_index
na_rate <- mean(!is.finite(use_pixel))
if (is.na(na_rate) || na_rate > 0.5) {
  message(">> spot_index からのPixelIDが不安定なので rownames 由来を使用します。")
  use_pixel <- pixel_from_rownames
}

md_keep$PixelID_for_export <- use_pixel
.stopif(any(is.finite(md_keep$PixelID_for_export)), "PixelIDが作れません（spot_index/rownamesの形式を確認）。")

exported_files <- c()
# ★ ver57.5: 再解析側のサンプル名 → 元の名前。マージのピクセル照合に使う。
.merge_sample_map <- c()

for (sn in SAMPLE_NAMES) {
  original_txt <- file.path(ORIGINAL_DATA_FOLDER, paste0(sn, ".txt"))
  .stopif(file.exists(original_txt), paste0("元txtが見つかりません: ", original_txt))

  rows_sn <- md_keep[as.character(md_keep$sample) == as.character(sn), , drop = FALSE]
  if (nrow(rows_sn) == 0) {
    message(".. skip (no remaining spots): ", sn)
    next
  }

  pix_ids <- unique(rows_sn$PixelID_for_export)
  pix_ids <- pix_ids[is.finite(pix_ids)]
  if (length(pix_ids) == 0) {
    message(".. skip (no valid PixelID_for_export): ", sn)
    next
  }

  suffix <- if (FILTER_MODE == "exclude") {
    paste0("_EXCL_Cl_", paste(TARGET_CLUSTERS, collapse = "-"))
  } else {
    paste0("_KEEP_Cl_", paste(TARGET_CLUSTERS, collapse = "-"))
  }
  out_txt <- file.path(EXPORT_TXT_DIR, paste0(sn, suffix, ".txt"))
  dbg_tsv <- file.path(EXPORT_TXT_DIR, paste0(sn, suffix, "_debug.tsv"))

  message(">> Exporting filtered txt: ", basename(out_txt))
  stat <- export_filtered_txt_from_original(original_txt, out_txt, pix_ids, debug_tsv_path = dbg_tsv)
  message(sprintf("   kept %d / %d lines", stat$n_kept, stat$n_total))

  exported_files <- c(exported_files, tools::file_path_sans_ext(basename(out_txt)))

  # ★ ver57.5 (デバッグ総点検 §5.4): マージ用の対応表を貯める。
  #   書き出したファイル名には `_KEEP_Cl_8` のような接尾辞が付くので、
  #   再解析側のサンプル名は `<sample>_KEEP_Cl_8`、元データ側は `<sample>` になる。
  #   マージはピクセルを `sample|spot_index` の鍵で照合するため、
  #   接尾辞を外す対応表が無いと **1 点も一致せず**、整列に必要な 3 点を
  #   満たせずに `stop()` する（UMAP もクラスタも DEG も計算し終えた最後の一歩で
  #   赤いエラーになり、結果フォルダがプロジェクトに登録されない）。
  #   TIMS 側 (ver18) は同じ形の対応表を作っていた。
  .merge_sample_map[tools::file_path_sans_ext(basename(out_txt))] <- sn
}

.stopif(length(exported_files) > 0, "新規txtが1つも生成されませんでした（RDSの sample 名やクラスタ指定を確認してください）")

message(">> Export complete. Export dir: ", EXPORT_TXT_DIR)
for (f in exported_files) message("   - ", f, ".txt")

# -----------------------
# v8を同一処理で実行（設定だけ差し替え）
# -----------------------
if (isTRUE(RUN_V8_AFTER_EXPORT)) {
  message(">> Running v8 pipeline on exported txt (same analysis).")

  tmp_v8 <- file.path(EXPORT_TXT_DIR, paste0("v8_runtime_copy_", format(Sys.time(), "%Y%m%d_%H%M%S"), ".R"))

  make_v8_copy_with_settings(
    v8_path = V8_SCRIPT_PATH,
    out_path = tmp_v8,
    data_folder = EXPORT_TXT_DIR,
    output_dir = V8_OUTPUT_DIR,
    sample_names = exported_files,
    project_prefix = V8_PROJECT_PREFIX,
    resume_from_rds = V8_RESUME_FROM_RDS,
    resume_dir_path = NULL
  )

  message(">> Sourcing v8 copy: ", tmp_v8)
  source(tmp_v8)
}

# ------------------------------------------------------------
# (ADD ver2) マージスクリプトによるサブクラスタ統合
# ------------------------------------------------------------
.should_merge <- FALSE
if (identical(ENABLE_MERGE_CLUSTERS, "auto")) {
  .should_merge <- (FILTER_MODE == "keep") && isTRUE(RUN_V8_AFTER_EXPORT)
} else if (isTRUE(ENABLE_MERGE_CLUSTERS)) {
  .should_merge <- (FILTER_MODE == "keep")
}
# PreFlight: reduction_only(①診断用)では merge をスキップ（クラスタ前提のため）
if (identical(RERUN_PIPELINE_STAGE, "reduction_only")) .should_merge <- FALSE

if (.should_merge && nzchar(MERGE_SCRIPT_PATH) && file.exists(MERGE_SCRIPT_PATH)) {
  message(">> [ver2] Running merge script for sub-cluster integration...")

  # rerun RDS を自動検索: v8 再解析出力から RDS を探す
  .find_rerun_rds <- function(output_dir) {
    # V8_OUTPUT_DIR 以下の RDS_Files/ から Seurat RDS を探す
    rds_dirs <- list.dirs(output_dir, recursive = TRUE)
    rds_dirs <- rds_dirs[grepl("RDS_Files", rds_dirs)]
    for (rd in rds_dirs) {
      rds_files <- list.files(rd, pattern = "\\.rds$", full.names = TRUE, ignore.case = TRUE)
      # harmony / RPCA / SingleSample の RDS を優先
      prio <- rds_files[grepl("(harmony|RPCA|SingleSample)", rds_files, ignore.case = TRUE)]
      if (length(prio) > 0) return(prio[1])
      if (length(rds_files) > 0) return(rds_files[1])
    }
    NULL
  }

  merge_rerun_rds <- .find_rerun_rds(V8_OUTPUT_DIR)

  if (!is.null(merge_rerun_rds) && file.exists(merge_rerun_rds)) {
    merge_out <- MERGE_OUT_DIR_OVERRIDE
    if (!nzchar(merge_out)) merge_out <- V8_OUTPUT_DIR

    # マージスクリプトのパラメータを設定してから source
    BASE_RDS_PATH       <- RDS_PATH
    RERUN_RDS_PATH      <- merge_rerun_rds
    MERGE_BASE_CLUSTERS <- TARGET_CLUSTERS
    SUBCLUSTER_NAMING   <- MERGE_SUBCLUSTER_NAMING
    # ★ ver57.5: 書き出し時に作った「再解析名 → 元の名前」の対応表を渡す。
    #   従来はここが NULL 固定で、接尾辞付きの名前を元の名前へ寄せられず、
    #   マージは必ず「対応点 3 点未満」で停止していた（TIMS 側は対応表を渡していた）。
    SAMPLE_NAME_MAP     <- if (length(.merge_sample_map) > 0) .merge_sample_map else NULL
    MERGE_OUT_DIR       <- merge_out
    # MERGE_OUT_PREFIX は USER SETTINGS で定義済み
    BASE_REDUCTION      <- "umap"
    RERUN_REDUCTION     <- "umap"

    message(">> [ver2] base RDS: ", BASE_RDS_PATH)
    message(">> [ver2] rerun RDS: ", RERUN_RDS_PATH)
    message(">> [ver2] merge clusters: ", paste(MERGE_BASE_CLUSTERS, collapse = ", "))
    source(MERGE_SCRIPT_PATH)
  } else {
    message(">> [ver2] Merge skipped: rerun RDS not found in ", V8_OUTPUT_DIR)
  }
} else if (.should_merge) {
  message(">> [ver2] Merge skipped: MERGE_SCRIPT_PATH not set or not found.")
}

message(">> Done.")

# --- 解析レシート: R サイドカー出力（rds_io.R で定義、防御的・失敗しても無害）---
if (exists("write_receipt_sidecar")) try(write_receipt_sidecar(), silent = TRUE)
