"""The three statements from a filing's printed tables, HTML or plain text, before XBRL, by rules.

A statement is found by its title just above its table ("CONSOLIDATED STATEMENTS OF OPERATIONS (In millions, except
share and per share amounts)"), which also gives its scale; a balance sheet or cash flow statement that runs over a
page break continues in the next table. Each figure column takes its period from the header above it: the date
("September 29, 2007"), or the year under a date in the title ("Three fiscal years ended September 29, 2007"), and its
span ("Three Months Ended"). Each line keeps its printed label, its indentation and its figure as printed.

It is kept only when its arithmetic holds on the printed figures (brackets for negatives): assets equal liabilities and
equity; opening cash plus the change (or the three sections) equals closing cash; each printed total equals the lines
above it; net income follows from income before taxes.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from ...checks.printed import balance_check, cash_check, income_check, total_check
from ...domain.model import Reading
from ...domain.periods import label_period
from ...domain.printed_labels import CASH_HEADING, KNOWN, OPERATING_DATA, PER_SHARE, SHARE_COUNT
from .company_title import titled_company
from .currency import column_currencies, reporting_currency, split_by_sign, translations
from .ranges import without_repeats
from .dates import column_periods
from .find import TITLES, find_statements, two_columns, unlabelled_totals
from .scales import line_scale, share_scale, statement_scale, states_two
from .scope import statement_scope
from .side_by_side import column_scopes
from .tables import Table, figure_columns, statement_rows
from .titled import titled_notes


def _concept(label: str, kind: str) -> str:
    for pattern, concept in KNOWN:
        if pattern.search(label):
            if kind != "cash_flow" and "activities" in concept.lower() + label.lower():
                continue
            if kind == "cash_flow" and "Activities" not in concept:
                continue
            return concept
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:120]
    return f"printed_{slug or 'subtotal'}"


def read_tables(all_tables: list[Table], *, accession: str, form: str, filed: date, document_end: date | None,
                fiscal_year_end: str, year_offset: int, unstated_scale: float | None = None,
                left_out: frozenset = frozenset()) -> tuple[list[Reading], list[str]]:
    """Every statement column of a printed filing, checked; and notes on what it could not read. With
    `unstated_scale`, a statement that states no scale is read in that one (pipeline/unstated_scale.py proves it);
    none titled for a company in `left_out` (Brookfield Asset Management ULC's in Brookfield Asset Management Ltd.'s)."""
    readings: list[Reading] = []
    notes: list[str] = []
    found = [(kind, group) for kind, group in find_statements(all_tables, release=form == "6-K")
             if titled_company(kind, group[0]) not in left_out]  # another company's (pipeline/printed.py)
    # Printed more than once: the group's (TSMC's 2008 6-Ks), a titled one (Brookfield's, not its MD&A's), the fullest.
    found.sort(key=lambda item: (statement_scope(item[0], item[1], form, found) != "consolidated",
                                 titled_company(item[0], item[1][0]) is None,
                                 -sum(len([row for row in table.rows if row.figures]) for table in item[1])))
    read_periods: dict[tuple, set] = {}  # by kind and scope: Copel prints the parent's quarter, then the group's
    for kind, group in found:
        periods = without_repeats(group[0], split_by_sign(group[0], column_periods(kind, group, fiscal_year_end, document_end)))
        notes.extend(titled_notes(kind, periods, document_end, fiscal_year_end, year_offset))
        # Another company's statement the filing carries has periods of its own (Heineken's in FEMSA's 20-Fs, its
        # fuller balance sheet read first; Coca-Cola FEMSA's release in FEMSA's, round 84): it never takes the company's.
        seen = (kind, statement_scope(kind, group, form, found), group[0].audited_of, group[0].release_of)
        if seen in read_periods:
            # A statement printed again gives only the periods it prints of its own: Chase's 1995 10-Qs print the three
            # months and the six months as two income statements; TSMC's releases print the quarters and the year.
            periods = {column: period for column, period in periods.items() if period.key not in read_periods[seen]}
            if not periods:
                continue
        scale = statement_scale(group)
        if scale is None and unstated_scale is not None and not states_two(group):
            scale = unstated_scale
        elif scale is None:
            notes.append(f"{kind}: no single scale stated")
            continue
        elif unstated_scale is not None:
            continue  # read with its own scale already
        if not periods:
            notes.append(f"{kind}: no dated columns")
            continue
        dated = sorted(set(periods) - two_columns(group[0], periods))
        by_column = column_currencies(group[0], dated)
        currency = reporting_currency(group, by_column, form)
        dropped = translations(dated, periods, by_column, currency, group)
        if dropped:
            notes.append(f"{kind}: {len(dropped)} convenience translation column(s) left out")
        read_periods.setdefault(seen, set()).update(period.key for period in periods.values())
        kept = [index for index, column in enumerate(dated) if column not in dropped]
        scopes = column_scopes(group, dated, periods)
        if scopes is None:
            notes.append(f"{kind}: parent and group columns side by side, not told apart")
            continue
        layout = [(group[0], dated)]
        for table in group[1:]:
            own = column_periods(kind, [table], fiscal_year_end, document_end)
            columns = sorted(own) if own else [c for c in figure_columns(table)][:len(dated)]
            # A page split as the first was, by the signs its title defined there: FEMSA's continued cash flows head
            # "2010" over the dollars and the pesos again, with no sign beside them (round 81).
            singles = {position for column in dated if len(column) == 1 for position in column}
            columns = sorted({(position,) for column in columns for position in column} if len(columns) != len(dated) and all(
                len(column) == 1 or set(column) <= singles for column in columns) else columns)
            if len(columns) != len(dated):
                notes.append(f"{kind}: a continuation table's columns do not line up")
                break
            layout.append((table, columns))
        for index in kept:
            labelled = label_period(period := periods[dated[index]], fiscal_year_end, year_offset)
            if labelled is None or filed != date.min and period.end > filed:  # a column ending after its filing is misdated
                notes.append(f"{kind}: column {period.key} {'off the calendar' if labelled is None else 'ends after the filing'}")
                continue
            # Every dated column stays in `columns`, translations too, so a figure off its place is never taken for the
            # column beside it.
            reading = _read_column(kind, [(table, columns[index], columns) for table, columns in layout], period,
                                   (scale, share_scale(group, scale)), labelled)
            if reading is None:
                continue
            reading.accession, reading.form, reading.filed, reading.currency = accession, form, filed, currency
            reading.scope = scopes.get(dated[index]) or statement_scope(kind, group, form, found)
            reading.titled = titled_company(kind, group[0])
            # Untitled, the company the auditors' report before it names: Heineken's statements in FEMSA's 20-Fs.
            reading.audited_of = "" if reading.titled else group[0].audited_of
            reading.own = document_end is not None and abs(period.end - document_end) <= timedelta(days=7)
            readings.append(reading)
    return readings, notes


def _figure(row, column: tuple[int, ...], columns: list[tuple[int, ...]]) -> float | None:
    """A row's figure in a column: at one of its positions, or a cell off them but nearer to it than to any other."""
    for position in column:
        if position in row.figures:
            return row.figures[position]
    for position, value in row.figures.items():
        nearest = min(columns, key=lambda other: min(abs(p - position) for p in other))
        if nearest == column and min(abs(p - position) for p in column) <= 2:
            return value
    return None


