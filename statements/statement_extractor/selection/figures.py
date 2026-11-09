"""A statement's key figures, the ones two presentations of a period are compared on: net income and revenue, total
assets, closing cash. Each is found by its XBRL concept or its printed label, so the same figure pairs across eras."""
from __future__ import annotations

import re

from ..domain.printed_labels import ASSETS, BARE_TOTAL, CLOSING, NET_INCOME

_REVENUE = re.compile(r"^(total\s+)?(net\s+)?(sales|revenues?|net\s+revenues?)\b|^total\s+net\s+(sales|revenues?)\b", re.I)
_CONCEPTS = {
    "net income": re.compile(r"_(NetIncomeLoss|ProfitLoss|ProfitLossAttributableToOwnersOfParent|NetIncomeLossAvailableToCommonStockholdersBasic)$"),
    "revenue": re.compile(r"_(Revenues|RevenueFromContractWithCustomerExcludingAssessedTax|SalesRevenueNet|Revenue)$"),
    "total assets": re.compile(r"_Assets$"),
    "closing cash": re.compile(r"_(CashAndCashEquivalentsAtCarryingValue|CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents|CashAndCashEquivalents|Cash)$"),
}
_KINDS = {"income": ("net income", "revenue"), "balance": ("total assets",), "cash_flow": ("closing cash",)}


def key_figures(statement: str, lines: list[dict]) -> dict[str, float]:
    """The statement's key figures that its lines show."""
    figures = [line for line in lines if not line["is_heading"] and line["value"] is not None and not line.get("member")]
    found: dict[str, float] = {}
    for name in _KINDS.get(statement, ()):
        by_concept = [line for line in figures if _CONCEPTS[name].search(line["concept"])]
        if name == "net income":
            by_label = [line for line in figures if NET_INCOME.match(line["label"])]
        elif name == "revenue":
            by_label = [line for line in figures if _REVENUE.match(line["label"])][:1]
        elif name == "total assets":
            by_label = [line for line in figures if ASSETS.match(line["label"])] or \
                [line for line in figures if BARE_TOTAL.match(line["label"])][:1]
        else:
            by_label = [line for line in figures if CLOSING.search(line["label"])]
        chosen = by_concept or by_label
        if chosen:
            # The last net income and closing cash (after the parent's share and the cash reconciliations), the first
            # revenue and total assets.
            found[name] = (chosen[-1] if name in ("net income", "closing cash") else chosen[0])["value"]
    return found


def _unit(value: float) -> float:
    for unit in (1e6, 1e5, 1e4, 1e3, 1e2, 10.0, 1.0):
        if abs(value / unit - round(value / unit)) < 1e-6:
            return unit
    return 0.01


def agree(left: float, right: float) -> bool:
    """Two printings of one figure, each exact to its own printed precision (Apple's 1993 net income: 86,589
    thousand in its own 10-K, 87 million two years on)."""
    return abs(left - right) <= max(_unit(left), _unit(right), 1.0)


def comparable(left: dict[str, float], right: dict[str, float]) -> list[str]:
    return [name for name in left if name in right]
