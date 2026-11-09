"""Classify rules answer only when a filing's titles say plainly what it is."""
from kpi_extractor.rules.classify import classify_by_rules


def excerpt(*exhibits: str) -> str:
    return "Form 6-K filed 2026-07-17\n" + "\n\n".join(exhibits)


def test_a_monthly_revenue_report_or_a_meeting_notice_is_other():
    assert classify_by_rules(excerpt("EX-99.1 ex99-1.htm (): TSMC Reports June 2026 Net Revenue ...")) == "other"
    assert classify_by_rules(excerpt("6-K tsm.htm (): Notice of the 2026 Annual General Meeting of Shareholders")) == "other"
    assert classify_by_rules(excerpt("EX-99.1 a.htm (): ASML reports transactions under its current share buyback program")) == "other"


def test_a_quarters_results_or_management_report_is_earnings():
    assert classify_by_rules(excerpt("EX-99.1 a.htm (): TSMC Reports Second Quarter EPS of NT$15.36")) == "earnings"
    assert classify_by_rules(excerpt("EX-99.2 b.htm (): 2Q26 Quarterly Management Report")) == "earnings"
    assert classify_by_rules(excerpt("EX-99.1 c.htm (): ASML reports EUR 7.5 billion total net sales and Q2 results")) == "earnings"


def test_titles_that_say_both_or_neither_are_left_to_the_ai():
    assert classify_by_rules(excerpt("EX-99.1 a.htm (): Third quarter results", "EX-99.2 b.htm (): Notice of dividend payment")) is None
    assert classify_by_rules(excerpt("EX-99.1 a.htm (): Company announces new chief executive")) is None
