# ============================================================
# TIMS-MSI 解析パイプライン (ver5_slim: ver4 を継承 / UMAPハイパラ明示化・診断連携の土台)
#
#   【ver5 での追加（挙動は ver4 と同一＝no-op。診断/注入/記録のための明示化）】
#   - UMAP/クラスタリングのハイパーパラメータを定数化（UI注入・analysis_params.json 記録を可能化）
#     UMAP表示用(n.neighbors/metric/min.dist/seed) と クラスタ用(k.param/annoy.metric/algorithm) を明示
#   - 既定値は Seurat 既定と同一（n.neighbors=30, metric="cosine", min.dist=0.3, k.param=20, euclidean）
#   - PIPELINE_STAGE 定数を予約（reduction_only / downstream_from_reduction は後続フェーズで実装）
#   ※ UMAPを実行する本スクリプトは ver4 を温存し、版を上げた新規ファイルとして作成している。
#
#   【ver4 から継承した方法論】
#
#   【主な修正・追加機能】
#   1. RDS保存: "RDS_Files" フォルダに各ステップ(Step1-3)で保存
#   2. Resume機能強化:
#      - 既存RDSがある場合: 読み込み -> 今回のフォルダにコピー -> スキップ
#      - ない/壊れている場合: エラー回避して再計算 -> 新規保存
#   3. マルチ出力: Harmony/RPCA/PCA すべての結果を独立して出力
#   4. TIC Overlay: 組織構造とクラスターの重ね合わせ可視化
#   5. Volcano Plot: ラベル被り抑制
#   6. HMDB形式CSV自動検出 (read_annotation_db_long)
#   7. 付加イオン +K 追加
#   8. Step1/2/3 の RDS を DietSeurat + qs で軽量保存 (-80% 前後)
#      旧 saveRDS 形式の .rds もそのまま読み込める (マジックバイト判定)
#
#   【ver4 での方法論的修正（既定の挙動が変わる点に注意）】
#   ① 過補正の防止: バッチ補正は技術的バッチ(BATCH_VAR='sample')に対してのみ実施。
#      単一sample（=切片が生物学的ROI/群）の場合は Harmony/RPCA をスキップし無補正PCAを使用。
#      （condition/slice_id による補正は ALLOW_CONDITION_CORRECTION=TRUE のときのみ許可）
#   ② 二重正規化の回避: INPUT_NORMALIZED=TRUE（SCiLS RMS等で正規化済み入力）なら
#      LogNormalize を行わず、NORM_MODE("none"/"sqrt"/"log1p") のみ適用する。
#   ③ マーカー表記の是正: markers_annotated.csv は「ピクセル単位の探索的ランキング」であり
#      群間の統計的推論ではない旨を、列(ranking_type/inference_note)と Volcano 副題で明記。
#   ④ 無補正PCAの併走出力: 補正を使った場合でも Step2_PCA_uncorrected を別途出力し、
#      補正の妥当性を比較可能にする（webアプリの手法一覧にも "PCA (uncorrected)" として表示）。
# ============================================================

if (!requireNamespace("tictoc", quietly = TRUE)) {
  install.packages("tictoc", repos = "https://cran.rstudio.com/")
}
if (!requireNamespace("ggrepel", quietly = TRUE)) {
  install.packages("ggrepel", repos = "https://cran.rstudio.com/")
}

suppressPackageStartupMessages({
  library(Seurat)
  library(tidyverse)
  library(data.table)
  library(tictoc)
  library(ggplot2)
  library(patchwork)
  library(Matrix)
  library(harmony)
  library(pheatmap)
  library(RColorBrewer)
  library(tools)
  library(grid)
  library(ggrepel)
  library(dbscan)
  # ggtext is optional
  if (requireNamespace("ggtext", quietly = TRUE)) library(ggtext)
})

# ---- パフォーマンス最適化: 並列化 & Leidenクラスタリング ----
if (!requireNamespace("future", quietly = TRUE)) install.packages("future", repos = "https://cran.rstudio.com/")
if (!requireNamespace("leiden", quietly = TRUE)) install.packages("leiden", repos = "https://cran.rstudio.com/")
library(future)
plan(sequential)  # workerはFindAllMarkers直前にのみ起動（メモリ節約）
options(future.globals.maxSize = 4 * 1024^3)  # 4GB制限
# RPCA(IntegrateLayers) のときだけ一時的に使う上限。実行時の plan は sequential（:68 既定）のため
# globals はワーカーへ複製されず in-process 参照＝上限を上げてもメモリは多重化しない。
# この一手の前後だけ適用し、finally で必ず上の 4GB（全域既定; FindAllMarkers の multisession 窓を守る）へ戻す。
RPCA_FGLOBALS_MAXSIZE <- 64 * 1024^3  # 64GB（>26.25GiB の globals を通すため）

`%||%` <- function(a,b) if (!is.null(a)) a else b

# ---- [ver45.7 計測] プロセス実使用量(RSS)の記録 ----
# これまでメモリを推測で議論してきたため、実測値をログに残す。RSS はプロセスが実際に確保して
# いる物理メモリで、cgroup(mem_limit) が見ているのもこれ。R ヒープ(gc)の値と違い、未返却領域や
# Arrow のプールも含むため実態に一致する。/proc/self/status を読むだけで依存追加なし。
.rss_gb <- function() {
  tryCatch({
    ln <- grep("^VmRSS:", readLines("/proc/self/status", warn = FALSE), value = TRUE)
    if (length(ln) == 0) return(NA_real_)
    as.numeric(sub("^VmRSS:\\s*([0-9]+)\\s*kB.*$", "\\1", ln[1])) / 1024^2
  }, error = function(e) NA_real_)
}
.mem_note_base <- function(tag) {
  cat(sprintf("[mem] %s: RSS %.2f GB\n", tag, .rss_gb()))
  flush(stdout())
}

# ---- 共通 RDS I/O ヘルパーの読み込み ----
# scale.data を落とした DietSeurat + qs 圧縮で Step1/2/3 RDS を軽量化する。
# 旧形式 (.rds = saveRDS 出力) もマジックバイト判定で透過的に読める。
local({
  helper_path <- NULL
  # 1) 環境変数 R_HELPERS_DIR が与えられていれば最優先
  env_dir <- Sys.getenv("R_HELPERS_DIR", unset = NA)
  if (!is.na(env_dir) && nzchar(env_dir)) {
    cand <- file.path(env_dir, "rds_io.R")
    if (file.exists(cand)) helper_path <- cand
  }
  # 2) 自身のスクリプト位置から ../helpers/rds_io.R を探索
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

# ============================================================
# ==== 【1】 毎回変更する可能性がある設定 (Data & Grid) ======
# ============================================================

# ---- 1. 入出力パス設定 ----
# 解析したいCSVファイルのパス (複数可)
INPUT_PATHS <- c(
  "C:\\Users\\Cciia\\Biochem Dropbox\\Biochem's shared workspace\\Workspace\\UMAP\\TIMS\\Data\\test-250809_Kizu_H2-18O_Brain_Transform\\test-250809_Kizu_H2-18O_Brain_Transform.parquet"
)

# 結果を出力するメインフォルダ
OUTPUT_DIR <- "C:\\Users\\Cciia\\Biochem Dropbox\\Biochem's shared workspace\\Workspace\\UMAP\\TIMS\\Data\\test-250809_Kizu_H2-18O_Brain_Transform"

# プロジェクト名
PROJECT_LABEL <- "Annotation-test1_" 

# ---- 途中解析 (Resume) 機能の設定 ----
RESUME_FROM_RDS <- FALSE
# ※ RDS保存先は自動的に OUTPUT_DIR/プロジェクト名/RDS_Files になります
# ※ 読み込み元を指定したい場合は以下にパスを記述（空欄なら保存先と同じ場所を探します）
#このRでは解析の段階によって途中セーブような機能を持っています(.rdsファイルの出力)
#　　→このファイルを使えば過去に行ったデータの読み込みやHarmony、RCPAの結果を飛ばしてそのあとの解析から進められます。
#    →さらに、「TRUE」でも既にあるファイルは使用し、なければ新規で作成して使用する方式を採用しています。なので、2回目以降はTRUE機能をご利用ください。木津
RESUME_DIR_PATH <- "C:\\Users\\Cciia\\Biochem Dropbox\\木津亮馬\\MSI_Tims\\SCiLS_Transform\\2025\\251230_Ishitani_Kilifish_Transform\\251230_Ishitani_Kilifish_POS2__20260117\\RDS_Files" 

# ---- 1b. CSV読み込みキャッシュ ----
RDS_CACHE_ENABLE <- TRUE          
RDS_CACHE_FORCE_REBUILD <- FALSE  
RDS_CACHE_DIR <- file.path(OUTPUT_DIR, "_csv_rds_cache") 

# ---- 2. slice_id / condition 設定 ----
# ここでは **annotation 名をそのまま slice_id として使用**します（DBSCAN/グリッド分割は使いません）。
# したがって GRID_NX / GRID_NY は不要です。
#
# SLICE_CONDITION_MAP は、slice_id (= annotation) を condition (群ラベル) に対応付けます。
# 例：H2[18]O_Brain_annotation.csv / Ctrl_Brain_annotation.csv を使う場合
SLICE_CONDITION_MAP <- tibble::tibble(
  slice_id = character(),
  condition = character()
)

# NOTE: 本スクリプトでは condition は slice_id と同一に設定します（annotation名をそのまま使用）。
# もし表示名を変更したい場合のみ、上のSLICE_CONDITION_MAPに (slice_id -> condition) の対応表を記入してください。
# ---- Annotation Filter（切片選択） ----
# NULL = 全annotation使用 / c("Brain", "Heart") = 指定のみ使用
ANNOTATION_FILTER <- NULL

# ============================================================
# ==== ver4 追加設定: バッチ補正ポリシー / 正規化ポリシー ====
# ============================================================
# 【背景】本パイプラインの目的は「切片(slice_id)＝生物学的構造/群」を UMAP で観察すること。
# Harmony/RPCA を condition(=slice_id) に対して掛けると、見たい生物差まで除去される(過補正)。
# ver4 では「技術的バッチに対してのみ補正」を既定とし、生物差は温存する。

# --- 補正対象の指定 ---
# BATCH_VAR: 補正に使うメタデータ列（技術的バッチ＝測定ロット等を指す列名）。
#   既定 "sample"（複数 sample がある場合のみ補正される）。
BATCH_VAR <- "sample"

# ANNOTATION_ROLE: annotation(=slice_id) が何を表すか。
#   "biological"     : ROI/組織/群など生物学的区分（既定）→ slice 単位の補正はしない
#   "section_id"     : 同一試料の連続切片＝技術反復 → 単一sampleでも RPCA を slice 単位で許可
#   "technical_batch": annotation 自体が技術バッチ
ANNOTATION_ROLE <- "biological"

# ALLOW_CONDITION_CORRECTION: TRUE で condition/slice_id による補正を明示的に許可（非推奨）。
ALLOW_CONDITION_CORRECTION <- FALSE

# --- 入力正規化ポリシー ---
# INPUT_NORMALIZED: 入力が既に SCiLS の RMS 等で正規化済みなら TRUE。
#   TRUE のとき LogNormalize(=NormalizeData) を行わず、二重正規化を回避する。
INPUT_NORMALIZED <- FALSE
# NORM_MODE: INPUT_NORMALIZED=TRUE のときに適用する変換。"none" / "sqrt" / "log1p"
NORM_MODE <- "log1p"

# --- 無補正PCAの併走出力 ---
# TRUE のとき、補正(Harmony等)を使った場合でも無補正PCAを別途出力し、補正の妥当性を比較可能にする。
ALWAYS_OUTPUT_UNCORRECTED_PCA <- TRUE

# --- Step3 RPCA(IntegrateLayers) の実行可否 ---
# RPCA は「技術的バッチ」の補正用で、Harmony/PCA の結果だけで足りる場面も多い。
# メモリ上限の厳しい環境（mem_limit 12g 等）で Step3 が OOM で落ちる場合は FALSE にすると、
# Harmony/無補正PCA まで完走して正常終了する。環境変数 RUN_RPCA=0/1 でも上書き可。
ENABLE_RPCA <- TRUE
local({
  .e <- Sys.getenv("RUN_RPCA", unset = "")
  if (nzchar(.e)) ENABLE_RPCA <<- !(.e %in% c("0", "false", "FALSE", "no", "NO"))
})

# ============================================================
# ==== 【2】 解析の前提条件 (Annotation & Statistics) ========
# ============================================================

# ---- アノテーション設定（TraceFinder DB Export を使用）----
ANNOTATION_ENABLE <- TRUE

# TraceFinder Compound Database Export (CSV)
# 例: "4500_endogenous_metabolites_mod_1.csv"
ANNOTATION_CSV_PATH <- "C:\\Users\\Cciia\\Biochem Dropbox\\木津亮馬\\MSI_Tims\\SCiLS_Transform\\DB\\4500_endogenous_metabolites_mod.csv"

# 極性（TraceFinder の Polarity 列が '+' / '-' の前提）
ION_MODE <- "Negative"   # "Positive" or "Negative"


# --- Annotation adduct filter (user-configurable) ---
# * POLARITY: Annotation will ALWAYS use ION_MODE (measurement polarity).
# * ADDUCT filter: specify which adduct(s) in the Excel/TraceFinder DB to use for annotation.
#   - If empty (character(0)), do NOT filter by adduct (use all adducts with the selected polarity).
#   - Typical examples: c("+H", "+Na", "+NH4") for Positive; c("-H") for Negative
#   - Matching rule: substring match (grepl, fixed=TRUE) against the DB "Adduct" column.
#     (So "+H" matches "[M+H]+", "M+H", etc.)
# Default adducts by polarity:
#   - Positive: +H, +Na, +NH4
#   - Negative: -H
ANNOT_ADDUCT_PATTERNS <- if (ION_MODE == "Positive") {
  c("+H", "+Na", "+NH4", "+K")
} else if (ION_MODE == "Negative") {
  c("-H")
} else {
  stop("ION_MODE must be 'Positive' or 'Negative'.")
}

.get_adduct_override <- function(current_patterns) {
  pats <- current_patterns
  # env: comma-separated, e.g. ANNOT_ADDUCTS=+H,+Na
  env_ad <- Sys.getenv("ANNOT_ADDUCTS", unset = "")
  if (nzchar(env_ad)) {
    pats2 <- trimws(unlist(strsplit(env_ad, ",")))
    pats2 <- pats2[nzchar(pats2)]
    pats <- pats2
  }
  # args: --adducts=+H,+Na  or adducts=+H,+Na
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) > 0) {
    hit <- args[grepl("(^--adducts=|^adducts=)", args)]
    if (length(hit) > 0) {
      s <- sub("(^--adducts=|^adducts=)", "", hit[1])
      pats2 <- trimws(unlist(strsplit(s, ",")))
      pats2 <- pats2[nzchar(pats2)]
      pats <- pats2
    }
  }
  pats
}

ANNOT_ADDUCT_PATTERNS <- .get_adduct_override(ANNOT_ADDUCT_PATTERNS)
rm(.get_adduct_override)


# 反映する m/z 許容誤差（m/z 直接照合）
# - ここを書き換えるのが一番シンプル（例:test sampleで実行した際にはm/zが 以上ずれていると、アノテーションが付かなかった）
DEFAULT_TOLERANCE_MZ <- 0.01
TOLERANCE_MZ <- DEFAULT_TOLERANCE_MZ

# ---- m/z キャリブレーション設定 ----
# Python (numpy.polyfit) が計算した多項式係数を注入する
# 係数は降順: c(c_n, c_{n-1}, ..., c_0)
# 補正: corrected_mz = mz - polyval(coefficients, mz)
CALIBRATION_ENABLE <- FALSE
CALIBRATION_COEFFICIENTS <- c(0)
CALIBRATION_BY_SAMPLE <- list()

# ---- m/z アライメント設定 ----
# 複数サンプル間のm/z値を統一する際のppm許容誤差
# 0 = 無効（従来動作: 列名の完全一致のみ）
MZ_ALIGN_PPM <- 0

# --- optional override from env / args ---
.get_mz_override <- function(current_mz) {
  mz <- current_mz 

  # env
  env_mz <- Sys.getenv("ANNOT_MZ", unset = "")
  if (nzchar(env_mz)) {
    v <- suppressWarnings(as.numeric(env_mz))
    if (!is.na(v) && v > 0) mz <- v
  }

  # args (both `--mz=0.01` and `mz=0.01`)
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) > 0) {
    hit <- args[grepl("(^--mz=|^mz=)", args)]
    if (length(hit) > 0) {
      v <- suppressWarnings(as.numeric(sub("(^--mz=|^mz=)", "", hit[1])))
      if (!is.na(v) && v > 0) mz <- v
    }
  }

  # interactive prompt (opt-in): set ASK_MZ=1
  if (interactive() && Sys.getenv("ASK_MZ", unset="0") == "1") {
    ans <- readline(sprintf("m/z tolerance? (current %.6f): ", mz))
    if (nzchar(ans)) {
      v <- suppressWarnings(as.numeric(ans))
      if (!is.na(v) && v > 0) mz <- v
    }
  }

  mz
}
TOLERANCE_MZ <- .get_mz_override(TOLERANCE_MZ)
rm(.get_mz_override)

# ---- m/z キャリブレーション補正関数 ----
calibrate_mz <- function(mz_values,
                         coefficients = CALIBRATION_COEFFICIENTS,
                         enable = CALIBRATION_ENABLE) {
  if (!isTRUE(enable) || length(coefficients) < 1) return(mz_values)
  # 多項式評価: correction = c_n * mz^n + c_{n-1} * mz^(n-1) + ... + c_0
  degree <- length(coefficients) - 1
  correction <- rep(0, length(mz_values))
  for (i in seq_along(coefficients)) {
    correction <- correction + coefficients[i] * mz_values^(degree - i + 1)
  }
  mz_values - correction
}


# ---- 統計解析・ボルケーノプロット設定 ----

DEG_P_THRESH_VAL   <- 0.05      
DEG_LOGFC_TH_VAL   <- 0.25
DEG_MIN_PCT_VAL    <- 0.05      
LABEL_TOP_N_EACH   <- 5         
VOLCANO_Y_CAP      <- 100       

# ---- TIC Overlay 設定 (Template v8準拠) ----
# UMAPとTIC(全イオン電流)を重ね合わせる機能の設定
OUTPUT_TIC_ONLY    <- TRUE
OUTPUT_TIC_OVERLAY <- TRUE
TIC_OVERLAY_STYLE  <- "tile" # "tile" (塗りつぶし) or "outline" (輪郭)
TIC_Q_LOW  <- 0.02
TIC_Q_HIGH <- 0.995
TIC_TRANS  <- "identity"
TIC_GRAY_LOW  <- "white"
TIC_GRAY_HIGH <- "black"

