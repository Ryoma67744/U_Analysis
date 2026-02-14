# =============================================================================
# MSI Analysis Application
# 質量分析イメージング(MSI)データ解析Webアプリケーション
# =============================================================================
#
# このファイルはアプリケーションのエントリポイントです。
# 各機能は R/ ディレクトリ内のモジュールに分割されています。
#
# モジュール構成:
#   R/global.R              - グローバル設定・定数
#   R/ui_main.R             - メインUI構造
#   R/ui_sidebar.R          - サイドバーUI
#   R/ui_settings.R         - 解析設定タブUI
#   R/ui_results.R          - 結果閲覧タブUI
#   R/ui_interactive.R      - インタラクティブ解析タブUI
#   R/server_file_handlers.R - ファイル参照ハンドラー
#   R/server_analysis.R     - 解析実行・ログ監視
#   R/server_results.R      - 結果閲覧ハンドラー
#   R/server_session.R      - セッション管理ハンドラー
#   R/server_interactive.R  - インタラクティブ解析ハンドラー
#   R/data_manager.R        - データ管理ユーティリティ
#   R/analysis_runner.R     - 解析エンジン
#   R/results_viewer.R      - 結果表示ユーティリティ
#   R/session_manager.R     - セッション管理ユーティリティ
#   R/interactive_analysis.R - インタラクティブ解析ユーティリティ
#
# =============================================================================

# グローバル設定・ライブラリ読み込み
source("R/global.R", encoding = "UTF-8")

# ユーティリティモジュール
source("R/data_manager.R", encoding = "UTF-8")
source("R/analysis_runner.R", encoding = "UTF-8")
source("R/results_viewer.R", encoding = "UTF-8")
source("R/session_manager.R", encoding = "UTF-8")
source("R/interactive_analysis.R", encoding = "UTF-8")

# UIモジュール
source("R/ui_sidebar.R", encoding = "UTF-8")
source("R/ui_settings.R", encoding = "UTF-8")
source("R/ui_results.R", encoding = "UTF-8")
source("R/ui_interactive.R", encoding = "UTF-8")
source("R/ui_main.R", encoding = "UTF-8")

# サーバーモジュール
source("R/server_file_handlers.R", encoding = "UTF-8")
source("R/server_analysis.R", encoding = "UTF-8")
source("R/server_results.R", encoding = "UTF-8")
source("R/server_session.R", encoding = "UTF-8")
source("R/server_interactive.R", encoding = "UTF-8")

# =============================================================================
# UI定義
# =============================================================================
ui <- create_main_ui()

# =============================================================================
# Server定義
# =============================================================================
server <- function(input, output, session) {
  # ボリューム設定
  volumes <- get_file_volumes()

  # リアクティブ値
  rv <- reactiveValues(
    # 解析状態
    is_running = FALSE,
    stop_requested = FALSE,
    current_section = 0,
    total_sections = 0,
    analysis_log = character(),
    log_file = NULL,
    status_file = NULL,
    full_output_dir = NULL,
    selected_samples_list = character(),
    analysis_start_time = NULL,

    # 結果閲覧
    gallery_page = 1,
    selected_image = NULL,
    image_zoom = 1,
    manual_result_folder = NULL
  )

  # ハンドラー登録
  register_file_handlers(input, output, session, volumes)
  register_analysis_handlers(input, output, session, rv)
  register_results_handlers(input, output, session, rv, volumes)
  register_session_handlers(input, output, session)
  register_interactive_handlers(input, output, session, volumes)
}

# =============================================================================
# アプリケーション起動
# =============================================================================
shinyApp(ui = ui, server = server)
