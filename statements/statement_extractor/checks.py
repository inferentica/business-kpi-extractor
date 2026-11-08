"""A statement's own arithmetic: assets equal liabilities and equity, opening cash plus the change equals closing cash,
and every subtotal equals its lines. A statement is kept only when these hold.

Each check works on one period column of reported (unsigned) figures, so a subtotal's lines add with the weights the
filer declared, whatever sign the statement prints them with. A figure is only as exact as it was reported (to the
million, say), so two sums agree when they differ by no more than the rounding of the figures in them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import FAIL, NONE, PASS, Check

_ASSETS = re.compile(r"^(us-gaap|ifrs-full)_Assets$")
_LIABILITIES_AND_EQUITY = re.compile(r"^(us-gaap_LiabilitiesAndStockholdersEquity|ifrs-full_EquityAndLiabilities)$")
_CASH_CHANGE = re.compile(r"PeriodIncreaseDecrease|IncreaseDecreaseInCashAndCashEquivalents|NetIncreaseDecreaseInCash", re.I)
# The exchange-rate line itself ("EffectOfExchangeRateOnCash…"), not a change line named for coming before it.
_EXCHANGE_EFFECT = re.compile(r"_EffectOfExchangeRate", re.I)
_SECTION_FLOWS = re.compile(r"NetCashProvidedByUsedIn(Operating|Investing|Financing)Activities$|"
                            r"CashFlowsFrom(?:UsedIn)?(Operating|Investing|Financing)Activities$")


@dataclass
class Figure:
    value: float
    decimals: int | None  # None: exact as given


@dataclass
class Column:
    """One period of one statement: each concept's reported figure, and its opening and closing cash if it has them."""
    figures: dict[str, Figure] = field(default_factory=dict)
    blank: set[str] = field(default_factory=set)  # lines the statement presents with no figure for this period
    opening: Figure | None = None
    closing: Figure | None = None


def rounding(*figures: Figure) -> float:
    """How far a sum of these figures can be from exact: half a unit of each figure's own rounding."""
    total = 0.0
    for figure in figures:
        total += 0.5 * 10 ** (-figure.decimals) if figure.decimals is not None else 0.0
    return max(total, 0.5)


def _agree(left: float, right: float, *figures: Figure) -> bool:
    return abs(left - right) <= rounding(*figures) + 1e-9 * max(abs(left), abs(right))


def _find(column: Column, pattern: re.Pattern) -> list[tuple[str, Figure]]:
    return [(concept, figure) for concept, figure in column.figures.items() if pattern.search(concept)]


def balance_check(column: Column) -> Check:
    assets = _find(column, _ASSETS)
    total = _find(column, _LIABILITIES_AND_EQUITY)
    if not assets or not total:
        return Check("balance", NONE, "no total assets and total liabilities and equity")
    (_, left), (_, right) = assets[0], total[0]
    if _agree(left.value, right.value, left, right):
        return Check("balance", PASS)
    return Check("balance", FAIL, f"assets {left.value:,.0f} vs liabilities and equity {right.value:,.0f}")


def cash_check(column: Column) -> Check:
    """Opening cash plus the period's change (with or without the exchange-rate effect, whichever the filer's change
    line leaves out) equals closing cash. A statement with no change line is checked on its three sections instead."""
    if column.opening is None or column.closing is None:
        return Check("cash", NONE, "no opening and closing cash")
    opening, closing = column.opening, column.closing
    effects = [figure for _, figure in _find(column, _EXCHANGE_EFFECT)]
    changes = [figure for concept, figure in _find(column, _CASH_CHANGE) if not _EXCHANGE_EFFECT.search(concept)]
    candidates: list[tuple[float, list[Figure]]] = []
    for change in changes:
        candidates.append((change.value, [change]))
        for effect in effects:
            candidates.append((change.value + effect.value, [change, effect]))
    if not changes:
        sections = [figure for _, figure in _find(column, _SECTION_FLOWS)]
        if len(sections) == 3:
            flows = sum(figure.value for figure in sections)
            candidates.append((flows, sections))
            for effect in effects:
                candidates.append((flows + effect.value, [*sections, effect]))
    if not candidates:
        return Check("cash", NONE, "no change in cash")
    for change, parts in candidates:
        if _agree(opening.value + change, closing.value, opening, closing, *parts):
            return Check("cash", PASS)
    return Check("cash", FAIL, f"opening {opening.value:,.0f} + change {candidates[0][0]:,.0f} vs closing {closing.value:,.0f}")


def calc_check(column: Column, arcs: dict[str, list[tuple[str, float]]]) -> Check:
    """Every subtotal the filer declared (its calculation relationships) that this column can test: the parent and at
    least one of its lines are presented with figures; a presented line without a figure counts as nothing."""
    verified, failures = 0, []
    for parent, children in arcs.items():
        total = column.figures.get(parent)
        present = [(column.figures[child], weight) for child, weight in children if child in column.figures]
        if total is None or not present:
            continue
        if len(present) < len(children) and not all(child in column.figures or child in column.blank
                                                     for child, _ in children):
            continue  # a line outside this statement (a note's figure): the sum cannot be tested here
        computed = sum(figure.value * weight for figure, weight in present)
        verified += 1
        if not _agree(total.value, computed, total, *(figure for figure, _ in present)):
            failures.append(f"{parent.split('_', 1)[-1]} {total.value:,.0f} vs lines {computed:,.0f}")
    if failures:
        return Check("calc", FAIL, "; ".join(failures[:3]) + (f" (+{len(failures) - 3})" if len(failures) > 3 else ""))
    if verified == 0:
        return Check("calc", NONE, "no subtotal to test")
    return Check("calc", PASS, f"{verified} subtotals")


def checks_for(statement: str, column: Column, arcs: dict[str, list[tuple[str, float]]]) -> list[Check]:
    found = [calc_check(column, arcs)]
    if statement == "balance":
        found.insert(0, balance_check(column))
    elif statement == "cash_flow":
        found.insert(0, cash_check(column))
    return found
