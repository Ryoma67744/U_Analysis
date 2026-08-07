# =============================================================================
# 描画コールバックの性能計測 (ver51.5)
# =============================================================================
# `/metrics` (main.py:122-158) はプロセス単位、seurat_bridge は RDS 抽出の段階
# ログを持っているが、**描画コールバック単位の内訳を取る手段が無かった**。
# そのため「m/z 切り替えが重い」に対して、転送量・サーバ処理時間・ブラウザ描画の
# どれが支配的なのかを実データで分離できず、改善効果も合成データからの推測でしか
# 語れていなかった。
#
# 使い方:
#     with perf_trace("update_feature_plot", tiles=4, points=203078) as t:
#         with t.phase("read"):
#             expr = bridge.get_feature_expression_fast(...)
#         with t.phase("build"):
#             figs = [...]
#         t.measure_payload(children)     # raw / gzip バイト数
#
# ★ 既定 OFF。`PERF_TRACE=1` のときだけ計測もログ出力も行う。
#   無効時は phase() も measure_payload() も即 return するので、
#   本番のホットパスに実質ゼロコストで置いておける。
#
# ★ 記録するのは **件数とバイト数と時間だけ**。CellID・検体名・ファイルパスなど
#   個人や検体を特定しうる値は記録しない (m/z は測定条件なので可)。
# =============================================================================

from __future__ import annotations

import contextlib
import gzip
import json
import logging
import os
import time

logger = logging.getLogger("msi.perf")

_TRUTHY = ("1", "true", "yes", "on")


def perf_enabled() -> bool:
    """PERF_TRACE が有効か。毎回 env を読むのでテストから切り替えられる。"""
    return os.environ.get("PERF_TRACE", "").strip().lower() in _TRUTHY


class _NullTrace:
    """無効時の器。呼び出し側に if を書かせないためのもの。"""

    enabled = False

    @contextlib.contextmanager
    def phase(self, _label):
        yield

    def measure_payload(self, _obj, label="payload"):
        return None

    def note(self, **_kw):
        pass


class _Trace:
    def __init__(self, name: str, **meta):
        self.enabled = True
        self._name = name
        self._meta = {k: v for k, v in meta.items() if v is not None}
        self._phases: "list[tuple[str, float]]" = []
        self._bytes: dict = {}
        self._t0 = time.monotonic()

    @contextlib.contextmanager
    def phase(self, label: str):
        t = time.monotonic()
        try:
            yield
        finally:
            self._phases.append((label, (time.monotonic() - t) * 1000.0))

    def measure_payload(self, obj, label: str = "payload"):
        """Dash が返す値の raw / gzip バイト数を測る。

        Dash と同じ経路 (plotly の JSON エンコーダ) を通す。gzip は Flask-Compress
        の既定と同じ level=6。**計測自体が重い**ので、有効時にしか呼ばれない。
        """
        try:
            import plotly.utils
            raw = json.dumps(obj, cls=plotly.utils.PlotlyJSONEncoder)
            enc = raw.encode("utf-8")
            self._bytes[label] = (len(enc), len(gzip.compress(enc, 6)))
            return self._bytes[label]
        except Exception as e:  # noqa: BLE001 - 計測が本処理を壊さない
            logger.debug("perf: payload 計測に失敗: %s", e)
            return None

    def note(self, **kw):
        """後から分かった件数などを足す (可視点数など)。"""
        self._meta.update({k: v for k, v in kw.items() if v is not None})

    def emit(self):
        total_ms = (time.monotonic() - self._t0) * 1000.0
        parts = [f"cb={self._name}", f"total_ms={total_ms:.1f}"]
        parts += [f"{k}={v}" for k, v in self._meta.items()]
        parts += [f"{lbl}_ms={ms:.1f}" for lbl, ms in self._phases]
        for lbl, (raw, gz) in self._bytes.items():
            parts.append(f"{lbl}_raw={raw}")
            parts.append(f"{lbl}_gzip={gz}")
        logger.info("[perf] %s", " ".join(parts))


@contextlib.contextmanager
def perf_trace(name: str, **meta):
    """描画コールバックを計測する。無効時は何もしない軽量な器を返す。"""
    if not perf_enabled():
        yield _NullTrace()
        return
    t = _Trace(name, **meta)
    try:
        yield t
    finally:
        try:
            t.emit()
        except Exception as e:  # noqa: BLE001
            logger.debug("perf: ログ出力に失敗: %s", e)
