"""The three statements from a filing printed in HTML, before XBRL (10-K, 10-Q, 20-F of about 2001-2011), by rules.

A statement is found by its title just above its table ("CONSOLIDATED STATEMENTS OF OPERATIONS (In millions, except
share and per share amounts)"), which also gives its scale; a balance sheet or cash flow statement that runs over a
page break continues in the next table. Each figure column takes its period from the header above it: the date
("September 29, 2007"), or the year under a date in the title ("Three fiscal years ended September 29, 2007"), and its
span ("Three Months Ended"). Each line keeps its printed label, its indentation and its figure as printed.

It is kept only when its arithmetic holds on the printed figures (brackets for negatives):
- assets equal liabilities and equity;
- opening cash plus the change (or the three sections) equals closing cash;
- each printed total equals the lines above it, and net income follows from income before taxes.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from .html_tables import Table, figure_columns, header_text, tables
from .model import FAIL, NONE, PASS, Check, Reading
from .periods import Period, label_period

_TITLES = (
    ("income", re.compile(r"\b(consolidated\s+)?(statements?\s+of\s+(consolidated\s+)?(operations|income|earnings)(\s+and\s+comprehensive\s+income)?|income\s+statements?)\b", re.I)),
    ("balance", re.compile(r"\b(consolidated\s+)?(balance\s+sheets?|statements?\s+of\s+(consolidated\s+)?financial\s+(position|condition))\b", re.I)),
    ("cash_flow", re.compile(r"\b(consolidated\s+)?statements?\s+of\s+(consolidated\s+)?cash\s+flows?\b", re.I)),
)
# Tables that carry a statement's name but are not the company's own statement.
_NOT_STATEMENT = re.compile(r"selected|quarterly|condensed\s+consolidating|consolidating|parent\s+company|registrant|"
                            r"guarantor|pro\s+forma|supplemental|segment|summary|five-year|index\s+to|unaudited\s+quarterly|"
                            r"statements?\s+of\s+\w+\s+data|percentage\s+of|average\s+balance|balance\s+sheet\s+analysis", re.I)
_SCALE = re.compile(r"\b(?:in|amounts in|dollars in|expressed in)\s+(?:[A-Z]{2,3}\$?\s+|US\$\s*|NT\$\s*|€\s*|euros?\s+)?"
                    r"(thousands|millions|billions)\b", re.I)
_SCALES = {"thousands": 1e3, "millions": 1e6, "billions": 1e9}
_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_MONTH_DAY = re.compile(rf"\b({_MONTH})\s+(\d{{1,2}})\b", re.I)
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_SPAN = re.compile(r"\b(three|thirteen|six|twenty-six|nine|thirty-nine|twelve|fifty-two|fifty-three)[-\s]+(months|weeks)\b|"
                   r"\b(?:fiscal\s+)?years?\s+ended\b|\b(?:quarter)\s+ended\b", re.I)
_SPAN_DAYS = {"three": 91, "thirteen": 91, "six": 182, "twenty-six": 182, "nine": 273, "thirty-nine": 273,
              "twelve": 364, "fifty-two": 364, "fifty-three": 371}
_PER_SHARE = re.compile(r"per\s+(common\s+|ordinary\s+)?share|per\s+ads|^(basic|diluted)\b|earnings\s+per|\beps\b", re.I)
_SHARE_COUNT = re.compile(r"\bshares\b|weighted[-\s]average", re.I)

# Lines a statement's checks and later eras pair by: the concept XBRL gives the same line.
_KNOWN = (
    (re.compile(r"^total\s+assets$", re.I), "us-gaap_Assets"),
    (re.compile(r"^total\s+liabilities\s+and\s+(stockholders|shareholders|shareowners)['’]?\s+(equity|investment)$|^total\s+liabilities\s+and\s+(total\s+)?equity$|"
                r"^total\s+liabilities,.*and\s+(stockholders|shareholders|shareowners)['’]?\s+equity$", re.I), "us-gaap_LiabilitiesAndStockholdersEquity"),
    (re.compile(r"^total\s+liabilities$", re.I), "us-gaap_Liabilities"),
    (re.compile(r"^total\s+(stockholders|shareholders|shareowners)['’]?\s+equity$", re.I), "us-gaap_StockholdersEquity"),
    (re.compile(r"^total\s+current\s+assets$", re.I), "us-gaap_AssetsCurrent"),
    (re.compile(r"^total\s+current\s+liabilities$", re.I), "us-gaap_LiabilitiesCurrent"),
    (re.compile(r"^(net|total net)\s+(sales|revenues?|operating revenues)$|^(total\s+)?revenues?$", re.I), "us-gaap_Revenues"),
    (re.compile(r"^gross\s+(margin|profit)(\s+on\s+sales)?$", re.I), "us-gaap_GrossProfit"),
    (re.compile(r"^operating\s+income(\s+\(loss\))?$|^income(\s+\(loss\))?\s+from\s+operations$", re.I), "us-gaap_OperatingIncomeLoss"),
    (re.compile(r"^net\s+(income|earnings)(\s+\(loss\))?$|^net\s+loss$", re.I), "us-gaap_NetIncomeLoss"),
    (re.compile(r"cash\s+(generated\s+by|provided\s+by|used\s+(in|for)|from)(\s+\(used\s+(in|for)\))?\s+operating\s+activities$|"
                r"operating\s+activities$", re.I), "us-gaap_NetCashProvidedByUsedInOperatingActivities"),
    (re.compile(r"investing\s+activities$", re.I), "us-gaap_NetCashProvidedByUsedInInvestingActivities"),
    (re.compile(r"financing\s+activities$", re.I), "us-gaap_NetCashProvidedByUsedInFinancingActivities"),
)
_ASSETS = re.compile(r"^total\s+assets$", re.I)
_LIABILITIES_AND_EQUITY = re.compile(r"^total\s+liabilities.*\b(equity|investment|deficit|capital)$", re.I)
_LIABILITIES = re.compile(r"^total\s+liabilities$", re.I)
_EQUITY = re.compile(r"^total\s+((stockholders|shareholders|shareowners)['’]?\s+)?equity$", re.I)
_OPENING = re.compile(r"\bcash\b.*\b(beginning|start)\b", re.I)
_CLOSING = re.compile(r"\bcash\b.*\bend\b", re.I)
_CHANGE = re.compile(r"(increase|decrease|change).*\bcash\b|\bcash\b.*(increase|decrease)|^total\s+cash\s+(generated|provided|used)", re.I)
_EXCHANGE = re.compile(r"exchange\s+rate|foreign\s+currency|currency\s+translation", re.I)
_SECTIONS = re.compile(r"(operating|investing|financing)\s+activities$", re.I)
_PRETAX = re.compile(r"before\b.{0,50}?\b(income\s+)?tax(es|ation)?\b|^profit\s+before\s+tax", re.I)
_NET_INCOME = re.compile(r"^net\s+(income|earnings|loss)(\s+\(loss\))?$|^net\s+income\s+\(loss\)\s+attributable\s+to\b|"
                         r"^net\s+income\s+attributable\s+to\s+(shareholders|stockholders)\s+of\s+the\s+parent|^profit\s+for\s+the\s+(year|period)$", re.I)
_TOTAL = re.compile(r"^total\b", re.I)


def find_statements(html: str | bytes) -> list[tuple[str, list[Table]]]:
    """Each statement table in a document, with the tables it continues into."""
    all_tables = tables(html)
    found: list[tuple[str, list[Table]]] = []
    for position, table in enumerate(all_tables):
        title_text = table.context[-300:] + " " + " ".join(" ".join(cell.text for cell in cells) for cells in table.header[:2])
        kind = next((name for name, pattern in _TITLES if pattern.search(title_text)), None)
        if kind is None or _NOT_STATEMENT.search(title_text[-260:]) or len([r for r in table.rows if r.figures]) < 4:
            continue
        if _is_contents(table):
            continue
        group = [table]
        for later in all_tables[position + 1:position + 3]:
            if _complete(kind, group) or len([r for r in later.rows if r.figures]) < 2:
                break
            later_title = later.context[-200:]
            other = next((name for name, pattern in _TITLES if pattern.search(later_title)), None)
            if other is not None and other != kind and "continued" not in later_title.lower():
                break
            group.append(later)
        if _complete(kind, group) or (kind == "cash_flow" and sum(bool(_SECTIONS.search(row.label)) for table in group
                                                                   for row in table.rows) >= 3):
            found.append((kind, group))
    return found


def _is_contents(table: Table) -> bool:
    labels = " ".join(row.label for row in table.rows).lower()
    return "notes to consolidated financial statements" in labels and len(table.rows) < 15


def _complete(kind: str, group: list[Table]) -> bool:
    labels = [row.label for table in group for row in table.rows if row.figures]
    if kind == "balance":
        return any(_ASSETS.match(label) for label in labels) and any(
            _LIABILITIES_AND_EQUITY.match(label) or _EQUITY.match(label) for label in labels)
    if kind == "cash_flow":
        return any(_CLOSING.search(label) for label in labels)
    return any(_NET_INCOME.match(label) or _PRETAX.search(label) for label in labels)


def _scale(group: list[Table]) -> float | None:
    first = group[0]
    leading = []
    for row in first.rows:
        if row.figures:
            break
        leading.append(row.label)  # "(In millions except par value)" printed as a row above the figures
    text = first.context[-400:] + " " + " ".join(" ".join(cell.text for cell in cells) for cells in first.header) + " " + " ".join(leading)
    found = {_SCALES[match.group(1).lower()] for match in _SCALE.finditer(text)}
    return found.pop() if len(found) == 1 else None


def _date(month: str, day: str, year: str) -> date | None:
    months = {name[:3]: index for index, name in enumerate(calendar.month_name) if name}
    number = months.get(month[:3].lower().capitalize()[:3]) or next(
        (index for index, name in enumerate(calendar.month_abbr) if name and name.lower() == month[:3].lower()), None)
    try:
        return date(int(year), number, int(day)) if number else None
    except ValueError:
        return None


def column_periods(kind: str, group: list[Table], fiscal_year_end: str) -> dict[tuple[int, ...], Period]:
    """Each figure column's period, from the header above it and the statement's title."""
    first = group[0]
    header_all = " ".join(" ".join(cell.text for cell in cells) for cells in first.header)
    context = first.context[-300:]
    # The header's own date first, else one printed after the statement's title ("… years ended December 31, 2007"):
    # a page names other dates before the title (TSMC's auditors' report, dated April 15, 2011).
    titled = [match.end() for _, pattern in _TITLES for match in pattern.finditer(context)]
    title = context[max(titled):] if titled else ""
    in_title = list(_MONTH_DAY.finditer(title))
    title_day = _MONTH_DAY.search(header_all) or (in_title[-1] if in_title else None)
    title_span = _SPAN.search(header_all) or _SPAN.search(title)
    periods: dict[tuple[int, ...], Period] = {}
    for column in figure_columns(first):
        above = header_text(first, column[0]) or header_text(first, column[-1])
        years = _YEAR.findall(above)
        if not years:
            continue
        year = years[-1]
        day = _MONTH_DAY.search(above) or title_day
        if day is not None:
            end = _date(day.group(1), day.group(2), year)
        else:
            month, day_of = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
            end = date(int(year), month, min(day_of, calendar.monthrange(int(year), month)[1]))
        if end is None:
            continue
        if kind == "balance":
            periods[column] = Period(end)
            continue
        span = _SPAN.search(above) or title_span
        days = 364
        if span is not None and span.group(1):
            days = _SPAN_DAYS.get(span.group(1).lower(), 364)
        elif span is not None and "quarter" in span.group(0).lower():
            days = 91
        periods[column] = Period(end, end - timedelta(days=days - 1))
    return periods


def _concept(label: str, kind: str) -> str:
    for pattern, concept in _KNOWN:
        if pattern.search(label):
            if kind != "cash_flow" and "activities" in concept.lower() + label.lower():
                continue
            if kind == "cash_flow" and "Activities" not in concept:
                continue
            return concept
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:120]
    return f"printed_{slug or 'subtotal'}"


def read_document(html: str | bytes, *, accession: str, form: str, filed: date, document_end: date | None,
                  fiscal_year_end: str, year_offset: int) -> tuple[list[Reading], list[str]]:
    """Every statement column of a printed filing, checked; and notes on what it could not read."""
    readings: list[Reading] = []
    notes: list[str] = []
    found = find_statements(html)
    # A statement printed more than once (in full, and as a summary elsewhere): the fullest table is the statement.
    found.sort(key=lambda item: -sum(len([row for row in table.rows if row.figures]) for table in item[1]))
    for kind, group in found:
        if any(reading.statement == kind for reading in readings):
            continue
        scale = _scale(group)
        if scale is None:
            notes.append(f"{kind}: no single scale stated")
            continue
        periods = column_periods(kind, group, fiscal_year_end)
        if not periods:
            notes.append(f"{kind}: no dated columns")
            continue
        ordered = [periods[column] for column in sorted(periods)]
        layout = [(group[0], sorted(periods))]
        for table in group[1:]:
            own = column_periods(kind, [table], fiscal_year_end)
            columns = sorted(own) if own else [c for c in figure_columns(table)][:len(ordered)]
            if len(columns) != len(ordered):
                notes.append(f"{kind}: a continuation table's columns do not line up")
                break
            layout.append((table, columns))
        for index, period in enumerate(ordered):
            labelled = label_period(period, fiscal_year_end, year_offset)
            if labelled is None:
                notes.append(f"{kind}: column {period.key} off the calendar")
                continue
            reading = _read_column(kind, [(table, columns[index], columns) for table, columns in layout], period, scale, labelled)
            if reading is None:
                continue
            reading.accession, reading.form, reading.filed = accession, form, filed
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


def _read_column(kind, layout, period, scale, labelled) -> Reading | None:
    fiscal_year, fiscal_period, duration = labelled
    lines: list[dict] = []
    printed: list[tuple[str, int, float | None]] = []  # label, level, figure as printed (None: a heading)
    heading = ""
    for table, column, columns in layout:
        base = min((row.level for row in table.rows if row.label), default=0)
        for row in table.rows:
            if not row.figures and row.label:
                heading = row.label
            if not row.label and not row.figures:
                continue
            value = _figure(row, column, columns) if row.figures else None
            if row.figures and value is None:
                continue
            per_share = bool(_PER_SHARE.search(row.label))
            shares = not per_share and bool(_SHARE_COUNT.search(row.label)) and kind == "income"
            amount = None if value is None else value if per_share else value * (1 if shares else scale)
            lines.append({"line": len(lines), "concept": _concept(row.label, kind), "label": row.label[:300],
                          "level": max(0, min(20, row.level - base)), "is_heading": not row.figures, "value": amount})
            meaning = row.label
            if re.match(r"^(balance|net\s*\(?(increase|decrease)|increase|decrease)\b", row.label, re.I) and "cash" not in row.label.lower() \
                    and "cash" in heading.lower():
                meaning = f"{heading} {row.label}"  # Coca-Cola: CASH AND CASH EQUIVALENTS / Balance at beginning of year
            if not row.figures:
                printed.append((row.label, row.level, None))
            elif not (per_share or shares):
                printed.append((meaning, row.level, value))
    lines = [line for index, line in enumerate(lines) if not line["is_heading"] or _has_lines_below(lines, index)]
    if not any(not line["is_heading"] for line in lines):
        return None
    for index, line in enumerate(lines):
        line["line"] = index
    # The checks run in printed units: a printed figure is exact to one unit.
    figures = [(label, level, value) for label, level, value in printed if value is not None]
    checks = [_total_check(printed)]
    if kind == "balance":
        checks.insert(0, _balance_check(figures))
    elif kind == "cash_flow":
        checks.insert(0, _cash_check(figures))
    else:
        checks.insert(0, _income_check(figures))
    return Reading(statement=kind, duration=duration, fiscal_year=fiscal_year, fiscal_period=fiscal_period,
                   period_end=period.end, period_start=period.start, currency=None, accession="", form="", filed=date.min,
                   own=False, source="html", lines=lines, checks=checks)


def _has_lines_below(lines: list[dict], index: int) -> bool:
    level = lines[index]["level"]
    for later in lines[index + 1:]:
        if not later["is_heading"]:
            return True
        if later["level"] <= level:
            return False
    return False


def _close(left: float, right: float, terms: int = 2) -> bool:
    return abs(left - right) <= 0.5 * terms + 1e-9


def _first(figures, pattern, after: int = -1) -> tuple[int, float] | None:
    for index, (label, _level, value) in enumerate(figures):
        if index > after and pattern.search(label):
            return index, value
    return None


def _balance_check(figures) -> Check:
    assets = _first(figures, _ASSETS)
    total = _first(figures, _LIABILITIES_AND_EQUITY)
    if assets and total:
        return Check("balance", PASS if _close(assets[1], total[1]) else FAIL,
                     "" if _close(assets[1], total[1]) else f"assets {assets[1]:,.1f} vs liabilities and equity {total[1]:,.1f}")
    liabilities, equity = _first(figures, _LIABILITIES), _first(figures, _EQUITY)
    if assets and liabilities and equity:
        between = [value for index, (_l, _v, value) in enumerate(figures) if liabilities[0] < index < equity[0]]
        if _close(assets[1], liabilities[1] + equity[1]) or _close(assets[1], liabilities[1] + equity[1] + sum(between[-1:])):
            return Check("balance", PASS)
        return Check("balance", FAIL, f"assets {assets[1]:,.1f} vs liabilities {liabilities[1]:,.1f} + equity {equity[1]:,.1f}")
    return Check("balance", NONE, "no total assets and total liabilities and equity")


def _cash_check(figures) -> Check:
    opening, closing = _first(figures, _OPENING), _first(figures, _CLOSING)
    if not opening or not closing:
        return Check("cash", NONE, "no opening and closing cash")
    changes = [value for label, _level, value in figures if _CHANGE.search(label) and not _OPENING.search(label)
               and not _CLOSING.search(label)]
    effects = [value for label, _level, value in figures if _EXCHANGE.search(label)]
    sections = [value for label, _level, value in figures if _SECTIONS.search(label) and re.search(r"^net|^cash|^total", label, re.I)]
    candidates = list(changes)
    candidates += [change + effect for change in changes for effect in effects]
    if len(sections) >= 3:
        candidates += [sum(sections[:3]), sum(sections[:3]) + sum(effects[:1])]
    for change in candidates:
        if _close(opening[1] + change, closing[1], 3):
            return Check("cash", PASS)
    if not candidates:
        return Check("cash", NONE, "no change in cash")
    return Check("cash", FAIL, f"opening {opening[1]:,.1f} + change {candidates[0]:,.1f} vs closing {closing[1]:,.1f}")


def _income_check(figures) -> Check:
    """Net income follows from income before taxes through the lines between them, each added or taken away as the
    statement means it (a printed statement shows a tax charge as a plain figure and takes it away). Lines a subtotal
    between them sums are read as that subtotal (Prudential's current and deferred tax under total income taxes)."""
    pretax = _first(figures, _PRETAX)
    net = _first(figures, _NET_INCOME, after=pretax[0] if pretax else -1)
    if not pretax or not net:
        return Check("calc", NONE, "no income before taxes and net income")
    between = figures[pretax[0] + 1:net[0]]
    starts = _subtotals(between)
    covered = {position for end, start in starts.items() for position in range(start, end)}
    # Lines a later subtotal sums are read through that subtotal, or each on its own: a running subtotal (income from
    # continuing operations) can also equal the two lines above it, and must then restart the chain instead.
    for skip in (covered, set()):
        if _chain(pretax[1], net[1], [value for index, (_l, _v, value) in enumerate(between) if index not in skip]):
            return Check("calc", PASS, "net income from income before taxes")
    return Check("calc", FAIL, f"income before taxes {pretax[1]:,.1f} does not lead to net income {net[1]:,.1f}")


def _chain(start: float, end: float, values: list[float]) -> bool:
    reachable = {start}
    for value in values:
        if any(_close(value, running, len(values) + 1) for running in reachable):
            reachable = {value}  # a running subtotal
            continue
        reachable = {running + sign * value for running in reachable for sign in (1, -1)}
        if len(reachable) > 512:
            return False
    return any(_close(running, end, len(values) + 2) for running in reachable)


def _subtotals(printed) -> dict[int, int]:
    """Where each subtotal's lines start: a printed "Total …", or a line equal to the two or more lines just above it
    (an unlabelled net figure). An earlier subtotal counts as one line."""
    starts: dict[int, int] = {}
    for index, (label, _level, value) in enumerate(printed):
        if value is None:
            continue
        sums, position, total = [], index - 1, 0.0
        while position >= 0 and len(sums) < 12:
            earlier_label, _earlier_level, earlier_value = printed[position]
            if earlier_value is None:
                break
            total += -earlier_value if re.match(r"^less\b", earlier_label, re.I) else earlier_value
            sums.append((position, total))
            position = starts.get(position, position) - 1
        for count, (start, running) in enumerate(sums, 1):
            if count >= 2 and _close(running, value, count + 1):
                starts[index] = start
                break
        else:
            # A difference of the two lines above: JPM's net interest income, interest income less interest expense,
            # both printed as plain figures.
            if index >= 2 and printed[index - 1][2] is not None and printed[index - 2][2] is not None \
                    and _close(printed[index - 2][2] - printed[index - 1][2], value, 3) and index - 1 not in starts:
                starts[index] = index - 2
    return starts


def _total_check(printed) -> Check:
    """Each printed "Total …" equals lines just above it, read upward: an earlier subtotal counts as one line for the
    lines it sums, together with the headings over them ("Total assets" = total current assets + the lines after it),
    and a line headed "Less" is taken away. A total with fewer than two lines above it is not tested."""
    verified, failures = 0, []
    starts = _subtotals(printed)
    for index, (label, _level, value) in enumerate(printed):
        if value is None or not _TOTAL.match(label) or _LIABILITIES_AND_EQUITY.match(label):
            continue
        running, count, position, matched = 0.0, 0, index - 1, False
        while position >= 0 and count < 40:
            earlier_label, _earlier_level, earlier_value = printed[position]
            if earlier_value is None:
                position -= 1
                continue  # a heading: a total can sum sections under several headings
            running += -earlier_value if re.match(r"^less\b", earlier_label, re.I) else earlier_value
            count += 1
            if count >= 2 and _close(running, value, count + 1):
                matched = True
                break
            position = starts.get(position, position) - 1
        if count < 2:
            continue
        verified += 1
        if not matched:
            failures.append(f"{label[:40]} {value:,.1f}")
    if failures:
        return Check("totals", FAIL, "; ".join(failures[:3]))
    return Check("totals", PASS if verified else NONE, f"{verified} totals")
