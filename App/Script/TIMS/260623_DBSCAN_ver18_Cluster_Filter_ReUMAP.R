# -*- coding: UTF-8 -*-
# ============================================================
# ClusterFilter_ReUMAP for 260125_DBSCAN_With_cluster_ver14.R
#   (Seurat RDS -> cluster exclude/keep -> filtered inputs export -> (optional) re-run ver13)
#   ver14: マージスクリプト自動呼出機能を追加
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

# ---- [ver45.7 計測] プロセス実使用量(RSS)の記録 ----
# これまでメモリを推測で議論してきたため、実測値をログに残す。
# RSS はプロセスが実際に確保している物理メモリで、cgroup(mem_limit) が見ているのもこれ。
# R ヒープ(gc)の値と違い、Arrow のメモリプールや未返却領域も含むため実態に一致する。
# /proc/self/status を読むだけなので依存追加なし。取得できない環境では静かに諦める。
.rss_gb <- function() {
  tryCatch({
    ln <- grep("^VmRSS:", readLines("/proc/self/status", warn = FALSE), value = TRUE)
    if (length(ln) == 0) return(NA_real_)
    as.numeric(sub("^VmRSS:\\s*([0-9]+)\\s*kB.*$", "\\1", ln[1])) / 1024^2
  }, error = function(e) NA_real_)
}
.mem_note_orch <- function(tag) {
  cat(sprintf("[mem/orch] %s: RSS %.2f GB\n", tag, .rss_gb()))
  flush(stdout())
}

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
# まずは「必須」だけ設定すれば動きます。
# それ以外は（必要になった時だけ）下の「任意/上級」から追加で触ってください。
# ------------------------------------------------------------

# ========== 必須 ==========
# (A) ベーススクリプト（ver15: キャリブレーション対応）のパス
V13_SCRIPT_PATH <- "C:\\Users\\Cciia\\Biochem Dropbox\\木津亮馬\\UMAP_Claudecode\\data\\TIMS\\Script\\260308_DBSCAN_With_cluster_ver16.R"

# (B) ver13 出力フォルダ（RDS_Files を含むフォルダ、または RDS_Files フォルダ自体）
# 例1) ...\\<PROJECT>_<YYYYMMDD>                （この中に RDS_Files がある）
# 例2) ...\\<PROJECT>_<YYYYMMDD>\\RDS_Files    （RDS_Files を直接指定）
RDS_RUN_DIR <- "C:\\Users\\Cciia\\Biochem Dropbox\\木津亮馬\\MSI_Tims\\SCiLS_Transform\\Data\\260207_HCC_Transform\\260207_HCC_1__20260207\\RDS_Files"

# (C) どの結果のクラスタを使って抽出/除外するか
#   - "harmony" : Step2_HarmonyPCA_Result.rds を参照
#   - "rpca"    : Step3_RPCA_Result.rds を参照
CLUSTER_SOURCE <- "harmony"

# (D) 旧仕様互換: RDS をファイルで直接指定したい場合（指定すると RDS_RUN_DIR/CLUSTER_SOURCE を無視）
#   - 通常は空("")のままでOK
RDS_PATH <- ""

# (E) 入力ファイル（ver13 の INPUT_PATHS と同じでなくてもOK：下のマッピング/xy対応で吸収できます）
#     Parquet(.parquet/.pq) / CSV(.csv) / TSV(.tsv/.txt) を混在させてもOK
ORIGINAL_INPUT_PATHS <- c(
  "C:\\Users\\Cciia\\Biochem Dropbox\\木津亮馬\\MSI_Tims\\SCiLS_Transform\\Data\\260207_HCC_Transform\\260207_HCC_Transform.parquet"
)


# (E2) [NEW] 入力ファイル名が RDS の meta.data$sample と一致しない場合のマッピング
#   - names() に「入力ファイル側の sample 名（= basename sans ext）」、
#     値に「RDS 側 sample 名」を入れます。
#   - 例: c("test-250809_Kizu_H2-18O_Brain_Transform_v2" = "test-250809_Kizu_H2-18O_Brain_Transform")
SAMPLE_NAME_MAP <- c()

# (E3) [NEW] 1つの入力ファイルを複数サンプルに紐づけたい場合（必要なときだけ）
#   - list("raw_sample" = c("rds_sampleA","rds_sampleB"), ...)
SAMPLE_NAME_MAP_MULTI <- list()

# (E4) [NEW] RDSで選んだスポットを「別ファイル」から抜き出すときの対応付け方法
#   - "id"      : RDS の spot_index（=ID_for_export）と 入力の id を一致させて抽出（従来）
#   - "xy"      : RDS の x_coord/y_coord と 入力の x/y を一致（完全一致）させて抽出（Parquetのみ推奨）
#   - "xy_tol"  : x/y が一致しない場合に、許容誤差 INPUT_XY_TOL 以内で対応付け（Parquetのみ）
INPUT_MATCH_METHOD <- "id"

# (E5) [NEW] INPUT_MATCH_METHOD="xy_tol" のときの許容誤差（座標が整数なら 0 or 1 推奨）
INPUT_XY_TOL <- 0
# (F) クラスタ抽出モード
#     "exclude" : TARGET_CLUSTERS を除外して残りで再解析
#     "keep"    : TARGET_CLUSTERS のみで再解析
FILTER_MODE <- "exclude"

# (G) 対象クラスタ（seurat_clusters の番号）
TARGET_CLUSTERS <- c(8)

# (H) フィルタ後入力の出力フォルダ
EXPORT_DATA_DIR <- "C:\\Users\\Cciia\\Biochem Dropbox\\木津亮馬\\MSI_Tims\\SCiLS_Transform\\Data\\260207_HCC_Transform"
dir.create(EXPORT_DATA_DIR, recursive = TRUE, showWarnings = FALSE)

# (I) 新規入力を書き出した後に ver13 を自動実行するか（重いのでデフォルトFALSE推奨）
RUN_V13_AFTER_EXPORT <- TRUE

# ========== PreFlight: reduction_only 再解析（ver18 追加） ==========
# RERUN_PIPELINE_STAGE: "full"(従来の通常再解析) / "reduction_only"(①診断用)。
#   アプリ(generate_cluster_filter_config)が注入。reduction_only のとき、
#   make_v13_copy_with_settings が ver5 copy の PIPELINE_STAGE へ伝播し、patch の
#   run_pipeline は reduction(PCA/Harmony)計算後に即返す（UMAP/クラスタをスキップ）。
#   ver5 の run_downstream_analysis(reduction_only ガード)で DEG/作図もスキップ、
#   さらに後段 ReUMAP-replace / merge もスキップ → 部分集合の reduction RDS だけ残る。
RERUN_PIPELINE_STAGE <- "full"


# ========== 任意（ver13 を自動実行する場合だけ設定） ==========
# (J) 自動実行する場合の ver13 側の出力先（OUTPUT_DIR）
V13_OUTPUT_DIR <- EXPORT_DATA_DIR

# (K) 自動実行する場合の ver13 側 PROJECT_LABEL（フォルダ名の先頭）
#     例: "MyProject_" のように末尾 "_" を付ける運用を推奨
V13_PROJECT_LABEL_PREFIX <- "ClusterFiltered_"

# (L) Re-UMAP では新規入力から解析し直すので、Resume は基本OFF推奨
V13_RESUME_FROM_RDS <- FALSE
V13_RESUME_DIR_PATH <- ""
# [ver46.0] 環境変数 REUMAP_RESUME_DIR に前回実行の RDS_Files を指定すると再開する。
#   10万px 級の再解析は 2 時間超かかるため、Step3(RPCA) だけを検証したいときに
#   毎回 Step1/Step2 をやり直すのは現実的でない。前回の Step1/Step2 RDS を読んで
#   その先だけを実行する。
#   条件: 再開元にしたい実行を SAVE_STEP2_WITH_COUNTS=1 で回しておくこと
#         （RPCA は counts 層を必要とし、既定の Step2 RDS には含まれないため）。
#   なお Parquet エクスポートは再開時もそのまま実行する。これは無駄ではなく、
#   オーケストレータ側のメモリ状態（Arrow プールの残留を含む）を通常実行と揃え、
#   RPCA 直前の条件を忠実に再現するためである。
local({
  .rd <- Sys.getenv("REUMAP_RESUME_DIR", unset = "")
  if (nzchar(.rd)) {
    if (dir.exists(.rd)) {
      V13_RESUME_FROM_RDS <<- TRUE
      V13_RESUME_DIR_PATH <<- .rd
      message(">> [ver46.0] RESUME 有効: ", .rd,
              " （Step1/Step2 RDS があればそこから再開します）")
    } else {
      message("!! [ver46.0] REUMAP_RESUME_DIR が存在しないため無視します: ", .rd)
    }
  }
})

