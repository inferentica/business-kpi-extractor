"""Quarterly statements read from ASML's quarterly releases: the slide-text layout (figures as hidden text, nil as a
dash, the header after the rows it heads) and the older HTML tables."""
from datetime import date

from kpi_extractor.document import Document, Table, TextBlock
from kpi_extractor.release_statements import release_statements

# ASML Q2 2026, Exhibit 99.3, as the hidden text reads: the quarterly summary balance sheet (two slides), complete,
# and its cash flows abridged (fewer reconciling lines, one adjusted so the abridged statement still adds up).
BALANCE = (
    "Jun 29, Sep 28, Dec 31, Mar 29, Jun 28, (Unaudited, €, in millions) 2025 2025 2025 2026 2026 Assets Cash and cash "
    "equivalents 7,243.5 5,126.5 12,916.0 7,970.4 6,671.9 Short-term investments 5.4 1.7 405.9 405.9 909.6 Accounts "
    "receivable, net 4,996.9 5,364.3 3,023.0 4,402.9 7,252.6 Finance receivables, net 531.1 666.2 613.5 630.4 560.0 "
    "Current tax assets 98.5 93.0 88.9 65.2 84.1 Contract assets 275.3 366.7 440.6 543.7 492.0 Inventories, net "
    "11,575.8 11,762.8 11,429.3 11,711.2 11,739.5 Loans receivable 17.8 190.0 266.1 267.8 269.2 Other assets 1,966.2 "
    "1,575.1 1,432.8 1,586.3 1,570.3 Total current assets 26,710.5 25,146.3 30,616.1 27,583.8 29,549.2 Finance "
    "receivables, net 175.1 — 13.3 198.2 — Deferred tax assets 1,830.4 1,838.6 1,719.4 1,761.9 1,742.7 Loans receivable "
    "1,444.9 1,686.0 1,653.9 1,725.9 1,766.8 Other assets 843.7 918.5 1,057.1 1,320.9 1,532.3 Equity investments — "
    "1,226.9 1,320.7 1,322.5 1,324.9 Equity method investments 1,029.2 1,109.3 822.6 935.9 985.8 Goodwill 4,588.6 "
    "4,588.6 4,588.6 4,588.6 4,588.6 Other intangible assets, net 565.7 555.7 540.1 532.1 589.5 Property, plant and "
    "equipment, net 7,296.0 7,681.2 7,893.8 7,770.0 7,815.8 Right-of-use assets 365.0 346.0 341.0 320.7 319.2 Total "
    "non-current assets 18,138.6 19,950.8 19,950.5 20,476.7 20,665.6 Total assets 44,849.1 45,097.1 50,566.6 48,060.5 "
    "50,214.8 Jun 29, Sep 28, Dec 31, Mar 29, Jun 28, (Unaudited, €, in millions) 2025 2025 2025 2026 2026 Liabilities "
    "and shareholders’ equity Current liabilities 18,616.0 19,224.9 24,263.9 20,288.0 22,154.6 Total current "
    "liabilities 18,616.0 19,224.9 24,263.9 20,288.0 22,154.6 Long-term debt 3,698.9 2,704.7 2,709.0 2,705.6 1,984.4 "
    "Deferred and other tax liabilities 294.8 187.2 183.0 235.4 240.1 Contract liabilities 4,187.1 3,572.3 3,366.3 "
    "3,585.7 3,570.3 Accrued and other liabilities 434.1 414.5 432.2 416.3 440.0 Total non-current liabilities 8,614.9 "
    "6,878.7 6,690.5 6,943.0 6,234.8 Total liabilities 27,230.9 26,103.6 30,954.4 27,231.0 28,389.4 Total "
    "shareholders’ equity 17,618.2 18,993.5 19,612.2 20,829.5 21,825.4 Total liabilities and shareholders’ equity "
    "44,849.1 45,097.1 50,566.6 48,060.5 50,214.8 Summary US GAAP Quarterly Summary US GAAP Notes ASML Financial "
    "statements US GAAP Q2 2026 8 Consolidated Statements of Operations Consolidated Balance Sheets")
