from datetime import date
from types import SimpleNamespace

from statement_extractor.readers.xbrl.read import read_filing

LABEL = "http://www.xbrl.org/2003/role/label"
TOTAL = "http://www.xbrl.org/2003/role/totalLabel"
START = "http://www.xbrl.org/2003/role/periodStartLabel"
END = "http://www.xbrl.org/2003/role/periodEndLabel"
NEGATED = "http://xbrl.us/us-gaap/role/label/negated"
FY09, FY08 = "duration_2008-09-28_2009-09-26", "duration_2007-09-30_2008-09-27"


def row(concept, values, level=4, abstract=False, role=None, signs=None, label="edgartools label", **extra):
    return {"concept": concept, "label": label, "values": values, "decimals": {key: -6 for key in values},
            "preferred_signs": signs or {}, "level": level, "is_abstract": abstract, "preferred_label": role, **extra}


class Statement:
    def __init__(self, role, rows):
        self.role_or_type, self.rows = role, rows

    def get_raw_data(self):
        return self.rows


def fake_xbrl():
    income = Statement("income", [
        row("us-gaap_IncomeStatementAbstract", {}, level=0, abstract=True, label="Income Statement [Abstract]"),
        row("us-gaap_StatementLineItems", {}, level=2, abstract=True, label="Statement [Line Items]"),
        row("us-gaap_SalesRevenueNet", {FY09: 36_537e6, FY08: 32_479e6}),
        row("us-gaap_CostOfGoodsSold", {FY09: 23_397e6, FY08: 21_334e6}),
        row("us-gaap_GrossProfit", {FY09: 13_140e6, FY08: 11_145e6}, role=TOTAL),
        row("us-gaap_NetIncomeLoss", {FY09: 5_704e6}, dimension=True),  # a breakdown inside the statement
    ])
    balance = Statement("balance", [
        row("us-gaap_AssetsAbstract", {}, level=3, abstract=True),
        row("us-gaap_AvailableForSaleSecuritiesCurrent", {"instant_2009-09-26": 18_201e6, "instant_2008-09-27": 10_236e6},
            level=5, label="Cash and Cash Equivalents"),
        row("us-gaap_Assets", {"instant_2009-09-26": 53_851e6, "instant_2008-09-27": 39_572e6}),
        row("us-gaap_LiabilitiesAndStockholdersEquity", {"instant_2009-09-26": 53_851e6, "instant_2008-09-27": 39_000e6}),
    ])
    cash = Statement("cash", [
        row("us-gaap_CashAndCashEquivalentsAtCarryingValue",
            {"instant_2008-09-27": 11_875e6, "instant_2009-09-26": 5_263e6, "instant_2007-09-29": 9_352e6}, role=START),
        row("us-gaap_PaymentsToAcquireProductiveAssets", {FY09: 1_144e6, FY08: 1_091e6}, role=NEGATED,
            signs={FY09: -1, FY08: -1}),
        row("us-gaap_CashAndCashEquivalentsPeriodIncreaseDecrease", {FY09: -6_612e6, FY08: 2_523e6}),
        row("us-gaap_CashAndCashEquivalentsAtCarryingValue",
            {"instant_2008-09-27": 11_875e6, "instant_2009-09-26": 5_263e6, "instant_2007-09-29": 9_352e6}, role=END),
    ])
    catalog = {
        "us-gaap_SalesRevenueNet": SimpleNamespace(labels={LABEL: "Net sales"}),
        "us-gaap_GrossProfit": SimpleNamespace(labels={LABEL: "Gross profit", TOTAL: "Gross margin"}),
        "us-gaap_AvailableForSaleSecuritiesCurrent": SimpleNamespace(labels={LABEL: "Short-term marketable securities"}),
        "us-gaap_CashAndCashEquivalentsAtCarryingValue": SimpleNamespace(labels={
            LABEL: "Cash", START: "Cash and cash equivalents, beginning of the year",
            END: "Cash and cash equivalents, end of the year"}),
        "us-gaap_PaymentsToAcquireProductiveAssets": SimpleNamespace(labels={
            LABEL: "Payments for PP&E", NEGATED: "Payment for acquisition of property, plant and equipment"}),
    }
    arcs = {"income": SimpleNamespace(all_arcs=[
        SimpleNamespace(parent_id="us-gaap_GrossProfit", child_id="us-gaap_SalesRevenueNet", weight=1.0),
        SimpleNamespace(parent_id="us-gaap_GrossProfit", child_id="us-gaap_CostOfGoodsSold", weight=-1.0)])}
    statements = SimpleNamespace(income_statement=lambda: income, balance_sheet=lambda: balance,
                                 cash_flow_statement=lambda: cash)
    facts = SimpleNamespace(to_dataframe=lambda: None)
    return SimpleNamespace(entity_info={"document_period_end_date": "2009-09-26"}, statements=statements,
                           element_catalog=catalog, calculation_trees=arcs, facts=facts)


def read():
    readings, notes = read_filing(fake_xbrl(), accession="a", form="10-K", filed=date(2009, 10, 27),
                                  fiscal_year_end="0926", year_offset=0)
    return {(r.statement, r.fiscal_year, r.duration): r for r in readings}, notes


