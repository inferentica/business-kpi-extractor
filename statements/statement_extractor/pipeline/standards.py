"""The standards' own titles and key lines put to use: the IFRS and US GAAP taxonomies' labels widen the finder's
titles and the checks' key lines once per run (domain/standard_labels.py)."""
from __future__ import annotations

from ..domain import printed_labels
from ..domain.standard_labels import load
from ..readers.printed.find import TITLES

_DONE: list[bool] = []


def use_standard_labels(labels: dict | None = None) -> None:
    """Widen the patterns with the taxonomies' labels (fetched once; `labels` given in tests)."""
    if _DONE and labels is None:
        return
    labels = load() if labels is None else labels
    for key, pattern in (("pretax", printed_labels.PRETAX), ("net_income", printed_labels.NET_INCOME),
                         ("assets", printed_labels.ASSETS), ("liabilities_and_equity", printed_labels.LIABILITIES_AND_EQUITY),
                         ("cash:periodStartLabel", printed_labels.OPENING), ("cash:periodEndLabel", printed_labels.CLOSING)):
        pattern.widen(labels.get(key))
    for kind, pattern in TITLES:
        pattern.widen(labels.get(f"title:{kind}"), whole=False)
    _DONE.append(True)