# (M) （必要なときだけ）ver13 の設定をこのスクリプト側から“上書き”したい場合
#     空("") / NA の場合は、ver13 側に書かれている設定をそのまま使います。
V13_ION_MODE <- "Negative"   # "" にすると上書きしない
# ★ ver55.0 (R-01): 直書きの Dropbox パスを空にした。Python 側の注入先が
#   `ANNOTATION_CSV_PATH`（V13_ 接頭辞なし）で名前が一致せず無言で素通りしていたため、
#   UI で指定したアノテーション CSV は破棄され、この直書き値が使われ続けていた。
#   エラーも出ず解析は緑で完走するので、気づく手段が無かった。
V13_ANNOTATION_CSV_PATH <- ""  # "" で上書きしない
V13_TOLERANCE_MZ <- 0.05     # NA にすると上書きしない
V13_ANNOTATION_ENABLE <- FALSE  # NA にすると上書きしない

# (M2) m/z キャリブレーション（再解析用）
# Python が analysis_params.json から復元した回帰係数を注入する
V13_CALIBRATION_ENABLE <- FALSE
V13_CALIBRATION_COEFFICIENTS <- c(0)

# (M3) 入力正規化ポリシー（二重正規化の回避・アプリのトグルから注入）
#   V13_INPUT_NORMALIZED=TRUE で LogNormalize を行わず NORM_MODE のみ適用。
#   TIMS は SCiLS の RMS で正規化済みのため既定 TRUE(=正規化OFF)。NA で ver4 側既定を使用。
V13_INPUT_NORMALIZED <- TRUE
V13_NORM_MODE <- "log1p"

# (M3ب) 解析シナリオ → 補正ポリシー（アプリの再解析シナリオから注入。ver6 のスイッチへ伝播）
#   初回解析のシナリオを引き継ぎ、subset の reduction にも同じ補正方針を効かせる。
#   未注入なら既定（biological / sample / FALSE）＝従来挙動。
V13_ANNOTATION_ROLE <- "biological"
V13_BATCH_VAR <- "sample"
V13_ALLOW_CONDITION_CORRECTION <- FALSE
# ★ ver58.0 (デバッグ総点検 A-1): そもそも補正するか。NA で ver6 既定(TRUE)を使う。
#   「補正なし」シナリオで再解析したのに補正が走る、という食い違いを防ぐ。
V13_BATCH_CORRECTION_ENABLE <- NA

# DEG 閾値（アプリの再解析設定から V13_ 経由で注入。未注入なら ver6 既定）。
V13_DEG_P_THRESH_VAL <- 0.05
V13_DEG_LOGFC_TH_VAL <- 0.25

# 切片 (Annotation) フィルタ。
#   ★ ver57.5 (デバッグ総点検 §5.3): 再解析画面の「Annotation（切片）選択」は
#     Python 側が params["annotation_filter"] を組み立てていたのに、注入するのは
#     本解析用の generate_v8_config だけで、再解析側には受け手も注入も無かった。
#     チェックを外しても**その切片のスポットが再解析にそのまま入る**空振りだった。
#     NULL のままなら ver6 側の既定（フィルタなし）を使う＝従来挙動。
V13_ANNOTATION_FILTER <- NULL

# (N) slice_id / condition を 1回目RDSから保存しておきたい場合（通常は不要）
#     ver13 は入力Parquetの annotation から slice_id/condition を再現できるため、
#     基本は FALSE 推奨です（Re-UMAP側でDBSCAN復元などはしない）。
SAVE_SLICE_MAP_FROM_FIRST_RUN <- FALSE


# ========== 任意（ReUMAP置換） ==========
# (O) === ReUMAP(keep) の結果を「元UMAP」に重ねて、指定クラスタだけ置換する ===
# 目的: keep したスポットだけで ReUMAP した結果（新UMAP & 新クラスタ）を、元UMAP空間に整列して、
#      元データ側の指定クラスタ部分だけ置き換えた UMAP を作る。
#
# 重要:
#  - ENABLE_REUMAP_REPLACE は "auto" / TRUE / FALSE を指定できます。
#      * "auto": FILTER_MODE が keep のときだけ置換を有効化（exclude では無効化）
#      * TRUE  : 置換を強制ON（exclude の場合は安全のため自動OFFに落とします）
#      * FALSE : 置換を常にOFF
#    ※置換（貼り戻し）は keep 前提です。
#  - UMAP は回すたびに座標系（回転/反転/スケール）が変わり得るため、
#    そのまま貼り戻すと「見た目が合わない」問題が起きます。
#    → ここでは「同じセルの oldUMAP と newUMAP を対応付けて、newUMAP を oldUMAP にアフィン整列」してから置換します。
ENABLE_REUMAP_REPLACE <- "auto"  # "auto" / TRUE / FALSE

# 置換対象: 元データ側のクラスタ（通常は TARGET_CLUSTERS と同じでOK）
REPLACE_BASE_CLUSTERS <- TARGET_CLUSTERS

# ReUMAP（再解析）後のRDS（Step2/Step3）を明示したい場合はパスを入れる
#   - 空欄("")の場合:
#       * RUN_V13_AFTER_EXPORT=TRUE なら「今まさに回した ver13 の出力フォルダ」から自動推定
#       * RUN_V13_AFTER_EXPORT=FALSE なら停止（ユーザーがここにパスを入れる必要あり）
REUMAP_RERUN_RDS_PATH <- ""

# 置換結果の出力先（PNG/RDS）
REPLACE_OUT_DIR <- EXPORT_DATA_DIR
REPLACE_OUT_PREFIX <- "UMAP_replace"


# ========== マージ統合（ver14 追加） ==========
# クラスタ抽出後の再UMAPサブクラスタを元のUMAP空間にマッピングし、
# 統合ラベル（"3-a", "3-b" 等）を生成する。
#
# ENABLE_MERGE_CLUSTERS:
#   "auto" : FILTER_MODE=="keep" かつ RUN_V13_AFTER_EXPORT==TRUE のとき有効
#   TRUE   : 強制ON（keep以外では安全のため自動OFF）
#   FALSE  : 常にOFF
ENABLE_MERGE_CLUSTERS <- "auto"

# マージスクリプトのパス（Python側から自動注入）
MERGE_SCRIPT_PATH <- ""

# サブクラスタの命名ルール: "alpha" → "3-a","3-b" / "numeric" → "3-0","3-1"
MERGE_SUBCLUSTER_NAMING <- "alpha"

# マージ結果の出力先（空の場合は EXPORT_DATA_DIR を使用）
MERGE_OUT_DIR_OVERRIDE <- ""

# マージ結果のファイル名接頭辞
MERGE_OUT_PREFIX <- "UMAP_merged"


# [UTILS] 以降は編集不要
# ------------------------------------------------------------
.stopif <- function(cond, msg) { if (!isTRUE(cond)) stop(msg, call. = FALSE) }


# ------------------------------------------------------------
# [PREFLIGHT] 設定の整合性チェック（合わない場合はすぐ停止）
#   - ユーザーが自由な場所にスクリプト/データを置いても、
#     設定ミスや想定外の構造なら “処理を始める前” に止めるための検証です。
# ------------------------------------------------------------
.preflight_check_writable_dir <- function(d) {
  .stopif(!is.null(d) && nzchar(d), "出力フォルダが空です。")
  dir.create(d, recursive = TRUE, showWarnings = FALSE)
  .stopif(dir.exists(d), paste0("出力フォルダを作成/参照できません: ", d))
  tf <- file.path(d, paste0(".write_test_", Sys.getpid(), "_", format(Sys.time(), "%Y%m%d%H%M%S"), ".tmp"))
  ok <- FALSE
  try({
    ok <- isTRUE(file.create(tf))
    if (ok) unlink(tf)
  }, silent = TRUE)
  .stopif(ok, paste0("出力フォルダに書き込みできません（権限/同期ロック等を確認）: ", d))
}