def test_every_column_is_read_and_only_the_documents_own_period_is_own():
    readings, notes = read()
    assert set(readings) == {("income", "2009", "year"), ("income", "2008", "year"), ("balance", "2009", "instant"),
                             ("balance", "2008", "instant"), ("cash_flow", "2009", "year"), ("cash_flow", "2008", "year")}
    assert readings[("income", "2009", "year")].own and not readings[("income", "2008", "year")].own
    assert notes == []


def test_lines_carry_the_filers_own_labels_in_their_role_and_skip_scaffolding_and_breakdowns():
    readings, _ = read()
    income = readings[("income", "2009", "year")]
    assert [line["label"] for line in income.lines] == ["Net sales", "edgartools label", "Gross margin"]
    assert [line["level"] for line in income.lines] == [0, 0, 0]
    balance = readings[("balance", "2009", "instant")]
    assert balance.lines[1]["label"] == "Short-term marketable securities"


def test_flows_show_signs_as_presented_and_open_with_the_prior_closing_balance():
    readings, _ = read()
    cash = readings[("cash_flow", "2009", "year")]
    values = [(line["label"], line["value"]) for line in cash.lines]
    assert values[0] == ("Cash and cash equivalents, beginning of the year", 11_875e6)
    assert values[1] == ("Payment for acquisition of property, plant and equipment", -1_144e6)
    assert values[-1] == ("Cash and cash equivalents, end of the year", 5_263e6)
    assert cash.status == "kept"


def test_arithmetic_decides_what_is_kept():
    readings, _ = read()
    assert readings[("income", "2009", "year")].status == "kept"
    assert readings[("balance", "2009", "instant")].status == "kept"
    assert readings[("balance", "2008", "instant")].status == "rejected"  # 39,572 vs 39,000


def test_closing_cash_is_the_opening_line_shown_again_not_a_reconciliation_after_it():
    # Coca-Cola: cash, cash equivalents and restricted cash close the statement, then "less restricted cash" leads to
    # the balance sheet's cash and cash equivalents.
    x = fake_xbrl()
    cash = x.statements.cash_flow_statement()
    total = "us-gaap_CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
    for line in cash.rows:
        if line["concept"] == "us-gaap_CashAndCashEquivalentsAtCarryingValue":
            line["concept"] = total
    cash.rows.append(row("us-gaap_CashAndCashEquivalentsAtCarryingValue",
                         {"instant_2009-09-26": 5_000e6, "instant_2008-09-27": 11_000e6}))
    readings, _ = read_filing(x, accession="a", form="10-K", filed=date(2009, 10, 27), fiscal_year_end="0926", year_offset=0)
    flows = next(r for r in readings if r.statement == "cash_flow" and r.own)
    assert flows.status == "kept"
    assert flows.lines[-1]["value"] == 5_000e6  # still shown, at the period's end


def test_a_figure_only_tagged_for_part_of_the_company_is_not_a_line():
    import pandas as pd
    x = fake_xbrl()
    facts = []
    for statement in (x.statements.income_statement(), x.statements.balance_sheet(), x.statements.cash_flow_statement()):
        for item in statement.rows:
            for key in item["values"]:
                facts.append({"concept": item["concept"].replace("_", ":", 1), "period_key": key, "is_dimensioned": False})
    # JPM: a memo line of consolidated VIEs' liabilities, tagged only with the VIE member.
    x.statements.balance_sheet().rows.append(row("us-gaap_OtherLiabilities", {"instant_2009-09-26": 799e6}))
    facts.append({"concept": "us-gaap:OtherLiabilities", "period_key": "instant_2009-09-26", "is_dimensioned": True})
    x.facts = SimpleNamespace(to_dataframe=lambda: pd.DataFrame(facts))
    readings, _ = read_filing(x, accession="a", form="10-K", filed=date(2009, 10, 27), fiscal_year_end="0926", year_offset=0)
    balance = next(r for r in readings if r.statement == "balance" and r.own)
    assert "us-gaap_OtherLiabilities" not in {line["concept"] for line in balance.lines}


def test_a_flow_statements_balance_tagged_under_a_stray_dimension_still_opens_and_closes_it():
    import pandas as pd
    x = fake_xbrl()
    facts = []
    for statement in (x.statements.income_statement(), x.statements.balance_sheet(), x.statements.cash_flow_statement()):
        for item in statement.rows:
            for key in item["values"]:
                stray = item["concept"] == "us-gaap_CashAndCashEquivalentsAtCarryingValue"  # Prudential 2026
                facts.append({"concept": item["concept"].replace("_", ":", 1), "period_key": key, "is_dimensioned": stray})
    x.facts = SimpleNamespace(to_dataframe=lambda: pd.DataFrame(facts))
    readings, _ = read_filing(x, accession="a", form="10-K", filed=date(2009, 10, 27), fiscal_year_end="0926", year_offset=0)
    flows = next(r for r in readings if r.statement == "cash_flow" and r.own)
    assert flows.status == "kept"