CASH_FLOW = (
    "Cash flows from operating activities Net income 2,290.3 2,124.5 2,839.6 2,756.7 2,917.6 Adjustments to reconcile "
    "net income to net cash flows from operating activities: Depreciation and amortization 254.9 274.6 255.2 259.4 "
    "246.9 Changes in assets and liabilities (1,797.5) (1,840.0) 8,315.5 (5,201.7) (1,461.5) Net cash provided by "
    "(used in) operating activities 747.7 559.1 11,410.3 (2,185.6) 1,703.0 Cash flows from investing activities "
    "Purchase of property, plant and equipment (414.8) (295.9) (447.9) (402.4) (299.4) Purchase of short-term "
    "investments — 3.5 (409.7) — (903.9) Net cash provided by (used in) investing activities (428.0) (1,927.8) "
    "(1,006.3) (487.6) (926.3) Three months ended Jun 29, Sep 28, Dec 31, Mar 29, Jun 28, (Unaudited,€, in millions) "
    "2025 2025 2025 2026 2026 Cash flows from financing activities Dividend paid (714.4) (619.6) (619.2) (617.0) "
    "(1,038.5) Net cash provided by (used in) financing activities (2,163.8) (742.0) (2,613.3) (2,274.9) (2,079.6) "
    "Net cash flows (1,844.1) (2,110.7) 7,790.7 (4,948.1) (1,302.9) Effect of changes in exchange rates on cash (10.8) "
    "(6.3) (1.2) 2.5 4.4 Net increase (decrease) in cash and cash equivalents (1,854.9) (2,117.0) 7,789.5 (4,945.6) "
    "(1,298.5) Cash and cash equivalents at beginning of the period 9,098.4 7,243.5 5,126.5 12,916.0 7,970.4 Cash and "
    "cash equivalents at end of the period 7,243.5 5,126.5 12,916.0 7,970.4 6,671.9 Three months ended Jun 29, Sep 28, "
    "Dec 31, Mar 29, Jun 28, (Unaudited,€, in millions) 2025 2025 2025 2026 2026 Summary US GAAP Notes Q2 2026 9")


def _text_document(*texts: str) -> Document:
    document = Document("https://example.test/release.htm")
    for index, text in enumerate(texts):
        document.blocks[f"P{index}"] = TextBlock(f"P{index}", text)
        document.order.append(f"P{index}")
    return document


def _by(statements, kind: str, end: date):
    return next(item for item in statements if item.statement == kind and item.period_end == end)


def _line(statement, label: str):
    return next(line for line in statement.lines if line["label"] == label and not line["is_heading"])


def test_a_slide_text_balance_sheet_is_read_for_each_quarter_with_its_nil_dashes():
    statements = release_statements(_text_document(BALANCE))
    assert [item.period_end for item in statements] == [date(2025, 6, 29), date(2025, 9, 28), date(2025, 12, 31),
                                                        date(2026, 3, 29), date(2026, 6, 28)]
    latest = _by(statements, "balance", date(2026, 6, 28))
    assert (latest.duration, latest.currency) == ("instant", "EUR")
    assert _line(latest, "Total assets")["value"] == 50_214_800_000
    assert _line(latest, "Total assets")["concept"] == "release_Assets"
    assert _line(latest, "Total shareholders’ equity")["value"] == 21_825_400_000
    # "—" is nil, and the same label under current and non-current assets stays two lines.
    assert [line["value"] for line in latest.lines if line["label"] == "Finance receivables, net"] == [560_000_000, 0]
    # Section titles glued to the first line under them are headings of their own.
    assert [line["label"] for line in latest.lines if line["is_heading"]] == ["Assets", "Liabilities and shareholders’ equity"]
    assert latest.lines[1]["label"] == "Cash and cash equivalents"


def test_slide_text_cash_flows_are_read_though_the_header_follows_the_rows_it_heads():
    statements = release_statements(_text_document(CASH_FLOW))
    quarter = _by(statements, "cash_flow", date(2025, 12, 31))
    assert quarter.duration == "quarter"
    assert _line(quarter, "Net cash provided by (used in) operating activities")["value"] == 11_410_300_000
    assert _line(quarter, "Net cash provided by (used in) operating activities")["concept"] == "release_NetCashProvidedByUsedInOperatingActivities"
    assert _line(quarter, "Purchase of short-term investments")["value"] == -409_700_000
    assert _line(quarter, "Depreciation and amortization")["value"] == 255_200_000
    assert "Cash flows from financing activities" in [line["label"] for line in quarter.lines if line["is_heading"]]


