"""Exact dates for comparative columns. A printed column is dated from its header, which can name only the year or the
latest year's month and day, so a 52/53-week company's older years come out a few days off (Apple's FY2006 printed
as ending September 29 in its FY2007 10-K; that year ended September 30). Each period's own filing states its dates;
a comparative column within a week of them takes them."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from ..domain.model import Reading

_SLACK = timedelta(days=7)


def align_dates(readings: list[Reading]) -> list[Reading]:
    own: dict[tuple, set] = {}
    for reading in readings:
        if reading.own:
            own.setdefault(reading.key, set()).add((reading.period_end, reading.period_start))
    aligned = []
    for reading in readings:
        dates = own.get(reading.key)
        if not reading.own and dates and len(dates) == 1:
            end, start = next(iter(dates))
            if end != reading.period_end and abs(end - reading.period_end) <= _SLACK:
                reading = replace(reading, period_end=end,
                                  period_start=start if reading.period_start is not None else None)
        aligned.append(reading)
    return aligned