def test_every_beginning_balance_opens_the_period_and_the_main_one_shown_again_closes_it():
    # TSMC 2015: cash held for sale at the beginning, then cash at the beginning; cash at the end closes.
    x = fake_xbrl()
    cash = x.statements.cash_flow_statement()
    cash.rows.insert(0, row("ifrs-full_CashAndCashEquivalentsClassifiedAsPartOfDisposalGroupHeldForSale",
                            {"instant_2008-09-27": 100e6}, role=START))
    for line in cash.rows:
        if line["concept"] == "us-gaap_CashAndCashEquivalentsPeriodIncreaseDecrease":
            line["values"][FY09] = -6_712e6  # 11,875 + 100 - 6,712 = 5,263
    readings, _ = read_filing(x, accession="a", form="10-K", filed=date(2009, 10, 27), fiscal_year_end="0926", year_offset=0)
    flows = next(r for r in readings if r.statement == "cash_flow" and r.own)
    assert flows.status == "kept"


def test_a_balance_sheet_ends_at_total_liabilities_and_equity():
    x = fake_xbrl()
    x.statements.balance_sheet().rows.append(row("jpm_LimitedProgramWideCreditEnhancement", {"instant_2009-09-26": 2_000e6}))
    readings, _ = read_filing(x, accession="a", form="10-K", filed=date(2009, 10, 27), fiscal_year_end="0926", year_offset=0)
    balance = next(r for r in readings if r.statement == "balance" and r.own)
    assert balance.lines[-1]["concept"] == "us-gaap_LiabilitiesAndStockholdersEquity"


def _member(axis, member, label):
    return [{"dimension": axis, "member": member, "member_label": label}]


def test_a_breakdown_is_a_line_only_where_the_statement_prints_it():
    # Apple prints net sales of products and services; edgartools also attaches its revenue by segment from a note.
    x = fake_xbrl()
    income = x.statements.income_statement()
    income.rows[3:3] = [
        row("us-gaap_SalesRevenueNet", {FY09: 30_000e6}, level=5, label="Products", is_dimension=True,
            dimension_metadata=_member("srt:ProductOrServiceAxis", "us-gaap_ProductMember", "Products")),
        row("us-gaap_SalesRevenueNet", {FY09: 6_537e6}, level=5, label="Services", is_dimension=True,
            dimension_metadata=_member("srt:ProductOrServiceAxis", "us-gaap_ServiceMember", "Services")),
        row("us-gaap_SalesRevenueNet", {FY09: 99e6}, level=5, label="Americas", is_dimension=True,
            dimension_metadata=_member("us-gaap:StatementBusinessSegmentsAxis", "aapl_AmericasSegmentMember", "Americas")),
    ]
    x.presentation_trees = {"income": SimpleNamespace(all_nodes={
        "us-gaap_SalesRevenueNet": 1, "srt_ProductOrServiceAxis": 1, "us-gaap_ProductMember": 1, "us-gaap_ServiceMember": 1})}
    readings, _ = read_filing(x, accession="a", form="10-K", filed=date(2009, 10, 27), fiscal_year_end="0926", year_offset=0)
    income = next(r for r in readings if r.statement == "income" and r.own)
    shown = [(line["label"], line.get("member"), line["value"]) for line in income.lines if line["concept"] == "us-gaap_SalesRevenueNet"]
    assert shown == [("Net sales", None, 36_537e6), ("Products", "Products", 30_000e6), ("Services", "Services", 6_537e6)]
    assert income.status == "kept"  # breakdowns stay out of the arithmetic


def test_a_balance_broken_down_in_a_flow_statement_opens_or_closes_with_its_line():
    # KKR 2021: cash at the beginning and at the end of the period, each by segment.
    x = fake_xbrl()
    cash = x.statements.cash_flow_statement()
    segment = _member("us-gaap:StatementBusinessSegmentsAxis", "kkr_InsuranceSegmentMember", "Insurance")
    balances = {"instant_2008-09-27": 1_000e6, "instant_2009-09-26": 2_000e6, "instant_2007-09-29": 500e6}
    cash.rows.insert(1, row("us-gaap_CashAndCashEquivalentsAtCarryingValue", dict(balances), level=5, role=TOTAL,
                            label="Insurance", is_dimension=True, dimension_metadata=segment))
    cash.rows.append(row("us-gaap_CashAndCashEquivalentsAtCarryingValue", dict(balances), level=5, role=TOTAL,
                         label="Insurance", is_dimension=True, dimension_metadata=segment))
    x.presentation_trees = {"cash": SimpleNamespace(all_nodes={
        "us-gaap_StatementBusinessSegmentsAxis": 1, "kkr_InsuranceSegmentMember": 1})}
    readings, _ = read_filing(x, accession="a", form="10-K", filed=date(2009, 10, 27), fiscal_year_end="0926", year_offset=0)
    flows = next(r for r in readings if r.statement == "cash_flow" and r.own)
    assert [line["value"] for line in flows.lines if line.get("member")] == [1_000e6, 2_000e6]
    assert flows.status == "kept"
