# =============================================================================
# MSI Analysis Application - Main Entry Point
# Dash アプリケーション エントリポイント
# =============================================================================

import os
from uuid import uuid4

import dash
import dash_bootstrap_components as dbc
import diskcache
from dash.long_callback import DiskcacheManager

from app.config import APP_PORT, APP_HOST, SESSIONS_DIR, OTHER_DIR
from app.layouts.main_layout import create_main_layout

# 前回設定をクリア（毎回クリーンな初期値で起動）
_last_settings = SESSIONS_DIR / "last_settings.json"
if _last_settings.exists():
    _last_settings.unlink(missing_ok=True)

# バックグラウンドコールバック用キャッシュ (Data/Other/cache)
_launch_uid = uuid4()
_cache_dir = OTHER_DIR / "cache"
_cache_dir.mkdir(parents=True, exist_ok=True)
_cache = diskcache.Cache(str(_cache_dir))
_background_manager = DiskcacheManager(
    _cache, cache_by=[lambda: _launch_uid], expire=300,
)

# Dash アプリケーション作成
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY],
    suppress_callback_exceptions=True,
    title="MSI Analysis Application",
    assets_folder="assets",
    background_callback_manager=_background_manager,
)

# Flask サーバーへの参照（画像配信用）
server = app.server

# パスワード認証（クラウドデプロイ用）
# APP_PASSWORD 環境変数が設定されている場合のみ有効化
_app_password = os.environ.get("APP_PASSWORD")
if _app_password:
    import dash_auth
    dash_auth.BasicAuth(app, {"msi": _app_password})

# レイアウト設定
app.layout = create_main_layout()

# コールバック登録
# 各コールバックモジュールの import で自動的に @app.callback が登録される
from app.callbacks import file_handlers  # noqa: E402, F401
from app.callbacks import analysis_callbacks  # noqa: E402, F401
from app.callbacks import results_callbacks  # noqa: E402, F401
from app.callbacks import session_callbacks  # noqa: E402, F401
from app.callbacks import interactive_callbacks  # noqa: E402, F401
from app.callbacks import project_callbacks  # noqa: E402, F401
from app.callbacks import share_callbacks  # noqa: E402, F401
from app.callbacks import preset_callbacks  # noqa: E402, F401
from app.callbacks import interactive_batch_save  # noqa: E402, F401
from app.callbacks import interactive_data_export  # noqa: E402, F401
from app.callbacks import scils_converter_callbacks  # noqa: E402, F401
from app.callbacks import env_settings_callbacks  # noqa: E402, F401

if __name__ == "__main__":
    app.run(debug=True, host=APP_HOST, port=APP_PORT)