def test_a_statement_whose_arithmetic_fails_is_not_stored():
    # A wrong figure for total assets: no quarter of this balance sheet balances any more.
    broken = BALANCE.replace("Total assets 44,849.1 45,097.1 50,566.6 48,060.5 50,214.8",
                             "Total assets 44,849.1 45,097.1 50,566.6 48,060.5 51,214.8")
    statements = release_statements(_text_document(broken))
    assert date(2026, 6, 28) not in [item.period_end for item in statements]
    assert date(2026, 3, 29) in [item.period_end for item in statements]


def test_an_html_summary_table_gives_each_quarter_and_names_bare_share_counts():
    rows = [
        ["", "Three months ended,", "", ""],
        ["", "Sep 29,", "Dec 31,", "Mar 30,"],
        ["(unaudited, in millions €, except per share data)", "2024", "2024", "2025"],
        ["Net system sales", "5,926.0", "7,115.9", "5,740.4"],
        ["Net service and field option sales", "1,541.3", "2,146.9", "2,001.1"],
        ["Total net sales", "7,467.3", "9,262.8", "7,741.5"],
        ["Total cost of sales", "(3,673.9)", "(4,473.0)", "(3,561.8)"],
        ["Gross profit", "3,793.4", "4,789.8", "4,179.7"],
        ["Income from operations", "2,441.2", "3,355.4", "2,737.9"],
        ["Net income", "2,076.5", "2,693.4", "2,355.0"],
        ["Diluted net income per ordinary share", "5.28", "6.84", "6.00"],
        ["", "Weighted average number of ordinary shares used in computing per share amounts (in millions):", "", ""],
        ["Diluted", "393.6", "393.6", "392.5"],
    ]
    document = Document("https://example.test/release.htm")
    document.tables["T0"] = Table("T0", "ASML - Quarterly Summary US GAAP Consolidated Statements of Operations", rows)
    document.order.append("T0")
    quarter = _by(release_statements(document), "income", date(2025, 3, 30))
    assert _line(quarter, "Total net sales")["value"] == 7_741_500_000
    assert _line(quarter, "Total net sales")["concept"] == "release_Revenues"
    # Per-share figures keep their unit; share counts take the table's millions and are not per-share lines.
    assert _line(quarter, "Diluted net income per ordinary share")["value"] == 6.0
    assert "PerShare" in _line(quarter, "Diluted net income per ordinary share")["concept"]
    assert _line(quarter, "Diluted")["value"] == 392_500_000
    assert _line(quarter, "Diluted")["concept"] == "release_WeightedAverageNumberOfSharesDiluted"


def test_a_table_mixing_quarters_with_a_year_to_date_is_left_alone():
    text = ("Three months ended Six months ended Jun 29, Jun 28, Jun 29, Jun 28, (Unaudited, €, in millions) 2025 2026 "
            "2025 2026 Net system sales 5,596.1 6,564.8 11,336.5 12,844.2 Net service and field option sales 2,095.6 "
            "2,761.7 4,096.7 5,249.2 Total net sales 7,691.7 9,326.5 15,433.2 18,093.4 Total cost of sales (3,562.2) "
            "(4,291.1) (7,124.0) (8,413.0) Gross profit 4,129.5 5,035.4 8,309.2 9,680.4 Income from operations 2,664.1 "
            "3,456.1 5,402.0 6,613.9 Net income 2,290.3 2,917.6 4,645.3 5,674.3")
    assert release_statements(_text_document(text)) == []


# TSMC's consolidated report, abridged: titled statements with "Amount" and "%" columns, a statement running over two
# page-tables, capitals, note references, unnamed totals, and cash flows for the year to date only.
TITLE = "Taiwan Semiconductor Manufacturing Company Limited and Subsidiaries {} (In Thousands of New Taiwan Dollars{})"