# ============================================================
# ==== 【3】 詳細パラメータ (通常は変更不要) ================
# ============================================================

SPATIAL_SMOOTH_ENABLE <- FALSE 
SPATIAL_SMOOTH_RADIUS <- 0.1  
SPATIAL_SMOOTH_SIGMA  <- 0.05 

GLOBAL_RANDOM_SEED <- 42      
MAX_PCS            <- 30
UMAP_DIMS_MAX      <- 30
UMAP_DIMS_N        <- 30L     # PreFlight: UI から dims を注入(_hp_int umap_dims_n→UMAP_DIMS_N)。既定30は現状と同一挙動(下の override は !=30 のときだけ発火)
CLUSTER_RESOLUTION <- 0.5
MIN_CELLS_RPCA     <- 50
N_VAR_FEATURES     <- 3000

# [ver5] UMAP/クラスタリングのハイパーパラメータ明示化（UI注入・記録を可能化。既定値=ver4と同一挙動=no-op）
#   UMAP表示用(n.neighbors/metric/min.dist) と クラスタ用(k.param/annoy.metric) を明示。seedは GLOBAL_RANDOM_SEED を流用。
UMAP_N_NEIGHBORS  <- 30L          # Seurat RunUMAP 既定
UMAP_MIN_DIST     <- 0.3          # Seurat RunUMAP 既定
UMAP_METRIC       <- "cosine"     # Seurat RunUMAP 既定
CLUSTER_K_PARAM   <- 20L          # Seurat FindNeighbors 既定
CLUSTER_METRIC    <- "euclidean"  # Seurat FindNeighbors 既定
CLUSTER_ALGORITHM <- 4L           # Leiden（従来 algorithm = 4）
# PIPELINE_STAGE: "full"(従来) / "reduction_only" / "downstream_from_reduction"（stage制御は後続フェーズで実装）
PIPELINE_STAGE    <- "full"

# リトライ設定
HARMONY_RETRY_GRID <- list(
  list(n_var_features = 3000, max_pcs = 30, umap_dims = 30),
  list(n_var_features = 1000, max_pcs = 20, umap_dims = 20),
  list(n_var_features = 500,  max_pcs = 15, umap_dims = 15)
)

PCA_RETRY_GRID <- list(
  list(n_var_features = 1000, max_pcs = 20, umap_dims = 20),
  list(n_var_features = 500,  max_pcs = 15, umap_dims = 15)
)

RPCA_NFEATURES_TRY <- c(500, 300, 200)

# PreFlight: UI から dims が指定された場合のみ TIMS の UMAP 次元をその値に合わせる。
# 既定(30)は override せず従来のグリッド(30→20→15)のまま＝挙動不変（後方互換）。
# アプリは umap_dims_n→UMAP_DIMS_N を常に注入する設計のため、!=30 のときだけ反映する。
if (is.numeric(UMAP_DIMS_N) && UMAP_DIMS_N > 0L && UMAP_DIMS_N != 30L) {
  .ud <- as.integer(UMAP_DIMS_N)
  UMAP_DIMS_MAX <- .ud                         # RPCA(Step3)・run_downstream_analysis が参照
  MAX_PCS       <- max(MAX_PCS, .ud)           # PCA が .ud 次元を確保できるように
  .apply_ud <- function(g) {
    g[[1]]$max_pcs   <- max(g[[1]]$max_pcs, .ud)   # 先頭(優先)エントリを .ud 次元に
    g[[1]]$umap_dims <- .ud
    if (length(g) > 1L) for (i in 2:length(g)) g[[i]]$umap_dims <- min(g[[i]]$umap_dims, .ud)  # 小データ用フォールバックは .ud 上限
    g
  }
  HARMONY_RETRY_GRID <- .apply_ud(HARMONY_RETRY_GRID)
  PCA_RETRY_GRID     <- .apply_ud(PCA_RETRY_GRID)
  message(sprintf(">> PreFlight: UMAP_DIMS_N=%d を TIMS の dims に適用（UMAP_DIMS_MAX/リトライグリッドを上書き）", .ud))
}

FAILSAFE_ENABLE <- TRUE

HEATMAP_TOPN_PER_CLUSTER      <- 5
SPATIAL_BASE_HEIGHT  <- 7
SPATIAL_LABEL_SIZE   <- 3

ADDUCTS <- if(tolower(ION_MODE) == "positive") {
  list("[M+H]+" = 1.007276, "[M+Na]+" = 22.989769, "[M+K]+" = 38.963706)
} else {
  list("[M-H]-" = -1.007276, "[M+Cl]-" = 34.968853)
}

# カラーパレット
my_colors <- c("#E41A1C", "#377EB8", "#4DAF4A", "#984EA3", "#FF7F00", "#FFFF33", "#A65628", "#F781BF", "#00CED1", "#1F78B4", "#B2DF8A", "#33A02C")
get_palette <- function(n) { if (n <= length(my_colors)) my_colors[seq_len(n)] else { extra <- n - length(my_colors); hues <- seq(0, 360, length.out = extra + 1)[-1]; c(my_colors, grDevices::hcl(h = hues, c = 70, l = 60)) } }
CLUSTER_LEVELS <- NULL; CLUSTER_PAL <- NULL
ensure_global_palette <- function(obj){ if (is.null(CLUSTER_LEVELS)) { lv <- levels(Idents(obj)); assign("CLUSTER_LEVELS", lv, envir = .GlobalEnv); assign("CLUSTER_PAL", setNames(get_palette(length(lv)), lv), envir = .GlobalEnv) } }

# ============================================================
# ==== ヘルパー関数定義 ====================================
# ============================================================

.grid_step <- function(v) {
  vu <- sort(unique(v)); if (length(vu) < 2) return(1)
  d <- diff(vu); min(d[d > 0], na.rm = TRUE)
}

# 1. MSI画像描画 (Top5用 - シンプル版)
plot_msi_tile <- function(obj, feature, title = NULL) {
  # Top5 MSI 用: 「背景=TIC（nCount_Spatial）」の上に feature 強度を半透明合成して表示
  # - ggplot の fill は 1系統しか扱えないため、TIC(灰色) と feature(viridis) を
  #   RGB 合成した色を各ピクセルに割り当て、scale_fill_identity() で描画する。
  df <- FetchData(obj, vars = c("x_coord", "y_coord", "nCount_Spatial", feature))
  colnames(df) <- c("x", "y", "tic", "val")
  dx <- .grid_step(df$x); dy <- .grid_step(df$y)
  if (is.null(title)) title <- feature

  # ---- normalize TIC to [0,1] using quantiles (Template v8 settings) ----
  tv <- df$tic
  ql <- if (exists("TIC_Q_LOW", envir = .GlobalEnv)) get("TIC_Q_LOW", envir = .GlobalEnv) else 0.02
  qh <- if (exists("TIC_Q_HIGH", envir = .GlobalEnv)) get("TIC_Q_HIGH", envir = .GlobalEnv) else 0.995
  vmin <- suppressWarnings(stats::quantile(tv, ql, na.rm = TRUE))
  vmax <- suppressWarnings(stats::quantile(tv, qh, na.rm = TRUE))
  if (!is.finite(vmin)) vmin <- suppressWarnings(min(tv, na.rm = TRUE))
  if (!is.finite(vmax) || vmax <= vmin) vmax <- suppressWarnings(max(tv, na.rm = TRUE))

  tic01 <- (tv - vmin) / (vmax - vmin)
  tic01[!is.finite(tic01)] <- 0
  tic01 <- pmin(1, pmax(0, tic01))

  # ---- normalize feature to [0,1] (robust) ----
  vv <- df$val
  fmin <- suppressWarnings(stats::quantile(vv, 0.01, na.rm = TRUE))
  fmax <- suppressWarnings(stats::quantile(vv, 0.99, na.rm = TRUE))
  if (!is.finite(fmin)) fmin <- suppressWarnings(min(vv, na.rm = TRUE))
  if (!is.finite(fmax) || fmax <= fmin) fmax <- suppressWarnings(max(vv, na.rm = TRUE))

  val01 <- (vv - fmin) / (fmax - fmin)
  val01[!is.finite(val01)] <- 0
  val01 <- pmin(1, pmax(0, val01))

  # ---- map colors: TIC -> grayscale, feature -> viridis(plasma) ----
  # TIC: low=white, high=black
  g <- as.integer(round(255 * (1 - tic01)))
  bg_rgb <- cbind(g, g, g)

  # feature palette (plasma)
  pal <- viridisLite::viridis(256, option = "plasma")
  idx <- as.integer(round(val01 * 255)) + 1L
  idx[idx < 1L] <- 1L; idx[idx > 256L] <- 256L
  fg_hex <- pal[idx]
  fg_rgb <- grDevices::col2rgb(fg_hex)

  # ---- blend: TIC is monochrome; feature overlays with alpha proportional to intensity ----
  alpha_max <- 0.90
  alpha <- alpha_max * (val01^0.80)
  alpha[!is.finite(alpha)] <- 0
  alpha <- pmin(1, pmax(0, alpha))
  out_rgb <- round((1 - alpha) * bg_rgb + alpha * t(fg_rgb))
  out_rgb[out_rgb < 0] <- 0
  out_rgb[out_rgb > 255] <- 255
  df$fill <- grDevices::rgb(out_rgb[,1], out_rgb[,2], out_rgb[,3], maxColorValue = 255)

  ggplot(df, aes(x = x, y = y, fill = fill)) +
    geom_tile(width = dx, height = dy) +
    scale_fill_identity() +
    scale_y_reverse() + coord_fixed() + theme_void() +
    ggtitle(title) +
    theme(
      plot.title = element_text(hjust = 0.5, size = 8, face = "bold"),
      legend.position = "none",
      # png 保存時の背景（余白）も白に固定
      plot.background  = element_rect(fill = "white", color = NA),
      panel.background = element_rect(fill = "white", color = NA)
    )
}

# 2. アノテーション（TraceFinder DB Export: ExtractedMass を直接 ppm マッチ）
#   - CSV は "TraceFinder Compound Database Export" を想定（先頭2行がメタ/グループ行）
#   - 3行目がヘッダ行（skip=2 で fread）
#   - ExtractedMass / Adduct / Polarity が Peak1..4 の各ブロックに存在（同名 + .1/.2/.3 のsuffix）
read_tracefinder_db_long <- function(csv_path) {
  # TraceFinder Compound Database Export (CSV) -> long table
  # - Export often has repeated column blocks for multiple adduct/polarity entries.
  # - In some exports, repeated blocks keep the SAME column names (no .1/.2 suffix).
  #   So we detect blocks by COLUMN POSITION (nth occurrence), not only by name suffix.
  if (is.null(csv_path) || !file.exists(csv_path)) return(NULL)

  dt <- data.table::fread(csv_path, skip = 2, fill = TRUE, data.table = TRUE)
  if (!("CompoundName" %in% names(dt))) return(NULL)

  # Locate repeated blocks by index (1st, 2nd, 3rd, ...)
  idx_mass <- which(names(dt) == "ExtractedMass")
  idx_add  <- which(names(dt) == "Adduct")
  idx_pol  <- which(names(dt) == "Polarity")
  if (length(idx_mass) == 0 || length(idx_add) == 0 || length(idx_pol) == 0) return(NULL)

  n_blocks <- min(length(idx_mass), length(idx_add), length(idx_pol))
  if (n_blocks <= 0) return(NULL)

  blocks <- vector("list", n_blocks)
  for (k in seq_len(n_blocks)) {
    mass_i <- idx_mass[k]; add_i <- idx_add[k]; pol_i <- idx_pol[k]
    tmp <- dt[, .(
      CompoundName  = as.character(CompoundName),
      ExtractedMass = suppressWarnings(as.numeric(dt[[mass_i]])),
      Adduct        = as.character(dt[[add_i]]),
      Polarity      = as.character(dt[[pol_i]])
    )]
    blocks[[k]] <- tmp
  }

  db <- data.table::rbindlist(blocks, use.names = TRUE, fill = TRUE)

  # Normalize Polarity to '+' / '-' whenever possible
  pol_raw <- trimws(toupper(as.character(db$Polarity)))
  pol_norm <- ifelse(pol_raw %in% c("+","POS","P","POSITIVE"), "+",
              ifelse(pol_raw %in% c("-","NEG","N","NEGATIVE"), "-", NA_character_))
  pol_norm2 <- ifelse(is.na(pol_norm) & nzchar(trimws(as.character(db$Polarity))),
                      trimws(as.character(db$Polarity)), pol_norm)
  db$Polarity <- pol_norm2

  # Basic cleanup
  # [P11] フィルタ一括化（4行→1行）
  db <- db[!is.na(ExtractedMass) & is.finite(ExtractedMass) &
           nzchar(trimws(CompoundName)) & nzchar(trimws(Adduct)) & nzchar(trimws(Polarity))]
  db
}


# ---------- HMDB charge-aware CSV → long format ----------
read_hmdb_db_long <- function(csv_path) {
  # HMDB charge-aware CSV → long format (CompoundName, ExtractedMass, Adduct, Polarity)
  if (is.null(csv_path) || !file.exists(csv_path)) return(NULL)

  dt <- data.table::fread(csv_path, data.table = TRUE)

  # 化合物名カラム検出
  name_col <- intersect(names(dt), c("name (accession)", "name", "Name", "CompoundName"))
  if (length(name_col) == 0) return(NULL)
  name_col <- name_col[1]

  # HMDB accession ID を除去: "Compound (HMDB0000001)" -> "Compound"
  compound_names <- sub("\\s*\\(HMDB\\d+\\)\\s*$", "", as.character(dt[[name_col]]))

  # 付加イオンカラム → (Adduct文字列, Polarity) のマッピング
  adduct_map <- list(
    "[M+H]+"  = list(adduct = "M+H",  pol = "+"),
    "[M+Na]+" = list(adduct = "M+Na", pol = "+"),
    "[M+K]+"  = list(adduct = "M+K",  pol = "+"),
    "[M+NH4]+"= list(adduct = "M+NH4",pol = "+"),
    "[M]+"    = list(adduct = "M+",   pol = "+"),
    "[M-H]-"  = list(adduct = "M-H",  pol = "-"),
    "[M]-"    = list(adduct = "M-",   pol = "-")
  )

  blocks <- list()
  for (col_name in names(adduct_map)) {
    if (!(col_name %in% names(dt))) next
    info <- adduct_map[[col_name]]
    mz_vals <- suppressWarnings(as.numeric(dt[[col_name]]))
    tmp <- data.table::data.table(
      CompoundName  = compound_names,
      ExtractedMass = mz_vals,
      Adduct        = info$adduct,
      Polarity      = info$pol
    )
    tmp <- tmp[!is.na(ExtractedMass) & is.finite(ExtractedMass) & ExtractedMass > 0]
    tmp <- tmp[nzchar(trimws(CompoundName))]
    blocks <- c(blocks, list(tmp))
  }

  if (length(blocks) == 0) return(NULL)
  data.table::rbindlist(blocks, use.names = TRUE)
}


# ---------- 自動検出ラッパー ----------
read_annotation_db_long <- function(csv_path) {
  # 1行目を読んで形式を自動判定
  if (is.null(csv_path) || !file.exists(csv_path)) return(NULL)

  first_line <- readLines(csv_path, n = 1, warn = FALSE)

  if (grepl("TraceFinder", first_line, ignore.case = TRUE)) {
    message("[Annotation] TraceFinder format detected.")
    return(read_tracefinder_db_long(csv_path))
  }

  if (grepl("name.*accession|\\[M\\+H\\]\\+|\\[M-H\\]-|formula_mass", first_line, ignore.case = TRUE)) {
    message("[Annotation] HMDB format detected.")
    return(read_hmdb_db_long(csv_path))
  }

  # フォールバック: TraceFinder を試す
  message("[Annotation] Unknown format, trying TraceFinder parser.")
  read_tracefinder_db_long(csv_path)
}


annotate_mz_with_format <- function(mz_vec, db_long, tol_mz,
                                   ion_mode = c("Positive","Negative"),
                                   adduct_patterns = NULL) {
  if (is.null(db_long) || length(mz_vec) == 0) return(mz_vec)
  ion_mode <- match.arg(ion_mode)

  pol_need <- if (tolower(ion_mode) == "positive") "+" else "-"

  # Normalize DB polarity in-scope (accepts '+', '-', 'POS/NEG', 'Positive/Negative')
  pol_raw_db <- trimws(toupper(as.character(db_long$Polarity)))
  pol_db_norm <- ifelse(pol_raw_db %in% c("+","POS","P","POSITIVE"), "+",
                 ifelse(pol_raw_db %in% c("-","NEG","N","NEGATIVE"), "-", as.character(db_long$Polarity)))
  db_long$Polarity <- pol_db_norm

  db_use <- db_long[db_long$Polarity == pol_need, , drop = FALSE]

  # Optional: filter by adduct patterns (substring match on DB 'Adduct')
  if (!is.null(adduct_patterns) && length(adduct_patterns) > 0) {
    pats <- trimws(as.character(adduct_patterns))
    pats <- pats[nzchar(pats)]
    if (length(pats) > 0 && nrow(db_use) > 0) {
      keep <- rep(FALSE, nrow(db_use))
      for (p in pats) keep <- keep | grepl(p, db_use$Adduct, fixed = TRUE)
      db_use <- db_use[keep, , drop = FALSE]
    }
  }

  # Expose db_use for post-run debugging
  assign("db_use", db_use, envir = .GlobalEnv)

  if (nrow(db_use) == 0) return(mz_vec)

  out <- sapply(mz_vec, function(target_mz_str) {
    val <- .feature_mz(target_mz_str)
    # キャリブレーション補正を annotation マッチング前に適用
    if (isTRUE(CALIBRATION_ENABLE) && !is.na(val)) {
      val <- calibrate_mz(val, CALIBRATION_COEFFICIENTS)
    }
    mz_display <- if (!is.na(val)) as.character(round(val, 3)) else target_mz_str
    if (is.na(val)) return(target_mz_str)

    mz_diff <- abs(db_use$ExtractedMass - val)
    hit <- which(mz_diff <= tol_mz)
    if (length(hit) == 0) return(mz_display)

    best <- hit[which.min(mz_diff[hit])]
    paste0(db_use$CompoundName[best], " ", db_use$Adduct[best], "_", mz_display)
  }, USE.NAMES = TRUE)

  out
}



