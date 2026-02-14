# =============================================================================
# MSI Analysis Application - Main Entry Point
# Dash アプリケーション エントリポイント
# =============================================================================

import dash
import dash_bootstrap_components as dbc

from app.config import APP_PORT, APP_HOST
from app.layouts.main_layout import create_main_layout

# Dash アプリケーション作成
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY],
    suppress_callback_exceptions=True,
    title="MSI Analysis Application",
    assets_folder="assets",
)

# Flask サーバーへの参照（画像配信用）
server = app.server

# レイアウト設定
app.layout = create_main_layout()

# コールバック登録
# 各コールバックモジュールの import で自動的に @app.callback が登録される
from app.callbacks import file_handlers  # noqa: E402, F401
from app.callbacks import analysis_callbacks  # noqa: E402, F401
from app.callbacks import results_callbacks  # noqa: E402, F401
from app.callbacks import session_callbacks  # noqa: E402, F401
from app.callbacks import interactive_callbacks  # noqa: E402, F401


if __name__ == "__main__":
    app.run(debug=True, host=APP_HOST, port=APP_PORT)
