"""The gap finder (owner/finder.py) lists what a hand-built company's filings report beyond, or apart from, its owner tables.
It never writes a figure."""
import json
from datetime import date
from pathlib import Path

import pandas as pd

from kpi_extractor.owner.finder import GapFinder, missing_quarters
from kpi_extractor.owner.resolve import facts_of
from kpi_extractor.sources.base import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
MAPPINGS = Path(__file__).resolve().parents[2] / "owner_sources"
LEFT_OUT = ("rev_type", "geography")  # the two tables the hand build missed, found by the run of 2026-11-24


def _dell_without_the_missed_tables():
    values = [r for r in json.loads((FIXTURES / "dell_values.json").read_text())["before"] if r["group_key"] not in LEFT_OUT]
    sources = json.loads((MAPPINGS / "DELL.json").read_text())
    sources["lines"] = {k: v for k, v in sources["lines"].items() if k.split(".")[0] not in LEFT_OUT}
    sources["groups"] = {k: v for k, v in sources["groups"].items() if k not in LEFT_OUT}
    return GapFinder("DELL", {"model": "owner", "sources": sources}, values)


def _facts(name):
    return facts_of(pd.DataFrame(json.loads((FIXTURES / name).read_text())["facts"]))


Q2 = FilingRef("0001571996-26-000046", "10-Q", date(2026, 9, 8), "periodic_report", "https://www.sec.gov/q2.htm", period_end=date(2026, 7, 31))
FY = FilingRef("0001571996-26-000008", "10-K", date(2026, 3, 16), "periodic_report", "https://www.sec.gov/k.htm", period_end=date(2026, 1, 30))


def test_dell_products_services_and_geography_are_found_as_tables_the_owner_tables_do_not_carry():
    finder = _dell_without_the_missed_tables()
    quarter = finder.find(Q2, _facts("dell_q2fy27_10q_facts.json"))
    year = finder.find(FY, _facts("dell_fy26_10k_facts.json"))
    untracked = {(f.form if hasattr(f, "form") else f.fiscal_period, f.group_key, tuple(f.members.values()), f.value)
                 for f in quarter + year if f.kind == "untracked"}
    assert untracked == {
        ("Q2", "axis:ProductOrServiceAxis", ("ProductMember",), 41112e6), ("Q2", "axis:ProductOrServiceAxis", ("ServiceMember",), 5859e6),
        ("FY", "axis:ProductOrServiceAxis", ("ProductMember",), 90405e6), ("FY", "axis:ProductOrServiceAxis", ("ServiceMember",), 23133e6),
        ("FY", "axis:StatementGeographicalAxis", ("US",), 63140e6), ("FY", "axis:StatementGeographicalAxis", ("NonUsMember",), 50398e6)}
    every = quarter + year
    assert all(f.accession and f.concept == "Revenues" and f.period_end for f in every if f.kind == "untracked")
    # The ISG and CSG figures are the owner's lines: no repeat under another key, and none differs.
    assert not [f for f in every if any("SolutionsGroup" in m or "ClientSolutions" in m for m in f.members.values())]
    assert not [f for f in every if f.kind == "differs"]


def test_a_figure_stored_under_another_key_is_a_repeat_never_a_finding():
    finder = _dell_without_the_missed_tables()
    stored = [{**r, "group_key": "other_name", "kpi_key": "products"} for r in json.loads(
        (FIXTURES / "dell_values.json").read_text())["before"] if r["group_key"] == "rev_type" and r["kpi_key"] == "products"]
    finder.values += stored
    found = finder.find(Q2, _facts("dell_q2fy27_10q_facts.json"))
    assert [tuple(f.members.values()) for f in found if f.kind == "untracked"] == [("ServiceMember",)]


def test_segment_income_findings_are_the_reportable_segments_only():
    finder = _dell_without_the_missed_tables()
    found = finder.find(Q2, _facts("dell_q2fy27_10q_facts.json"))
    assert not [f for f in found if f.concept == "OperatingIncomeLoss"]  # Corporate's operating income is not a segment


def test_a_filed_figure_unlike_the_stored_one_is_listed_with_both():
    finder = _dell_without_the_missed_tables()
    for row in finder.values:
        if (row["group_key"], row["kpi_key"], row["fiscal_year"], row["fiscal_period"]) == ("segments", "isg", "2027", "Q2"):
            row["value"] = 31_000e6
    found = [f for f in finder.find(Q2, _facts("dell_q2fy27_10q_facts.json")) if f.kind == "differs"]
    assert [(f.group_key, f.kpi_key, f.value, f.stored_value) for f in found] == [("segments", "isg", 31782e6, 31_000e6)]


def test_quarters_missing_inside_a_line_are_listed_and_the_window_is_not_extended():
    row = lambda year, period: {"group_key": "segments", "kpi_key": "a", "fiscal_year": year, "fiscal_period": period,  # noqa: E731
                                "owner_decision": "confirmed", "validation_status": "verified"}
    found = missing_quarters([row("2025", "Q1"), row("2025", "Q2"), row("2025", "Q4"), row("2026", "Q1")])
    assert [(f.fiscal_year, f.fiscal_period, f.kind) for f in found] == [("2025", "Q3", "missing_quarter")]


def test_the_finder_writes_nothing():
    # It has no store and no control plane: findings are its only output.
    finder = _dell_without_the_missed_tables()
    assert not hasattr(finder, "store") and not hasattr(finder, "control")
