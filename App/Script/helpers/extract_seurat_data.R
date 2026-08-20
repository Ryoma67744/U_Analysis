# =============================================================================
# MSI Analysis Application - Seurat Data Extractor
# Seurat RDS → Parquet/CSV 変換ヘルパー
#
# Usage: Rscript extract_seurat_data.R <rds_path> <output_dir> [--with-expression]
#
# --with-expression: expression_matrix.parquet を生成。Feature plot / m/z キャリブ
#                    レーション時のみ必要なので、初回データロードでは省略するのが推奨。
#
# 所要時間の実測 (ver50.1 時点 / 203,078 cell x 1,536 feature / コンテナ 12GB):
#   RDS 展開      118.7 秒 (xz。qs が使えれば 5〜15 秒の見込み)
#   JoinLayers     12.6 秒
#   発現行列生成   87.4 秒 → ver50.1 で密行列コピー削減により短縮
#   合計          233.7 秒
# ※ 旧コメントは「20-60 秒」としていたが実測と 4〜11 倍乖離していた。
#   各段の秒数は下の [extract] 行として標準出力に出る。
# =============================================================================

# ---- 段階ごとの所要時間と常駐メモリを必ず残す -------------------------------
# [ver50.1] これが無かったため「抽出が遅い」の内訳を手作業で測るまで特定できず、
#   結果として xz フォールバックに数か月気づけなかった。
.rss_gb <- function() {
  tryCatch({
    v <- grep("^VmRSS:", readLines(sprintf("/proc/%d/status", Sys.getpid())),
              value = TRUE)
    as.numeric(gsub("[^0-9]", "", v)) / 1024^2
  }, error = function(e) NA_real_)
}
.step <- function(label, expr) {
  t0 <- Sys.time()
  v <- force(expr)
  cat(sprintf("[extract] %-22s %7.1f 秒  RSS %5.2f GB\n", label,
              as.numeric(difftime(Sys.time(), t0, units = "secs")), .rss_gb()))
  flush(stdout())
  invisible(v)
}

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) {
  stop("Usage: Rscript extract_seurat_data.R <rds_path> <output_dir> [--with-expression]")
}

rds_path        <- args[1]
output_dir      <- args[2]
with_expression <- length(args) >= 3 && any(args[-c(1, 2)] == "--with-expression")

if (!file.exists(rds_path)) {
  stop("RDS file not found: ", rds_path)
}

dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

cat("Loading Seurat object:", rds_path, "\n")
suppressPackageStartupMessages(library(Seurat))

# 共通 I/O ヘルパーを読み込み (slim qs 形式 / 旧 saveRDS 形式の両対応)
source(file.path(dirname(normalizePath(sub("^--file=", "",
        grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)[1]),
        mustWork = FALSE)), "rds_io.R"))

obj <- .step("RDS 展開", load_rds_compact(rds_path))

# TIMS ver13 互換: list(obj=seu, ...) 形式の場合、Seuratオブジェクトを取り出す
if (is.list(obj) && !inherits(obj, "Seurat") && "obj" %in% names(obj)) {
  cat("Detected list-wrapped Seurat object. Extracting $obj...\n")
  obj <- obj$obj
}

# --- UMAP coordinates ---
has_umap <- "umap" %in% names(obj@reductions)
if (has_umap) {
  umap_coords <- Embeddings(obj, "umap")
} else {
  # UMAP がなければ PCA の最初の2次元で代替
  umap_coords <- Embeddings(obj, "pca")[, 1:2]
  colnames(umap_coords) <- c("UMAP_1", "UMAP_2")
}

# --- Cluster IDs ---
clusters <- as.character(Idents(obj))

# --- Metadata ---
meta <- obj@meta.data
cell_ids <- rownames(meta)

# --- Sample identification ---
# 最もユニーク数が多い列を Sample として採用する。
#
# ★ ver58.3: コメントが実装と逆だった。「優先順: slice_id > condition > sample >
#   orig.ident」と書いてあったが、実際は候補を sample → condition → slice_id の順に
#   見て **厳密に多いときだけ** 置き換えるので、同数なら先に見た sample が勝つ。
#   起点も orig.ident なので、全候補が 1 種類なら orig.ident が残る
#   (Seurat の既定でセル名 `<ステム>_Spot_<n>` の第 1 トークン = ファイル名側)。
#
#   **この挙動は意図どおりなので変えない。** コメントどおりの優先順に直すと、
#   領域アノテーション CSV 無しのデータ (annotation が全 spot 'Unannotated') で
#   slice_id が勝ってしまい、画面のサンプル一覧が全部 'Unannotated' になる。
#   Sample 名は H&E オーバーレイの保存キー (hne_overlay_state.json) でもあるため、
#   ここを変えると既存プロジェクトの ROI 割当が丸ごと参照できなくなる。
#
#   なお、この「ファイル名側が採用される」ことと、データ出力が annotation 列で
#   突合していたことが噛み合わず、クラスタ列が全行空欄になっていた。
#   そちらは export_transform.py 側の stem フォールバックで解消済み (ver58.3)。
sample_col <- as.character(meta$orig.ident)
best_n <- length(unique(sample_col))

