"""The causes the 200-figure wave 3 audit found (2026-11-18)."""
from types import SimpleNamespace

from kpi_extractor.ai import GroupSpec
from kpi_extractor.documents.document import Document, Table
from kpi_extractor.quality.checks import validate_groups
from kpi_extractor.quality.reasons import PART_ROW
from kpi_extractor.readers.tagged_metrics import Tag
from kpi_extractor.rules.rate_columns import rate_column
from kpi_extractor.steps.tagged_kpis import names_measure


def test_a_tag_a_member_alone_ties_to_a_line_must_name_its_measure():
    ownership = Tag("us-gaap:MinorityInterestOwnershipPercentageByParent",
                    (("dim_srt_OwnershipAxis", "mcd:InternationalOperatedMarketsMember"),), True)
    assert not names_measure(ownership, "International Operated Markets", "Comparable Sales by Segment")
    units = Tag("us-gaap:NumberOfRestaurants", (("dim_us-gaap_FranchisorDisclosureAxis", "mcd:ConventionalFranchisesMember"),), True)
    assert names_measure(units, "Conventional franchised", "Restaurants by Ownership")
    # A tag whose member does not name the line (Capital One's delinquency rate) is left to the other checks.
    rate = Tag("us-gaap:FinancingReceivableNonaccrualPercentPastDue1", (), True)
    assert names_measure(rate, "Nonperforming Loan Rate", "Operating Metrics")
    assert names_measure("Tag(...)", "International Operated Markets", "Comparable Sales by Segment")  # a cached pick's text
    # An amount tagged for its member is that member's (KeyCorp's credit card loans), and a line naming its own measure
    # (Alphabet's "Google Cloud Backlog") is the AI's pick to make.
    cards = Tag("us-gaap:FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss",
                (("dim_us-gaap_FinancingReceivablePortfolioSegmentAxis", "key:CreditCardsMember"),), True)
    assert names_measure(cards, "Credit Cards", "Loans by Type", "currency")
    cloud = Tag("us-gaap:RevenueRemainingPerformanceObligation", (("dim_srt_ProductOrServiceAxis", "goog:GoogleCloudMember"),), True)
    assert names_measure(cloud, "Google Cloud Backlog", "Operating", "currency")


def test_a_line_read_from_the_row_of_one_of_its_parts_is_held():
    group = GroupSpec.model_validate({"key": "deposits", "label": "Average Deposits by Type", "kind": "metric", "kpis": [
        {"key": "time", "label": "Time Deposits", "unit": "currency"}, {"key": "savings", "label": "Savings Deposits", "unit": "currency"}]})
    kpis = {kpi.key: kpi for kpi in group.kpis}

    def item(key, row_label, row):
        return SimpleNamespace(group=group, kpi=kpis[key], value=1.0, is_total=False, status="verified", notes=[],
                               locator={"table": "A_T5", "row": row, "col": 1, "row_label": row_label})

    read = [item("time", "Other time deposits", 5), item("savings", "Savings deposits", 4)]
    validate_groups(read, {})
    assert [(i.kpi.key, i.status, i.notes) for i in read] == [("time", "needs_review", [PART_ROW]), ("savings", "verified", [])]
    other = item("time", "Time deposits", 6)
    validate_groups([other], {})
    assert other.status == "verified"


def test_an_amount_read_from_a_column_of_rates():
    table = Table("A_T6", "AVERAGE STATEMENT OF CONDITION - RATES EARNED AND PAID", [
        ["", "3Q21", "", "4Q21", ""],
        ["", "Average Balance", "Average Rate", "Average Balance", "Average Rate"],
        ["Securities", "112,604", "1.27", "113,330", "1.30"],
        ["Loans(3)", "32,035", "2.08", "34,425", "2.00"],
        ["Deposits", "186,805", "(0.13)", "190,100", "(0.10)"],
        ["Net interest income", "", "$490", "", "$487"],
    ])
    document = Document("https://x/exhibit992-4q21.htm", {"A_T6": table}, {}, ["A_T6"])
    assert rate_column(document, {"table": "A_T6", "row": 3, "col": 2})
    assert not rate_column(document, {"table": "A_T6", "row": 3, "col": 3})
    assert not rate_column(None, {"table": "A_T6", "row": 3, "col": 2})
