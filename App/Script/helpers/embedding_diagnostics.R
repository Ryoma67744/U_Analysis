# =============================================================================
# embedding_diagnostics.R
# UMAP Preflight ＋ Batch補正ベンチマーク の共通診断モジュール（Phase 1, ラベル不要部分）
#
# 設計方針（プラン準拠）:
#   - 既存/ReductionReady の reduction 埋め込み(Embeddings)上で診断する（UMAPは実行しない）。
#   - cosine は L2 正規化後の Euclidean kNN で計算（順序は cosine と一致）。
#   - 「最適値の自動決定」ではなく「不適切値の除外＋推奨値＋推奨度＋警告」を返す。
#   - ラベル無しで測れるのは “構造保存” と “バッチ混合(技術反復/strata内)” のみ。
#     生物保存・過補正の確定は独立参照(H&E等)が必要（Phase 2）。
#   - 依存は base R ＋ RANN/dbscan（kNN）＋ 任意で igraph（連結成分高速化）。重い新規依存なし。
#
# 注: 本環境では R 実行による検証は未実施（R 未インストール）。実行時は formals 検証＋回帰テストを行うこと。
# =============================================================================

# ---- kNN バックエンド（RANN を優先、無ければ dbscan）-------------------------
.ed_l2norm_rows <- function(x) {
  nr <- sqrt(rowSums(x * x))
  nr[nr == 0] <- 1
  x / nr
}

# emb: n×d 行列。metric: "cosine" or "euclidean"。k: 近傍数（自己を除く）。
# 返り値: list(idx = n×k 整数行列, dist = n×k 数値行列)
ed_knn <- function(emb, k, metric = c("cosine", "euclidean"), seed = 42L) {
  metric <- match.arg(metric)
  set.seed(seed)
  X <- as.matrix(emb)
  if (metric == "cosine") X <- .ed_l2norm_rows(X)
  n <- nrow(X)
  k <- min(k, n - 1L)
  res <- NULL
  if (requireNamespace("RANN", quietly = TRUE)) {
    nn <- RANN::nn2(data = X, query = X, k = k + 1L)
    res <- list(idx = nn$nn.idx[, -1, drop = FALSE], dist = nn$nn.dists[, -1, drop = FALSE])
  } else if (requireNamespace("dbscan", quietly = TRUE)) {
    nn <- dbscan::kNN(X, k = k)            # dbscan は自己を含まない
    res <- list(idx = nn$id, dist = nn$dist)
  } else {
    stop("RANN も dbscan も利用できません。kNN を計算できません。")
  }
  # Annoy/近似ではないが、recall 監査の枠組みとして自己整合性を記録（将来 Annoy 化時に使用）
  attr(res, "metric") <- metric
  attr(res, "k") <- k
  res
}

# 大きめ k で一度だけ計算し、k ごとに先頭列を切り出す
ed_knn_once <- function(emb, k_max, metric = "cosine", seed = 42L) {
  ed_knn(emb, k_max, metric = metric, seed = seed)
}
ed_slice <- function(nn, k) {
  k <- min(k, ncol(nn$idx))
  list(idx = nn$idx[, seq_len(k), drop = FALSE], dist = nn$dist[, seq_len(k), drop = FALSE])
}

# ---- 安定性（近傍 Jaccard 中央値）-------------------------------------------
ed_jaccard_stability <- function(idx_a, idx_b) {
  n <- nrow(idx_a)
  k <- min(ncol(idx_a), ncol(idx_b))
  vals <- numeric(n)
  for (i in seq_len(n)) {
    a <- idx_a[i, seq_len(k)]; b <- idx_b[i, seq_len(k)]
    inter <- length(intersect(a, b))
    uni   <- length(union(a, b))
    vals[i] <- if (uni > 0) inter / uni else NA_real_
  }
  stats::median(vals, na.rm = TRUE)
}

