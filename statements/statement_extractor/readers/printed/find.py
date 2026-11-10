"""Where a printed filing's statements are: each statement table by the title above it, with the tables a
statement continues into across a page break, and never a summary, a selected-data table or another statement."""
from __future__ import annotations

import re
from dataclasses import replace

from ...domain.printed_labels import (ASSETS, BARE_TOTAL, CASH_HEADING, CLOSING, DATED_CASH, EQUITY, LIABILITIES,
                                      LIABILITIES_AND_EQUITY, NET_INCOME, PRETAX, SECTIONS, EQUITY_SECTION)
from .side_by_side import SIDE_BY_SIDE
from .tables import Table, figure_columns


TITLES = (
    ("income", re.compile(r"\b(consolidated\s+)?(statements?\s+of\s+(consolidated\s+)?(operations|income|earnings)(\s+and\s+comprehensive\s+income)?|(?<!comprehensive )income\s+statements?|"
                          r"statements?\s+of\s+(consolidated\s+)?profit\s+or\s+loss(\s+and\s+(other\s+)?comprehensive\s+income)?|"
                          # UK GAAP (Lloyds TSB to 2004): "CONSOLIDATED PROFIT AND LOSS ACCOUNT".
                          r"profit\s+and\s+loss\s+accounts?)\b", re.I)),
    ("balance", re.compile(r"\b(consolidated\s+)?(balance\s+sheets?|statements?\s+of\s+(consolidated\s+)?financial\s+(position|condition))\b", re.I)),
    # Morgan Stanley: "Consolidated Cash Flow Statements".
    ("cash_flow", re.compile(r"\b(consolidated\s+)?(statements?\s+of\s+(consolidated\s+)?cash\s+flows?|cash\s+flows?\s+statements?)\b", re.I)),
)


# Tables that carry a statement's name but are not the company's own statement.
_NOT_STATEMENT = re.compile(r"selected|summari[sz]ed|\bcomparison\b.{0,120}?\bversus\b|quarterly(?!\s+report)|condensed\s+consolidating|consolidating|parent\s+company|registrant|"
                            r"guarantor|pro\s+forma|supplemental|segment|summary|five-year|index\s+to|unaudited\s+quarterly|"
                            r"statements?\s+of\s+\w+\s+data|percentage\s+of|average\s+balance|balance\s+sheet\s+analysis|"
                            r"(income\s+statements?|balance\s+sheets?|cash\s+flows?(\s+statements?)?)\s+data\b|"
                            # Chase's 1998 reconciliation of reported results to an operating basis, headed "INCOME STATEMENT".
                            r"operating\s+basis|reported\s+results|"
                            # A foreign filer's reconciliation to U.S. GAAP (TSMC: "reconciliation of the significant
                            # balance sheet accounts to the amounts determined under U.S. GAAP"), its rows "As reported".
                            r"reconciliation\s+of|amounts\s+determined\s+under|(under|to|with)\s+u\.?\s?s\.?\s+gaap", re.I)


# "quarterly" names quarterly-data tables, not the report's own running header (Tennant's "Quarterly Report – Form 10-Q"
# atop every page).
_NOT_IN_RELEASE = re.compile(_NOT_STATEMENT.pattern.replace("summary|", "").replace("|quarterly(?!\\s+report)|", "|"), re.I)


_COMPREHENSIVE = re.compile(r"statements?\s+of\s+(consolidated\s+)?comprehensive\s+income", re.I)
TITLE_PATTERNS = [pattern for _kind, pattern in TITLES] + [_COMPREHENSIVE]  # where a title ends, for its dates


# A link printed atop every page back to the index (TSMC's "Index to Financial Statements"), with a title after it: not
# the index itself.
_RUNNING_LINK = re.compile(r"\b(index\s+to\s+(consolidated\s+)?financial\s+statements|table\s+of\s+contents)\b(?=.{40,})", re.I | re.S)


# Column headings that are no period: a statement's columns are periods, a reconciliation's are its steps (Chase's
# 1998 "Reported Results", "Credit Costs", "Special Items", "Operating Basis").
# Or its changes: Lloyds' IFRS 17 transition tables, "As previously reported | Impact of IFRS 17 | IFRS 17 adjustments |
# IFRS 17 2022 movements | Other changes | Restated".
_NOT_A_PERIOD = re.compile(r"^(reported|operating|adjustments?|special|as\s+adjusted)\b|"
                           r"\b(adjustments|movements|impact\s+of|other\s+changes|as\s+previously\s+reported)\b", re.I)


# Statements of the set that are none of the three: a table under one of these titles never continues another.
_OTHER_STATEMENT = re.compile(r"statements?\s+of\s+(changes\s+in\s+)?(consolidated\s+)?((share|stock)(holders|owners)['’]?|"
                              r"shareowners['’]?)\s+equity|changes\s+in\s+equity|statements?\s+of\s+comprehensive\s+income|"
                              r"comprehensive\s+income\s+statements?", re.I)


