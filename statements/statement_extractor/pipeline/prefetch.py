"""Filings' submissions downloaded a few at a time, while the readers work through them in order.

A round's time is SEC requests waiting their turn, not bytes: one filing's submission arrives in about a second
whatever its size (Woori's 120.6 MB 20-F in about 8 s), and a company's jobs asked for them one after another (round 19
measured it). Here the next few download on threads; the readers still take each filing in the same order, so what
they read is unchanged. edgartools paces every request through one rate limiter per process (EDGAR_RATE_LIMIT_PER_SEC,
9 a second), shared by the threads, and each runner has its own address. A download that fails here is simply tried
again by the reader, with its usual retries."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from itertools import islice


def _quiet(load, item) -> None:
    try:
        load(item)
    except Exception:  # noqa: BLE001 - the reader loads it again in order, with retries, and records any failure
        pass


def load_all(items, load, workers: int = 4) -> None:
    """Every item loaded, `workers` at a time."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda item: _quiet(load, item), items))


def ahead(items, load, depth: int = 3):
    """The items in order, each yielded once it is loaded, the next `depth` loading meanwhile."""
    pool = ThreadPoolExecutor(max_workers=depth)
    pending: deque = deque()
    source = iter(items)
    try:
        for item in islice(source, depth):
            pending.append((item, pool.submit(_quiet, load, item)))
        while pending:
            item, future = pending.popleft()
            future.result()
            following = next(source, None)
            if following is not None:
                pending.append((following, pool.submit(_quiet, load, following)))
            yield item
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
