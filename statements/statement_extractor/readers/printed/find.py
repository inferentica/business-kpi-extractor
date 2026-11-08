"""Where a printed filing's statements are: each statement table by the title above it, with the tables a
statement continues into across a page break, and never a summary, a selected-data table or another statement."""
from __future__ import annotations

import re

from ...domain.printed_labels import (ASSETS, BARE_TOTAL, CASH_HEADING, CLOSING, DATED_CASH, EQUITY, LIABILITIES_AND_EQUITY, NET_INCOME,
                                      PRETAX, SECTIONS)
from .tables import Table, figure_columns


TITLES = (
    ("income", re.compile(r"\b(consolidated\s+)?(statements?\s+of\s+(consolidated\s+)?(operations|income|earnings)(\s+and\s+comprehensive\s+income)?|(?<!comprehensive )income\s+statements?|"
                          r"statements?\s+of\s+(consolidated\s+)?profit\s+or\s+loss(\s+and\s+(other\s+)?comprehensive\s+income)?)\b", re.I)),
    ("balance", re.compile(r"\b(consolidated\s+)?(balance\s+sheets?|statements?\s+of\s+(consolidated\s+)?financial\s+(position|condition))\b", re.I)),
    # Morgan Stanley: "Consolidated Cash Flow Statements".
    ("cash_flow", re.compile(r"\b(consolidated\s+)?(statements?\s+of\s+(consolidated\s+)?cash\s+flows?|cash\s+flows?\s+statements?)\b", re.I)),
)


# Tables that carry a statement's name but are not the company's own statement.
_NOT_STATEMENT = re.compile(r"selected|quarterly|condensed\s+consolidating|consolidating|parent\s+company|registrant|"
                            r"guarantor|pro\s+forma|supplemental|segment|summary|five-year|index\s+to|unaudited\s+quarterly|"
                            r"statements?\s+of\s+\w+\s+data|percentage\s+of|average\s+balance|balance\s+sheet\s+analysis|"
                            r"(income\s+statements?|balance\s+sheets?|cash\s+flows?(\s+statements?)?)\s+data\b", re.I)


# Statements of the set that are none of the three: a table under one of these titles never continues another.
_OTHER_STATEMENT = re.compile(r"statements?\s+of\s+(changes\s+in\s+)?(consolidated\s+)?((share|stock)(holders|owners)['’]?|"
                              r"shareowners['’]?)\s+equity|changes\s+in\s+equity|statements?\s+of\s+comprehensive\s+income|"
                              r"comprehensive\s+income\s+statements?", re.I)


def find_statements(all_tables: list[Table]) -> list[tuple[str, list[Table]]]:
    """Each statement table in a document's tables, with the tables it continues into."""
    found: list[tuple[str, list[Table]]] = []
    for position, table in enumerate(all_tables):
        title_text = table.context[-300:] + " " + " ".join(" ".join(cell.text for cell in cells) for cells in table.header[:2])
        kind = next((name for name, pattern in TITLES if _titled(pattern, title_text)), None)
        if kind is None:
            # A title printed as the table's own first rows, above its figures (PepsiCo's 2013 balance sheet), when
            # nothing before the table names it.
            leading = " ".join(row.label for row in _leading(table))
            kind = next((name for name, pattern in TITLES if _titled(pattern, leading)), None)
            title_text += " " + leading
        if kind is None or _NOT_STATEMENT.search(title_text[-260:]) or len([r for r in table.rows if r.figures]) < 4:
            continue
        if _is_contents(table):
            continue
        group = [table]
        for later in all_tables[position + 1:position + 6]:
            if _complete(kind, group):
                break
            if len([r for r in later.rows if r.figures]) < 2:
                if later.rows and any(r.figures for r in later.rows):
                    break  # a small table of its own, not a spacer
                continue  # an empty spacer table between a statement's pages (TSMC's cash flows)
            later_title = later.context[-200:]
            if len(figure_columns(later)) != len(figure_columns(table)):
                break  # a table of another shape: not this statement's next page
            other = next((name for name, pattern in TITLES if _titled(pattern, later_title)), None)
            if (other is not None and other != kind or _OTHER_STATEMENT.search(later_title)) and "continued" not in later_title.lower():
                break
            group.append(later)
        if _complete(kind, group) or (kind == "cash_flow" and sum(bool(SECTIONS.search(label))
                                                                   for label in labels_in_context(group)) >= 3):
            found.append((kind, group))
    return found


