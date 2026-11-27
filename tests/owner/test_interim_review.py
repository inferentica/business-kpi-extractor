"""A foreign filer's interim 6-K review without XBRL (Nebius's "Operating and Financial Review", exhibit 99.1) is its
quarter's report for the owner-locked refresh: told apart from the results 6-K by code, its two-row headers read with
their zero-width blank cells, and its Q3 nine months used for Q4. Fixtures are Nebius's own exhibits."""
import gzip
import json
from datetime import date
from pathlib import Path

import pandas as pd

from kpi_extractor.documents.document import parse_document
from kpi_extractor.owner.foreign import interim_reviews, plain_text, review_period
from kpi_extractor.owner.refresh import OwnerRefresh
from kpi_extractor.owner.resolve import ONE, _columns, _anchored, facts_of, resolve_table
from kpi_extractor.owner.sources import LineMapping, TableSource, parse_sources
from kpi_extractor.sources.base import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
SOURCES = json.loads((Path(__file__).resolve().parents[2] / "owner_sources" / "NBIS.json").read_text())
Q3_2026 = "0001104659-26-126238"
Q3_2025 = "0001104659-25-110831"


def _html(name: str) -> str:
    return gzip.open(FIXTURES / name, "rt").read()


def _review(name: str):
    return parse_document(_html(name), f"https://www.sec.gov/{name}")


REVIEW_2026 = _review("nbis_q3_2026_6k_review_ex991.htm.gz")
REVIEW_2025 = _review("nbis_q3_2025_6k_review_ex991.htm.gz")


def _interim(line: str) -> TableSource:
    group, kpi = line.split(".")
    mapping, _ = parse_sources(SOURCES)
    return mapping.line(group, kpi).interim


# --- the reader ---------------------------------------------------------------------------------------------------

def test_a_header_split_over_two_rows_with_zero_width_blank_cells_dates_every_column():
    table = _anchored(REVIEW_2026, r"revenues by operating segment")[0]
    dated = {col: (days, span) for col, (days, span, _) in _columns(table).items() if days}
    # "Three months ended September 30," over the columns, "2025 | 2026" below it, "(in millions …)" below that.
    assert dated == {2: ([date(2025, 9, 30)], "quarter"), 4: ([date(2026, 9, 30)], "quarter"),
                     8: ([date(2025, 9, 30)], "ytd"), 10: ([date(2026, 9, 30)], "ytd")}


def test_the_q3_2026_review_gives_nebius_ai_cloud_revenue_and_adjusted_ebitda():
    revenue = resolve_table(REVIEW_2026, _interim("segments.nebius"), date(2026, 9, 30), "quarter")
    ebitda = resolve_table(REVIEW_2026, _interim("segment_income.nebius_e"), date(2026, 9, 30), "quarter")
    nine = resolve_table(REVIEW_2026, _interim("segments.nebius"), date(2026, 9, 30), "ytd")
    assert (revenue.status, revenue.value) == (ONE, 973_800_000.0)
    assert (ebitda.status, ebitda.value) == (ONE, 518_200_000.0)
    assert (nine.status, nine.value) == (ONE, 1_938_400_000.0)
    assert resolve_table(REVIEW_2026, _interim("segments.elim"), date(2026, 9, 30), "quarter").value == -3_300_000.0


def test_the_2025_layout_dates_its_current_year_column_too():
    # One header row fewer, no zero-width cells, "Nebius" before its "AI cloud" name: the year in the cell under the span.
    current = resolve_table(REVIEW_2025, _interim("segments.nebius"), date(2025, 9, 30), "quarter")
    prior = resolve_table(REVIEW_2025, _interim("segments.nebius"), date(2024, 9, 30), "quarter")
    nine = resolve_table(REVIEW_2025, _interim("segment_income.nebius_e"), date(2025, 9, 30), "ytd")
    assert (current.value, prior.value, nine.value) == (131_100_000.0, 27_100_000.0, 7_200_000.0)


# --- telling the review apart ---------------------------------------------------------------------------------------

RELEASE = ("Exhibit 99.1 Nebius reports third quarter 2026 financial results Amsterdam, November 12, 2026 – Nebius Group "
           "N.V. (NASDAQ: NBIS), the AI cloud company, today announced its unaudited financial results for the three and nine "
           "months ended September 30, 2026.")
LETTER = "Exhibit 99.2 November 12, 2026 Nebius Group Letter to shareholders Q3 2026"
STATEMENTS = ("Table of Contents Exhibit 99.2 NEBIUS GROUP N.V. INDEX TO UNAUDITED CONDENSED CONSOLIDATED FINANCIAL STATEMENTS "
              "Page Unaudited Condensed Consolidated Balance Sheets as of December 31, 2025 and September 30 , 202 6")