def find_statements(all_tables: list[Table], release: bool = False) -> list[tuple[str, list[Table]]]:
    """Each statement table in a document's tables, with the tables it continues into. A results release titles its
    statements "Summary Consolidated Statements of Operations" (ASML): there they are the statements."""
    not_statement = _NOT_IN_RELEASE if release else _NOT_STATEMENT
    found: list[tuple[str, list[Table]]] = []
    for position, table in enumerate(all_tables):
        title_text = table.context[-300:] + " " + " ".join(" ".join(cell.text for cell in cells) for cells in table.header[:2])
        kind = next((name for name, pattern in TITLES if _titled(pattern, title_text)), None)
        if kind is None and found and found[-1][1][-1].index == table.index - 1 and not table.context.strip() and \
                _header_words(table) == _header_words(found[-1][1][0]) and _SCOPE_ROW.search(" ".join(r.label for r in _leading(table))):
            # Copel's group statement printed after the parent's, untitled ("CONTINUING OPERATIONS Consolidated"): the
            # same title and scale.
            kind, table = found[-1][0], replace(table, context=found[-1][1][0].context)
            title_text = table.context[-300:] + " " + " ".join(" ".join(cell.text for cell in cells) for cells in table.header[:2])
        if kind is not None and _retitled(kind, title_text):
            kind = None  # an earlier statement's title, then this one's (UBS: "Income statement" … "Statement of comprehensive income")
        if kind is None:
            # A title printed as the table's own first rows, above its figures (PepsiCo's 2013 balance sheet), when
            # nothing before the table names it.
            leading = " ".join(row.label for row in _leading(table))
            kind = next((name for name, pattern in TITLES if _titled(pattern, leading)), None)
            title_text += " " + leading
        # A first row naming the statement is its title, wherever it falls among them: UBS's Pillar 3 report heads a
        # table "Table 33: Reconciliation of accounting balance sheet to balance sheet under the regulatory scope of
        # consolidation", with two rows of text below it before "Assets".
        if kind is not None and not_statement.search(next(
                (row.label for row in _leading(table) if _titled(dict(TITLES)[kind], row.label)), "")):
            continue
        if kind is None and _GENERIC_TITLE.search(table.context[-250:]) and any(
                _EARNINGS_HEAD.match(cell.text) for cells in table.header for cell in cells):
            kind = "income"  # Tennant's 1990s 10-Qs: "CONSOLIDATED STATEMENTS (UNAUDITED)", "EARNINGS (note 1)" over its columns
        # Its profit before tax may be on the statement's second page (KEPCO's "Statements of Comprehensive Income
        # (Loss), Continued").
        pages = [table] + [later for later in all_tables[position + 1:position + 2]
                           if re.search(r"\bcontinued\b", later.context[-200:], re.I) and _COMPREHENSIVE.search(later.context[-300:])]
        if kind is None and (_COMPREHENSIVE.search(title_text[-300:]) or _COMPREHENSIVE.search(table.context[-200:])) and \
                any(row.figures and PRETAX.search(row.label) and _PROFIT_LINE.match(row.label) for page in pages for row in page.rows):
            kind = "income"  # one statement of profit and other comprehensive income (TSMC's Taiwan-IFRS reports)
        # The words before the table count too, though its own first rows extend the title text (UBS's "Comparison UBS
        # Group AG (consolidated) versus UBS AG (consolidated)", then "Income statement" as the table's first row).
        # A parent company's columns beside the group's are no parent-only statement (side_by_side.py).
        title_text = SIDE_BY_SIDE.sub(" ", title_text)
        if kind is None or not_statement.search(_RUNNING_LINK.sub(" ", title_text[-260:])) or \
                not_statement.search(_RUNNING_LINK.sub(" ", _heading(table.context[-200:]))) or \
                len([r for r in table.rows if r.figures]) < 4:
            continue
        if any(_NOT_A_PERIOD.search(cell.text) for cells in table.header for cell in cells if cell.start > 0):
            continue
        if kind == "balance" and any(_INTERIM_SPAN.search(cell.text) for cells in table.header for cell in cells):
            continue  # a balance sheet is at a date: "THREE MONTHS ENDED" over balances is an average balance table (JPM's MD&A)
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


# "GOLD TOE MORETZ HOLDINGS CORP. AND SUBSIDIARIES CONSOLIDATED BALANCE SHEETS": the company a statement's title names.
_SUBSIDIARIES = re.compile(r"\band\s+(?:its\s+)?(?:consolidated\s+)?subsidiar(?:y|ies)\b", re.I)
_ABBREVIATIONS = {"INC.", "CORP.", "CO.", "LTD.", "S.A.", "N.V.", "L.P.", "PLC.", "B.V.", "AG.", "S.A.B."}


