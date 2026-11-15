"""Statements in the printout of a Mexican issuer's quarterly XBRL report (BMV's IFRS template since 2016), which some
issuers furnish in their 6-Ks (Televisa's "kr6k2qfinan" and "jw6k3qfinancials" documents): each statement under its
taxonomy role's number and name ("[210000] Statement of financial position, current/non-current", "[310000] Statement
of comprehensive income, profit or loss, by function of expense", "[520000] Statement of cash flows, indirect
method"), a "Concept" column of the taxonomy's labels, columns headed by their dates ("Close Current Quarter
2017-06-30", "Accumulated Current Year 2017-01-01 - 2017-06-30"), the figures in whole pesos as the report files them
(costs as positive amounts), a statement's later pages headed again by the same dates. The report states its own
currency and rounding ("Description of presentation currency: MXN", "Level of rounding used in financial statements:
THOUSANDS OF MEXICAN PESOS"). BMV keeps only the last 20 quarters' packages (sources/latam.py); these printouts reach
back to the template's first quarter."""
from __future__ import annotations

import html
import re
from datetime import date

from ...checks.coded import tagged_checks
from ...domain.model import Reading
from ...domain.periods import Period, label_period
from .tables import clean, number

MARKER = re.compile(r"\[\s*(210000|220000|310000|320000|510000|520000)\s*\]\s*Statement\s+of", re.I)
_SECTION = re.compile(r"\[\s*\d{6}\s*\]")
_KIND = {"210000": "balance", "220000": "balance", "310000": "income", "320000": "income",
         "510000": "cash_flow", "520000": "cash_flow"}
_DATES = re.compile(r"((?:19|20)\d{2}-\d{2}-\d{2})(?:\s*-\s*((?:19|20)\d{2}-\d{2}-\d{2}))?")
_CURRENCY = re.compile(r"Description\s+of\s+presentation\s+currency\s*:\s*([A-Z]{3})\b")
_ROUNDING = re.compile(r"Level\s+of\s+rounding\s+used\s+in\s+financial\s+statements\s*:\s*(THOUSANDS|MILLIONS)", re.I)
_CONSOLIDATED = re.compile(r"\bConsolidated\s*:\s*(Yes|No)\b", re.I)
_FOOTNOTE = re.compile(r"^\[\s*\d+\s*\]\s*")  # "[1] 7,143,099,000": a note's number before the figure
# The template's totals, by their labels, named as the IFRS taxonomy tags them (checks/coded.py's tagged checks).
_TAGS = {
    "balance": {"total assets": "Assets", "total current assets": "CurrentAssets", "total non-current assets": "NoncurrentAssets",
                "total liabilities": "Liabilities", "total equity": "Equity", "total equity and liabilities": "EquityAndLiabilities",
                "cash and cash equivalents": "CashAndCashEquivalents"},
    "income": {"revenue": "Revenue", "cost of sales": "CostOfSales", "gross profit": "GrossProfit",
               "profit (loss) before tax": "ProfitLossBeforeTax", "profit (loss)": "ProfitLoss",
               "profit (loss), attributable to owners of parent": "ProfitLossAttributableToOwnersOfParent",
               "profit (loss), attributable to non-controlling interests": "ProfitLossAttributableToNoncontrollingInterests"},
    "cash_flow": {"net cash flows from (used in) operating activities": "CashFlowsFromUsedInOperatingActivities",
                  "net cash flows from (used in) investing activities": "CashFlowsFromUsedInInvestingActivities",
                  "net cash flows from (used in) financing activities": "CashFlowsFromUsedInFinancingActivities",
                  "net increase (decrease) in cash and cash equivalents before effect of exchange rate changes":
                      "IncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges",
                  "effect of exchange rate changes on cash and cash equivalents": "EffectOfExchangeRateChangesOnCashAndCashEquivalents",
                  "net increase (decrease) in cash and cash equivalents": "IncreaseDecreaseInCashAndCashEquivalents",
                  "cash and cash equivalents at beginning of period": "CashAndCashEquivalentsAtBeginningOfPeriodCf",
                  "cash and cash equivalents at end of period": "CashAndCashEquivalentsAtEndOfPeriodCf"},
}
for _names in _TAGS.values():  # "Cash flows from (used in) operating activities", the taxonomy's own label, too
    _names.update({label.removeprefix("net "): tag for label, tag in list(_names.items()) if label.startswith("net cash flows")})


