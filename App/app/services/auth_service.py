"""認証パスワード管理サービス。

- Password A: プロジェクト一覧フル機能用 (bcrypt hash)
- Password B: 共有 URL 閲覧用 (bcrypt hash)
- Master Password: A/B 変更権限用 (env 直接、bcrypt 不要)

保存場所: Data/Other/common/auth.json (atomic write)
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import bcrypt
from filelock import FileLock

logger = logging.getLogger("msi.auth")


def _default_auth_path() -> Path:
    env = os.environ.get("AUTH_CONFIG_PATH")
    if env:
        return Path(env)
    # ★ ver56.4: 既定パスを `/app/Data/Other/common/auth.json` の決め打ちから
    #   `config.DATA_DIR` 由来へ変更する。決め打ちは Docker の中でしか成立せず、
    #   Docker を使わない起動 (setup.bat / run_app.bat の手順) では
    #   root なら FS 直下に `/app` を作って認証情報を書き、
    #   一般ユーザーなら PermissionError で **起動できなかった**。
    #   E2E も同じ経路のため「失敗」ではなく無言 skip になり、
    #   ブラウザテストが動いていないことに気づけなかった。
    #
    #   Docker では WORKDIR=/app のため DATA_DIR は `/app/Data` に解決され、
    #   このパスは **従来と完全に同一** になる。既存の named volume
    #   (`msi-common:/app/Data/Other/common`) と保存済みパスワードはそのまま。
    #   ディレクトリ名を `Common` (大文字) に変えると既存環境の auth.json を
    #   見失うため、小文字 `common` を維持すること。
    from app.config import DATA_DIR
    return DATA_DIR / "Other" / "common" / "auth.json"


AUTH_CONFIG_PATH: Path = _default_auth_path()
_LOCK_PATH = AUTH_CONFIG_PATH.with_suffix(".lock")
_BCRYPT_ROUNDS = 12


def _ensure_parent() -> None:
    AUTH_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    if not AUTH_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(AUTH_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("auth.json read failed: %s", e)
        return {}


def _save(data: dict) -> None:
    _ensure_parent()
    tmp = AUTH_CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(AUTH_CONFIG_PATH)


def _hash(plain: str) -> str:
    return bcrypt.hashpw(
        plain.encode("utf-8"),
        bcrypt.gensalt(rounds=_BCRYPT_ROUNDS),
    ).decode("ascii")


def _verify(plain: str, hashed: Optional[str]) -> bool:
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def verify_password_a(plain: str) -> bool:
    """Password A の検証 (プロジェクト一覧フル機能)"""
    return _verify(plain, _load().get("password_a_hash"))


def verify_password_b(plain: str) -> bool:
    """Password B の検証 (共有 URL 閲覧)"""
    return _verify(plain, _load().get("password_b_hash"))


def verify_master(plain: str) -> bool:
    """Master Password の検証。

    auth.json に master_password_hash があればそれを優先 (UI で変更済)。
    無ければ .env の MASTER_PASSWORD と timing-safe 比較 (初回起動時)。
    """
    if not plain:
        return False
    data = _load()
    h = data.get("master_password_hash")
    if h:
        return _verify(plain, h)
    expected = os.environ.get("MASTER_PASSWORD", "")
    if not expected:
        return False
    return hmac.compare_digest(plain.encode("utf-8"), expected.encode("utf-8"))


def update_master(new_plain: str, updated_by: str) -> int:
    """Master Password を更新。返り値は新しい password_version。

    auth.json の master_password_hash を bcrypt で更新。
    以後 verify_master はこのハッシュを参照し、.env の MASTER_PASSWORD は
    無視される (初回起動時のフォールバックのみ)。
    """
    if not new_plain or len(new_plain) < 4:
        raise ValueError("master password must be at least 4 characters")

    with FileLock(str(_LOCK_PATH), timeout=10):
        data = _load()
        data["master_password_hash"] = _hash(new_plain)
        data["password_version"] = int(data.get("password_version", 0)) + 1
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        data["updated_by"] = updated_by
        _save(data)
        new_version = int(data["password_version"])

    logger.warning(
        "Master Password updated by %s (version=%d)",
        updated_by, new_version,
    )
    return new_version


def get_password_version() -> int:
    """現在の password_version を返す。未設定なら 0。

    パスワード変更後にこの値が増分される。Flask session の pw_version と
    照合することで、変更時に既存セッションを失効させる。
    """
    return int(_load().get("password_version", 0))


def update_password(which: str, new_plain: str, updated_by: str) -> int:
    """A/B パスワードを更新。返り値は新しい password_version。

    Args:
        which: "a" or "b"
        new_plain: 平文の新パスワード
        updated_by: 解析者名 (監査ログ用)

    Returns:
        新しい password_version
    """
    if which not in ("a", "b"):
        raise ValueError(f"which must be 'a' or 'b', got {which!r}")
    if not new_plain or len(new_plain) < 4:
        raise ValueError("password must be at least 4 characters")

    with FileLock(str(_LOCK_PATH), timeout=10):
        data = _load()
        data[f"password_{which}_hash"] = _hash(new_plain)
        data["password_version"] = int(data.get("password_version", 0)) + 1
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        data["updated_by"] = updated_by
        _save(data)
        new_version = int(data["password_version"])

    logger.warning(
        "Password %s updated by %s (version=%d)",
        which.upper(), updated_by, new_version,
    )
    return new_version


def is_initialized() -> bool:
    """共有用 (B) ハッシュが保存済みか。

    ver4.0: Master でログインに統合したため A は必須でなくなった。
    共有用 (B) のみ初期化チェック対象。
    """
    data = _load()
    return bool(data.get("password_b_hash"))


def init_from_env() -> None:
    """起動時呼び出し。auth.json が無ければ INITIAL_PASSWORD_B から初期化する。

    ver4.0: Password A は廃止 (Master でログイン)。INITIAL_PASSWORD_A は
    後方互換で受け付けるが必須ではない。共有用 (B) のみ必須。
    既に初期化済みなら何もしない。
    """
    if is_initialized():
        logger.info("Auth config already initialized: %s", AUTH_CONFIG_PATH)
        return

    initial_a = os.environ.get("INITIAL_PASSWORD_A", "").strip()
    initial_b = os.environ.get("INITIAL_PASSWORD_B", "").strip()

    if not initial_b:
        raise RuntimeError(
            "Auth config not initialized and INITIAL_PASSWORD_B not set. "
            "Set INITIAL_PASSWORD_B (共有用パスワード) in .env and restart. "
            "ログインは MASTER_PASSWORD を使用します。"
        )

    # ★ ver56.4: ロックを取る前に親ディレクトリを用意する。
    #   従来は `_ensure_parent()` が `_save()` の中でしか呼ばれず、
    #   その手前の FileLock が先に走っていた (順序の誤り)。
    #   現行の filelock は親を作るため表面化していないが、
    #   ライブラリの実装詳細に依存した危うい状態だった。
    _ensure_parent()

    with FileLock(str(_LOCK_PATH), timeout=10):
        data = _load()
        # A は後方互換: env にあれば保存するが、無くても OK (Master ログインに統合)
        if initial_a and not data.get("password_a_hash"):
            data["password_a_hash"] = _hash(initial_a)
        if not data.get("password_b_hash"):
            data["password_b_hash"] = _hash(initial_b)
        data["password_version"] = int(data.get("password_version", 0)) + 1
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        data["updated_by"] = "system_init"
        _save(data)

    logger.warning(
        "Auth initialized from env (B=共有用). ログインは MASTER_PASSWORD。"
        " Change via UI ASAP! (config=%s)",
        AUTH_CONFIG_PATH,
    )
