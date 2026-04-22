# -*- coding: UTF-8 -*-
# ============================================================
# UMAP Merge Clusters — サブクラスタ統合スクリプト
#  VERSION: ver1 (2026-03-05)
# ============================================================
# 目的:
#   クラスタ抽出（keep）→ 再UMAP 後のサブクラスタラベルを
#   元の UMAP 座標空間にマッピングして統合表示する。
#
# 入力:
#   - BASE_RDS_PATH  : 元の Seurat RDS（全クラスタ入り）
#   - RERUN_RDS_PATH : 再解析後の Seurat RDS（抽出クラスタのみ）
#   - MERGE_BASE_CLUSTERS : マージ対象の元クラスタ番号
#
# 出力:
#   - 統合 Seurat RDS（umap_merged + seurat_clusters_merged 追加）
#   - Before / After 比較 PNG
#
# 対応: DESI / TIMS 共通（Seurat オブジェクトのみ操作）
# ============================================================

message("=== RUNNING: UMAP_Merge_Clusters  [ver1] ===")

# ---- 共通 RDS I/O ヘルパーの読み込み (slim RDS / 旧 RDS の両対応) ----
# 呼び出し元で既に source() 済みなら重複読み込みを避ける
if (!exists("load_rds_compact", mode = "function")) {
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
        # Common -> ../helpers
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
}

# ------------------------------------------------------------
# [USER SETTINGS] ここだけ編集（Python側から自動注入される）
# ------------------------------------------------------------

# (A) 元の Seurat RDS（全クラスタ入り / Step2 or Step3）
#     source() で呼び出された場合、呼び出し元で設定済みなら上書きしない
if (!exists("BASE_RDS_PATH") || !nzchar(BASE_RDS_PATH))     BASE_RDS_PATH <- ""

# (B) 再解析後の Seurat RDS（抽出クラスタのみ / 再UMAP結果）
if (!exists("RERUN_RDS_PATH") || !nzchar(RERUN_RDS_PATH))   RERUN_RDS_PATH <- ""

# (C) マージ対象の元クラスタ番号リスト
if (!exists("MERGE_BASE_CLUSTERS") || length(MERGE_BASE_CLUSTERS) == 0) MERGE_BASE_CLUSTERS <- c()

# (D) サブクラスタの命名ルール
#     "alpha"   → "3-a", "3-b", "3-c" ...
#     "numeric" → "3-0", "3-1", "3-2" ...
if (!exists("SUBCLUSTER_NAMING"))   SUBCLUSTER_NAMING <- "alpha"

# (E) sample_name_map（マルチサンプル時のサンプル名対応）
#     base と rerun でサンプル名が異なる場合に、rerun→base のマッピングを指定
#     NULL の場合は名前がそのまま一致する前提
if (!exists("SAMPLE_NAME_MAP"))     SAMPLE_NAME_MAP <- NULL

# (F) 出力先
if (!exists("MERGE_OUT_DIR") || !nzchar(MERGE_OUT_DIR))     MERGE_OUT_DIR <- ""
if (!exists("MERGE_OUT_PREFIX"))    MERGE_OUT_PREFIX <- "UMAP_merged"

# (G) 元 Seurat の UMAP reduction 名
if (!exists("BASE_REDUCTION"))      BASE_REDUCTION <- "umap"

# (H) 再解析 Seurat の UMAP reduction 名
if (!exists("RERUN_REDUCTION"))     RERUN_REDUCTION <- "umap"


# ------------------------------------------------------------
# [UTILS] 以降は編集不要
# ------------------------------------------------------------

.stopif <- function(cond, msg) { if (!isTRUE(cond)) stop(msg, call. = FALSE) }


# --- セルキー生成 ---
# sample + spot_index で安定に対応付け（cellname 変更に強い）
.make_cell_key <- function(obj, sample_name_map = NULL) {
  md <- obj@meta.data
  .stopif(all(c("sample", "spot_index") %in% colnames(md)),
          "マージには meta.data に sample/spot_index が必要です。")
  sn <- as.character(md$sample)

  # sample 名が base と rerun で違う場合に、rerun 側を base 名へ寄せる
  if (!is.null(sample_name_map) && length(sample_name_map) > 0) {
    tmp <- unname(sample_name_map[sn])
    sn <- ifelse(!is.na(tmp), tmp, sn)
  }

  si <- md$spot_index
  if (is.factor(si)) si <- as.character(si)
  si <- suppressWarnings(as.numeric(si))
  .stopif(any(is.finite(si)), "spot_index が数値として解釈できません。")
  key <- paste0(sn, "|", si)
  names(key) <- rownames(md)   # セル名で名前付き（.get_umap_df で emb$cell による参照に必要）
  key
}


