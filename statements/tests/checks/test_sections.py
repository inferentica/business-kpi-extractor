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


def test_a_row_pushed_off_its_columns_by_dollar_cells_is_read():
    # TSMC's Q1 2024 6-K (0001046179-24-000061): "$ - $ (121,692)" sits three cells right of the columns; the line was
    # dropped and the investing section failed by 121,692 (round 132).
    from types import SimpleNamespace
    from statement_extractor.readers.printed.read import _figure, _shifted
    columns = [(9,), (24,)]
    cell = lambda start, text: SimpleNamespace(start=start, text=text)
    row = SimpleNamespace(figures={12: 0.0, 27: -121692.0}, cells=[cell(6, "$"), cell(12, "-"), cell(21, "$"), cell(27, "(121,692")])
    assert _shifted(row, columns) == {(9,): 0.0, (24,): -121692.0}
    assert _figure(row, (24,), columns) == -121692.0
    # Crown's Q3 2009 10-Q: weighted-average share counts three cells left of the EPS columns, no "$", stay off them.
    shares = SimpleNamespace(figures={6: 159208879.0, 21: 160006745.0}, cells=[cell(6, "159,208,879"), cell(21, "160,006,745")])
    assert _shifted(shares, columns) is None
    assert _shifted(SimpleNamespace(figures={9: 5.0, 27: 1.0}, cells=[]), columns) is None  # one already on its column


def test_a_currency_sign_in_the_figures_own_cell_is_no_part_of_the_label():
    # POSCO's Q1 2026 6-K (0001193125-26-246188): "W&#8195;&#8195;(4,465,247)" in one cell; the 2026 figure was dropped and
    # its investing section failed (round 132).
    from statement_extractor.readers.html.parse import parse_html
    html = ("<table><tr><td>Cash flows from investing activities</td><td></td><td></td></tr>"
            "<tr><td>Acquisitions of short-term financial instruments</td><td>W&#8195;&#8195;(4,465,247)</td>"
            "<td>(4,879,919)</td></tr>"
            "<tr><td>Proceeds from disposal of short-term financial instruments</td><td>5,005,638</td>"
            "<td>4,299,071</td></tr></table>")
    rows = [row for table in parse_html(html) for row in table.rows if row.figures]
    assert rows[0].label == "Acquisitions of short-term financial instruments"
    assert sorted(rows[0].figures.values()) == [-4879919.0, -4465247.0]


def test_gains_printed_as_plain_figures_are_taken_away():
    # Woori's Q3 2012 6-K (0001193125-12-484634), 2011's column in millions: among the adjustments, losses and gains both
    # print as plain figures; among financing, "Net decrease in hedging derivatives 3,425" too.
    def statement(gain):
        return [heading("Cash flows from operating activities:")] + lines(("Net income", 2012071)) + \
            [heading("Adjustments:")] + \
            lines(("Income tax expense", 710342), ("Interest income", -11144145), ("Interest expense", 5746246),
                  ("Dividend income", -160477), ("Impairment loss on credit loss", 1685883),
                  ("Loss on valuation of associates investments", 12344), ("Loss on foreign exchange translation", 34092),
                  ("Loss on disposal of premises and equipment and other assets", 5270),
                  ("Impairment loss of premises and equipment and other assets", 10334), ("Loss on transaction of derivatives", 5140),
                  ("Loss on valuation of derivatives", 640), ("Loss on fair value hedged items", 185194), ("Provisions", 58199),
                  ("Retirement benefits", 123945), ("Depreciation and amortization", 200377),
                  ("Gain on valuation of financial instruments at fair value through profit or loss", 5692),
                  ("Gain on available-for-sale financial assets", gain), ("Gain on valuation of associates investments", 69250),
                  ("Gain on foreign exchange translation", 46874),
                  ("Gain on disposal of investments in jointly controlled entities and associates", 34094),
                  ("Gain on disposal of premises and equipment and other assets", 15591),
                  ("Reversal of impairment loss of premises and equipment and other assets", 2148),
                  ("Gain on transaction of derivatives", 233), ("Gain on valuation of derivatives", 203385),
                  ("Gain on fair value hedged items", 5206), ("Reversal of provisions", 18388)) + \
            [heading("Changes in operating assets and liabilities:")] + \
            lines(("Increase in financial instruments at fair value through profit or loss", -1344340),
                  ("Increase in loans and receivables", -13989283), ("Increase in other assets", -7123807),
                  ("Increase in deposits due to customers", 7141807), ("Decrease in provision for guarantee and unused commitment", -28129),
                  ("Decrease in retirement benefit obligation", -564), ("Increase in other financial liabilities", 10575621),
                  ("Increase (decrease) in other liabilities", 184341)) + \
            [heading("Cash received from (paid for) operating activities:")] + \
            lines(("Interest income received", 11012138), ("Interest expense paid", -5377077), ("Dividends received", 160477),
                  ("Income tax paid", -484260), ("Net cash used in operating activities", -1229663)) + \
            [heading("Cash flows from financing activities:")] + \
            lines(("Increase in borrowings", 68716756), ("Issuance of debentures", 5893361), ("Payment of borrowings", -67591476),
                  ("Payment of debentures", -5041327), ("Dividends paid", -201503), ("Net decrease in hedging derivatives", 3425),
                  ("Net decrease in non-controlling interests", -455674),
                  ("Net cash (used in) provided by financing activities", 1316712))
    assert section_check(statement(1041181)).result == "pass"
    assert section_check(statement(1041811)).result == "fail"  # one gain misread


