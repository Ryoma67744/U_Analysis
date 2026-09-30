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
source(file.path(dirname(normalizePath(sub("^--file=", "",
        grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)[1]),
        mustWork = FALSE)), "analysis_contract.R"))

obj <- .step("RDS 展開", load_rds_compact(rds_path))

# ★ ver77.0: ファイル名と reduction 名だけでは補正空間を判定できないため、
# wrapper / assay / 実行コマンドを、Seurat を取り出す前から保存する。
wrapper_reduction <- if (is.list(obj) && !inherits(obj, "Seurat")) obj$reduction else NULL

# TIMS ver13 互換: list(obj=seu, ...) 形式の場合、Seuratオブジェクトを取り出す
if (is.list(obj) && !inherits(obj, "Seurat") && "obj" %in% names(obj)) {
  cat("Detected list-wrapped Seurat object. Extracting $obj...\n")
  obj <- obj$obj
}

# ★ ver77.0: 旧結果の別保存 UMAP は CellID を完全照合してから使用する。
sidecar_arg <- grep("^--embedding-sidecar=", args, value = TRUE)
sidecar_path <- if (length(sidecar_arg)) sub("^--embedding-sidecar=", "", sidecar_arg[1]) else NULL
embedding_location <- "primary"
embedding_kind <- "none"
# --- UMAP coordinates ---
has_umap <- "umap" %in% names(obj@reductions)
if (has_umap) {
  umap_coords <- Embeddings(obj, "umap")
  embedding_kind <- "umap"
} else if (!is.null(sidecar_path) && file.exists(sidecar_path)) {
  side <- load_rds_compact(sidecar_path)
  if (is.list(side) && !is.data.frame(side) && !is.null(side$umap)) side <- side$umap
  side <- as.matrix(side)
  ids <- colnames(obj)
  if (!is.numeric(side) || ncol(side) < 2 || is.null(rownames(side)) ||
      anyDuplicated(rownames(side)) || !setequal(rownames(side), ids) ||
      any(!is.finite(side[, 1:2, drop = FALSE]))) {
    stop("別保存 UMAP の CellID / 座標が主 RDS と一致しません")
  }
  umap_coords <- side[ids, 1:2, drop = FALSE]
  has_umap <- TRUE
  embedding_kind <- "umap"
  embedding_location <- "sidecar"
  rm(side)
} else if ("pca" %in% names(obj@reductions) && ncol(Embeddings(obj, "pca")) >= 2) {
  # ★ ver77.0: PC1–PC2 は UMAP と区別して descriptor に渡す。
  umap_coords <- Embeddings(obj, "pca")[, 1:2]
  embedding_kind <- "pca2d"
} else {
  umap_coords <- matrix(NA_real_, ncol(obj), 2, dimnames = list(colnames(obj), NULL))
}
colnames(umap_coords)[1:2] <- c("UMAP_1", "UMAP_2")
if (embedding_kind != "none") {
  ids <- colnames(obj)
  if (is.null(rownames(umap_coords)) || anyDuplicated(rownames(umap_coords)) ||
      !setequal(rownames(umap_coords), ids) || any(!is.finite(umap_coords[, 1:2, drop = FALSE]))) {
    stop("埋め込み座標の CellID / 数値が不正です")
  }
  umap_coords <- umap_coords[ids, 1:2, drop = FALSE]
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

# ★ ver67.0: 新しい由来情報を持つ結果だけ、複数ファイルにまたがる同名Sampleを分ける。
# 旧RDSの保存済みH&E対応キーと、衝突していないSample名は維持する。
if ("source_file_id" %in% names(meta) &&
    (identical(obj@misc$source_identity_policy, "file_pixel_v1") ||
     nzchar(ua_value(obj@misc$analysis_signature)))) {
  sample_col <- ua_disambiguate_samples(sample_col, meta$source_file_id)
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

# ★ ver67.0: 固定列の抽出で脱落していた切片・個体・群を専用列のまま渡す。
# Sample は H&E 対応キーとして従来値を維持する。
section_columns <- c("ua_coordinate_component", "source_file_id", "source_pixel_id",
                     "section_id", "section_display_name", "subject_id",
                     "group", "integration_unit_id")
for (field in intersect(section_columns, colnames(meta))) {
  plot_data[[field]] <- as.character(meta[[field]])
}

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
# ★ ver77.0: 巨大な features/行列を JSON に複製せず、手法と各段階の根拠だけ抽出する。
command_facts <- lapply(names(obj@commands), function(nm) {
  command <- obj@commands[[nm]]
  params <- command@params
  keep <- c("reduction", "dims", "assay", "graph.name", "k.param", "algorithm",
            "resolution", "n.neighbors", "min.dist", "metric", "annoy.metric", "seed.use", "random.seed", "reduction.name")
  list(name = nm, time = as.numeric(command@time.stamp),
       assay = command@assay.used, parameters = params[intersect(names(params), keep)])
})
command_facts <- command_facts[order(vapply(command_facts, function(x) x$time, numeric(1)))]
last_command <- function(pattern) {
  hits <- Filter(function(x) grepl(pattern, x$name, ignore.case = TRUE), command_facts)
  if (length(hits)) hits[[length(hits)]] else list(parameters = list())
}
neighbor_command <- last_command("FindNeighbors")
cluster_command <- last_command("FindClusters")
# ★ ver77.0: 別 graph を作った履歴が後にあっても、最後のクラスタ計算の graph を追う。
cluster_graph <- cluster_command$parameters$graph.name
if (!is.null(cluster_graph)) {
  matching_neighbors <- Filter(function(x) {
    graph <- x$parameters$graph.name
    if (is.null(graph) && length(x$assay)) graph <- paste0(x$assay, "_snn")
    grepl("FindNeighbors", x$name) && any(cluster_graph %in% graph) &&
      x$time <= cluster_command$time
  }, command_facts)
  neighbor_command <- if (length(matching_neighbors)) matching_neighbors[[length(matching_neighbors)]] else list(parameters = list())
}
umap_command <- last_command("RunUMAP")
reduction_facts <- lapply(obj@reductions, function(red) {
  emb <- red@cell.embeddings
  list(assay = red@assay.used, n_cells = nrow(emb), n_dims = ncol(emb))
})
embedding_space <- if (embedding_kind == "pca2d") "pca" else umap_command$parameters$reduction
# ★ ver77.0: 同じ CellID だけでは sidecar がどの空間由来か証明できない。
# v2 manifest の内容 hash / numerical facts との照合は Python 側で行う。
if (embedding_location == "sidecar") embedding_space <- NULL
cluster_space <- neighbor_command$parameters$reduction
if (is.null(cluster_space)) cluster_space <- obj@misc$cluster_reduction
has_clusters <- "seurat_clusters" %in% names(meta)
facts <- list(
  wrapper_reduction = wrapper_reduction,
  wrapper_method = obj@misc$analysis_method,
  result_provenance = obj@misc$result_provenance,
  misc = obj@misc[intersect(names(obj@misc), c("pca_origin", "cluster_reduction", "analysis_method", "independent_pca"))],
  reductions = reduction_facts, commands = command_facts,
  embedding = list(kind = embedding_kind, space = embedding_space, location = embedding_location,
                   origin_state = if (embedding_location == "sidecar") "unknown" else "inferred",
                   parameters = if (embedding_kind == "pca2d") list(dims = c(1L, 2L)) else umap_command$parameters),
  clusters = list(space = cluster_space, graph = cluster_command$parameters$graph.name,
                  parameters = modifyList(neighbor_command$parameters, cluster_command$parameters)),
  has_rpca_integration = any(vapply(command_facts, function(x)
    grepl("FindIntegrationAnchors", x$name) && identical(x$parameters$reduction, "rpca"), logical(1))),
  has_clusters = has_clusters, has_spatial = "SpatialX" %in% colnames(plot_data),
  has_expression = length(features) > 0,
  cell_ids_valid = !anyDuplicated(cell_ids) && !anyNA(cell_ids),
  cell_ids_r_hash = if (exists("ua_digest", mode = "function")) ua_digest(cell_ids) else NULL,
  cluster_commands_verified = !is.null(neighbor_command$name) && !is.null(cluster_command$name),
  idents_match_seurat_clusters = if ("seurat_clusters" %in% names(meta))
    identical(as.character(meta$seurat_clusters), clusters) else NULL
)
meta_info <- list(
  n_cells    = nrow(plot_data),
  n_clusters = length(unique(clusters)),
  n_features = length(features),
  samples    = samples,
  has_umap   = has_umap,
  has_spatial = ("SpatialX" %in% colnames(plot_data)),
  has_merged_clusters = (has_merged && has_merged_umap),
  section_metadata_columns = intersect(section_columns, colnames(plot_data)),
  pca_origin = obj@misc$pca_origin,
  cluster_reduction = obj@misc$cluster_reduction,
  result_facts = facts
)
jsonlite::write_json(meta_info, file.path(output_dir, "extraction_meta.json"),
                     auto_unbox = TRUE, pretty = TRUE)
cat("Wrote extraction_meta.json\n")

cat("Extraction complete.\n")