# ---- 連結成分（igraph 優先、無ければ union-find）----------------------------
.ed_components <- function(idx) {
  n <- nrow(idx); k <- ncol(idx)
  if (requireNamespace("igraph", quietly = TRUE)) {
    ii <- rep(seq_len(n), times = k)
    jj <- as.vector(idx)
    g <- igraph::graph_from_edgelist(cbind(ii, jj), directed = FALSE)
    g <- igraph::simplify(g)
    cmp <- igraph::components(g)
    return(list(membership = cmp$membership, csize = cmp$csize))
  }
  # union-find（path compression）
  parent <- seq_len(n)
  find <- function(x) { while (parent[x] != x) { parent[x] <<- parent[parent[x]]; x <- parent[x] }; x }
  unite <- function(a, b) { ra <- find(a); rb <- find(b); if (ra != rb) parent[ra] <<- rb }
  for (i in seq_len(n)) for (c in seq_len(k)) { j <- idx[i, c]; if (!is.na(j) && j >= 1 && j <= n) unite(i, j) }
  roots <- vapply(seq_len(n), find, integer(1))
  membership <- as.integer(factor(roots))
  csize <- as.integer(table(membership))
  list(membership = membership, csize = csize)
}

ed_giant_component_fraction <- function(idx) {
  cmp <- .ed_components(idx); max(cmp$csize) / sum(cmp$csize)
}
ed_small_component_fraction <- function(idx, min_size = NULL) {
  cmp <- .ed_components(idx)
  if (is.null(min_size)) min_size <- max(10L, ncol(idx))
  sum(cmp$csize[cmp$csize < min_size]) / sum(cmp$csize)
}
ed_n_components <- function(idx) length(.ed_components(idx)$csize)

# ---- 相互近傍率 -------------------------------------------------------------
ed_mutual_nn_rate <- function(idx) {
  n <- nrow(idx); k <- ncol(idx)
  nbr <- lapply(seq_len(n), function(i) idx[i, ])
  cnt <- 0L; tot <- 0L
  for (i in seq_len(n)) {
    for (j in nbr[[i]]) {
      if (is.na(j) || j < 1 || j > n) next
      tot <- tot + 1L
      if (i %in% nbr[[j]]) cnt <- cnt + 1L
    }
  }
  if (tot > 0) cnt / tot else NA_real_
}

# ---- hubness（入次数の Gini）------------------------------------------------
.ed_gini <- function(x) {
  x <- sort(x[is.finite(x)]); n <- length(x); if (n == 0) return(NA_real_)
  if (sum(x) == 0) return(0)
  g <- (2 * sum(seq_len(n) * x)) / (n * sum(x)) - (n + 1) / n
  g
}
ed_hubness_gini <- function(idx, n = NULL) {
  if (is.null(n)) n <- nrow(idx)
  indeg <- tabulate(as.vector(idx), nbins = n)
  .ed_gini(indeg)
}

# ---- k 近傍距離曲線（各 k での k 番目距離の中央値）---------------------------
ed_kdist_curve <- function(dist) apply(dist, 2, stats::median, na.rm = TRUE)

# ---- inverse Simpson（iLISI/cLISI の簡易 kNN 版）----------------------------
# labels: 各点のラベル（factor/character）。返り値: 平均 inverse Simpson（高いほど多様＝混合）
ed_inverse_simpson <- function(idx, labels) {
  labels <- as.integer(factor(labels))
  n <- nrow(idx); k <- ncol(idx)
  out <- numeric(n)
  for (i in seq_len(n)) {
    lab <- labels[idx[i, ]]; lab <- lab[!is.na(lab)]
    if (length(lab) == 0) { out[i] <- NA_real_; next }
    p <- table(lab) / length(lab)
    out[i] <- 1 / sum(p * p)
  }
  mean(out, na.rm = TRUE)
}

# ---- batch が説明する分散（PCA 回帰の簡易版。低いほど混合）------------------
ed_batch_explained_variance <- function(emb, batch) {
  emb <- as.matrix(emb); batch <- factor(batch)
  if (nlevels(batch) < 2) return(NA_real_)
  r2 <- apply(emb, 2, function(v) {
    fit <- tryCatch(stats::lm(v ~ batch), error = function(e) NULL)
    if (is.null(fit)) return(NA_real_)
    s <- summary(fit); s$r.squared
  })
  mean(r2, na.rm = TRUE)
}

