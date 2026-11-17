"""Per-share figures on today's share basis (selection/splits.py)."""
from datetime import date

from statement_extractor.domain.model import Check, Reading
from statement_extractor.selection.splits import adjust_for_splits, split_ledger


def reading(filed, year, eps, shares, member=""):
    lines = [{"line": 0, "concept": "us-gaap_EarningsPerShareBasic", "label": "Basic", "level": 0, "is_heading": False, "value": eps[0]},
             {"line": 1, "concept": "us-gaap_EarningsPerShareDiluted", "label": "Diluted", "level": 0, "is_heading": False, "value": eps[1]}]
    if shares:
        lines.append({"line": 2, "concept": "us-gaap_WeightedAverageNumberOfSharesOutstandingBasic", "label": "Basic",
                      "level": 0, "is_heading": False, "value": shares})
    for line in lines:
        if member:
            line["member"] = member
    return Reading(statement="income", duration="year", fiscal_year=year, fiscal_period="FY", period_end=date(int(year), 9, 30),
                   period_start=None, currency="USD", accession=f"a{filed}", form="10-K", filed=filed, own=False,
                   source="xbrl", checks=[Check("calc", "pass")], lines=lines)


def test_a_split_is_dated_by_the_first_filing_presenting_it_and_served_figures_are_adjusted():
    # Apple: 4-for-1 in August 2020; its FY2020 10-K presents FY2019 and FY2018 divided by four, shares times four.
    readings = [reading(date(2019, 10, 30), "2019", (11.97, 11.89), 4_617e6), reading(date(2019, 10, 30), "2018", (12.01, 11.91), 4_955e6),
                reading(date(2020, 10, 30), "2019", (2.99, 2.97), 18_471e6), reading(date(2020, 10, 30), "2018", (3.00, 2.98), 19_822e6)]
    [(when, ratio, member)] = split_ledger(readings)
    assert when == date(2020, 10, 30) and abs(ratio - 4) < 0.01 and member == ""
    served = [{"statement": "income", "source_filed": "2019-10-30", "lines": [dict(line) for line in readings[0].lines]}]
    adjust_for_splits(served, [(when, 4.0, "")])
    basic, _diluted, shares = served[0]["lines"]
    assert (basic["value"], basic["printed_value"], shares["value"]) == (2.9925, 11.97, 18_468e6)


def test_a_restatement_of_earnings_alone_is_no_split():
    # Coca-Cola 2019: earnings per share restated by 0.97, share counts unchanged.
    readings = [reading(date(2018, 2, 1), "2017", (0.29, 0.29), 4_272e6), reading(date(2018, 2, 1), "2016", (1.51, 1.49), 4_317e6),
                reading(date(2019, 2, 1), "2017", (0.30, 0.30), 4_272e6), reading(date(2019, 2, 1), "2016", (1.556, 1.536), 4_317e6)]
    assert split_ledger(readings) == []


def test_every_line_a_share_or_of_shares_is_adjusted_by_its_unit():
    # Apple's FY2013, printed in its FY2015 10-K after the 2014 seven-for-one: dividends per share beside EPS, both
    # divided by four for the 2020 four-for-one. Its FY1995 "per common and common equivalent share" (primary EPS).
    lines = [{"label": "Basic", "concept": "us-gaap_EarningsPerShareBasic", "unit": "USD/share", "value": 5.72},
             {"label": "Cash dividends declared per share", "concept": "us-gaap_CommonStockDividendsPerShareDeclared", "unit": "USD/share", "value": 1.64},
             {"label": "Earnings per common and common equivalent share", "unit": "USD/share", "value": 3.45},
             {"label": "Shares used in computing earnings per share", "unit": "shares", "value": 6_477_320_000}]
    served = [{"statement": "income", "source_filed": "2015-10-28", "lines": lines,
               "other_bases": [{"source_filed": "2015-10-28", "lines": [{"label": "Basic", "unit": "USD/share", "value": 5.72}]}]}]
    adjust_for_splits(served, [(date(2014, 7, 23), 7.0, ""), (date(2020, 10, 30), 4.0, "")])
    assert [line["value"] for line in lines] == [1.43, 0.41, 0.8625, 25_909_280_000]
    assert served[0]["other_bases"][0]["lines"][0]["value"] == 1.43


def test_a_split_of_all_shares_moves_each_class():
    # Alphabet's twenty-for-one in 2022 split classes A, B and C alike; its per-class EPS lines carry their class.
    lines = [{"label": "Basic net income per share", "unit": "USD/share", "value": 113.88, "member": "Class A common stock"}]
    adjust_for_splits([{"statement": "income", "source_filed": "2022-02-02", "lines": lines}], [(date(2022, 7, 27), 20.0, "")])
    assert lines[0]["value"] == 5.694
    visa = [{"label": "Basic", "unit": "USD/share", "value": 8.0, "member": "Class B common stock"}]
    adjust_for_splits([{"statement": "income", "source_filed": "2015-01-01", "lines": visa}], [(date(2015, 11, 20), 4.0, "a class common stock")])
    assert visa[0]["value"] == 8.0  # Visa's 2015 four-for-one was its class A's alone


def test_a_split_measured_from_figures_rounded_to_the_cent():
    # Kroger's 2015 two-for-one: quarterly EPS of 0.69 printed again as 0.34 measures 2.03.
    quarter = lambda filed, eps, shares: Reading(  # noqa: E731
        statement="income", duration="quarter", fiscal_year="2014", fiscal_period="Q2", period_end=date(2014, 8, 16),
        period_start=None, currency="USD", accession=f"q{filed}", form="10-Q", filed=filed, own=False, source="xbrl",
        checks=[Check("calc", "pass")],
        lines=[{"concept": "us-gaap_EarningsPerShareBasic", "label": "Basic", "value": eps[0]},
               {"concept": "us-gaap_EarningsPerShareDiluted", "label": "Diluted", "value": eps[1]},
               {"concept": "us-gaap_WeightedAverageNumberOfSharesOutstandingBasic", "label": "Basic", "value": shares}])
    readings = [quarter(date(2014, 9, 23), (0.69, 0.68), 492e6), quarter(date(2015, 9, 22), (0.34, 0.34), 984e6)]
    assert split_ledger(readings) == [(date(2015, 9, 22), 2.0, "")]


def test_two_splits_of_one_ratio_within_a_year_stay_two():
    def at(filed, eps):
        return reading(filed, "2010", eps, None)
    readings = [at(date(2011, 1, 30), (4.0, 3.96)), at(date(2011, 8, 1), (2.0, 1.98)), at(date(2012, 4, 1), (1.0, 0.99))]
    for item in list(readings):
        other = reading(item.filed, "2009", tuple(value / 2 for value in [line["value"] for line in item.lines]), None)
        readings.append(other)
    assert [ratio for _when, ratio, _member in split_ledger(readings)] == [2.0, 2.0]
