"""A filing's XBRL facts as the readers need them: which figures are tagged for the company as a whole, and
which currency the filing reports in."""
from __future__ import annotations

import re

import math


def number(raw) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) or math.isinf(value) else value


def decimals(raw) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None  # "INF" or not given: exact as reported


def load_facts(xbrl):
    try:
        facts = xbrl.facts.to_dataframe()
    except Exception:  # noqa: BLE001 - without facts the statement's own figures stand
        return None
    return None if facts is None or getattr(facts, "empty", True) else facts


def consolidated_figures(facts) -> set[tuple[str, str]]:
    """(concept, period) of every figure tagged for the company as a whole. A line whose figure for a period exists only
    for a part of it (JPM's memo of consolidated VIEs' assets and liabilities, "included in the line items above") is
    not a line of that period's statement: counted as one, it is counted twice."""
    if facts is None or "is_dimensioned" not in facts or "period_key" not in facts:
        return set()
    plain = facts[facts["is_dimensioned"] != True]  # noqa: E712 - pandas mask
    # Period keys as the rows carry them, without the line breaks some filings' keys hold (Omega's 10-Ks of 2015-18).
    return {(str(concept).replace(":", "_", 1), re.sub(r"\s+", "", str(key)))
            for concept, key in zip(plain["concept"], plain["period_key"])}


class ReportingCurrency:
    """A filer may tag a line twice for one period: in its own currency and as a convenience translation (TSMC's 20-F
    gives the latest year in US dollars as well). Each line takes the figure in the currency the filing reports in."""

    def __init__(self, facts):
        self.currency: str | None = None
        self.facts: dict[tuple[str, str], list[tuple[str, float, str]]] = {}
        if facts is None or "currency" not in facts:
            return
        if "is_dimensioned" in facts:
            facts = facts[facts["is_dimensioned"] != True]  # noqa: E712 - pandas mask
        monetary = facts[facts["currency"].notna() & ~facts["unit_ref"].astype(str).str.contains("per", case=False)]
        if monetary.empty:
            return
        self.currency = str(monetary["currency"].value_counts().idxmax())
        for row in monetary.to_dict("records"):
            when = row.get("period_end") if isinstance(row.get("period_end"), str) else row.get("period_instant")
            value = number(row.get("numeric_value"))
            if isinstance(when, str) and value is not None:
                self.facts.setdefault((str(row["concept"]), when.strip()[:10]), []).append(
                    (str(row["currency"]), value, re.sub(r"\s+", "", str(row.get("period_key")))))

    def value(self, concept: str, when: str, value: float) -> float:
        candidates = self.facts.get((concept.replace("_", ":", 1), when))
        if not candidates or self.currency is None:
            return value
        shown = next((fact for fact in candidates if fact[1] == value), None)
        if shown is None or shown[0] == self.currency:
            return value
        own = next((fact for fact in candidates if fact[0] == self.currency and fact[2] == shown[2]), None)
        return own[1] if own else value
