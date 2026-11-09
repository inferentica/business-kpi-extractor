"""Statements that must tie to one another, checked on the chosen statements: the cash flow statement's closing cash
is the balance sheet's cash on the same date, and a year's four quarters add up to the year. A statement can add up on
its own and still be another period's or another table's; a second statement of the same company and period is an
independent witness. These are verdicts on record beside each statement, not reasons to reject it: definitions differ
(cash flows count restricted cash since 2018; a bank's cash is cash and due from banks plus deposits with banks)."""
from __future__ import annotations

import re
from itertools import combinations

from .figures import agree, key_figures

# Balance sheet lines that make up the cash a cash flow statement closes with.
_CASH_PARTS = re.compile(r"\bcash\b|deposits\s+with\s+banks|restricted|_Cash|_RestrictedCash|InterestBearingDepositsInBanks",
                         re.I)


def _cash_parts(lines: list[dict]) -> list[float]:
    return [line["value"] for line in lines if not line["is_heading"] and line["value"] is not None and not line.get("member")
            and (_CASH_PARTS.search(line["label"]) or _CASH_PARTS.search(line["concept"]))][:8]


def cash_on_balance(flows: dict, balance: dict | None) -> str:
    """agree: closing cash is a cash line of the balance sheet, or two or three of them together; differ; untested."""
    closing = key_figures("cash_flow", flows["lines"]).get("closing cash")
    if closing is None or balance is None:
        return "untested"
    parts = _cash_parts(balance["lines"])
    closing_line = next((line for line in reversed(flows["lines"]) if not line["is_heading"] and line["value"] == closing),
                        None)
    restricted = closing_line is not None and bool(re.search(r"restricted", closing_line["label"] + closing_line["concept"], re.I))
    if restricted and not any(re.search(r"restricted", line["label"] + line["concept"], re.I) for line in balance["lines"]):
        return "untested"  # cash flows with restricted cash, which the balance sheet keeps among other assets (Apple)
    for size in (1, 2, 3):
        if any(agree(sum(group), closing) for group in combinations(parts, size)):
            return "agree"
    return "differ" if parts else "untested"


def quarters_to_year(quarters: list[dict], year: dict) -> str:
    """add up: the four quarters' net income and revenue sum to the year's; differ: by how much; untested."""
    names = ("net income", "revenue")
    figures = [key_figures("income", record["lines"]) for record in quarters]
    annual = key_figures("income", year["lines"])
    shared = [name for name in names if name in annual and all(name in quarter for quarter in figures)]
    if len(quarters) != 4 or not shared:
        return "untested"
    gaps = []
    for name in shared:
        total = sum(quarter[name] for quarter in figures)
        tolerance = max(4 * abs(total) * 1e-4, 4.0)  # four roundings
        if abs(total - annual[name]) > tolerance:
            gaps.append(f"{name} {100 * (total - annual[name]) / annual[name]:+.2f}%" if annual[name] else name)
    return "add up" if not gaps else "differ: " + ", ".join(gaps)


def tie(chosen: list[dict], readings: list) -> None:
    """Writes each verdict on the chosen record it concerns. Closing cash is checked against the balance sheet of the
    same filing, so both are one vintage and one scope (ASML's FY2001 20-F restated 1999 for a merger its 2000 report
    had not seen); quarters against a year of the same scope and basis (TSMC's quarters to 2004 are the parent's)."""
    kept = {(item["statement"], item["fiscal_year"], item["fiscal_period"], item["duration"]): item
            for item in chosen if item["status"] == "kept"}
    balances: dict[tuple, list[dict]] = {}
    for reading in readings:
        if reading.statement == "balance" and reading.status == "kept":
            balances.setdefault((reading.period_end.isoformat(), reading.scope, reading.basis), []).append(
                {"lines": reading.lines, "accession": reading.accession})
    for item in kept.values():
        if item["statement"] == "cash_flow":
            candidates = balances.get((item["period_end"], item.get("scope"), item.get("basis")), [])
            # The same filing's balance sheet first; any other's on that date and basis can agree too.
            candidates.sort(key=lambda balance: balance["accession"] != item["source_accession"])
            verdicts = [cash_on_balance(item, balance) for balance in candidates]
            item["cash_on_balance"] = "agree" if "agree" in verdicts else "differ" if "differ" in verdicts else "untested"
        if item["statement"] == "income" and item["fiscal_period"] == "FY":
            quarters = [kept.get(("income", item["fiscal_year"], f"Q{number}", "quarter")) for number in range(1, 5)]
            if all(quarters) and all((q.get("scope"), q.get("basis")) == (item.get("scope"), item.get("basis"))
                                     for q in quarters):
                item["quarters"] = quarters_to_year(quarters, item)