def titled_company(kind: str, table: Table) -> str | None:
    """The company named just before a statement's title, as "<name> and subsidiaries"; None where the title names
    none that way, or the name is cut off or runs into a page's other words."""
    text = table.context[-600:]
    title = None
    for title in dict(TITLES)[kind].finditer(text):
        pass
    if title is None or title.start() < len(text) - 300:
        return None
    named = None
    for named in _SUBSIDIARIES.finditer(text, 0, title.start()):
        pass
    if named is None or title.start() - named.end() > 80:
        return None
    before = text[:named.start()].split()
    words: list[str] = []
    for position in range(len(before) - 1, max(-1, len(before) - 9), -1):
        word = before[position]
        if not re.match(r"[A-Z&]", word) or _PAGE_WORD.match(word) or re.search(r"['’]s$", word, re.I) or \
                (words and word.endswith(".") and word.upper() not in _ABBREVIATIONS):
            break  # a page number or label, a lower-case word or a sentence's end: the text before the name
        if position == 0 and len(table.context) >= 600:
            return None  # the name runs back to where the parser's 600 characters before the table begin: cut off
        words.insert(0, word)
    return " ".join(words) or None


_PAGE_WORD = re.compile(r"^(F|FS|FS-R)-?\d+$|^(contents|index|statements?|page)$", re.I)


def _heading(context: str) -> str:
    """The words before a table that describe it. Where the lines just above it are a statement's own title, the
    sentences before that title are other text (Visteon's auditor's report ends "… the segment disclosures as discussed
    in Note 22 …" above "CONSOLIDATED STATEMENTS OF OPERATIONS"); otherwise the sentence before it describes the table
    (JPM's "The following table sets forth selected financial data of the Investment Bank.")."""
    heading = re.split(r"[.;]\s+(?=\S)", context)[-1]
    return heading if any(pattern.search(heading) for _kind, pattern in TITLES) else context


# The company's own profit before taxes ("INCOME BEFORE INCOME TAX"), not an item of other comprehensive income stated
# before its tax (UBS: "Foreign currency translation movements, before tax"; Lloyds: "Remeasurements before tax").
_PROFIT_LINE = re.compile(r"^\(?(net\s+)?(income|profit|earnings|loss|\(loss\))\b|^pre-?\s?tax\b", re.I)


def _retitled(kind: str, text: str) -> bool:
    """Whether another statement's title follows the last title of this kind."""
    pattern = dict(TITLES)[kind]
    last = max((match.end() for match in pattern.finditer(text)), default=0)
    return bool(_OTHER_STATEMENT.search(text, last))


_INTERIM_SPAN = re.compile(r"\b(months|weeks)\s+ended\b", re.I)
_SCOPE_ROW = re.compile(r"\b(consolidated|parent\s+company)$", re.I)


def _header_words(table: Table) -> list[str]:
    return [cell.text for cells in table.header for cell in cells]


_GENERIC_TITLE = re.compile(r"\bconsolidated\s+statements\b(?!\s+of)", re.I)
_EARNINGS_HEAD = re.compile(r"^(earnings|income|operations)\b(\s*\(notes?\b[^)]*\))?$", re.I)


def _leading(table: Table) -> list:
    leading = []
    for row in table.rows:
        if row.figures:
            break
        leading.append(row)
    return leading[:4]


# Words that put a statement's name inside a sentence ("as reported on the Consolidated Statement of Income").
_SENTENCE_WORDS = {"the", "on", "in", "of", "our", "its", "their", "to", "from", "see", "with", "by", "and", "these", "this",
                   "those", "such", "accompanying", "following", "into", "within", "per", "under", "a", "an", "related",
                   # "presents average balance sheets for the three month periods" (SYBT's MD&A): averages, no statement
                   "average", "presents", "present", "summarized"}


_PAGE_FOOTER = re.compile(r"\bpage\s+\d+\s+of\s+\d+\b", re.I)


def _titled(pattern: re.Pattern, text: str) -> bool:
    """Whether a statement's name stands as a heading, not inside a sentence: "J.P. MORGAN CHASE & CO. CONSOLIDATED
    STATEMENT OF INCOME" is a title, "as reported on the Consolidated Statement of Income." is not. A page's footer
    before it is no sentence (Lloyds: "Page 48 of 100 CONSOLIDATED CASH FLOW STATEMENT")."""
    text = _PAGE_FOOTER.sub(" . ", text)
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
    # Or with them in its column header (Delta's 1997 10-Qs: "LIABILITIES AND STOCKHOLDERS' EQUITY  1997  1996").
    page_ends = {id(next((row for row in reversed(table.rows) if row.figures), None))
                 for table, later in zip(group, group[1:])
                 if re.search(r"\bliabilities\b", later.context[-160:] + " " +
                              " ".join(cell.text for cells in later.header for cell in cells), re.I)}
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
            any(LIABILITIES_AND_EQUITY.match(label) or EQUITY.match(label) for label in labels) or bare >= 2
            or any(EQUITY_SECTION.search(label) for label in labels) and any(LIABILITIES.match(label) for label in labels))
    if kind == "cash_flow":
        return any(CLOSING.search(label) for label in labels) or sum(1 for label in labels if DATED_CASH.search(label)) >= 2
    return any(NET_INCOME.match(label) or PRETAX.search(label) for label in labels)