candidate_cols <- c("sample", "condition", "slice_id")
for (cname in candidate_cols) {
  if (cname %in% colnames(meta)) {
    cand <- as.character(meta[[cname]])
    n_unique <- length(unique(cand[!is.na(cand) & nzchar(cand)]))
    if (n_unique > best_n) {
      sample_col <- cand
      best_n <- n_unique
      cat("Using meta$", cname, " for Sample column (", n_unique, " samples)\n", sep = "")
    }
  }
}

# --- Build plot_data ---
plot_data <- data.frame(
  CellID   = cell_ids,
  UMAP_1   = umap_coords[, 1],
  UMAP_2   = umap_coords[, 2],
  Cluster  = clusters,
  Sample   = sample_col,
  stringsAsFactors = FALSE
)

# Optional columns
if ("nCount_Spatial" %in% colnames(meta)) {
  plot_data$TotalCount <- meta$nCount_Spatial
}
if ("nFeature_Spatial" %in% colnames(meta)) {
  plot_data$nFeature <- meta$nFeature_Spatial
}

# Spatial coordinates (multiple possible column names)
x_col <- intersect(c("x", "x_coord", "X"), colnames(meta))
y_col <- intersect(c("y", "y_coord", "Y"), colnames(meta))
if (length(x_col) > 0 && length(y_col) > 0) {
  plot_data$SpatialX <- meta[[x_col[1]]]
  plot_data$SpatialY <- meta[[y_col[1]]]
}

# --- Write plot_data ---
# Try Parquet first (faster + smaller), fallback to CSV
tryCatch({
  suppressPackageStartupMessages(library(arrow))
  arrow::write_parquet(plot_data, file.path(output_dir, "plot_data.parquet"))
  cat("Wrote plot_data.parquet\n")
}, error = function(e) {
  cat("arrow not available, writing CSV instead\n")
  write.csv(plot_data, file.path(output_dir, "plot_data.csv"), row.names = FALSE)
})

# --- Cluster stats ---
cluster_counts <- as.data.frame(table(clusters), stringsAsFactors = FALSE)
colnames(cluster_counts) <- c("Cluster", "Count")
write.csv(cluster_counts, file.path(output_dir, "cluster_stats.csv"), row.names = FALSE)
cat("Wrote cluster_stats.csv\n")

# --- 強度/発現は測定アッセイ(Spatial)から読む ---
# RPCA(v4 IntegrateData)の integrated は補正値のため定量に使わない（統合手法非依存）。
# UMAP座標/クラスタ/plot_data は上で確定済み（reduction/Idents/meta 由来）→ 本切替の影響外。
# 以降の features_list / expression_matrix.parquet が測定強度になる。
if (exists("pick_measurement_assay", mode = "function")) {
  DefaultAssay(obj) <- pick_measurement_assay(obj)
}

# --- Features list ---
# Seurat v5 では JoinLayers() が必要（複数レイヤー対応）
# 注: .step の第 2 引数は promise なので、評価はこの呼び出し元 (global) の
#     環境で行われる。したがって通常どおり `<-` で obj を更新できる。
.step("JoinLayers", tryCatch({
  obj <- JoinLayers(obj)
}, error = function(e) {
  # v4 以前では JoinLayers が存在しないため無視
  NULL
}))
# v5: LayerData()、v4 fallback: GetAssayData(layer=...)
expr_data <- .step("LayerData(data)", tryCatch({
  LayerData(obj, layer = "data")
}, error = function(e) {
  tryCatch({
    GetAssayData(obj, layer = "data")
  }, error = function(e2) {
    GetAssayData(obj, slot = "data")
  })
}))
features <- rownames(expr_data)
cat(sprintf("[extract] %s feature x %s cell / %s\n",
            format(nrow(expr_data), big.mark = ","),
            format(ncol(expr_data), big.mark = ","), class(expr_data)[1]))
writeLines(features, file.path(output_dir, "features_list.txt"))
cat("Wrote features_list.txt (", length(features), " features)\n")