.preflight_validate <- function() {
  message("=== PREFLIGHT: validating settings & paths ===")

  # (1) 基本設定
  .stopif(tolower(FILTER_MODE) %in% c("exclude", "keep"),
          "FILTER_MODE は 'exclude' または 'keep' を指定してください。")
  .stopif(length(TARGET_CLUSTERS) > 0, "TARGET_CLUSTERS が空です。")
  .stopif(tolower(CLUSTER_SOURCE) %in% c("harmony", "rpca"),
          "CLUSTER_SOURCE は 'harmony' または 'rpca' を指定してください。")

  # (2) ver13 スクリプト（自動実行する場合のみ必須）
  if (isTRUE(RUN_V13_AFTER_EXPORT)) {
    .stopif(!is.null(V13_SCRIPT_PATH) && nzchar(V13_SCRIPT_PATH), "RUN_V13_AFTER_EXPORT=TRUE ですが V13_SCRIPT_PATH が空です。")
    .stopif(file.exists(V13_SCRIPT_PATH), paste0("ver13スクリプトが見つかりません: ", V13_SCRIPT_PATH))
  }

  # (3) RDS の指定（ファイル直指定 or フォルダ指定）
  if (!nzchar(RDS_PATH)) {
    .stopif(nzchar(RDS_RUN_DIR), "RDS_PATH が空の場合は RDS_RUN_DIR を指定してください。")
  } else {
    .stopif(file.exists(RDS_PATH), paste0("RDS_PATH の .rds が見つかりません: ", RDS_PATH))
  }

  # 参照するRDSを確定して存在確認（ここで止める）
  rds_target <- resolve_rds_path(RDS_PATH, RDS_RUN_DIR, CLUSTER_SOURCE)
  .stopif(file.exists(rds_target), paste0("参照すべきRDSが見つかりません: ", rds_target,
                                         "\n  - RDS_RUN_DIR が正しいか（RDS_Files を含むか）",
                                         "\n  - CLUSTER_SOURCE が正しいか（harmony/rpca）",
                                         "\n  - フォルダ名の打ち間違いがないか を確認してください。"))

  # (4) 入力ファイルの存在
  .stopif(length(ORIGINAL_INPUT_PATHS) > 0, "ORIGINAL_INPUT_PATHS が空です。")
  .stopif(all(file.exists(ORIGINAL_INPUT_PATHS)), paste0(
    "ORIGINAL_INPUT_PATHS に存在しないパスがあります:\n",
    paste0("  - ", ORIGINAL_INPUT_PATHS[!file.exists(ORIGINAL_INPUT_PATHS)], collapse = "\n")
  ))

  # 拡張子チェック（想定外なら即停止）
  exts <- tolower(tools::file_ext(ORIGINAL_INPUT_PATHS))
  ok_ext <- exts %in% c("parquet", "pq", "csv", "tsv", "txt")
  .stopif(all(ok_ext), paste0(
    "入力ファイルの拡張子が想定外です（.parquet/.pq/.csv/.tsv/.txt のみ対応）。\n",
    paste0("  - ", ORIGINAL_INPUT_PATHS[!ok_ext], collapse = "\n")
  ))

  # (5) 入力マッピング方式の整合性
  mm <- tolower(as.character(INPUT_MATCH_METHOD))
  .stopif(mm %in% c("id", "xy", "xy_tol"), "INPUT_MATCH_METHOD は 'id' / 'xy' / 'xy_tol' を指定してください。")
  if (mm %in% c("xy", "xy_tol")) {
    # xy系は Parquet を強く前提（列検証が容易 & 大規模でも軽い）
    .stopif(all(exts %in% c("parquet", "pq")),
            "INPUT_MATCH_METHOD が 'xy' / 'xy_tol' の場合、入力は Parquet(.parquet/.pq) のみを推奨します（CSV/TSVは座標列が無いことが多く事故りやすい）。")
    if (mm == "xy_tol") .stopif(is.numeric(INPUT_XY_TOL) && length(INPUT_XY_TOL) == 1 && INPUT_XY_TOL >= 0,
                               "INPUT_MATCH_METHOD='xy_tol' の場合、INPUT_XY_TOL は 0以上の数値を指定してください。")
    .stopif(requireNamespace("arrow", quietly = TRUE),
            "INPUT_MATCH_METHOD が 'xy' / 'xy_tol' のため Parquet の列検証に arrow が必要です。install.packages('arrow')")

    # 列の存在を最小読み込みでチェック（ここで止める）
    for (fp in ORIGINAL_INPUT_PATHS) {
      # x/y のどちらかの表記を許容（スクリプト本体のロジックと一致）
      need_any <- list(c("x", "y"), c("x_coord", "y_coord"))
      ok_xy <- FALSE
      for (cand in need_any) {
        ok_xy <- TRUE
        for (cc in cand) {
          ok_xy <- ok_xy && !inherits(try(arrow::read_parquet(fp, col_select = c(cc), as_data_frame = TRUE), silent = TRUE), "try-error")
        }
        if (ok_xy) break
      }
      .stopif(ok_xy, paste0("Parquetに座標列(x,y) もしくは (x_coord,y_coord) が見つかりません: ", fp))
    }
  } else {
    # id方式のときも Parquetなら id列だけは早期検証
    if (any(exts %in% c("parquet", "pq"))) {
      .stopif(requireNamespace("arrow", quietly = TRUE), "Parquet入出力に arrow が必要です。install.packages('arrow')")
      for (fp in ORIGINAL_INPUT_PATHS[exts %in% c("parquet", "pq")]) {
        ok_id <- !inherits(try(arrow::read_parquet(fp, col_select = c("id"), as_data_frame = TRUE), silent = TRUE), "try-error")
        .stopif(ok_id, paste0("Parquetに 'id' 列が見つかりません（INPUT_MATCH_METHOD='id' では必須）: ", fp))
      }
    }
  }

  # (6) 出力フォルダの書き込み可否
  .preflight_check_writable_dir(EXPORT_DATA_DIR)

  # (7) ReUMAP置換の整合性
  # ENABLE_REUMAP_REPLACE は "auto"/TRUE/FALSE を許可します。
  # "auto" の場合は FILTER_MODE に応じて有効/無効を自動決定します（keep=ON, exclude=OFF）。
  if (is.character(ENABLE_REUMAP_REPLACE) && tolower(ENABLE_REUMAP_REPLACE) == "auto") {
    if (tolower(FILTER_MODE) == "keep") {
      message("NOTE: ENABLE_REUMAP_REPLACE='auto' かつ FILTER_MODE='keep' のため、置換を有効化します。")
      ENABLE_REUMAP_REPLACE <<- TRUE
    } else {
      message("NOTE: ENABLE_REUMAP_REPLACE='auto' かつ FILTER_MODE='", FILTER_MODE, "' のため、置換を無効化します（置換は keep 前提）。")
      ENABLE_REUMAP_REPLACE <<- FALSE
    }
  }

  if (isTRUE(ENABLE_REUMAP_REPLACE)) {
    # 置換（貼り戻し）は keep 前提。exclude のときは自動的に無効化して続行します。
    if (tolower(FILTER_MODE) != "keep") {
      message("NOTE: FILTER_MODE='", FILTER_MODE, "' のため、ENABLE_REUMAP_REPLACE を FALSE に自動変更します（置換は keep 前提）。")
      ENABLE_REUMAP_REPLACE <<- FALSE
    }
  }

  # (7) ReUMAP置換の整合性（有効な場合のみ）
  if (isTRUE(ENABLE_REUMAP_REPLACE)) {
    .stopif(length(REPLACE_BASE_CLUSTERS) > 0, "ENABLE_REUMAP_REPLACE=TRUE ですが REPLACE_BASE_CLUSTERS が空です。")
    .preflight_check_writable_dir(REPLACE_OUT_DIR)

    if (!isTRUE(RUN_V13_AFTER_EXPORT) && !nzchar(REUMAP_RERUN_RDS_PATH)) {
      stop("RUN_V13_AFTER_EXPORT=FALSE かつ REUMAP_RERUN_RDS_PATH が空です。\nReUMAP後のRDS（Step2/Step3）を明示指定してください。", call. = FALSE)
    }
    if (nzchar(REUMAP_RERUN_RDS_PATH)) .stopif(file.exists(REUMAP_RERUN_RDS_PATH),
                                              paste0("REUMAP_RERUN_RDS_PATH の .rds が見つかりません: ", REUMAP_RERUN_RDS_PATH))
  }

  # (8) サンプル名マッピングの健全性
  if (length(SAMPLE_NAME_MAP) > 0) {
    .stopif(all(nzchar(names(SAMPLE_NAME_MAP))), "SAMPLE_NAME_MAP のキー（入力側sample名）が空です。")
    .stopif(all(nzchar(as.character(SAMPLE_NAME_MAP))), "SAMPLE_NAME_MAP の値（RDS側sample名）が空です。")
  }
  if (length(SAMPLE_NAME_MAP_MULTI) > 0) {
    .stopif(all(nzchar(names(SAMPLE_NAME_MAP_MULTI))), "SAMPLE_NAME_MAP_MULTI のキー（入力側sample名）が空です。")
  }

  message("=== PREFLIGHT: OK ===")
}

