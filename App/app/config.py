# =============================================================================
# MSI Analysis Application - Global Settings
# グローバル設定・定数
# =============================================================================

import os
import platform
import shutil
import tempfile
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # .env ファイルがあれば読み込み

# アプリケーションのベースディレクトリ
# App/app/config.py → parent.parent.parent = プロジェクトルート (UMAP/)
APP_BASE_DIR = Path(__file__).parent.parent.parent
APP_DIR = APP_BASE_DIR / "App"
DATA_DIR = APP_BASE_DIR / "Data"

# R実行環境 (OS 別デフォルト + PATH フォールバック)
_SYS = platform.system()
if _SYS == "Windows":
    _R_DEFAULT = r"C:\Program Files\R\R-4.4.2"
elif _SYS == "Darwin":
    _R_DEFAULT = "/Library/Frameworks/R.framework/Resources"  # CRAN 公式 macOS
else:
    _R_DEFAULT = "/usr/lib/R"  # Linux

R_HOME = Path(os.environ.get("R_HOME", _R_DEFAULT))
_RSCRIPT_NAME = "Rscript.exe" if _SYS == "Windows" else "Rscript"
RSCRIPT_PATH = R_HOME / "bin" / _RSCRIPT_NAME

# R_HOME/bin/Rscript が存在しない場合、PATH 上の Rscript を拾う (Homebrew 等)
if not RSCRIPT_PATH.exists():
    _which = shutil.which(_RSCRIPT_NAME) or shutil.which("Rscript")
    if _which:
        RSCRIPT_PATH = Path(_which)

# DESI用フォルダのパス
DESI_SCRIPT_DIR = APP_DIR / "Script" / "DESI"
DESI_DB_DIR = APP_DIR / "DB" / "DESI"
# 生データ格納先: .env の DESI_DATA_DIR で外部パスに切り替え可能。
# 未設定時は同梱の Data/DESI/Data を参照。
DESI_DATA_DIR = Path(os.environ.get("DESI_DATA_DIR", str(DATA_DIR / "DESI" / "Data")))
DESI_DATA_DIR_LEGACY = DATA_DIR / "DESI" / "Data"  # 旧パス (fallback 検索用)
DESI_DIR = DATA_DIR / "DESI"  # 後方互換 (Data 配下)

# TIMS用フォルダのパス
TIMS_SCRIPT_DIR = APP_DIR / "Script" / "TIMS"
TIMS_DB_DIR = APP_DIR / "DB" / "TIMS"
TIMS_DATA_DIR = Path(os.environ.get("TIMS_DATA_DIR", str(DATA_DIR / "TIMS" / "Data")))
TIMS_DATA_DIR_LEGACY = DATA_DIR / "TIMS" / "Data"
TIMS_DIR = DATA_DIR / "TIMS"  # 後方互換

# データ探索時に順番に検索する候補リスト (path_resolver が使用)
DESI_DATA_CANDIDATES = [DESI_DATA_DIR, DESI_DATA_DIR_LEGACY]
TIMS_DATA_CANDIDATES = [TIMS_DATA_DIR, TIMS_DATA_DIR_LEGACY]

# Common（DESI/TIMS共通）スクリプトのパス
COMMON_SCRIPT_DIR = APP_DIR / "Script" / "Common"
# Data/Other/ — アプリ内部データ (セッション/プロジェクト/プリセット/共有/キャッシュ/ログ/出力/Common)
OTHER_DIR = DATA_DIR / "Other"
COMMON_DIR = OTHER_DIR / "Common"  # 後方互換
MERGE_CLUSTERS_SCRIPT_PATH = COMMON_SCRIPT_DIR / "UMAP_Merge_Clusters_ver1.R"

# 解析出力先 (output_dir): .env の OUTPUT_DATA_DIR で外部パスに切り替え可能。
# 未設定時は Data/Other/output を参照。
OUTPUT_DATA_DIR = Path(os.environ.get("OUTPUT_DATA_DIR", str(OTHER_DIR / "output")))
OUTPUT_DATA_DIR_LEGACY = OTHER_DIR / "output"  # 旧パス (fallback 検索用)
OUTPUT_DATA_CANDIDATES = [OUTPUT_DATA_DIR, OUTPUT_DATA_DIR_LEGACY]

# file_browser_modal 上部に表示するショートカットボタン定義
# (key: 内部識別子 / label: 日本語表示 / path: 遷移先絶対パス)
BROWSER_SHORTCUTS = [
    {"key": "desi", "label": "DESI生データ", "path": str(DESI_DATA_DIR)},
    {"key": "tims", "label": "TIMS生データ", "path": str(TIMS_DATA_DIR)},
    {"key": "output", "label": "解析出力", "path": str(OUTPUT_DATA_DIR)},
    {"key": "internal", "label": "アプリ内部データ", "path": str(OTHER_DIR)},
]

