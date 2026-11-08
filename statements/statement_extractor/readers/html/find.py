"""Where a printed filing's statements are: each statement table by the title above it, with the tables a
statement continues into across a page break, and never a summary, a selected-data table or another statement."""
from __future__ import annotations

import re

from ...domain.printed_labels import ASSETS, CLOSING, EQUITY, LIABILITIES_AND_EQUITY, NET_INCOME, PRETAX, SECTIONS
from .tables import Table, tables


TITLES = (
    ("income", re.compile(r"\b(consolidated\s+)?(statements?\s+of\s+(consolidated\s+)?(operations|income|earnings)(\s+and\s+comprehensive\s+income)?|income\s+statements?)\b", re.I)),
    ("balance", re.compile(r"\b(consolidated\s+)?(balance\s+sheets?|statements?\s+of\s+(consolidated\s+)?financial\s+(position|condition))\b", re.I)),
    ("cash_flow", re.compile(r"\b(consolidated\s+)?statements?\s+of\s+(consolidated\s+)?cash\s+flows?\b", re.I)),
)


# Tables that carry a statement's name but are not the company's own statement.
_NOT_STATEMENT = re.compile(r"selected|quarterly|condensed\s+consolidating|consolidating|parent\s+company|registrant|"
                            r"guarantor|pro\s+forma|supplemental|segment|summary|five-year|index\s+to|unaudited\s+quarterly|"
                            r"statements?\s+of\s+\w+\s+data|percentage\s+of|average\s+balance|balance\s+sheet\s+analysis", re.I)


# Statements of the set that are none of the three: a table under one of these titles never continues another.
_OTHER_STATEMENT = re.compile(r"statements?\s+of\s+(changes\s+in\s+)?(consolidated\s+)?((share|stock)(holders|owners)['’]?|"
                              r"shareowners['’]?)\s+equity|changes\s+in\s+equity|statements?\s+of\s+comprehensive\s+income", re.I)


def find_statements(html: str | bytes) -> list[tuple[str, list[Table]]]:
    """Each statement table in a document, with the tables it continues into."""
    all_tables = tables(html)
    found: list[tuple[str, list[Table]]] = []
    for position, table in enumerate(all_tables):
        title_text = table.context[-300:] + " " + " ".join(" ".join(cell.text for cell in cells) for cells in table.header[:2])
        kind = next((name for name, pattern in TITLES if pattern.search(title_text)), None)
        if kind is None or _NOT_STATEMENT.search(title_text[-260:]) or len([r for r in table.rows if r.figures]) < 4:
            continue
        if _is_contents(table):
            continue
        group = [table]
        for later in all_tables[position + 1:position + 3]:
            if _complete(kind, group) or len([r for r in later.rows if r.figures]) < 2:
                break
            later_title = later.context[-200:]
            other = next((name for name, pattern in TITLES if pattern.search(later_title)), None)
            if (other is not None and other != kind or _OTHER_STATEMENT.search(later_title)) and "continued" not in later_title.lower():
                break
            group.append(later)
        if _complete(kind, group) or (kind == "cash_flow" and sum(bool(SECTIONS.search(label))
                                                                   for label in labels_in_context(group)) >= 3):
            found.append((kind, group))
    return found


def _is_contents(table: Table) -> bool:
    labels = " ".join(row.label for row in table.rows).lower()
    return "notes to consolidated financial statements" in labels and len(table.rows) < 15


def labels_in_context(group: list[Table]) -> list[str]:
    """Each line's label, a balance line read under its heading ("CASH AND CASH EQUIVALENTS / Balance at end of year")."""
    labels, heading = [], ""
    for table in group:
        for row in table.rows:
            if not row.figures:
                heading = row.label
                continue
            if re.match(r"^(balance|net\s*\(?(increase|decrease))", row.label, re.I) and "cash" in heading.lower():
                labels.append(f"{heading} {row.label}")
            else:
                labels.append(row.label)
    return labels


def _complete(kind: str, group: list[Table]) -> bool:
    labels = labels_in_context(group)
    if kind == "balance":
        return any(ASSETS.match(label) for label in labels) and any(
            LIABILITIES_AND_EQUITY.match(label) or EQUITY.match(label) for label in labels)
    if kind == "cash_flow":
        return any(CLOSING.search(label) for label in labels)
    return any(NET_INCOME.match(label) or PRETAX.search(label) for label in labels)