# --- ver13 出力のどのRDSを読むかを解決 ---
resolve_rds_path <- function(rds_path, rds_run_dir, cluster_source = "harmony") {
  # 1) 旧仕様: ファイルを直接指定
  if (!is.null(rds_path) && nzchar(rds_path)) return(rds_path)

  # 2) 推奨: 実行フォルダ（または RDS_Files フォルダ）+ クラスタソースで決定
  .stopif(!is.null(rds_run_dir) && nzchar(rds_run_dir), "RDS_PATH が空の場合は RDS_RUN_DIR を指定してください。")

  d <- rds_run_dir
  # run dir を指しているなら RDS_Files を掘る
  if (dir.exists(file.path(d, "RDS_Files"))) d <- file.path(d, "RDS_Files")
  .stopif(dir.exists(d), paste0("RDSフォルダが見つかりません: ", d))

  cs <- tolower(as.character(if (is.null(cluster_source) || !nzchar(as.character(cluster_source))) "harmony" else cluster_source))
  fname <- if (cs %in% c("harmony", "step2", "s2")) {
    "Step2_HarmonyPCA_Result.rds"
  } else if (cs %in% c("rpca", "step3", "s3")) {
    "Step3_RPCA_Result.rds"
  } else {
    stop("CLUSTER_SOURCE は 'harmony' か 'rpca' を指定してください。", call. = FALSE)
  }
  file.path(d, fname)
}


# 実行前に、設定/パスの不整合があれば即停止
.preflight_validate()


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


# ---------- [NEW] 入力ファイル側 sample 名 -> RDS 側 sample 名 の解決 ----------
resolve_rds_samples_for_input <- function(input_sample, sample_name_map = c(), sample_name_map_multi = list()) {
  # multi の方が優先
  if (length(sample_name_map_multi) > 0 && input_sample %in% names(sample_name_map_multi)) {
    v <- sample_name_map_multi[[input_sample]]
    return(as.character(v))
  }
  if (length(sample_name_map) > 0 && input_sample %in% names(sample_name_map)) {
    return(as.character(unname(sample_name_map[[input_sample]])))
  }
  return(as.character(input_sample))
}

# ---------- [NEW] x/y 列名のゆらぎ対応 ----------
.pick_xy_cols <- function(df, x_candidates = c("x","X","x_coord","xCoord","Xcoord","X_coord"),
                          y_candidates = c("y","Y","y_coord","yCoord","Ycoord","Y_coord")) {
  cx <- x_candidates[x_candidates %in% colnames(df)]
  cy <- y_candidates[y_candidates %in% colnames(df)]
  if (length(cx) == 0 || length(cy) == 0) return(list(x = NA_character_, y = NA_character_))
  list(x = cx[[1]], y = cy[[1]])
}

