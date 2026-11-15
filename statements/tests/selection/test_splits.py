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