# A row that only states the scale or the audit status: "Notes (in thousands, except per share data) EUR EUR".
# Woori's "(Korean Won in millions, except per share data)": the currency named before its scale.
_CAPTION = re.compile(r"\(\s*(?:[A-Za-z]+\s+){0,2}(?:in|amounts\s+in|dollars\s+in|expressed\s+in)\s+(?:[A-Z]{2,3}\s+)?(thousands|millions|billions)\b|"
                      r"^\(?unaudited\)?$|^amounts\s+expressed\s+in\s+(thousands|millions|billions)\b|"  # Coca-Cola FEMSA's 20-Fs
                      # A caption's second line: TSMC's "Except for Per Share Amounts and Shares Outstanding)".
                      r"^\(?\s*except\s+(for\s+)?(per\s+share|share|earnings\s+per)|"
                      # …and Woori's 2010 caption over two columns, its second line twice: "per common share data) per
                      # common share data)".
                      r"^(?:(?:income\s+)?per\s+(?:common\s+)?share\s+data\)\s*)+$", re.I)


# A cash flow section's heading: "Cash flows from operating activities".
_SECTION_HEADING = re.compile(r"^(?:cash\s+flows?\s+(?:from|used\s+in|provided\s+by|\(used\s+in\))?.*?\b)?(operat|invest|financ)ing"
                              r"(?:\s+activities)?\b", re.I)