# ---------- [NEW] Parquet の x/y で対応付けして keep_ids を作る ----------
derive_keep_ids_by_xy <- function(parquet_path, rows_md, xy_tol = 0) {
  .stopif(requireNamespace("arrow", quietly = TRUE), "Parquet入出力に arrow が必要です。install.packages('arrow')")
  df <- arrow::read_parquet(parquet_path, as_data_frame = TRUE)

  .stopif("id" %in% colnames(df), paste0("Parquetに id 列がありません: ", parquet_path))
  xy <- .pick_xy_cols(df)
  .stopif(!is.na(xy$x) && !is.na(xy$y),
          paste0("INPUT_MATCH_METHOD='xy' には Parquet 側に x/y（または x_coord/y_coord）列が必要です: ", basename(parquet_path)))

  .stopif(all(c("x_coord","y_coord") %in% colnames(rows_md)),
          "RDS 側 meta.data に x_coord/y_coord が無いため xy マッチができません（ver13 Step1 で付与されます）。")

  rx <- suppressWarnings(as.numeric(rows_md$x_coord))
  ry <- suppressWarnings(as.numeric(rows_md$y_coord))
  .stopif(all(is.finite(rx)) && all(is.finite(ry)), "RDS 側 x_coord/y_coord が数値として解釈できません。")

  px <- suppressWarnings(as.numeric(df[[xy$x]]))
  py <- suppressWarnings(as.numeric(df[[xy$y]]))
  pid <- suppressWarnings(as.numeric(df$id))

  .stopif(any(is.finite(px)) && any(is.finite(py)), "Parquet 側 x/y が数値として解釈できません。")
  .stopif(any(is.finite(pid)), "Parquet 側 id が数値として解釈できません。")

  # 完全一致（高速・安全）
  if (xy_tol <= 0) {
    key_r <- paste(rx, ry, sep = "_")
    key_p <- paste(px, py, sep = "_")
    keep_flag <- key_p %in% key_r
    keep_ids <- unique(pid[keep_flag])
    keep_ids <- keep_ids[is.finite(keep_ids)]
    return(keep_ids)
  }

  # 近傍（許容誤差以内）: まず粗く候補を絞ってから判定
  # ※大規模データでも破綻しにくいように、四捨五入ビンで候補集合を作る
  bx_r <- round(rx / xy_tol)
  by_r <- round(ry / xy_tol)
  bx_p <- round(px / xy_tol)
  by_p <- round(py / xy_tol)
  key_r <- paste(bx_r, by_r, sep = "_")
  key_p <- paste(bx_p, by_p, sep = "_")
  candidate <- key_p %in% key_r
  if (!any(candidate)) return(numeric(0))

  # 候補の中で実距離もチェック
  # 許容: |dx|<=xy_tol かつ |dy|<=xy_tol
  keep_ids <- numeric(0)
  # 参照点を data.frame にしておき、候補ごとに近いものがあるか確認
  ref <- data.frame(x = rx, y = ry, stringsAsFactors = FALSE)

  idx <- which(candidate)
  for (i in idx) {
    dx <- abs(ref$x - px[i])
    dy <- abs(ref$y - py[i])
    if (any(dx <= xy_tol & dy <= xy_tol)) keep_ids <- c(keep_ids, pid[i])
  }
  keep_ids <- unique(keep_ids)
  keep_ids <- keep_ids[is.finite(keep_ids)]
  keep_ids
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

    # [ver45.5 メモリ根本対策] 旧実装は元 Parquet 全体を密 data.frame 化し、行フィルタで
    # もう 1 個作っていた（10万px 級で計 8GB 超）。Arrow Table のまま扱えば列バッファは
    # R ヒープ外に置かれ、float32 も広げずに済む。R 側に載せるのは id 列だけ。
    tab <- arrow::read_parquet(in_path, as_data_frame = FALSE)
    .stopif("id" %in% names(tab), paste0("Parquetに id 列がありません: ", in_path))

    id_num <- suppressWarnings(as.numeric(as.vector(tab$id)))
    keep_flag <- id_num %in% id_keep
    n_total <- tab$num_rows
    n_kept  <- sum(keep_flag, na.rm = TRUE)

    # Arrow Table の行サブセットは arrow のバージョン差がありうるため、失敗時は
    # 従来の data.frame 経路へフォールバックして必ず処理を通す。
    .subset_err <- NULL
    tab2 <- tryCatch(tab[keep_flag, ],
                     error = function(e) { .subset_err <<- conditionMessage(e); NULL })
    if (is.null(tab2)) {
      message(">> [ver45.5] Arrow Table の行抽出に失敗したため data.frame 経路にフォールバックします: ",
              .subset_err)
      df <- as.data.frame(tab)
      rm(tab); invisible(gc(verbose = FALSE))
      df2 <- df[keep_flag, , drop = FALSE]
      rm(df); invisible(gc(verbose = FALSE))
    } else {
      rm(tab); invisible(gc(verbose = FALSE))
      df2 <- tab2
    }

    if (n_kept == 0) {
      if (!is.null(debug_tsv_path)) {
        dbg <- data.frame(
          input = basename(in_path),
          n_rows = n_total,
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

    # 行グループが概ね 64MB 以内に収まる行数を指定して書く。
    # 注: かつて「単一行グループだと下流で行グループ単位の分割読みができなくなる」と
    # 書いていたが誤りだった。ver6 の取り込みは m/z 列をブロック単位で読む列ブロック方式で、
    # ブロック幅は行グループではなく総行数から決まるため行グループ構成に依存しない
    # （ver45.5 の CHANGELOG 参照）。本体 parquet も全行 1 行グループで出力している。
    # ここで chunk_size を指定するのは書き込み時のメモリを抑えるためであり、
    # 下流の読み取り互換性のためではない。
    .ncol_out <- length(names(df2))
    .chunk <- max(1024L, as.integer(floor(64 * 1024^2 / max(1, .ncol_out * 4))))
    arrow::write_parquet(df2, out_path, chunk_size = .chunk)
    return(invisible(list(n_kept = n_kept, n_total = n_total)))
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
        '    # 1) Normalize(=apply_input_norm: INPUT_NORMALIZED で二重正規化を回避) -> HVF -> Scale -> PCA',
        '    s <- apply_input_norm(seu_merged)',
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
        '    try({ Seurat::DefaultAssay(s) <- Seurat::DefaultAssay(seu_merged); s[[Seurat::DefaultAssay(s)]]@scale.data <- matrix(nrow=0, ncol=0) }, silent = TRUE)',
        '    # [ver45.7 計測] 今回の停止点。ScaleData は密行列(hvf x cells x 8byte)を作るため',
        '    #   ここが全工程で最も急激にメモリが増える。前後を実測する。',
        '    if (exists(".mem_note_base")) .mem_note_base(sprintf("Step2 ScaleData 前 (hvf=%d, cells=%d)", n_feat, n_cells))',
        '    s <- ScaleData(s, features = hvf)',
        '    if (exists(".mem_note_base")) .mem_note_base("Step2 ScaleData 後")',
        '    s <- RunPCA(s, npcs = npcs_use, features = hvf)',
        '    if (exists(".mem_note_base")) .mem_note_base("Step2 RunPCA 後")',
        '    # [ver45.9] scale.data は PCA を計算し終えれば不要。実測で ScaleData は +4.68GB を',
        '    #   要し、以後 11.4GB 前後で全工程（Harmony/UMAP/downstream x2/RPCA）が走っていた。',
        '    #   ここで破棄すると以降すべてが軽くなる。安全な根拠:',
        '    #   - downstream のヒートマップは subset に対し ScaleData を作り直す（空の前提の設計）',
        '    #   - Step2 の RDS 保存は keep_scale=FALSE で元々 scale.data を落としている',
        '    #   - RunHarmony は PCA 埋め込みに対して動くため scale.data 不要',
        '    #   - RPCA ブロックでも既に破棄済み（それを前倒しするだけ）',
        '    suppressWarnings(try(s[[Seurat::DefaultAssay(s)]]$scale.data <- NULL, silent = TRUE))',
        '    invisible(gc(verbose = FALSE))',
        '    if (exists(".mem_note_base")) .mem_note_base("Step2 scale.data 破棄後")',
        '',
        '    # 2) dims を「実際に存在する次元数」で丸める（UMAP/Neighborsの範囲外エラー回避）',
        '    dims_use <- 1:min(cfg$umap_dims, npcs_use)',
        '',
        '    if (use_harmony) {',
        '      s <- RunHarmony(s, group.by.vars = group_var)',
        '    }',
        '    # PreFlight: reduction_only は reduction(PCA/Harmony)計算後に即返す（UMAP/クラスタをスキップ）',
        '    if (identical(PIPELINE_STAGE, "reduction_only")) return(s)',
        '    if (use_harmony) {',
        '      nh <- ncol(Seurat::Embeddings(s, "harmony"))',
        '      dims_h <- 1:min(cfg$umap_dims, nh)',
        '      s <- RunUMAP(s, reduction = "harmony", dims = dims_h) %>%',
        '        FindNeighbors(reduction = "harmony", dims = dims_h)',
        '    } else {',
        '      s <- RunUMAP(s, reduction = "pca", dims = dims_use) %>%',
        '        FindNeighbors(reduction = "pca", dims = dims_use)',
        '    }',
        '    FindClusters(s, resolution = CLUSTER_RESOLUTION, algorithm = 4)',
        '  }'
      )
      code_vec <- c(code_vec[1:(s-1)], new_run, code_vec[(e+1):length(code_vec)])
    }
  }

  # --- 2) Retry Logic ---
  # [ver56.5] かつてここで Retry Logic ブロックを丸ごと置換し、失敗理由を表示する
  #   verbose 版へ差し替えていた。本体テンプレ(ver6 slim)が ver56.5 で
  #   失敗理由の表示に加えて「採用した段と実効ハイパーパラメータ」の記録
  #   (RETRY_*_EFFECTIVE / [retry] ログ) まで行うようになったため、この置換は
  #   不要になった。置換を残すと **本体側の記録を消してしまう** うえ、
  #   `for (cfg in HARMONY_RETRY_GRID)` という目印は本体から消えたので
  #   無言で空振りする anchor になる（tests/test_r_patch_anchors.py が検出）。
  #   よって置換は行わず、本体テンプレの実装をそのまま引き継ぐ。

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

  # m/z calibration (再解析用: Python が analysis_params.json から復元した係数を注入)
  if (exists("V13_CALIBRATION_ENABLE") && isTRUE(V13_CALIBRATION_ENABLE)) {
    code <- replace_assign_line(code, "CALIBRATION_ENABLE", "TRUE", multiple = TRUE)
    coef_str <- paste0("c(", paste(V13_CALIBRATION_COEFFICIENTS, collapse = ", "), ")")
    code <- replace_assign_line(code, "CALIBRATION_COEFFICIENTS", coef_str, multiple = TRUE)
  }

  # 入力正規化ポリシー（二重正規化の回避: アプリのトグルから注入。ver4 の apply_input_norm が参照）
  if (exists("V13_INPUT_NORMALIZED") && !is.na(V13_INPUT_NORMALIZED)) {
    code <- replace_assign_line(code, "INPUT_NORMALIZED", if (isTRUE(V13_INPUT_NORMALIZED)) "TRUE" else "FALSE", multiple = TRUE)
  }
  if (exists("V13_NORM_MODE") && nzchar(V13_NORM_MODE)) {
    code <- replace_assign_line(code, "NORM_MODE", r_str(V13_NORM_MODE), multiple = TRUE)
  }

  # 解析シナリオ → ver6 の補正ポリシースイッチへ伝播（初回シナリオの引き継ぎ）
  if (exists("V13_ANNOTATION_ROLE") && nzchar(V13_ANNOTATION_ROLE)) {
    code <- replace_assign_line(code, "ANNOTATION_ROLE", r_str(V13_ANNOTATION_ROLE), multiple = TRUE)
  }
  if (exists("V13_BATCH_VAR") && nzchar(V13_BATCH_VAR)) {
    code <- replace_assign_line(code, "BATCH_VAR", r_str(V13_BATCH_VAR), multiple = TRUE)
  }
  if (exists("V13_ALLOW_CONDITION_CORRECTION") && !is.na(V13_ALLOW_CONDITION_CORRECTION)) {
    code <- replace_assign_line(code, "ALLOW_CONDITION_CORRECTION",
      if (isTRUE(V13_ALLOW_CONDITION_CORRECTION)) "TRUE" else "FALSE", multiple = TRUE)
  }
  # ★ ver58.0 (A-1): 補正の要否を ver6 コピーへ伝播
  if (exists("V13_BATCH_CORRECTION_ENABLE") && !is.na(V13_BATCH_CORRECTION_ENABLE)) {
    code <- replace_assign_line(code, "BATCH_CORRECTION_ENABLE",
      if (isTRUE(V13_BATCH_CORRECTION_ENABLE)) "TRUE" else "FALSE", multiple = TRUE)
  }

  # 切片 (Annotation) フィルタ → ver6 copy へ伝播
  #   ★ ver57.5: これが無いと、再解析画面で外した切片が解析に入ったままになる。
  if (exists("V13_ANNOTATION_FILTER") && length(V13_ANNOTATION_FILTER) > 0) {
    .af <- paste0("c(", paste(sprintf("\"%s\"", V13_ANNOTATION_FILTER), collapse = ", "), ")")
    code <- replace_assign_line(code, "ANNOTATION_FILTER", .af, multiple = TRUE)
  }

  # DEG 閾値 → ver6 copy へ伝播（再解析の p/logFC を反映）
  if (exists("V13_DEG_P_THRESH_VAL") && !is.na(V13_DEG_P_THRESH_VAL)) {
    code <- replace_assign_line(code, "DEG_P_THRESH_VAL", as.character(V13_DEG_P_THRESH_VAL), multiple = TRUE)
  }
  if (exists("V13_DEG_LOGFC_TH_VAL") && !is.na(V13_DEG_LOGFC_TH_VAL)) {
    code <- replace_assign_line(code, "DEG_LOGFC_TH_VAL", as.character(V13_DEG_LOGFC_TH_VAL), multiple = TRUE)
  }

  code <- replace_assign_line(code, "RESUME_FROM_RDS", if (isTRUE(resume_from_rds)) "TRUE" else "FALSE")
  code <- replace_assign_line(code, "RESUME_DIR_PATH", r_str(resume_dir_path))

  # PreFlight: reduction_only 再解析なら ver5 copy の PIPELINE_STAGE を伝播。
  #   patch の run_pipeline と ver5 の run_downstream_analysis が UMAP 以降をスキップし、
  #   部分集合の reduction RDS だけ保存する。"full"(通常)では置換しない（後方互換）。
  if (!identical(RERUN_PIPELINE_STAGE, "full")) {
    code <- replace_assign_line(code, "PIPELINE_STAGE", r_str(RERUN_PIPELINE_STAGE))
  }

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

  # 生成コピーの構文検証: source() 前に parse() で確認し、失敗時は出力パスを明示して即停止
  # （"unexpected end of input" のような不明瞭なエラーを未然に検知）。
  parse_ok <- tryCatch({ parse(file = out_path); TRUE },
                       error = function(e) {
                         message("!! 生成した ver13 コピーの構文解析に失敗: ", conditionMessage(e))
                         FALSE
                       })
  if (!isTRUE(parse_ok)) {
    stop(sprintf("生成した ver13 コピーが構文的に不正です: %s", out_path), call. = FALSE)
  }

  invisible(out_path)
}

