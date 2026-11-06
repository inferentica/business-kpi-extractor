import io
import json
import urllib.error

import pytest

from kpi_extractor import control as control_module
from kpi_extractor.control import ControlError, ControlPlane


def _plane(monkeypatch, replies):
    plane = ControlPlane("https://example.test/functions/v1/business-kpi-workflow")
    monkeypatch.setattr(plane, "_oidc_token", lambda: "token")
    monkeypatch.setattr(control_module.time, "sleep", lambda _seconds: None)
    calls = []

    def urlopen(request, timeout=None):
        calls.append(request)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())
    monkeypatch.setattr(control_module.urllib.request, "urlopen", urlopen)
    return plane, calls


def _http(code, detail):
    return urllib.error.HTTPError("https://example.test", code, "x", {}, io.BytesIO(detail.encode()))


def test_a_token_check_that_timed_out_is_asked_again(monkeypatch):
    plane, calls = _plane(monkeypatch, [_http(401, '{"error":"Invalid GitHub OIDC token: request timed out"}'), {"ok": True}])
    assert plane.call("store", symbol="PHG") == {"ok": True} and len(calls) == 2


def test_a_refused_token_still_fails_at_once(monkeypatch):
    plane, calls = _plane(monkeypatch, [_http(401, '{"error":"Repository is not allowed"}'), {"ok": True}])
    with pytest.raises(ControlError):
        plane.call("store", symbol="PHG")
    assert len(calls) == 1
