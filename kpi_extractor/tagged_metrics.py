"""Operating KPIs a company states in its earnings releases only now and then but tags in every report's XBRL
(Microsoft's commercial remaining performance obligation: in its releases from FY2026, in every 10-Q and 10-K since
FY2020 as RevenueRemainingPerformanceObligation of its commercial customers).

A KPI's tag is learned, never guessed: the report's fact that equals the release's figure, to the release's rounding,
in at least two quarters, the same concept and dimensions each time, and in no quarter a different figure. Other
quarters are then read from that fact, no AI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

MIN_MATCHES = 2


@dataclass(frozen=True)
class Tag:
    concept: str
    dimensions: tuple[tuple[str, str], ...]  # (axis column, member), sorted
    instant: bool


def quarter_facts(facts: pd.DataFrame, period_end: date) -> dict[Tag, float]:
    """Each numeric fact of the quarter: an instant at its end, or a three-month duration ending then."""
    frame = facts[facts["numeric_value"].notna()]
    dimension_columns = [column for column in frame.columns if column.startswith("dim_")]
    ends = pd.to_datetime(frame["period_end"], errors="coerce").dt.date
    starts = pd.to_datetime(frame["period_start"], errors="coerce").dt.date
    instants = pd.to_datetime(frame.get("period_instant", pd.Series(index=frame.index, dtype=object)), errors="coerce").dt.date
    found: dict[Tag, float] = {}
    for index, fact in frame.iterrows():
        instant = fact["period_type"] == "instant"
        if instant:
            if instants[index] != period_end:
                continue
        elif ends[index] != period_end or starts[index] is None or pd.isna(starts[index]) \
                or not 80 <= (period_end - starts[index]).days <= 100:
            continue
        dimensions = tuple(sorted((column, str(fact[column])) for column in dimension_columns if pd.notna(fact[column])))
        found.setdefault(Tag(str(fact["concept"]), dimensions, instant), float(fact["numeric_value"]))
    return found


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
    matched: dict[Tag, int] = {}
    for quarter, value in readings.items():
        for tag, fact in (facts.get(quarter) or {}).items():
            if fact and agrees(fact, value):
                matched[tag] = matched.get(tag, 0) + 1
    candidates = [tag for tag, count in matched.items() if count >= MIN_MATCHES and not any(
        tag in (facts.get(quarter) or {}) and not agrees(facts[quarter][tag], value) for quarter, value in readings.items())]
    return candidates[0] if len(candidates) == 1 else None
