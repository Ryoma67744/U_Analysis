"""描画コールバックの性能計測 (ver51.5)。

★ 既定 OFF であることが最重要。本番のホットパスに置くので、無効時に
   計測もログ出力も走らないことを固定する (gzip 圧縮は実測が重い)。
"""

import logging

import pytest

from app.utils.perf_trace import perf_enabled, perf_trace


def test_disabled_by_default(monkeypatch, caplog):
    """PERF_TRACE 未設定なら何も測らず、ログも出さない。"""
    monkeypatch.delenv("PERF_TRACE", raising=False)
    assert perf_enabled() is False

    with caplog.at_level(logging.INFO, logger="msi.perf"):
        with perf_trace("dummy", tiles=4) as t:
            assert t.enabled is False
            with t.phase("read"):
                pass
            # ★ 無効時は計測自体を行わない (gzip が走らないこと)
            assert t.measure_payload({"x": list(range(10000))}) is None
            t.note(points=1)
    assert not [r for r in caplog.records if r.name == "msi.perf"]


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_enabled_by_env(monkeypatch, val):
    monkeypatch.setenv("PERF_TRACE", val)
    assert perf_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "", "  "])
def test_disabled_values(monkeypatch, val):
    monkeypatch.setenv("PERF_TRACE", val)
    assert perf_enabled() is False


def test_emits_phases_and_payload_sizes(monkeypatch, caplog):
    """有効時は phase の ms と raw/gzip バイト数を 1 行で出す。"""
    monkeypatch.setenv("PERF_TRACE", "1")

    with caplog.at_level(logging.INFO, logger="msi.perf"):
        with perf_trace("update_feature_plot", tiles=4) as t:
            assert t.enabled is True
            with t.phase("read"):
                pass
            t.note(points=50000)
            got = t.measure_payload({"x": list(range(1000))}, "children")
            assert got is not None and got[0] > got[1] > 0, "gzip が効いていない"

    msgs = [r.getMessage() for r in caplog.records if r.name == "msi.perf"]
    assert len(msgs) == 1, msgs
    line = msgs[0]
    for token in ("cb=update_feature_plot", "tiles=4", "points=50000",
                  "read_ms=", "children_raw=", "children_gzip=", "total_ms="):
        assert token in line, f"{token} が出ていない: {line}"


def test_measurement_failure_does_not_break_the_callback(monkeypatch, caplog):
    """計測が失敗しても本処理を壊さないこと。"""
    monkeypatch.setenv("PERF_TRACE", "1")

    class _Unserializable:
        def __repr__(self):
            raise RuntimeError("boom")

    with caplog.at_level(logging.INFO, logger="msi.perf"):
        with perf_trace("x") as t:
            assert t.measure_payload({"bad": _Unserializable()}) is None
    # 例外が外へ出ず、ログ行自体は出る
    assert [r for r in caplog.records if r.name == "msi.perf"]


def test_exception_inside_still_emits(monkeypatch, caplog):
    """本処理が例外を投げても計測ログは残る (遅いまま失敗した記録が要る)。"""
    monkeypatch.setenv("PERF_TRACE", "1")

    with caplog.at_level(logging.INFO, logger="msi.perf"):
        with pytest.raises(ValueError):
            with perf_trace("y") as t:
                with t.phase("read"):
                    pass
                raise ValueError("boom")
    assert any("cb=y" in r.getMessage()
               for r in caplog.records if r.name == "msi.perf")