sanitize_dimnames <- function(mat, prefix_row = "mz-", prefix_col = "Spot-") {
  rn <- rownames(mat); if (is.null(rn)) rn <- rep(NA_character_, nrow(mat))
  bad_r <- is.na(rn) | rn == "" | duplicated(replace(rn, is.na(rn), ""))
  if (any(bad_r)) rn[bad_r] <- paste0(prefix_row, seq_len(sum(bad_r)))
  rn <- make.unique(rn)
  cn <- colnames(mat); if (is.null(cn)) cn <- rep(NA_character_, ncol(mat))
  bad_c <- is.na(cn) | cn == "" | duplicated(replace(cn, is.na(cn), ""))
  if (any(bad_c)) cn[bad_c] <- paste0(prefix_col, seq_len(sum(bad_c)))
  cn <- make.unique(cn)
  rownames(mat) <- rn; colnames(mat) <- cn; mat
}

# ---- 特徴量名から m/z を頑健に取得（"m/z 123.45" / "化合物名_123.45 | ..." 両対応）----
.feature_mz <- function(x) {
  x <- as.character(x)
  out <- rep(NA_real_, length(x))
  is_leg <- grepl("^m/z ", x)
  out[is_leg] <- suppressWarnings(as.numeric(sub("^m/z\\s+", "", x[is_leg])))
  rest <- which(is.na(out))
  if (length(rest) > 0) {
    head_tok <- trimws(sub("\\s*\\|.*$", "", x[rest]))   # " | " 以降を除去
    out[rest] <- suppressWarnings(as.numeric(sub("^.*_([0-9]+\\.?[0-9]*)$", "\\1", head_tok)))
  }
  out
}

# ---- 注釈付き列名 "<化合物名>_<mz> | DB | adduct | k=v ..." を per-feature テーブルに分解 ----
#  raw 全文は必ず保持し、将来機能から参照可能にする（パース不能フィールドは NA）。
.parse_feature_annotations <- function(raw, feature, mz, compound = NA_character_) {
  raw <- as.character(raw)
  ex <- function(pat) vapply(raw, function(s) {
    m <- regmatches(s, regexpr(pat, s, perl = TRUE)); if (length(m) > 0) m[1] else NA_character_
  }, character(1), USE.NAMES = FALSE)
  exkv <- function(key) vapply(raw, function(s) {
    m <- regmatches(s, regexpr(paste0(key, "=[^|]*"), s, perl = TRUE))
    if (length(m) > 0) trimws(sub(paste0("^", key, "="), "", m[1])) else NA_character_
  }, character(1), USE.NAMES = FALSE)
  data.frame(
    feature       = feature,
    compound      = compound,
    mz            = mz,
    adduct        = ex("\\[[^]]*\\][+-]?"),
    ppm           = ex("[0-9.]+\\s*ppm"),
    formula       = exkv("formula"),
    smiles        = exkv("SMILES"),
    adduct_family = exkv("adduct_family"),
    raw           = raw,
    stringsAsFactors = FALSE
  )
}

