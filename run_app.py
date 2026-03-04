#!/usr/bin/env python
"""MSI Analysis Application - Python Launcher"""

import sys
from pathlib import Path

# アプリケーションルートをパスに追加
app_root = Path(__file__).parent
sys.path.insert(0, str(app_root))

from app.main import app
from app.config import APP_HOST, APP_PORT

if __name__ == "__main__":
    # 起動時バックアップ
    try:
        from app.services.backup_manager import startup_backup
        backed = startup_backup()
        if backed:
            print(f"Startup backup created: {', '.join(backed)}")
    except Exception as e:
        print(f"Backup warning: {e}")

    print(f"Starting MSI Analysis Application on http://{APP_HOST}:{APP_PORT}")
    app.run(debug=False, host=APP_HOST, port=APP_PORT)