def _leading(table: Table) -> list:
    leading = []
    for row in table.rows:
        if row.figures:
            break
        leading.append(row)
    return leading[:4]


# Words that put a statement's name inside a sentence ("as reported on the Consolidated Statement of Income").
_SENTENCE_WORDS = {"the", "on", "in", "of", "our", "its", "their", "to", "from", "see", "with", "by", "and", "these", "this",
                   "those", "such", "accompanying", "following", "into", "within", "per", "under", "a", "an", "related"}


def _titled(pattern: re.Pattern, text: str) -> bool:
    """Whether a statement's name stands as a heading, not inside a sentence: "J.P. MORGAN CHASE & CO. CONSOLIDATED
    STATEMENT OF INCOME" is a title, "as reported on the Consolidated Statement of Income." is not."""
    for match in pattern.finditer(text):
        words = re.findall(r"[A-Za-z]+", text[max(0, match.start() - 40):match.start()])
        if not words or words[-1] not in _SENTENCE_WORDS:
            return True
    return False


def _is_contents(table: Table) -> bool:
    labels = " ".join(row.label for row in table.rows).lower()
    return "notes to consolidated financial statements" in labels and len(table.rows) < 15


def unlabelled_totals(group: list[Table]) -> dict[int, str]:
    """What a balance sheet's unlabelled grand totals are (Apple's 1994 balance sheet prints both without a label): the
    figure line just before the liabilities heading is total assets, and the statement's last figure line, when it
    equals it, is total liabilities and equity. Keyed by the row's id."""
    rows = [row for table in group for row in table.rows]
    # A page that opens with the liabilities in its title (Apple's 1998 10-Q: "BALANCE SHEETS (Continued) LIABILITIES
    # AND SHAREHOLDERS' EQUITY") closes the assets on the page before it.
    page_ends = {id(next((row for row in reversed(table.rows) if row.figures), None))
                 for table, later in zip(group, group[1:]) if re.search(r"\bliabilities\b", later.context[-160:], re.I)}
    named: dict[int, str] = {}
    assets = None
    for index, row in enumerate(rows):
        if row.label or not row.figures:
            continue
        following = next((later for later in rows[index + 1:] if later.label or later.figures), None)
        heading_next = following is not None and not following.figures and re.match(r"^liabilities\b", following.label, re.I)
        if heading_next or id(row) in page_ends:
            named[id(row)], assets = "Total assets", row
    figured = [row for row in rows if row.figures]
    if assets is not None and figured and not figured[-1].label and figured[-1] is not assets \
            and sorted(figured[-1].figures.values()) == sorted(assets.figures.values()):  # pages can sit a column apart
        named[id(figured[-1])] = "Total liabilities and equity"
    return named


def labels_in_context(group: list[Table], kind: str = "") -> list[str]:
    """Each line's label as the checks read it: a balance line under its heading ("CASH AND CASH EQUIVALENTS / Balance
    at end of year"), and a balance sheet's unlabelled grand totals by their place."""
    labels, heading = [], ""
    named = unlabelled_totals(group) if kind == "balance" else {}
    for table in group:
        for row in table.rows:
            if not row.figures:
                heading = row.label
                continue
            if id(row) in named:
                labels.append(named[id(row)])
            elif re.match(r"^(balance|net\s*\(?(increase|decrease))", row.label, re.I) and CASH_HEADING.match(heading):
                labels.append(f"{heading} {row.label}")
            else:
                labels.append(row.label)
    return labels


def _complete(kind: str, group: list[Table]) -> bool:
    labels = labels_in_context(group, kind)
    if kind == "balance":
        bare = sum(1 for label in labels if BARE_TOTAL.match(label))
        return (any(ASSETS.match(label) for label in labels) or bare >= 1) and (
            any(LIABILITIES_AND_EQUITY.match(label) or EQUITY.match(label) for label in labels) or bare >= 2)
    if kind == "cash_flow":
        return any(CLOSING.search(label) for label in labels) or sum(1 for label in labels if DATED_CASH.search(label)) >= 2
    return any(NET_INCOME.match(label) or PRETAX.search(label) for label in labels)
