# =============================================================================
# MSI Analysis Application - Logging Configuration
# ログ設定モジュール
# =============================================================================

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: Path = None, level=logging.INFO):
    """アプリ全体のログ設定を初期化する。

    Parameters
    ----------
    log_dir : Path, optional
        ログファイル出力ディレクトリ。None の場合は Data/Other/logs/。
    level : int
        ログレベル（デフォルト: INFO）。
    """
    if log_dir is None:
        # App/app/services/log_config.py → 4 階層上 = UMAP/
        root = Path(__file__).parent.parent.parent.parent
        log_dir = root / "Data" / "Other" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ファイルハンドラ（10MB × 5世代ローテーション）
    fh = RotatingFileHandler(
        log_dir / "msi_app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(formatter)

    # コンソールハンドラ
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(formatter)

    # ルートロガー "msi" を設定
    root = logging.getLogger("msi")
    root.setLevel(level)
    # 重複ハンドラを防止（リロード対応）
    if not root.handlers:
        root.addHandler(fh)
        root.addHandler(ch)
