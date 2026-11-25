"""The backfill's derivation (owner/derive.py): a line's source is the fact or cell that gives the owner's stored figures
in their own filings, proven on every quarter checked; a figure nothing gives is listed for the owner, never guessed."""
import gzip
from datetime import date
from pathlib import Path

import pandas as pd

from kpi_extractor.documents.document import parse_document
from kpi_extractor.owner.derive import Evidence, derive_line, table_candidates, xbrl_candidates
from kpi_extractor.owner.refresh import OwnerLine
from kpi_extractor.owner.resolve import facts_of
from kpi_extractor.owner.sources import TableSource, XbrlSource

FIXTURES = Path(__file__).parent / "fixtures"
SEG, CONS = "dim_us-gaap_StatementBusinessSegmentsAxis", "dim_srt_ConsolidationItemsAxis"


def _fact(value, start, end, seg=None, cons=None):
    return {"concept": "us-gaap:Revenues", "numeric_value": value, "period_start": start, "period_end": end, SEG: seg, CONS: cons}


def _row(year, period, end, value):
    return {"group_key": "segments", "kpi_key": "isg", "fiscal_year": year, "fiscal_period": period, "period_end": end, "value": value}


def _line():
    return OwnerLine("segments", "isg", {"group_kind": "revenue_breakdown"}, date(2026, 7, 31))


def test_an_xbrl_source_is_proven_on_every_quarter_and_drops_a_wrapper_member():
    # The filer wraps the segment in OperatingSegmentsMember in one report and not in the other: the source ignores it.
    q2 = facts_of(pd.DataFrame([_fact(31782e6, "2026-05-02", "2026-07-31", "dell:InfrastructureSolutionsGroupMember", "us-gaap:OperatingSegmentsMember"),
                                _fact(46971e6, "2026-05-02", "2026-07-31")]))
    q1 = facts_of(pd.DataFrame([_fact(28000e6, "2026-01-31", "2026-05-01", "dell:InfrastructureSolutionsGroupMember")]))
    evidence = [Evidence(_row("2027", "Q2", "2026-07-31", 31782e6), None, q2, None),
                Evidence(_row("2027", "Q1", "2026-05-01", 28000e6), None, q1, None)]
    assert xbrl_candidates(evidence[0])[0] == XbrlSource(concepts=["Revenues"], members={
        "StatementBusinessSegmentsAxis": "InfrastructureSolutionsGroupMember"}, ignore=["OperatingSegmentsMember"])
    result = derive_line(_line(), [], evidence)
    assert result.mapping.report.members == {"StatementBusinessSegmentsAxis": "InfrastructureSolutionsGroupMember"}
    assert result.checked == 2 and result.problem is None


def test_a_release_cell_is_found_by_its_table_row_and_column():
    document = parse_document(gzip.open(FIXTURES / "dell_q3fy27_8k_ex991.htm.gz", "rt").read(), "https://www.sec.gov/x.htm")
    evidence = Evidence(_row("2027", "Q3", "2026-10-30", 41974e6), None, None, document)
    found = table_candidates(evidence)
    assert found and found[0].row == "Total\\ ISG\\ net\\ revenue" and found[0].scale == 1e6
    # A repeated label is pinned to its section: Dell's "Corporate and other" is in both reconciliations.
    other = table_candidates(Evidence(_row("2027", "Q3", "2026-10-30", 77e6), None, None, document))
    assert other and all(source.section and "revenue" in source.section for source in other if source.row == "Corporate\\ and\\ other")


def test_a_figure_no_fact_or_cell_gives_is_listed_for_the_owner():
    document = parse_document(gzip.open(FIXTURES / "dell_q3fy27_8k_ex991.htm.gz", "rt").read(), "https://www.sec.gov/x.htm")
    # Dell's AI orders ($60.2 billion) are a sentence in the CEO's comment, in no table.
    result = derive_line(_line(), [Evidence(_row("2027", "Q3", "2026-10-30", 60.2e9), None, None, document)], [])
    assert result.mapping is None and "no release table cell" in result.problem


def test_a_hand_written_mapping_is_checked_and_kept_never_replaced():
    document = parse_document(gzip.open(FIXTURES / "dell_q3fy27_8k_ex991.htm.gz", "rt").read(), "https://www.sec.gov/x.htm")
    mine = TableSource(anchor="Segment Information", row="Total ISG net revenue", scale=1e6)
    from kpi_extractor.owner.sources import LineMapping
    result = derive_line(_line(), [Evidence(_row("2027", "Q3", "2026-10-30", 41974e6), None, None, document)], [],
                         LineMapping(release=mine))
    assert result.mapping.release == mine and result.checked == 1