def _report(income_header, cash_header, date_text="June 30, 2026") -> Document:
    document = Document("https://example.test/report.htm")

    def add(key: str, title: str, rows: list[list[str]], extra: str = "") -> None:
        document.tables[key] = Table(key, TITLE.format(title, extra), rows)
        document.order.append(key)

    add("T1", "CONSOLIDATED BALANCE SHEETS", [
        ["", date_text, "", "December 31, 2025", ""],
        ["", "Amount", "%", "Amount", "%"],
        ["ASSETS", "", "", "", ""],
        ["Cash and cash equivalents (Note 6)", "$3,134,218,213", "33", "$2,767,856,402", "35"],
        ["Inventories", "400,000,000", "4", "300,000,000", "4"],
        ["Property, plant and equipment", "5,841,436,514", "63", "4,865,167,476", "61"],
        ["TOTAL", "$9,375,654,727", "100", "$7,933,023,878", "100"],
        ["LIABILITIES AND EQUITY", "", "", "", ""],
        ["Accounts payable", "901,183,746", "10", "872,228,595", "11"],
        ["Bonds payable", "2,000,000,000", "21", "1,600,000,000", "20"],
        ["Total liabilities", "2,901,183,746", "31", "2,472,228,595", "31"],
        ["Total equity", "6,474,470,981", "69", "5,460,795,283", "69"],
        ["TOTAL", "$9,375,654,727", "100", "$7,933,023,878", "100"],
    ])
    add("T3", "CONSOLIDATED STATEMENTS OF COMPREHENSIVE INCOME", [
        *income_header,
        ["NET REVENUE (Notes 20, 31 and 37)", "$1,270,380,250", "100", "$933,791,869", "100", "$2,404,483,690", "100", "$1,773,045,533", "100"],
        ["COST OF REVENUE (Notes 12, 27 and 31)", "410,069,555", "32", "386,422,631", "41", "792,877,574", "33", "732,281,219", "41"],
        ["GROSS PROFIT", "860,310,695", "68", "547,369,238", "59", "1,611,606,116", "67", "1,040,764,314", "59"],
        ["OPERATING EXPENSES (Notes 27 and 31)", "", "", "", "", "", "", "", ""],
        ["Research and development", "73,146,138", "6", "61,279,719", "7", "140,902,823", "6", "117,827,212", "7"],
        ["INCOME FROM OPERATIONS", "766,602,651", "60", "463,423,638", "50", "1,425,568,793", "59", "870,504,446", "49"],
        ["", "", "", "", "", "", "", "(Continued)", ""],
    ], ", Except Earnings Per Share")
    add("T5", "CONSOLIDATED STATEMENTS OF COMPREHENSIVE INCOME", [
        *income_header,
        ["NET INCOME", "$706,780,923", "56", "$397,493,424", "43", "$1,279,582,227", "53", "$758,226,085", "43"],
        ["NET INCOME ATTRIBUTABLE TO:", "", "", "", "", "", "", "", ""],
        ["Shareholders of the parent", "$706,561,938", "56", "$398,273,102", "43", "$1,279,041,690", "53", "$759,837,230", "43"],
        ["", "$706,780,923", "56", "$397,493,424", "43", "$1,279,582,227", "53", "$758,226,085", "43"],
        ["EARNINGS PER SHARE (NT$, Note 25)", "", "", "", "", "", "", "", ""],
        ["Basic earnings per share", "$27.25", "", "$15.36", "", "$49.33", "", "$29.31", ""],
        ["The accompanying notes are an integral part of the consolidated financial statements.", "", "", "", "", "", "", "(Concluded)", ""],
    ], ", Except Earnings Per Share")
    add("T9", "CONSOLIDATED STATEMENTS OF CASH FLOWS", [
        *cash_header,
        ["CASH FLOWS FROM OPERATING ACTIVITIES", "", ""],
        ["Income before income tax", "$1,550,229,773", "$923,930,616"],
        ["Depreciation expense", "359,541,458", "359,012,194"],
        ["Income taxes paid", "(189,719,612)", "(128,883,523)"],
        ["Net cash generated by operating activities", "1,482,341,242", "1,122,637,757"],
        ["Net cash used in investing activities", "(849,664,174)", "(518,680,733)"],
        ["Net cash used in financing activities", "(304,563,833)", "(204,366,193)"],
        ["EFFECT OF EXCHANGE RATE CHANGES ON CASH AND CASH EQUIVALENTS", "$38,248,576", "$(162,693,534)"],
        ["NET INCREASE IN CASH AND CASH EQUIVALENTS", "366,361,811", "236,897,297"],
        ["CASH AND CASH EQUIVALENTS, BEGINNING OF PERIOD", "2,767,856,402", "2,127,627,043"],
        ["CASH AND CASH EQUIVALENTS, BEGINNING OF PERIOD", "2,767,856,402", "2,127,627,043"],
        ["CASH AND CASH EQUIVALENTS, END OF PERIOD", "$3,134,218,213", "$2,364,524,340"],
    ])
    document.tables["T14"] = Table("T14", TITLE.format("CONSOLIDATED STATEMENTS OF CASH FLOWS", ""), [["NOTES TO CONSOLIDATED FINANCIAL STATEMENTS"]])
    document.order.append("T14")
    # A note's table after the statements, under a text that names a statement: not a statement.
    add("T18", "6. CASH AND CASH EQUIVALENTS as in the CONSOLIDATED BALANCE SHEETS", [
        ["", "June 30, 2026", "December 31, 2025"], ["", "Amount", "Amount"], ["Cash and deposits in banks", "$1", "$2"]])
    return document


