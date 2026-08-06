# =============================================================================
# MSI Analysis Application - Main Entry Point
# Dash アプリケーション エントリポイント
# =============================================================================

import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import dash
import dash_bootstrap_components as dbc
import diskcache
from dash.long_callback import DiskcacheManager
from flask import Flask

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


def _bg_cache_key_contributor():
    """DiskcacheManager の cache_by 用関数。

    キャッシュキーに以下を含めることで、複数ユーザー並行実行時の
    progress/result 混線を防ぐ:
    - _launch_uid: アプリ再起動時のキャッシュ無効化用 (既存挙動)
    - session_id: ユーザー (ブラウザ) 単位でキャッシュ分離

    Flask request context 外で呼ばれた場合は "no-session" を返し例外を投げない。
    """
    try:
        from flask import has_request_context, request
        if has_request_context():
            sid = request.cookies.get("msi_session_id", "no-session")
        else:
            sid = "no-session"
    except Exception:
        sid = "no-session"
    return f"{_launch_uid}:{sid}"


# Note: DiskcacheManager は各 background_callback 起動毎に
# multiprocess.Process を spawn する仕様のため、明示的な workers 設定は不要。
# 複数ユーザーの background_callback (load_interactive_data, cb_export_report)
# は自動的に並列実行される。リソース制限は OS / Docker レベルで管理。
_background_manager = DiskcacheManager(
    _cache, cache_by=[_bg_cache_key_contributor], expire=300,
)

# Dash アプリケーション作成
# 認証ログイン画面のテンプレート用に Flask インスタンスを明示生成
_templates_dir = Path(__file__).parent / "templates"
_flask_server = Flask(__name__, template_folder=str(_templates_dir))
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY],
    suppress_callback_exceptions=True,
    title="MSI Analysis Application",
    assets_folder="assets",
    background_callback_manager=_background_manager,
    server=_flask_server,
    # ver46.1: コールバック応答 (Plotly figure JSON) を gzip 圧縮する。
    # figure JSON は座標・色の数値が延々と並ぶため 8〜12 倍に縮む。Dash の既定は
    # 圧縮なしで、数 MB〜数十 MB がそのまま流れていた。
    # 本番は Caddy の `encode` でも圧縮されるが、リバースプロキシを通さない
    # 直アクセス (docker-compose 単体 / E2E テスト) でも効かせるためここでも有効化する。
    # 既に Content-Encoding が付いた応答を Caddy が二重圧縮することはない。
    compress=True,
)

# Flask サーバーへの参照（画像配信用 / 認証ミドルウェア用）
server = app.server

# Flask セッション設定 (Tier A/B 認証用)
# SECRET_KEY は環境変数必須 (未設定なら起動失敗 = フェイルファースト)
_secret_key = os.environ.get("FLASK_SECRET_KEY", "").strip()
if not _secret_key:
    raise RuntimeError(
        "FLASK_SECRET_KEY env var is required (32+ random bytes). "
        "Generate with: openssl rand -hex 32"
    )
server.config["SECRET_KEY"] = _secret_key
server.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    seconds=int(os.environ.get("SESSION_COOKIE_MAX_AGE_SEC", 86400))
)
server.config["SESSION_COOKIE_HTTPONLY"] = True
server.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# HTTPS 経由時は Flask セッション Cookie も secure 化
# (msi_session_id Cookie は session_id.py 側で動的判定)
server.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("SESSION_COOKIE_SECURE", "").lower() in ("1", "true", "yes")
)

# ヘルスチェック用エンドポイント（認証をバイパスする軽量応答）
# Docker healthcheck や外部監視サービスから利用される。
# auth_middleware._require_login より前に before_request を登録することで、
# /healthz は認証無しで応答する。
from flask import request as _flask_request  # noqa: E402