# --- UMAP embedding を data.frame(UMAP_1, UMAP_2, cell, key) で取得 ---
.get_umap_df <- function(obj, reduction = "umap", sample_name_map = NULL) {
  .stopif(reduction %in% names(obj@reductions),
          paste0("UMAP reduction '", reduction, "' が見つかりません。"))
  emb <- as.data.frame(Seurat::Embeddings(obj, reduction = reduction))
  .stopif(ncol(emb) >= 2, "UMAP embedding の次元が不足しています。")
  emb <- emb[, 1:2, drop = FALSE]
  colnames(emb) <- c("UMAP_1", "UMAP_2")
  emb$cell <- rownames(emb)
  emb$key <- .make_cell_key(obj, sample_name_map)[emb$cell]
  emb
}


# --- 2次元アフィン変換（new → old）を最小二乗で推定 ---
.fit_affine_2d <- function(old_xy, new_xy) {
  .stopif(nrow(old_xy) == nrow(new_xy) && nrow(old_xy) >= 3,
          "整列には3点以上の対応が必要です。")
  D <- cbind(1, as.matrix(new_xy))
  X <- as.matrix(old_xy)
  B <- qr.solve(D, X)  # 3x2
  list(B = B)
}


# --- アフィン変換の適用 ---
.apply_affine_2d <- function(new_xy, fit) {
  D <- cbind(1, as.matrix(new_xy))
  Xhat <- D %*% fit$B
  Xhat
}


# --- サブクラスタ名の生成 ---
.make_subcluster_label <- function(parent_cluster, sub_cluster, naming = "alpha") {
  sub_int <- suppressWarnings(as.integer(sub_cluster))
  if (naming == "alpha") {
    # 0 → a, 1 → b, 2 → c, ...
    suffix <- letters[sub_int + 1]
    if (is.na(suffix)) suffix <- as.character(sub_cluster)
  } else {
    # numeric: そのまま
    suffix <- as.character(sub_cluster)
  }
  paste0(parent_cluster, "-", suffix)
}


# --- Seurat オブジェクトのロード（list wrapper 対応） ---
.load_seurat <- function(rds_path) {
  .stopif(file.exists(rds_path), paste0("RDS が見つかりません: ", rds_path))
  obj <- load_rds_compact(rds_path)
  if (is.list(obj) && !inherits(obj, "Seurat") && "obj" %in% names(obj)) {
    message("  Detected list-wrapped Seurat object. Extracting $obj...")
    obj <- obj$obj
  }
  .stopif(inherits(obj, "Seurat"), "RDS から Seurat オブジェクトを取得できません。")

  # seurat_clusters が無い場合、Idents から作る
  if (!("seurat_clusters" %in% colnames(obj@meta.data))) {
    message("  meta.data に seurat_clusters がないので Idents(obj) を使用します。")
    obj@meta.data$seurat_clusters <- as.character(Seurat::Idents(obj))
  }
  obj
}


# --- 自然順ソート（"0","1","2",...,"5-a","5-b",...,"10" の順にする） ---
.natural_sort <- function(x) {
  num_part <- as.numeric(gsub("-.*$", "", x))
  sub_part <- gsub("^[^-]*-?", "", x)
  x[order(num_part, sub_part)]
}

# --- グリッド間隔を計算 ---
.grid_step_local <- function(v) {
  vu <- sort(unique(v)); if (length(vu) < 2) return(1)
  d <- diff(vu); min(d[d > 0], na.rm = TRUE)
}

# ============================================================
# [MAIN] マージ処理
# ============================================================

