"""Fourth quarters of one vintage (selection/fourth.py)."""
from datetime import date

from statement_extractor.domain.model import Check, Reading
from statement_extractor.selection.fourth import derive_fourth_quarters


def reading(accession, filed, period, duration, statement, lines):
    return Reading(statement=statement, duration=duration, fiscal_year="2019", fiscal_period=period,
                   period_end=date(2019, 9, 28) if period == "FY" else date(2019, 6, 29), period_start=None,
                   currency="USD", accession=accession, form="10-K" if period == "FY" else "10-Q", filed=filed,
                   own=False, source="xbrl", basis="US GAAP", checks=[Check("cash" if statement == "cash_flow" else "calc", "pass")],
                   lines=[{"line": i, "concept": c, "label": label, "level": 0, "is_heading": False, "value": v}
                          for i, (c, label, v) in enumerate(lines)])


def test_a_fourth_quarter_comes_from_one_vintage_of_year_and_nine_months():
    # Apple FY2019: the nine months' newest presentation (July 2020), the year as the next 10-K gives it (October 2020),
    # not the year's newest presentation (October 2021).
    nine = reading("q3-2020", date(2020, 7, 31), "Q3", "ytd", "income", [("us-gaap_Revenues", "Net sales", 196_000e6),
                                                                          ("us-gaap_EarningsPerShareDiluted", "Diluted", 8.0)])
    year = reading("k-2020", date(2020, 10, 30), "FY", "year", "income", [("us-gaap_Revenues", "Net sales", 260_040e6),
                                                                           ("us-gaap_EarningsPerShareDiluted", "Diluted", 11.89)])
    newest = reading("k-2021", date(2021, 10, 29), "FY", "year", "income", [("us-gaap_Revenues", "Net sales", 260_174e6)])
    chosen = [{"statement": "income", "fiscal_year": "2019", "fiscal_period": "FY", "duration": "year", "status": "kept",
               "period_end": "2019-09-28", "basis": "US GAAP", "currency": "USD", "lines": newest.lines}]
    [q4] = derive_fourth_quarters(chosen, [nine, year, newest], {})
    assert (q4["fiscal_period"], q4["derived_from"]["year"], q4["derived_from"]["nine_months"]) == ("Q4", "k-2020", "q3-2020")
    assert [(line["label"], line["value"]) for line in q4["lines"]] == [("Net sales", 64_040e6)]  # no per-share difference


def test_a_cash_flows_fourth_quarter_opens_on_the_nine_months_closing_cash():
    cash = "us-gaap_CashAndCashEquivalentsAtCarryingValue"
    nine = reading("q3", date(2019, 7, 31), "Q3", "ytd", "cash_flow", [(cash, "Beginning", 25e9), ("us-gaap_NetIncomeLoss", "Net income", 41e9), (cash, "Ending", 50e9)])
    year = reading("k", date(2019, 10, 30), "FY", "year", "cash_flow", [(cash, "Beginning", 25e9), ("us-gaap_NetIncomeLoss", "Net income", 55e9), (cash, "Ending", 48e9)])
    chosen = [{"statement": "cash_flow", "fiscal_year": "2019", "fiscal_period": "FY", "duration": "year", "status": "kept",
               "period_end": "2019-09-28", "basis": "US GAAP", "currency": "USD", "lines": year.lines}]
    [q4] = derive_fourth_quarters(chosen, [nine, year], {})
    assert [line["value"] for line in q4["lines"]] == [50e9, 14e9, 48e9]