def _close_section(printed: list, section: str | None, index: int | None) -> None:
    """Names a cash flow section's unlabelled total as the section's net cash, for the checks."""
    if section and index is not None and index < len(printed):
        _label, level, value = printed[index]
        printed[index] = (f"Net cash from {section}ing activities", level, value)


# A title or a slide's navigation run into a column's year (its use in _read_column says whose).
_YEAR_ROW_LABEL = re.compile(r"\bstatements?\s+of\b|\bfor\s+the\s+(years?|quarters?|periods?|(three|six|nine|twelve)\s+months)\b|"
                             r"\byears?\s+ended\b|\bsummary\b|\bforward\s+look", re.I)


# A memo line inside the line above it, served but never summed: UBS's "of which: assets pledged as collateral which
# may be sold or repledged by counterparties", within its trading portfolio assets.
_MEMO = re.compile(r"^\s*(of\s+which|thereof)\b", re.I)


def _caption_only(label: str) -> bool:
    """A row that is a scale caption ("(In millions, except share amounts)", ASML's "For the year ended December 31
    (Amounts in thousands, except per share data)"). One that names share counts before their scale is a heading:
    Apple's "Shares used in computing earnings (loss) per share (in thousands):", whose "Basic" and "Diluted" below
    are share counts in thousands, not figures a share."""
    match = _CAPTION.search(label)
    return bool(match) and not _SHARE_HEADING.search(label[:match.start()])


_SHARE_CLASS = re.compile(r"^(ordinary|common|preferred|class\s+\S+)\b.*\bshares?$", re.I)
_SHARE_HEADING = re.compile(r"\bshares\b|weighted[-\s]average|\baverage\s+(number|common)", re.I)


