"""What a periodic report's figures are reported in: the accounting standard its facts are tagged in, and its reporting
currency. Kept with each filing, so figures stay as reported and are converted only when served."""
from __future__ import annotations

import re
from collections import Counter

from .model import REVENUE_CONCEPTS

_CURRENCY = re.compile(r"^(?:ISO4217:)?([A-Z]{3})$")
STANDARDS = {"us-gaap": "US GAAP", "ifrs-full": "IFRS"}


def report_basis(facts) -> tuple[str | None, str | None]:
    """The report's accounting standard (US GAAP or IFRS, from the taxonomy its facts are tagged in; a company's own
    extension concepts sit beside one of them) and its reporting currency (its revenue's, else its monetary facts'
    most common one). None where the facts do not say."""
    if facts is None or len(facts) == 0 or "concept" not in facts:
        return None, None
    concepts = facts["concept"].astype(str)
    prefixes = set(concepts.str.split(":", n=1).str[0].str.lower())
    standard = next((name for prefix, name in (("ifrs-full", "IFRS"), ("us-gaap", "US GAAP")) if prefix in prefixes), None)
    # edgartools' resolved currency first: a unit's id can be any name (SAP's "Unit_Standard_EUR_Dznin…").
    units = facts["currency"] if "currency" in facts else facts.get("unit_ref")
    if units is None:
        return standard, None

    def most_common(rows) -> str | None:
        found = Counter(match.group(1) for unit in rows if isinstance(unit, str)
                        and (match := _CURRENCY.match(unit.strip().upper())))
        return found.most_common(1)[0][0] if found else None

    return standard, most_common(units[concepts.isin(REVENUE_CONCEPTS)]) or most_common(units)
