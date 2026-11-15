"""Network failures retried before a filing is given up as unreadable. A filing that times out once would otherwise be
skipped, and its periods served from another filing's presentation (ANET's FY2020 Q3 10-Q timed out in phase-1 run
22, so its 2019 Q3 comparatives came from an older filing). Only network errors are retried; a filing that fails to
parse fails at once."""
from __future__ import annotations

import re
import time

# SEC's own refusals while busy are passing too: "Server error '503 Service Unavailable'", "429 Too Many Requests"
# (Vale's 2013 20-F was lost to one in round 25).
_TRANSIENT = re.compile(r"Timeout|Connect|RemoteProtocol|NetworkError|ReadError|ChunkedEncoding|IncompleteRead|"
                        r"Server error '5\d\d|\b429\b", re.I)
_WAITS = (15, 60, 180)


def transient(error: BaseException) -> bool:
    return bool(_TRANSIENT.search(type(error).__name__) or _TRANSIENT.search(str(error)[:200]))


def with_network_retries(read, waits=_WAITS, sleep=time.sleep):
    """read(), tried again after each wait while it fails with a network error."""
    for wait in (*waits, None):
        try:
            return read()
        except Exception as error:  # noqa: BLE001 - re-raised unless it is a network failure worth another try
            if wait is None or not transient(error):
                raise
            sleep(wait)
    return None
