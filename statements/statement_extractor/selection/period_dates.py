"""Exact dates for comparative columns. A printed column is dated from its header, which can name only the year or the
latest year's month and day, so a 52/53-week company's older years come out a few days off (Apple's FY2006 printed
as ending September 29 in its FY2007 10-K; that year ended September 30). Each period takes the dates of its best
dated presentation: its own filing's XBRL, else its own filing's, else any XBRL filing's (Varex's FY2018 10-K prints
"October 2, 2018" over the year its XBRL closes on September 28). A presentation within six days of them takes them;
one a week or more off is another period (selection/choose.py)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import timedelta

from ..domain.model import Reading

SLACK = timedelta(days=6)


def align_dates(readings: list[Reading]) -> list[Reading]:
    groups: dict[tuple, list[Reading]] = defaultdict(list)
    for reading in readings:
        groups[reading.key].append(reading)
    dated = {key: _reference(group) for key, group in groups.items()}
    aligned = []
    for reading in readings:
        reference = dated.get(reading.key)
        if reference is not None:
            end, start = reference
            if end != reading.period_end and abs(end - reading.period_end) <= SLACK:
                reading = replace(reading, period_end=end,
                                  period_start=start if reading.period_start is not None else None)
        aligned.append(reading)
    return aligned


def _reference(group: list[Reading]):
    """The one (end, start) the period's best dated presentations agree on, or None."""
    for chosen in ([r for r in group if r.own and r.source == "xbrl"], [r for r in group if r.own],
                   [r for r in group if r.source == "xbrl"]):
        dates = {(reading.period_end, reading.period_start) for reading in chosen}
        if len(dates) == 1:
            return next(iter(dates))
        if dates:
            return None
    return None


def same_period(group: list[Reading]) -> tuple[list[Reading], list[Reading]]:
    """(the presentations of the period, those of another period under its label). The period ends on its best dated
    presentation's end, else the one most presentations share, the newest filing's on a tie; a presentation ending a week
    or more off it is another period: CSN's 2013 20-F dates a column April 27, 2012 under its first quarter of 2012, Delta's
    2007 10-Qs present April 30 and May 1 balance sheets after its first quarter, and Gildan's 2015 releases recast its
    fiscal 2014 quarters in calendar months a week before its own."""
    reference = _reference(group)
    if reference is None:
        counts: dict = defaultdict(lambda: [0, None])
        for reading in group:
            entry = counts[(reading.period_end, reading.period_start)]
            entry[0] += 1
            entry[1] = max(entry[1] or reading.filed, reading.filed)
        reference = max(counts, key=lambda dates: tuple(counts[dates]))
    end = reference[0]
    # By its end alone: a period's start differs where a company's first year was short (Lucid's own 10-K for 2020
    # opens on April 30, its later ones on January 1) or a reader counted it short (Costco's 12-week third quarter of
    # 2010 read from February 8); the winner's own start is served.
    near = [reading for reading in group if abs(reading.period_end - end) <= SLACK]
    return near, [reading for reading in group if abs(reading.period_end - end) > SLACK]