def test_a_review_beside_its_statements_names_its_quarter_and_a_results_6k_is_none():
    review = plain_text(_html("nbis_q3_2026_6k_review_ex991.htm.gz"))
    assert review_period([review, STATEMENTS]) == date(2026, 9, 30)
    assert review_period([plain_text(_html("nbis_q3_2025_6k_review_ex991.htm.gz")), STATEMENTS]) == date(2025, 9, 30)
    # The first half of 2025 ("six months ended June 30, 2025 and 2024"): the later year is the period's.
    half = ("Exhibit 99.1 Operating and Financial Review and Prospects First Half of the Year 2025 and 2024 You should read "
            "the following discussion and analysis of our financial condition and results of operations for the six months "
            "ended June 30, 2025 and 2024 in conjunction with our unaudited condensed consolidated financial statements")
    assert review_period([half, STATEMENTS]) == date(2025, 6, 30)
    assert review_period([RELEASE, LETTER]) is None
    assert review_period([review]) is None  # a review with no statements beside it is not the quarter's report


def _six_k(accession, filed, end, role="earnings_release"):
    exhibit = type("Exhibit", (), {"url": f"https://www.sec.gov/{accession}.htm"})()
    return FilingRef(accession, "6-K", date.fromisoformat(filed), role, exhibit.url, period_end=date.fromisoformat(end),
                     exhibits=[exhibit])


def test_the_review_filed_beside_the_results_6k_becomes_the_quarters_report():
    release, review = _six_k("results", "2026-11-12", "2026-09-30"), _six_k(Q3_2026, "2026-11-12", "2026-09-30")
    texts = {"results": [RELEASE, LETTER], Q3_2026: [plain_text(_html("nbis_q3_2026_6k_review_ex991.htm.gz")), STATEMENTS]}
    found = interim_reviews([release, review], [], lambda ref: texts[ref.accession])
    assert [(ref.accession, ref.role, ref.period_end, ref.confirmed) for ref in found] == [
        (Q3_2026, "periodic_report", date(2026, 9, 30), True)]
    assert release.role == "earnings_release"  # the listing's own refs are left as they were


# --- the refresh ------------------------------------------------------------------------------------------------------

def _values(through: str):
    return [row for row in json.loads((FIXTURES / "nbis_values.json").read_text()) if row["period_end"] <= through]


def _refresh(through, documents=None, facts=None, sources=SOURCES):
    spec = {"model": "owner", "version": 1, "sources": sources}
    return OwnerRefresh("NBIS", spec, _values(through), {}, facts=lambda ref: (facts or {}).get(ref.accession),
                        document=lambda ref: (documents or {}).get(ref.accession))


def test_the_q3_2026_review_fills_the_quarter_tied_to_its_totals_even_beside_a_results_6k():
    review = _six_k(Q3_2026, "2026-11-12", "2026-09-30", role="periodic_report")
    # The results 6-K the same day, confirmed as a release: it no longer crowds the review out of the cluster.
    release = _six_k("results", "2026-11-12", "2026-09-30")
    plan = _refresh("2026-06-30", {Q3_2026: REVIEW_2026}).plan([review], [release])
    got = {f"{row['group_key']}.{row['kpi_key']}": row for row in plan.rows}
    assert {key: row["value"] / 1e6 for key, row in got.items()} == {
        "segments.nebius": 973.8, "segments.avride": 1.3, "segments.tripleten": 9.0, "segments.elim": -3.3,
        "segment_income.nebius_e": 518.2, "segment_income.avride_e": -45.5, "segment_income.tripleten_e": -6.9}
    assert {row["validation_status"] for row in got.values()} == {"verified"}
    assert got["segments.nebius"]["locator"]["total"] == 980_800_000.0
    assert got["segment_income.nebius_e"]["locator"]["total"] == 465_800_000.0
    assert {row["source_accession"] for row in got.values()} == {Q3_2026}
    assert {(row["fiscal_year"], row["fiscal_period"], row["period_end"]) for row in got.values()} == {("2026", "Q3", "2026-09-30")}
    assert plan.hand == []


SEG, CONS = "dim_us-gaap_StatementBusinessSegmentsAxis", "dim_srt_ConsolidationItemsAxis"


