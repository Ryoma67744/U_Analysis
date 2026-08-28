# =============================================================================
# MSI Analysis Application - Logging Configuration
# ログ設定モジュール
# =============================================================================

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.services.access_logger import AnalystContextFilter, setup_access_logger


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
        "%(asctime)s [%(levelname)s] [%(analyst_name)s/%(access_tier)s] "
        "%(name)s: %(message)s",
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
    # 子 logger からのレコードにも analyst_name/access_tier を注入するため
    # handler レベルに Filter を装着する (Logger.addFilter は子 logger には伝播しない)
    fh.addFilter(AnalystContextFilter())

    # コンソールハンドラ
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(formatter)
    ch.addFilter(AnalystContextFilter())

    # ルートロガー "msi" を設定
    root = logging.getLogger("msi")
    root.setLevel(level)
    # 重複ハンドラを防止（リロード対応）
    if not root.handlers:
        root.addHandler(fh)
        root.addHandler(ch)

    # ★ ver62.6: "app" 配下（`logging.getLogger(__name__)` を使うモジュール）にも
    #   同じハンドラを付ける。
    #
    #   このリポジトリには 2 つの流儀が混在している:
    #     - `logging.getLogger("msi.xxx")`  … 大半のモジュール
    #     - `logging.getLogger(__name__)`   … app.callbacks.interactive_data_export
    #       など 16 モジュール。名前は "app.callbacks.…" になる
    #   ハンドラは "msi" にしか付いていなかったので、後者のログは
    #   **msi_app.log に 1 行も残らない**。しかも "app" にも root にもレベル指定が
    #   無いため実効レベルが既定の WARNING になり、INFO は emit すらされない
    #   （`isEnabledFor(INFO)` が False）。
    #
    #   実害: データ出力の不具合を追ったとき
    #   `grep "\[DataExport\]" msi_app.log` が 0 件だったので
    #   「処理がそこまで到達していない」と読んでしまった。実際には**出力側が
    #   壊れていただけ**で、コードは動いていた。計器が壊れていると、無い情報を
    #   証拠として使ってしまう。
    #
    #   個々の logger 名を "msi.*" へ書き換える案は、今後 `__name__` を使う
    #   モジュールが増えたときにまた漏れる。パッケージの根に付ければ漏れない。
    app_root = logging.getLogger("app")
    app_root.setLevel(level)
    if not app_root.handlers:
        app_root.addHandler(fh)
        app_root.addHandler(ch)

    # 監査用 access.log を別ハンドラで初期化
    setup_access_logger(log_dir)
