# =============================================================================
# MSI Analysis Application - Global Settings
# グローバル設定・定数
# =============================================================================

import os
import tempfile
from pathlib import Path

# アプリケーションのベースディレクトリ
# app/ の親ディレクトリ = プロジェクトルート (UMAP_Claudecode/)
APP_BASE_DIR = Path(__file__).parent.parent

# R実行環境
R_HOME = Path(os.environ.get("R_HOME", r"C:\Program Files\R\R-4.4.2"))
RSCRIPT_PATH = R_HOME / "bin" / "Rscript.exe"

# DESI用フォルダのパス
DESI_DIR = APP_BASE_DIR / "DESI"
DESI_SCRIPT_DIR = DESI_DIR / "Script"
DESI_DB_DIR = DESI_DIR / "DB"
DESI_DATA_DIR = DESI_DIR / "Data"

# TIMS用フォルダのパス
TIMS_DIR = APP_BASE_DIR / "TIMS"
TIMS_SCRIPT_DIR = TIMS_DIR / "Script"
TIMS_DB_DIR = TIMS_DIR / "DB"
TIMS_DATA_DIR = TIMS_DIR / "Data"

# DESI用スクリプトのパス
DESI_V8_TEMPLATE_PATH = DESI_SCRIPT_DIR / "260130_DESI-UMAP_Template_v9.R"
DESI_CLUSTER_FILTER_PATH = DESI_SCRIPT_DIR / "DESI_RDS_ClusterFilter_ver1.R"

# TIMS用スクリプトのパス
TIMS_V8_TEMPLATE_PATH = TIMS_SCRIPT_DIR / "260125_DBSCAN_With_cluster_ver13.R"
TIMS_CLUSTER_FILTER_PATH = TIMS_SCRIPT_DIR / "260126_DBSCAN_ver13_Cluster_Filter_ReUMAP.R"

# R ヘルパースクリプトのパス
R_HELPERS_DIR = Path(__file__).parent / "r_helpers"

# DESI用デフォルト設定
DEFAULT_DESI_DATA_FOLDER = str(DESI_DATA_DIR)
DEFAULT_MRM_FILE_PATH = str(DESI_DB_DIR / "MRM.xlsx")

# TIMS用デフォルト設定
DEFAULT_TIMS_DATA_FOLDER = str(TIMS_DATA_DIR)
DEFAULT_ANNOTATION_CSV_PATH = str(TIMS_DB_DIR / "4500_endogenous_metabolites_mod.csv")
DEFAULT_ION_MODE = "Positive"
DEFAULT_TOLERANCE_MZ = 0.01
DEFAULT_ADDUCT_POSITIVE = ["+H", "+Na", "+NH4"]
DEFAULT_ADDUCT_NEGATIVE = ["-H"]

# 後方互換性のためのエイリアス
V8_TEMPLATE_PATH = DESI_V8_TEMPLATE_PATH
CLUSTER_FILTER_PATH = DESI_CLUSTER_FILTER_PATH

# セッションディレクトリ
SESSIONS_DIR = APP_BASE_DIR / "app" / "sessions"

# プロジェクトディレクトリ・ファイル
PROJECTS_DIR = APP_BASE_DIR / "app" / "projects"
PROJECTS_FILE = PROJECTS_DIR / "projects.json"

# Seuratブリッジ キャッシュディレクトリ
# 日本語パスを含むとRscript.exeが文字化けするため、tempdir（ASCII安全）を使用
SEURAT_CACHE_DIR = Path(tempfile.gettempdir()) / "msi_seurat_cache"

# クラスタ色パレット（Rスクリプト UMAP_DISTINCT_COLORS_50 と同一）
DESI_COLORS_50 = [
    "#FF2D2D", "#1E5BFF", "#00A650", "#B000FF", "#FF8C00",
    "#00D5FF", "#A52A2A", "#FF1493", "#7A7A7A", "#00C27A",
    "#FFD400", "#2F4F4F", "#8B4513", "#00FF00", "#000000",
    "#FF00FF", "#00FFFF", "#800000", "#008000", "#000080",
    "#808000", "#800080", "#008080", "#FF4500", "#17BECF",
    "#BCBD22", "#9467BD", "#8C564B", "#2CA02C", "#1F77B4",
    "#D62728", "#AEC7E8", "#98DF8A", "#FF9896", "#C49C94",
    "#F7B6D2", "#C7C7C7", "#DBDB8D", "#9EDAE5", "#E41A1C",
    "#377EB8", "#4DAF4A", "#984EA3", "#FF7F00", "#FFFF33",
    "#A65628", "#F781BF", "#999999", "#66A61E", "#E6AB02",
]
HIGHLIGHT_GRAY = "#D9D9D9"

# アプリケーション設定
APP_VERSION = "2.0.0"
APP_PORT = 3838
APP_HOST = "127.0.0.1"
