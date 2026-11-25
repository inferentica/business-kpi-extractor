"""The owner-locked refresh (owner/refresh.py): an owner-built company gets its new period by code, into its own lines only,
never by AI, and never a change to a period the owner built."""
import gzip
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from kpi_extractor.documents.document import parse_document
from kpi_extractor.owner.guard import AiRefused, NoAi
from kpi_extractor.owner.refresh import OwnerRefresh
from kpi_extractor.owner.resolve import facts_of
from kpi_extractor.pipeline import SymbolPipeline
from kpi_extractor.sources.base import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
MAPPINGS = Path(__file__).resolve().parents[2] / "owner_sources"
SEG, PROD, CONS = "dim_us-gaap_StatementBusinessSegmentsAxis", "dim_srt_ProductOrServiceAxis", "dim_srt_ConsolidationItemsAxis"


# --- a made-up owner-built company: two segments and a backlog, by XBRL ------------------------------------------------

SOURCES = {
    "lines": {
        "segments.alpha": {"report": {"kind": "xbrl", "concepts": ["Revenues"], "members": {"StatementBusinessSegmentsAxis": "AlphaMember"}}},
        "segments.beta": {"report": {"kind": "xbrl", "concepts": ["Revenues"], "members": {"StatementBusinessSegmentsAxis": "BetaMember"}}},
        "kpi_operating.backlog": {"hand": "the backlog is in the CEO's letter", "balance": True},
    },
    "groups": {"segments": {"report_total": {"kind": "xbrl", "concepts": ["Revenues"], "members": {}}},
               "kpi_operating": {"fy_rows": False}},
}
QUARTERS = [("2026", "Q1", "2026-03-31"), ("2026", "Q2", "2026-06-30")]


def _owner_row(group, kpi, year, period, end, value, kind="revenue_breakdown", decision="confirmed", **extra):
    return {"symbol": "FAKE", "group_key": group, "kpi_key": kpi, "fiscal_year": year, "fiscal_period": period,
            "period_end": end, "group_label": "Revenue by Segment" if group == "segments" else "Operating Metrics",
            "group_kind": kind, "group_order": 0, "kpi_label": kpi.title(), "kpi_order": 0, "unit": "currency",
            "currency": "USD", "value": value, "method": "xbrl", "validation_status": "verified",
            "owner_decision": decision, "source_accession": f"acc-{year}{period}", "source_url": "https://www.sec.gov/a.htm",
            **extra}


def _stored():
    rows = []
    for year, period, end in QUARTERS:
        rows += [_owner_row("segments", "alpha", year, period, end, 600.0), _owner_row("segments", "beta", year, period, end, 400.0),
                 _owner_row("kpi_operating", "backlog", year, period, end, 50.0, kind="metric")]
    return rows


def _fact(value, start, end, **dims):
    return {"concept": "us-gaap:Revenues", "numeric_value": value, "period_type": "duration", "period_start": start,
            "period_end": end, SEG: dims.get("seg"), PROD: dims.get("prod"), CONS: dims.get("cons")}


def _q3_facts(alpha=700.0, beta=500.0, extra=None):
    rows = [_fact(alpha + beta + (extra or 0), "2026-07-01", "2026-09-30"), _fact(alpha, "2026-07-01", "2026-09-30", seg="fake:AlphaMember"),
            _fact(beta, "2026-07-01", "2026-09-30", seg="fake:BetaMember"),
            # The year to date and the year-ago quarter are in the same report: never taken for the quarter.
            _fact(3200.0, "2026-01-01", "2026-09-30"), _fact(1700.0, "2026-01-01", "2026-09-30", seg="fake:AlphaMember"),
            _fact(900.0, "2025-07-01", "2025-09-30", seg="fake:AlphaMember")]
    if extra:
        rows.append(_fact(extra, "2026-07-01", "2026-09-30", seg="fake:GammaMember"))
    return facts_of(pd.DataFrame(rows))


def _report(accession="acc-2026Q3", end=date(2026, 9, 30), form="10-Q", filed=date(2026, 10, 30)):
    return FilingRef(accession, form, filed, "periodic_report", f"https://www.sec.gov/{accession}.htm", period_end=end)


def _refresh(values=None, sources=SOURCES, facts=None, filings=None, document=None):
    spec = {"model": "owner", "version": 3, "sources": sources}
    return OwnerRefresh("FAKE", spec, _stored() if values is None else values, filings or {},
                        facts=facts or (lambda ref: _q3_facts()), document=document or (lambda ref: None))


def _keys(rows):
    return sorted((r["group_key"], r["kpi_key"], r["fiscal_year"], r["fiscal_period"]) for r in rows)


