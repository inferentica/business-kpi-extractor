"""The business-kpi-workflow Edge Function: run bookkeeping, storage and the DeepSeek proxy, signed in with GitHub OIDC."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

OIDC_AUDIENCE = "tradetracker-business-kpis"
_TOKEN_LIFETIME_SECONDS = 240
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504, 546}


class ControlError(RuntimeError):
    pass


class ControlPlane:
    def __init__(self, url: str):
        self.url = url
        self.run_id: str | None = None
        self._token: tuple[str, float] | None = None

    def call(self, operation: str, timeout: float = 90, attempts: int = 5, **payload) -> dict:
        # Up to ~30 s of backoff: long enough to ride out a database or gateway blip (a Bad Gateway lasted ~15 s).
        body = {"operation": operation, **({"runId": self.run_id} if self.run_id else {}), **payload}
        data = json.dumps(body, default=str).encode()
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(self.url, data=data, method="POST", headers={
                "Authorization": f"Bearer {self._oidc_token()}", "Content-Type": "application/json",
            })
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    body = response.read() or b"{}"
                try:
                    return json.loads(body)
                except json.JSONDecodeError as error:
                    # The function stopped at its wall-clock limit after sending keep-alive spaces: a failed call.
                    if attempt == attempts:
                        raise ControlError(f"{operation} failed: reply cut off after {len(body)} bytes") from error
            except urllib.error.HTTPError as error:
                detail = error.read().decode(errors="replace")[:500]
                if error.code not in _RETRYABLE_STATUS or attempt == attempts:
                    raise ControlError(f"{operation} failed ({error.code}): {detail}") from error
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == attempts:
                    raise ControlError(f"{operation} failed: {error}") from error
            time.sleep(2 ** attempt)
        raise ControlError(f"{operation} failed")

    def ai(self, symbol: str, purpose: str, system: str, user: str, thinking: bool, model: str = "flash") -> str:
        # The reply streams keep-alive spaces before its JSON, so a long reasoning call outlasts the gateway's idle
        # limit; a failure after the first byte arrives as {"error": ...} and is retried once.
        for attempt in (1, 2):
            response = self.call("ai", timeout=390, attempts=2, symbol=symbol, purpose=purpose, model=model,
                                 system=system, user=user, thinking=thinking)
            if not response.get("error"):
                return str(response.get("content") or "")
            if attempt == 2:
                raise ControlError(f"ai failed: {str(response['error'])[:300]}")
            time.sleep(5)
        return ""

    def _oidc_token(self) -> str:
        if self._token and time.monotonic() < self._token[1]:
            return self._token[0]
        request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
        request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
        if not request_url or not request_token:
            raise ControlError("GitHub OIDC is unavailable; run inside GitHub Actions with id-token: write")
        separator = "&" if "?" in request_url else "?"
        request = urllib.request.Request(
            f"{request_url}{separator}audience={urllib.parse.quote(OIDC_AUDIENCE)}",
            headers={"Authorization": f"Bearer {request_token}"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            token = json.loads(response.read())["value"]
        self._token = (token, time.monotonic() + _TOKEN_LIFETIME_SECONDS)
        return token


class DryRunControl:
    """Local development: reads SEC data and prints what would be stored; no AI, no writes."""

    run_id = "dry-run"

    def call(self, operation: str, **payload) -> dict:
        if operation == "start":
            return {"runId": self.run_id, "symbols": payload.get("symbols") or [], "quarters": payload.get("quarters", 20),
                    "forceRefresh": bool(payload.get("forceRefresh"))}
        if operation == "symbol_state":
            return {"spec": None, "filings": [], "values": []}
        if operation == "store":
            values = payload.get("values") or []
            print(f"[dry-run] store {payload.get('symbol')}: {len(values)} values, {len(payload.get('filings') or [])} filings")
            for value in values[:60]:
                print(f"    {value['fiscal_year']} {value['fiscal_period']} {value['group_label']} / {value['kpi_label']}"
                      f" = {value['value']:,.2f} {value.get('currency') or ''} [{value['method']}, {value['validation_status']}]")
            return {"stored": len(values)}
        return {}

    def ai(self, symbol: str, purpose: str, system: str, user: str, thinking: bool, model: str = "flash") -> str:
        return ""