# ------------------------------------------------------------

# ------------------------------------------------------------
# [ADD] ReUMAP結果を元UMAPに整列して「指定クラスタだけ」置換
# ------------------------------------------------------------

# cell key: sample + spot_index で安定に対応付け（cellname変更に強い）
.make_cell_key <- function(obj, sample_name_map = NULL) {
  md <- obj@meta.data
  .stopif(all(c("sample","spot_index") %in% colnames(md)),
          "ReUMAP置換には meta.data に sample/spot_index が必要です（ver13 Step1 で付与されます）。")
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
  # ★ ver57.5 (デバッグ総点検 §5.4): セル名で名前を付けて返す。
  #   `.get_umap_df` は戻り値を `[emb$cell]` と**セル名で引く**が、
  #   ここで名前を付けていなかったため R の仕様どおり **全て NA** になっていた
  #   （名前の無いベクトルを文字列で添字すると NA）。
  #   NA 同士の merge は既定で「一致」と見なされるので、元 N 行 × 再解析 M 行の
  #   **総当り結合**に化け、大きなデータではメモリを食い潰し、小さいと
  #   「何も貼り戻されていない成果物」が出る。しかもエラーは出ない。
  #   共通マージスクリプト (Common/UMAP_Merge_Clusters_ver1.R) の同名関数には
  #   元から names() があり、**こちらのコピーだけが欠けていた**。
  names(key) <- rownames(md)
  key
}

# UMAP embedding を data.frame(UMAP_1, UMAP_2, key) で取得
.get_umap_df <- function(obj, reduction = "umap", sample_name_map = NULL) {
  .stopif(reduction %in% names(obj@reductions), paste0("UMAP reduction '", reduction, "' が見つかりません。"))
  emb <- as.data.frame(Seurat::Embeddings(obj, reduction = reduction))
  .stopif(ncol(emb) >= 2, "UMAP embedding の次元が不足しています。")
  emb <- emb[, 1:2, drop = FALSE]
  colnames(emb) <- c("UMAP_1", "UMAP_2")
  emb$cell <- rownames(emb)
  emb$key <- .make_cell_key(obj, sample_name_map)[emb$cell]
  emb
}

# 2次元アフィン変換（new -> old）を最小二乗で推定
#  D = [1, y1, y2] ; X = D %*% B  (B: 3x2)
.fit_affine_2d <- function(old_xy, new_xy) {
  .stopif(nrow(old_xy) == nrow(new_xy) && nrow(old_xy) >= 3, "整列には3点以上の対応が必要です。")
  D <- cbind(1, as.matrix(new_xy))
  X <- as.matrix(old_xy)
  # 安定性のため qr.solve を使用
  B <- qr.solve(D, X)  # 3x2
  list(B = B)
}

.apply_affine_2d <- function(new_xy, fit) {
  D <- cbind(1, as.matrix(new_xy))
  Xhat <- D %*% fit$B
  Xhat
}

# 元Seuratに「置換後UMAP」と「置換後クラスタラベル」を追加し、PNG/RDSで保存
apply_reumap_replace <- function(base_seu, rerun_seu,
                                 replace_base_clusters,
                                 sample_name_map = NULL,
                                 base_reduction = "umap",
                                 rerun_reduction = "umap",
                                 out_dir = ".", out_prefix = "UMAP_replace") {

  .stopif(requireNamespace("Seurat", quietly = TRUE), "ReUMAP置換には Seurat パッケージが必要です。")
  .stopif(requireNamespace("ggplot2", quietly = TRUE), "ReUMAP置換には ggplot2 パッケージが必要です。")
  .stopif(requireNamespace("patchwork", quietly = TRUE), "ReUMAP置換には patchwork パッケージが必要です。")


  # --- embeddings + keys ---
  base_um <- .get_umap_df(base_seu, reduction = base_reduction)
  rer_um  <- .get_umap_df(rerun_seu, reduction = rerun_reduction, sample_name_map = sample_name_map)

  # --- 置換対象セル（元クラスタで判定） ---
  md_base <- base_seu@meta.data
  .stopif("seurat_clusters" %in% colnames(md_base), "base meta.data に seurat_clusters がありません。")
  base_cl <- as.character(md_base$seurat_clusters)
  rep_set <- as.character(replace_base_clusters)
  rep_cells <- rownames(md_base)[base_cl %in% rep_set]

  .stopif(length(rep_cells) > 0, "置換対象セルが0件です（REPLACE_BASE_CLUSTERS を確認）。")

  # --- 対応付け（key で突合） ---
  base_sub <- base_um[base_um$cell %in% rep_cells, , drop = FALSE]
  .stopif(nrow(base_sub) > 0, "base側 UMAP の対応セルが取れません（cell名不一致）。")

  # rerun側は全てが対象セルのはずだが、念のため key で inner join
  m <- merge(base_sub, rer_um, by = "key", suffixes = c("_old", "_new"))
  .stopif(nrow(m) >= 3, paste0("対応セルが少なすぎます（n=", nrow(m), "）。sample/spot_index の不一致を疑ってください。"))

  old_xy <- m[, c("UMAP_1_old", "UMAP_2_old")]
  new_xy <- m[, c("UMAP_1_new", "UMAP_2_new")]

  # --- newUMAP を oldUMAP に整列（回転/反転/スケール差を吸収） ---
  fit <- .fit_affine_2d(old_xy, new_xy)

  # 置換対象セルの new embedding（整列後）を作成
  # rerun側 cell名は変わり得るので、m の対応表（key -> base_cell）で base側のcell順に並べる
  new_xy_all <- rer_um[, c("UMAP_1","UMAP_2")]
  new_xy_all2 <- .apply_affine_2d(new_xy_all, fit)
  rer_um$UMAP_1_aligned <- new_xy_all2[,1]
  rer_um$UMAP_2_aligned <- new_xy_all2[,2]

  # key -> base_cell の対応
  key_to_basecell <- setNames(m$cell_old, m$key)

  # baseの embedding をコピーして置換
  base_emb <- as.data.frame(Seurat::Embeddings(base_seu, reduction = base_reduction))
  base_emb <- base_emb[, 1:2, drop = FALSE]
  colnames(base_emb) <- c("UMAP_1", "UMAP_2")

  # 置換: base側の rep_cells のうち、rerunに存在するものだけ
  rep_keys <- unique(m$key)
  rep_base_cells2 <- unique(key_to_basecell[rep_keys])
  rep_base_cells2 <- rep_base_cells2[!is.na(rep_base_cells2)]

  # rerun aligned coords を rep_base_cells2 の順に並べる
  rer_sub_aligned <- rer_um[rer_um$key %in% rep_keys, c("key","UMAP_1_aligned","UMAP_2_aligned"), drop=FALSE]
  rer_sub_aligned$base_cell <- key_to_basecell[rer_sub_aligned$key]
  rer_sub_aligned <- rer_sub_aligned[!is.na(rer_sub_aligned$base_cell), , drop=FALSE]

  # base_emb に代入
  for (i in seq_len(nrow(rer_sub_aligned))) {
    bc <- rer_sub_aligned$base_cell[i]
    if (bc %in% rownames(base_emb)) {
      base_emb[bc, "UMAP_1"] <- rer_sub_aligned$UMAP_1_aligned[i]
      base_emb[bc, "UMAP_2"] <- rer_sub_aligned$UMAP_2_aligned[i]
    }
  }

  # --- 置換後クラスタラベル ---
  md_rer <- rerun_seu@meta.data
  .stopif("seurat_clusters" %in% colnames(md_rer), "rerun meta.data に seurat_clusters がありません。")
  rer_cl <- as.character(md_rer$seurat_clusters)
  # rerun cell -> key
  rer_key <- .make_cell_key(rerun_seu, sample_name_map)
  names(rer_key) <- rownames(md_rer)

  # key -> rerun cluster
  key_to_rercl <- setNames(rer_cl, rer_key)

  # base側: 置換対象セルに対して「元クラスタ_新クラスタ」でラベル付与
  base_new_label <- as.character(md_base$seurat_clusters)
  base_key <- .make_cell_key(base_seu); names(base_key) <- rownames(md_base)
  for (bc in rep_cells) {
    k <- base_key[[bc]]
    if (!is.null(k) && k %in% names(key_to_rercl)) {
      base_new_label[bc] <- paste0(as.character(md_base$seurat_clusters[bc]), "_", key_to_rercl[[k]])
    }
  }

  # --- base_seu に new reduction / new ident を追加 ---
  seu2 <- base_seu

  # reduction
  red <- Seurat::CreateDimReducObject(embeddings = as.matrix(base_emb),
                             key = "UMAPR_",
                             assay = Seurat::DefaultAssay(seu2))
  seu2[["umap_replaced"]] <- red

  # label column
  seu2$seurat_clusters_replaced <- base_new_label

  # Plot (before/after)
  p_before <- Seurat::DimPlot(seu2, reduction = base_reduction, group.by = "seurat_clusters") + ggtitle("UMAP (base)")
  p_after  <- Seurat::DimPlot(seu2, reduction = "umap_replaced", group.by = "seurat_clusters_replaced") + ggtitle("UMAP (replace)")
  p_pair <- p_before + p_after + patchwork::plot_layout(ncol = 2)

  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  # ggplot2::ggsave(file.path(out_dir, paste0(out_prefix, "_before_after.png")),
  #        p_pair, width = 14, height = 6, dpi = 300, bg = "white", limitsize = FALSE)

  # Save Seurat object (RDS, slim: DietSeurat + qs)
  save_rds_compact(seu2, file.path(out_dir, paste0(out_prefix, "_seurat_with_umap_replaced.rds")))

  message(">> Saved: ", file.path(out_dir, paste0(out_prefix, "_before_after.png")))
  message(">> Saved: ", file.path(out_dir, paste0(out_prefix, "_seurat_with_umap_replaced.rds")))

  invisible(seu2)
}
# [MAIN]
# ------------------------------------------------------------
RDS_RESOLVED <- resolve_rds_path(RDS_PATH, RDS_RUN_DIR, CLUSTER_SOURCE)
message(">> Loading Seurat RDS: ", RDS_RESOLVED)
.stopif(file.exists(RDS_RESOLVED), paste0("RDSが見つかりません: ", RDS_RESOLVED))
.mem_note_orch("起動直後")
rds_obj <- load_rds_compact(RDS_RESOLVED)
.mem_note_orch("元 RDS 読み込み後")

