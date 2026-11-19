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


def test_a_lines_unit_decides_whether_it_adds_up():
    from statement_extractor.selection.fourth import _additive
    assert _additive({"label": "Common shares and equivalents issued (net of repurchases)", "unit": "USD"})  # Brookfield
    assert not _additive({"label": "Effect of dilutive securities", "unit": "shares"})
    assert not _additive({"label": "Net income", "unit": "USD/share"})
    assert not _additive({"label": "Weighted average shares outstanding"})  # no unit: the label decides


def test_a_count_tagged_without_its_unit_never_adds_up():
    from statement_extractor.selection.fourth import _additive
    assert not _additive({"label": "Basic", "concept": "us-gaap_WeightedAverageNumberOfSharesOutstandingBasic", "unit": "pure"})
    # Arista's "Basic" under net income available to common stockholders is dollars.
    assert _additive({"label": "Basic", "concept": "us-gaap_NetIncomeLossAvailableToCommonStockholdersBasic", "unit": "USD"})


def test_a_key_holding_several_lines_pairs_them_by_concept():
    # EOG's 2020 cash flow: the key across eras holds three investing lines, the nine months' only its section total.
    from statement_extractor.selection.fourth import _quarter
    other = {"label": "Other Investing Activities", "concept": "us-gaap_PaymentsForProceedsFromOtherInvestingActivities", "unit": "USD", "value": 0.0}
    total = {"label": "Net Cash Used in Investing Activities", "concept": "us-gaap_NetCashProvidedByUsedInInvestingActivities", "unit": "USD", "value": -3_347.5}
    nine = [{"label": "Net Cash Used in Investing Activities", "concept": "us-gaap_NetCashProvidedByUsedInInvestingActivities", "unit": "USD", "value": -2_710.6}]
    mine = [other, total]
    assert _quarter(other, 0, nine, "cash_flow", mine) is None
    assert _quarter(total, 1, nine, "cash_flow", mine) == -636.9


def test_a_key_across_eras_pairs_the_closest_label():
    from statement_extractor.selection.fourth import _quarter
    # KEPCO 2017: the nine months key two lines with the year's one; "Cash generated from operations" is not it.
    year = {"label": "Net cash flows from operating activities", "concept": "ifrs-full_CashFlowsFromUsedInOperatingActivities", "unit": "KRW", "value": 10.0}
    nine = [{"label": "Cash generated from operations", "concept": "us-gaap_NetCashProvidedByUsedInOperatingActivities", "unit": "KRW", "value": 13.0},
            {"label": "Net cash from operating activities", "concept": "us-gaap_NetCashProvidedByUsedInOperatingActivities", "unit": "KRW", "value": 9.0}]
    assert _quarter(year, 0, nine, "cash_flow", [year]) == 1.0
    # Gerdau 2022: "Net income" printed twice in the year, once in the nine months as "NET INCOME".
    first, second = ({"label": "Net income", "concept": "ifrs-full_ProfitLoss", "unit": "BRL", "value": 12.0} for _ in range(2))
    ytd = [{"label": "NET INCOME", "concept": "us-gaap_NetIncomeLoss", "unit": "BRL", "value": 10.0}]
    assert (_quarter(first, 0, ytd, "income", [first, second]), _quarter(second, 1, ytd, "income", [first, second])) == (2.0, None)


def test_a_balance_printed_without_cash_in_its_label_is_a_balance():
    # Coca-Cola's 1997 cash flow: "Balance at beginning of year" and "Balance at end of year" under its cash heading.
    from statement_extractor.selection.fourth import _quarter
    nine = [{"label": "Balance at beginning of year", "unit": "USD", "value": 1_433.0}, {"label": "Balance at end of year", "unit": "USD", "value": 1_798.0}]
    opening, closing = {"label": "Balance at beginning of year", "unit": "USD", "value": 1_433.0}, {"label": "Balance at end of year", "unit": "USD", "value": 1_737.0}
    assert (_quarter(opening, 0, nine, "cash_flow"), _quarter(closing, 0, nine, "cash_flow")) == (1_798.0, 1_737.0)


def test_a_year_on_ifrs_derives_its_fourth_quarter_from_its_regulators_forms_on_national_ifrs():
    # BrasilAgro FY2014: the 20-F's IFRS year is served; CVM's year and nine months on IFRS (CPC) pair.
    nine = reading("itr", date(2014, 5, 15), "Q3", "ytd", "income", [("cvm_3.01", "Receita", 100.0)])
    year = reading("dfp", date(2014, 9, 20), "FY", "year", "income", [("cvm_3.01", "Receita", 160.0)])
    for item in (nine, year):
        item.basis, item.source, item.form = "IFRS (CPC)", "cvm", "ITR" if item is nine else "DFP"
    nine.period_end = date(2014, 3, 31)
    year.period_end = date(2014, 6, 30)
    nine.fiscal_year = year.fiscal_year = "2014"
    served = reading("20-F", date(2014, 10, 30), "FY", "year", "income", [("ifrs-full_Revenue", "Revenue", 160.0)])
    chosen = [{"statement": "income", "fiscal_year": "2014", "fiscal_period": "FY", "duration": "year", "status": "kept",
               "period_end": "2014-06-30", "basis": "IFRS", "currency": "USD", "lines": served.lines}]
    [q4] = derive_fourth_quarters(chosen, [nine, year], {})
    assert (q4["basis"], q4["derived_from"]["year"], q4["lines"][0]["value"]) == ("IFRS (CPC)", "dfp", 60.0)


def test_the_served_years_own_basis_pairs_before_a_national_ifrs():
    # Ultrapar FY2018: its 6-K's IFRS year and nine months pair, though CVM's DFP and ITR on IFRS (CPC) are newer.
    def item(accession, filed, period, basis, value, source="html"):
        found = reading(accession, filed, period, "ytd" if period == "Q3" else "year", "income",
                        [("c", "Net revenue", value)])
        found.basis, found.source = basis, source
        return found
    nines = [item("6k-9m", date(2019, 1, 10), "Q3", "IFRS", 100.0), item("itr", date(2019, 1, 20), "Q3", "IFRS (CPC)", 100.0, "cvm")]
    years = [item("6k-fy", date(2019, 2, 20), "FY", "IFRS", 160.0), item("dfp", date(2019, 2, 25), "FY", "IFRS (CPC)", 160.0, "cvm")]
    chosen = [{"statement": "income", "fiscal_year": "2019", "fiscal_period": "FY", "duration": "year", "status": "kept",
               "period_end": "2019-09-28", "basis": "IFRS", "currency": "USD", "lines": years[0].lines}]
    [q4] = derive_fourth_quarters(chosen, nines + years, {})
    assert (q4["basis"], q4["derived_from"]["year"], q4["derived_from"]["nine_months"]) == ("IFRS", "6k-fy", "6k-9m")
