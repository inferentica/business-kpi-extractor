"""Statements in Mexico's earlier reporting template (CNBV's, as filed with BMV until issuers moved to IFRS in 2012),
which some issuers also furnish in their 6-Ks (Televisa's quarterly "kr6kfinan" documents of 2010-11): every line a
lettered account code in a column of its own ("s01 | TOTAL ASSETS", "r09 | INCOME BEFORE INCOME TAXES", "e14 | CASH AND
CASH EQUIVALENTS AT END OF PERIOD"), columns headed "CURRENT YEAR | PREVIOUS YEAR", each with its share of the total
beside it ("Amount | %"). The balance sheet compares the same quarter a year before ("AS OF JUNE 30, 2010 AND 2009"),
the income statements the year to date and the quarter ("FROM JANUARY 1 TO JUNE 30, 2010 AND 2009", "FROM APRIL 1 TO
JUNE 30"). The cash flow statement prints no dates: it covers the year to date of the report's quarter ("QUARTER: 02
YEAR: 2010"), read only where its closing cash is the balance sheet's cash at the same dates. Each statement is kept
only when its code arithmetic holds; the codes, not the labels, name its lines (mexico_coded.py)."""
from __future__ import annotations

import html
import re
from datetime import date

from ...domain.model import Reading
from ...domain.periods import Period, label_period
from .mexico_coded import _day, _start, _sum_check
from .tables import clean, number

_CODE = re.compile(r"^(s|rt|r|e)(\d{2,3})$")
_KINDS = (("balance", re.compile(r"\bbalance\s+sheets?\b", re.I)), ("cash_flow", re.compile(r"\bstatements?\s+of\s+cash\s+flows?", re.I)),
          ("income", re.compile(r"\bstatements?\s+of\s+income\b", re.I)))
_OTHER = re.compile(r"breakdown|other\s+concepts|analysis", re.I)  # the template's breakdowns, not its statements
_AS_OF = re.compile(r"as\s+of\s+([a-z]+)\s+(\d{1,2}),?\s+((?:19|20)\d{2})\s+and\s+((?:19|20)\d{2})", re.I)
_FROM = re.compile(r"from\s+([a-z]+)\s+(\d{1,2})\s+to\s+([a-z]+)\s+(\d{1,2}),?\s+((?:19|20)\d{2})\s+and\s+((?:19|20)\d{2})", re.I)
_QUARTER = re.compile(r"QUARTER:\s*0?([1-4])D?\s+YEAR:\s*((?:19|20)\d{2})", re.I)
_SCALE = re.compile(r"\(\s*(thousands|millions)\s+of\s+([a-z\s]+?)\s*\)", re.I)
_RULES = {"balance": ([("s01", [("s20", 1), ("s33", 1)]), ("s33", [("s34", 1), ("s35", 1)]),
                       ("s20", [("s21", 1), ("s27", 1), ("s31", 1), ("s32", 1)])], 2),
          "income": ([("03", [("01", 1), ("02", -1)]), ("05", [("03", 1), ("04", -1)]), ("11", [("09", 1), ("10", -1)]),
                      ("18", [("11", 1), ("14", 1)]), ("18", [("19", 1), ("20", 1)])], 3),
          "cash_flow": ([("e14", [("e11", 1), ("e12", 1), ("e13", 1)]), ("e11", [("e09", 1), ("e10", 1)]),
                         ("e09", [("e07", 1), ("e08", 1)])], 2)}
_KEY = {"s01": "bmv_Assets", "s03": "bmv_CashAndCashEquivalents", "01": "bmv_Revenue", "18": "bmv_ProfitLoss",
        "e14": "bmv_CashAndCashEquivalents"}


def is_lettered(content: str) -> bool:
    return "CURRENT YEAR" in content[:3_000_000].upper() and re.search(r">\s*s01\s*<", content[:3_000_000]) is not None


def _columns(table) -> tuple[int, int] | None:
    """The amount columns: under "CURRENT YEAR" and "PREVIOUS YEAR", else the two headed "Amount" (each followed by
    its "%"); the income statements' "CURRENT YEAR | PREVIOUS YEAR" row is printed apart, above the table."""
    rows = [row.cells for row in table.rows[:4]] + list(table.header)
    for cells in rows:
        named = {clean(cell.text).upper(): cell.start for cell in cells}
        if "CURRENT YEAR" in named and "PREVIOUS YEAR" in named:
            return named["CURRENT YEAR"], named["PREVIOUS YEAR"]
    for cells in rows:
        amounts = [cell.start for cell in cells if clean(cell.text).upper() == "AMOUNT"]
        if len(amounts) == 2:
            return amounts[0], amounts[1]
    return None