QUARTERLY_INCOME = [["", "For the Three Months Ended June 30", "", "", "", "For the Six Months Ended June 30", "", "", ""],
                    ["", "2026", "", "2025", "", "2026", "", "2025", ""],
                    ["", "Amount", "%", "Amount", "%", "Amount", "%", "Amount", "%"]]


def test_a_full_report_gives_its_quarter_its_year_to_date_and_its_balance_sheet():
    statements = release_statements(_report(QUARTERLY_INCOME, [["", "Six Months Ended June 30", ""], ["", "2026", "2025"]]))
    assert [(item.statement, item.duration, item.period_end) for item in statements] == [
        ("balance", "instant", date(2026, 6, 30)), ("cash_flow", "ytd", date(2026, 6, 30)),
        ("income", "quarter", date(2026, 6, 30)), ("income", "ytd", date(2026, 6, 30))]
    balance, cash, quarter, to_date = statements
    assert balance.currency == "TWD"
    # Thousands of New Taiwan dollars; the two unnamed totals are assets, then liabilities and equity.
    assert _line(balance, "Total assets")["value"] == 9_375_654_727_000
    assert _line(balance, "Total liabilities and equity")["concept"] == "release_LiabilitiesAndStockholdersEquity"
    assert _line(balance, "Cash and cash equivalents")["value"] == 3_134_218_213_000  # the note reference is dropped
    # Only the report's own period: the "Amount" column of the latest year, never the "%" beside it.
    assert _line(quarter, "Net revenue")["value"] == 1_270_380_250_000
    assert _line(to_date, "Net revenue")["value"] == 2_404_483_690_000
    assert _line(quarter, "Net revenue")["concept"] == "release_Revenues"
    # The statement runs over two page-tables; capitals read in sentence case; a per-share figure keeps its unit.
    assert _line(quarter, "Net income")["value"] == 706_780_923_000
    assert _line(quarter, "Basic earnings per share")["value"] == 27.25
    assert [line["label"] for line in quarter.lines if line["is_heading"]] == ["Operating expenses", "Net income attributable to", "Earnings per share"]
    assert next(line for line in quarter.lines if line["label"] == "Total")["concept"] == "release_NetIncomeAttributableToTotal"
    assert not any("accompanying notes" in line["label"].lower() for line in quarter.lines)
    # Cash flows: the row a page break repeats is taken once, and opening and closing cash are named apart.
    assert [line["label"] for line in cash.lines].count("Cash and cash equivalents, beginning of period") == 1
    assert _line(cash, "Cash and cash equivalents, end of period")["concept"] == "release_CashAndCashEquivalentsEndOfPeriod"
    assert _line(cash, "Net cash used in investing activities")["value"] == -849_664_174_000


def test_an_annual_report_headed_by_the_bare_year_is_the_year_to_its_balance_sheet_date():
    yearly = [["", "2026", "", "2025", "", "", "", "", ""], ["", "Amount", "%", "Amount", "%", "", "", "", ""]]
    statements = release_statements(_report(yearly, [["", "2026", "2025"]], date_text="December 31, 2026"))
    assert [(item.statement, item.duration, item.period_end) for item in statements] == [
        ("balance", "instant", date(2026, 12, 31)), ("cash_flow", "ytd", date(2026, 12, 31)), ("income", "ytd", date(2026, 12, 31))]