# DESI用スクリプトのパス
# v15: UMAP/クラスタリングのハイパーパラメータを明示定数化（UI注入・記録の土台。挙動は v14 と同一）。
#      旧 v14 はロールバック用に温存。
DESI_V8_TEMPLATE_PATH = DESI_SCRIPT_DIR / "260619_DESI-UMAP_Template_v15.R"
# ver3: 再解析にも PreFlight ループ（reduction_only 再解析）を通すための版。
#       旧 ver2 はロールバック用に温存。
DESI_CLUSTER_FILTER_PATH = DESI_SCRIPT_DIR / "DESI_RDS_ClusterFilter_ver3.R"

# TIMS用スクリプトのパス
# ver5: 同上（UMAPハイパラ明示化。挙動は ver4 と同一）。旧 ver4 は温存。
TIMS_V8_TEMPLATE_PATH = TIMS_SCRIPT_DIR / "260619_DBSCAN_With_cluster_ver5_no-png_slim.R"
# ver18: 再解析にも PreFlight ループ（reduction_only 再解析）を通すための版。
#        旧 ver17 はロールバック用に温存。
TIMS_CLUSTER_FILTER_PATH = TIMS_SCRIPT_DIR / "260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R"

# R ヘルパースクリプトのパス (App/Script/helpers/)
R_HELPERS_DIR = APP_DIR / "Script" / "helpers"

# PreFlight 診断 CLI (run_diagnostics.R) のパス
RUN_DIAGNOSTICS_PATH = R_HELPERS_DIR / "run_diagnostics.R"

# DESI用デフォルト設定
DEFAULT_DESI_DATA_FOLDER = str(DESI_DATA_DIR)
DEFAULT_ANNOTATION_FILE_PATH = str(DESI_DB_DIR / "263010-MRM.xlsx")

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

# セッションディレクトリ (Data/Other/ 配下 — App/ 差し替えで失われない)
SESSIONS_DIR = OTHER_DIR / "sessions"

# プロジェクトディレクトリ・ファイル
PROJECTS_DIR = OTHER_DIR / "projects"
PROJECTS_FILE = PROJECTS_DIR / "projects.json"

# プリセットディレクトリ
PRESETS_DIR = OTHER_DIR / "presets"

# 共有リンク管理
SHARES_DIR = OTHER_DIR / "shares"
SHARES_FILE = SHARES_DIR / "shares.json"
DEFAULT_SHARE_EXPIRY_DAYS = 30
# 共有URL生成用ベースURL
# - 空の場合: LAN IPを自動検出（LAN内共有用）
# - Cloudflare Tunnel使用時:
#     1. cloudflared をインストール: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
#     2. Quick Tunnel: cloudflared tunnel --url http://localhost:3838
#     3. 表示されたURLをここに設定: "https://xxxx.trycloudflare.com"
#     4. 固定ドメインの場合: cloudflared tunnel run --url http://localhost:3838 <tunnel-name>
SHARE_BASE_URL = os.environ.get("SHARE_BASE_URL", "")

# Seuratブリッジ キャッシュディレクトリ
# 日本語パスを含むとRscript.exeが文字化けするため、tempdir（ASCII安全）を既定とする。
# ver4.4: SEURAT_CACHE_DIR env で永続ボリューム上のパスに上書き可能
# (Docker では /tmp が再デプロイで消えるため、永続化して共有のコールド抽出を防ぐ)。
_seurat_cache_env = os.environ.get("SEURAT_CACHE_DIR", "").strip()
SEURAT_CACHE_DIR = (Path(_seurat_cache_env) if _seurat_cache_env
                    else Path(tempfile.gettempdir()) / "msi_seurat_cache")

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
APP_VERSION = "2.1.0"
APP_PORT = int(os.environ.get("APP_PORT", "3838"))
APP_HOST = os.environ.get("APP_HOST", "0.0.0.0")  # LAN + Cloudflare Tunnel 対応

# UI ロック設定（複数ユーザー同時編集時の排他制御）
# EDIT_LOCK_TIMEOUT_SEC: ロック自動解放までの秒数 (heartbeat で延長される)
# EDIT_LOCK_HEARTBEAT_INTERVAL_SEC: ブラウザ → サーバの heartbeat 間隔
#   通常 TIMEOUT の 1/3 を推奨 (10s/30s, 30s/90s, 60s/300s 等)
EDIT_LOCK_TIMEOUT_SEC = int(os.environ.get("EDIT_LOCK_TIMEOUT_SEC", "30"))
EDIT_LOCK_HEARTBEAT_INTERVAL_SEC = int(
    os.environ.get("EDIT_LOCK_HEARTBEAT_INTERVAL_SEC", "10")
)
