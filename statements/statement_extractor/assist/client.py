"""DeepSeek through Quotiva's statements-ai function, signed in with the runner's GitHub OIDC token: the runner holds no
key, and the function fixes the model (Flash), reasoning (off), temperature (0) and a monthly cap in yuan, and logs each
call. Every answer is cached by its prompt, so a rerun asks nothing."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .columns import PROMPT_VERSION, cost_cny

URL = os.environ.get("STATEMENTS_AI_URL", "https://gplrniwqomrrufkkxqjh.supabase.co/functions/v1/statements-ai")
AUDIENCE = "quotiva-statements"
MODEL = "deepseek-flash"


def is_peak(at: datetime) -> bool:
    """DeepSeek's peak hours, 01:00-04:00 and 06:00-10:00 UTC on a weekday (as Quotiva prices them)."""
    return at.weekday() < 5 and (1 <= at.hour < 4 or 6 <= at.hour < 10)


class Client:
    def __init__(self, cache: Path, max_calls: int = 200):
        self.cache, self.max_calls = cache, max_calls
        self.calls, self.cost, self.seconds, self.cached = 0, 0.0, 0.0, 0
        self.cache.mkdir(parents=True, exist_ok=True)

    def ask(self, system: str, user: str, purpose: str = "columns", symbol: str | None = None) -> str:
        digest = hashlib.sha256(f"{PROMPT_VERSION}\n{MODEL}\n{system}\n{user}".encode()).hexdigest()
        kept = self.cache / f"{digest}.json"
        if kept.exists():
            self.cached += 1
            return json.loads(kept.read_text())["content"]
        if self.calls >= self.max_calls:
            raise RuntimeError(f"call limit {self.max_calls} reached")
        body = json.dumps({"purpose": purpose, "system": system, "user": user, "promptVersion": PROMPT_VERSION,
                           "symbol": symbol}).encode()
        started = time.monotonic()
        for attempt in range(1, 4):
            request = urllib.request.Request(URL, data=body, method="POST", headers={
                "Authorization": f"Bearer {_oidc_token()}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=150) as response:
                    reply = json.loads(response.read())
                break
            except urllib.error.HTTPError as error:
                detail = error.read().decode(errors="replace")[:300]
                if error.code in (400, 401, 403, 429) or attempt == 3:
                    raise RuntimeError(f"statements-ai refused ({error.code}): {detail}") from error
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == 3:
                    raise RuntimeError(f"statements-ai failed: {error}") from error
            time.sleep(5 * attempt)
        usage = {"prompt_tokens": reply.get("inputTokens", 0), "prompt_cache_hit_tokens": reply.get("cacheHitTokens", 0),
                 "completion_tokens": reply.get("outputTokens", 0)}
        cost = cost_cny(usage, is_peak(datetime.now(timezone.utc)))
        self.calls, self.cost, self.seconds = self.calls + 1, self.cost + cost, self.seconds + time.monotonic() - started
        kept.write_text(json.dumps({"content": reply["content"], "usage": usage, "cost_cny": cost, "model": MODEL,
                                    "call_id": reply.get("callId"), "prompt_version": PROMPT_VERSION}))
        return reply["content"]


def _oidc_token() -> str:
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not request_url or not request_token:
        raise RuntimeError("GitHub OIDC is unavailable; run inside GitHub Actions with id-token: write")
    separator = "&" if "?" in request_url else "?"
    request = urllib.request.Request(f"{request_url}{separator}audience={urllib.parse.quote(AUDIENCE)}",
                                     headers={"Authorization": f"Bearer {request_token}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())["value"]
