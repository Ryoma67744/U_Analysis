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
DESI_DIR = APP_BASE_DIR / "data" / "DESI"
DESI_SCRIPT_DIR = DESI_DIR / "Script"
DESI_DB_DIR = DESI_DIR / "DB"
DESI_DATA_DIR = DESI_DIR / "Data"

# TIMS用フォルダのパス
TIMS_DIR = APP_BASE_DIR / "data" / "TIMS"
TIMS_SCRIPT_DIR = TIMS_DIR / "Script"
TIMS_DB_DIR = TIMS_DIR / "DB"
TIMS_DATA_DIR = TIMS_DIR / "Data"

# Common（DESI/TIMS共通）スクリプトのパス
COMMON_DIR = APP_BASE_DIR / "data" / "Common"
COMMON_SCRIPT_DIR = COMMON_DIR / "Script"
MERGE_CLUSTERS_SCRIPT_PATH = COMMON_SCRIPT_DIR / "UMAP_Merge_Clusters_ver1.R"

# DESI用スクリプトのパス
DESI_V8_TEMPLATE_PATH = DESI_SCRIPT_DIR / "260130_DESI-UMAP_Template_v9.R"
DESI_CLUSTER_FILTER_PATH = DESI_SCRIPT_DIR / "DESI_RDS_ClusterFilter_ver2.R"

# TIMS用スクリプトのパス
TIMS_V8_TEMPLATE_PATH = TIMS_SCRIPT_DIR / "260306_DBSCAN_With_cluster_ver15.R"
TIMS_CLUSTER_FILTER_PATH = TIMS_SCRIPT_DIR / "260306_DBSCAN_ver15_Cluster_Filter_ReUMAP.R"

# R ヘルパースクリプトのパス
R_HELPERS_DIR = Path(__file__).parent / "r_helpers"

# DESI用デフォルト設定
DEFAULT_DESI_DATA_FOLDER = str(DESI_DATA_DIR)
DEFAULT_ANNOTATION_FILE_PATH = str(DESI_DB_DIR / "MRM.xlsx")

# TIMS用デフォルト設定
DEFAULT_TIMS_DATA_FOLDER = str(TIMS_DATA_DIR)
DEFAULT_ANNOTATION_CSV_PATH = str(TIMS_DB_DIR / "4500_endogenous_metabolites_mod.csv")
DEFAULT_ION_MODE = "Positive"
DEFAULT_TOLERANCE_MZ = 0.01
DEFAULT_ADDUCT_POSITIVE = ["+H", "+Na", "+NH4", "+K"]
DEFAULT_ADDUCT_NEGATIVE = ["-H"]

# m/z キャリブレーション
DEFAULT_CALIBRATION_ENABLE = False
DEFAULT_CALIBRATION_MATRIX = "DHB"
DEFAULT_CALIBRATION_SEARCH_WINDOW = 0.5  # Da
DEFAULT_CALIBRATION_MIN_PEAKS = 2
DEFAULT_CALIBRATION_REGRESSION = "poly3"  # "linear" | "poly2" | "poly3"

MATRIX_REFERENCE_MZ = {
    "DHB": {
        "Positive": [137.0233, 155.0339, 177.0158, 273.0399],
        "Negative": [153.0193, 136.0166],
    },
    "CHCA": {
        "Positive": [190.0499, 172.0393, 212.0318, 379.0925],
        "Negative": [188.0353],
    },
    "9AA": {
        "Positive": [195.0917],
        "Negative": [193.0771],
    },
}

# 後方互換性のためのエイリアス
V8_TEMPLATE_PATH = DESI_V8_TEMPLATE_PATH
CLUSTER_FILTER_PATH = DESI_CLUSTER_FILTER_PATH

# セッションディレクトリ
SESSIONS_DIR = APP_BASE_DIR / "app" / "sessions"

# プロジェクトディレクトリ・ファイル
PROJECTS_DIR = APP_BASE_DIR / "app" / "projects"
PROJECTS_FILE = PROJECTS_DIR / "projects.json"

# プリセットディレクトリ
PRESETS_DIR = APP_BASE_DIR / "app" / "presets"

# 共有リンク管理
SHARES_DIR = APP_BASE_DIR / "app" / "shares"
SHARES_FILE = SHARES_DIR / "shares.json"
DEFAULT_SHARE_EXPIRY_DAYS = 30
# 共有URL生成用ベースURL
# - 空の場合: LAN IPを自動検出（LAN内共有用）
# - Cloudflare Tunnel使用時:
#     1. cloudflared をインストール: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
#     2. Quick Tunnel: cloudflared tunnel --url http://localhost:3838
#     3. 表示されたURLをここに設定: "https://xxxx.trycloudflare.com"
#     4. 固定ドメインの場合: cloudflared tunnel run --url http://localhost:3838 <tunnel-name>
SHARE_BASE_URL = "https://administered-exercises-dude-give.trycloudflare.com"

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
HIGHLIGHT_GRAY = "#808080"  # RGB(128,128,128) — 50%灰色

# クラスタ色プリセットパレット（科学論文向け30色、白なし）
# NPG, JCO, ColorBrewer Dark2/Set1/Set2 から選定
# インタラクティブ解析のデフォルト配色としても使用
CLUSTER_PRESET_COLORS = [
    "#E64B35",  # NPG赤
    "#4DBBD5",  # NPGシアン
    "#00A087",  # NPGティール
    "#3C5488",  # NPGネイビー
    "#F39B7F",  # NPGサーモン
    "#8491B4",  # NPGスレートブルー
    "#91D1C2",  # NPGミント
    "#DC9600",  # NPGゴールド
    "#7E6148",  # NPGブラウン
    "#B09C85",  # NPGトープ
    "#0073C2",  # JCOブルー
    "#EFC000",  # JCOイエロー
    "#CD534C",  # JCOレッド
    "#7AA6DC",  # JCOライトブルー
    "#003C67",  # JCOダークブルー
    "#8F7700",  # JCOオリーブ
    "#A73030",  # JCOダークレッド
    "#4A6990",  # JCOスチールブルー
    "#E7298A",  # Dark2ピンク
    "#66A61E",  # Dark2グリーン
    "#E6AB02",  # Dark2アンバー
    "#A6761D",  # Dark2シエナ
    "#1B9E77",  # Dark2ティールグリーン
    "#D95F02",  # Dark2オレンジ
    "#7570B3",  # Dark2パープル
    "#984EA3",  # Set1パープル
    "#66C2A5",  # Set2ティール
    "#FC8D62",  # Set2コーラル
    "#377EB8",  # Set1ブルー
    "#4DAF4A",  # Set1グリーン
]

# アプリケーション設定
APP_VERSION = "2.0.0"
APP_PORT = 3838
APP_HOST = "0.0.0.0"  # LAN + Cloudflare Tunnel 対応
