# =============================================================================
# MSI Analysis Application - Global Settings
# グローバル設定・定数
# =============================================================================

# ライブラリ読み込み
library(shiny)
library(shinyFiles)
library(shinyjs)
library(bslib)
library(DT)
library(plotly)

# アプリケーションのベースディレクトリ（実行時に自動検出）
# run_app.bat から起動時: getwd() は Other/ になるため、親ディレクトリをベースとする
APP_BASE_DIR <- dirname(getwd())

# DESI用フォルダのパス
DESI_DIR <- file.path(APP_BASE_DIR, "DESI")
DESI_SCRIPT_DIR <- file.path(DESI_DIR, "Script")
DESI_DB_DIR <- file.path(DESI_DIR, "DB")
DESI_DATA_DIR <- file.path(DESI_DIR, "Data")

# TIMS用フォルダのパス
TIMS_DIR <- file.path(APP_BASE_DIR, "TIMS")
TIMS_SCRIPT_DIR <- file.path(TIMS_DIR, "Script")
TIMS_DB_DIR <- file.path(TIMS_DIR, "DB")
TIMS_DATA_DIR <- file.path(TIMS_DIR, "Data")

# DESI用スクリプトのパス
DESI_V8_TEMPLATE_PATH <- file.path(DESI_SCRIPT_DIR, "260130_DESI-UMAP_Template_v9.R")
DESI_CLUSTER_FILTER_PATH <- file.path(DESI_SCRIPT_DIR, "DESI_RDS_ClusterFilter_ver1.R")

# TIMS用スクリプトのパス
TIMS_V8_TEMPLATE_PATH <- file.path(TIMS_SCRIPT_DIR, "260125_DBSCAN_With_cluster_ver13.R")
TIMS_CLUSTER_FILTER_PATH <- file.path(TIMS_SCRIPT_DIR, "260126_DBSCAN_ver13_Cluster_Filter_ReUMAP.R")

# DESI用デフォルト設定
DEFAULT_DESI_DATA_FOLDER <- DESI_DATA_DIR
DEFAULT_MRM_FILE_PATH <- file.path(DESI_DB_DIR, "MRM.xlsx")

# TIMS用デフォルト設定
DEFAULT_TIMS_DATA_FOLDER <- TIMS_DATA_DIR
DEFAULT_ANNOTATION_CSV_PATH <- file.path(TIMS_DB_DIR, "4500_endogenous_metabolites_mod.csv")
DEFAULT_ION_MODE <- "Positive"
DEFAULT_TOLERANCE_MZ <- 0.01
DEFAULT_ADDUCT_POSITIVE <- c("+H", "+Na", "+NH4")
DEFAULT_ADDUCT_NEGATIVE <- c("-H")

# 後方互換性のためのエイリアス
V8_TEMPLATE_PATH <- DESI_V8_TEMPLATE_PATH
CLUSTER_FILTER_PATH <- DESI_CLUSTER_FILTER_PATH
