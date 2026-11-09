"""Checks across statements: a release's cash flow statement often prints no opening or closing cash (ASML's quarterly
releases), so its change in cash is proven by the balance sheets instead. The change must equal the balance sheet's cash
at the period's end less its cash at the period's start, both read from balance sheets whose own arithmetic holds,
of the same filing first, else any other."""
from __future__ import annotations

import re
from datetime import timedelta

from ..domain.model import NONE, PASS, Check, Reading
from ..domain.printed_labels import CHANGE, EXCHANGE

# A balance sheet's cash line: "Cash and cash equivalents", "Cash".
_CASH = re.compile(r"^cash(\s+and\s+(cash\s+equivalents|short-term\s+investments|deposits))?\s*(\(note\s*\d+\))?$", re.I)
_SLACK = timedelta(days=7)


def _cash(balance: Reading) -> float | None:
    return next((line["value"] for line in balance.lines if not line["is_heading"] and line["value"] is not None
                 and _CASH.match(line["label"])), None)


def _change(flows: Reading) -> float | None:
    """The change in cash, net of exchange effects where the statement prints it after them."""
    return next((line["value"] for line in reversed(flows.lines) if not line["is_heading"] and line["value"] is not None
                 and CHANGE.search(line["label"]) and not EXCHANGE.search(line["label"])), None)


def _unit(values: list[float]) -> float:
    for unit in (1e6, 1e5, 1e4, 1e3, 1e2, 10.0, 1.0):
        if all(abs(value / unit - round(value / unit)) < 1e-6 for value in values):
            return unit
    return 1.0


def _balance_at(day, balances: list[Reading], accession: str) -> Reading | None:
    near = [balance for balance in balances if abs(balance.period_end - day) <= _SLACK and _cash(balance) is not None]
    near.sort(key=lambda balance: (balance.accession != accession, -balance.filed.toordinal()))
    return near[0] if near else None


def cash_against_balances(readings: list[Reading]) -> int:
    """Proves each cash flow statement that has nothing else to prove it by the balance sheets' cash; returns how many
    it proved."""
    balances = [reading for reading in readings if reading.statement == "balance" and reading.status == "kept"]
    proved = 0
    for flows in readings:
        if flows.statement != "cash_flow" or flows.period_start is None:
            continue
        cash_check = next((check for check in flows.checks if check.name == "cash"), None)
        if cash_check is None or cash_check.result != NONE:
            continue
        change = _change(flows)
        end = _balance_at(flows.period_end, balances, flows.accession)
        start = _balance_at(flows.period_start - timedelta(days=1), balances, flows.accession)
        if change is None or end is None or start is None or end is start:
            continue
        moved = _cash(end) - _cash(start)
        if abs(moved - change) <= 2 * _unit([change, _cash(end), _cash(start)]):
            flows.checks[flows.checks.index(cash_check)] = Check(
                "cash", PASS, f"change in cash equals the balance sheets' ({start.accession} to {end.accession})")
            proved += 1
    return proved