@server.before_request
def _healthz_bypass():
    """ヘルスチェック endpoint。認証 hook より前で応答。

    /healthz       → 軽量応答 (Docker / load balancer 用)。プロセス生存のみ確認。
    /healthz/ready → Dash の callback registry / layout を実検査 (READY 確認用)。
                     起動直後の不完全な状態を弾けるため、CI/CD / deploy 時推奨。
    """
    if _flask_request.path == "/healthz":
        return ("OK", 200, {"Content-Type": "text/plain"})
    if _flask_request.path == "/metrics":
        # PR-H5 E7: psutil ベースの軽量メトリクス。auth bypass で外部監視から取得可能。
        # Prometheus-style ではなく単純な key=value 形式 (依存軽量化)。
        try:
            import psutil
            import os as _os
            proc = psutil.Process(_os.getpid())
            with proc.oneshot():
                rss = proc.memory_info().rss
                vms = proc.memory_info().vms
                num_fds = proc.num_fds() if hasattr(proc, "num_fds") else 0
                threads = proc.num_threads()
                cpu_pct = proc.cpu_percent(interval=None)
            try:
                from app.callbacks.interactive_callbacks import get_project_states_size
                ps_size = get_project_states_size()
            except Exception:
                ps_size = -1
            try:
                cache_size_mb = sum(
                    f.stat().st_size for f in _cache_dir.rglob("*") if f.is_file()
                ) / (1024 * 1024)
            except Exception:
                cache_size_mb = -1
            body = (
                f"rss_bytes={rss}\n"
                f"vms_bytes={vms}\n"
                f"num_fds={num_fds}\n"
                f"num_threads={threads}\n"
                f"cpu_percent={cpu_pct:.1f}\n"
                f"project_states_size={ps_size}\n"
                f"diskcache_mb={cache_size_mb:.1f}\n"
            )
            return (body, 200, {"Content-Type": "text/plain"})
        except Exception as e:
            return (f"metrics_error: {type(e).__name__}\n", 500,
                    {"Content-Type": "text/plain"})

    if _flask_request.path == "/healthz/ready":
        # Dash の中身を軽く検査:
        # 1. layout が None でない (起動完了)
        # 2. _dash-dependencies 経由で callback registry が機能
        # 3. project_manager の projects.json アクセス可能
        try:
            if app.layout is None:
                return ("NOT_READY: layout missing", 503,
                        {"Content-Type": "text/plain"})
            # Dash 公式 API: 内部 _callback_list に登録された callback 数
            # (起動完了後は数百規模になる)
            try:
                cb_count = len(getattr(app, "_callback_list", []) or [])
            except Exception:
                cb_count = -1
            # projects.json (PR-E のプロジェクト分離基盤) が機能するか
            from app.services.project_manager import list_projects
            _ = list_projects()
            return (f"READY callbacks={cb_count}", 200,
                    {"Content-Type": "text/plain"})
        except Exception as e:
            return (f"NOT_READY: {type(e).__name__}", 503,
                    {"Content-Type": "text/plain"})


@server.before_request
def _ensure_session_id():
    """全リクエストで Cookie セッション ID を確保（/healthz は除外）。

    複数ユーザー識別のため各ブラウザに UUID を発行する。
    """
    if _flask_request.path == "/healthz":
        return  # ヘルスチェックは Cookie 不要
    from app.services.session_id import get_or_create_session_id
    get_or_create_session_id()  # 副作用で after_this_request 経由で set-cookie


@server.after_request
def _security_headers(response):
    """PR-H5 E8: セキュリティヘッダー (clickjacking / MIME sniff / referrer)。

    Caddy 経由でも同等のヘッダーを設定しているが、Flask 直接アクセス
    (ローカル開発 / リバプロバイパス) でも 安全性を確保。
    """
    # /healthz / /metrics は monitoring 用なので header は最小限に
    if _flask_request.path in ("/healthz", "/healthz/ready", "/metrics"):
        return response
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # CSP: Dash は inline script を使うため 'unsafe-inline' が必要。
    # style-src/font-src/connect-src で関連 CDN とフォント供給元 (Google Fonts)
    # を許可。これは dbc.themes.FLATLY が cdn.jsdelivr.net から
    # Bootswatch テーマ CSS を読み込むため必須 (許可しないと素の Bootstrap
    # 表示になりボタンの色等が崩れる)。
    # frame-ancestors 'none' で iframe 埋込禁止。
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline' "
        "https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data: "
        "https://cdn.jsdelivr.net https://fonts.gstatic.com; "
        "connect-src 'self' https://cdn.jsdelivr.net; "
        "frame-ancestors 'none'",
    )
    return response


