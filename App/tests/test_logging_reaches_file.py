"""アプリのログが本当に msi_app.log に届くことの回帰テスト（ver62.6）。

## 背景（実際に起きたこと）

データ出力の不具合を追うとき、本番で

    docker exec msi-analysis-app grep "\\[DataExport\\]" /app/Data/Other/logs/msi_app.log

が **0 件**だった。`interactive_data_export.py` はモジュール読み込み時にも
実行中にも `[DataExport]` を INFO で出しているので、これを
「処理がそこまで到達していない」と読んだ。**それが誤りだった。**

`setup_logging` はハンドラを logger `"msi"` にしか付けていない。ところが
`interactive_data_export` などは `logging.getLogger(__name__)` を使うので
logger 名が `app.callbacks.interactive_data_export` になり、"msi" を通らない。
さらに `"app"` にも root にもレベル指定が無いため実効レベルが既定の WARNING に
なり、INFO は emit すらされていなかった。

計器が壊れていると「出ていない」を証拠として使ってしまう。ここで守るのは
「**アプリ内のどのモジュールのログも msi_app.log に届く**」という性質。
"""
import logging

import pytest

from app.services.log_config import setup_logging

# 実際に `logging.getLogger(__name__)` を使っているモジュールの logger 名。
# 1 つでも届かなければ、その領域の調査は再び目隠しになる。
DUNDER_NAME_LOGGERS = [
    "app.callbacks.interactive_data_export",
    "app.callbacks.export_options_callbacks",
    "app.services.export_mzlist",
    "app.utils.raster",
]


@pytest.fixture
def isolated_logging(tmp_path):
    """"msi" / "app" のハンドラを退避して差し替え、テスト後に必ず戻す。"""
    names = ("msi", "app")
    saved = {n: (list(logging.getLogger(n).handlers),
                 logging.getLogger(n).level) for n in names}
    for n in names:
        logging.getLogger(n).handlers = []
    try:
        setup_logging(log_dir=tmp_path)
        yield tmp_path / "msi_app.log"
    finally:
        for n in names:
            lg = logging.getLogger(n)
            for h in lg.handlers:
                try:
                    h.close()
                except Exception:  # noqa: BLE001
                    pass
            lg.handlers, lg.level = saved[n]


def _flush():
    for n in ("msi", "app"):
        for h in logging.getLogger(n).handlers:
            h.flush()


@pytest.mark.parametrize("logger_name", DUNDER_NAME_LOGGERS)
def test_dunder_name_の_INFO_がログファイルに届く(isolated_logging, logger_name):
    marker = f"__probe__{logger_name}__"
    logging.getLogger(logger_name).info(marker)
    _flush()

    body = isolated_logging.read_text(encoding="utf-8")
    assert marker in body, (
        f"{logger_name} の INFO が msi_app.log に届いていない。"
        "この状態でログを根拠に切り分けると誤った結論を出す")


def test_dunder_name_の_logger_が_INFO_を_emit_できる(isolated_logging):
    """実効レベルの検査。ハンドラだけ足してもレベルが WARNING なら INFO は消える。"""
    for name in DUNDER_NAME_LOGGERS:
        lg = logging.getLogger(name)
        assert lg.isEnabledFor(logging.INFO), (
            f"{name} の実効レベルが INFO を落としている "
            f"(getEffectiveLevel={lg.getEffectiveLevel()})")


def test_従来からの_msi_配下も引き続き届く(isolated_logging):
    """"app" を足したことで既存の "msi.*" が壊れていないこと。"""
    logging.getLogger("msi.interactive").info("__probe_msi__")
    _flush()
    assert "__probe_msi__" in isolated_logging.read_text(encoding="utf-8")


def test_ハンドラが二重に付かない(isolated_logging):
    """`setup_logging` の再呼び出しでログが二重に出ないこと（リロード対応）。"""
    before = {n: len(logging.getLogger(n).handlers) for n in ("msi", "app")}
    setup_logging(log_dir=isolated_logging.parent)
    after = {n: len(logging.getLogger(n).handlers) for n in ("msi", "app")}
    assert before == after
