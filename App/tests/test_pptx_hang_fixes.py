"""PPTX エクスポートのハング対策の回帰テスト。

背景: kaleido(0.2.1) + WebGL(scattergl) + GPU 無しコンテナのソフト GL で、静的画像描画が
無言ハングし、タイムアウトが無いため無限待機 → ProcessPool と Chromium がリークした。

本テストは以下を担保する:
  1. 描画前に WebGL(scattergl) が SVG(scatter) へ変換される（ハングの直接対処）。
  2. 呼び出し側の figure dict を破壊しない（deepcopy 経由）。
  3. RenderQueue.result がタイムアウト/失敗時に None を返しスキップする（無限待機しない）。
  4. 後始末系ヘルパーが例外なく冪等に動く。
  5. render_png が最悪でも例外を投げず、後始末できる（スモーク）。
"""

import concurrent.futures

import app.utils.pptx_helpers as ph


# ---------------------------------------------------------------------------
# WebGL(scattergl) -> SVG(scatter) 変換
# ---------------------------------------------------------------------------

def test_scattergl_converted_to_scatter():
    d = {
        "data": [
            {"type": "scattergl", "x": [1, 2], "y": [3, 4]},
            {"type": "bar", "x": [1], "y": [2]},
            {"x": [0], "y": [0]},  # type 省略 → 触らない
        ],
        "layout": {},
    }
    ph._sanitize_fig_dict_for_export(d)
    assert d["data"][0]["type"] == "scatter"   # 変換された
    assert d["data"][1]["type"] == "bar"        # 他タイプは不変
    assert "type" not in d["data"][2]


def test_sanitize_handles_malformed_input():
    # 例外を投げず、そのまま返すこと
    assert ph._sanitize_fig_dict_for_export(None) is None
    assert ph._sanitize_fig_dict_for_export({"data": "not-a-list"}) == {"data": "not-a-list"}
    assert ph._sanitize_fig_dict_for_export({}) == {}


def test_fig_to_png_bytes_does_not_mutate_caller_dict():
    """deepcopy されるため、呼び出し側の scattergl が書き換わらないこと。"""
    d = {"data": [{"type": "scattergl", "x": [1, 2], "y": [3, 4]}], "layout": {}}
    _ = ph.fig_to_png_bytes(d, width=100, height=100, scale=1)  # kaleido 無ければ None
    assert d["data"][0]["type"] == "scattergl"  # 元 dict は不変


# ---------------------------------------------------------------------------
# ダウンサンプル
# ---------------------------------------------------------------------------

def test_downsample_disabled_by_default():
    tr = {"type": "scatter", "x": list(range(100)), "y": list(range(100))}
    ph._maybe_downsample_scatter(tr)
    assert len(tr["x"]) == 100  # 既定 (_MAX_SCATTER_POINTS<=0) では間引かない


def test_downsample_when_enabled_keeps_arrays_aligned(monkeypatch):
    monkeypatch.setattr(ph, "_MAX_SCATTER_POINTS", 10)
    tr = {
        "type": "scatter",
        "x": list(range(100)),
        "y": list(range(100)),
        "text": list(range(100)),
        "customdata": list(range(100)),
        "marker": {"color": list(range(100)), "size": list(range(100))},
    }
    ph._maybe_downsample_scatter(tr)
    n = len(tr["x"])
    assert n < 100
    assert n == len(tr["y"]) == len(tr["text"]) == len(tr["customdata"])
    assert n == len(tr["marker"]["color"]) == len(tr["marker"]["size"])


# ---------------------------------------------------------------------------
# RenderQueue.result — タイムアウト/失敗時に None（無限待機しない）
# ---------------------------------------------------------------------------

class _FakeFutureTimeout:
    def result(self, timeout=None):
        raise concurrent.futures.TimeoutError()

    def cancel(self):
        return False


class _FakeFutureError:
    def result(self, timeout=None):
        raise RuntimeError("boom")

    def cancel(self):
        return False


class _FakeFutureOK:
    def result(self, timeout=None):
        return b"PNG"

    def cancel(self):
        return True


def test_renderqueue_result_timeout_returns_none():
    rq = ph.RenderQueue(max_workers=1)  # __enter__ せずとも result は使える
    assert rq.result(_FakeFutureTimeout(), timeout=0.01) is None


def test_renderqueue_result_error_returns_none():
    rq = ph.RenderQueue(max_workers=1)
    assert rq.result(_FakeFutureError(), timeout=0.01) is None


def test_renderqueue_result_success_passthrough():
    rq = ph.RenderQueue(max_workers=1)
    assert rq.result(_FakeFutureOK(), timeout=5) == b"PNG"


def test_renderqueue_result_none_future():
    rq = ph.RenderQueue(max_workers=1)
    assert rq.result(None) is None


# ---------------------------------------------------------------------------
# 後始末ヘルパー
# ---------------------------------------------------------------------------

def test_shutdown_helpers_are_safe_and_idempotent():
    ph._shutdown_pool_hard(None)      # None でも落ちない
    ph.shutdown_shared_queue()        # 未生成でも落ちない
    ph.shutdown_shared_queue()        # 冪等


def test_render_png_never_raises_and_cleans_up():
    """実プール経由のスモーク: 例外を投げず None か bytes を返し、後始末できる。"""
    fig = {"data": [{"type": "scatter", "x": [1, 2], "y": [3, 4]}], "layout": {}}
    try:
        out = ph.render_png(fig, width=100, height=100, scale=1, timeout=30)
        assert out is None or isinstance(out, (bytes, bytearray))
    finally:
        ph.shutdown_shared_queue()


def test_kill_lingering_kaleido_targets_only_kaleido(monkeypatch):
    """残存 kaleido のみを kill するバックストップ（名前 or cmdline に kaleido）。"""
    import psutil

    killed = []

    class _FakeProc:
        def __init__(self, name, cmdline):
            self.info = {"name": name, "cmdline": cmdline}
            self._name = name

        def kill(self):
            killed.append(self._name)

    procs = [
        _FakeProc("kaleido", ["/x/kaleido", "plotly"]),      # name 一致
        _FakeProc("bash", ["/bin/bash", "/x/bin/kaleido"]),   # cmdline 一致
        _FakeProc("python3", ["python3", "run_app.py"]),      # 非対象
        _FakeProc("chrome", ["chrome", "--headless"]),        # 非対象
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: procs)
    ph._kill_lingering_kaleido()
    assert set(killed) == {"kaleido", "bash"}