# 認証設定 (Tier A: 解析者用 / Tier B: 共有 URL 閲覧用)
# - 初回起動時は INITIAL_PASSWORD_A/B から auth.json を生成
# - before_request hook で全リクエストを Tier 判定
# - 既存 _healthz_bypass / _ensure_session_id の後に登録 (順序重要)
from app.services import auth_service  # noqa: E402
from app.services.auth_middleware import register as register_auth  # noqa: E402

auth_service.init_from_env()
register_auth(server)

# ver41.0: ChatGPT 連携用の読み取り専用 API (/api/gpt/*)。
# auth_middleware は /api/gpt/ を bypass するため、ここで独自の X-API-Key で保護する。
# register_auth の後に登録し、before_request チェーンで _require_login → _gpt_before_request の順に評価される。
from app.services.gpt_api import register_gpt_api  # noqa: E402

register_gpt_api(server)


# ヘルプページ（取扱説明書）: 認証不要
# auth_middleware._BYPASS_PREFIXES に "/help/" を登録済み
from flask import render_template as _render_template  # noqa: E402
from flask import (  # noqa: E402
    send_file as _send_file, make_response as _make_response, abort as _abort,
)
from app.version import version_label as _version_label  # noqa: E402


@server.route("/help/registration")
def _help_registration():
    """登録画面（ランディングページ）の取扱説明書を別タブで表示"""
    return _render_template("help/registration.html", app_version=_version_label())


@server.route("/help/analysis")
def _help_analysis():
    """解析画面の取扱説明書を別タブで表示"""
    return _render_template("help/analysis.html", app_version=_version_label())


# ver3.9: プロジェクトサムネ配信 (キャッシュ済 JPG / フォールバック透明 PNG)
# 認証は Tier A 必須 (auth_middleware の bypass に含めない)
@server.route("/api/project_thumb/<project_id>")
def _project_thumbnail(project_id):
    """プロジェクトカード用サムネ画像を配信する。

    - 解析結果あり: source 画像から生成した 60x60 JPG (cache hit で即時)
    - 解析結果なし: 透明 1x1 PNG をフォールバック (Img タグの broken icon 抑止)
    """
    import base64
    try:
        from app.services.project_manager import get_project
        from app.services.thumbnail_service import (
            get_thumbnail_path, resolve_thumbnail_source,
        )
        project = get_project(project_id)
        if project:
            source = resolve_thumbnail_source(project)
            if source:
                cache_path = get_thumbnail_path(project_id, source)
                if cache_path:
                    response = _send_file(
                        str(cache_path), mimetype="image/jpeg"
                    )
                    response.headers["Cache-Control"] = "public, max-age=3600"
                    return response
    except Exception as e:
        # エラー時もフォールバック画像を返す (UI 崩れ防止)
        import logging as _logging
        _logging.getLogger("msi.thumb").warning(
            "thumbnail route error project=%s: %s", project_id, e,
        )
    # フォールバック: 透明 1x1 PNG (broken image icon を出さない)
    transparent_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB"
        "0C8AAAAASUVORK5CYII="
    )
    response = _make_response(transparent_png)
    response.headers["Content-Type"] = "image/png"
    # 短めキャッシュ: 解析後に新サムネが出るまでの待ち時間を最小化
    response.headers["Cache-Control"] = "public, max-age=60"
    return response


