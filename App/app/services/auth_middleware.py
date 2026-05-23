"""認証ミドルウェア (Flask before_request hook) と認証ルート。

- Tier A: プロジェクト一覧フル機能 (Password A)
- Tier B: 共有 URL `/share/<token>` のみ閲覧可 (Password B)

Path 判定:
- Bypass (認証不要): /healthz*, /metrics, /login*, /logout, /assets/*, /_dash-*, /_favicon.ico
- /share/<token> および /share/<token>/* : Tier A or B 必要
- それ以外: Tier A 必要

パスワード変更後の既存セッション失効: session["pw_version"] が
auth_service の現在 password_version と一致しなければ強制再ログイン。
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote, urlparse

from flask import (
    Flask, abort, jsonify, redirect, render_template,
    request, session, url_for,
)

from app.services import auth_service

logger = logging.getLogger("msi.auth")

_BYPASS_PREFIXES = (
    "/assets/",
    "/_dash-component-suites/",
    "/_favicon.ico",
    "/help/",  # ヘルプページ (取扱説明書) は認証なしで閲覧可
    "/view/",  # 無期限共有 URL: token を知る人全員が閲覧可 (認証不要)
)

_BYPASS_EXACT = {
    "/healthz",
    "/healthz/ready",
    "/metrics",
    "/login",
    "/logout",
    "/_dash-layout",
    "/_dash-dependencies",
    "/_dash-update-component",
    "/_reload-hash",
}

_SHARE_PATH_RE = re.compile(r"^/share/([^/]+)/?")
_ANALYST_NAME_MAX = 50


def _sanitize_name(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    name = raw.strip()
    if not name or len(name) > _ANALYST_NAME_MAX:
        return None
    return name


def _is_bypass(path: str) -> bool:
    if path in _BYPASS_EXACT:
        return True
    return any(path.startswith(p) for p in _BYPASS_PREFIXES)


def _is_share_path(path: str) -> bool:
    return bool(_SHARE_PATH_RE.match(path))


def _session_valid_for_tier(required_tier: str) -> bool:
    """session の access_tier が required_tier 以上か。

    Tier A は B 用パス (共有 URL) にもアクセス可能。
    pw_version が不一致なら無効。
    """
    tier = session.get("access_tier")
    if not tier:
        return False
    sess_ver = session.get("pw_version")
    current_ver = auth_service.get_password_version()
    if sess_ver != current_ver:
        logger.info(
            "Session invalidated by pw_version mismatch (sess=%s, current=%d)",
            sess_ver, current_ver,
        )
        return False
    if required_tier == "B":
        return tier in ("A", "B")
    return tier == "A"


def _safe_next(next_url: Optional[str]) -> str:
    """ログイン後のリダイレクト先を検証 (オープンリダイレクト対策)。"""
    if not next_url:
        return "/"
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return "/"
    if not next_url.startswith("/"):
        return "/"
    return next_url


def _require_login():
    """before_request hook: Tier 判定して未認証なら /login にリダイレクト。"""
    path = request.path

    if _is_bypass(path):
        return None

    if _is_share_path(path):
        if _session_valid_for_tier("B"):
            return None
        return redirect(url_for("auth.login", next=path))

    if _session_valid_for_tier("A"):
        return None
    return redirect(url_for("auth.login", next=path))


def _render_login(error: Optional[str] = None, next_url: str = "/"):
    return render_template(
        "login.html",
        error=error,
        next_url=next_url,
    )


def _login_view():
    if request.method == "GET":
        next_url = _safe_next(request.args.get("next"))
        if _session_valid_for_tier("A"):
            return redirect(next_url if next_url != "/login" else "/")
        return _render_login(next_url=next_url)

    # POST
    next_url = _safe_next(
        request.form.get("next") or request.args.get("next")
    )
    name = _sanitize_name(request.form.get("analyst_name"))
    pw = request.form.get("password", "")

    if not name:
        return _render_login(
            error="解析者名を入力してください (1-50 文字)",
            next_url=next_url,
        ), 400

    if auth_service.verify_password_a(pw):
        session.clear()
        session.permanent = True
        session["analyst_name"] = name
        session["access_tier"] = "A"
        session["pw_version"] = auth_service.get_password_version()
        logger.info("Login success: analyst=%s tier=A", name)
        return redirect(next_url)

    if auth_service.verify_password_b(pw):
        session.clear()
        session.permanent = True
        session["analyst_name"] = name
        session["access_tier"] = "B"
        session["pw_version"] = auth_service.get_password_version()
        logger.info("Login success: analyst=%s tier=B", name)
        if _is_share_path(next_url):
            return redirect(next_url)
        return redirect("/")

    logger.warning("Login failed: analyst=%s", name)
    return _render_login(
        error="認証に失敗しました",
        next_url=next_url,
    ), 401


def _logout_view():
    name = session.get("analyst_name")
    if name:
        logger.info("Logout: analyst=%s", name)
    session.clear()
    return redirect(url_for("auth.login"))


def _change_password_view():
    """JSON API: パスワード A/B 変更。Tier A + 正しい Master Pass が必須。"""
    if not _session_valid_for_tier("A"):
        return jsonify({"ok": False, "error": "Tier A 認証が必要です"}), 403

    payload = request.get_json(silent=True) or {}
    master = payload.get("master_password", "")
    if not auth_service.verify_master(master):
        logger.warning(
            "Password change rejected (bad master): analyst=%s",
            session.get("analyst_name"),
        )
        return jsonify(
            {"ok": False, "error": "Master Password が違います"}
        ), 403

    new_a = (payload.get("new_password_a") or "").strip()
    new_b = (payload.get("new_password_b") or "").strip()
    new_master = (payload.get("new_master_password") or "").strip()
    if not new_a and not new_b and not new_master:
        return jsonify(
            {"ok": False,
             "error": "変更対象 (Master / A / B のいずれか) を指定してください"}
        ), 400

    updated = []
    analyst = session.get("analyst_name", "unknown")
    try:
        if new_a:
            if len(new_a) < 4:
                return jsonify(
                    {"ok": False, "error": "Password A は 4 文字以上"}
                ), 400
            auth_service.update_password("a", new_a, analyst)
            updated.append("A")
        if new_b:
            if len(new_b) < 4:
                return jsonify(
                    {"ok": False, "error": "Password B は 4 文字以上"}
                ), 400
            auth_service.update_password("b", new_b, analyst)
            updated.append("B")
        if new_master:
            if len(new_master) < 8:
                return jsonify(
                    {"ok": False,
                     "error": "Master Password は 8 文字以上にしてください"}
                ), 400
            auth_service.update_master(new_master, analyst)
            updated.append("Master")
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    # 変更を行った本人のセッションは新 pw_version に揃える (他人のは失効)
    session["pw_version"] = auth_service.get_password_version()
    return jsonify({"ok": True, "updated": updated})


def register(server: Flask) -> None:
    """Flask server に認証 hook と routes を登録する。

    呼び出し順序の前提:
    - 既存の `_healthz_bypass`, `_ensure_session_id` の **後** に呼ぶ
      (before_request はチェーン順に評価される)
    """
    server.before_request(_require_login)
    server.add_url_rule(
        "/login", endpoint="auth.login",
        view_func=_login_view, methods=["GET", "POST"],
    )
    server.add_url_rule(
        "/logout", endpoint="auth.logout",
        view_func=_logout_view, methods=["GET"],
    )
    server.add_url_rule(
        "/api/admin/change-password",
        endpoint="auth.change_password",
        view_func=_change_password_view,
        methods=["POST"],
    )
