from statement_extractor.checks import Column, Figure, balance_check, calc_check, cash_check, rounding


def figure(value, decimals=-6):
    return Figure(float(value), decimals)


def test_rounding_is_half_a_unit_of_each_figure():
    assert rounding(figure(1), figure(1)) == 1_000_000
    assert rounding(Figure(1.0, None)) == 0.5


def test_balance_holds_and_fails():
    column = Column({"us-gaap_Assets": figure(53_851e6), "us-gaap_LiabilitiesAndStockholdersEquity": figure(53_851e6)})
    assert balance_check(column).result == "pass"
    # JPM's 2010 10-K: edgartools put a 2010-01-01 total assets in the 2009 column.
    column = Column({"us-gaap_Assets": figure(2_119_673e6), "us-gaap_LiabilitiesAndStockholdersEquity": figure(2_031_989e6)})
    assert balance_check(column).result == "fail"
    assert balance_check(Column({"us-gaap_Assets": figure(1)})).result == "none"


def test_balance_reads_ifrs_totals():
    column = Column({"ifrs-full_Assets": figure(10), "ifrs-full_EquityAndLiabilities": figure(10)})
    assert balance_check(column).result == "pass"


def test_cash_rolls_forward_with_or_without_the_exchange_effect():
    # JPM 2010: the change already includes the exchange-rate effect.
    jpm = Column({"us-gaap_CashAndCashEquivalentsPeriodIncreaseDecrease": figure(1_361e6),
                  "us-gaap_EffectOfExchangeRateOnCashAndCashEquivalents": figure(328e6)},
                 opening=figure(26_206e6), closing=figure(27_567e6))
    assert cash_check(jpm).result == "pass"
    # A change line before the exchange-rate effect.
    before = Column({"ifrs-full_IncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges": figure(778e6),
                     "ifrs-full_EffectOfExchangeRateChangesOnCashAndCashEquivalents": figure(4e6)},
                    opening=figure(1_950e6), closing=figure(2_732e6))
    assert cash_check(before).result == "pass"
    wrong = Column({"us-gaap_CashAndCashEquivalentsPeriodIncreaseDecrease": figure(100e6)},
                   opening=figure(1_000e6), closing=figure(1_200e6))
    assert cash_check(wrong).result == "fail"
    assert cash_check(Column({}, closing=figure(1))).result == "none"


def test_cash_without_a_change_line_uses_its_three_sections():
    column = Column({"us-gaap_NetCashProvidedByUsedInOperatingActivities": figure(10),
                     "us-gaap_NetCashProvidedByUsedInInvestingActivities": figure(-4),
                     "us-gaap_NetCashProvidedByUsedInFinancingActivities": figure(-1)},
                    opening=figure(20), closing=figure(25))
    assert cash_check(column).result == "pass"


def test_calculations_add_with_their_weights():
    arcs = {"us-gaap_GrossProfit": [("us-gaap_Revenues", 1.0), ("us-gaap_CostOfRevenue", -1.0)]}
    good = Column({"us-gaap_GrossProfit": figure(30e6), "us-gaap_Revenues": figure(100e6), "us-gaap_CostOfRevenue": figure(70e6)})
    assert calc_check(good, arcs).result == "pass"
    bad = Column({"us-gaap_GrossProfit": figure(32e6), "us-gaap_Revenues": figure(100e6), "us-gaap_CostOfRevenue": figure(70e6)})
    assert calc_check(bad, arcs).result == "fail"


def test_a_presented_line_without_a_figure_counts_as_nothing_but_a_line_elsewhere_stops_the_test():
    arcs = {"us-gaap_OperatingExpenses": [("us-gaap_ResearchAndDevelopmentExpense", 1.0),
                                          ("us-gaap_RestructuringCharges", 1.0)]}
    blank = Column({"us-gaap_OperatingExpenses": figure(5), "us-gaap_ResearchAndDevelopmentExpense": figure(5)},
                   blank={"us-gaap_RestructuringCharges"})
    assert calc_check(blank, arcs).result == "pass"
    elsewhere = Column({"us-gaap_OperatingExpenses": figure(9), "us-gaap_ResearchAndDevelopmentExpense": figure(5)})
    assert calc_check(elsewhere, arcs).result == "none"


def test_a_subtotal_whose_declared_lines_miss_adds_up_as_printed_with_the_lines_beside_them():
    # Prudential Q3 2021: the calculation file names 7 asset lines; assets held for sale sit beside them.
    arcs = {"us-gaap_Assets": [("us-gaap_Investments", 1.0), ("us-gaap_OtherAssets", 1.0)]}
    column = Column({"us-gaap_Investments": figure(488_790e6), "us-gaap_OtherAssets": figure(299_882e6),
                     "us-gaap_AssetsOfDisposalGroupIncludingDiscontinuedOperation": figure(143_910e6),
                     "us-gaap_Assets": figure(932_582e6)},
                    presented=[("us-gaap_Investments", "assets", 1.0),
                               ("us-gaap_AssetsOfDisposalGroupIncludingDiscontinuedOperation", "assets", 1.0),
                               ("us-gaap_OtherAssets", "assets", 1.0), ("us-gaap_Assets", "assets", 1.0)])
    check = calc_check(column, arcs)
    assert check.result == "pass" and "as printed" in check.detail
    # A line elsewhere in the statement never completes a subtotal.
    column.presented[1] = ("us-gaap_AssetsOfDisposalGroupIncludingDiscontinuedOperation", "liabilities", 1.0)
    assert calc_check(column, arcs).result == "fail"


def test_a_subtotal_with_wrong_declared_weights_adds_up_as_printed():
    # TSMC 2018: other comprehensive income not reclassified; printed signs add up, declared weights do not.
    arcs = {"ifrs-full_OCI": [("a", 1.0), ("b", 1.0), ("tax", -1.0)]}
    column = Column({"a": figure(861.2e6, -5), "b": figure(-3_309.1e6, -5), "tax": figure(-195.7e6, -5),
                     "ifrs-full_OCI": figure(-3_974.6e6, -5)},
                    presented=[("a", "oci", -1.0), ("b", "oci", 1.0), ("tax", "oci", -1.0), ("ifrs-full_OCI", "oci", 1.0)])
    assert calc_check(column, arcs).result == "pass"
