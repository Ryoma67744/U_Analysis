import pytest

from app.services.progress_reporting import ProgressReporter


def test_numeric_events_are_coalesced_but_latest_state_is_retained():
    now, writes = [0.0], []
    reporter = ProgressReporter(writes.append, clock=lambda: now[0])
    for done in range(1000):
        reporter.update({"stage": "convert", "sample": "a", "done": done, "total": 2000})
    assert len(writes) == 1
    assert reporter.latest["done"] == 999
    now[0] = 1.0
    assert reporter.update({"stage": "convert", "sample": "a", "done": 1000, "total": 2000})
    assert len(writes) == 2
    assert writes[-1]["done"] == 1000


@pytest.mark.parametrize("last", [
    {"stage": "convert", "sample": "a", "done": 10, "total": 10},
    {"stage": "validate", "sample": "a"},
    {"stage": "convert", "sample": "b", "done": 0, "total": 10},
    {"stage": "error", "error": "invalid input"},
    {"state": "stopped"},
    {"state": "done", "result": {"path": "complete"}},
])
def test_boundaries_and_terminal_events_are_immediate(last):
    writes = []
    reporter = ProgressReporter(writes.append, clock=lambda: 0)
    reporter.update({"stage": "convert", "sample": "a", "done": 1, "total": 10})
    reporter.update(last)
    assert writes[-1] == last
    assert len(writes) == 2


def test_flush_persists_the_latest_suppressed_value():
    writes = []
    reporter = ProgressReporter(writes.append, clock=lambda: 0)
    for i in range(100):
        reporter.update({"done": i, "total": 1000, "state": "running"})
    assert len(writes) == 1
    assert reporter.flush()
    assert writes[-1]["done"] == 99


def test_no_shared_mutation_and_invalid_intervals():
    values = []
    reporter = ProgressReporter(values.append, clock=lambda: 0)
    payload = {"stage": "hash", "metadata": {"path": "a"}}
    reporter.update(payload)
    payload["metadata"]["path"] = "changed"
    assert values[0]["metadata"]["path"] == "a"
    for value in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ProgressReporter(values.append, interval=value)
