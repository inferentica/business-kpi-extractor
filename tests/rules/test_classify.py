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


def test_routine_notices_are_other():
    assert classify_by_rules(excerpt("6-K a.htm (): Taiwan Semiconductor Manufacturing Company Limited This is to report 1) the changes in the shareholdings of our directors")) == "other"
    assert classify_by_rules(excerpt("6-K a.htm (): TSMC Sets July 3 as Record Date for Common Share Dividends")) == "other"
    assert classify_by_rules(excerpt("6-K a.htm (): TSMC Announces Pricing of US$3.5 Billion of Senior Unsecured Notes")) == "other"


def test_a_release_headline_with_its_figures_or_translated_statements_is_earnings():
    assert classify_by_rules(excerpt("EX-99.1 r.htm (EX-99.1): ASML report s €5.2 billion net sales and €1.7 billion n et income in Q3 2021")) == "earnings"
    assert classify_by_rules(excerpt("EX-99.1 pressreleasequarterlyresul.htm (EX-99.1): Q3 sales strong across the full product portfolio")) == "earnings"
    assert classify_by_rules(excerpt("EX-99.1 f.htm (EX-99.1): English Translation of Financial Statements Originally Issued in Chinese")) == "earnings"


def test_full_year_results_beside_a_buyback_are_left_to_the_ai():
    assert classify_by_rules(excerpt(
        "EX-99.1 r.htm (EXHIBIT 99.1): ASML exceeds €9 billion net sales and €2 billion net income in 2017",
        "EX-99.2 p.htm (EXHIBIT 99.2): 2018-2019 share buyback program")) is None