def _values(table, position: int, unit: float, kind: str) -> tuple[dict, list]:
    values, lines = {}, []
    for row in table.rows:
        code = next((clean(cell.text) for cell in row.cells if cell.start == 0), "")
        label = next((clean(cell.text) for cell in row.cells if cell.start == 1), "")
        raw = next((cell.text for cell in row.cells if cell.start == position), "")
        value = number(raw) if raw.strip() else None
        if not label or value is None:
            continue
        match = _CODE.match(code)
        key = (match.group(2) if kind == "income" else code) if match else None
        if key is not None and key not in values:
            values[key] = value * unit
        lines.append({"line": len(lines), "concept": _KEY.get(key or "", f"bmv_{code}" if match else "printed_"
                      + re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")), "code": code, "label": label,
                      "level": 0, "is_heading": False, "value": value * unit})
    return values, lines


def lettered_readings(tables, content: str, *, accession: str, form: str, filed: date, document_end: date | None,
                      fiscal_year_end: str, year_offset: int) -> list[Reading]:
    quarter = _QUARTER.search(clean(html.unescape(re.sub(r"<[^>]+>", " ", content[:400_000]))))
    found: list[tuple[str, Period, dict, list, float, str | None, str]] = []
    for table in tables:
        columns = _columns(table)
        title = table.context[-300:]
        titles = [(match.start(), kind) for kind, pattern in _KINDS for match in pattern.finditer(title)]
        if columns is None or not titles or _OTHER.search(title):
            continue
        kind = max(titles)[1]
        scale = _SCALE.search(title)
        if scale is None:
            continue
        unit = {"thousands": 1e3, "millions": 1e6}[scale.group(1).lower()]
        currency = "MXN" if "mexican" in scale.group(2).lower() else None
        if kind == "balance" and (match := _AS_OF.search(title)):
            ends = [_day(match.group(1), match.group(2), year) for year in match.group(3, 4)]
            periods = [Period(end) for end in ends if end]
        elif kind == "income" and (match := _FROM.search(title)):
            periods = [Period(end, _day(match.group(1), match.group(2), str(end.year))) for end in
                       (_day(match.group(3), match.group(4), year) for year in match.group(5, 6)) if end]
        elif kind == "cash_flow" and quarter:
            month = int(quarter.group(1)) * 3
            periods = [Period(end, _start(end, month)) for end in
                       (_day(("march", "june", "september", "december")[month // 3 - 1], "31" if month in (3, 12) else "30",
                             str(int(quarter.group(2)) - back)) for back in (0, 1)) if end]
        else:
            continue
        if len(periods) != 2:
            continue
        for position, period in zip(columns, periods):
            values, lines = _values(table, position, unit, kind)
            found.append((kind, period, values, lines, unit, currency, title))
    cash = {period.end: values.get("s03") for kind, period, values, *_ in found if kind == "balance"}
    readings = []
    for kind, period, values, lines, unit, currency, title in found:
        if kind == "cash_flow" and (cash.get(period.end) is None or values.get("e14") is None or
                                    abs(cash[period.end] - values["e14"]) > unit):
            continue  # its dates unproven: the closing cash is not the balance sheet's cash on that day
        labelled = label_period(period, fiscal_year_end, year_offset)
        if labelled is None or period.end > filed or not lines:
            continue
        rules, needed = _RULES[kind]
        readings.append(Reading(
            statement=kind, duration=labelled[2], fiscal_year=labelled[0], fiscal_period=labelled[1], period_end=period.end,
            period_start=period.start, currency=currency, accession=accession, form=form, filed=filed, source="html",
            own=document_end is not None and abs((period.end - document_end).days) <= 7, lines=lines,
            scope="consolidated" if "consolidated" in title.lower() else "parent",
            checks=[_sum_check({"balance": "balance", "income": "calc", "cash_flow": "cash"}[kind], values, rules, unit, needed)]))
    return readings
