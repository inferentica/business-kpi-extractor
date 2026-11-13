"""Opening cash plus the change in cash, and the effects beside it, gives closing cash."""
from __future__ import annotations

import re

from ..domain.model import FAIL, NONE, PASS, Check
from ..domain.printed_labels import (BEFORE_FINANCING, CHANGE, CLOSING, DATED_CASH, EFFECT, EXCHANGE, GROSS_CLOSING, NET_OPENING, OPENING,
                                     SECTION_HEADS, SECTION_KINDS, SECTIONS)
from .match import first_match
from .subtotals import subtotals
from .tolerance import close


# Cash moved into or out of assets held for sale (Brookfield): it adjusts the change like the exchange effect.
_HELD_FOR_SALE = re.compile(r"\bcash\b.*\bheld\s+for\s+sale\b|\bheld\s+for\s+sale\b.*\bcash\b", re.I)


def cash_check(figures) -> Check:
    opening, closing = first_match(figures, OPENING), first_match(figures, CLOSING)
    dated = [(index, value) for index, (label, _level, value) in enumerate(figures) if DATED_CASH.search(label)]
    if (not opening or not closing) and len(dated) >= 2:
        opening, closing = dated[0], dated[-1]
    if not opening or not closing:
        return _frs1(figures) or Check("cash", NONE, "no opening and closing cash")
    # Every beginning balance opens the period (TSMC: cash held for sale at the beginning of the year, then cash),
    # unless one is printed net of the others (Coca-Cola FEMSA's "Initial balance, net").
    net_opening = first_match(figures, NET_OPENING)
    openings = [value for label, _level, value in figures if OPENING.search(label)]
    if net_opening:
        opening = net_opening
    elif len(openings) > 1:
        opening = (opening[0], sum(openings))
    kinds = {match.group(2).lower() for label, _level, _value in figures if (match := SECTION_KINDS.search(label))}  # operat, invest, financ
    kinds |= {match.group(1).lower() for label, _level, _value in figures if (match := SECTION_HEADS.match(label))}
    if len(kinds) < 3:
        return Check("cash", NONE, "not every section of the statement was read: " + ", ".join(sorted(kinds) or ["none"]))
    changes = [value for label, _level, value in figures if CHANGE.search(label) and not OPENING.search(label)
               and not CLOSING.search(label) and not _HELD_FOR_SALE.search(label)]
    effects = [value for label, _level, value in figures if EXCHANGE.search(label) or EFFECT.match(label)
               or _HELD_FOR_SALE.search(label)]
    # Any other line printed between the change in cash and the opening balance adjusts it too (Kroger 2008: "cash
    # from consolidated variable interest entity").
    first_change = next((index for index, (label, _level, _value) in enumerate(figures) if CHANGE.search(label)
                         and not OPENING.search(label) and not CLOSING.search(label)), None)
    labelled = first_match(figures, OPENING)  # never the lines a net opening sums; an opening by its date has no label
    first_opening = min(opening[0], labelled[0]) if labelled else opening[0]
    between = [value for label, _level, value in figures[first_change + 1:first_opening] if value is not None
               and not CHANGE.search(label)] if first_change is not None and first_change < first_opening else []
    if first_change is not None and first_change < first_opening:
        effects += [value for label, _level, value in figures[first_change + 1:first_opening]
                    if value is not None and not (EXCHANGE.search(label) or EFFECT.match(label) or CHANGE.search(label))]
    placed = [(index, value) for index, (label, _level, value) in enumerate(figures)
              if SECTIONS.search(label) and re.search(r"^net|^cash|^total", label, re.I)]
    sections = [value for _index, value in placed]
    # Each change with the printed figures it takes, opening and closing among them: each is exact to half a unit (UBS's
    # Q3 2022 cash flow, 207,875 - 2,382 - 15,788 against 189,707, rejected until round 88).
    candidates = [(change, 3) for change in changes]
    candidates += [(change + effect, 4) for change in changes for effect in effects]
    candidates += [(change + sum(effects), 3 + len(effects)) for change in changes if len(effects) > 1]
    if len(changes) > 1:  # the year's change and its restricted cash's (Coca-Cola FEMSA), with what lies between
        candidates += [(sum(changes) + sum(between), 2 + len(changes) + len(between))]
    if len(sections) >= 3:
        candidates += [(sum(sections[:3]), 5), (sum(sections[:3]) + sum(effects[:1]), 5 + len(effects[:1]))]
        # The effect of exchange rates on cash printed after the sections, not an operating adjustment of the same
        # words before them (Gildan's 2001 "Foreign exchange loss (gain)" among its adjustments to net earnings).
        after = [value for index, (label, _level, value) in enumerate(figures) if index > placed[2][0] and value is not None
                 and (EXCHANGE.search(label) or EFFECT.match(label))]
        candidates += [(sum(sections[:3]) + sum(after), 5 + len(after))]
    closings = [closing[1]] + [value for label, _level, value in figures if GROSS_CLOSING.match(label) and value is not None]
    for change, terms in candidates:
        if any(close(opening[1] + change, end, terms) for end in closings):
            return Check("cash", PASS)
    if not candidates:
        return Check("cash", NONE, "no change in cash")
    return Check("cash", FAIL, f"opening {opening[1]:,.1f} + change {candidates[0][0]:,.1f} vs closing {closing[1]:,.1f}")


_FINANCING = re.compile(r"^net\s+cash\s+(?:inflow|outflow)[^:]*\bfrom\s+financing$", re.I)


def _frs1(figures) -> Check | None:
    """A UK GAAP cash flow statement (FRS 1) closes on the change in cash, its balances in a note: the net cash flow
    before financing sums the operating cash flow and the sections after it, each at its own total, financing sums
    its lines, and the change is the two (Lloyds TSB's half-years to 2004: 4,670 + 5 - 337 - 324 - 1,055 - 7 - 1,311
    = 1,641; 1,641 + 503 = 2,144)."""
    before = next((index for index, (label, _level, value) in enumerate(figures) if value is not None and BEFORE_FINANCING.match(label)), None)
    financing = next((index for index, (label, _level, value) in enumerate(figures) if value is not None and _FINANCING.match(label)), None)
    change = next((index for index, (label, _level, value) in enumerate(figures) if value is not None and CHANGE.search(label)
                   and financing is not None and index > financing), None)
    if None in (before, financing, change) or not before < financing < change:
        return None
    starts = subtotals(figures)
    if before in starts and financing in starts and close(figures[before][2] + figures[financing][2], figures[change][2], 3):
        return Check("cash", PASS)
    return Check("cash", FAIL, f"FRS 1: before financing {figures[before][2]:,.1f} + financing {figures[financing][2]:,.1f} "
                               f"vs change {figures[change][2]:,.1f}")