def is_rendered(content: str) -> bool:
    head = content[:3_000_000]
    if "0000]" not in head:
        return False
    text = clean(html.unescape(re.sub(r"<[^>]+>", " ", head)))
    return MARKER.search(text) is not None and _CURRENCY.search(text) is not None


def _periods(text: str) -> list[Period | None]:
    """Each dated column's period; None for a date no calendar has (Televisa's Q1 2017 report: "Close Current Quarter
    2017-02-31"), never a guess at the date meant."""
    periods: list[Period | None] = []
    for start, end in _DATES.findall(text):
        try:
            periods.append(Period(date.fromisoformat(end or start), date.fromisoformat(start) if end else None))
        except ValueError:
            periods.append(None)
    return periods


def _pages(tables, first: int) -> list:
    """The statement's table and its later pages, before the next role's number: tables headed by as many dated
    columns. Not by the same dates: Televisa's Q1 2016 printout heads its balance sheet's second page with the income
    statement's ("Accumulated Current Year 2016-01-01 - 2016-03-31"); the balance check proves the pages one statement."""
    head = " ".join(cell.text for cells in tables[first].header for cell in cells)
    count, pages = len(_periods(head)), [tables[first]]
    for table in tables[first + 1:]:
        if _SECTION.search(table.context[-400:]):
            break
        heading = " ".join(cell.text for cells in table.header for cell in cells) or table.context[-300:]
        if len(_periods(heading)) != count:
            break
        pages.append(table)
    return pages


def rendered_readings(tables, content: str, *, accession: str, form: str, filed: date, document_end: date | None,
                      fiscal_year_end: str, year_offset: int) -> list[Reading]:
    text = clean(html.unescape(re.sub(r"<[^>]+>", " ", content[:3_000_000])))
    currency = _CURRENCY.search(text)
    rounding = _ROUNDING.search(text)
    unit = {"thousands": 1e3, "millions": 1e6}[rounding.group(1).lower()] if rounding else 1.0
    consolidated = _CONSOLIDATED.search(text)
    scope = "parent" if consolidated and consolidated.group(1).lower() == "no" else "consolidated"
    readings: list[Reading] = []
    for index, table in enumerate(tables):
        markers = MARKER.findall(table.context[-300:])
        if not markers or not table.header:
            continue
        kind = _KIND[markers[-1]]
        columns = [(cell.start, period) for cell in table.header[0] for period in _periods(cell.text)[:1]
                   if period is not None]
        if not columns:
            continue
        rows = [row for page in _pages(tables, index) for row in page.rows]
        for position, period in columns:
            labelled = label_period(period, fiscal_year_end, year_offset)
            if labelled is None or period.end > filed or (kind == "balance") != (period.start is None):
                continue
            lines, tagged = [], {}
            for row in rows:
                label = next((clean(cell.text) for cell in row.cells if cell.start == 0), "")
                if not label:
                    continue
                raw = next((cell.text for cell in row.cells if cell.start == position), "")
                value = number(_FOOTNOTE.sub("", raw.strip())) if raw.strip() else None
                tag = _TAGS[kind].get(label.lower()) if value is not None else None
                if tag is not None and tag not in tagged:
                    tagged[tag] = value
                concept = "ifrs-full_CashAndCashEquivalents" if tag and tag.startswith("CashAndCashEquivalentsAt") else \
                    f"ifrs-full_{tag}" if tag else "printed_" + re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
                lines.append({"line": len(lines), "concept": concept, "label": label, "level": 0,
                              "is_heading": value is None, "value": value})
            if len(tagged) < 2 or not any(not line["is_heading"] for line in lines):
                continue
            readings.append(Reading(
                statement=kind, duration=labelled[2], fiscal_year=labelled[0], fiscal_period=labelled[1],
                period_end=period.end, period_start=period.start, currency=currency.group(1) if currency else None,
                accession=accession, form=form, filed=filed, basis="IFRS", scope=scope, source="html", lines=lines, template=True,
                own=document_end is not None and abs((period.end - document_end).days) <= 7,
                checks=tagged_checks(kind, tagged, unit, tuple(line["value"] for line in lines if line["value"] is not None))))
    return readings
