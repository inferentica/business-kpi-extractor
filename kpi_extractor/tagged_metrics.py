"""Operating KPIs a company states in its earnings releases only now and then but tags in every report's XBRL
(Microsoft's commercial remaining performance obligation: in its releases from FY2026, in every 10-Q and 10-K since
FY2020 as RevenueRemainingPerformanceObligation of its commercial customers).

A KPI's tag is learned, never guessed: the report's fact that equals the release's figure, to the release's rounding,
in at least two quarters, the same concept and dimensions each time, and in no quarter a different figure. Other
quarters are then read from that fact, no AI."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import pandas as pd

MIN_MATCHES = 2


@dataclass(frozen=True)
class Tag:
    concept: str
    dimensions: tuple[tuple[str, str], ...]  # (axis column, member), sorted
    instant: bool


def quarter_facts(facts: pd.DataFrame, period_end: date, days: tuple[int, int] = (80, 100)) -> dict[Tag, float]:
    """Each numeric fact of the quarter: an instant at its end, or a three-month duration ending then (a year's, with
    days of 330 to 400)."""
    frame = facts[facts["numeric_value"].notna()]
    dimension_columns = [column for column in frame.columns if column.startswith("dim_")]
    ends = pd.to_datetime(frame["period_end"], errors="coerce").dt.date
    starts = pd.to_datetime(frame["period_start"], errors="coerce").dt.date
    instants = pd.to_datetime(frame.get("period_instant", pd.Series(index=frame.index, dtype=object)), errors="coerce").dt.date
    found: dict[Tag, float] = {}
    timed: set[Tag] = set()
    for index, fact in frame.iterrows():
        instant = fact["period_type"] == "instant"
        if instant:
            if instants[index] != period_end:
                continue
        elif ends[index] != period_end or starts[index] is None or pd.isna(starts[index]) \
                or not days[0] <= (period_end - starts[index]).days <= days[1]:
            continue
        every = [(column, str(fact[column])) for column in dimension_columns if pd.notna(fact[column])]
        # A timing member (the date a backlog is expected from) changes every quarter and names no part: it is left
        # out of the tag, which an untimed figure of the same concept takes first.
        dimensions = tuple(sorted(item for item in every if not _TIMING.search(item[0])))
        tag = Tag(str(fact["concept"]), dimensions, instant)
        if tag not in found or (tag in timed and len(every) == len(dimensions)):
            found[tag] = float(fact["numeric_value"])
            if len(every) != len(dimensions):
                timed.add(tag)
            else:
                timed.discard(tag)
    return found


def year_ago_facts(facts: pd.DataFrame, period_end: date, days: tuple[int, int] = (80, 100)) -> tuple[date, dict[Tag, float]] | None:
    """A report's comparative column: the facts of the period ending about a year before its own (a 52/53-week year
    ends within a week of the date a year back), with its end."""
    ends = {end for end in pd.to_datetime(facts["period_end"], errors="coerce").dt.date if pd.notna(end)}
    earlier = [end for end in ends if 357 <= (period_end - end).days <= 371]
    if not earlier:
        return None
    end = min(earlier, key=lambda found: abs((period_end - found).days - 364))
    return end, quarter_facts(facts, end, days)


def _tolerance(value: float) -> float:
    """Half a unit of the figure's own rounding ("$677 billion" is 676.5 to 677.5 billion), at least one unit."""
    whole = abs(int(round(value)))
    step = 1
    while whole and whole % (step * 10) == 0 and step < 10 ** 12:
        step *= 10
    return max(step / 2, 1.0)


def agrees(fact: float, value: float) -> bool:
    return abs(fact - value) <= _tolerance(value)


def learn_tag(readings: dict[tuple[str, str], float], facts: dict[tuple[str, str], dict[Tag, float]]) -> Tag | None:
    """The one tag whose fact equals the KPI's reading in at least two quarters and never differs from one."""
    candidates = learn_tags(readings, facts)
    return candidates[0] if len(candidates) == 1 else None


def learn_tags(readings: dict[tuple[str, str], float], facts: dict[tuple[str, str], dict[Tag, float]]) -> list[Tag]:
    """Every tag whose fact equals the KPI's reading in at least two quarters and never differs from one: a company
    that retags a line (Adobe's customer groups, by segment in FY2025 and on their own since) gives one per layout."""
    matched: dict[Tag, int] = {}
    for quarter, value in readings.items():
        for tag, fact in (facts.get(quarter) or {}).items():
            if fact and agrees(fact, value):
                matched[tag] = matched.get(tag, 0) + 1
    return [tag for tag, count in matched.items() if count >= MIN_MATCHES and not any(
        tag in (facts.get(quarter) or {}) and not agrees(facts[quarter][tag], value) for quarter, value in readings.items())]


_TIMING = re.compile(r"Timing|StartDate|ExpectedTiming", re.I)
_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _member_name(member: str) -> str:
    name = member.split(":")[-1]
    name = re.sub(r"Member$", "", name)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)


def fact_lines(facts: pd.DataFrame, period_end: date) -> list[tuple[Tag, float, str, str]]:
    """The quarter's figures as a reader sees them: (tag, value, "concept label | member", unit)."""
    found = quarter_facts(facts, period_end)
    labels: dict[str, str] = {}
    units: dict[str, str] = {}
    for _, fact in facts.iterrows():
        concept = str(fact["concept"])
        if concept not in labels and isinstance(fact.get("label"), str):
            labels[concept] = fact["label"]
        unit = fact.get("unit_ref")
        if concept not in units and isinstance(unit, str):
            units[concept] = unit
    lines = []
    for tag, value in found.items():
        # A timing axis (Amazon's backlog "expected from 2026-07-01") qualifies the whole figure; it names no part.
        members = " · ".join(_member_name(member) for _axis, member in tag.dimensions)
        label = labels.get(tag.concept)
        if not label or _DATE_LIKE.match(label):
            label = _member_name(tag.concept)
        name = tag.concept.split(":")[-1]
        lines.append((tag, value, f"{label} [{name}]{' | ' + members if members else ''}", units.get(tag.concept, "")))
    return lines


def unit_fits(kpi_unit: str, fact_unit: str) -> bool:
    """A currency KPI is a monetary figure; a count is shares or a pure number; a percentage or ratio is pure."""
    fact = fact_unit.lower()
    money = "iso4217" in fact or fact in ("usd", "eur", "jpy", "twd", "gbp", "cny") or fact.startswith("usd")
    if kpi_unit == "currency":
        return money
    return not money


def tag_from_locator(locator: dict) -> Tag | None:
    if not locator.get("tagged") or not locator.get("concept"):
        return None
    return Tag(locator["concept"], tuple(sorted((locator.get("dimensions") or {}).items())), bool(locator.get("instant")))