# Step2/Step3 は list(obj=..., reduction=...) の形式になっている場合がある
seu <- rds_obj
if (is.list(rds_obj) && !is.null(rds_obj$obj) && inherits(rds_obj$obj, "Seurat")) {
  seu <- rds_obj$obj
}

.stopif(inherits(seu, "Seurat"), "RDSからSeuratオブジェクトを取得できません（Step2/Step3のRDSを指定してください）。")

# seurat_clusters が無い場合、Identsから作る（念のため）
if (!("seurat_clusters" %in% colnames(seu@meta.data))) {
  message(">> meta.data に seurat_clusters が無いので Seurat::Idents(seu) を seurat_clusters として追加します。")
  seu@meta.data$seurat_clusters <- as.character(Seurat::Idents(seu))
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
.merge_sample_map <- c()   # rerun_sample → base_sample マッピング（マージ用）


for (fp in ORIGINAL_INPUT_PATHS) {
  .stopif(file.exists(fp), paste0("元入力が見つかりません: ", fp))
  input_sn <- tools::file_path_sans_ext(basename(fp))

  # 入力ファイルの sample 名が RDS と違う場合に備え、マッピングで解決
  rds_samples <- resolve_rds_samples_for_input(input_sn, SAMPLE_NAME_MAP, SAMPLE_NAME_MAP_MULTI)
  rows_sn <- md_keep[as.character(md_keep$sample) %in% as.character(rds_samples), , drop = FALSE]
  if (nrow(rows_sn) == 0) {
    message(". skip (no remaining spots for sample): ", input_sn,
            "  [mapped-> ", paste(rds_samples, collapse = ","), "]")
    next
  }

  ext <- tolower(tools::file_ext(fp))

  # keep_ids（= 入力側で残す id）を作る
  keep_ids <- numeric(0)
  if (INPUT_MATCH_METHOD %in% c("xy", "xy_tol") && ext %in% c("parquet", "pq")) {
    xy_tol <- if (INPUT_MATCH_METHOD == "xy_tol") INPUT_XY_TOL else 0
    keep_ids <- derive_keep_ids_by_xy(fp, rows_sn, xy_tol = xy_tol)
  } else {
    # 既定: RDS の ID_for_export（= spot_index）と入力の id を一致させる
    keep_ids <- unique(rows_sn$ID_for_export)
  }

  keep_ids <- suppressWarnings(as.numeric(keep_ids))
  keep_ids <- keep_ids[is.finite(keep_ids)]
  if (length(keep_ids) == 0) {
    message(". skip (no matching ids): ", input_sn,
            "  [method=", INPUT_MATCH_METHOD, "]")
    next
  }

  suffix <- if (FILTER_MODE == "exclude") {
    paste0("_EXCL_Cl_", paste(TARGET_CLUSTERS, collapse = "-"))
  } else {
    paste0("_KEEP_Cl_", paste(TARGET_CLUSTERS, collapse = "-"))
  }

  out_ext <- tolower(tools::file_ext(fp))
  out_name <- paste0(input_sn, suffix, ".", out_ext)
  out_fp <- file.path(EXPORT_DATA_DIR, out_name)

  dbg_fp <- file.path(EXPORT_DATA_DIR, paste0(input_sn, suffix, "_debug.tsv"))

  res <- export_filtered_input(fp, out_fp, keep_ids, debug_tsv_path = dbg_fp)
  message(sprintf(">> Exported: %s   (%d / %d rows)", basename(out_fp), res$n_kept, res$n_total))
  exported <- c(exported, out_fp)

  # マージ用sample名マッピング蓄積: rerun側のsample名 → base RDS側のsample名
  rerun_sn <- tools::file_path_sans_ext(out_name)   # = input_sn + suffix
  for (rs in rds_samples) {
    .merge_sample_map[rerun_sn] <- rs
  }
}

.stopif(length(exported) > 0, "出力が0件です（sample名の不一致やクラスタ指定を確認）。")

message("=== Export finished ===")
message("Exported files:")
for (x in exported) message("  - ", x)
.mem_note_orch("フィルタ Parquet 書き出し後（Arrow プールの残留を含む）")

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
  # 置換(apply_reumap_replace)を行う時だけ元オブジェクトを退避する。それ以外(exclude 等で
  # 置換無効)は不要なので解放してから再解析へ進む（再解析コピーは自前で parquet を読むため
  # 元 seu は不要）。判定は下の ENABLE_REUMAP_REPLACE 置換ブロックの実行条件と厳密に一致させる。
  .will_replace <- isTRUE(ENABLE_REUMAP_REPLACE) &&
    !identical(RERUN_PIPELINE_STAGE, "reduction_only")
  if (.will_replace) {
    .base_seu_original <- seu   # 退避（再解析結果とのマージに使う）
  } else {
    rm(seu, rds_obj)            # 両参照を外さないと大行列が解放されない（seu<-rds_obj で共有）
    invisible(gc(verbose = FALSE))
    # gc() 後に RSS が下がらない場合、解放領域が OS へ返らず常駐している（＝解析側の
    # 使える枠がその分減る）。ここが下がるかどうかが対策方針を分ける決定的な指標。
    .mem_note_orch("元オブジェクト解放 + gc 後")
  }

  # [ver45.7] ver45.5 で子プロセス起動(system2)に変えたが、実測で退行が確認されたため
  # source() による同一プロセス実行へ戻す。
  #   子プロセス方式: 親は system2 でブロックしたまま常駐し、子は綺麗なヒープを得る代わりに
  #     「親の解放済み領域を再利用できない」。ピークは 親 + 子 になる。
  #   source() 方式  : 1 プロセスなので、オーケストレータが解放したヒープを解析側が再利用でき、
  #     ピークは max(親, 子) で済む。
  # 実測: 子プロセス方式で 127,901 spot が Step2 ScaleData で OOM。source() 方式では
  # それより多い 139,682 spot が Step3 RPCA まで到達していた。よって source() が有利。
  .mem_note_orch("ver13 コピー実行直前（この値が解析側のベースに積み上がる）")
  source(v13_copy_path)

  message("=== ver13 re-run finished ===")
}


