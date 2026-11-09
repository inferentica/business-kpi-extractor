"""Summary figures (multi-year tables, readers/printed/summary.py) checked against the statements before any is
served. A summary table is checked against the statements printed in its own filing, for the years they share: a year
row taken from the wrong line dates every column wrong (TSMC's FY2001 20-F would have dated its 1997 figures 1999), and
a business segment's own "selected financial data" (JPM's) prints other figures; either way the whole table is left
out. Where the filing prints no statement of its years, the table must agree with some other filing's on one of them.
A summary's older years can differ from the statements first printed for them, by right: they are restated
(reclassifications, discontinued operations, ASML's 1998 after its SVG pooling); those are counted, not rejected. Each
year's figure is the newest such table's, and it is served only for years the statements do not cover: a summary
figure never stands in for a statement."""
from __future__ import annotations

from collections import defaultdict

from ..domain.model import Reading
from .figures import agree, key_figures

_STATEMENT = {"revenue": "income", "net income": "income", "total assets": "balance", "equity": "balance",
              "long-term debt": "balance"}


def _printed(readings: list[Reading]) -> dict[tuple[str, str], list[tuple[float, str]]]:
    """Every statement reading's key figures for a fiscal year, by (fiscal year, name): (value, accession)."""
    found: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
    for reading in readings:
        if reading.status == "kept" and reading.fiscal_period == "FY" and reading.duration in ("year", "instant") \
                and reading.scope == "consolidated":
            for name, value in key_figures(reading.statement, reading.lines).items():
                found[(reading.fiscal_year, name)].append((value, reading.accession))
    return found


_ANCHORS = ("net income", "total assets")  # one meaning everywhere; "revenue" is a bank's or insurer's own definition


def _verdict(rows: list[dict], printed: dict) -> dict:
    """A table's verdict on its own filing's statements (else any filing's): contradicted when its net income or
    total assets differ, or when nothing it prints agrees. A revenue line that differs while those agree is that
    table's own definition (JPM's selected data counts revenue on its managed basis): its figures are dropped."""
    own, other = [], []
    for row in rows:
        values = printed.get((row["fiscal_year"], row["name"]), [])
        same = [value for value, accession in values if accession == row["accession"]]
        if same:
            own.append((row, any(agree(row["value"], value) for value in same)))
        elif values:
            other.append((row, any(agree(row["value"], value) for value, _ in values)))
    checks = own or other
    wrong = [row for row, ok in checks if not ok]
    anchored = any(ok and row["name"] in _ANCHORS for row, ok in checks)
    if not checks:
        verdict = "unverified"
    elif any(row["name"] in _ANCHORS for row in wrong) or (wrong and not anchored):
        verdict = "contradicted"
    else:
        verdict = "verified" if own else "verified elsewhere"
    return {"verdict": verdict, "checked": len(checks), "own_filing": len(own),
            "differ": len(wrong) if verdict == "contradicted" else 0,
            "dropped": sorted({row["name"] for row in wrong}) if verdict != "contradicted" else [],
            "restated": sum(1 for _, ok in other if not ok) if own else 0,
            "years": sorted({row["fiscal_year"] for row in rows}),
            "differing": [f"{row['fiscal_year']} {row['name']}" for row in wrong][:6]}


def choose_summaries(figures: list[dict], readings: list[Reading], chosen: list[dict]) -> dict:
    """{years: [...], tables: {table: verdict}}: the figures served for years without statements, and each table's
    verdict: verified (by its own filing's statements), verified elsewhere, unverified (no statement prints its years),
    contradicted (left out)."""
    printed = _printed(readings)
    by_table: dict[str, list[dict]] = defaultdict(list)
    for figure in figures:
        by_table[figure["table"]].append(figure)
    verdicts, usable = {}, []
    for table, rows in by_table.items():
        verdicts[table] = _verdict(rows, printed)
        if verdicts[table]["verdict"] != "contradicted":
            usable.extend({**row, "verdict": verdicts[table]["verdict"]} for row in rows
                          if row["name"] not in verdicts[table]["dropped"])
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
            "tables": {verdict: verdicts.count(verdict) for verdict in ("verified", "verified elsewhere", "unverified",
                                                                        "contradicted")},
            "checked": sum(table["checked"] for table in tables), "differ": sum(table["differ"] for table in tables),
            "restated": sum(table["restated"] for table in tables)}
