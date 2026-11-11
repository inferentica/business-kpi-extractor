"""Whose statement a printed table is: the group's (consolidated) or the company's alone (parent)."""
from __future__ import annotations

import re

from .tables import Table

FOREIGN_FORMS = ("20-F", "40-F", "6-K")

# Results a document titles as the group's: Azul's releases head their statements "Consolidated Financial Results",
# the cash flows then titled "Cash Flow Statement – IFRS" alone.
_GROUP_TITLE = re.compile(r"\bconsolidated\s+(?:financial\s+(?:results|statements|information)|statements?\s+of|"
                          r"balance\s+sheets?|income\s+statements?|cash\s+flows?|results)\b", re.I)


def _title(group: list[Table]) -> str:
    leading = " ".join(row.label for row in group[0].rows[:4] if not row.figures)  # a title in the table's own rows
    return (group[0].context[-300:] + " " + " ".join(" ".join(cell.text for cell in cells) for cells in group[0].header)
            + " " + leading).lower()


def statement_scope(kind: str, group: list[Table], form: str, found: list) -> str:
    """A foreign filer's statement is the company's alone when its title says so ("UNCONSOLIDATED BALANCE SHEET",
    UBS's "(standalone)"), or when it names no scope and the document prints the same statement for the group beside
    it (TSMC's 2008 6-Ks) or titles nothing as the group's (TSMC's parent-only quarterly reports, "TAIWAN
    SEMICONDUCTOR MANUFACTURING COMPANY LTD. BALANCE SHEETS"). A statement naming no scope in a document whose results
    are titled the group's is the group's, in a release printing it once (Azul's). A U.S. filer's is taken as its group's: some print a bare
    "BALANCE SHEETS" though they consolidate."""
    title = _title(group)
    own_rows = [row.label for row in group[0].rows[:4] if not row.figures]
    if re.search(r"\b(unconsolidated|non-consolidated|parent\s+company\s+only)\b", title) or \
            any(re.search(r"\bparent\s+company$", label, re.I) for label in own_rows) or \
            re.search(r"\bstandalone\b", title[-160:]):
        return "parent"  # UBS's "UBS AG (standalone) financial information"
    if form.upper() not in FOREIGN_FORMS or "consolidated" in title:
        return "consolidated"
    if form.upper() != "6-K" or any(other is not group and other_kind == kind for other_kind, other in found):
        return "parent"  # an annual report's, or one of several of its kind (UBS's quarterly reports print UBS AG's own)
    return "consolidated" if any(_GROUP_TITLE.search(_title(other)) for _kind, other in found) else "parent"
