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
# Note: DiskcacheManager は各 background_callback 起動毎に
# multiprocess.Process を spawn する仕様のため、明示的な workers 設定は不要。
# 複数ユーザーの background_callback (load_interactive_data, cb_export_report)
# は自動的に並列実行される。リソース制限は OS / Docker レベルで管理。
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

# ヘルスチェック用エンドポイント（BasicAuth をバイパスする軽量応答）
# Docker healthcheck や外部監視サービスから利用される。
# dash_auth.BasicAuth より前に before_request を登録することで、
# /healthz は認証無しで応答する。
from flask import request as _flask_request  # noqa: E402


@server.before_request
def _healthz_bypass():
    if _flask_request.path == "/healthz":
        return ("OK", 200, {"Content-Type": "text/plain"})


@server.before_request
def _ensure_session_id():
    """全リクエストで Cookie セッション ID を確保（/healthz は除外）。

    複数ユーザー識別のため各ブラウザに UUID を発行する。
    """
    if _flask_request.path == "/healthz":
        return  # ヘルスチェックは Cookie 不要
    from app.services.session_id import get_or_create_session_id
    get_or_create_session_id()  # 副作用で after_this_request 経由で set-cookie


# 認証設定 (クラウドデプロイ用)
#   優先順位 1: APP_USERS=alice:pw1,bob:pw2,...  → 複数ユーザー BasicAuth
#   優先順位 2: APP_PASSWORD=...                → 単一 "msi" ユーザー (後方互換)
#   両方未設定                                 → 認証なし (ローカル開発時)
from app.services.session_id import parse_app_users

_app_users = parse_app_users(os.environ.get("APP_USERS", ""))
_app_password = os.environ.get("APP_PASSWORD")

_auth_dict = None
if _app_users:
    _auth_dict = _app_users
elif _app_password and _app_password != "CHANGE_ME_BEFORE_DEPLOY":
    # CHANGE_ME_BEFORE_DEPLOY のプレースホルダーで認証を有効化しない
    _auth_dict = {"msi": _app_password}

if _auth_dict:
    import dash_auth
    dash_auth.BasicAuth(app, _auth_dict)

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
from app.callbacks import lite_view_callbacks  # noqa: E402, F401
from app.callbacks import rds_maintenance_callbacks  # noqa: E402, F401
from app.callbacks import edit_lock_callbacks  # noqa: E402, F401

if __name__ == "__main__":
    # Docker CMD は run_app.py 経由。ここは bare-metal 開発用のフォールバック。
    # 本番環境でもブラウザに stack trace を漏らさないよう debug=False で固定。
    app.run(debug=False, host=APP_HOST, port=APP_PORT)
