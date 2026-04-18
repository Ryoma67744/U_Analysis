#!/usr/bin/env python
"""MSI Analysis Application - Python Launcher"""

import logging
import sys
from pathlib import Path

# アプリケーションルートをパスに追加
app_root = Path(__file__).parent
sys.path.insert(0, str(app_root))

from app.services.log_config import setup_logging

setup_logging()
logger = logging.getLogger("msi.startup")

from app.main import app
from app.config import APP_HOST, APP_PORT


def main():
    """アプリケーションのエントリーポイント。"""
    # 起動時バックアップ
    try:
        from app.services.backup_manager import startup_backup
        backed = startup_backup()
        if backed:
            logger.info("Startup backup created: %s", ", ".join(backed))
    except Exception as e:
        logger.warning("Backup warning: %s", e)

    logger.info(
        "Starting MSI Analysis Application on http://%s:%s", APP_HOST, APP_PORT
    )
    app.run(debug=False, host=APP_HOST, port=APP_PORT)


if __name__ == "__main__":
    main()
