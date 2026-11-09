"""Summary figures (multi-year tables, readers/printed/summary.py) checked against the statements before any is
served. A summary table is a witness like any filing: where it prints a year that a statement reading also prints, it
must agree with one of them (that year's own report or a later restatement), or the whole table is left out: a year
row taken from the wrong line dates every column wrong (TSMC's FY2001 20-F would have dated its 1997 figures 1999). A
table that overlaps no statement is kept and marked unverified. Each year's figure is the newest such table's, and it
is served only for years the statements do not cover: a summary figure never stands in for a statement."""
from __future__ import annotations

from collections import defaultdict

from ..domain.model import Reading
from .figures import agree, key_figures

_STATEMENT = {"revenue": "income", "net income": "income", "total assets": "balance", "equity": "balance",
              "long-term debt": "balance"}


def _printed(readings: list[Reading]) -> dict[tuple[str, str], list[float]]:
    """Every statement reading's key figures for a fiscal year, by (fiscal year, name)."""
    found: dict[tuple[str, str], list[float]] = defaultdict(list)
    for reading in readings:
        if reading.status == "kept" and reading.fiscal_period == "FY" and reading.duration in ("year", "instant") \
                and reading.scope == "consolidated":
            for name, value in key_figures(reading.statement, reading.lines).items():
                found[(reading.fiscal_year, name)].append(value)
    return found


def choose_summaries(figures: list[dict], readings: list[Reading], chosen: list[dict]) -> dict:
    """{years: [...], tables: {table: verdict}}: the figures served for years without statements, and each table's
    verdict: verified (it agrees where statements print its years), unverified (they print none), contradicted."""
    printed = _printed(readings)
    by_table: dict[str, list[dict]] = defaultdict(list)
    for figure in figures:
        by_table[figure["table"]].append(figure)
    verdicts, usable = {}, []
    for table, rows in by_table.items():
        checked = [row for row in rows if (row["fiscal_year"], row["name"]) in printed]
        wrong = [row for row in checked if not any(agree(row["value"], value)
                                                   for value in printed[(row["fiscal_year"], row["name"])])]
        verdict = "contradicted" if wrong else "verified" if checked else "unverified"
        verdicts[table] = {"verdict": verdict, "checked": len(checked), "differ": len(wrong),
                           "years": sorted({row["fiscal_year"] for row in rows}),
                           "differing": [f"{row['fiscal_year']} {row['name']}" for row in wrong][:6]}
        if verdict != "contradicted":
            usable.extend({**row, "verdict": verdict} for row in rows)
    covered = {(item["fiscal_year"], item["statement"]) for item in chosen
               if item["status"] == "kept" and item["fiscal_period"] == "FY" and item["duration"] in ("year", "instant")}
    newest: dict[tuple[str, str], dict] = {}
    for row in usable:
        key = (row["fiscal_year"], row["name"])
        if (row["fiscal_year"], _STATEMENT[row["name"]]) not in covered and \
                (key not in newest or row["filed"] > newest[key]["filed"]):
            newest[key] = row
    return {"years": sorted(newest.values(), key=lambda row: (row["fiscal_year"], row["name"])), "tables": verdicts}


def summary_coverage(summary_figures: dict, chosen: list[dict]) -> dict:
    """How far back the summary figures reach before the first statement, and how their tables were checked."""
    statement_years = sorted({item["fiscal_year"] for item in chosen if item["status"] == "kept"
                              and item["fiscal_period"] == "FY"})
    served = sorted({row["fiscal_year"] for row in summary_figures["years"]})
    verdicts = [table["verdict"] for table in summary_figures["tables"].values()]
    tables = summary_figures["tables"].values()
    return {"first_statement_year": statement_years[0] if statement_years else None,
            "years": served, "before_statements": [year for year in served
                                                   if not statement_years or year < statement_years[0]],
            "figures": len(summary_figures["years"]),
            "tables": {verdict: verdicts.count(verdict) for verdict in ("verified", "unverified", "contradicted")},
            "checked": sum(table["checked"] for table in tables), "differ": sum(table["differ"] for table in tables)}