def _read_column(kind, layout, period, scales, labelled) -> Reading | None:
    fiscal_year, fiscal_period, duration = labelled
    scale, shares_scale = scales
    lines: list[dict] = []
    printed: list[tuple[str, int, float | None]] = []  # label, level, figure as printed (None: a heading)
    named = unlabelled_totals([table for table, _column, _columns in layout]) if kind == "balance" else {}
    heading, section, section_total = "", None, None
    for table, column, columns in layout:
        base = min((row.level for row in table.rows if row.label), default=0)
        last_heading = None
        for row in statement_rows(table.rows):
            if not row.label and not row.figures:
                continue
            # A heading wrapped onto a second line: Apple's "Shares used in computing earnings (loss)" / "per common
            # share (in thousands):".
            # Copel's "Basic and diluted net earning per share attributed" / "To parent company shareholders - in reais".
            label = f"{last_heading} {row.label}" if not row.figures and last_heading and (row.label[:1].islower() or re.search(
                r"\b(attributed|attributable|of|to|per|for)$", last_heading, re.I)) else row.label
            last_heading = None
            if not row.figures and (any(pattern.search(label) for _, pattern in TITLES) or _caption_only(label)):
                continue  # the statement's title or scale printed as a row of its own table (PepsiCo, ASML), not a line
            if not row.figures:
                heading = last_heading = label
                opened = _SECTION_HEADING.match(row.label)
                if opened or CASH_HEADING.match(row.label):
                    _close_section(printed, section, section_total)
                    section, section_total = (opened.group(1).lower() if opened else None), None
            value = _figure(row, column, columns) if row.figures else None
            if row.figures and (value is None or OPERATING_DATA.search(row.label)):
                continue
            if value is not None and _YEAR_ROW_LABEL.search(row.label) and any(
                    figure == int(figure) and abs(figure - period.end.year) <= 1 for figure in row.figures.values()):
                # A title or a slide's navigation run into the column's year: ASML's "Consolidated Statements of
                # Comprehensive Income For the year ended December 31  1998", its Q1 2026 release's "Summary US GAAP
                # … Forward Looking Statements … 2026" (and the slide's page number). Not a line.
                continue
            # A balance sheet has no per-share lines; "per share" there is a par value in a stock line's label (Kroger's
            # "Common stock, $1 par per share … 955").
            # A line under a per-share heading is per share too: Coca-Cola's "INCOME PER SHARE" / "Before change in
            # accounting principle  $ .55".
            per_share = kind != "balance" and bool(PER_SHARE.search(row.label) or kind == "income" and row.figures and PER_SHARE.search(
                heading) and (not _SHARE_HEADING.search(row.label) or _SHARE_CLASS.match(row.label)))
            under_shares = kind == "income" and bool(_SHARE_HEADING.search(heading)) and \
                re.match(r"^(basic|diluted|primary|fully\s+diluted|assuming\s+dilution)\b", row.label, re.I) is not None
            counted = re.match(r"^(shares|weighted|average|number\s+of\s+shares)\b", row.label, re.I) is not None
            # A share class under a per-share heading is that class's amount per share (Copel's "Class \"B\" Preferred shares").
            if not (per_share and _SHARE_CLASS.match(row.label)) and (under_shares or counted or re.search(r"\bshares\b", row.label, re.I) and not re.search(
                    r"per\s+(common\s+)?share\b|earnings|income|loss", row.label, re.I)):
                per_share = False  # TSMC's "BASIC WEIGHTED AVERAGE SHARES OUTSTANDING (THOUSANDS)"
            shares = not per_share and kind == "income" and (under_shares or counted or bool(SHARE_COUNT.search(row.label)))
            if value is None or per_share:
                amount = value
            elif shares:
                amount = value * (line_scale(row.label, heading if under_shares else "") or shares_scale)
            else:
                amount = value * scale
            lines.append({"line": len(lines), "concept": _concept(row.label, kind), "label": row.label[:300],
                          "level": max(0, min(20, row.level - base)), "is_heading": not row.figures, "value": amount,
                          **({"code": row.code} if row.code else {})})
            meaning = named.get(id(row), row.label)
            if kind == "cash_flow" and row.figures and not row.label and section:
                # A section closed by an unlabelled total (Scotiabank, Brookfield): its last before the next section
                # is the net cash of the section (Brookfield's operating section has a subtotal before it).
                section_total = len(printed)
            if re.match(r"^(balance|net\s*\(?(increase|decrease)|increase|decrease|beginning|end|start)\b", row.label, re.I) \
                    and "cash" not in row.label.lower() \
                    and CASH_HEADING.match(heading):
                # Coca-Cola: CASH AND CASH EQUIVALENTS / Balance at beginning of year; Kroger: Cash and temporary cash
                # investments: / Beginning of year
                meaning = f"{heading} {row.label}"
            if not row.figures:
                printed.append((row.label, row.level, None))
            elif not (per_share or shares or _MEMO.match(row.label)):
                printed.append((meaning, row.level, value))
    _close_section(printed, section, section_total)
    lines = [line for index, line in enumerate(lines) if not line["is_heading"] or _has_lines_below(lines, index)]
    if not any(not line["is_heading"] for line in lines):
        return None
    for index, line in enumerate(lines):
        line["line"] = index
    # The checks run in printed units: a printed figure is exact to one unit of the precision it is printed to, a
    # whole million for most filers, a tenth for TSMC (where a whole-unit tolerance let 32.5 - 31.3 pass as 0.0).
    unit = _printed_unit([value for _label, _level, value in printed if value is not None])
    printed = [(label, level, None if value is None else round(value / unit, 6)) for label, level, value in printed]
    figures = [(label, level, value) for label, level, value in printed if value is not None]
    checks = [total_check(printed)]
    if kind == "balance":
        checks.insert(0, balance_check(figures))
    elif kind == "cash_flow":
        checks.insert(0, cash_check(figures))
    else:
        checks.insert(0, income_check(figures))
    return Reading(statement=kind, duration=duration, fiscal_year=fiscal_year, fiscal_period=fiscal_period,
                   period_end=period.end, period_start=period.start, currency=None, accession="", form="", filed=date.min,
                   own=False, source="html", lines=lines, checks=checks, release_of=layout[0][0].release_of)


def _printed_unit(values: list[float]) -> float:
    """The smallest place the column's figures are printed to: 1, 0.1, 0.01 or 0.001."""
    for places in range(4):
        if all(abs(value * 10 ** places - round(value * 10 ** places)) < 1e-6 for value in values):
            return 10.0 ** -places
    return 0.001


def _has_lines_below(lines: list[dict], index: int) -> bool:
    level = lines[index]["level"]
    for later in lines[index + 1:]:
        if not later["is_heading"]:
            return True
        if later["level"] <= level:
            return False
    return False
