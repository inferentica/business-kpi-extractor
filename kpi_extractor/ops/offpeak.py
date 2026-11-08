"""DeepSeek bills half outside its peak hours: 09:00-12:00 and 14:00-18:00 Beijing time, Monday to Friday (01:00-04:00 and
06:00-10:00 UTC). Work that can wait (a backfill, a trial, the golden set) starts off-peak.

    python -m kpi_extractor.ops.offpeak          # sleeps until off-peak, printing how long
    python -m kpi_extractor.ops.offpeak --check  # prints the seconds to wait and exits
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone

PEAK_HOURS_UTC = ((1, 4), (6, 10))


def is_peak(at: datetime) -> bool:
    at = at.astimezone(timezone.utc)
    return at.weekday() < 5 and any(start <= at.hour < end for start, end in PEAK_HOURS_UTC)


def seconds_until_offpeak(at: datetime) -> int:
    """Seconds until DeepSeek's next off-peak moment; 0 when `at` is already off-peak."""
    at = at.astimezone(timezone.utc)
    if not is_peak(at):
        return 0
    end = next(end for start, end in PEAK_HOURS_UTC if start <= at.hour < end)
    off = at.replace(hour=end, minute=0, second=0, microsecond=0)
    return max(0, int((off - at).total_seconds())) + 1


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    wait = seconds_until_offpeak(datetime.now(timezone.utc))
    if "--check" in args:
        print(wait)
        return 0
    if wait:
        print(f"DeepSeek peak hours: waiting {timedelta(seconds=wait)} for off-peak prices", flush=True)
        time.sleep(wait)
    return 0


if __name__ == "__main__":
    sys.exit(main())
