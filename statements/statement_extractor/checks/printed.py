"""A printed statement's own arithmetic, on its figures as printed (brackets for negatives): assets equal
liabilities and equity, opening cash plus the change equals closing cash, net income follows from income before
taxes, and each printed total equals its lines."""
from __future__ import annotations

import re

from ..domain.model import FAIL, NONE, PASS, Check
from ..domain.printed_labels import (ASSETS, BARE_TOTAL, CHANGE, CLOSING, DATED_CASH, EFFECT, EQUITY, EXCHANGE, LIABILITIES,
                                     LIABILITIES_AND_EQUITY, NET_INCOME, OPENING, PRETAX, SECTION_KINDS, SECTIONS, TOTAL)
from .subtotals import subtotals
from .tolerance import close


def first_match(figures, pattern, after: int = -1) -> tuple[int, float] | None:
    for index, (label, _level, value) in enumerate(figures):
        if index > after and pattern.search(label):
            return index, value
    return None


def balance_check(figures) -> Check:
    assets = first_match(figures, ASSETS)
    total = first_match(figures, LIABILITIES_AND_EQUITY)
    bare = [(index, value) for index, (label, _level, value) in enumerate(figures) if BARE_TOTAL.match(label)]
    if len(bare) >= 2:  # TSMC closes each side with a bare "TOTAL"
        assets, total = assets or bare[0], total or bare[-1]
    if assets and total:
        return Check("balance", PASS if close(assets[1], total[1]) else FAIL,
                     "" if close(assets[1], total[1]) else f"assets {assets[1]:,.1f} vs liabilities and equity {total[1]:,.1f}")
    liabilities, equity = first_match(figures, LIABILITIES), first_match(figures, EQUITY)
    if assets and liabilities and equity:
        between = [value for index, (_l, _v, value) in enumerate(figures) if liabilities[0] < index < equity[0]]
        if close(assets[1], liabilities[1] + equity[1]) or close(assets[1], liabilities[1] + equity[1] + sum(between[-1:])):
            return Check("balance", PASS)
        return Check("balance", FAIL, f"assets {assets[1]:,.1f} vs liabilities {liabilities[1]:,.1f} + equity {equity[1]:,.1f}")
    return Check("balance", NONE, "no total assets and total liabilities and equity")


def cash_check(figures) -> Check:
    opening, closing = first_match(figures, OPENING), first_match(figures, CLOSING)
    dated = [(index, value) for index, (label, _level, value) in enumerate(figures) if DATED_CASH.search(label)]
    if (not opening or not closing) and len(dated) >= 2:
        opening, closing = dated[0], dated[-1]
    if not opening or not closing:
        return Check("cash", NONE, "no opening and closing cash")
    # Every beginning balance opens the period (TSMC: cash held for sale at the beginning of the year, then cash).
    openings = [value for label, _level, value in figures if OPENING.search(label)]
    if len(openings) > 1:
        opening = (opening[0], sum(openings))
    kinds = {match.group(2).lower() for label, _level, _value in figures if (match := SECTION_KINDS.search(label))}
    if len(kinds) < 3:
        return Check("cash", NONE, "not every section of the statement was read: " + ", ".join(sorted(kinds) or ["none"]))
    changes = [value for label, _level, value in figures if CHANGE.search(label) and not OPENING.search(label)
               and not CLOSING.search(label)]
    effects = [value for label, _level, value in figures if EXCHANGE.search(label) or EFFECT.match(label)]
    sections = [value for label, _level, value in figures if SECTIONS.search(label) and re.search(r"^net|^cash|^total", label, re.I)]
    candidates = list(changes)
    candidates += [change + effect for change in changes for effect in effects]
    candidates += [change + sum(effects) for change in changes if len(effects) > 1]
    if len(sections) >= 3:
        candidates += [sum(sections[:3]), sum(sections[:3]) + sum(effects[:1])]
    for change in candidates:
        if close(opening[1] + change, closing[1], 3):
            return Check("cash", PASS)
    if not candidates:
        return Check("cash", NONE, "no change in cash")
    return Check("cash", FAIL, f"opening {opening[1]:,.1f} + change {candidates[0]:,.1f} vs closing {closing[1]:,.1f}")


def income_check(figures) -> Check:
    """Net income follows from income before taxes through the lines between them, each added or taken away as the
    statement means it (a printed statement shows a tax charge as a plain figure and takes it away). Lines a subtotal
    between them sums are read as that subtotal (Prudential's current and deferred tax under total income taxes)."""
    pretax = first_match(figures, PRETAX)
    net = first_match(figures, NET_INCOME, after=pretax[0] if pretax else -1)
    if not pretax or not net:
        return Check("calc", NONE, "no income before taxes and net income")
    between = figures[pretax[0] + 1:net[0]]
    starts = subtotals(between)
    covered = {position for end, start in starts.items() for position in range(start, end)}
    # Lines a later subtotal sums are read through that subtotal, or each on its own: a running subtotal (income from
    # continuing operations) can also equal the two lines above it, and must then restart the chain instead.
    for skip in (covered, set()):
        if _chain(pretax[1], net[1], [value for index, (_l, _v, value) in enumerate(between) if index not in skip]):
            return Check("calc", PASS, "net income from income before taxes")
    return Check("calc", FAIL, f"income before taxes {pretax[1]:,.1f} does not lead to net income {net[1]:,.1f}")


def _chain(start: float, end: float, values: list[float]) -> bool:
    reachable = {start}
    for value in values:
        if any(close(value, running, len(values) + 1) for running in reachable):
            reachable = {value}  # a running subtotal
            continue
        reachable = {running + sign * value for running in reachable for sign in (1, -1)}
        if len(reachable) > 512:
            return False
    return any(close(running, end, len(values) + 2) for running in reachable)


def total_check(printed) -> Check:
    """Each printed "Total …" equals lines just above it, read upward: an earlier subtotal counts as one line for the
    lines it sums, together with the headings over them ("Total assets" = total current assets + the lines after it),
    and a line headed "Less" is taken away. A total with fewer than two lines above it is not tested."""
    verified, failures = 0, []
    starts = subtotals(printed)
    for index, (label, _level, value) in enumerate(printed):
        if value is None or not TOTAL.match(label) or LIABILITIES_AND_EQUITY.match(label) or BARE_TOTAL.match(label):
            continue  # a grand total is proven by the balance identity
        if index in starts:
            verified += 1  # proven as a sum or a difference of the lines above it (JPM's total net revenue)
            continue
        running, count, position, matched = 0.0, 0, index - 1, False
        while position >= 0 and count < 40:
            earlier_label, _earlier_level, earlier_value = printed[position]
            if earlier_value is None:
                if count == 0:
                    break  # the first line under its heading (a supplemental block's total): nothing above it to sum
                position -= 1
                continue  # a heading: a total can sum sections under several headings
            running += -earlier_value if re.match(r"^less\b", earlier_label, re.I) else earlier_value
            count += 1
            if count >= 2 and close(running, value, count + 1):
                matched = True
                break
            position = starts.get(position, position) - 1
        if count < 2:
            continue
        verified += 1
        if not matched:
            failures.append(f"{label[:40]} {value:,.1f}")
    if failures:
        return Check("totals", FAIL, "; ".join(failures[:3]))
    return Check("totals", PASS if verified else NONE, f"{verified} totals")
