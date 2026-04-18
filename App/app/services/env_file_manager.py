# =============================================================================
# MSI Analysis Application - .env File Manager
# アプリ UI から App/.env を読み書きする薄いラッパー。
# 既存のコメントや他キーを保全しつつ、指定キーだけを更新する。
# =============================================================================

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values, set_key

logger = logging.getLogger("msi.env_file_manager")

# App/config.py と同じ起点で `App/.env` を解決
APP_DIR = Path(__file__).parent.parent.parent  # App/
ENV_PATH = APP_DIR / ".env"
ENV_EXAMPLE_PATH = APP_DIR / ".env.example"

# UI で編集可能なキー一覧 (定義順 = モーダル表示順)
EDITABLE_KEYS: tuple[str, ...] = (
    "TIMS_DATA_DIR",
    "DESI_DATA_DIR",
    "R_HOME",
    "SHARE_BASE_URL",
    "APP_PORT",
    "APP_HOST",
)


def env_file_path() -> Path:
    """App/.env の絶対パスを返す"""
    return ENV_PATH


def ensure_env_file_exists() -> Path:
    """`.env` が無ければ `.env.example` をコピーして作る。両方無ければ空ファイル作成。"""
    if ENV_PATH.exists():
        return ENV_PATH
    if ENV_EXAMPLE_PATH.exists():
        shutil.copy2(ENV_EXAMPLE_PATH, ENV_PATH)
        logger.info(".env を .env.example から新規作成: %s", ENV_PATH)
    else:
        ENV_PATH.touch()
        logger.info(".env を空ファイルで新規作成: %s", ENV_PATH)
    return ENV_PATH


def read_env_values() -> dict[str, str]:
    """現在の `.env` から EDITABLE_KEYS の値を読み取る (キー欠落時は空文字)"""
    if not ENV_PATH.exists():
        return {k: "" for k in EDITABLE_KEYS}
    values = dotenv_values(ENV_PATH)
    return {k: (values.get(k) or "") for k in EDITABLE_KEYS}


def write_env_values(updates: dict[str, Optional[str]]) -> Path:
    """`.env` に指定キーを書き込む。既存のコメント・他キーは保持。

    - 値が空文字 / None のキーはスキップ (既存値を消したい場合は `unset` 推奨だが UI 上は空欄維持で足りる)
    - ファイルが無ければ .env.example から複製 or 空ファイルで作成
    """
    ensure_env_file_exists()
    for key, raw in updates.items():
        if key not in EDITABLE_KEYS:
            logger.warning("編集非対象キーを無視: %s", key)
            continue
        value = (raw or "").strip()
        if not value:
            # 空欄は「未変更 / 既定に戻したい」と解釈し書き込まない
            continue
        set_key(
            str(ENV_PATH), key, value,
            quote_mode="never",  # パスのバックスラッシュ等を素直に書く
            export=False,
        )
    logger.info(".env を更新: %s (keys=%s)", ENV_PATH, list(updates.keys()))
    return ENV_PATH


def get_effective_values() -> dict[str, str]:
    """実際にアプリが使っている値 (起動時点で解決された値) を返す。
    `.env` の値と違う場合は「再起動が必要」を示せる。
    """
    # 遅延 import: 循環参照回避
    from app import config as cfg
    return {
        "TIMS_DATA_DIR": str(cfg.TIMS_DATA_DIR),
        "DESI_DATA_DIR": str(cfg.DESI_DATA_DIR),
        "R_HOME": str(cfg.R_HOME),
        "SHARE_BASE_URL": cfg.SHARE_BASE_URL or "",
        "APP_PORT": str(cfg.APP_PORT),
        "APP_HOST": cfg.APP_HOST,
    }
