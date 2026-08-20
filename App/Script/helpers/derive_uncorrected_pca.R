# =============================================================================
# MSI Analysis Application - Derive uncorrected-PCA view
# Harmony RDS 内の未補正 pca 次元から UMAP を計算し、独立 RDS として保存する。
# インタラクティブ解析で「PCA」を Harmony/RPCA と同じ UMAP 形式で並べて比較表示するため
# （既存結果でも再解析せずに未補正PCAを確認できる）。
#
# Usage: Rscript derive_uncorrected_pca.R <src_rds> <out_rds>
#   src_rds : Harmony 等の RDS（list(obj=seu, reduction=...) 形式 / 素の Seurat も可）。
#             "pca" reduction を含む必要がある。
#   out_rds : 出力先（list(obj=seu, reduction="pca") を保存。umap は pca 由来に置換）。
# =============================================================================

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) {
  stop("Usage: Rscript derive_uncorrected_pca.R <src_rds> <out_rds>")
}
src_path <- args[1]
out_path <- args[2]

if (!file.exists(src_path)) {
  stop("Source RDS not found: ", src_path)
}

suppressPackageStartupMessages(library(Seurat))

# 共通 I/O ヘルパー (slim qs 形式 / 旧 saveRDS 形式の両対応) を読み込み
source(file.path(dirname(normalizePath(sub("^--file=", "",
        grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)[1]),
        mustWork = FALSE)), "rds_io.R"))

obj <- load_rds_compact(src_path)

# list(obj=seu, ...) ラップ形式なら Seurat を取り出す
if (is.list(obj) && !inherits(obj, "Seurat") && "obj" %in% names(obj)) {
  obj <- obj$obj
}
if (!inherits(obj, "Seurat")) {
  stop("Source RDS does not contain a Seurat object")
}
if (!("pca" %in% names(obj@reductions))) {
  stop("Source object has no 'pca' reduction; cannot derive uncorrected PCA view")
}

# 未補正 pca から UMAP を計算し、既存の (harmony 由来) umap を置換する。
#
# ★ ver58.0 (デバッグ総点検 A-2): **このスクリプトはクラスタを取り直さない**。
#   座標だけ無補正で作り直し、クラスタ (Idents) は元の (Harmony 等) を維持する。
#   これは「無補正の座標 ＋ 補正後のクラスタ」という混成であり、
#   『補正の有無でクラスタがどう変わるか』を見る目的には使えない。
#
#   本来あるべき「無補正空間でクラスタも決め直した結果」は、
#   **解析本体が併走出力するようになった**:
#     - TIMS: Step2_PCA_uncorrected.rds
#     - DESI: DESI_SeuratCombined_PCA_uncorrected.rds
#   本体側なら本解析と同じクラスタリング条件 (近傍数・解像度・アルゴリズム) を
#   使えるが、このスクリプトは独立プロセスで起動されるためそれらを知り得ない。
#   条件を勝手に決めて取り直すと「本解析と違う条件のクラスタ」を作ってしまう。
#
#   したがってここは**併走出力を持たない過去の結果を開くための後方互換**として残す。
#   画面には「クラスタは補正後の定義」と分かる注記が出る。
n_pc     <- ncol(Embeddings(obj, "pca"))
dims_use <- 1:min(30, n_pc)
obj <- RunUMAP(obj, reduction = "pca", dims = dims_use,
               reduction.name = "umap", seed.use = 42, verbose = FALSE)

dir.create(dirname(out_path), recursive = TRUE, showWarnings = FALSE)
save_rds_compact(list(obj = obj, reduction = "pca"), out_path)
cat("Derived uncorrected-PCA RDS saved:", out_path, "\n")
