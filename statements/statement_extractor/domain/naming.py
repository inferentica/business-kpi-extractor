"""A company's fiscal-year naming, era by era. A company names its years its own way (Kroger's fiscal 2015 ends in
January 2016, RPM's fiscal 2025 in May 2025), and a company that moves its year end starts another era (Gildan closed
its years in late September or early October until 2014, then in early January). Each era's naming is learnt from the
fiscal years its XBRL filings state, by majority and an annual report's counting twice: one filing's slip (RPM's
first-quarter 10-Q of fiscal 2027 states 2026) never names the company's years. An era no XBRL filing covers names a
year for the year it ends in, and a year ending in the first week of January for the year before.

A period takes its fiscal year from the era its own end falls in, never the era of the report that printed it: a new
era's comparatives recast in its months (Oshkosh's calendar quarters of 2021, presented after its move to December) and
its transition months (Gildan's October 2014 to January 2015) are left out where they would take a year the era before
already names."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from ..sources.edgar import FiscalCalendarError, fiscal_label, fiscal_year_end_for
from .calendar import _SAME, _gap, _mmdd, year_end_for
from .periods import Period, label_period

_SLACK = timedelta(days=10)  # a 52/53-week year ends up to a week either side of its usual day
_YTD_START = timedelta(days=14)  # a year to date opens on the day after the year before closed, or within two weeks


@dataclass
class Era:
    fiscal_year_end: str  # as its newest annual report closes
    last: date | None  # its newest annual report's period end; None for a year end no annual report closes yet
    naming: int = 0  # the years' names against the year their ends fall in, before a first-week-of-January end's shift
    learnt: bool = False


def _shift(fiscal_year_end: str) -> int:
    """A year ending in the first week of January is the year before's (Gildan's fiscal 2015 ends January 3, 2016)."""
    return -1 if fiscal_year_end[:2] == "01" and int(fiscal_year_end[2:]) <= 7 else 0


class Naming:
    def __init__(self, annual_ends, usual: str, samples=()):
        self.usual = usual
        self.annual_ends = sorted({end for end in annual_ends if end is not None})
        self.samples: list[tuple[date, str, bool]] = []
        self.eras = _eras(self.annual_ends, usual)
        for sample in samples:
            self.add(sample)

    def add(self, sample: tuple[date, str, bool]) -> None:
        """A fiscal year a filing states: (its period end, the year it names, an annual report or not)."""
        if sample[1] and str(sample[1]).isdigit() and sample not in self.samples:
            self.samples.append(sample)
            self._learn()

    def with_ends(self, ends) -> Naming:
        """The same naming over more annual reports' ends (printed annual reports EDGAR gives no period for)."""
        return Naming([*self.annual_ends, *ends], self.usual, self.samples)

    def year_end(self, end: date) -> str:
        return year_end_for(end, self.annual_ends, self.usual)

    def era_of(self, end: date) -> int:
        for index, era in enumerate(self.eras):
            if era.last is not None and end <= era.last + _SLACK:
                return index
        return len(self.eras) - 1

    def offset(self, end: date) -> int:
        """The year offset fiscal_label takes for a period ending on `end`."""
        return self.eras[self.era_of(end)].naming + _shift(self.year_end(end))

    def current_offset(self) -> int:
        return self.eras[-1].naming + _shift(self.usual)

    def label(self, period: Period) -> tuple[tuple[str, str, str] | None, str]:
        """(fiscal year, fiscal period, duration) and "" ; or None and why: "off calendar" (no label on this era's
        calendar), "year to date" (one that does not open its fiscal year), "recast" (a year the era before names)."""
        fiscal_year_end, offset = self.year_end(period.end), self.offset(period.end)
        labelled = label_period(period, fiscal_year_end, offset)
        if labelled is None:
            return None, "off calendar"
        if labelled[2] == "ytd" and period.start is not None:
            closed = fiscal_year_end_for(period.end, fiscal_year_end)
            opened = date(closed.year - 1, closed.month, min(closed.day, calendar.monthrange(closed.year - 1, closed.month)[1]))
            if abs(period.start - (opened + timedelta(days=1))) > _YTD_START:
                return None, "year to date"
        era = self.era_of(period.end)
        before = self._last_year(era - 1) if era > 0 else None
        if before is not None and int(labelled[0]) <= before:
            return None, "recast"
        return labelled, ""

    def _last_year(self, index: int) -> int | None:
        last = self.eras[index].last
        if last is None:
            return None
        try:
            return int(fiscal_label(last, self.year_end(last), annual=True, year_offset=self.offset(last))[0])
        except FiscalCalendarError:
            return None

    def _learn(self) -> None:
        votes: dict[int, dict[int, int]] = {}
        newest: dict[int, tuple[date, int]] = {}
        for end, year, annual in self.samples:
            fiscal_year_end = self.year_end(end)
            stated = int(year) - fiscal_year_end_for(end, fiscal_year_end).year
            if stated not in (-1, 0, 1):
                continue
            naming, era = stated - _shift(fiscal_year_end), self.era_of(end)
            votes.setdefault(era, {})
            votes[era][naming] = votes[era].get(naming, 0) + (2 if annual else 1)  # a year's report names the year it closes
            if era not in newest or end > newest[era][0]:
                newest[era] = (end, naming)
        for index, era in enumerate(self.eras):
            counted = votes.get(index)
            if not counted:
                era.naming, era.learnt = 0, False
                continue
            best = max(counted.values())
            leaders = [naming for naming, count in counted.items() if count == best]
            era.naming = newest[index][1] if newest[index][1] in leaders else max(leaders)
            era.learnt = True

    def describe(self) -> list[dict]:
        return [{"fiscal_year_end": era.fiscal_year_end, "last": era.last.isoformat() if era.last else None,
                 "naming": era.naming, "learnt": era.learnt} for era in self.eras]