merge_clusters <- function(base_seu, rerun_seu,
                           merge_base_clusters,
                           sample_name_map = NULL,
                           base_reduction = "umap",
                           rerun_reduction = "umap",
                           subcluster_naming = "alpha",
                           out_dir = ".",
                           out_prefix = "UMAP_merged") {

  .stopif(requireNamespace("Seurat", quietly = TRUE),
          "マージには Seurat パッケージが必要です。")
  .stopif(requireNamespace("ggplot2", quietly = TRUE),
          "マージには ggplot2 パッケージが必要です。")
  .stopif(requireNamespace("patchwork", quietly = TRUE),
          "マージには patchwork パッケージが必要です。")

  library(ggplot2)

  # --- 1. embeddings + keys ---
  message(">> [Merge] Extracting UMAP embeddings...")
  base_um <- .get_umap_df(base_seu, reduction = base_reduction)
  rer_um  <- .get_umap_df(rerun_seu, reduction = rerun_reduction,
                           sample_name_map = sample_name_map)

  # --- 2. マージ対象セルの特定 ---
  md_base <- base_seu@meta.data
  base_cl <- as.character(md_base$seurat_clusters)
  rep_set <- as.character(merge_base_clusters)
  rep_cells <- rownames(md_base)[base_cl %in% rep_set]
  message(">> [Merge] Target cells: ", length(rep_cells),
          " (clusters: ", paste(rep_set, collapse = ", "), ")")
  .stopif(length(rep_cells) > 0,
          "マージ対象セルが 0 件です（MERGE_BASE_CLUSTERS を確認）。")

  # --- 3. キーマッチング ---
  base_sub <- base_um[base_um$cell %in% rep_cells, , drop = FALSE]
  .stopif(nrow(base_sub) > 0, "base 側 UMAP の対応セルが取れません。")

  m <- merge(base_sub, rer_um, by = "key", suffixes = c("_old", "_new"))
  message(">> [Merge] Matched cells: ", nrow(m))
  .stopif(nrow(m) >= 3,
          paste0("対応セルが少なすぎます（n=", nrow(m),
                 "）。sample/spot_index の不一致を疑ってください。"))

  old_xy <- m[, c("UMAP_1_old", "UMAP_2_old")]
  new_xy <- m[, c("UMAP_1_new", "UMAP_2_new")]

  # --- 4. アフィン整列 ---
  message(">> [Merge] Fitting affine transformation...")
  fit <- .fit_affine_2d(old_xy, new_xy)

  # rerun 全セルの UMAP を base 空間に整列
  new_xy_all <- rer_um[, c("UMAP_1", "UMAP_2")]
  new_xy_aligned <- .apply_affine_2d(new_xy_all, fit)
  rer_um$UMAP_1_aligned <- new_xy_aligned[, 1]
  rer_um$UMAP_2_aligned <- new_xy_aligned[, 2]

  # key → base cell の対応
  key_to_basecell <- setNames(m$cell_old, m$key)

  # --- 5. 統合 embedding 構築 ---
  base_emb <- as.data.frame(Seurat::Embeddings(base_seu, reduction = base_reduction))
  base_emb <- base_emb[, 1:2, drop = FALSE]
  colnames(base_emb) <- c("UMAP_1", "UMAP_2")

  # マッチしたセルの座標を置換
  rep_keys <- unique(m$key)
  rep_base_cells2 <- unique(key_to_basecell[rep_keys])
  rep_base_cells2 <- rep_base_cells2[!is.na(rep_base_cells2)]

  rer_sub_aligned <- rer_um[rer_um$key %in% rep_keys,
                             c("key", "UMAP_1_aligned", "UMAP_2_aligned"), drop = FALSE]
  rer_sub_aligned$base_cell <- key_to_basecell[rer_sub_aligned$key]
  rer_sub_aligned <- rer_sub_aligned[!is.na(rer_sub_aligned$base_cell), , drop = FALSE]

  for (i in seq_len(nrow(rer_sub_aligned))) {
    bc <- rer_sub_aligned$base_cell[i]
    if (bc %in% rownames(base_emb)) {
      base_emb[bc, "UMAP_1"] <- rer_sub_aligned$UMAP_1_aligned[i]
      base_emb[bc, "UMAP_2"] <- rer_sub_aligned$UMAP_2_aligned[i]
    }
  }

  # --- 6. サブクラスタラベル生成 ---
  message(">> [Merge] Generating sub-cluster labels...")
  md_rer <- rerun_seu@meta.data
  rer_cl <- as.character(md_rer$seurat_clusters)
  rer_key <- .make_cell_key(rerun_seu, sample_name_map)
  names(rer_key) <- rownames(md_rer)
  key_to_rercl <- setNames(rer_cl, rer_key)

  # base 側のラベルを構築
  base_new_label <- as.character(md_base$seurat_clusters)
  names(base_new_label) <- rownames(md_base)
  base_key <- .make_cell_key(base_seu)
  names(base_key) <- rownames(md_base)

  for (bc in rep_cells) {
    k <- base_key[[bc]]
    if (!is.null(k) && k %in% names(key_to_rercl)) {
      parent_cl <- as.character(md_base$seurat_clusters[bc])
      sub_cl <- key_to_rercl[[k]]
      base_new_label[bc] <- .make_subcluster_label(parent_cl, sub_cl, subcluster_naming)
    }
  }

  # --- 7. Seurat に新 reduction / 新ラベルを追加 ---
  message(">> [Merge] Adding merged reduction and labels to Seurat object...")
  seu2 <- base_seu

  # umap_merged reduction
  red <- Seurat::CreateDimReducObject(
    embeddings = as.matrix(base_emb),
    key = "UMAPM_",
    assay = Seurat::DefaultAssay(seu2)
  )
  seu2[["umap_merged"]] <- red

  # seurat_clusters_merged メタデータ
  seu2$seurat_clusters_merged <- base_new_label

  # --- 8. Before / After プロット ---
  message(">> [Merge] Generating comparison plot...")
  p_before <- Seurat::DimPlot(seu2, reduction = base_reduction,
                               group.by = "seurat_clusters") +
    ggtitle("UMAP (Original Clusters)")

  p_after <- Seurat::DimPlot(seu2, reduction = "umap_merged",
                              group.by = "seurat_clusters_merged") +
    ggtitle("UMAP (Merged Sub-clusters)")

  p_pair <- p_before + p_after + patchwork::plot_layout(ncol = 2)

  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  png_path <- file.path(out_dir, paste0(out_prefix, "_before_after.png"))
  ggplot2::ggsave(png_path, p_pair, width = 16, height = 7, dpi = 300,
                  bg = "white", limitsize = FALSE)
  message(">> [Merge] Saved: ", png_path)

  # --- 8b. 空間マッピング（切片上のサブクラスタ表示） ---
  if (all(c("x_coord", "y_coord") %in% colnames(seu2@meta.data))) {
    message(">> [Merge] Generating spatial mapping...")
    md_sp <- seu2@meta.data

    # クラスタレベルとパレット構築（サブクラスタ込み）
    merged_levels <- .natural_sort(unique(as.character(md_sp$seurat_clusters_merged)))
    n_lev <- length(merged_levels)
    .base_cols <- c("#E41A1C","#377EB8","#4DAF4A","#984EA3","#FF7F00",
                    "#FFFF33","#A65628","#F781BF","#00CED1","#1F78B4","#B2DF8A","#33A02C")
    if (n_lev <= length(.base_cols)) {
      pal <- setNames(.base_cols[seq_len(n_lev)], merged_levels)
    } else {
      extra <- n_lev - length(.base_cols)
      hues <- seq(0, 360, length.out = extra + 1)[-1]
      pal <- setNames(c(.base_cols, grDevices::hcl(h = hues, c = 70, l = 60)), merged_levels)
    }

    md_sp$seurat_clusters_merged <- factor(md_sp$seurat_clusters_merged, levels = merged_levels)

    # グリッド間隔
    dx <- .grid_step_local(md_sp$x_coord)
    dy <- .grid_step_local(md_sp$y_coord)

    # グルーピング（condition > sample）
    group_var <- "sample"
    if ("condition" %in% colnames(md_sp) && dplyr::n_distinct(na.omit(md_sp$condition)) >= 2) {
      group_var <- "condition"
    }
    groups <- levels(factor(md_sp[[group_var]]))

    plots_label <- list()
    plots_nolabel <- list()
    for (g in groups) {
      md_g <- md_sp[md_sp[[group_var]] == g & !is.na(md_sp[[group_var]]), , drop = FALSE]
      if (nrow(md_g) == 0) next
      centers <- md_g %>% dplyr::group_by(seurat_clusters_merged) %>%
        dplyr::summarise(x = median(x_coord), y = median(y_coord), .groups = "drop")

      p_lab <- ggplot(md_g, aes(x = x_coord, y = y_coord, fill = seurat_clusters_merged)) +
        geom_tile(width = dx, height = dy) +
        scale_fill_manual(values = pal, breaks = merged_levels, drop = FALSE) +
        geom_text(data = centers, aes(x = x, y = y, label = seurat_clusters_merged),
                  inherit.aes = FALSE, size = 3, fontface = "bold") +
        scale_y_reverse() + coord_fixed() + theme_minimal() + ggtitle(g)

      p_nolab <- ggplot(md_g, aes(x = x_coord, y = y_coord, fill = seurat_clusters_merged)) +
        geom_tile(width = dx, height = dy) +
        scale_fill_manual(values = pal, breaks = merged_levels, drop = FALSE) +
        scale_y_reverse() + coord_fixed() + theme_minimal() + ggtitle(paste0(g, " (no labels)"))

      plots_label[[length(plots_label) + 1L]] <- p_lab
      plots_nolabel[[length(plots_nolabel) + 1L]] <- p_nolab
    }

    if (length(plots_label) > 0) {
      p_sp <- patchwork::wrap_plots(plots_label, nrow = 1) +
        patchwork::plot_annotation(title = "Spatial Mapping (Merged Sub-clusters)")
      sp_path <- file.path(out_dir, paste0(out_prefix, "_spatial.png"))
      ggplot2::ggsave(sp_path, p_sp,
                      width = max(6, 5 * length(plots_label)), height = 7,
                      dpi = 300, bg = "white", limitsize = FALSE)
      message(">> [Merge] Saved: ", sp_path)

      p_sp_nl <- patchwork::wrap_plots(plots_nolabel, nrow = 1) +
        patchwork::plot_annotation(title = "Spatial Mapping (Merged Sub-clusters, no labels)")
      sp_nl_path <- file.path(out_dir, paste0(out_prefix, "_spatial_nolabel.png"))
      ggplot2::ggsave(sp_nl_path, p_sp_nl,
                      width = max(6, 5 * length(plots_nolabel)), height = 7,
                      dpi = 300, bg = "white", limitsize = FALSE)
      message(">> [Merge] Saved: ", sp_nl_path)
    }
  } else {
    message(">> [Merge] Spatial mapping skipped (x_coord/y_coord not found in metadata)")
  }

  # --- 9. RDS 保存 (slim: DietSeurat + qs) ---
  rds_path <- file.path(out_dir, paste0(out_prefix, "_seurat.rds"))
  save_rds_compact(seu2, rds_path)
  message(">> [Merge] Saved: ", rds_path)

  # サブクラスタの統計情報をログ出力
  merged_labels <- unique(base_new_label)
  merged_labels <- merged_labels[grepl("-", merged_labels)]
  if (length(merged_labels) > 0) {
    merged_labels <- sort(merged_labels)
    message(">> [Merge] Sub-cluster labels created:")
    for (lbl in merged_labels) {
      n <- sum(base_new_label == lbl)
      message("     ", lbl, ": ", n, " cells")
    }
  }

  message(">> [Merge] Done.")
  invisible(seu2)
}