def _year_facts():
    """The FY 2025 20-F's segment facts (Nebius's own figures)."""
    rows = []
    for concept, by in (("us-gaap:Revenues", {"Nebius": 480.3, "Avride": 1.3, "TripleTen": 54.1}),
                        ("nbis:AdjustedEbitda", {"Nebius": 59.0, "Avride": -82.7, "TripleTen": -41.2})):
        for segment, value in by.items():
            rows.append({"concept": concept, "numeric_value": value * 1e6, "period_start": "2025-01-01",
                         "period_end": "2025-12-31", SEG: f"nbis:{segment}SegmentMember", CONS: "srt:OperatingSegmentsMember"})
        rows.append({"concept": concept, "numeric_value": round(sum(by.values()), 1) * 1e6 + (-5.9e6 if "Rev" in concept else 0),
                     "period_start": "2025-01-01", "period_end": "2025-12-31", SEG: None, CONS: None})
    rows.append({"concept": "us-gaap:Revenues", "numeric_value": -5.9e6, "period_start": "2025-01-01", "period_end": "2025-12-31",
                 SEG: None, CONS: "srt:IntersegmentEliminationMember"})
    return facts_of(pd.DataFrame(rows))


def test_q4_is_the_20f_year_less_the_q3_reviews_nine_months():
    annual = FilingRef("20-F", "20-F", date(2026, 4, 30), "periodic_report", "https://www.sec.gov/20f.htm",
                       period_end=date(2025, 12, 31))
    q3 = _six_k(Q3_2025, "2025-11-13", "2025-09-30", role="periodic_report")
    plan = _refresh("2025-09-30", {Q3_2025: REVIEW_2025}, {"20-F": _year_facts()}).plan([q3, annual], [])
    quarter = {f"{r['group_key']}.{r['kpi_key']}": round(r["value"] / 1e6, 1) for r in plan.rows if r["fiscal_period"] == "Q4"}
    assert quarter == {"segments.nebius": 214.1, "segments.avride": 0.7, "segments.tripleten": 15.1, "segments.elim": -2.2,
                       "segment_income.nebius_e": 51.8, "segment_income.avride_e": -27.7, "segment_income.tripleten_e": -9.1}
    assert {r["validation_status"] for r in plan.rows if r["fiscal_period"] == "Q4"} == {"verified"}
    assert {r["method"] for r in plan.rows if r["fiscal_period"] == "Q4"} == {"derived"}


# --- a line only a report gives, when no report comes -------------------------------------------------------------------

def _report_only():
    """NBIS's segment lines with no interim source: read only from a report."""
    sources = json.loads(json.dumps(SOURCES))
    for line in sources["lines"].values():
        line.pop("interim", None)
    for group in sources["groups"].values():
        group.pop("interim_total", None)
    return sources


def test_a_report_only_line_with_no_report_coming_goes_to_the_hand_queue_not_waiting():
    release = _six_k("results", "2026-11-12", "2026-09-30")
    plan = _refresh("2026-06-30", sources=_report_only()).plan([], [release])
    assert plan.rows == []
    queued = {(h.group_key, h.kpi_key, h.kind, h.fiscal_period) for h in plan.hand}
    assert ("segments", "nebius", "hand", "Q3") in queued and len(queued) == 7


def test_a_report_only_line_waits_where_the_filer_files_interim_reports_and_the_filing_says_so():
    release = _six_k("results", "2026-11-12", "2026-09-30")
    earlier = _six_k(Q3_2025, "2025-11-13", "2025-09-30", role="periodic_report")  # a filer of interim reports
    plan = _refresh("2026-06-30", sources=_report_only()).plan([earlier], [release])
    assert plan.hand == [] and plan.rows == []
    notes = next(outcome.notes for outcome in plan.filings if outcome.ref.accession == "results")
    assert any("7 lines wait for the Q3 2026 report" in note for note in notes)


def test_an_interim_source_falls_back_to_the_report_source_for_arms_xbrl_6ks():
    from kpi_extractor.owner.refresh import report_source
    six_k = _six_k("arm", "2026-11-04", "2026-09-30", role="periodic_report")
    xbrl = {"kind": "xbrl", "concepts": ["Revenues"], "members": {}}
    assert report_source(LineMapping.model_validate({"report": xbrl}), six_k).kind == "xbrl"
    mapping = LineMapping.model_validate({"report": xbrl, "interim": {"kind": "table", "anchor": "x", "row": "y"}})
    assert report_source(mapping, six_k).kind == "table"
    assert report_source(mapping, FilingRef("10q", "10-Q", date(2026, 11, 4), "periodic_report", "")).kind == "xbrl"
