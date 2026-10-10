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