def test_a_new_10q_adds_exactly_its_quarter_on_the_owners_keys():
    plan = _refresh().plan([_report()], [])
    assert _keys(plan.rows) == [("segments", "alpha", "2026", "Q3"), ("segments", "beta", "2026", "Q3")]
    alpha = next(r for r in plan.rows if r["kpi_key"] == "alpha")
    assert alpha["value"] == 700.0 and alpha["validation_status"] == "verified" and alpha["period_end"] == "2026-09-30"
    assert alpha["locator"]["total"] == 1200.0 and alpha["group_label"] == "Revenue by Segment"
    assert "no AI" in alpha["notes"][0]
    # The backlog has no code source: it waits in the hand-reading queue, never guessed or marked N/A.
    assert [(h.group_key, h.kpi_key, h.kind) for h in plan.hand] == [("kpi_operating", "backlog", "hand")]
    assert [(f.ref.accession, f.status, f.fiscal_period) for f in plan.filings] == [("acc-2026Q3", "needs_review", "Q3")]


def test_the_pipeline_runs_an_owner_company_with_no_ai_and_changes_no_earlier_row():
    class Control:
        def __init__(self):
            self.calls = []

        def call(self, operation, **payload):
            self.calls.append((operation, payload))
            return {}

        def ai(self, *args, **kwargs):
            raise AssertionError("AI called")

    class Source:
        language = "en"

        def earnings_releases(self, *args, **kwargs):
            return []

        def tagged_facts(self, ref, archive=None):
            return SimpleNamespace(facts=SimpleNamespace(to_dataframe=lambda: pd.DataFrame([
                _fact(700.0, "2026-07-01", "2026-09-30", seg="fake:AlphaMember"),
                _fact(500.0, "2026-07-01", "2026-09-30", seg="fake:BetaMember"),
                _fact(1200.0, "2026-07-01", "2026-09-30")])), entity_info={})

    control = Control()
    pipeline = SymbolPipeline(control, "FAKE", quarters=4, force=False, log=lambda *_: None, source=Source(),
                              today=date(2026, 11, 1))
    pipeline.profile = SimpleNamespace(name="Fake", fiscal_year_end="1231", foreign=False, cik=1)
    stored = _stored()
    pipeline.store.load(stored, [{"accession": "acc-2026Q2", "status": "processed"}])
    state = {"spec": {"model": "owner", "version": 3, "sources": SOURCES}, "values": stored, "ownerRejected": []}
    result = pipeline._run_owner(state, None, [], {}, [_report()])
    assert result.owner_locked and result.ai_calls == 0 and result.hand_reads == 1
    assert not any(operation in ("ai", "ocr") for operation, _ in control.calls)
    written = [v for operation, payload in control.calls if operation == "store" for v in payload.get("values", [])]
    assert _keys(written) == [("segments", "alpha", "2026", "Q3"), ("segments", "beta", "2026", "Q3")]
    before = {(r["group_key"], r["kpi_key"], r["fiscal_year"], r["fiscal_period"]): r for r in stored}
    for key, row in before.items():
        assert dict(pipeline.store.values[key]) == row  # every earlier row exactly as it was
    queued = [h for operation, payload in control.calls if operation == "store" for h in payload.get("handReads", [])]
    assert [(h["kpi_key"], h["kind"], h["fiscal_period"]) for h in queued] == [("backlog", "hand", "Q3")]


def test_a_new_segment_is_a_basis_change_flagged_for_the_owner_not_built():
    plan = _refresh(facts=lambda ref: _q3_facts(alpha=700.0, beta=500.0, extra=80.0)).plan([_report()], [])
    assert not [r for r in plan.rows if r["group_key"] == "segments"]
    basis = [h for h in plan.hand if h.kind == "basis"]
    assert len(basis) == 1 and basis[0].group_key == "segments" and "add up to 1,200, the total is 1,280" in basis[0].reason


def test_with_no_total_to_tie_a_member_no_line_maps_is_a_basis_change():
    untied = {**SOURCES, "groups": {"kpi_operating": {"fy_rows": False}}}
    plan = _refresh(sources=untied, facts=lambda ref: _q3_facts(extra=80.0)).plan([_report()], [])
    assert not [r for r in plan.rows if r["group_key"] == "segments"]
    assert any(h.kind == "basis" and "GammaMember" in h.reason for h in plan.hand)


def test_parts_that_no_longer_add_up_are_a_basis_change():
    facts = _q3_facts(alpha=700.0, beta=500.0)
    facts = [f for f in facts if not (f.dims == frozenset() and f.start == date(2026, 7, 1))] + facts_of(pd.DataFrame(
        [_fact(1300.0, "2026-07-01", "2026-09-30")]))
    plan = _refresh(facts=lambda ref: facts).plan([_report()], [])
    assert not [r for r in plan.rows if r["group_key"] == "segments"]
    assert any(h.kind == "basis" and "add up to 1,200" in h.reason for h in plan.hand)