def test_the_taxonomy_template_under_other_headings_is_left_untested():
    # Volaris's 2016 Q1 column in its 2017 6-K (0001157523-17-001082): BMV's template, its payments printed positive.
    template = [heading("Cash flows (used in) provided by investing activities")] + \
        lines(("Cash flows from losing control of subsidiaries or other businesses", 0),
              ("Proceeds from disposals of rotable spare parts and pre-delivery payments", 642776),
              ("Acquisitions of rotable spare parts furniture and equipment", 196802), ("Acquisitions of intangible assets", 10606),
              ("Net cash flows from (used in) provided by investing activities", 435368))
    assert section_check(template).result == "none"


def test_a_line_that_only_seems_to_close_the_lines_above_it_hides_no_parts():
    # Azul's Q4 2025 6-K (0001292814-26-001837), R$ millions to one decimal (the check counts in tenths): "Interest paid
    # (215.6)" over its six parts, the fifth of which, (49.5), is within rounding of the 14 lines above it.
    def statement(interest):
        return [(label, level, None if value is None else round(value * 10)) for label, level, value in millions(interest)]

    def millions(interest):
        return [heading("Cash flows from operating activities")] + lines(("Net profit (loss) for the period", -1630.3)) + \
            [heading("Total non-cash adjustments")] + \
            lines(("Depreciation and amortization", 718.0), ("Unrealized derivatives", -263.2),
                  ("Exchange gain and (losses) in foreign currency", 1259.9), ("Financial income and expenses, net", 2843.8),
                  ("Provisions", 842.1), ("Result from modification of lease and provision", -1544.0), ("Other", -2071.0)) + \
            [heading("Changes in operating assets and liabilities")] + \
            lines(("Trade and other receivables", -241.7), ("Security deposits and maintenance reserves", 114.7),
                  ("Other assets", 206.0), ("Derivatives", 0.0), ("Accounts payable", -214.1), ("Salaries and benefits", 59.2),
                  ("Air traffic liability", 69.9), ("Provisions", -134.6), ("Other liabilities", 426.6), ("Interest paid", interest),
                  ("Interest on loans and financing", -47.7), ("Interest on leases", -70.1), ("Interest on leases - notes and equity", 0.0),
                  ("Interest on convertible instruments", -2.7), ("Interest on factoring credit card receivables", -49.5),
                  ("Other interest", -45.5), ("Net cash generated (used) by operating activities", 225.9))
    assert section_check(statement(-215.6)).result == "pass"
    assert section_check(statement(-251.6)).result == "fail"  # its digits misread