def test_a_year_recast_after_the_nine_months_or_a_misdated_nine_months_derives_nothing():
    # Vertex's 2002: the year as its 2004 10-K restates it for a sale, the nine months last printed before that.
    own_year = reading("k-2003", date(2003, 3, 1), "FY", "year", "income", [("us-gaap_Revenues", "Revenues", 160e6)])
    nine = reading("q3-2003", date(2003, 11, 1), "Q3", "ytd", "income", [("us-gaap_Revenues", "Revenues", 117e6)])
    restated = reading("k-2004", date(2004, 3, 1), "FY", "year", "income", [("us-gaap_Revenues", "Revenues", 95e6)])
    chosen = [{"statement": "income", "fiscal_year": "2019", "fiscal_period": "FY", "duration": "year", "status": "kept",
               "period_end": "2019-09-28", "basis": "US GAAP", "currency": "USD", "lines": restated.lines}]
    assert derive_fourth_quarters(chosen, [own_year, nine, restated], {}) == []
    # a "nine months" read off a three-month column (RPM's): it does not open the year
    three = reading("q3", date(2019, 7, 31), "Q3", "ytd", "income", [("us-gaap_Revenues", "Revenues", 60e9)])
    three.period_start, own_year.period_start = date(2019, 3, 31), date(2018, 9, 30)
    assert derive_fourth_quarters(chosen, [three, own_year], {}) == []


def test_a_share_count_by_its_unit_gives_no_fourth_quarter():
    # Coca-Cola's "Effect of dilutive securities (in shares)": a count by its unit, though its label names no count.
    nine = reading("q3", date(2019, 10, 28), "Q3", "ytd", "income", [("us-gaap_Revenues", "Net sales", 28_000e6),
                                                                     ("us-gaap_Dilutive", "Effect of dilutive securities", 30e6)])
    year = reading("k", date(2020, 2, 24), "FY", "year", "income", [("us-gaap_Revenues", "Net sales", 37_266e6),
                                                                    ("us-gaap_Dilutive", "Effect of dilutive securities", 30e6)])
    for item in (nine, year):
        item.lines[1]["unit"] = "shares"
    chosen = [{"statement": "income", "fiscal_year": "2019", "fiscal_period": "FY", "duration": "year", "status": "kept",
               "period_end": "2019-09-28", "basis": "US GAAP", "currency": "USD", "lines": year.lines}]
    [q4] = derive_fourth_quarters(chosen, [nine, year], {})
    assert [line["label"] for line in q4["lines"]] == ["Net sales"]


def test_repeated_keys_pair_by_occurrence_and_only_amounts_are_subtracted():
    # JPMorgan's 2007 income statement keys three lines "Net income": the amount, then basic and diluted EPS.
    from statement_extractor.selection.fourth import _quarter
    year = [{"label": "Net income", "unit": "USD", "value": 15_365e6}, {"label": "Net income", "unit": "USD/share", "value": 4.51},
            {"label": "Net income", "unit": "USD/share", "value": 4.38}]
    nine = [{"label": "Net income", "unit": "USD", "value": 12_394e6}, {"label": "Net income", "unit": "USD/share", "value": 3.63},
            {"label": "Net income", "unit": "USD/share", "value": 3.52}]
    assert [_quarter(line, index, nine, "income") for index, line in enumerate(year)] == [2_971e6, None, None]
    assert _quarter({"label": "Net income", "unit": "USD", "value": 1.0}, 0, [{"label": "Net income", "unit": "EUR", "value": 0.5}], "income") is None


def test_only_cash_is_a_balance_in_a_cash_flow():
    from statement_extractor.selection.fourth import _quarter
    nine = [{"label": "Cash at beginning of period", "unit": "USD", "value": 10.0},
            {"label": "Cash at end of period", "unit": "USD", "value": 14.0}]
    opening, closing = {"label": "Cash at beginning of period", "unit": "USD", "value": 10.0}, {"label": "Cash at end of period", "unit": "USD", "value": 20.0}
    assert (_quarter(opening, 0, nine, "cash_flow"), _quarter(closing, 1, nine, "cash_flow")) == (14.0, 20.0)
    # A flow whose key repeats (two "Other" lines) is a flow: each less the nine months' line of its occurrence.
    others = [{"label": "Other", "unit": "USD", "value": 3.0}, {"label": "Other", "unit": "USD", "value": 5.0}]
    year = [{"label": "Other", "unit": "USD", "value": 4.0}, {"label": "Other", "unit": "USD", "value": 9.0}]
    assert [_quarter(line, index, others, "cash_flow") for index, line in enumerate(year)] == [1.0, 4.0]
