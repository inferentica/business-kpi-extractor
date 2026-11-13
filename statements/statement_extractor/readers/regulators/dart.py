"""Statements from OpenDART (sources/regulators.py): each periodic report's balance sheet, income statement and cash
flows, for the report's own periods and the earlier ones it presents again. Every line keeps its tag and its name as
filed, in Korean; a line tagged from the IFRS taxonomy ("ifrs_Revenue") gets the taxonomy's English name later
(pipeline/regulators.py). Amounts are as filed: costs and outflows positive where the report tags them so."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from ...checks.coded import tagged_checks
from ...domain.basis import K_IFRS
from ...domain.model import Reading
from ...domain.periods import Period, label_period

_STATEMENT = {"BS": "balance", "IS": "income", "CIS": "income", "CF": "cash_flow"}
_QUARTER_END = {"11013": (3, 31), "11012": (6, 30), "11014": (9, 30), "11011": (12, 31)}
_FORMS = {"11013": "DART-Q1", "11012": "DART-H1", "11014": "DART-Q3", "11011": "DART-FY"}
_UNSET = ("", "-", None)


def _amount(text) -> float | None:
    if text in _UNSET:
        return None
    try:
        return float(str(text).replace(",", ""))
    except ValueError:
        return None


def _ifrs(tag: str) -> str | None:
    """The IFRS taxonomy element a tag names: "ifrs_Revenue" to 2018, "ifrs-full_Revenue" from 2019."""
    for prefix in ("ifrs-full_", "ifrs_"):
        if tag.startswith(prefix):
            return tag[len(prefix):]
    return None


def _concept(tag: str) -> str:
    """The IFRS taxonomy's element ("ifrs-full_Revenue"); "dart_…" DART's own; untagged lines have none."""
    if _ifrs(tag):
        return "ifrs-full_" + _ifrs(tag)
    if tag == "dart_CashAndCashEquivalentsAtEndOfPeriodCf":
        return "dart_CashAndCashEquivalents"  # closing cash, the key figure two cash flows are compared on
    if tag.startswith(("dart_", "entity")):
        return tag
    return "dart_untagged"


def _columns(statement: str, report: str, year: int) -> list[tuple[str, date, date | None, bool]]:
    """(field, end, start, own) of each period a report's rows give: balance sheets at the period's end and the last
    year end; a quarter's three months and its year to date with the year before's; a year with the two before it."""
    month, day = _QUARTER_END[report]
    end, opened = date(year, month, day), date(year, 1, 1)
    prior_end = date(year - 1, month, day)
    if statement == "balance":
        columns = [("thstrm_amount", end, None, True), ("frmtrm_amount", date(year - 1, 12, 31), None, False)]
        return columns + ([("bfefrmtrm_amount", date(year - 2, 12, 31), None, False)] if report == "11011" else [])
    if report == "11011":
        return [("thstrm_amount", end, opened, True), ("frmtrm_amount", prior_end, date(year - 1, 1, 1), False),
                ("bfefrmtrm_amount", date(year - 2, 12, 31), date(year - 2, 1, 1), False)]
    quarter_start = date(year, month - 2, 1)
    if statement == "cash_flow":  # a quarterly report's cash flows are the year to date's
        return [("thstrm_amount", end, opened, True), ("frmtrm_amount", prior_end, date(year - 1, 1, 1), False)]
    columns = [("thstrm_amount", end, quarter_start, True), ("frmtrm_q_amount", prior_end, date(year - 1, month - 2, 1), False)]
    if report != "11013":
        columns += [("thstrm_add_amount", end, opened, True), ("frmtrm_add_amount", prior_end, date(year - 1, 1, 1), False)]
    return columns


def dart_readings(rows: list[dict], year: int, report: str, scope: str, fiscal_year_end: str,
                  year_offset: int) -> list[Reading]:
    """Readings of one OpenDART report for one company and scope ("CFS" group, "OFS" the company alone)."""
    by_statement: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("sj_div") in _STATEMENT:
            by_statement[_STATEMENT[row["sj_div"]]].append(row)
    if "income" in by_statement and any(row["sj_div"] == "IS" for row in by_statement["income"]):
        # A separate income statement and statement of comprehensive income: the income statement is the statement.
        by_statement["income"] = [row for row in by_statement["income"] if row["sj_div"] == "IS"]
    readings = []
    for statement, lines in by_statement.items():
        lines = sorted(lines, key=lambda row: int(row.get("ord") or 0))
        receipt = lines[0]["rcept_no"]
        for field, end, start, own in _columns(statement, report, year):
            period = Period(end, start)
            labelled = label_period(period, fiscal_year_end, year_offset)
            values = [(row, _amount(row.get(field))) for row in lines]
            if labelled is None or all(value is None for _, value in values):
                continue
            tagged = {_ifrs(row["account_id"]) or row["account_id"][5:]: value for row, value in values
                      if value is not None and (_ifrs(row["account_id"]) or row["account_id"].startswith("dart_"))}
            readings.append(Reading(
                statement=statement, duration=labelled[2], fiscal_year=labelled[0], fiscal_period=labelled[1],
                period_end=end, period_start=start, currency=lines[0].get("currency") or "KRW",
                accession=f"DART-{receipt}", form=_FORMS[report],
                filed=date(int(receipt[:4]), int(receipt[4:6]), int(receipt[6:8])),
                own=own, source="dart", basis=K_IFRS, scope="consolidated" if scope == "CFS" else "parent",
                lines=[{"line": index, "concept": _concept(row["account_id"]), "code": row["account_id"],
                        "label": row["account_nm"], "level": 0, "is_heading": value is None, "value": value}
                       for index, (row, value) in enumerate(values)],
                checks=tagged_checks(statement, tagged, 1e6, tuple(value for _, value in values if value is not None))))
    return readings