read_desi_data <- function(file_path, sample_prefix = NULL) {
  ext <- tolower(tools::file_ext(file_path))

  # -------------------------------
  # Case 1) Parquet (analysis-friendly wide table)
  #   columns: id, x, y, mz_....
  # -------------------------------
  if (ext %in% c("parquet", "pq")) {
    if (!requireNamespace("arrow", quietly = TRUE)) {
      stop("Package 'arrow' is required to read Parquet. Please install.packages('arrow').")
    }
    cat("Reading Parquet (arrow):", file_path, "
")
    # [P4] スキーマから列名取得→必要列のみ読込（メモリ節約）
    pf <- arrow::ParquetFileReader$create(file_path)
    all_names <- pf$GetSchema()$names
    mz_cols <- grep("^mz_", all_names, value = TRUE)
    is_bare_numeric <- FALSE
    is_annotated <- FALSE
    if (length(mz_cols) == 0) {
      non_meta <- setdiff(all_names, c("id", "x", "y", "annotation"))
      bare_num <- non_meta[!is.na(suppressWarnings(as.numeric(non_meta)))]
      if (length(bare_num) > 0) {
        mz_cols <- bare_num
        is_bare_numeric <- TRUE
        cat("  [Info] Bare numeric column names detected, treating as m/z values\n")
      } else if (length(non_meta) > 0) {
        # 注釈付き列名 "<化合物名>_<mz> | DB | adduct | ..." 形式。
        # "| より前" の末尾 _<数値> が m/z として取れる列のみ特徴量として採用。
        .head <- trimws(sub("\\s*\\|.*$", "", non_meta))
        .mz   <- suppressWarnings(as.numeric(sub("^.*_([0-9]+\\.?[0-9]*)$", "\\1", .head)))
        if (any(!is.na(.mz))) {
          mz_cols <- non_meta[!is.na(.mz)]
          is_annotated <- TRUE
          cat(sprintf("  [Info] Annotated column names detected (compound_m/z | ...): %d features\n",
                      length(mz_cols)))
        }
      }
    }
    # [ver45.5 メモリ根本対策] 旧実装は全表を密 data.frame 化したうえ as.matrix / 論理行列 /
    # t() / dgCMatrix と密コピーを 4〜5 個同時に抱え、取り込みだけでデータ実体の数倍を要した
    # （10万px 級で 12GB コンテナを超過）。ここではメタ列だけ先に読み、強度は m/z 列を
    # ブロック単位で読んで逐次スパース化して積む。全体の密行列・論理行列・t() を作らない。
    meta_cols <- intersect(c("id", "x", "y", "annotation"), all_names)
    df <- arrow::read_parquet(file_path, col_select = dplyr::all_of(meta_cols), as_data_frame = TRUE)

    miss <- setdiff(c("id", "x", "y"), colnames(df))
    if (length(miss) > 0) stop("Parquet is missing required columns: ", paste(miss, collapse = ", "))
    if (length(mz_cols) == 0) stop("No mz_ columns found in Parquet: ", file_path)

    # Build feature names + m/z（注釈付きは「化合物名_m/z」を表示名に採用し、|以降のメタは保持）
    feature_annotations <- NULL
    if (is_annotated) {
      raw_names <- mz_cols
      head_tok  <- trimws(sub("\\s*\\|.*$", "", raw_names))                       # 化合物名_m/z
      mz_num    <- suppressWarnings(as.numeric(sub("^.*_([0-9]+\\.?[0-9]*)$", "\\1", head_tok)))
      compound  <- sub("_[0-9]+\\.?[0-9]*$", "", head_tok)                         # 化合物名のみ（末尾 _m/z を除去）
      metabolite_names <- make.unique(head_tok)
      feature_annotations <- .parse_feature_annotations(raw_names, metabolite_names, mz_num, compound)
      cat(sprintf("  [Info] Using compound_m/z feature names; metadata preserved (%d features)\n",
                  length(metabolite_names)))
    } else {
      # Build metabolite names to MATCH CSV pipeline naming exactly: "m/z %.5f"
      if (is_bare_numeric) {
        mz_num <- as.numeric(mz_cols)
      } else {
        mz_num <- suppressWarnings(as.numeric(sub("^mz_", "", mz_cols)))
        if (anyNA(mz_num)) {
          # fallback: strip non-numeric
          mz_num <- suppressWarnings(as.numeric(gsub("[^0-9.]", "", sub("^mz_", "", mz_cols))))
        }
      }
      if (anyNA(mz_num)) stop("Failed to parse m/z from Parquet column names.")

      metabolite_names <- make.unique(sprintf("m/z %.5f", mz_num))
    }

    # Spot IDs consistent with CSV reader:
    base_prefix <- gsub("[^A-Za-z0-9_-]", "_", sample_prefix %||% "Sample")
    raw_spot <- df$id
    raw_spot[is.na(raw_spot)] <- seq_len(sum(is.na(raw_spot)))
    spot_id <- paste0(base_prefix, "_Spot_", raw_spot)

    coordinates <- data.frame(
      spot_index = raw_spot,
      x = as.numeric(df$x),
      y = as.numeric(df$y),
      spot_id = spot_id,
      row.names = spot_id
    )

    if ("annotation" %in% colnames(df)) {
      coordinates$annotation <- as.character(df$annotation)
    }

    # ---- Annotation Filter: 指定された切片のみ保持 ----
    # 行マスクは強度ブロック側にも同じ順序で適用する（旧実装が df を同時に絞っていたのと等価）。
    row_mask <- NULL
    if (!is.null(ANNOTATION_FILTER) && length(ANNOTATION_FILTER) > 0 &&
        "annotation" %in% colnames(coordinates)) {
      mask <- coordinates$annotation %in% ANNOTATION_FILTER
      if (sum(mask) == 0) {
        stop(sprintf("ANNOTATION_FILTER に一致する spot がありません: %s",
                      paste(ANNOTATION_FILTER, collapse = ", ")))
      }
      cat(sprintf("  Annotation filter: %d/%d spots kept (%s)\n",
                  sum(mask), nrow(coordinates),
                  paste(ANNOTATION_FILTER, collapse = ", ")))
      coordinates <- coordinates[mask, , drop = FALSE]
      spot_id <- coordinates$spot_id
      row_mask <- mask
    }
    n_rows_total <- nrow(df)
    rm(df); invisible(gc(verbose = FALSE))   # メタ列はもう不要（座標へ写し済み）

    # ---- 必要メモリの事前見積り（PreFlight）----
    # 見積りを先に出しておくと、2 時間走ってから OOM で落ちる事故を避けられる。
    .n_feat <- length(mz_cols)
    .n_cell <- length(spot_id)
    .dense_gb <- .n_cell * .n_feat * 8 / 1024^3
    cat(sprintf("  [size] %d spots x %d features (密換算 %.2f GB / スパース化して保持)\n",
                .n_cell, .n_feat, .dense_gb))

    # ---- 強度行列: m/z 列をブロック単位で読み、逐次スパース化して積む ----
    # ブロック幅は「1 ブロックの密サイズが .blk_budget を超えない」よう行数から決める。
    # 既定 256MB/ブロック。環境変数 INGEST_BLOCK_MB で上書き可（未設定なら既定＝挙動不変）。
    # 等価性テストが小さなデータでも複数ブロック経路を通せるようにするためのフック兼、
    # メモリ逼迫時の調整つまみ。
    .blk_mb <- suppressWarnings(as.numeric(Sys.getenv("INGEST_BLOCK_MB", unset = "")))
    if (!is.finite(.blk_mb) || .blk_mb <= 0) .blk_mb <- 256
    .blk_budget <- .blk_mb * 1024^2
    .blk_ncol <- max(1L, min(.n_feat,
                             as.integer(floor(.blk_budget / max(1, n_rows_total * 8)))))
    .starts <- seq.int(1L, .n_feat, by = .blk_ncol)
    cat(sprintf("  [stream] %d 列ずつ %d ブロックで読み込みます\n",
                .blk_ncol, length(.starts)))

    blocks <- vector("list", length(.starts))
    for (.bi in seq_along(.starts)) {
      .s <- .starts[.bi]
      .e <- min(.n_feat, .s + .blk_ncol - 1L)
      .cols <- mz_cols[.s:.e]
      .blk <- arrow::read_parquet(file_path, col_select = dplyr::all_of(.cols),
                                  as_data_frame = TRUE)
      .m <- as.matrix(.blk)
      rm(.blk)
      if (!is.null(row_mask)) .m <- .m[row_mask, , drop = FALSE]
      .m[!is.finite(.m)] <- 0
      # dimnames は最終行列へまとめて付けるため、ここでは外して rbind の挙動を決定的にする
      dimnames(.m) <- NULL
      # 転置はブロック単位なので一時領域も小さい。features x cells の向きで積む。
      blocks[[.bi]] <- as(t(.m), "dgCMatrix")
      rm(.m)
      if (.bi %% 10L == 0L || .bi == length(.starts)) invisible(gc(verbose = FALSE))
    }
    count_matrix <- if (length(blocks) == 1L) blocks[[1L]] else do.call(rbind, blocks)
    rm(blocks); invisible(gc(verbose = FALSE))

    # ---- [ver45.7 計測] 実際の疎性を出す ----
    # MSI 強度はゼロが少なく、dgCMatrix は 12 byte/非ゼロ要素（密は 8 byte/要素）なので、
    # 密度が 2/3 を超えると「スパース化」の方が密より重い。これまで疎性を測っておらず
    # スパース保持が得か損か判断できていなかったため、実測値をログに残す。
    .nz <- tryCatch(Matrix::nnzero(count_matrix), error = function(e) NA_real_)
    if (is.finite(.nz)) {
      .dens <- .nz / (as.numeric(nrow(count_matrix)) * ncol(count_matrix))
      .sp_gb <- .nz * 12 / 1024^3
      cat(sprintf("  [sparsity] 非ゼロ %.0f (密度 %.1f%%) / スパース保持 %.2f GB vs 密 %.2f GB%s\n",
                  .nz, .dens * 100, .sp_gb, .dense_gb,
                  if (.sp_gb > .dense_gb) "  ← 密の方が小さい" else ""))
    }
    .mem_note_base("取り込み完了")

    # Align dimnames
    if (nrow(count_matrix) != length(metabolite_names)) {
      k <- min(nrow(count_matrix), length(metabolite_names))
      count_matrix <- count_matrix[seq_len(k), , drop = FALSE]
      metabolite_names <- metabolite_names[seq_len(k)]
    }
    rownames(count_matrix) <- metabolite_names
    colnames(count_matrix) <- spot_id

    return(list(count_matrix = count_matrix, coordinates = coordinates,
                feature_annotations = if (!is.null(feature_annotations))
                  feature_annotations[match(rownames(count_matrix), feature_annotations$feature), , drop = FALSE]
                  else NULL))
  }

  # -------------------------------
  # Case 2) SCiLS Transform CSV (legacy)
  # -------------------------------
  cat("Reading CSV (fread):", file_path, "
")
  hdr <- readLines(file_path, n = 4, warn = FALSE)
  d <- if (grepl(",", hdr[1], fixed = TRUE)) "," else "	"
  tokens3 <- strsplit(hdr[3], d, fixed = TRUE)[[1]]
  mz_vals <- suppressWarnings(as.numeric(tokens3[4:(length(tokens3) - 2)]))
  metabolite_names <- make.unique(sprintf("m/z %.5f", mz_vals))
  dt <- data.table::fread(file_path, skip = 4, header = FALSE, sep = d, colClasses = "numeric", fill = TRUE, showProgress = FALSE)
  raw_spot <- dt[[1]]; if (anyNA(raw_spot)) raw_spot[is.na(raw_spot)] <- seq_len(sum(is.na(raw_spot)))
  base_prefix <- gsub("[^A-Za-z0-9_-]", "_", sample_prefix %||% "Sample")
  spot_id <- paste0(base_prefix, "_Spot_", raw_spot)

  # Detect optional trailing 'annotation' column (Transform CSV patched to include it at the very end).
  # We keep the numeric fast-path for the large intensity matrix, and read the annotation column separately as character.
  has_ann <- FALSE
  ann_vec <- NULL
  if (ncol(dt) >= 6) {
    last_na <- mean(is.na(dt[[ncol(dt)]]))
    prev_na <- mean(is.na(dt[[ncol(dt) - 1]]))
    if (is.finite(last_na) && is.finite(prev_na) && last_na > 0.90 && prev_na < 0.10) {
      has_ann <- TRUE
      dt_ann <- data.table::fread(file_path, skip = 4, header = FALSE, sep = d,
                                  select = ncol(dt), colClasses = "character",
                                  fill = TRUE, showProgress = FALSE)
      ann_vec <- as.character(dt_ann[[1]])
    }
  }

  if (has_ann) {
    x_col <- ncol(dt) - 2
    y_col <- ncol(dt) - 1
    feat_end <- ncol(dt) - 3
  } else {
    x_col <- ncol(dt) - 1
    y_col <- ncol(dt)
    feat_end <- ncol(dt) - 2
  }

  if (feat_end < 4) stop("Transform CSV format error: not enough columns to extract intensities.")

  coordinates <- data.frame(
    spot_index = raw_spot,
    x = dt[[x_col]],
    y = dt[[y_col]],
    spot_id = spot_id,
    row.names = spot_id
  )
  if (has_ann) coordinates$annotation <- ann_vec

  feat_mat <- as.matrix(dt[, 4:feat_end]); feat_mat[!is.finite(feat_mat)] <- 0
  count_matrix <- t(feat_mat)
  if (nrow(count_matrix) != length(metabolite_names)) {
    k <- min(nrow(count_matrix), length(metabolite_names))
    count_matrix <- count_matrix[seq_len(k), , drop = FALSE]
    metabolite_names <- metabolite_names[seq_len(k)]
  }
  rownames(count_matrix) <- metabolite_names; colnames(count_matrix) <- spot_id
  list(count_matrix = as(count_matrix, "dgCMatrix"), coordinates = coordinates)
}

read_desi_data_cached <- function(file_path, sample_prefix = NULL, cache_dir = RDS_CACHE_DIR, enable_cache = RDS_CACHE_ENABLE, force_rebuild = RDS_CACHE_FORCE_REBUILD) {
  sn <- sample_prefix %||% tools::file_path_sans_ext(basename(file_path))
  sn_safe <- gsub("[^A-Za-z0-9_\\-]", "_", sn)
  if (!dir.exists(cache_dir)) dir.create(cache_dir, recursive = TRUE, showWarnings = FALSE)
  cache_fp <- file.path(cache_dir, paste0(sn_safe, ".rds"))
  fi <- file.info(file_path)
  if (enable_cache && !force_rebuild && file.exists(cache_fp)) {
    obj <- tryCatch(readRDS(cache_fp), error = function(e) NULL)
    if (!is.null(obj) && is.list(obj) && !is.null(obj$meta) && !is.null(obj$data)) {
      ok <- isTRUE(all.equal(as.numeric(obj$meta$size), as.numeric(fi$size))) && isTRUE(all.equal(as.numeric(obj$meta$mtime), as.numeric(fi$mtime)))
      if (ok) { return(obj$data) }
    }
  }
  dat <- read_desi_data(file_path, sn)
  if (enable_cache) try(saveRDS(list(meta = list(size = as.numeric(fi$size), mtime = as.numeric(fi$mtime)), data = dat), cache_fp), silent = TRUE)
  dat
}

# ---- ヘルパー: フィーチャー名リネーム (Seurat v5 対応) ----
rename_seurat_features <- function(seu, new_names) {
  # Seurat v5 では LayerData<- がフィーチャー名の一致を要求するため、
  # オブジェクトを再構築する方式で対応
  mat <- LayerData(seu[["Spatial"]], layer = "counts")
  rownames(mat) <- new_names
  meta <- seu@meta.data
  new_seu <- CreateSeuratObject(
    counts = as(mat, "dgCMatrix"),
    project = seu@project.name,
    assay = "Spatial"
  )
  new_seu@meta.data <- meta
  new_seu
}

# ---- フィーチャー名のm/zをキャリブレーション補正 ----
calibrate_feature_names <- function(seu_list) {
  if (!isTRUE(CALIBRATION_ENABLE) || length(CALIBRATION_COEFFICIENTS) < 1) {
    return(seu_list)
  }
  cat("  [Calibration] Applying m/z calibration to feature names...\n")
  has_per_sample <- length(CALIBRATION_BY_SAMPLE) > 0

  for (i in seq_along(seu_list)) {
    sname <- seu_list[[i]]$sample[1]

    # サンプル固有 or グローバルフォールバック
    if (has_per_sample && !is.null(sname) && sname %in% names(CALIBRATION_BY_SAMPLE)) {
      coefs <- CALIBRATION_BY_SAMPLE[[sname]]
      cat(sprintf("  [Calibration] Sample '%s': per-sample coefficients\n", sname))
    } else {
      coefs <- CALIBRATION_COEFFICIENTS
      if (has_per_sample) {
        cat(sprintf("  [Calibration] Sample '%s': global fallback\n", sname))
      }
    }

    old_names <- rownames(seu_list[[i]])
    old_mz <- .feature_mz(old_names)
    new_mz <- calibrate_mz(old_mz, coefs, TRUE)
    new_names <- sprintf("m/z %.5f", new_mz)
    if (any(duplicated(new_names))) {
      seu_list[[i]] <- merge_duplicate_features(seu_list[[i]], new_names)
    } else {
      seu_list[[i]] <- rename_seurat_features(seu_list[[i]], new_names)
    }
  }
  n_feat <- length(rownames(seu_list[[1]]))
  cat(sprintf("  [Calibration] Done (%d features)\n", n_feat))
  seu_list
}

# ---- m/z アライメント: ppm tolerance内のピークを統一 ----
align_mz_features <- function(seu_list, ppm_tol) {
  all_mz <- c()
  for (s in seu_list) {
    feat <- rownames(s)
    mz_num <- .feature_mz(feat)
    all_mz <- c(all_mz, mz_num[!is.na(mz_num)])
  }
  all_mz <- sort(unique(all_mz))

  groups <- list()
  used <- rep(FALSE, length(all_mz))
  for (i in seq_along(all_mz)) {
    if (used[i]) next
    mz_i <- all_mz[i]
    within <- which(!used & abs(all_mz - mz_i) / mz_i * 1e6 <= ppm_tol)
    used[within] <- TRUE
    representative <- median(all_mz[within])
    groups[[length(groups) + 1]] <- list(
      members = all_mz[within],
      rep_mz = representative,
      rep_name = sprintf("m/z %.5f", representative)
    )
  }

  for (i in seq_along(seu_list)) {
    old_names <- rownames(seu_list[[i]])
    old_mz <- .feature_mz(old_names)
    new_names <- old_names
    for (g in groups) {
      for (member in g$members) {
        idx <- which(abs(old_mz - member) < 1e-8)
        if (length(idx) == 1) {
          new_names[idx] <- g$rep_name
        }
      }
    }
    if (any(duplicated(new_names))) {
      seu_list[[i]] <- merge_duplicate_features(seu_list[[i]], new_names)
    } else {
      seu_list[[i]] <- rename_seurat_features(seu_list[[i]], new_names)
    }
  }

  n_groups <- length(groups)
  n_multi <- sum(sapply(groups, function(g) length(g$members) > 1))
  cat(sprintf("  [m/z Align] %d unique m/z -> %d groups (%d merged, ppm=%g)\n",
              length(all_mz), n_groups, n_multi, ppm_tol))

  seu_list
}

merge_duplicate_features <- function(seu, new_names) {
  mat <- as.matrix(LayerData(seu[["Spatial"]], layer = "counts"))
  rownames(mat) <- new_names
  rows_keep <- list()
  for (nm in unique(new_names)) {
    idx <- which(new_names == nm)
    if (length(idx) == 1) {
      rows_keep[[nm]] <- mat[idx, , drop = FALSE]
    } else {
      rows_keep[[nm]] <- matrix(colSums(mat[idx, , drop = FALSE]),
                                nrow = 1, dimnames = list(nm, colnames(mat)))
    }
  }
  new_mat <- do.call(rbind, rows_keep)
  new_seu <- CreateSeuratObject(
    counts = as(new_mat, "dgCMatrix"),
    project = seu@project.name,
    assay = "Spatial"
  )
  new_seu@meta.data <- seu@meta.data
  new_seu
}

# [P2: 距離行列事前計算で高速化]
spatial_smooth_seurat <- function(seurat_obj, radius, sigma = NULL) {
  if (is.null(sigma)) sigma <- radius / 3
  coords <- data.frame(x = seurat_obj$x_coord, y = seurat_obj$y_coord)
  count_data <- LayerData(seurat_obj[["Spatial"]], layer = "counts")
  n_spots <- ncol(count_data)
  smoothed_data <- matrix(0, nrow = nrow(count_data), ncol = n_spots, dimnames = dimnames(count_data))

  # ver3.8: DESI と同じ閾値 15000 に統一。
  # 50000 spots は 50000^2 * 8 bytes = 20 GB の距離行列を要求し、
  # Docker メモリ上限を超えて R プロセスが OOM Killer に殺される。
  # 距離行列を1回だけ計算（15,000スポット以下: 高速、超過: メモリ節約のためスポットごと）
  use_dist_mat <- (n_spots <= 15000)
  if (use_dist_mat) {
    dist_mat <- as.matrix(dist(coords))
  }

  for (i in 1:n_spots) {
    if (use_dist_mat) {
      dists <- dist_mat[i, ]
    } else {
      dists <- sqrt((coords$x - coords$x[i])^2 + (coords$y - coords$y[i])^2)
    }
    neighbors <- which(dists <= radius)
    if (length(neighbors) == 0) next
    weights <- exp(-(dists[neighbors]^2) / (2 * sigma^2)); weights <- weights / sum(weights)
    neighbor_data <- count_data[, neighbors, drop = FALSE]
    smoothed_data[, i] <- if (length(neighbors) == 1) as.vector(neighbor_data) else as.vector(neighbor_data %*% weights)
  }
  seurat_obj[["Spatial"]] <- SetAssayData(seurat_obj[["Spatial"]], layer = "counts", new.data = as(smoothed_data, "dgCMatrix"))
  seurat_obj
}

visualize_spatial_smoothing <- function(original, smoothed) {
  features <- head(rownames(original), 4)
  md <- original@meta.data; dx <- .grid_step(md$x_coord); dy <- .grid_step(md$y_coord)
  plots <- list()
  for (f in features) {
    orig_d <- LayerData(original[["Spatial"]], layer="counts")[f,]; sm_d <- LayerData(smoothed[["Spatial"]], layer="counts")[f,]
    df <- rbind(data.frame(x=md$x_coord, y=md$y_coord, val=as.numeric(orig_d), type="Original"),
                data.frame(x=md$x_coord, y=md$y_coord, val=as.numeric(sm_d), type="Smoothed"))
    plots[[f]] <- ggplot(df, aes(x, y, fill=val)) + geom_tile(width=dx, height=dy) + scale_fill_viridis_c() +
      scale_y_reverse() + coord_fixed() + facet_wrap(~type) + theme_minimal() + labs(title=f)
  }
  wrap_plots(plots, ncol=2)
}

# ============================================================
# ★追加機能: TIC Overlay 関連関数 (Template_v8より移植)
# ============================================================
safe_ggsave <- function(filename, plot, width, height, dpi = 300) {
  tryCatch({
    ggsave(filename, plot, width=width, height=height, dpi=dpi, limitsize=FALSE, bg="white")
  }, error = function(e) {
    message("!! ggsave failed: ", e$message)
  })
}

.plot_spatial_tic_only <- function(md, tic_col = "nCount_Spatial", title = NULL) {
  df <- md %>% dplyr::select(x_coord, y_coord, tic = .data[[tic_col]])
  tv <- df$tic; ql <- TIC_Q_LOW; qh <- TIC_Q_HIGH
  vmin <- quantile(tv, ql, na.rm=TRUE); vmax <- quantile(tv, qh, na.rm=TRUE)
  if(vmax<=vmin) vmax <- max(tv)
  
  ggplot(df, aes(x = x_coord, y = y_coord, fill = tic)) +
    geom_raster(interpolate = FALSE) +
    scale_fill_gradient(low = TIC_GRAY_LOW, high = TIC_GRAY_HIGH, limits = c(vmin, vmax), oob = scales::squish) +
    scale_y_reverse() + coord_fixed() + theme_void() +
    theme(plot.title = element_text(hjust=0.5, face="bold")) +
    if(!is.null(title)) ggtitle(title) else NULL
}

.plot_spatial_tic_overlay <- function(md, cl, cl_color, tic_col="nCount_Spatial", alpha_hi=0.65, title=NULL) {
  df <- md %>% dplyr::select(x_coord, y_coord, seurat_clusters, tic = .data[[tic_col]])
  df_hi <- df[as.character(df$seurat_clusters) == as.character(cl), , drop = FALSE]
  tv <- df$tic; ql <- TIC_Q_LOW; qh <- TIC_Q_HIGH
  vmin <- quantile(tv, ql, na.rm=TRUE); vmax <- quantile(tv, qh, na.rm=TRUE)
  if(vmax<=vmin) vmax <- max(tv)
  
  p <- ggplot(df, aes(x=x_coord, y=y_coord)) +
    geom_raster(aes(fill=tic), interpolate=FALSE) +
    scale_fill_gradient(low=TIC_GRAY_LOW, high=TIC_GRAY_HIGH, limits=c(vmin, vmax), oob=scales::squish) +
    {
      if(TIC_OVERLAY_STYLE == "outline") {
        geom_point(data=df_hi, aes(x=x_coord, y=y_coord), shape=0, color=cl_color, size=1.1, stroke=0.45)
      } else {
        geom_raster(data=df_hi, aes(x=x_coord, y=y_coord), fill=cl_color, alpha=alpha_hi, interpolate=FALSE)
      }
    } +
    scale_y_reverse() + coord_fixed() + theme_void() + theme(legend.position="none") +
    theme(plot.title = element_text(hjust=0.5, face="bold"))
  if(!is.null(title)) p <- p + ggtitle(title)
  p
}

export_cluster_highlights <- function(obj, prefix, outdir) {
  clusters <- levels(Idents(obj))
  base_dir <- file.path(outdir, "PerCluster_Highlight", prefix)
  dir.create(base_dir, recursive=TRUE, showWarnings=FALSE)

  # Template_v8で出力されている「一覧（ALLclusters）画像」に合わせて、ここでも集約用プロットを保持
  umap_hi_all <- list()
  umap_per_group_rows_all <- list()


# === (Add) Keep per-cluster UMAPtop/TICbottom grids to build an ALLclusters summary per method (requirement ②) ===
umap_tic_pair_rows_all <- list()
max_panels_in_row <- 1L

  ensure_global_palette(obj)

  # Template_v8準拠:
  #  - 比較単位は「切片（slice_id）」を最優先、次に condition、最後に sample
  md_all <- obj@meta.data
  group_col <- NULL
  if ("slice_id" %in% colnames(md_all) && dplyr::n_distinct(na.omit(md_all$slice_id)) >= 2) {
    group_col <- "slice_id"
  } else if ("condition" %in% colnames(md_all) && dplyr::n_distinct(na.omit(md_all$condition)) >= 2) {
    group_col <- "condition"
  } else if ("sample" %in% colnames(md_all)) {
    group_col <- "sample"
  } else {
    group_col <- "condition"
  }
  group_names <- unique(md_all[[group_col]])



# Display label:
# If group_col == "slice_id" and SLICE_CONDITION_MAP provides mapping (slice_id -> condition),
# show the mapped label in plots instead of raw slice_id.
slice_to_label <- NULL
if (exists("SLICE_CONDITION_MAP", envir = .GlobalEnv)) {
  tryCatch({
    sm <- get("SLICE_CONDITION_MAP", envir = .GlobalEnv)
    if (all(c("slice_id", "condition") %in% colnames(sm))) {
      slice_to_label <- setNames(as.character(sm$condition), as.character(sm$slice_id))
    }
  }, error = function(e) {})
}
.disp_group <- function(g_raw) {
  g_raw <- as.character(g_raw)
  if (!is.null(slice_to_label) && group_col == "slice_id" && g_raw %in% names(slice_to_label)) {
    return(slice_to_label[[g_raw]])
  }
  return(g_raw)
}
  tic_overlay_list_all <- list()

  for (cl in clusters) {
    cl_dir <- file.path(base_dir, paste0("Cluster_", cl))
    dir.create(cl_dir, showWarnings=FALSE)
    cl_color <- unname(CLUSTER_PAL[as.character(cl)])

    # UMAP Highlight（全体）
    p_umap_all <- DimPlot(
      obj, reduction="umap",
      cells.highlight = setNames(list(WhichCells(obj, idents = cl)), paste0("Group_", cl)),
      cols="grey85", cols.highlight=cl_color, sizes.highlight=1.2
    ) + NoAxes() + ggtitle(paste0("Cluster ", cl))
    # safe_ggsave(file.path(cl_dir, paste0("umap_highlight_cluster_", cl, ".png")), p_umap_all, 7, 6)

    umap_hi_all[[as.character(cl)]] <- p_umap_all

    # Template_v8形式で「上段UMAP」「下段Overlay」を同じキー（切片/condition）で保持
    umap_hi_list <- list()
    tic_overlay_list <- list()

    for (g in group_names) {
      cells_g <- rownames(md_all)[as.character(md_all[[group_col]]) == as.character(g)]
      if (length(cells_g) == 0) next

      sub <- subset(obj, cells=cells_g)
      if (!any(sub$seurat_clusters == cl)) next

      safe_g <- gsub("[^A-Za-z0-9_-]", "_", as.character(g))

      # 上段: この切片（group）だけのUMAP（まとめない）
      p_umap_g <- DimPlot(
        sub, reduction="umap",
        cells.highlight = setNames(list(WhichCells(sub, idents = cl)), paste0("Group_", cl)),
        cols="grey85", cols.highlight=cl_color, sizes.highlight=1.2
      ) + NoAxes() + ggtitle(.disp_group(g)) +
        theme(plot.margin = margin(6, 6, 6, 6),
              panel.border = element_rect(color="grey55", fill=NA, size=0.5))
      umap_hi_list[[as.character(g)]] <- p_umap_g

      # TIC Only
      if (isTRUE(OUTPUT_TIC_ONLY)) {
        p_tic <- .plot_spatial_tic_only(sub@meta.data, title=.disp_group(g))
        # safe_ggsave(file.path(cl_dir, paste0("spatial_TIC_only_", safe_g, ".png")), p_tic, 6, 6)
      }

      # 下段: TIC overlay（対応する切片）
      if (isTRUE(OUTPUT_TIC_OVERLAY)) {
        p_ov <- .plot_spatial_tic_overlay(sub@meta.data, cl, cl_color, title=paste0(.disp_group(g), " (Cl ", cl, ")"))
        # safe_ggsave(file.path(cl_dir, paste0("spatial_overlay_", safe_g, ".png")), p_ov, 6, 6)
        tic_overlay_list[[as.character(g)]] <- p_ov

        # UMAP x Spatial Pair（個別）
        p_pair <- p_umap_g + p_ov + plot_layout(widths=c(1,1))
        # safe_ggsave(file.path(cl_dir, paste0("UMAPxSpatial_cluster_", cl, "_", safe_g, ".png")), p_pair, 12, 6)
      }
    }

    # Combined Overlay（下段のみ）
    if (length(tic_overlay_list) > 0) {
      keys <- names(tic_overlay_list)

      p_comb <- wrap_plots(tic_overlay_list[keys], nrow=1) +
        plot_annotation(title=paste0("TIC Overlay: Cluster ", cl))
      # safe_ggsave(file.path(cl_dir, "COMBINED_TIC_overlay.png"),
      #             p_comb, width=6*length(keys), height=6)

      # 要望②: 上段＝各切片のUMAPを横並び（1つにまとめない）
      #        下段＝対応するTIC overlayを横並び
            p_top <- wrap_plots(umap_hi_list[keys], nrow=1) + plot_layout(guides="collect") & theme(legend.position="bottom")
      # Template_v8準拠: clusterごとの「group別UMAP」一覧行を保持
      umap_per_group_rows_all[[as.character(cl)]] <- p_top
      p_bottom <- wrap_plots(tic_overlay_list[keys], nrow=1)
      p_grid <- (p_top / p_bottom) +
        plot_annotation(title=paste0("UMAP (top) + TIC overlay (bottom): Cluster ", cl, " / ", group_col))
      # safe_ggsave(file.path(cl_dir, "UMAPtop_TICoverlaybottom_COMBINED.png"),
      #             p_grid, width=6*length(keys), height=12)


# (Add) Store this grid for ALLclusters summary (per method/prefix)
umap_tic_pair_rows_all[[as.character(cl)]] <- p_grid
max_panels_in_row <- max(max_panels_in_row, length(keys))
    }
  }

# === (Add) Build a single ALLclusters summary image for this method (prefix) (requirement ②) ===
# Following the design pattern of Template_v8: keep per-cluster grids, then wrap_plots at the end.
if (length(umap_tic_pair_rows_all) > 0) {
  clusters <- levels(Idents(obj))
  rows2 <- umap_tic_pair_rows_all[clusters]
  rows2 <- rows2[!vapply(rows2, is.null, logical(1))]
  if (length(rows2) > 0) {
    p_allclusters <- wrap_plots(rows2, ncol = 1) +
      plot_annotation(title = paste0("UMAP(top) + TIC overlay(bottom) | ALL clusters | ", prefix))
    w <- max(12, 6 * max_panels_in_row)
    h <- 12 * length(rows2)
    safe_ggsave(file.path(base_dir, "UMAPtop_TICoverlaybottom_COMBINED_ALLclusters.png"),
                p_allclusters, width = w, height = h)
  }


  # ---- SUMMARY: Template_v8 相当の「一覧（ALLclusters）画像」を追加（要望④） ----
  if (length(umap_hi_all) > 0) {
    n_all <- length(umap_hi_all)
    ncol <- min(5, n_all)
    nrow <- ceiling(n_all / ncol)
    p_all <- wrap_plots(umap_hi_all, ncol = ncol) +
      plot_annotation(title = paste0("UMAP Highlight (all clusters) / ", prefix))
    # safe_ggsave(file.path(base_dir, paste0("UMAP_Highlight_", prefix, "_ALLclusters.png")),
    #             p_all, width = 6 * ncol, height = 6 * nrow)
  }

  if (length(umap_per_group_rows_all) > 0) {
    rows <- umap_per_group_rows_all[clusters]
    rows <- rows[!vapply(rows, is.null, logical(1))]
    if (length(rows) > 0) {
      p_big <- wrap_plots(rows, ncol = 1) +
        plot_annotation(title = paste0("UMAP per-", group_col, " (all clusters) / ", prefix))
      # safe_ggsave(file.path(base_dir, paste0("UMAP_per_", group_col, "_", prefix, "_ALLclusters.png")),
      #             p_big, width = max(12, 6 * max_panels_in_row), height = 6 * length(rows))
    }
  }

}

}


plot_spatial_clusters_all_samples <- function(seu, red_used, outdir, fname_prefix) {
  ensure_global_palette(seu); md_all <- seu@meta.data
  group_var <- if ("condition" %in% colnames(md_all) && dplyr::n_distinct(na.omit(md_all$condition)) >= 2) "condition" else "sample"
  groups <- levels(factor(md_all[[group_var]])); plots <- list(); plots_nolabel <- list()
  dx <- .grid_step(md_all$x_coord); dy <- .grid_step(md_all$y_coord)

  for (g in groups) {
    md <- md_all[md_all[[group_var]] == g & !is.na(md_all[[group_var]]), ]
    if (nrow(md) == 0) next
    md$seurat_clusters <- factor(as.character(md$seurat_clusters), levels = CLUSTER_LEVELS)
    centers <- md %>% group_by(seurat_clusters) %>% summarise(x = median(x_coord), y = median(y_coord))

    # labeled (existing)
    p_g <- ggplot(md, aes(x = x_coord, y = y_coord, fill = seurat_clusters)) + geom_tile(width = dx, height = dy) +
      scale_fill_manual(values = CLUSTER_PAL, breaks = CLUSTER_LEVELS, drop = FALSE) +
      geom_text(data = centers, aes(x = x, y = y, label = seurat_clusters), inherit.aes = FALSE, size = SPATIAL_LABEL_SIZE, fontface = "bold") +
      scale_y_reverse() + coord_fixed() + theme_minimal() + ggtitle(g)

    # no-label (requirement ①)
    p_g_nolabel <- ggplot(md, aes(x = x_coord, y = y_coord, fill = seurat_clusters)) + geom_tile(width = dx, height = dy) +
      scale_fill_manual(values = CLUSTER_PAL, breaks = CLUSTER_LEVELS, drop = FALSE) +
      scale_y_reverse() + coord_fixed() + theme_minimal() + ggtitle(paste0(g, " (no labels)"))

    plots[[length(plots) + 1L]] <- p_g
    plots_nolabel[[length(plots_nolabel) + 1L]] <- p_g_nolabel
  }

  p_all <- wrap_plots(plots, nrow = 1) + plot_annotation(title = paste0(toupper(red_used)))
  ggsave(file.path(outdir, paste0(fname_prefix, red_used, "_tight.png")), p_all,
         width = max(6, 5 * length(plots)), height = SPATIAL_BASE_HEIGHT)

  # requirement ①: add a no-label version
  p_all_nl <- wrap_plots(plots_nolabel, nrow = 1) + plot_annotation(title = paste0(toupper(red_used), " (no labels)"))
  # ggsave(file.path(outdir, paste0(fname_prefix, red_used, "_tight_nolabel.png")), p_all_nl,
  #        width = max(6, 5 * length(plots_nolabel)), height = SPATIAL_BASE_HEIGHT)
}

save_spatial_per_group <- function(seu, outdir, prefix, title_prefix) {
  ensure_global_palette(seu); md_full <- seu@meta.data
  group_var <- if ("condition" %in% colnames(md_full) && dplyr::n_distinct(na.omit(md_full$condition)) >= 2) "condition" else "sample"
  groups <- levels(factor(md_full[[group_var]])); dx <- .grid_step(md_full$x_coord); dy <- .grid_step(md_full$y_coord)

  for (g in groups){
    sub <- subset(seu, cells = rownames(md_full)[md_full[[group_var]] == g])
    md <- sub@meta.data
    md$seurat_clusters <- factor(as.character(md$seurat_clusters), levels = CLUSTER_LEVELS)
    centers <- md %>% group_by(seurat_clusters) %>% summarise(x = median(x_coord), y = median(y_coord))

    # labeled (existing)
    p <- ggplot(md, aes(x = x_coord, y = y_coord, fill = seurat_clusters)) + geom_tile(width = dx, height = dy) +
      scale_fill_manual(values = CLUSTER_PAL, breaks = CLUSTER_LEVELS, drop = FALSE) +
      geom_text(data = centers, aes(x = x, y = y, label = seurat_clusters), inherit.aes = FALSE, size = SPATIAL_LABEL_SIZE, fontface = "bold") +
      scale_y_reverse() + coord_fixed() + theme_minimal() + ggtitle(paste0(title_prefix, " / ", g))
    # ggsave(file.path(outdir, paste0(prefix, g, "_tight.png")), p, width = 6, height = 5)

    # requirement ①: no-label
    p_nl <- ggplot(md, aes(x = x_coord, y = y_coord, fill = seurat_clusters)) + geom_tile(width = dx, height = dy) +
      scale_fill_manual(values = CLUSTER_PAL, breaks = CLUSTER_LEVELS, drop = FALSE) +
      scale_y_reverse() + coord_fixed() + theme_minimal() + ggtitle(paste0(title_prefix, " / ", g, " (no labels)"))
    # ggsave(file.path(outdir, paste0(prefix, g, "_tight_nolabel.png")), p_nl, width = 6, height = 5)
  }
}

assign_xy_grid <- function(seu, nx=NULL, ny=NULL){
  # NOTE: DBSCAN / グリッド推定は使用しません。
  # per-spot の 'annotation' をそのまま slice_id として採用します。

  md <- seu@meta.data

  if (!("annotation" %in% colnames(md))) {
    stop("assign_xy_grid: 'annotation' column not found in meta.data.\n- Ensure your Transform CSV/Parquet includes an 'annotation' column and that it is loaded into the Seurat object.")
  }

  ann <- as.character(md$annotation)
  if (any(is.na(ann)) || any(trimws(ann) == "")) {
    bad_n <- sum(is.na(ann) | trimws(ann) == "")
    stop(sprintf("assign_xy_grid: found %d invalid annotation values (NA/blank). Please fix annotation input.", bad_n))
  }

  seu$slice_id <- ann
  return(seu)
}


# ============================================================
# ★追加機能: 共通ダウンストリーム解析関数
#   (Harmony/RPCA/PCA すべてで実行)
# ============================================================

# ============================================================
# ★追加: RDS (UMAP/DEG/PlotData/PixelTable) 追加保存 & Resume ヘルパー
#   - UMAP: Embeddings の保存/復元（RunUMAPの再計算を回避）
#   - DEG: FindAllMarkers 生結果の再利用（既存実装を維持しつつ強化）
#   - PlotData: Volcano/Top5再描画用の軽量テーブルを保存
#   - PixelTable: x/y/cluster/TIC など描画材料を保存（Web共有向け）
# ============================================================

.safe_readRDS <- function(fp) {
  tryCatch(readRDS(fp), error = function(e) NULL)
}
.safe_saveRDS <- function(obj, fp) {
  tryCatch({
    dir.create(dirname(fp), recursive = TRUE, showWarnings = FALSE)
    saveRDS(obj, fp)
    TRUE
  }, error = function(e) {
    message("!! saveRDS failed: ", fp, " | ", e$message)
    FALSE
  })
}

# Get candidate paths (resume dir -> current save dir)
.get_rds_candidates <- function(fname) {
  cand <- c()
  if (exists("RESUME_DIR_PATH", envir = .GlobalEnv) && nzchar(get("RESUME_DIR_PATH", envir = .GlobalEnv))) {
    cand <- c(cand, file.path(get("RESUME_DIR_PATH", envir = .GlobalEnv), fname))
  }
  if (exists("RDS_SAVE_DIR", envir = .GlobalEnv) && nzchar(get("RDS_SAVE_DIR", envir = .GlobalEnv))) {
    cand <- c(cand, file.path(get("RDS_SAVE_DIR", envir = .GlobalEnv), fname))
  }
  unique(cand)
}

.load_umap_embedding_if_exists <- function(obj, prefix) {
  fname <- paste0("UMAP_", prefix, "_umap_embedding.rds")
  for (fp in .get_rds_candidates(fname)) {
    if (file.exists(fp)) {
      message(">> RESUME: Loading UMAP embedding from RDS: ", fp)
      emb <- .safe_readRDS(fp)
      if (!is.null(emb) && is.matrix(emb) && ncol(emb) == ncol(obj)) {
        # Ensure rownames match cells; if not, try to align by cell names
        if (!is.null(rownames(emb)) && !is.null(colnames(obj))) {
          if (!all(rownames(emb) == colnames(obj))) {
            common <- intersect(rownames(emb), colnames(obj))
            if (length(common) == ncol(obj)) {
              emb <- emb[colnames(obj), , drop = FALSE]
            }
          }
        }
        obj[["umap"]] <- Seurat::CreateDimReducObject(
          embeddings = emb,
          key = "UMAP_",
          assay = DefaultAssay(obj)
        )
        return(obj)
      }
    }
  }
  obj
}

.save_umap_embedding <- function(obj, prefix) {
  if (!("umap" %in% names(obj@reductions))) return(invisible(FALSE))
  emb <- tryCatch(Seurat::Embeddings(obj, reduction = "umap"), error = function(e) NULL)
  if (is.null(emb)) return(invisible(FALSE))
  if (exists("RDS_SAVE_DIR", envir = .GlobalEnv) && nzchar(get("RDS_SAVE_DIR", envir = .GlobalEnv))) {
    fp <- file.path(get("RDS_SAVE_DIR", envir = .GlobalEnv), paste0("UMAP_", prefix, "_umap_embedding.rds"))
    return(invisible(.safe_saveRDS(emb, fp)))
  }
  invisible(FALSE)
}

.save_plotdata_and_pixel_table <- function(obj, deg, prefix, outdir) {
  # PlotData (Volcano再描画用): degを元に必要列だけ保存
  if (!is.null(deg) && is.data.frame(deg) && nrow(deg) > 0) {
    # Ensure common computed columns exist if caller already computed
    cols_keep <- intersect(c("gene","cluster","avg_log2FC","p_val","p_val_adj","pct.1","pct.2",
                             "annotation","mz_only","color","log_p","annot_or_mz"),
                           colnames(deg))
    plotdat <- deg[, cols_keep, drop = FALSE]
    if (exists("RDS_SAVE_DIR", envir = .GlobalEnv) && nzchar(get("RDS_SAVE_DIR", envir = .GlobalEnv))) {
      .safe_saveRDS(plotdat, file.path(get("RDS_SAVE_DIR", envir = .GlobalEnv),
                                       paste0("plotdata_volcano_", prefix, ".rds")))
      # Top5用: 採用候補（UP/DOWN topN）をクラスタごとに保存
      if (all(c("cluster","avg_log2FC","p_val_adj") %in% colnames(deg))) {
        topN <- if (exists("LABEL_TOP_N_EACH", envir = .GlobalEnv)) get("LABEL_TOP_N_EACH", envir = .GlobalEnv) else 5
        pth  <- if (exists("DEG_P_THRESH_VAL", envir = .GlobalEnv)) get("DEG_P_THRESH_VAL", envir = .GlobalEnv) else 0.05
        tmp <- deg
        tmp <- tmp[tmp$p_val_adj < pth & is.finite(tmp$avg_log2FC), , drop = FALSE]
        if (nrow(tmp) > 0) {
          top_up <- tmp %>% dplyr::group_by(cluster) %>% dplyr::arrange(dplyr::desc(avg_log2FC)) %>% dplyr::slice_head(n = topN)
          top_dn <- tmp %>% dplyr::group_by(cluster) %>% dplyr::arrange(avg_log2FC)              %>% dplyr::slice_head(n = topN)
          top_tbl <- dplyr::bind_rows(
            dplyr::mutate(top_up, direction = "UP"),
            dplyr::mutate(top_dn, direction = "DOWN")
          )
          cols2 <- intersect(c("gene","cluster","avg_log2FC","p_val_adj","annotation","mz_only","direction"), colnames(top_tbl))
          top_tbl2 <- top_tbl[, cols2, drop = FALSE]
          .safe_saveRDS(top_tbl2, file.path(get("RDS_SAVE_DIR", envir = .GlobalEnv),
                                            paste0("plotdata_top5_", prefix, ".rds")))
        }
      }
    }
  }

  # PixelTable: meta.data から描画材料（x/y/cluster/TIC/条件など）を保存
  md <- obj@meta.data
  keep_md <- intersect(c("x_coord","y_coord","seurat_clusters","nCount_Spatial","slice_id","condition","sample"),
                       colnames(md))
  if (length(keep_md) > 0) {
    pix <- md[, keep_md, drop = FALSE]
    pix$cell <- rownames(md)
    if (exists("RDS_SAVE_DIR", envir = .GlobalEnv) && nzchar(get("RDS_SAVE_DIR", envir = .GlobalEnv))) {
      .safe_saveRDS(pix, file.path(get("RDS_SAVE_DIR", envir = .GlobalEnv),
                                   paste0("pixel_table_", prefix, ".rds")))
    }
  }
  invisible(TRUE)
}

run_downstream_analysis <- function(obj, prefix, outdir, ann_db, generate_mz_only = TRUE) {
  # PIPELINE_STAGE: "reduction_only" の場合は UMAP/クラスタリング/DEG/作図など
  # 下流処理を一切行わずに即終了する（PreFlight 診断用に reduction RDS だけ確定させる）。
  # reduction（Step2 Harmony/PCA・Step3 RPCA）は本関数の呼び出し前に既に保存済み。
  if (identical(PIPELINE_STAGE, "reduction_only")) {
    cat(paste0("\n>>> PIPELINE_STAGE=reduction_only: skip downstream for: ", prefix, " <<<\n"))
    return(invisible(NULL))
  }
  cat(paste0("\n>>> Starting Downstream Analysis for: ", prefix, " <<<\n"))
  
  # サブフォルダに出力 (上書き防止)
  sub_od <- file.path(outdir, prefix)
  dir.create(sub_od, showWarnings = FALSE)
  
  # 1. 基本プロット (UMAP / Spatial)
  ensure_global_palette(obj)

  # ---- UMAP Resume/Save (追加RDS) ----
  # If UMAP reduction is missing, try to restore from RDS; otherwise compute once and save embedding.
  if (!("umap" %in% names(obj@reductions))) {
    obj <- .load_umap_embedding_if_exists(obj, prefix)
  }
  if (!("umap" %in% names(obj@reductions))) {
    message(">> UMAP reduction not found. Running RunUMAP() ...")
    # Choose dims & reduction source
    red_src <- if ("harmony" %in% names(obj@reductions)) {
      "harmony"
    } else if ("rpca" %in% names(obj@reductions)) {
      "rpca"
    } else if ("pca" %in% names(obj@reductions)) {
      "pca"
    } else {
      "pca"
    }
    # dims は選んだ reduction が実際に持つ次元数を超えないよう上限を合わせる
    #   （下の FindNeighbors:.dims_clust と対称。④で①と dims 設定が食い違っても RunUMAP が落ちないよう防御）。
    dims_use <- 1:min(UMAP_DIMS_MAX, MAX_PCS, ncol(Embeddings(obj, red_src)))
    obj <- RunUMAP(obj, dims = dims_use, reduction = red_src, reduction.name = "umap", reduction.key = "UMAP_",
                   n.neighbors = UMAP_N_NEIGHBORS, min.dist = UMAP_MIN_DIST,
                   metric = UMAP_METRIC, seed.use = GLOBAL_RANDOM_SEED)
    .save_umap_embedding(obj, prefix)
  } else {
    # Always refresh/save embedding for reproducibility
    .save_umap_embedding(obj, prefix)
  }

  # ④/フル: クラスタが無ければ後付け。reduction-only RDS には seurat_clusters 列が
  #   無いので検出して FindNeighbors+FindClusters を実行（full/classic-resume では
  #   既に列があるため no-op）。④では完成(umap+cluster付き)RDSを新フォルダに再保存。
  if (!("seurat_clusters" %in% colnames(obj@meta.data))) {
    .red_for_clust <- if (prefix %in% names(obj@reductions)) prefix
                      else if ("harmony" %in% names(obj@reductions)) "harmony"
                      else if ("rpca" %in% names(obj@reductions)) "rpca"
                      else "pca"
    .dims_clust <- 1:min(UMAP_DIMS_MAX, MAX_PCS, ncol(Embeddings(obj, .red_for_clust)))
    obj <- FindNeighbors(obj, reduction = .red_for_clust, dims = .dims_clust,
                         k.param = CLUSTER_K_PARAM, annoy.metric = CLUSTER_METRIC)
    obj <- FindClusters(obj, resolution = CLUSTER_RESOLUTION, algorithm = CLUSTER_ALGORITHM)
    Idents(obj) <- obj$seurat_clusters
    if (identical(PIPELINE_STAGE, "downstream_from_reduction")) {
      if (identical(prefix, "rpca")) {
        save_rds_compact(list(obj = obj), file.path(RDS_SAVE_DIR, "Step3_RPCA_Result.rds"), keep_counts = FALSE)
      } else if (prefix %in% c("harmony", "pca")) {
        save_rds_compact(list(obj = obj, reduction = REDUCTION_USED),
                         file.path(RDS_SAVE_DIR, "Step2_HarmonyPCA_Result.rds"), keep_counts = FALSE)
      }
    }
  }

  # ---- Save PixelTable early (x/y/cluster/TIC, etc.) ----
  .save_plotdata_and_pixel_table(obj, deg = NULL, prefix = prefix, outdir = sub_od)

  # UMAP Cluster Labeled
  p1 <- DimPlot(obj, reduction="umap", group.by="seurat_clusters", cols=CLUSTER_PAL) + ggtitle(paste0("UMAP (", prefix, ")"))

  # 要望①: cluster番号なし版も追加で出力
  # ggsave(file.path(sub_od, "umap_cluster_nolabel.png"), p1, width=7, height=6)

  # 要望①: クラスター番号を付与
  um_emb <- as.data.frame(Embeddings(obj, reduction = "umap"))
  # Seuratのバージョン/Key設定により列名が UMAP_1/UMAP_2 でない場合があるため、ここで確実に統一
  if (ncol(um_emb) >= 2) {
    colnames(um_emb)[1:2] <- c("UMAP_1", "UMAP_2")
  }
  um_emb$cluster <- as.character(Idents(obj))
  cent <- um_emb %>% group_by(cluster) %>% summarise(UMAP_1 = median(UMAP_1), UMAP_2 = median(UMAP_2), .groups = "drop")
  p1 <- p1 + ggrepel::geom_text_repel(
    data = cent, aes(x = UMAP_1, y = UMAP_2, label = cluster),
    inherit.aes = FALSE, size = 4, fontface = "bold", color = "black", seed = 1,
    box.padding = 0.4, point.padding = 0.2, show.legend = FALSE
  )
  # ggsave(file.path(sub_od, "umap_cluster.png"), p1, width=7, height=6)
  # UMAP Split (Condition/Sample) - "umap_by_sample_xxx" は廃止(要望④)
  # 条件別分割プロットのみ残す（要望①: クラスター番号付与）
  if("condition" %in% colnames(obj@meta.data)) {
    conds <- unique(obj@meta.data$condition)
    p_list <- list()
    p_list_nolabel <- list()
    for (cd in conds) {
      cells_cd <- rownames(obj@meta.data)[obj@meta.data$condition == cd]
      if(length(cells_cd) == 0) next
      sub_cd <- subset(obj, cells = cells_cd)
      ensure_global_palette(sub_cd)

      # base (no cluster number)
      p_cd_base <- DimPlot(sub_cd, reduction="umap", group.by="seurat_clusters", cols=CLUSTER_PAL) + ggtitle(as.character(cd))
      p_list_nolabel[[as.character(cd)]] <- p_cd_base

      # labeled (existing behavior)
      p_cd <- p_cd_base
      um_cd <- as.data.frame(Embeddings(sub_cd, reduction = "umap"))
      # Seuratのバージョン/Key設定により列名が UMAP_1/UMAP_2 でない場合があるため、ここで確実に統一
      if (ncol(um_cd) >= 2) {
        colnames(um_cd)[1:2] <- c("UMAP_1", "UMAP_2")
      }
      um_cd$cluster <- as.character(Idents(sub_cd))
      cent_cd <- um_cd %>% group_by(cluster) %>% summarise(UMAP_1 = median(UMAP_1), UMAP_2 = median(UMAP_2), .groups = "drop")
      p_cd <- p_cd + ggrepel::geom_text_repel(
        data = cent_cd, aes(x = UMAP_1, y = UMAP_2, label = cluster),
        inherit.aes = FALSE, size = 3.5, fontface = "bold", color = "black", seed = 1,
        box.padding = 0.35, point.padding = 0.15, show.legend = FALSE
      )
      p_list[[as.character(cd)]] <- p_cd
    }
    if(length(p_list) > 0) {
      p2 <- wrap_plots(p_list, nrow=1) + plot_annotation(title="Split by Condition")
      ggsave(file.path(sub_od, "umap_split_condition.png"), p2, width=max(12, 6*length(p_list)), height=4)

      # 要望①: cluster番号なし版も追加で出力
      p2_nl <- wrap_plots(p_list_nolabel[names(p_list)], nrow=1) + plot_annotation(title="Split by Condition (no labels)")
      # ggsave(file.path(sub_od, "umap_split_condition_nolabel.png"), p2_nl, width=max(12, 6*length(p_list)), height=4)
    }
  }
  
  # Spatial Clusters (All)
  plot_spatial_clusters_all_samples(obj, prefix, sub_od, "spatial_all_")
  save_spatial_per_group(obj, sub_od, "spatial_single_", prefix)
  
  # 2. マーカー抽出
  DefaultAssay(obj) <- "Spatial"
  obj <- JoinLayers(obj)
  
  
cat("  Finding Markers...\n")

# (Add) Volcano/DEG resume: try reading saved DEG RDS first (requirement ②).
# If available and readable, we reuse it to regenerate volcano plots without re-running FindAllMarkers.
deg <- NULL
if (exists("RESUME_FROM_RDS", envir = .GlobalEnv) && isTRUE(get("RESUME_FROM_RDS", envir = .GlobalEnv))) {
  deg_rds_name <- paste0("deg_FindAllMarkers_raw_", prefix, ".rds")
  cand <- c()
  if (exists("RESUME_DIR_PATH", envir = .GlobalEnv) && nzchar(get("RESUME_DIR_PATH", envir = .GlobalEnv))) {
    cand <- c(cand, file.path(get("RESUME_DIR_PATH", envir = .GlobalEnv), deg_rds_name))
  }
  if (exists("RDS_SAVE_DIR", envir = .GlobalEnv) && nzchar(get("RDS_SAVE_DIR", envir = .GlobalEnv))) {
    cand <- c(cand, file.path(get("RDS_SAVE_DIR", envir = .GlobalEnv), deg_rds_name))
  }
  for (fp_rds in cand) {
    if (file.exists(fp_rds)) {
      message(">> RESUME: Loading DEG for volcano from RDS: ", fp_rds)
      deg_try <- tryCatch(readRDS(fp_rds), error = function(e) NULL)
      if (!is.null(deg_try) && is.data.frame(deg_try) && nrow(deg_try) > 0) {
        deg <- deg_try
        break
      }
    }
  }
}

if (is.null(deg)) {
  # ---- 並列化開始: FindAllMarkers用 ----
  plan(multisession, workers = min(4, max(1, parallel::detectCores(logical = FALSE) - 1)))
  deg <- FindAllMarkers(obj, only.pos=FALSE, min.pct=DEG_MIN_PCT_VAL, logfc.threshold=DEG_LOGFC_TH_VAL, test.use="wilcox")
  # ---- 並列化終了: メモリ解放 ----
  plan(sequential)
  }
  # BH/FDR補正に置換（Seuratデフォルトの Bonferroni は探索的解析に保守的すぎるため）
  deg$p_val_adj <- p.adjust(deg$p_val, method = "BH")

  if(nrow(deg) == 0) {
    cat("  No markers found.\n")
    return(invisible(NULL))
  }


# === (Add) Save raw DEG (FindAllMarkers) as RDS into RDS_Files (requirement ①) ===
# Save BEFORE annotation so this is truly the "raw" DEG table.
if (exists("RDS_SAVE_DIR", envir = .GlobalEnv)) {
  tryCatch({
    saveRDS(deg, file.path(get("RDS_SAVE_DIR", envir = .GlobalEnv),
                           paste0("deg_FindAllMarkers_raw_", prefix, ".rds")))
  }, error = function(e) {
    message("!! saveRDS failed for deg: ", e$message)
  })
}
  
  # 3. アノテーション
  cat("  Annotating...\n")
  if (!is.null(ann_db)) {
    deg$annotation <- annotate_mz_with_format(unique(deg$gene), ann_db, TOLERANCE_MZ, ion_mode = ION_MODE, adduct_patterns = ANNOT_ADDUCT_PATTERNS)[deg$gene]
  } else {
    deg$annotation <- deg$gene
  }
  # ver4: マーカー表はピクセル単位の探索的ランキング（空間自己相関未補正・群間検定ではない）である旨を明記
  deg_csv <- deg
  deg_csv$ranking_type   <- "exploratory_pixel_level"
  deg_csv$inference_note <- "Exploratory pixel-level ranking; spatial autocorrelation not modeled; NOT sample-level statistical inference"
  write.csv(deg_csv, file.path(sub_od, "markers_annotated.csv"), row.names=FALSE)
  
  # 4. Heatmap (要望②: 全手法で出力)
  cat("  Generating Heatmap...\n")
  top5 <- deg %>% group_by(cluster) %>% top_n(HEATMAP_TOPN_PER_CLUSTER, wt=avg_log2FC)
  top_genes <- unique(top5$gene)
  if(length(top_genes) > 0) {
    # ランダムサンプリングで軽量化
    cells_sub <- sample(Cells(obj), min(ncol(obj), 1000))

    # Heatmap の y軸ラベル: annotation が付く場合は化合物名、付かない場合は m/z を表示
    .format_mz_only_heat <- function(x) {
      v <- suppressWarnings(as.numeric(gsub("[^0-9.]", "", x)))
      ifelse(is.na(v), as.character(x), formatC(v, format="f", digits=3))
    }
    mz_only_heat <- setNames(.format_mz_only_heat(top_genes), top_genes)

    ann_tbl <- deg %>%
      dplyr::select(gene, annotation) %>%
      dplyr::distinct()

    ann_vec <- setNames(as.character(ann_tbl$annotation), as.character(ann_tbl$gene))
    ann_for_top <- ann_vec[top_genes]

    .is_missing_annot_h <- is.na(ann_for_top) | !nzchar(as.character(ann_for_top))
    .is_hit_format_h <- grepl("_", as.character(ann_for_top), fixed = TRUE)
    heat_labels <- ifelse(.is_missing_annot_h | !.is_hit_format_h, mz_only_heat[top_genes], as.character(ann_for_top))
    heat_label_map <- setNames(heat_labels, top_genes)

    # ④ downstream: 再利用する reduction RDS は DietSeurat で scale.data を落としているため、
    #   DoHeatmap 用に heatmap 対象 feature の scale.data をその場で補完する（DESI v16 と同様）。
    #   ヒートマップは画像保存されない補助計算のため、失敗しても解析全体は止めない（tryCatch）。
    hm <- tryCatch({
      obj_h <- ScaleData(subset(obj, cells=cells_sub), features=top_genes, assay="Spatial", verbose=FALSE)  # slim RDS/diet で空の scale.data を補完（DoHeatmap 用）
      DoHeatmap(obj_h, features=top_genes, group.by="ident", size=3) +
        scale_fill_gradientn(colors=c("blue", "white", "red")) +
        scale_y_discrete(labels = heat_label_map) +
        ggtitle(paste0("Top 5 Markers (", prefix, ")"))
    }, error = function(e) {
      message("!! Heatmap skipped (scale.data/DoHeatmap): ", conditionMessage(e)); NULL
    })
    # ggsave(file.path(sub_od, "heatmap_top5.png"), hm, width=12, height=10)

    # 要望②: Average Heatmap（クラスタ平均）
    avg_mat <- tryCatch({
      AverageExpression(obj, features = top_genes, group.by = "seurat_clusters", assays = "Spatial")$Spatial
    }, error = function(e) {
      NULL
    })
    if(!is.null(avg_mat)) {
      # rownames を Heatmap と同じ表示ルールに置換（annotation がなければ m/z）
      rn0 <- rownames(avg_mat)
      if (!is.null(rn0)) {
        rn_new <- heat_label_map[rn0]
        rn_new[is.na(rn_new) | !nzchar(rn_new)] <- rn0[is.na(rn_new) | !nzchar(rn_new)]
        rownames(avg_mat) <- rn_new
      }
      # png(file.path(sub_od, "heatmap_top5_average.png"), width=1800, height=1200, res=200)
      # pheatmap::pheatmap(as.matrix(avg_mat), scale="row",
      #                    main=paste0("Average Heatmap (", prefix, ")"))
      # dev.off()
    }
  }
  
  # 5. Volcano Plot (要望③: Volcano_Plots_Mz は m/z のみ / 注釈付きは別出力)
  
  # 5. Volcano Plot (m/z only & annotated)
  cat("  Volcano Plots...\n")
  vol_dir_mz <- file.path(sub_od, "Volcano_Plots_Mz")
  vol_dir_annot <- file.path(sub_od, "Volcano_Plots_Annotated")
  dir.create(vol_dir_mz, showWarnings=FALSE)
  dir.create(vol_dir_annot, showWarnings=FALSE)

  .format_mz_only <- function(x) {
    v <- suppressWarnings(as.numeric(gsub("[^0-9.]", "", x)))
    ifelse(is.na(v), as.character(x), formatC(v, format="f", digits=3))
  }
  deg$mz_only <- .format_mz_only(deg$gene)

  # ---- (追加RDS) PlotData / DEG派生テーブル保存（Volcano/Top5再描画用）----
  deg_plot <- deg

  # p_val_adj == 0 対策（全体で一括）
  if ("p_val_adj" %in% colnames(deg_plot) && any(deg_plot$p_val_adj == 0, na.rm = TRUE)) {
    min_nz <- suppressWarnings(min(deg_plot$p_val_adj[deg_plot$p_val_adj > 0], na.rm = TRUE))
    if (is.finite(min_nz)) {
      deg_plot$p_val_adj[deg_plot$p_val_adj == 0] <- min_nz * 0.1
    } else {
      deg_plot$p_val_adj[deg_plot$p_val_adj == 0] <- .Machine$double.xmin
    }
  }
  if ("p_val_adj" %in% colnames(deg_plot)) {
    deg_plot$log_p <- -log10(pmax(deg_plot$p_val_adj, .Machine$double.xmin))
  }

  # Volcano色分け（全体で一括）
  deg_plot$color <- "NO"
  if (all(c("p_val_adj","avg_log2FC") %in% colnames(deg_plot))) {
    deg_plot$color[deg_plot$p_val_adj < DEG_P_THRESH_VAL & deg_plot$avg_log2FC >  DEG_LOGFC_TH_VAL] <- "UP"
    deg_plot$color[deg_plot$p_val_adj < DEG_P_THRESH_VAL & deg_plot$avg_log2FC < -DEG_LOGFC_TH_VAL] <- "DOWN"
  }

  # 注釈（DB一致しない場合は m/z を使う）
  if ("annotation" %in% colnames(deg_plot)) {
    .is_missing_annot0 <- is.na(deg_plot$annotation) | !nzchar(as.character(deg_plot$annotation))
    .is_hit_format0 <- grepl("_", as.character(deg_plot$annotation), fixed = TRUE)
    deg_plot$annot_or_mz <- ifelse(.is_missing_annot0 | !.is_hit_format0, deg_plot$mz_only, as.character(deg_plot$annotation))
  } else {
    deg_plot$annot_or_mz <- deg_plot$mz_only
  }

  # 追加RDS: plotdata & pixel_table
  .save_plotdata_and_pixel_table(obj, deg_plot, prefix, sub_od)

  # 追加RDS: DEGの加工結果（resume用）
  if (exists("RDS_SAVE_DIR", envir = .GlobalEnv) && nzchar(get("RDS_SAVE_DIR", envir = .GlobalEnv))) {
    # padj<0.05 のみ
    if ("p_val_adj" %in% colnames(deg_plot)) {
      deg_sig <- deg_plot[deg_plot$p_val_adj < DEG_P_THRESH_VAL, , drop = FALSE]
      .safe_saveRDS(deg_sig, file.path(get("RDS_SAVE_DIR", envir = .GlobalEnv),
                                       paste0("deg_sig_", prefix, "_padj", DEG_P_THRESH_VAL, ".rds")))
    }
    # Top5 (UP/DOWN)
    if (all(c("cluster","p_val_adj","avg_log2FC") %in% colnames(deg_plot))) {
      topN <- LABEL_TOP_N_EACH
      pth  <- DEG_P_THRESH_VAL
      tmp2 <- deg_plot[deg_plot$p_val_adj < pth & is.finite(deg_plot$avg_log2FC), , drop = FALSE]
      if (nrow(tmp2) > 0) {
        top_up2 <- tmp2 %>% dplyr::group_by(cluster) %>% dplyr::arrange(dplyr::desc(avg_log2FC)) %>% dplyr::slice_head(n = topN)
        top_dn2 <- tmp2 %>% dplyr::group_by(cluster) %>% dplyr::arrange(avg_log2FC)              %>% dplyr::slice_head(n = topN)
        top_tbl2 <- dplyr::bind_rows(dplyr::mutate(top_up2, direction="UP"),
                                     dplyr::mutate(top_dn2, direction="DOWN"))
        .safe_saveRDS(top_tbl2, file.path(get("RDS_SAVE_DIR", envir = .GlobalEnv),
                                          paste0("deg_top", topN, "_", prefix, "_padj", pth, ".rds")))
      }
    }
  }

  if (generate_mz_only) {
    write.csv(deg %>% dplyr::select(-annotation),
              file.path(sub_od, "markers_mz_only.csv"), row.names=FALSE)
  }

  for (cl in unique(deg$cluster)) {
    df_sub <- deg[deg$cluster == cl, ]
    if (nrow(df_sub) == 0) next

    # p値0対策
    # SeuratのDEGで p_val_adj が極端に小さいと、double精度の下限で 0 に丸め込まれます。
    # そのまま -log10(0) にすると Inf になるため、0 の部分だけ「doubleで表現できる最小正の値」に寄せます。
    # ※ doubleの理論上の上限は ~308 (-log10(.Machine$double.xmin)) 付近で、これ以上は数値的に表現できません。
    if (any(df_sub$p_val_adj == 0, na.rm=TRUE)) {
      min_nz <- suppressWarnings(min(df_sub$p_val_adj[df_sub$p_val_adj > 0], na.rm=TRUE))
      if (is.finite(min_nz)) {
        df_sub$p_val_adj[df_sub$p_val_adj == 0] <- min_nz * 0.1
      } else {
        df_sub$p_val_adj[df_sub$p_val_adj == 0] <- .Machine$double.xmin
      }
    }
    df_sub$log_p <- -log10(pmax(df_sub$p_val_adj, .Machine$double.xmin))
    # df_sub$log_p[df_sub$log_p > VOLCANO_Y_CAP] <- VOLCANO_Y_CAP  # (PATCH) capを外し、Y軸maxはデータに従い100を超えても表示する

    df_sub$color <- "NO"
    df_sub$color[df_sub$p_val_adj < DEG_P_THRESH_VAL & df_sub$avg_log2FC >  DEG_LOGFC_TH_VAL] <- "UP"
    df_sub$color[df_sub$p_val_adj < DEG_P_THRESH_VAL & df_sub$avg_log2FC < -DEG_LOGFC_TH_VAL] <- "DOWN"

    # ラベル用トップ抽出
    top_hits <- rbind(
      df_sub %>% dplyr::filter(color=="UP")   %>% dplyr::arrange(dplyr::desc(avg_log2FC)) %>% head(LABEL_TOP_N_EACH),
      df_sub %>% dplyr::filter(color=="DOWN") %>% dplyr::arrange(avg_log2FC)              %>% head(LABEL_TOP_N_EACH)
    )
    df_sub$label_mz <- ifelse(df_sub$gene %in% top_hits$gene, df_sub$mz_only, NA)

    # 注釈付きラベルは、DB一致せず annotation を返せない場合は m/z を表示する
    .is_missing_annot <- is.na(df_sub$annotation) | !nzchar(as.character(df_sub$annotation))
    # annotate_mz_with_format の「ヒットしたとき」は "Compound Adduct_<mz>" 形式になる前提
    .is_hit_format <- grepl("_", as.character(df_sub$annotation), fixed = TRUE)
    df_sub$annot_or_mz <- ifelse(.is_missing_annot | !.is_hit_format, df_sub$mz_only, as.character(df_sub$annotation))
    df_sub$label_annot <- ifelse(df_sub$gene %in% top_hits$gene, df_sub$annot_or_mz, NA)

    # 要望③: Volcano Y軸のmax値をデータから自動算出して固定（cluster間で縮尺がブレて見えにくいのを防ぐ）
    y_max <- suppressWarnings(max(df_sub$log_p, na.rm = TRUE))
    if (!is.finite(y_max) || is.na(y_max)) y_max <- 1
    y_max <- y_max * 1.05

    # --- m/z only ---
    p_mz <- ggplot(df_sub, aes(x=avg_log2FC, y=log_p, col=color)) +
      geom_point(alpha=0.6, size=1.0) +
      scale_color_manual(values=c("UP"="red", "DOWN"="blue", "NO"="gray")) +
      geom_vline(xintercept=c(-DEG_LOGFC_TH_VAL, DEG_LOGFC_TH_VAL), linetype="dashed", color="gray40") +
      geom_hline(yintercept=-log10(DEG_P_THRESH_VAL), linetype="dashed", color="gray40") +
      ggrepel::geom_text_repel(aes(label=label_mz), size=3,
                              max.overlaps=20, box.padding=0.5, point.padding=0.3, force=10,
                              show.legend=FALSE) +
      theme_minimal() +
      labs(title=paste0("Cluster ", cl, " (", prefix, ")"),
           subtitle="Exploratory pixel-level ranking — not sample-level inference",
           x="Log2FC", y="-Log10 P-adj") +
      coord_cartesian(ylim = c(0, y_max))

    # ggsave(file.path(vol_dir_mz, paste0("volcano_cluster_", cl, "_mz.png")),
    #        p_mz, width=7, height=6, dpi=300)

    # --- annotated ---
    p_annot <- ggplot(df_sub, aes(x=avg_log2FC, y=log_p, col=color)) +
      geom_point(alpha=0.6, size=1.0) +
      scale_color_manual(values=c("UP"="red", "DOWN"="blue", "NO"="gray")) +
      geom_vline(xintercept=c(-DEG_LOGFC_TH_VAL, DEG_LOGFC_TH_VAL), linetype="dashed", color="gray40") +
      geom_hline(yintercept=-log10(DEG_P_THRESH_VAL), linetype="dashed", color="gray40") +
      ggrepel::geom_text_repel(aes(label=label_annot), size=2.8,
                              max.overlaps=20, box.padding=0.5, point.padding=0.3, force=10,
                              show.legend=FALSE) +
      theme_minimal() +
      labs(title=paste0("Cluster ", cl, " (", prefix, ")"),
           subtitle="Exploratory pixel-level ranking — not sample-level inference",
           x="Log2FC", y="-Log10 P-adj") +
      coord_cartesian(ylim = c(0, y_max))

    # ggsave(file.path(vol_dir_annot, paste0("volcano_cluster_", cl, "_annot.png")),
    #        p_annot, width=7, height=6, dpi=300)
  }

# 5b. MSI Images (要望⑥: Volcano Top5 の MSI 画像出力を復活)
  cat("  MSI Images (Top5)...\n")
  msi_dir <- file.path(sub_od, "Cluster_Top5_MSI")
  dir.create(msi_dir, showWarnings=FALSE)
  for (cl in unique(deg$cluster)) {
    targets <- rbind(
      deg %>% filter(cluster==cl, p_val_adj<DEG_P_THRESH_VAL, avg_log2FC>0) %>% arrange(desc(avg_log2FC)) %>% head(LABEL_TOP_N_EACH),
      deg %>% filter(cluster==cl, p_val_adj<DEG_P_THRESH_VAL, avg_log2FC<0) %>% arrange(avg_log2FC) %>% head(LABEL_TOP_N_EACH)
    )
    if(nrow(targets)==0) next
    titles <- paste0(targets$annotation, " ", ifelse(targets$avg_log2FC>0,"(UP)","(DOWN)"),
                     "\nP=", formatC(targets$p_val_adj, format="e", digits=2))
    plts <- lapply(seq_len(nrow(targets)), function(i) plot_msi_tile(obj, feature = targets$gene[i], title = titles[i]))

    # ============================================================
    # PATCH (Top5 MSI):
    # ① カラーバー(凡例)は「右下の1枚」だけ表示し、サイズも小さくする
    # ② 画像間にパイプ（仕切り線）を入れる（Top5 MSI出力に限定）
    # ============================================================

    # ---- 右下(=最後の1枚)以外は凡例を消す / 右下は凡例を小さく ----
    n_pl <- length(plts)
    plts <- lapply(seq_along(plts), function(i) {
      if (i != n_pl) {
        plts[[i]] + theme(legend.position = "none")
      } else {
        plts[[i]] +
          theme(
            legend.position = "right",
            legend.key.height = grid::unit(2.2, "mm"),
            legend.key.width  = grid::unit(2.2, "mm"),
            legend.title = element_text(size = 6),
            legend.text  = element_text(size = 6),
            legend.margin = margin(0, 0, 0, 0, unit = "mm")
          )
      }
    })

    # ---- パイプ（仕切り線）用の細いプロット ----
    .pipe_v <- function() {
      ggplot() +
        geom_segment(aes(x = 0.5, xend = 0.5, y = 0, yend = 1), linewidth = 0.4, color = "black") +
        coord_cartesian(xlim = c(0, 1), ylim = c(0, 1), expand = FALSE) +
        theme_void() +
        theme(plot.margin = margin(0, 0, 0, 0, unit = "mm"),
              plot.background = element_rect(fill = "white", colour = NA))
    }
    .pipe_h <- function() {
      ggplot() +
        geom_segment(aes(x = 0, xend = 1, y = 0.5, yend = 0.5), linewidth = 0.4, color = "black") +
        coord_cartesian(xlim = c(0, 1), ylim = c(0, 1), expand = FALSE) +
        theme_void() +
        theme(plot.margin = margin(0, 0, 0, 0, unit = "mm"),
              plot.background = element_rect(fill = "white", colour = NA))
    }

    # ---- 2行(UP/DOWN)×5列を想定（nrow=2, ncol=LABEL_TOP_N_EACH） ----
    row1 <- plts[1:LABEL_TOP_N_EACH]
    row2 <- plts[(LABEL_TOP_N_EACH + 1):(2 * LABEL_TOP_N_EACH)]

    # 行ごとに「plot | pipe | plot | ...」へ
    vpipe <- .pipe_v()
    hpipe <- .pipe_h()

    .interleave_with_vpipes <- function(row_plots) {
      pieces <- list()
      for (j in seq_along(row_plots)) {
        pieces[[length(pieces) + 1]] <- row_plots[[j]]
        if (j < length(row_plots)) pieces[[length(pieces) + 1]] <- vpipe
      }
      widths <- rep(1, length(row_plots) * 2 - 1)
      widths[seq(2, length(widths), by = 2)] <- 0.035  # パイプ幅
      patchwork::wrap_plots(pieces, nrow = 1) + patchwork::plot_layout(widths = widths)
    }

    p_row1 <- .interleave_with_vpipes(row1)
    p_row2 <- .interleave_with_vpipes(row2)

    p_comb <- (p_row1 / hpipe / p_row2) +
      plot_annotation(title=paste0("Cluster ", cl, " Top", LABEL_TOP_N_EACH, " UP/DOWN (", prefix, ")")) +
      patchwork::plot_layout(heights = c(1, 0.035, 1))

    # ggsave(file.path(msi_dir, paste0("MSI_Cluster_", cl, ".png")), p_comb, width=18, height=9, bg="white")
  }

# 6. TIC Overlay (要望⑤)
  cat("  TIC Overlay...\n")
  export_cluster_highlights(obj, prefix, outdir)
  
  # 7. クラスタ付きファイルの書き出し (CSV / Parquet 両対応; prefix付きで上書き防止)
  if(!is.null(INPUT_PATHS)){
    for (fp in INPUT_PATHS[file.exists(INPUT_PATHS)]) {

      ext <- tolower(tools::file_ext(fp))
      sn  <- tools::file_path_sans_ext(basename(fp))

      meta <- obj@meta.data %>%
        tibble::rownames_to_column("cell") %>%
        dplyr::filter(sample==sn) %>%
        dplyr::select(spot_index, seurat_clusters)

      if(nrow(meta) == 0) next

      # ---- Case A) CSV/TSV (legacy SCiLS Transform) ----
      if (ext %in% c("csv", "txt", "tsv")) {

        # オリジナルCSVを読み込む（ヘッダー等はスキップしてデータ部分のみ）
        first_line <- readLines(fp, n = 1, warn = FALSE)
        d <- if (length(first_line) && grepl(",", first_line, fixed = TRUE)) "," else "\t"

        dat <- data.table::fread(fp, skip = 4, header = FALSE, sep = d,
                                 colClasses = "numeric", fill = TRUE,
                                 showProgress = FALSE)
        dat <- as.data.frame(dat, stringsAsFactors = FALSE)
        dat$spot <- as.numeric(dat[,1])
        if(anyNA(dat$spot)) dat$spot[is.na(dat$spot)] <- seq_len(sum(is.na(dat$spot)))

        dat <- dplyr::left_join(dat, meta %>% dplyr::mutate(spot=as.numeric(spot_index)), by="spot")

        # prefix付きで保存
        write.csv(dat, file.path(sub_od, paste0(sn, "_with_clusters_", prefix, ".csv")), row.names=FALSE)

      # ---- Case B) Parquet (analysis-friendly wide table) ----
      } else if (ext %in% c("parquet", "pq")) {

        if (!requireNamespace("arrow", quietly = TRUE)) {
          stop("Package 'arrow' is required to read/write Parquet. Please install.packages('arrow').")
        }

        df <- arrow::read_parquet(fp, as_data_frame = TRUE)

        # 必須列チェック（本パイプラインのParquet仕様: id, x, y, mz_...）
        need_cols <- c("id", "x", "y")
        miss <- setdiff(need_cols, colnames(df))
        if (length(miss) > 0) stop("Parquet is missing required columns: ", paste(miss, collapse = ", "))

        # id を spot_index とみなして join（Seurat側の spot_index は数値のことが多い）
        df2 <- df %>%
          dplyr::mutate(id_num = suppressWarnings(as.numeric(id))) %>%
          dplyr::left_join(meta %>% dplyr::mutate(spot_index = as.numeric(spot_index)),
                           by = c("id_num" = "spot_index")) %>%
          dplyr::select(-id_num)

        # クラスタ割り当てCSVのみ保存（元データとid/x/yでjoin可能）
        cluster_info <- data.frame(
          id      = df2$id,
          x       = df2$x,
          y       = df2$y,
          cluster = df2$seurat_clusters
        )
        write.csv(cluster_info,
                  file.path(sub_od, paste0(sn, "_cluster_assignment_", prefix, ".csv")),
                  row.names = FALSE)

      } else {
        message("!! Skip cluster export (unsupported input ext): ", fp)
      }
    }
  }

cat("  Done.\n")
}

# ============================================================
# ==== メイン処理開始 ======================================
# ============================================================

today <- format(Sys.Date(), "%Y%m%d")
# 出力先 = OUTPUT_DIR 直下（アプリ側でフォルダを指定済み）
od <- OUTPUT_DIR
dir.create(od, recursive = TRUE, showWarnings = FALSE)

# ★要望①: RDSファイル保存フォルダ "RDS_Files"
RDS_SAVE_DIR <- file.path(od, "RDS_Files")
dir.create(RDS_SAVE_DIR, recursive=TRUE, showWarnings=FALSE)
cat("RDS Save Dir:", RDS_SAVE_DIR, "\n")

# Resume用パス (読み込み元)
# 指定がなければ今回の保存先と同じ場所を見る
if (RESUME_FROM_RDS && (is.null(RESUME_DIR_PATH) || RESUME_DIR_PATH == "")) {
  RESUME_DIR_PATH <- RDS_SAVE_DIR
} else if (RESUME_FROM_RDS) {
  # ユーザー指定パスがある場合、そこを見る
  if(!dir.exists(RESUME_DIR_PATH)) dir.create(RESUME_DIR_PATH, recursive=TRUE, showWarnings=FALSE)
}

if (RDS_CACHE_ENABLE) dir.create(RDS_CACHE_DIR, recursive=TRUE, showWarnings=FALSE)

set.seed(GLOBAL_RANDOM_SEED)
tic("Total Analysis")

# アノテーション DB 読み込み（TraceFinder / HMDB 自動検出）
ann_db <- NULL
if (ANNOTATION_ENABLE && file.exists(ANNOTATION_CSV_PATH)) {
  ann_db <- read_annotation_db_long(ANNOTATION_CSV_PATH)
}

# ============================================================
# Step 1: データ読込 & 前処理
# ============================================================
# ④ downstream_from_reduction: ①の reduction RDS(Step2/Step3)を再利用し UMAP 以降のみ
# 実行。raw 再読込/seu_list 構築/reduction 再計算をスキップする。
.stage_downstream <- identical(PIPELINE_STAGE, "downstream_from_reduction")

rds_fname1 <- "Step1_SeuratList_Preprocessed.rds"
rds_step1_out <- file.path(RDS_SAVE_DIR, rds_fname1)
rds_step1_in  <- if(RESUME_FROM_RDS) file.path(RESUME_DIR_PATH, rds_fname1) else ""

step1_done <- FALSE
if (RESUME_FROM_RDS && file.exists(rds_step1_in)) {
  message(">> RESUME: Loading Step1 ...")
  tryCatch({
    seu_list <- load_rds_compact(rds_step1_in)
    if (length(seu_list) > 0) {
      step1_done <- TRUE
      # 読み込み成功したら今回のフォルダにもコピーして保存
      if (normalizePath(rds_step1_in) != normalizePath(rds_step1_out, mustWork=FALSE)) {
        file.copy(rds_step1_in, rds_step1_out, overwrite=TRUE)
      }
    }
  }, error = function(e) {
    message("!! Resume failed (Step1 broken?): ", e$message)
    step1_done <- FALSE
  })
}

if (!step1_done && !.stage_downstream) {
  seu_list <- list(); input_paths <- unique(INPUT_PATHS[file.exists(INPUT_PATHS)])
  fa_all <- list()  # 注釈付き列名データの per-feature メタ（化合物名/m/z/|以降）を保持
  for (fp in input_paths) {
    sn <- tools::file_path_sans_ext(basename(fp))
    dat <- read_desi_data_cached(fp, sn)
    if (!is.null(dat$feature_annotations)) {
      .fa <- dat$feature_annotations; .fa$sample <- sn
      fa_all[[length(fa_all) + 1]] <- .fa
    }
    seu <- CreateSeuratObject(counts=dat$count_matrix, project="DESI", assay="Spatial")
    seu$sample <- sn; seu$x_coord <- dat$coordinates$x; seu$y_coord <- dat$coordinates$y; seu$spot_index <- dat$coordinates$spot_index
    if ("annotation" %in% colnames(dat$coordinates)) seu$annotation <- dat$coordinates$annotation
    seu <- assign_xy_grid(seu)
    # condition は slice_id（= annotation名）をそのまま使用
seu$condition <- seu$slice_id
    if(ncol(seu)>0) seu_list[[length(seu_list)+1]] <- seu
  }

  # ---- feature_annotations を出力に保存（要件: |以降のメタ情報を保持し将来参照可能に）----
  if (length(fa_all) > 0) {
    .fa_out <- tryCatch(do.call(rbind, fa_all), error = function(e) NULL)
    if (!is.null(.fa_out)) {
      tryCatch({
        if (requireNamespace("arrow", quietly = TRUE)) {
          arrow::write_parquet(.fa_out, file.path(RDS_SAVE_DIR, "feature_annotations.parquet"))
          cat(sprintf("  [feature_annotations] saved %d rows -> %s/feature_annotations.parquet\n",
                      nrow(.fa_out), RDS_SAVE_DIR))
        } else {
          saveRDS(.fa_out, file.path(RDS_SAVE_DIR, "feature_annotations.rds"))
          cat(sprintf("  [feature_annotations] saved %d rows -> %s/feature_annotations.rds\n",
                      nrow(.fa_out), RDS_SAVE_DIR))
        }
      }, error = function(e) message("[feature_annotations] save failed: ", conditionMessage(e)))
    }
  }

  # ---- (1) キャリブレーション補正 ----
  seu_list <- calibrate_feature_names(seu_list)

  # ---- (2) m/z アライメント ----
  if (MZ_ALIGN_PPM > 0 && length(seu_list) > 1) {
    seu_list <- align_mz_features(seu_list, MZ_ALIGN_PPM)
  }

  if(SPATIAL_SMOOTH_ENABLE) {
    for(i in seq_along(seu_list)) {
      seu_list[[i]] <- spatial_smooth_seurat(seu_list[[i]], radius=SPATIAL_SMOOTH_RADIUS, sigma=SPATIAL_SMOOTH_SIGMA)
    }
  }
  
  # [P6] NormalizeData/ScaleData はStep2で再実行されるため、ここではFindVariableFeaturesのみ
  .mem_note_base("Step1 FindVariableFeatures 前")
  for(i in seq_along(seu_list)) {
    seu_list[[i]] <- FindVariableFeatures(seu_list[[i]], selection.method = "vst", nfeatures = N_VAR_FEATURES)
  }
  .mem_note_base("Step1 FindVariableFeatures 後")

  # ★要望①: Step1 完了時のRDS保存 (slim: DietSeurat + qs 圧縮)
  # [ver45.8] この RDS は「同一実行内では一度も読まれず、解析完了時に削除される」
  #   （読むのは RESUME_FROM_RDS で別ディレクトリを指した後続実行のみ / 削除は末尾 cleanup）。
  #   つまり中断時の再開専用の一時ファイルであり、通常完走時は保存コストが丸ごと無駄になる。
  #   保存は DietSeurat による複製 + qs 圧縮を伴い、10万px 級では最も重い山のひとつ。
  #   メモリ逼迫環境では SAVE_STEP1_RDS=0 でスキップできる（最終出力は一切変わらない）。
  .save_step1 <- Sys.getenv("SAVE_STEP1_RDS", unset = "1")
  if (.save_step1 %in% c("0", "false", "FALSE", "no", "NO")) {
    message(">> Step1 RDS の保存をスキップしました (SAVE_STEP1_RDS=0)。",
            "中断時の再開はできませんが、完走時の出力は同一です。")
  } else {
    save_rds_compact(seu_list, rds_step1_out)
  }
  gc()
  .mem_note_base("Step1 完了")
}
if (.stage_downstream && !step1_done) {
  # ④: Step1 RDS は①の末尾cleanupで削除済み。raw 再読込を避け seu_list を空にする
  #     （Step3 の length(seu_list) 判定に作用 → reduction は Step2/Step3 RDS から復元）。
  seu_list <- list()
}

# ============================================================
# Step 2: Harmony / PCA
# ============================================================
rds_fname2 <- "Step2_HarmonyPCA_Result.rds"
rds_step2_out <- file.path(RDS_SAVE_DIR, rds_fname2)
rds_step2_in  <- if(RESUME_FROM_RDS) file.path(RESUME_DIR_PATH, rds_fname2) else ""

step2_done <- FALSE
seu_harmony <- NULL
REDUCTION_USED <- NULL

# ---- ver4: 入力正規化ヘルパ（二重正規化の回避）----
# INPUT_NORMALIZED=TRUE のとき、既に正規化済みの counts を NORM_MODE で変換して
# 'data' layer に格納し、NormalizeData(LogNormalize) は行わない。FALSE のときは従来通り。
apply_input_norm <- function(s) {
  if (isTRUE(INPUT_NORMALIZED)) {
    asy <- DefaultAssay(s)
    # v5: 複数sampleの merge で counts が複数レイヤーに分かれている場合があるため統合
    s   <- tryCatch(JoinLayers(s), error = function(e) s)
    cm  <- LayerData(s[[asy]], layer = "counts")
    dat <- switch(NORM_MODE,
                  "none"  = cm,
                  "sqrt"  = sqrt(cm),
                  "log1p" = log1p(cm),
                  stop("NORM_MODE は 'none' / 'sqrt' / 'log1p' のいずれかにしてください"))
    s[[asy]] <- SetAssayData(s[[asy]], layer = "data", new.data = dat)
    s@misc$preprocessing_method <- paste0("RMS_input+", NORM_MODE)
    s
  } else {
    s <- NormalizeData(s)
    s@misc$preprocessing_method <- "LogNormalize"
    s
  }
}

if (RESUME_FROM_RDS && file.exists(rds_step2_in)) {
  message(">> RESUME: Loading Step2 ...")
  tryCatch({
    res_obj <- load_rds_compact(rds_step2_in)
    seu_harmony <- res_obj$obj
    REDUCTION_USED <- res_obj$reduction
    step2_done <- TRUE
    # コピー保存
    if (normalizePath(rds_step2_in) != normalizePath(rds_step2_out, mustWork=FALSE)) {
      file.copy(rds_step2_in, rds_step2_out, overwrite=TRUE)
    }
  }, error = function(e) {
    message("!! Resume failed (Step2 broken?): ", e$message)
    step2_done <- FALSE
  })
}

if (!step2_done && !.stage_downstream) {
  # [P9] 一括merge（Reduceの逐次mergeより効率的）
  cell_ids <- sapply(seu_list, function(s) s$sample[1])
  seu_merged <- merge(seu_list[[1]], y = seu_list[-1], add.cell.ids = cell_ids)
  # ---- ver4: 補正変数の決定（過補正防止）----
  # 既定では技術的バッチ(BATCH_VAR='sample')が複数ある場合のみ補正。
  # condition/slice_id（生物差）は ALLOW_CONDITION_CORRECTION=TRUE のときのみ許可し、
  # それ以外は group_var=NA（補正せず無補正PCAを使用）。
  .bv        <- if (BATCH_VAR %in% colnames(seu_merged@meta.data)) BATCH_VAR else "sample"
  .bv_levels <- length(unique(na.omit(seu_merged@meta.data[[.bv]])))
  .bv_is_bio <- (.bv %in% c("condition", "slice_id")) && !isTRUE(ALLOW_CONDITION_CORRECTION)
  group_var  <- if (.bv_levels > 1 && !.bv_is_bio) .bv else NA_character_
  if (is.na(group_var)) {
    cat(sprintf("[ver4] 技術的バッチが無いため補正をスキップ (BATCH_VAR='%s', levels=%d) -> 無補正PCAを使用\n", .bv, .bv_levels))
  } else {
    cat(sprintf("[ver4] バッチ補正変数: '%s' (levels=%d)\n", group_var, .bv_levels))
  }
  
  # 統合処理 (Harmony / PCA fallback)
  run_pipeline <- function(use_harmony, cfg) {
    # [P7] pipe→sequential（中間オブジェクトコピー削減）
    s <- apply_input_norm(seu_merged)
    s <- FindVariableFeatures(s, nfeatures = cfg$n_var_features)
    s <- ScaleData(s)
    s <- RunPCA(s, npcs = cfg$max_pcs)
    if(use_harmony) {
      s <- RunHarmony(s, group.by.vars=group_var)
    }
    # PIPELINE_STAGE=reduction_only: reduction(PCA/Harmony)計算後に即返す
    #   （UMAP/FindNeighbors/FindClusters をスキップ＝PreFlight 診断用の軽量実行）
    if (identical(PIPELINE_STAGE, "reduction_only")) {
      return(s)
    }
    red_use <- if (use_harmony) "harmony" else "pca"
    s <- RunUMAP(s, reduction=red_use, dims=1:cfg$umap_dims,
                 n.neighbors=UMAP_N_NEIGHBORS, min.dist=UMAP_MIN_DIST,
                 metric=UMAP_METRIC, seed.use=GLOBAL_RANDOM_SEED)
    s <- FindNeighbors(s, reduction=red_use, dims=1:cfg$umap_dims,
                       k.param=CLUSTER_K_PARAM, annoy.metric=CLUSTER_METRIC)
    FindClusters(s, resolution=CLUSTER_RESOLUTION, algorithm=CLUSTER_ALGORITHM)
  }
  
  # Retry Logic
  # ver4: 補正変数が無い(NA)場合は Harmony をスキップし、無補正PCAへフォールバック
  # [ver6.6] 進捗バー用の段階マーカー。reduction 構築中は一致キーワードが出ず
  #   UI が「準備中」に見えるため、"preprocessing" を1行出す（_detect_current_step が拾う）。
  cat("Preprocessing (variable features / scaling / PCA)...\n")
  if (!is.na(group_var)) {
    for (cfg in HARMONY_RETRY_GRID) {
      ok <- tryCatch({ seu_harmony <- run_pipeline(TRUE, cfg); TRUE }, error=function(e) FALSE)
      if(ok) { REDUCTION_USED <- "harmony"; break }
    }
  }
  if(is.null(seu_harmony)) {
    for (cfg in PCA_RETRY_GRID) {
      ok <- tryCatch({ seu_harmony <- run_pipeline(FALSE, cfg); TRUE }, error=function(e) FALSE)
      if(ok) { REDUCTION_USED <- "pca"; break }
    }
  }
  if(is.null(seu_harmony)) stop("All pipelines failed.")
  
  # ★要望①: Step2 完了時のRDS保存 (slim: DietSeurat + qs 圧縮)
  save_rds_compact(list(obj=seu_harmony, reduction=REDUCTION_USED), rds_step2_out, keep_counts=FALSE)  # 生counts層は保存後未使用→除去
  gc()

  # ---- 無補正PCAの併走出力（補正の妥当性を比較するため）----
  # 主結果が補正(harmony等)のときのみ出力。主結果が既に "pca" の場合は同一のため出さない。
  # [ver6.5] 旧来の run_pipeline(FALSE) による再計算を廃止し、Harmony が内部に持つ
  #   入力 pca をそのまま流用する。これで (a) 重複PCA計算が消え（時間・メモリ削減）、
  #   (b) Harmony と同一設定(同HVG/同PC数)の pca になり、補正前後の比較が公平になる。
  if (isTRUE(ALWAYS_OUTPUT_UNCORRECTED_PCA) && !identical(REDUCTION_USED, "pca")) {
    if ("pca" %in% names(seu_harmony@reductions)) {
      cat("無補正PCAは Harmony の pca を流用します -> Step2_PCA_uncorrected.rds\n")
      seu_unc <- seu_harmony                                   # copy-on-write（即時複製しない）
      for (.rn in setdiff(names(seu_unc@reductions), "pca"))   # 補正系 reduction を除去し pca のみ残す
        seu_unc[[.rn]] <- NULL                                 # 既存 RPCA 部と同じ除去イディオム
      save_rds_compact(list(obj=seu_unc, reduction="pca"),
                       file.path(RDS_SAVE_DIR, "Step2_PCA_uncorrected.rds"), keep_counts=FALSE)
      rm(seu_unc); gc()
    } else {
      message("!! 無補正PCA: seu_harmony に pca が無いためスキップ")
    }
  }
}

# ★解析実行 (Harmony or PCA)
# ★要望②, ⑥: 関数化により、後続のRPCAの結果に上書きされることなく確実に出力される
# ④: Step3 のみ読み込んだ場合 seu_harmony は NULL → スキップ
if (!is.null(seu_harmony)) {
  run_downstream_analysis(seu_harmony, REDUCTION_USED, od, ann_db)
}

# ★ver4: 無補正PCA結果の下流解析（Step2_PCA_uncorrected が存在すれば実行）
.unc_rds_path <- file.path(RDS_SAVE_DIR, "Step2_PCA_uncorrected.rds")
if (isTRUE(ALWAYS_OUTPUT_UNCORRECTED_PCA) && file.exists(.unc_rds_path)) {
  .unc_obj <- tryCatch(load_rds_compact(.unc_rds_path)$obj, error=function(e) NULL)
  if (!is.null(.unc_obj)) {
    run_downstream_analysis(.unc_obj, "pca_uncorrected", od, ann_db, generate_mz_only = FALSE)
    rm(.unc_obj); gc()
  }
}


# ============================================================
# Step 3: RPCA
# ============================================================
rds_fname3 <- "Step3_RPCA_Result.rds"
rds_step3_out <- file.path(RDS_SAVE_DIR, rds_fname3)
rds_step3_in  <- if(RESUME_FROM_RDS) file.path(RESUME_DIR_PATH, rds_fname3) else ""

step3_done <- FALSE
seu_rpca <- NULL

if (RESUME_FROM_RDS && file.exists(rds_step3_in)) {
  message(">> RESUME: Loading Step3 ...")
  tryCatch({
    res_obj <- load_rds_compact(rds_step3_in)
    seu_rpca <- res_obj$obj
    step3_done <- TRUE
    # コピー保存
    if (normalizePath(rds_step3_in) != normalizePath(rds_step3_out, mustWork=FALSE)) {
      file.copy(rds_step3_in, rds_step3_out, overwrite=TRUE)
    }
  }, error = function(e) {
    message("!! Resume failed (Step3 broken?): ", e$message)
    step3_done <- FALSE
  })
}

if (!step3_done && !.stage_downstream) {
  # ver4: RPCAは「技術的バッチ」に対してのみ実行する。
  #   - 複数sample（別測定）→ sampleごとに統合（正当なバッチ補正）
  #   - 単一sample + ANNOTATION_ROLE=="section_id"（連続切片=技術反復）→ slice_idで統合
  #   - それ以外（生物学的ROI/群; 既定）→ RPCAをスキップ（生物差を消さない）
  .n_slice         <- length(unique(na.omit(seu_harmony$slice_id)))
  .rpca_section_ok <- (ANNOTATION_ROLE == "section_id") && .n_slice >= 2
  if (!isTRUE(ENABLE_RPCA)) {
    cat("RPCA skip: ENABLE_RPCA=FALSE (Harmony/無補正PCA の結果で完了します)\n")
    seu_rpca <- NULL
  } else if (length(seu_list) >= 2 || .rpca_section_ok) {
    cat("Running RPCA (Seurat v5 IntegrateLayers)...\n")

    # ---- v5 ネイティブ統合（IntegrateLayers + RPCAIntegration; 省メモリ）----
    # v4 の FindIntegrationAnchors+IntegrateData は補正済み発現行列を実体化するため
    # 大規模(>~10万px)で OOM する。低次元PCA空間で統合し reduction だけ作る
    # IntegrateLayers に置換。新 reduction 名 "rpca"（下流/診断がそのまま採用）。
    .rpca_batch <- if (length(seu_list) >= 2) "sample" else "slice_id"

    # [ver6.x メモリ削減] Step3 に入る前に、直前段階(Harmony/無補正PCAの下流解析)が
    # 残した巨大な副産物を先に捨てる。RPCA は PCA 空間しか使わないため結果は不変。
    #   - graphs: FindNeighbors が作る近傍グラフ(セル数^2 相当のスパース)。RPCA では未使用。
    #   - reductions: RPCA は RunPCA から作り直すため後段でどのみち全除去する。subset(複製)
    #     する前に落として複製サイズ自体を小さくする（umap/harmony の embedding ぶん）。
    #   - scale.data: RPCA ブロック内の ScaleData で作り直されるため保持不要(dense で最大)。
    for (.gn in names(seu_harmony@graphs))     seu_harmony[[.gn]] <- NULL
    for (.rn in names(seu_harmony@reductions)) seu_harmony[[.rn]] <- NULL
    suppressWarnings(try(seu_harmony[["Spatial"]]$scale.data <- NULL, silent = TRUE))
    gc(verbose = FALSE)
    # [ver45.7] gc() の値は R ヒープしか見ておらず、未返却領域や Arrow プールを含む実態と
    # 乖離する。cgroup が見ているのは RSS なので、そちらに統一する。
    .mem_note <- .mem_note_base
    .mem_note("Step3 RPCA 開始前")

    # [ver6.x メモリ削減] 「元(seu_harmony)から部分集合(seu_rpca)を作ってから元を捨てる」
    # 順序だと両方が同時に載るピークが必ず出る。全セルが対象で内容が変わらないケースでは
    # 複製を作らず参照を付け替え、複製が要る場合も直後に元参照を外して即回収する。
    .sl_all  <- unique(na.omit(seu_harmony$slice_id))
    .need_ss <- any(is.na(seu_harmony$slice_id)) ||
                !all(as.character(seu_harmony$slice_id) %in% as.character(.sl_all))
    if (.need_ss) {
      seu_rpca <- tryCatch(
        subset(seu_harmony, subset = slice_id %in% .sl_all),
        error = function(e) seu_harmony)
      rm(seu_harmony); gc(verbose = FALSE)   # 複製直後に元を解放（二重保持の窓を最小化）
    } else {
      seu_rpca <- seu_harmony                # 内容同一 → 複製せず付け替え（copy-on-write）
      rm(seu_harmony)                        # 参照を1本にしてから以降の破壊的変更を行う
    }
    DefaultAssay(seu_rpca) <- "Spatial"
    # Step2 由来の reduction(harmony 等)を除去（run_downstream の harmony 優先採用を回避）。
    for (.rn in names(seu_rpca@reductions)) seu_rpca[[.rn]] <- NULL
    gc(verbose = FALSE)
    .mem_note("Step3 RPCA 入力確定後")

    .bt   <- as.character(seu_rpca@meta.data[[.rpca_batch]])
    .keep <- names(which(table(.bt) >= MIN_CELLS_RPCA))
    if (length(.keep) >= 2) {
      seu_rpca <- subset(seu_rpca, cells = colnames(seu_rpca)[.bt %in% .keep])
      .kw <- max(5L, min(100L, as.integer(min(table(as.character(seu_rpca@meta.data[[.rpca_batch]])))) - 1L))
      seu_rpca <- apply_input_norm(seu_rpca)
      # [ver6.x メモリ削減] 旧 scale.data 層は split 対象外＆後段 ScaleData で再計算されるため、
      # split 前に破棄して dense 行列ぶんのピークを下げる（結果不変）。
      suppressWarnings(try(seu_rpca[["Spatial"]]$scale.data <- NULL, silent = TRUE))
      gc(verbose = FALSE)
      seu_rpca[["Spatial"]] <- split(seu_rpca[["Spatial"]], f = seu_rpca@meta.data[[.rpca_batch]])
      gc(verbose = FALSE)  # split 直後の一時メモリを早期解放

      .mem_note("Step3 split 完了・統合開始前")

      ok <- FALSE
      for (nf in c(2000L, 1000L, 500L)) {
        cat(sprintf("  [RPCA] IntegrateLayers 試行: nfeatures=%d, k.weight=%d\n", nf, .kw))
        ok <- tryCatch({
          seu_rpca <- FindVariableFeatures(seu_rpca, nfeatures = nf, verbose = FALSE)
          seu_rpca <- ScaleData(seu_rpca, verbose = FALSE)
          seu_rpca <- RunPCA(seu_rpca, npcs = MAX_PCS, verbose = FALSE)
          # この一手だけ future.globals 上限を一時解除（plan=sequential のため複製なし）。
          # 成功/失敗いずれでも finally で必ず元（4GB）へ戻す（:69 の全域既定は不変）。
          .old_gmax <- getOption("future.globals.maxSize")
          options(future.globals.maxSize = RPCA_FGLOBALS_MAXSIZE)
          seu_rpca <- tryCatch(
            IntegrateLayers(seu_rpca, method = RPCAIntegration,
                            orig.reduction = "pca", new.reduction = "rpca",
                            assay = "Spatial", dims = 1:MAX_PCS,
                            k.weight = .kw, verbose = FALSE),
            finally = options(future.globals.maxSize = .old_gmax)
          )
          seu_rpca <- JoinLayers(seu_rpca)
          if (!identical(PIPELINE_STAGE, "reduction_only")) {
            .rd <- 1:min(UMAP_DIMS_MAX, ncol(Embeddings(seu_rpca, "rpca")))
            seu_rpca <- RunUMAP(seu_rpca, reduction = "rpca", dims = .rd,
                                n.neighbors = UMAP_N_NEIGHBORS, min.dist = UMAP_MIN_DIST,
                                metric = UMAP_METRIC, seed.use = GLOBAL_RANDOM_SEED)
            seu_rpca <- FindNeighbors(seu_rpca, reduction = "rpca", dims = .rd,
                                      k.param = CLUSTER_K_PARAM, annoy.metric = CLUSTER_METRIC)
            seu_rpca <- FindClusters(seu_rpca, resolution = CLUSTER_RESOLUTION, algorithm = CLUSTER_ALGORITHM)
          }
          TRUE
        }, error = function(e) { message("!! RPCA(IntegrateLayers) failed: ", e$message); FALSE })
        if (ok) break
      }
      if (!ok) seu_rpca <- NULL
    } else {
      cat(sprintf("RPCA skip: 有効バッチ<2 (batch='%s', >=%d cells)\n", .rpca_batch, MIN_CELLS_RPCA))
      seu_rpca <- NULL
    }
  }
  
  # ★要望①: Step3 完了時のRDS保存 (slim: DietSeurat + qs 圧縮)
  # RPCA がスキップ/失敗で NULL のときは空ファイル list(obj=NULL) を作らない。
  # （空RDSがあると PreFlight 診断が「reduction が検出されませんでした」と誤表示する。
  #   下流は直後の if(!is.null(seu_rpca)) で既にガード済み。RESUME は file.exists で安全。）
  if (!is.null(seu_rpca)) {
    save_rds_compact(list(obj=seu_rpca), rds_step3_out, keep_counts=FALSE)  # 生counts層は保存後未使用→除去
  }
  gc()
}

# ★解析実行 (RPCA)
# ★要望②, ⑥: Harmonyとは独立して実行されるため、確実に結果が出る
if(!is.null(seu_rpca)) {
  run_downstream_analysis(seu_rpca, "rpca", od, ann_db, generate_mz_only = FALSE)
}

# ---- Cleanup: Step1 RDS（解析完了後は不要） ----
if (file.exists(rds_step1_out)) {
  message(">> Cleanup: Step1 RDS を削除しました（解析完了のため不要）")
  file.remove(rds_step1_out)
}

cat("\nAll Done -> ", od, "\n")

# ---- 並列化終了: sequential に戻す ----
plan(sequential)
toc()

# --- 解析レシート: R サイドカー出力（rds_io.R で定義、防御的・失敗しても無害）---
if (exists("write_receipt_sidecar")) try(write_receipt_sidecar(), silent = TRUE)