# ============================================================
# [MAIN] スクリプト実行
# ============================================================

suppressPackageStartupMessages(library(Seurat))

# --- パラメータ検証 ---
.stopif(nzchar(BASE_RDS_PATH), "BASE_RDS_PATH が空です。")
.stopif(nzchar(RERUN_RDS_PATH), "RERUN_RDS_PATH が空です。")
.stopif(length(MERGE_BASE_CLUSTERS) > 0, "MERGE_BASE_CLUSTERS が空です。")
.stopif(nzchar(MERGE_OUT_DIR), "MERGE_OUT_DIR が空です。")

message(">> [Merge] Loading base Seurat: ", BASE_RDS_PATH)
base_seu <- .load_seurat(BASE_RDS_PATH)

message(">> [Merge] Loading rerun Seurat: ", RERUN_RDS_PATH)
rerun_seu <- .load_seurat(RERUN_RDS_PATH)

result_seu <- merge_clusters(
  base_seu          = base_seu,
  rerun_seu         = rerun_seu,
  merge_base_clusters = MERGE_BASE_CLUSTERS,
  sample_name_map   = SAMPLE_NAME_MAP,
  base_reduction    = BASE_REDUCTION,
  rerun_reduction   = RERUN_REDUCTION,
  subcluster_naming = SUBCLUSTER_NAMING,
  out_dir           = MERGE_OUT_DIR,
  out_prefix        = MERGE_OUT_PREFIX
)

message(">> [Merge] Script complete.")