# --- Expression matrix (for fast Python-side feature queries) ---
# 遅延化: Feature plot / m/z キャリブレーションが必要なときのみ --with-expression で生成。
if (with_expression) {
  tryCatch({
    suppressPackageStartupMessages(library(arrow))
    cat("Exporting expression matrix to Parquet...\n")
    .t0 <- Sys.time()
    # [ver50.1] 密行列のコピーを 4 回から 1 回に削減。
    #   旧実装は as.matrix -> t() -> as.data.frame -> 列並べ替え と 4 回コピーしており、
    #   実測 (203,078 cell x 1,536 feature) で 1 コピー 2.32GB x 4 = 9.3GB。
    #   各段で rm+gc を挟んだ計測ですら RSS 11.17GB に達し、コンテナ上限 12GB を
    #   超える見込みだった（今 OOM していないのは運）。時間も t() 26.0 秒 +
    #   as.data.frame 25.5 秒を要していた。
    #   Matrix::t() はスパースのまま転置するので、密になるのは Arrow 配列だけになる。
    #   R のベクタは 1 列ぶん (約 1.6MB) しか同時に持たない。
    #   ※ 型は float64 のまま維持する。出力を旧実装とビット単位で一致させるため。
    #      float32 化は seurat_bridge.get_feature_expression_fast の型前提を
    #      確認してから別途。
    expr_t <- Matrix::t(expr_data)          # cell x feature（スパースのまま）
    acols <- vector("list", length(features) + 1L)
    names(acols) <- c("CellID", features)
    acols[[1L]] <- arrow::Array$create(cell_ids)
    for (j in seq_along(features)) {
      acols[[j + 1L]] <- arrow::Array$create(as.numeric(expr_t[, j]))
    }
    rm(expr_t); invisible(gc(FALSE))
    tbl <- do.call(arrow::Table$create, acols)
    rm(acols); invisible(gc(FALSE))
    # row_group_size は明示する（既定 None は 1,048,576 行で無言分割する）
    arrow::write_parquet(tbl, file.path(output_dir, "expression_matrix.parquet"),
                         chunk_size = max(1L, tbl$num_rows))
    cat("Wrote expression_matrix.parquet (", length(features), " features, ",
        sprintf("%.1f", as.numeric(difftime(Sys.time(), .t0, units = "secs"))),
        " sec)\n", sep = "")
    rm(tbl); invisible(gc(FALSE))
  }, error = function(e) {
    cat("Warning: expression matrix export failed:", conditionMessage(e), "\n")
    cat("Feature queries will fall back to R subprocess.\n")
  })
} else {
  cat("Skipping expression_matrix.parquet (use --with-expression to generate)\n")
}

# --- Merged cluster data (if available) ---
has_merged <- "seurat_clusters_merged" %in% colnames(obj@meta.data)
has_merged_umap <- "umap_merged" %in% names(obj@reductions)

if (has_merged && has_merged_umap) {
  cat("Detected merged cluster data. Exporting...\n")
  merged_umap <- Embeddings(obj, "umap_merged")
  plot_data$Cluster_merged <- as.character(obj@meta.data$seurat_clusters_merged)
  plot_data$UMAP_1_merged  <- merged_umap[, 1]
  plot_data$UMAP_2_merged  <- merged_umap[, 2]
  cat("Added Cluster_merged, UMAP_1_merged, UMAP_2_merged to plot_data\n")

  # plot_data を再書き出し（マージデータ含む）
  tryCatch({
    arrow::write_parquet(plot_data, file.path(output_dir, "plot_data.parquet"))
    cat("Re-wrote plot_data.parquet (with merged data)\n")
  }, error = function(e) {
    write.csv(plot_data, file.path(output_dir, "plot_data.csv"), row.names = FALSE)
    cat("Re-wrote plot_data.csv (with merged data)\n")
  })
} else {
  cat("No merged cluster data found (umap_merged / seurat_clusters_merged).\n")
}

# --- Metadata JSON ---
samples <- unique(sample_col)
meta_info <- list(
  n_cells    = nrow(plot_data),
  n_clusters = length(unique(clusters)),
  n_features = length(features),
  samples    = samples,
  has_umap   = has_umap,
  has_spatial = ("SpatialX" %in% colnames(plot_data)),
  has_merged_clusters = (has_merged && has_merged_umap)
)
jsonlite::write_json(meta_info, file.path(output_dir, "extraction_meta.json"),
                     auto_unbox = TRUE, pretty = TRUE)
cat("Wrote extraction_meta.json\n")

cat("Extraction complete.\n")
