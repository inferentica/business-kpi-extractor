"""Layouts IAS 1, IAS 7 and US GAAP allow, each accepted by the checks (s4-standards, 2026-11-13)."""
from statement_extractor.checks.cash import cash_check
from statement_extractor.checks.printed import balance_check, income_check, total_check


def lines(*pairs):
    return [(label, 0, value) for label, value in pairs]


def test_expenses_by_function_and_by_nature():
    by_function = lines(("Revenue", 1000), ("Cost of sales", -600), ("Gross profit", 400), ("Distribution costs", -100),
                        ("Administrative expenses", -80), ("Operating profit", 220), ("Finance costs", -20),
                        ("Profit before tax", 200), ("Income tax expense", -50), ("Profit for the year", 150))
    by_nature = lines(("Revenue", 1000), ("Raw materials and consumables used", -450), ("Employee benefits expense", -200),
                      ("Depreciation and amortisation expense", -130), ("Operating profit", 220), ("Finance costs", -20),
                      ("Profit before tax", 200), ("Income tax expense", -50), ("Profit for the year", 150))
    for statement in (by_function, by_nature):
        assert income_check(statement).result == "pass" and total_check(statement).result != "fail"


def test_direct_and_indirect_cash_flows_with_interest_and_dividends_in_either_section():
    tail = lines(("Net cash from investing activities", -300), ("Interest paid", -20), ("Dividends paid", -40),
                 ("Net cash used in financing activities", -60), ("Net increase in cash and cash equivalents", 40),
                 ("Cash and cash equivalents at beginning of year", 100), ("Cash and cash equivalents at end of year", 140))
    direct = lines(("Cash receipts from customers", 1200), ("Cash paid to suppliers and employees", -800),
                   ("Net cash from operating activities", 400)) + tail
    indirect = lines(("Profit before tax", 200), ("Depreciation and amortisation", 130), ("Increase in trade receivables", -30),
                     ("Interest received", 10), ("Dividends received", 5), ("Income taxes paid", 85),
                     ("Net cash from operating activities", 400)) + tail
    for statement in (direct, indirect):
        assert cash_check(statement).result == "pass"
    # Interest and dividends paid as operating cash flows instead (IAS 7.33-34 lets either stand).
    moved = lines(("Cash receipts from customers", 1200), ("Cash paid to suppliers and employees", -800), ("Interest paid", -20),
                  ("Dividends paid", -40), ("Net cash from operating activities", 340), ("Net cash from investing activities", -300),
                  ("Net cash from financing activities", 0), ("Net increase in cash and cash equivalents", 40),
                  ("Cash and cash equivalents at beginning of year", 100), ("Cash and cash equivalents at end of year", 140))
    assert cash_check(moved).result == "pass"


def test_a_banks_balance_sheet_in_order_of_liquidity():
    # No current and non-current split (IAS 1.60, a bank's presentation by liquidity).
    bank = lines(("Cash and balances at central banks", 50), ("Loans and advances to customers", 600),
                 ("Financial assets at fair value through profit or loss", 250), ("Other assets", 100), ("Total assets", 1000),
                 ("Deposits from customers", 700), ("Debt securities in issue", 150), ("Other liabilities", 60),
                 ("Total liabilities", 910), ("Share capital", 40), ("Retained earnings", 50), ("Total equity", 90),
                 ("Total liabilities and equity", 1000))
    assert balance_check(bank).result == "pass" and total_check(bank).result == "pass"
