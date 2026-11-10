from statement_extractor.checks.subtotals import subtotals


def test_an_unlabelled_subtotal_of_three_or_more_lines_sums_exactly():
    # Lloyds: "Financial assets at amortised cost" after its four lines, no "Total" in its label.
    lloyds = [("Loans and advances to banks", 0, 11333.0), ("Loans and advances to customers", 0, 450720.0),
              ("Reverse repurchase agreements", 0, 36006.0), ("Debt securities", 0, 12849.0),
              ("Financial assets at amortised cost", 0, 510908.0)]
    assert subtotals(lloyds) == {4: 0}
    # Apple's 1999 investing lines: "Other" 32 is no sum of 45 - 12 + 0 = 33, rounding allowance or not.
    apple = [("Net proceeds from property, plant, and equipment retirements", 0, 45.0),
             ("Purchase of property, plant, and equipment", 0, -12.0), ("Proceeds from sale of ARM shares", 0, 0.0),
             ("Other", 0, 32.0)]
    assert 3 not in subtotals(apple)


def test_sections_headed_by_their_kind_alone_count_as_read():
    # Brascan's 2001 releases: "CONSOLIDATED CASH FLOW FROM OPERATIONS", "FINANCING AND SHAREHOLDER DISTRIBUTIONS",
    # "INVESTING", each total unlabelled, then the cash heading's "Increase (Decrease)" and its balances.
    from statement_extractor.checks.printed import cash_check
    figures = [("CONSOLIDATED CASH FLOW FROM OPERATIONS", 0, 110.0), ("FINANCING AND SHAREHOLDER DISTRIBUTIONS", 0, None),
               ("Dividends paid", 1, -54.0), ("", 0, -346.0), ("INVESTING", 0, None), ("Other", 1, 64.0), ("", 0, 88.0),
               ("CASH AND CASH EQUIVALENTS Increase (Decrease)", 1, -148.0),
               ("CASH AND CASH EQUIVALENTS Balance, beginning of period", 1, 347.0),
               ("CASH AND CASH EQUIVALENTS Balance, end of period", 1, 199.0)]
    assert cash_check(figures).result == "pass"


def test_a_uk_gaap_balance_sheet_closes_on_total_liabilities():
    # Lloyds TSB's 2002 balance sheet: shareholders' funds sit among the liabilities; "Total liabilities" equals total
    # assets.
    from statement_extractor.checks.printed import balance_check
    figures = [("Loans and advances to customers", 1, 200.0), ("Other assets", 1, 52.0), ("Total assets", 0, 252.0),
               ("Customer accounts", 1, 230.0), ("Shareholders' funds (equity)", 1, 22.0), ("Total liabilities", 0, 252.0)]
    assert balance_check(figures).result == "pass"
    figures[-1] = ("Total liabilities", 0, 230.0)
    assert balance_check(figures).result != "pass"  # no line equal to total assets: unproven, never served


def test_parts_printed_below_their_total_count_once():
    # Copel's CVM quarterly forms: each account, then the accounts it sums.
    from statement_extractor.checks.printed import income_check
    figures = [("Profit before taxation", 0, 126363.0), ("Provision for income tax and social contribution", 0, -54877.0),
               ("Income tax", 0, -39517.0), ("Social contribution", 0, -15360.0), ("Deferred income tax", 0, 11733.0),
               ("Income tax", 0, 8519.0), ("Social contribution", 0, 3214.0), ("Minority interest", 0, -4812.0),
               ("Net income for the period", 0, 78407.0)]
    assert income_check(figures).result == "pass"