# ---- silhouette（batch もしくは bio ラベル）--------------------------------
ed_asw_label <- function(emb, labels, metric = "euclidean", sample_n = 5000L, seed = 42L) {
  if (!requireNamespace("cluster", quietly = TRUE)) return(NA_real_)
  labels <- factor(labels); if (nlevels(labels) < 2) return(NA_real_)
  set.seed(seed)
  n <- nrow(emb)
  idx <- if (n > sample_n) sort(sample.int(n, sample_n)) else seq_len(n)
  X <- as.matrix(emb)[idx, , drop = FALSE]
  if (metric == "cosine") X <- .ed_l2norm_rows(X)
  d <- stats::dist(X)
  sil <- cluster::silhouette(as.integer(labels[idx]), d)
  if (is.null(sil) || all(is.na(sil))) return(NA_real_)
  mean(sil[, "sil_width"], na.rm = TRUE)
}

# =============================================================================
# 上位エントリ: Preflight（dims / n.neighbors の診断）
# =============================================================================
# emb: reduction 埋め込み（n×d）。batch: 任意（strata別連結性のため）。
preflight_diagnose <- function(emb,
                               dims_grid = c(10L, 15L, 20L, 30L),
                               k_grid    = c(10L, 15L, 20L, 30L, 50L, 75L),
                               k_max     = 75L,
                               metric    = "cosine",
                               seed      = 42L,
                               jaccard_thresh = 0.85,
                               giant_thresh   = 0.95) {
  emb <- as.matrix(emb)
  d_avail <- ncol(emb)
  dims_grid <- sort(unique(dims_grid[dims_grid <= d_avail]))
  k_max <- min(k_max, nrow(emb) - 1L)

  # --- dims: 隣接候補間の近傍 Jaccard 安定性（固定 k=min(30,k_max)）---
  k_for_dims <- min(30L, k_max)
  nn_by_dims <- lapply(dims_grid, function(d) ed_knn(emb[, seq_len(d), drop = FALSE], k_for_dims, metric, seed))
  dims_stab <- rep(NA_real_, length(dims_grid))
  if (length(dims_grid) >= 2) {
    for (i in seq_len(length(dims_grid) - 1L)) {
      dims_stab[i] <- ed_jaccard_stability(nn_by_dims[[i]]$idx, nn_by_dims[[i + 1L]]$idx)
    }
  }
  # プラトーに入る最小 dims（隣接安定性 >= 閾値）。無ければ最大候補。
  rec_dims <- dims_grid[length(dims_grid)]
  hit <- which(dims_stab >= jaccard_thresh)
  if (length(hit) > 0) rec_dims <- dims_grid[min(hit)]

  # --- n.neighbors: 採用 dims で k_max を一度だけ計算し、各 k を切り出して評価 ---
  emb_use <- emb[, seq_len(min(rec_dims, d_avail)), drop = FALSE]
  nn_max <- ed_knn_once(emb_use, k_max, metric, seed)
  kg <- sort(unique(k_grid[k_grid <= k_max]))
  per_k <- lapply(kg, function(k) {
    s <- ed_slice(nn_max, k)
    list(
      k = k,
      giant_component_fraction = ed_giant_component_fraction(s$idx),
      small_component_fraction = ed_small_component_fraction(s$idx),
      n_components             = ed_n_components(s$idx),
      mutual_nn_rate           = ed_mutual_nn_rate(s$idx),
      hubness_gini             = ed_hubness_gini(s$idx, n = nrow(emb_use))
    )
  })
  giant <- vapply(per_k, function(x) x$giant_component_fraction, numeric(1))
  # 推奨 n.neighbors: giant >= 閾値 となる最小 k
  rec_nn <- kg[length(kg)]
  hitk <- which(giant >= giant_thresh)
  if (length(hitk) > 0) rec_nn <- kg[min(hitk)]
  # 許容範囲
  allowed <- if (length(hitk) > 0) c(kg[min(hitk)], kg[length(kg)]) else c(NA_integer_, NA_integer_)

  warns <- character(0)
  bad_k <- kg[giant < giant_thresh]
  if (length(bad_k) > 0) warns <- c(warns, sprintf("k=%s で連結成分率 < %.2f", paste(bad_k, collapse=","), giant_thresh))
  if (all(is.na(dims_stab))) warns <- c(warns, "dims 安定性を評価できませんでした")

  conf <- if (length(hitk) > 0 && any(dims_stab >= jaccard_thresh, na.rm = TRUE)) "high" else "medium"

  list(
    metric = metric,
    dims = list(grid = dims_grid, adjacent_jaccard = dims_stab, recommended = rec_dims),
    n_neighbors = list(grid = kg, per_k = per_k, recommended = rec_nn,
                       allowed_range = allowed, kdist_curve = ed_kdist_curve(nn_max$dist)),
    min_dist = 0.3,   # 高次元から決められないため目的固定（既定）
    confidence = conf,
    warnings = warns
  )
}

