"""The four causes the wave 3 audit found (2026-11-17), each checked on the filing's own layout."""
from types import SimpleNamespace

from kpi_extractor.ai import GroupSpec
from kpi_extractor.quality.checks import validate_groups
from kpi_extractor.quality.reasons import OFF_ROW, OUTSIDE_SEGMENT_ERA, SIGN_TURNED
from kpi_extractor.steps.release_flow import _turn_opposite_signs
from kpi_extractor.steps.segment_eras import lines_outside_segment_eras
from kpi_extractor.steps.tagged_kpis import TaggedKpiReading


def test_a_line_read_off_the_row_its_group_shares_is_held():
    # State Street's 2Q24 net flows: the asset classes are columns of one "Total flows, net" row; Cash was read from
    # the "Cash fund" row of the five-quarter table beside it.
    group = GroupSpec.model_validate({"key": "flows", "label": "AUM Net Flows by Asset Class", "kind": "metric", "kpis": [
        {"key": k, "label": label, "unit": "currency"} for k, label in
        [("equity", "Equity"), ("fixed_income", "Fixed-Income"), ("cash", "Cash"), ("total", "Total Flows, Net")]]})
    kpis = {kpi.key: kpi for kpi in group.kpis}

    def item(key, row_label, table, col, row=9):
        return SimpleNamespace(group=group, kpi=kpis[key], value=1.0, is_total=False, status="verified", notes=[],
                               locator={"table": table, "row": row, "col": col, "row_label": row_label})

    read = [item("equity", "Total flows, net", "A_T5", 1), item("fixed_income", "Total flows, net", "A_T5", 2),
            item("cash", "Cash fund", "A_T6", 1), item("total", "Total flows, net", "A_T5", 6)]
    validate_groups(read, {})
    assert [(i.kpi.key, i.status) for i in read if i.status != "verified"] == [("cash", "needs_review")]
    assert read[2].notes == [OFF_ROW]
    # Lines that are rows share no row: nothing held.
    rows = [item("equity", "Equity", "A_T5", 1, 2), item("fixed_income", "Fixed income", "A_T5", 1, 3),
            item("cash", "Cash", "A_T5", 1, 4)]
    validate_groups(rows, {})
    assert {i.status for i in rows} == {"verified"}


def test_a_row_stating_the_opposite_convention_turns_the_sign():
    # Northern Trust's Q3 2019 release: "(0.01)" under "Net Charge-offs / (Recoveries)", a net recovery.
    kpi = SimpleNamespace(label="Annualized Net Recoveries (Charge-offs) to Average Loans", unit="percent")

    def item(row_label, value, printed):
        return SimpleNamespace(kpi=kpi, value=value, notes=[],
                               locator={"row_label": row_label, "row_numbers": [printed, 0.02]})

    flipped = item("Net Charge-offs / (Recoveries) (Annualized) to Avg Loans and Leases", -0.01, -0.01)
    same = item("Annualized Net Recoveries (Charge-offs) to Avg Loans and Leases", -0.04, -0.04)
    turned_already = item("Net Charge-offs / (Recoveries) (Annualized) to Avg Loans and Leases", 0.01, -0.01)
    alone = item("Net Charge-offs (Annualized) to Average Loans and Leases", 0.03, 0.03)  # 2016: charge-offs alone
    _turn_opposite_signs([flipped, same, turned_already, alone])
    assert (flipped.value, same.value, turned_already.value, alone.value) == (0.01, -0.04, 0.01, -0.03)
    assert flipped.notes == [SIGN_TURNED] and same.notes == [] and turned_already.notes == []


def test_a_segment_line_holds_no_period_before_its_segment():
    # McDonald's: International Operated Markets tagged from 2019; 2016 tags International Lead Markets.
    def row(group, key, label, year, method="ai"):
        return {"group_key": group, "kpi_key": key, "kpi_label": label, "fiscal_year": year, "fiscal_period": "Q2",
                "method": method, "validation_status": "verified", "notes": []}

    rows = [row("segments", "internationalleadmarkets", "International Lead Markets", "2016", "xbrl"),
            row("segments", "internationaloperatedmarkets", "International Operated Markets", "2019", "xbrl"),
            row("segments", "internationaldevelopmentallicensedmarketsandcorporate", "IDL and Corporate", "2019", "xbrl"),
            row("kpi_restaurants", "iom", "International Operated Markets", "2016"),
            row("kpi_restaurants", "iom", "International Operated Markets", "2019"),
            row("kpi_restaurants", "idl", "International Developmental Licensed Markets", "2016"),
            row("kpi_other", "iom", "International Operated Markets", "2016")]  # one segment line alone: not a group of segments
    held = lines_outside_segment_eras({i: r for i, r in enumerate(rows)})
    assert [(r["group_key"], r["kpi_key"], r["fiscal_year"], r["validation_status"]) for r in held] == [
        ("kpi_restaurants", "iom", "2016", "needs_review"), ("kpi_restaurants", "idl", "2016", "needs_review")]
    assert held[0]["notes"] == [OUTSIDE_SEGMENT_ERA]


def test_a_picked_tag_must_give_a_figure_a_release_states_or_name_the_kpi():
    from kpi_extractor.readers.tagged_metrics import Tag
    rpo = Tag("us-gaap:RevenueRemainingPerformanceObligation",
              (("dim_us-gaap_StatementBusinessSegmentsAxis", "stt:SoftwareLicenseSalesSaaSMember"),), True)
    quarters = {("2022", "Q1"): None, ("2025", "Q3"): None}
    facts = {("2022", "Q1"): {}, ("2025", "Q3"): {rpo: 2.11e9}}
    # State Street: its release's $93 million backlog in Q1 2022; no report tags that quarter, and the tag names
    # another measure.
    assert TaggedKpiReading._unconfirmed(rpo, {("2022", "Q1"): 93e6}, quarters, facts.get, "Uninstalled Revenue Backlog")
    assert not TaggedKpiReading._unconfirmed(rpo, {}, quarters, facts.get, "Revenue backlog")  # no release states it
    # A quarter both give decides by the figure.
    assert not TaggedKpiReading._unconfirmed(rpo, {("2025", "Q3"): 2.11e9}, quarters, facts.get, "Uninstalled Revenue Backlog")
    assert TaggedKpiReading._unconfirmed(rpo, {("2025", "Q3"): 93e6}, quarters, facts.get, "Revenue backlog")
    # McDonald's counts by type, in its releases until 2017 and tagged from 2022: the tag names the line.
    units = Tag("us-gaap:NumberOfRestaurants", (("dim_mcd_SegmentReportingInformationBySecondarySegmentAxis", "mcd:ConventionalFranchisesMember"),
                                                ("dim_us-gaap_FranchisorDisclosureAxis", "us-gaap:FranchisedUnitsMember")), True)
    assert not TaggedKpiReading._unconfirmed(units, {("2017", "Q2"): 21317}, {("2017", "Q2"): None}, {("2017", "Q2"): {}}.get,
                                             "Conventional franchised")


def test_the_wave3_holds_are_picked_for_a_re_read():
    from kpi_extractor.ops.reread import picked_filings
    held = {"validation_status": "needs_review", "source_accession": "a",
            "notes": ["held 2026-11-17 (wave 3 audit, owner's Allow): re-read after the fix"]}
    decided = {**held, "source_accession": "b", "owner_decision": "confirmed"}
    other = {"validation_status": "needs_review", "source_accession": "c", "notes": ["scale not stated in the document"]}
    assert picked_filings("wave3-holds", {1: held, 2: decided, 3: other}) == {"a"}
