"""Statements from a filing's IFRS-tagged facts (BMV's quarterly reports, CMF's financial statements): each statement
laid out by the IFRS taxonomy role the filing's facts fill most, one reading per period it gives. Every line keeps its
tag; its name is the taxonomy's English label (the filing prints none of its own), else the tag. Values are as filed,
in the filing's currency; the facts' own precision is the checks' tolerance."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from ...checks.coded import tagged_checks
from ...domain.model import Reading
from ...domain.periods import Period, label_period

_MIN_LINES = 5  # a period a statement shows on fewer lines is a note's figure, not the statement's column


@dataclass(frozen=True)
class Fact:
    name: str  # the IFRS element without "ifrs-full_"
    start: date | None
    end: date
    value: float
    currency: str | None
    decimals: int | None


# The element that marks each statement's first layout (ifrs_roles.ROLES): a gross profit is an income statement by
# function, current assets a balance sheet by current and non-current, adjustments to profit an indirect cash flow.
# A filing's notes tag expenses by nature too (SQM's), so counting facts alone picks the wrong layout.
_FIRST_LAYOUT = {"balance": ("CurrentAssets", "NoncurrentAssets"), "income": ("GrossProfit", "CostOfSales"),
                 "cash_flow": ("AdjustmentsForReconcileProfitLoss",)}


def _best_role(statement: str, roles: list[list[tuple[str, int]]], names: set[str]) -> list[tuple[str, int]]:
    if not roles:
        return []
    if len(roles) > 1 and any(name in names for name in _FIRST_LAYOUT.get(statement, ())):
        return roles[0]
    return max(roles, key=lambda role: sum(1 for name, _depth in role if name in names))


def tagged_readings(facts: list[Fact], roles: dict[str, list], english: dict[str, str], *, accession: str, form: str,
                    filed: date, document_end: date, fiscal_year_end: str, year_offset: int, source: str,
                    scope: str = "consolidated") -> list[Reading]:
    by_period: dict[tuple, dict[str, Fact]] = defaultdict(dict)
    for fact in facts:
        by_period[(fact.start, fact.end)][fact.name] = fact
    instants = {end: values for (start, end), values in by_period.items() if start is None}
    readings = []
    for statement, options in roles.items():
        role = _best_role(statement, options, {fact.name for fact in facts})
        names = [name for name, _depth in role]
        for (start, end), values in by_period.items():
            if (start is None) != (statement == "balance") or sum(1 for name in names if name in values) < _MIN_LINES:
                continue
            labelled = label_period(Period(end, start), fiscal_year_end, year_offset)
            if labelled is None:
                continue
            lines, tagged = [], {}
            opening = instants.get(start - timedelta(days=1), {}) if start else {}
            for name, depth in role:
                fact = values.get(name)
                if statement == "cash_flow" and name == "CashAndCashEquivalents":
                    # Opening and closing cash, the role's one element at the period's start and end.
                    for kind, found in (("AtBeginningOfPeriodCf", opening.get(name)), ("AtEndOfPeriodCf", instants.get(end, {}).get(name))):
                        if found is not None:
                            tagged["CashAndCashEquivalents" + kind] = found.value
                            lines.append(_line(len(lines), name, depth, found.value, english, kind))
                    continue
                if fact is None or name in tagged:
                    continue
                tagged[name] = fact.value
                lines.append(_line(len(lines), name, depth, fact.value, english))
            sample = next(iter(values.values()))
            unit = 10.0 ** -sample.decimals if sample.decimals is not None and sample.decimals < 0 else 1.0
            readings.append(Reading(
                statement=statement, duration=labelled[2], fiscal_year=labelled[0], fiscal_period=labelled[1],
                period_end=end, period_start=start, currency=sample.currency, accession=accession, form=form,
                filed=filed, own=end == document_end, source=source, basis="IFRS", scope=scope, lines=lines,
                checks=tagged_checks(statement, tagged, unit, tuple(line["value"] for line in lines))))
    return readings


def _line(index: int, name: str, depth: int, value: float, english: dict[str, str], kind: str = "") -> dict:
    label = english.get(name, name)
    if kind:
        label += ", at the beginning of the period" if kind == "AtBeginningOfPeriodCf" else ", at the end of the period"
    return {"line": index, "concept": f"ifrs-full_{name}", "code": f"ifrs-full_{name}", "label": label,
            "label_en": english.get(name), "level": min(20, depth), "is_heading": False, "value": value}