def test_a_line_between_cost_and_gross_profit_adjusts_the_check_and_a_misread_still_fails():
    def report(gross: str) -> Document:
        document = _report(QUARTERLY_INCOME, [["", "Six Months Ended June 30", ""], ["", "2026", "2025"]])
        rows = document.tables["T3"].rows
        at = next(index for index, row in enumerate(rows) if row[0] == "GROSS PROFIT")
        rows[at][1] = gross
        rows[at:at] = [["GROSS PROFIT BEFORE REALIZED GROSS PROFIT ON SALES TO ASSOCIATES", "860,310,695", "68", "", "", "", "", "", ""],
                       ["REALIZED GROSS PROFIT ON SALES TO ASSOCIATES", "(37,317)", "-", "", "", "", "", "", ""]]
        return document

    quarters = [item for item in release_statements(report("860,273,378")) if item.statement == "income" and item.duration == "quarter"]
    assert len(quarters) == 1 and _line(quarters[0], "Gross profit")["value"] == 860_273_378_000
    assert not [item for item in release_statements(report("860,999,999")) if item.statement == "income" and item.duration == "quarter"]


def test_reported_units_come_from_the_quarterly_ratios_row_in_either_layout():
    from kpi_extractor.release_statements import reported_units

    text = ("Three months ended Jun 29, Sep 28, Dec 31, Mar 29, Jun 28, (Unaudited) 2025 2025 2025 2026 2026 Gross margin "
            "53.7 % 51.6 % 52.2 % 53.0 % 54.0 % Sales of lithography systems (in units) 1 76 72 102 79 91 Net bookings 2 5,541 "
            "5,399 13,158 3,936 5,541")
    assert reported_units(_text_document(text)) == {date(2025, 6, 29): 76, date(2025, 9, 28): 72, date(2025, 12, 31): 102,
                                                    date(2026, 3, 29): 79, date(2026, 6, 28): 91}
    # A current-against-last-year table mixes a quarter with a year to date: not a quarter's units.
    mixed = ("Three months ended Six months ended Jun 29, Jun 28, Jun 29, Jun 28, (Unaudited) 2025 2026 2025 2026 "
             "Sales of lithography systems (in units) 1 76 91 153 170")
    assert reported_units(_text_document(mixed)) == {}
    # An older "Quarterly Summary" table does not say "Three months ended": its dates, a quarter apart, do.
    document = Document("https://example.test/release.htm")
    document.tables["T5"] = Table("T5", "ASML - Quarterly Summary Ratios and Other Data", [
        ["", "Dec 31,", "Mar 29,", "Jun 28,", "Sep 27,", "Dec 31,"],
        ["(unaudited, in millions €, except otherwise indicated)", "2019", "2020", "2020", "2020", "2020"],
        ["Gross profit as a percentage of net sales", "48.1%", "45.1%", "48.2%", "47.5%", "52.0%"],
        ["Sales of lithography systems (in units) 1", "76", "57", "61", "60", "80"],
    ])
    document.order.append("T5")
    assert reported_units(document)[date(2020, 12, 31)] == 80 and reported_units(document)[date(2020, 9, 27)] == 60


def test_a_release_summary_gives_its_own_quarter_headline_lines():
    from kpi_extractor.release_statements import release_headline
    rows = [
        ["", "3Q26Amounta", "3Q25Amount", "YoYInc. (Dec.) %", "2Q26Amount", "QoQInc. (Dec.) %"],
        ["Net sales", "1,494,243", "989,918", "50.9", "1,270,381", "17.6"],
        ["Gross profit", "992,379", "588,543", "68.6", "860,311", "15.4"],
        ["Income from operations", "876,445", "500,685", "75.0", "766,603", "14.3"],
        ["Income before tax", "911,039", "525,369", "73.4", "862,430", "5.6"],
        ["Net income", "763,064", "452,302", "68.7", "706,562", "8.0"],
        ["EPS (NT$)", "29.43b", "17.44c", "68.7", "27.25b", "8.0"],
    ]
    document = Document("https://example.test/release.htm")
    document.tables["T0"] = Table("T0", "TSMC’s 2026 third quarter consolidated results: (Unit: NT$ million, except for EPS)", rows)
    document.order.append("T0")
    headline = release_headline(document, date(2026, 9, 30))
    assert headline is not None and headline.duration == "headline" and headline.currency == "TWD"
    values = {line["concept"]: line["value"] for line in headline.lines}
    assert values["release_Revenues"] == 1_494_243_000_000
    assert values["release_NetIncomeAttributableToShareholdersOfTheParent"] == 763_064_000_000
    assert values["release_DilutedEarningsPerShare"] == 29.43  # the footnote mark is not part of it
    # Another quarter's release, or a summary whose first column is not the release's quarter, gives nothing.
    assert release_headline(document, date(2026, 6, 30)) is None