def test_a_mapped_member_missing_from_a_breakdown_flags_it_and_marks_nothing_na():
    facts = [f for f in _q3_facts() if ("StatementBusinessSegmentsAxis", "BetaMember") not in f.dims]
    plan = _refresh(facts=lambda ref: facts).plan([_report()], [])
    assert not [r for r in plan.rows if r["group_key"] == "segments"]
    assert any(h.kind == "basis" and "segments.beta is missing" in h.reason for h in plan.hand)


def test_an_owner_decided_period_is_read_only_and_a_differing_report_goes_to_the_hand_pass():
    stored = _stored() + [_owner_row("segments", "alpha", "2026", "Q3", "2026-09-30", 690.0),
                          _owner_row("segments", "beta", "2026", "Q3", "2026-09-30", 500.0)]
    plan = _refresh(values=stored).plan([_report()], [])
    assert plan.rows == []
    assert [(h.kpi_key, h.kind) for h in plan.hand] == [("alpha", "differs")]
    assert "the period is owner-decided: nothing in it was changed" in plan.filings[0].notes


def test_only_a_release_figure_of_the_newest_period_gives_way_to_its_report():
    release = FilingRef("rel-q3", "8-K", date(2026, 10, 20), "earnings_release", "https://www.sec.gov/r.htm", period_end=date(2026, 9, 30))
    refreshed = [_owner_row("segments", "alpha", "2026", "Q3", "2026-09-30", 690.0, decision=None, source_accession="rel-q3"),
                 _owner_row("segments", "beta", "2026", "Q3", "2026-09-30", 500.0, decision=None, source_accession="rel-q3")]
    filings = {"rel-q3": {"accession": "rel-q3", "status": "processed", "document_role": "earnings_release"}}
    plan = _refresh(values=_stored() + refreshed, filings=filings).plan([_report()], [release])
    assert _keys(plan.rows) == [("segments", "alpha", "2026", "Q3")]
    assert plan.rows[0]["value"] == 700.0 and any("replaces the earnings release's 690" in n for n in plan.rows[0]["notes"])


def test_a_10k_adds_its_fy_rows_and_q4_as_the_year_less_nine_months():
    stored = _stored() + [_owner_row("segments", k, "2026", "Q3", "2026-09-30", v) for k, v in (("alpha", 700.0), ("beta", 500.0))]
    q3 = _report()
    annual = _report("acc-2026FY", date(2026, 12, 31), "10-K", date(2027, 2, 20))
    year = facts_of(pd.DataFrame([_fact(4000.0, "2026-01-01", "2026-12-31"), _fact(2400.0, "2026-01-01", "2026-12-31", seg="fake:AlphaMember"),
                                  _fact(1600.0, "2026-01-01", "2026-12-31", seg="fake:BetaMember")]))
    nine = facts_of(pd.DataFrame([_fact(3000.0, "2026-01-01", "2026-09-30"), _fact(1900.0, "2026-01-01", "2026-09-30", seg="fake:AlphaMember"),
                                  _fact(1100.0, "2026-01-01", "2026-09-30", seg="fake:BetaMember")]))
    filings = {"acc-2026Q3": {"accession": "acc-2026Q3", "status": "processed"}}
    plan = _refresh(values=stored, filings=filings, facts=lambda ref: year if ref is annual else nine).plan([q3, annual], [])
    rows = {(r["kpi_key"], r["fiscal_period"]): r for r in plan.rows}
    assert set(rows) == {("alpha", "Q4"), ("beta", "Q4"), ("alpha", "FY"), ("beta", "FY")}
    assert rows[("alpha", "Q4")]["value"] == 500.0 and rows[("alpha", "Q4")]["method"] == "derived"
    assert rows[("alpha", "Q4")]["locator"]["total"] == 1000.0 and rows[("beta", "FY")]["value"] == 1600.0
    assert all(r["validation_status"] == "verified" for r in plan.rows)


def test_a_filing_two_quarters_ahead_is_a_gap_for_the_owner():
    plan = _refresh().plan([_report(end=date(2026, 12, 31))], [])
    assert plan.rows == [] and [h.kind for h in plan.hand] == ["gap"]


def test_sources_that_do_not_parse_send_every_line_to_the_hand_queue():
    plan = _refresh(sources={"lines": {"segments.alpha": {"report": {"kind": "xbrl"}}}}).plan([_report()], [])
    assert plan.rows == [] and {h.kind for h in plan.hand} == {"unmapped"}
    assert any("do not parse" in line for line in plan.log)


