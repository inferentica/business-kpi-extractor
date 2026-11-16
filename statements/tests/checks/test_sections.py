"""Each cash flow section's total against its own lines (review T1b)."""
from statement_extractor.checks.sections import section_check


def heading(label):
    return (label, 0, None)


def lines(*pairs):
    return [(label, 0, value) for label, value in pairs]


# Oshkosh's FY1994 10-K (0000897069-94-000126), 1993's column in thousands: its cash flow statement prints net income
# $11,063, its income statement 1,063.
def oshkosh(net_income):
    return [heading("Operating activities:")] + lines(("Net income", net_income)) + \
        [heading("Adjustments to reconcile net income to net cash provided (used) by operating activities:")] + \
        lines(("Depreciation and amortization", 9420), ("Deferred income taxes", -7279),
              ("Cumulative effect of change in accounting principle", 6814),
              ("Loss on disposal of property, plant, and equipment", 377)) + \
        [heading("Changes in operating assets and liabilities:")] + \
        lines(("Receivables", -21861), ("Inventories", 30834), ("Prepaid expenses", -2956), ("Deferred charges", 4881),
              ("Accounts payable", 5886), ("Income taxes", 3943), ("Federal excise taxes", -1914),
              ("Payroll-related obligations", 154), ("Accrued warranty", 967), ("Other liabilities", 3954),
              ("Long-term liabilities", 4087), ("Total adjustments", 37307),
              ("Net cash provided (used) by operating activities", 38370)) + \
        [heading("Investing activities:")] + \
        lines(("Additions to property, plant, and equipment", -8401), ("Less amount capitalized under financing leases", 639),
              ("Net additions to property, plant, and equipment", -7762), ("Increase in other assets", -6054),
              ("Net cash used by investing activities", -13816)) + \
        [heading("Financing activities:")] + \
        lines(("Net borrowings (payments) on lines of credit", -19871), ("Sale of common stock from treasury", 2),
              ("Dividends paid", -4314), ("Net cash provided (used) by financing activities", -24183),
              ("Increase (decrease) in cash and cash equivalents", 371),
              ("Cash and cash equivalents at beginning of year", 221), ("Cash and cash equivalents at end of year", 592))


def test_a_statement_whose_section_does_not_add_up_fails():
    # Round 131 served the cash flow's 11,063 as printed: its roll-forward held, its operating section is 10,000 off.
    check = section_check(oshkosh(11063))
    assert check.result == "fail" and "operating activities 38,370.0" in check.detail
    assert section_check(oshkosh(1063)).result == "pass"


def test_a_lost_bracket_fails_its_section():
    # Review C1: "Capital expenditures   (  2,000)" read as +2,000.
    def statement(capex):
        return [heading("Cash flows from investing activities:")] + \
            lines(("Capital expenditures", capex), ("Proceeds from sale of assets", 500),
                  ("Net cash used in investing activities", -1500))
    assert section_check(statement(-2000)).result == "pass"
    assert section_check(statement(2000)).result == "fail"


def test_a_note_number_read_as_a_figure_fails_its_section():
    # CSN's 2017 20-F (0001292814-18-001534): dividends' note 20 read as the figure, the amount left blank.
    def statement(dividends):
        return [heading("Cash flows from financing activities")] + \
            lines(("Borrowings and financing raised", 534506), ("Cost of borrowing", -238),
                  ("Payment of borrowings", -1528023), ("Payment of dividends and interests on shareholder's equity", dividends),
                  ("Net cash used in financing activities", -993755))
    assert section_check(statement(0)).result == "pass"
    assert section_check(statement(20)).result == "fail"


def test_plain_outflows_and_deductions_with_their_own_totals_are_taken_away():
    # Woori's 6-Ks: lines under "Cash out-flows" and "Deductions of revenues" print as plain figures, each block with an
    # unlabelled total.
    printed = [heading("Cash in-flows from investing activities:")] + \
        lines(("Disposal of available-for-sale financial assets", 3652919), ("Repayment of held-to-maturity financial assets", 674202),
              ("Disposal of premises and equipment", 132), ("", 4327253)) + \
        [heading("Cash out-flows from investing activities:")] + \
        lines(("Acquisition of available-for-sale financial assets", 3554541), ("Acquisition of held-to-maturity financial assets", 988272),
              ("Acquisition of premises and equipment", 15230), ("Acquisition of intangible assets", 2378), ("", 4560421),
              ("Net cash used in investing activities", -233168))
    assert section_check(printed).result == "pass"


def test_a_heading_with_a_figure_of_its_own_is_a_line():
    # Alcon's Q2 2006 6-K: "Cash provided by (used in) financing activities: (1,864)", a line whose label was lost.
    printed = [heading("Financing activities")] + \
        lines(("Cash provided by (used in) financing activities:", -1864), ("Net proceeds from (repayment of) short term debt", -54),
              ("Repayment of long term debt", -4168), ("Proceeds from exercise of stock options", 501),
              ("Tax benefits from share-based payment arrangements", 490), ("Acquisition of treasury shares", -3615),
              ("Net cash from financing activities", -8710))
    assert section_check(printed).result == "pass"
    # A heading's figure equal to its lines is their total, read as a heading (Woori's parent 6-Ks).
    woori = [heading("Cash flows from operating activities:")] + \
        lines(("Net income", 501448), ("Changes in operating assets and liabilities:", -37108), ("Increase in loans", -36701),
              ("Decrease in other assets", 332), ("Decrease in other liabilities", -739), ("Net cash provided by operating activities", 464340))
    assert section_check(woori).result == "pass"


def test_lines_that_only_look_like_subtotals_are_also_read_as_lines():
    # Keurig Dr Pepper's FY2018 10-K, in millions: "Unrealized (gain) or loss on foreign currency 28" is within rounding of
    # the three lines above it.
    printed = [heading("Operating activities:")] + lines(("Net income", 589)) + \
        [heading("Adjustments to reconcile net income to net cash provided by operating activities:")] + \
        lines(("Depreciation expense", 233), ("Amortization expense", 229), ("Provision for sales returns", 54),
              ("Deferred income taxes", -81), ("Employee stock based compensation expense", 35),
              ("Loss on early extinguishment of debt", 13), ("Gain on step acquisition of unconsolidated subsidiaries", -18),
              ("Unrealized (gain) or loss on foreign currency", 28), ("Unrealized (gain) or loss on derivatives", 49),
              ("Other, net", 25)) + \
        [heading("Changes in assets and liabilities, net of effects of acquisition:")] + \
        lines(("Trade accounts receivable", 82), ("Inventories", 185), ("Income taxes receivable, prepaid and payables, net", 71),
              ("Other current and non current assets", -49), ("Accounts payable and accrued expenses", 206),
              ("Other current and non current liabilities", -38), ("Net change in operating assets and liabilities", 457),
              ("Net cash provided by operating activities", 1613))
    assert section_check(printed).result == "pass"


def test_layouts_it_cannot_read_are_left_untested():
    # BMV's forms print the IFRS taxonomy's template, amounts signed by their elements (Televisa's Q1 2016 6-K).
    template = [heading("Cash flows from (used in) investing activities")] + \
        lines(("Purchase of property, plant and equipment", 5977002), ("Proceeds from sales of property, plant and equipment", 98065),
              ("Net cash flows from (used in) investing activities", -5878937))
    assert section_check(template).result == "none"
    # A release with no section headings (CSN's 6-Ks): its sections are not tested.
    release = lines(("Purchase of property, plant and equipment", -100), ("Loans granted", -20),
                    ("Net cash used in investing activities", -150))
    assert section_check(release).result == "none"
