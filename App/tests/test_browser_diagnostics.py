"""204 後の Chromium 中断と、本当の callback 通信障害を取り違えない。"""

import json
from types import SimpleNamespace

import pytest

from tests.e2e.conftest import _BrowserDiagnostics


BASE = "http://127.0.0.1:12345"


class _Page:
    def on(self, *_):
        pass


class _Request:
    def __init__(self, *, failure="net::ERR_ABORTED", method="POST", path="/_dash-update-component"):
        self.url = BASE + path
        self.method = method
        self.failure = failure
        self.post_data_json = {"output": "sample_selector.children", "inputs": [{"value": "private"}]}


def _response(request, status):
    return SimpleNamespace(url=request.url, request=request, status=status,
                           headers={"x-request-id": "request-123"})


def test_only_the_same_dash_request_completed_with_204_can_be_classified_as_no_content(tmp_path):
    diagnostics = _BrowserDiagnostics(_Page(), BASE)
    request = _Request()
    diagnostics._response(_response(request, 204))
    diagnostics._request_failed(request)
    diagnostics.assert_clean()
    assert diagnostics.failed_requests == []
    [entry] = diagnostics.completed_204_cancellations
    assert entry["response_status"] == 204
    assert entry["output"] == "sample_selector.children"
    assert entry["request_id"] == "request-123"
    assert entry["response_time"] <= entry["time"]
    # 分類しても証拠は消さず、Input/State の値は記録しない。
    diagnostics.save(tmp_path, _Page())
    saved = (tmp_path / "browser.json").read_text(encoding="utf-8")
    assert json.loads(saved)["completed_204_cancellations"] == [entry]
    assert "private" not in saved


@pytest.mark.parametrize("status", [None, 200, 500, 502])
def test_unanswered_or_non_204_abort_is_still_a_failure(status):
    diagnostics = _BrowserDiagnostics(_Page(), BASE)
    request = _Request()
    if status is not None:
        diagnostics._response(_response(request, status))
    diagnostics._request_failed(request)
    assert diagnostics.completed_204_cancellations == []
    assert diagnostics.failed_requests[0]["response_status"] == status
    with pytest.raises(AssertionError, match="requestfailed"):
        diagnostics.assert_clean()


@pytest.mark.parametrize("failure", ["net::ERR_CONNECTION_RESET", "net::ERR_CONNECTION_REFUSED"])
def test_204_does_not_excuse_other_transport_failures(failure):
    diagnostics = _BrowserDiagnostics(_Page(), BASE)
    request = _Request(failure=failure)
    diagnostics._response(_response(request, 204))
    diagnostics._request_failed(request)
    assert diagnostics.completed_204_cancellations == []
    with pytest.raises(AssertionError, match=failure):
        diagnostics.assert_clean()


def test_another_request_with_the_same_url_and_output_cannot_excuse_an_abort():
    diagnostics = _BrowserDiagnostics(_Page(), BASE)
    completed, aborted = _Request(), _Request()
    diagnostics._response(_response(completed, 204))
    diagnostics._request_failed(aborted)
    assert diagnostics.failed_requests[0]["response_status"] is None
    assert diagnostics.completed_204_cancellations == []
    with pytest.raises(AssertionError, match="requestfailed"):
        diagnostics.assert_clean()


@pytest.mark.parametrize("method,path", [
    ("GET", "/_dash-update-component"),
    ("POST", "/unrelated/_dash-update-component"),
])
def test_204_exception_is_limited_to_dash_callback_posts(method, path):
    diagnostics = _BrowserDiagnostics(_Page(), BASE)
    request = _Request(method=method, path=path)
    diagnostics._response(_response(request, 204))
    diagnostics._request_failed(request)
    assert diagnostics.completed_204_cancellations == []
    with pytest.raises(AssertionError, match="requestfailed"):
        diagnostics.assert_clean()


def test_502_response_is_a_failure_even_without_a_requestfailed_event():
    diagnostics = _BrowserDiagnostics(_Page(), BASE)
    diagnostics._response(_response(_Request(), 502))
    assert diagnostics.failed_requests == []
    with pytest.raises(AssertionError, match="http_5xx"):
        diagnostics.assert_clean()
