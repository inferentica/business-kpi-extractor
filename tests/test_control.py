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


def test_offpeak_waits_only_in_deepseek_peak_hours():
    from datetime import datetime, timezone
    from kpi_extractor.offpeak import is_peak, seconds_until_offpeak
    friday_peak = datetime(2026, 11, 6, 8, 30, tzinfo=timezone.utc)
    assert is_peak(friday_peak) and seconds_until_offpeak(friday_peak) == 90 * 60 + 1
    assert seconds_until_offpeak(datetime(2026, 11, 6, 3, 59, tzinfo=timezone.utc)) == 61
    assert not is_peak(datetime(2026, 11, 6, 13, 0, tzinfo=timezone.utc))
    assert not is_peak(datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc))  # Saturday
    assert seconds_until_offpeak(datetime(2026, 11, 8, 2, 0, tzinfo=timezone.utc)) == 0


def test_the_checker_flags_a_per_unit_scale_a_year_row_and_an_unnamed_place():
    from kpi_extractor.audit_check import Fetcher, check_figure
    html = """<table><tr><td>($ in millions)</td><td>Q3 2026</td></tr>
<tr><td>Revenue per package</td><td>15.32</td></tr><tr><td>2025</td><td>1,210</td></tr>
<tr><td>International Businesses</td><td>53,903</td></tr></table>"""
    fetcher = Fetcher(); fetcher.raw["https://example.com/x.htm"] = html
    def check(kpi, row, value, unit="currency"):
        item = {"symbol": "X", "kpi": f"Operating Metrics / {kpi}", "period": "Q3 2026", "periodEnd": "2026-09-30", "value": value,
                "unit": unit, "currency": "USD", "source": "https://example.com/x.htm"}
        return check_figure(item, {"locator": {"table": "A_T0", "row": row, "col": 1, "scale": 1e6}}, fetcher).flags
    assert any("per-unit" in f for f in check("Revenue per Package", 1, 15_320_000))
    assert any("row is a year" in f for f in check("Pre-Owned Home Sales", 2, 1210, "count"))
    assert any("names a place" in f for f in check("Japan Account Balances", 3, 53_903_000_000))
    assert check("Total Revenue", 3, 53_903_000_000) == ["row label shares no word with the KPI: 'International Businesses'"]