@server.route("/api/data_export/<job_id>")
def _data_export_download(job_id):
    """データ出力(UMAP cluster)の生成済みファイルを send_file でストリーム配信する。

    base64 でブラウザに載せるとタブが落ちるため、作業スレッドが保存した一時ファイルを
    ここでストリーム配信する。job は export_progress レジストリで解決する。
    """
    from pathlib import Path as _Path
    from app.services import export_progress as _ep
    job = _ep.get_job(job_id)
    fp = (job or {}).get("filepath")
    name = (job or {}).get("filename") or "export.bin"
    if not fp or not _Path(fp).exists():
        _abort(404)
    return _send_file(fp, as_attachment=True, download_name=name)


# レイアウト設定
app.layout = create_main_layout()

# コールバック登録
# 各コールバックモジュールの import で自動的に @app.callback が登録される
from app.callbacks import file_handlers  # noqa: E402, F401
from app.callbacks import analysis_callbacks  # noqa: E402, F401
from app.callbacks import session_callbacks  # noqa: E402, F401
from app.callbacks import interactive_callbacks  # noqa: E402, F401
from app.callbacks import project_callbacks  # noqa: E402, F401
from app.callbacks import share_callbacks  # noqa: E402, F401
from app.callbacks import preset_callbacks  # noqa: E402, F401
from app.callbacks import interactive_batch_save  # noqa: E402, F401
from app.callbacks import interactive_data_export  # noqa: E402, F401
from app.callbacks import scils_converter_callbacks  # noqa: E402, F401
from app.callbacks import annotation_preview_callbacks  # noqa: E402, F401
from app.callbacks import add_molinfo_callbacks  # noqa: E402, F401
from app.callbacks import hne_overlay_callbacks  # noqa: E402, F401
from app.callbacks import env_settings_callbacks  # noqa: E402, F401
from app.callbacks import lite_view_callbacks  # noqa: E402, F401
from app.callbacks import rds_maintenance_callbacks  # noqa: E402, F401
from app.callbacks import parquet_maintenance_callbacks  # noqa: E402, F401
from app.callbacks import edit_lock_callbacks  # noqa: E402, F401
from app.callbacks import auth_callbacks  # noqa: E402, F401
from app.callbacks import data_management_callbacks  # noqa: E402, F401
from app.callbacks import tab_url_routing  # noqa: E402, F401
from app.callbacks import provenance_callbacks  # noqa: E402, F401
from app.callbacks import preflight_callbacks  # noqa: E402, F401

# ---------------------------------------------------------------------------
# 起動時の後始末
# ---------------------------------------------------------------------------
# [ver51.0] 「実行中の記録が残っているのにプロセスが居ない」解析を締める。
#   コンテナ再起動や再ビルドをすると、Dash アプリはコンテナの PID 1 なので
#   カーネルが R プロセスごと SIGKILL する。これを拾わないと
#   analysis_status.txt が running のまま永久に残り、後から何が起きたのか
#   分からなくなる（実際にその状態のログが報告された）。
def _reconcile_interrupted_analyses() -> None:
    import logging as _logging
    _log = _logging.getLogger("msi.main")
    try:
        from app.config import DESI_DATA_DIR, OUTPUT_DATA_DIR, TIMS_DATA_DIR
        from app.services.analysis_finalizer import reconcile_stale_jobs
        roots = [str(d) for d in (TIMS_DATA_DIR, DESI_DATA_DIR, OUTPUT_DATA_DIR)
                 if d and Path(d).is_dir()]
        closed = reconcile_stale_jobs(roots)
        if closed:
            _log.warning(
                "中断されていた解析 %d 件を error として記録しました: %s",
                len(closed), ", ".join(Path(c).name for c in closed),
            )
    except Exception as e:  # noqa: BLE001
        _log.warning("起動時の解析後始末に失敗（起動は継続）: %s", e)


_reconcile_interrupted_analyses()

if __name__ == "__main__":
    # Docker CMD は run_app.py 経由。ここは bare-metal 開発用のフォールバック。
    # 本番環境でもブラウザに stack trace を漏らさないよう debug=False で固定。
    app.run(debug=False, host=APP_HOST, port=APP_PORT)
