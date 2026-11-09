"""Filings as witnesses of one another, and statements that must tie: a misreading set aside by the filings around
it, a restatement kept, closing cash against the balance sheet, four quarters against the year."""
from datetime import date

from statement_extractor.domain.model import Check, Reading
from statement_extractor.selection.choose import choose
from statement_extractor.selection.ties import cash_on_balance, quarters_to_year
from statement_extractor.selection.witnesses import contradicted, verdict


def income(accession, filed, net, period="FY", duration="year", own=False):
    return Reading(statement="income", duration=duration, fiscal_year="2001", fiscal_period=period,
                   period_end=date(2001, 12, 31), period_start=None, currency="TWD", accession=accession, form="20-F",
                   filed=filed, own=own, source="html", basis="ROC GAAP",
                   lines=[{"line": 0, "concept": "printed_net_income", "label": "NET INCOME", "level": 0,
                           "is_heading": False, "value": net}],
                   checks=[Check("calc", "pass")])


def test_a_misreading_between_two_filings_that_agree_is_set_aside():
    # TSMC FY2001 income: its own 20-F and the FY2003 20-F print 14,483; the FY2002 20-F was read with FY2000's 65,106.
    own = income("fy2001", date(2002, 5, 9), 14_483e6, own=True)
    misread = income("fy2002", date(2003, 6, 23), 65_106e6)
    later = income("fy2003", date(2004, 5, 28), 14_483e6)
    assert contradicted([own, misread, later]) == [misread]
    [chosen] = choose([own, misread, later])
    assert chosen["source_accession"] == "fy2003" and chosen["witness"] == "confirmed" and chosen["set_aside"]


def test_a_restatement_carried_on_by_later_filings_is_kept():
    # JPM's Q2 1995 income: 453 as first reported, 729 once the Chase merger was pooled, and so on after.
    first = income("1995", date(1995, 8, 14), 453e6, own=True)
    pooled = income("1996", date(1996, 8, 14), 729e6)
    again = income("1997", date(1997, 8, 14), 729e6)
    assert contradicted([first, pooled, again]) == []
    assert verdict(again, [first, pooled, again]) == "confirmed"
    assert verdict(pooled, [first, pooled]) == "restated" and verdict(first, [first]) == "single"


def lines(*pairs, concept="printed"):
    return [{"line": n, "concept": concept, "label": label, "level": 0, "is_heading": False, "value": value}
            for n, (label, value) in enumerate(pairs)]


def test_closing_cash_against_the_balance_sheets_cash():
    flows = {"lines": lines(("Net income", 10.0), ("Cash and due from banks and deposits with banks at the end of the period",
                                                     391_154e6))}
    bank = {"lines": lines(("Cash and due from banks", 23_873e6), ("Deposits with banks", 367_281e6), ("Loans", 9e11))}
    assert cash_on_balance(flows, bank) == "agree"  # JPM: two balance sheet lines together
    assert cash_on_balance(flows, {"lines": lines(("Cash and due from banks", 23_873e6))}) == "differ"
    restricted = {"lines": lines(("Cash, cash equivalents and restricted cash, end of period", 26_635e6))}
    assert cash_on_balance(restricted, {"lines": lines(("Cash and cash equivalents", 25_565e6))}) == "untested"


def test_four_quarters_against_the_year():
    quarters = [{"lines": lines(("Net sales", sales), ("Net income", net))}
                for sales, net in ((100.0e6, 10.0e6), (110.0e6, 11.0e6), (120.0e6, 12.0e6), (130.0e6, 13.0e6))]
    assert quarters_to_year(quarters, {"lines": lines(("Net sales", 460.0e6), ("Net income", 46.0e6))}) == "add up"
    assert quarters_to_year(quarters, {"lines": lines(("Net sales", 460.0e6), ("Net income", 48.0e6))}).startswith("differ")


def test_a_statement_in_a_currency_the_company_does_not_report_in_is_never_served():
    annual = income("fy2001", date(2002, 5, 9), 14_483e6, own=True)
    translated = income("q3", date(2001, 10, 30), 2_765e6, period="Q3", duration="ytd", own=True)
    translated.currency = "USD"
    chosen = {item["fiscal_period"]: item for item in choose([annual, translated])}
    assert chosen["FY"]["status"] == "kept" and chosen["Q3"]["status"] != "kept"