# ------------------------------------------------------------
# (ADD) ReUMAP結果を元UMAPに重ねて、指定クラスタだけ置換（PNG/RDSを保存）
# ------------------------------------------------------------
if (isTRUE(ENABLE_REUMAP_REPLACE) && !identical(RERUN_PIPELINE_STAGE, "reduction_only")) {
  # PreFlight: reduction_only(①診断用)では ReUMAP-replace をスキップ（umap/クラスタ前提のため）
  # keep で抽出したデータのみで ReUMAP していることを前提
  .stopif(FILTER_MODE == "keep",
          "ENABLE_REUMAP_REPLACE=TRUE の場合は FILTER_MODE='keep' を使用してください（抽出データのみでReUMAPする前提）。")

  # rerun RDS path auto-detect (if not specified)
  rerun_rds <- REUMAP_RERUN_RDS_PATH
  if (!nzchar(rerun_rds)) {
    .stopif(isTRUE(RUN_V13_AFTER_EXPORT),
            "RUN_V13_AFTER_EXPORT=FALSE の場合は REUMAP_RERUN_RDS_PATH に ReUMAP後のRDSパスを指定してください。")

    # ver13/ver15 は od <- OUTPUT_DIR として直接出力するためサブディレクトリなし
    od_run <- V13_OUTPUT_DIR
    rerun_rds <- file.path(od_run, "RDS_Files", basename(RDS_RESOLVED))
  }

  .stopif(file.exists(rerun_rds), paste0("ReUMAP側のRDSが見つかりません: ", rerun_rds))
  message(">> Loading ReUMAP (rerun) RDS: ", rerun_rds)
  r2 <- load_rds_compact(rerun_rds)
  rerun_seu <- r2
  if (is.list(r2) && !is.null(r2$obj) && inherits(r2$obj, "Seurat")) rerun_seu <- r2$obj
  .stopif(inherits(rerun_seu, "Seurat"), "ReUMAP側RDSからSeuratオブジェクトを取得できません。")

  # ensure seurat_clusters exists
  if (!("seurat_clusters" %in% colnames(rerun_seu@meta.data))) {
    rerun_seu@meta.data$seurat_clusters <- as.character(Seurat::Idents(rerun_seu))
  }
  if (!("seurat_clusters" %in% colnames(.base_seu_original@meta.data))) {
    .base_seu_original@meta.data$seurat_clusters <- as.character(Seurat::Idents(.base_seu_original))
  }

  # UMAP exists?
  .stopif("umap" %in% names(.base_seu_original@reductions), "元データ側に 'umap' reduction がありません（RDSにUMAPが入っているStep2/Step3を指定してください）。")
  .stopif("umap" %in% names(rerun_seu@reductions), "ReUMAP側に 'umap' reduction がありません（ver13 rerun がUMAPまで完走しているか確認）。")

  # ★ ver57.5 (デバッグ総点検 §5.4): 書き出し時に作った対応表を渡す。
  #   再解析側のサンプル名は `<sample>_KEEP_Cl_8` のように接尾辞が付くため、
  #   元データ側の `<sample>` とは鍵 (`sample|spot_index`) が一致しない。
  #   マージスクリプト呼び出し (下の .should_merge 側) は既に
  #   `.merge_sample_map` を使っていたのに、**こちらだけが生の
  #   SAMPLE_NAME_MAP**（利用者が明示しない限り空）を渡しており、
  #   接尾辞を吸収できず対応付けが原理的に不可能だった。
  apply_reumap_replace(
    base_seu = .base_seu_original,
    rerun_seu = rerun_seu,
    replace_base_clusters = REPLACE_BASE_CLUSTERS,
    sample_name_map = if (length(.merge_sample_map) > 0) .merge_sample_map else SAMPLE_NAME_MAP,
    base_reduction = "umap",
    rerun_reduction = "umap",
    out_dir = REPLACE_OUT_DIR,
    out_prefix = paste0(REPLACE_OUT_PREFIX, "_", paste(REPLACE_BASE_CLUSTERS, collapse = "-"))
  )
}

# ------------------------------------------------------------
# (ADD ver14) マージスクリプトによるサブクラスタ統合
# ------------------------------------------------------------
.should_merge <- FALSE
if (identical(ENABLE_MERGE_CLUSTERS, "auto")) {
  .should_merge <- (FILTER_MODE == "keep") && isTRUE(RUN_V13_AFTER_EXPORT)
} else if (isTRUE(ENABLE_MERGE_CLUSTERS)) {
  .should_merge <- (FILTER_MODE == "keep")
}
# PreFlight: reduction_only(①診断用)では merge をスキップ（クラスタ前提のため）
if (identical(RERUN_PIPELINE_STAGE, "reduction_only")) .should_merge <- FALSE

if (.should_merge && nzchar(MERGE_SCRIPT_PATH) && file.exists(MERGE_SCRIPT_PATH)) {
  message(">> [ver14] Running merge script for sub-cluster integration...")

  # rerun RDS を自動検索（ENABLE_REUMAP_REPLACE と同じロジック）
  merge_rerun_rds <- REUMAP_RERUN_RDS_PATH
  if (!nzchar(merge_rerun_rds)) {
    if (isTRUE(RUN_V13_AFTER_EXPORT)) {
      # ver13/ver15 は od <- OUTPUT_DIR として直接出力するためサブディレクトリなし
      od_run <- V13_OUTPUT_DIR
      merge_rerun_rds <- file.path(od_run, "RDS_Files", basename(RDS_RESOLVED))
    }
  }

  if (nzchar(merge_rerun_rds) && file.exists(merge_rerun_rds)) {
    # マージスクリプトのパラメータを設定してから source
    merge_out <- MERGE_OUT_DIR_OVERRIDE
    if (!nzchar(merge_out)) merge_out <- EXPORT_DATA_DIR

    BASE_RDS_PATH      <- RDS_RESOLVED
    RERUN_RDS_PATH     <- merge_rerun_rds
    MERGE_BASE_CLUSTERS <- TARGET_CLUSTERS
    SUBCLUSTER_NAMING  <- MERGE_SUBCLUSTER_NAMING
    # エクスポート時に構築した rerun_sample→base_sample マッピングを優先使用
    SAMPLE_NAME_MAP    <- if (length(.merge_sample_map) > 0) .merge_sample_map else SAMPLE_NAME_MAP
    MERGE_OUT_DIR      <- merge_out
    MERGE_OUT_PREFIX   <- MERGE_OUT_PREFIX
    BASE_REDUCTION     <- "umap"
    RERUN_REDUCTION    <- "umap"

    message(">> [ver14] base RDS: ", BASE_RDS_PATH)
    message(">> [ver14] rerun RDS: ", RERUN_RDS_PATH)
    message(">> [ver14] merge clusters: ", paste(MERGE_BASE_CLUSTERS, collapse = ", "))
    source(MERGE_SCRIPT_PATH)
  } else {
    message(">> [ver14] Merge skipped: rerun RDS not found at ", merge_rerun_rds)
  }
} else if (.should_merge) {
  message(">> [ver14] Merge skipped: MERGE_SCRIPT_PATH not set or not found.")
}

message("=== DONE: ClusterFilter_ReUMAP for DBSCAN ver14 ===")

# --- 解析レシート: R サイドカー出力（rds_io.R で定義、防御的・失敗しても無害）---
if (exists("write_receipt_sidecar")) try(write_receipt_sidecar(), silent = TRUE)