def _eras(annual_ends: list[date], usual: str) -> list[Era]:
    """Runs of annual reports closing on about the same day each year, oldest first; one report on another day between
    two runs on one day is no era of its own."""
    runs: list[list[date]] = []
    for end in annual_ends:
        if runs and _gap(_mmdd(end), _mmdd(runs[-1][-1])) <= _SAME:
            runs[-1].append(end)
        else:
            runs.append([end])
    merged: list[list[date]] = []
    for index, run in enumerate(runs):
        lone = len(run) == 1 and 0 < index < len(runs) - 1 and _gap(_mmdd(runs[index - 1][-1]), _mmdd(runs[index + 1][0])) <= _SAME
        if lone:
            continue
        if merged and _gap(_mmdd(merged[-1][-1]), _mmdd(run[0])) <= _SAME:
            merged[-1].extend(run)
        else:
            merged.append(run)
    eras = [Era(_mmdd(run[-1]), run[-1]) for run in merged]
    if not eras or _gap(eras[-1].fiscal_year_end, usual) > _SAME:
        eras.append(Era(usual, None))  # a year end the company has moved to and closed no year on yet
    return eras


def name_readings(readings: list, naming: Naming) -> tuple[list, dict[str, int]]:
    """Each reading labelled by the era its own end falls in. Left out: a year to date that does not open its fiscal
    year, and a new era's recast or transition period that would take a year the era before names. A reading off its
    era's calendar keeps the label its reader gave it (Brookfield Asset Management's short first year)."""
    kept, left_out = [], {}
    for reading in readings:
        labelled, why = naming.label(Period(reading.period_end, reading.period_start))
        if labelled is not None:
            reading.fiscal_year, reading.fiscal_period = labelled[0], labelled[1]
        elif why != "off calendar":
            left_out[why] = left_out.get(why, 0) + 1
            continue
        kept.append(reading)
    return kept, left_out
