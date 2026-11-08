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


def test_githubs_token_service_is_asked_again_after_an_outage(monkeypatch):
    plane = ControlPlane("https://example.test/functions/v1/business-kpi-workflow")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://token.example.test/?x=1")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "t")
    monkeypatch.setattr(control_module.time, "sleep", lambda _seconds: None)
    replies = [_http(503, "unavailable"), io.BytesIO(json.dumps({"value": "oidc"}).encode())]

    def urlopen(request, timeout=None):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(control_module.urllib.request, "urlopen", urlopen)
    assert plane._oidc_token() == "oidc"


def test_a_company_is_run_again_once_after_a_temporary_sec_failure(monkeypatch):
    import httpx
    from kpi_extractor import cli
    monkeypatch.setattr(cli, "COMPANY_RETRY_WAIT", 0)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("The read operation timed out")
        return cli.SymbolResult("PRU", values=3)

    assert cli.run_symbol("PRU", flaky, log=lambda *_: None).values == 3 and len(calls) == 2
    # A failure trying again cannot cure is the company's result at once.
    broken = lambda: (_ for _ in ()).throw(ValueError("bad list"))  # noqa: E731
    assert cli.run_symbol("X", broken, log=lambda *_: None).errors == ["ValueError: bad list"]