def test_the_control_plane_refuses_ai_for_an_owner_company():
    guard = NoAi(SimpleNamespace(call=lambda operation, **payload: {"ok": operation}))
    assert guard.call("store", values=[]) == {"ok": "store"}
    for attempt in (lambda: guard.call("ai"), lambda: guard.call("ocr"), lambda: guard.ai("X", "locate", "", "", False)):
        with pytest.raises(AiRefused):
            attempt()
    assert guard.refused == 3


# --- Dell's Q3 FY2027 release, replayed: 8-K 0001571996-26-000050 -----------------------------------------------------

def _dell():
    values = json.loads((FIXTURES / "dell_values.json").read_text())
    spec = {"model": "owner", "version": 2, "sources": json.loads((MAPPINGS / "DELL.json").read_text())}
    release = FilingRef("0001571996-26-000050", "8-K", date(2026, 11, 24), "earnings_release",
                        "https://www.sec.gov/Archives/edgar/data/1571996/000157199626000050/exhibit991earnings8kq3fy27.htm",
                        period_end=date(2026, 10, 29))  # as SEC's listing guesses it; the release's columns say October 30
    q2 = FilingRef("0001571996-26-000046", "10-Q", date(2026, 9, 8), "periodic_report",
                   "https://www.sec.gov/Archives/edgar/data/1571996/000157199626000046/dell-20260731.htm", period_end=date(2026, 7, 31))
    html = gzip.open(FIXTURES / "dell_q3fy27_8k_ex991.htm.gz", "rt").read()
    return values, spec, release, q2, html


def test_dell_q3_fy2027_release_adds_only_the_owners_q3_lines():
    values, spec, release, q2, html = _dell()
    document = parse_document(html, release.source_url)
    filings = {q2.accession: {"accession": q2.accession, "status": "processed", "document_role": "periodic_report"}}
    plan = OwnerRefresh("DELL", spec, values["before"], filings, facts=lambda ref: None,
                        document=lambda ref: document if ref is release else None).plan([q2], [release])
    by_hand = {(r["group_key"], r["kpi_key"]): r for r in values["q3_by_hand"]}
    written = {(r["group_key"], r["kpi_key"]): r for r in plan.rows}
    # Every figure the release's tables give, exactly as the owner entered it by hand on 2026-11-25; nothing else.
    assert set(written) == set(by_hand) - {("kpi_operating", "ai_orders"), ("kpi_operating", "ai_backlog")}
    for key, row in written.items():
        assert row["value"] == by_hand[key]["value"], key
        assert (row["fiscal_year"], row["fiscal_period"], row["period_end"]) == ("2027", "Q3", "2026-10-30")
        assert row["validation_status"] == "verified" and row["source_accession"] == release.accession
        assert (row["group_label"], row["kpi_label"]) == (by_hand[key]["group_label"], by_hand[key]["kpi_label"])
    # The AI orders and backlog are a sentence in the CEO's comment: the owner reads them, no AI does.
    assert sorted((h.kpi_key, h.kind) for h in plan.hand) == [("ai_backlog", "hand"), ("ai_orders", "hand")]
    # No other period, group or key: the 418 rows of 2026-11-24 cannot happen.
    assert {(r["fiscal_year"], r["fiscal_period"]) for r in plan.rows} == {("2027", "Q3")}
    assert {r["group_key"] for r in plan.rows} <= {r["group_key"] for r in values["before"]}


def test_dell_release_whose_reconciliation_lost_a_line_is_flagged_not_built():
    values, spec, release, q2, html = _dell()
    changed = parse_document(html.replace("Corporate and other", "Other items"), release.source_url)
    plan = OwnerRefresh("DELL", spec, values["before"], {}, facts=lambda ref: None,
                        document=lambda ref: changed).plan([], [release])
    assert not [r for r in plan.rows if r["group_key"] == "segments"]
    assert any(h.group_key == "segments" and h.kind == "basis" for h in plan.hand)
    # The other tables still tie, and still come in.
    assert {r["group_key"] for r in plan.rows} == {"isg_lines", "csg_lines", "segment_income", "rev_type"}


def test_dell_release_whose_segment_table_is_gone_builds_nothing_from_it():
    values, spec, release, q2, html = _dell()
    changed = parse_document(html.replace("Segment Information", "Business Results"), release.source_url)
    plan = OwnerRefresh("DELL", spec, values["before"], {}, facts=lambda ref: None,
                        document=lambda ref: changed).plan([], [release])
    assert {r["group_key"] for r in plan.rows} == {"rev_type"}
    assert {h.group_key for h in plan.hand if h.kind == "basis"} == {"segments", "isg_lines", "csg_lines", "segment_income"}