# =============================================================================
# 上位エントリ: バッチ混合（strata内）＋（参照あれば）保存系
# =============================================================================
# emb: 埋め込み。batch: 技術バッチ。strata: 同一であるべき層(任意)。ref_labels: 生物参照(任意, Phase2)。
space_diagnose <- function(emb, batch, strata = NULL, ref_labels = NULL,
                           k = 30L, metric = "cosine", seed = 42L) {
  emb <- as.matrix(emb)
  res <- list(k = k, metric = metric)

  # --- バッチ混合（strata 指定時は strata 内で評価し平均）---
  mixing_one <- function(e, b) {
    nn <- ed_knn(e, min(k, nrow(e) - 1L), metric, seed)
    list(
      ilisi = ed_inverse_simpson(nn$idx, b),
      asw_batch = ed_asw_label(e, b, metric = metric, seed = seed),
      batch_explained_variance = ed_batch_explained_variance(e, b)
    )
  }
  if (is.null(strata)) {
    res$batch_mixing <- mixing_one(emb, batch)
    res$batch_mixing$scope <- "global (strata未指定の参考値)"
  } else {
    st <- factor(strata)
    parts <- lapply(levels(st), function(L) {
      sel <- which(st == L)
      if (length(sel) < (k + 2L) || length(unique(batch[sel])) < 2) return(NULL)
      mixing_one(emb[sel, , drop = FALSE], batch[sel])
    })
    parts <- parts[!vapply(parts, is.null, logical(1))]
    agg <- function(field) mean(vapply(parts, function(p) p[[field]], numeric(1)), na.rm = TRUE)
    res$batch_mixing <- list(ilisi = agg("ilisi"), asw_batch = agg("asw_batch"),
                             batch_explained_variance = agg("batch_explained_variance"),
                             scope = "within_strata", n_strata = length(parts))
  }

  # --- 構造保存（ラベル不要）: 無補正PCAからの近傍保持などは run_diagnostics 側で空間比較 ---
  # ここでは hubness/連結性の健全性のみ付帯（過変形の警告材料）。
  nn <- ed_knn(emb, min(k, nrow(emb) - 1L), metric, seed)
  res$structure <- list(
    giant_component_fraction = ed_giant_component_fraction(nn$idx),
    hubness_gini = ed_hubness_gini(nn$idx, n = nrow(emb)),
    mutual_nn_rate = ed_mutual_nn_rate(nn$idx)
  )

  # --- 生物保存（参照あれば; Phase 2）---
  if (!is.null(ref_labels)) {
    res$biological_conservation <- list(
      clisi = ed_inverse_simpson(nn$idx, ref_labels),   # 低いほど単一ラベル= 保存
      asw_bio = ed_asw_label(emb, ref_labels, metric = metric, seed = seed)
    )
  } else {
    res$biological_conservation <- NULL
    res$note <- "生物参照なし→構造保存のみ。過補正は確定不可（警告のみ）。"
  }
  res
}
