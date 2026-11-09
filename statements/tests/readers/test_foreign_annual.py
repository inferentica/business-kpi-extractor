"""Foreign filers' annual statements printed before XBRL: translation columns, misplaced year rows, running page
links, reconciliations to U.S. GAAP, and each statement's currency and basis."""
from datetime import date

from statement_extractor.domain.basis import ROC_GAAP, TAIWAN_IFRS, US_GAAP, IFRS, currencies_named, stated_basis
from statement_extractor.readers.html.read import read_document


def cells(*items):
    return "".join(f"<td>{item}</td>" for item in items)


def row(*items):
    return "<tr>" + cells(*items) + "</tr>"


TITLE = ("<p>Index to Financial Statements</p><p>TAIWAN SEMICONDUCTOR MANUFACTURING COMPANY LIMITED AND SUBSIDIARIES</p>"
         "<p>CONSOLIDATED BALANCE SHEETS (In Millions of New Taiwan and U.S. Dollars, Except Shares)</p>")

# TSMC's FY2003 20-F: "2003" spans its NT$ column and the US$ translation; the currency row names each column; the
# year row sits one cell left of its figures.
BALANCE = TITLE + "<table>" + \
    "<tr><td></td><td></td><td>Notes</td><td></td><td colspan=\"5\">December 31</td></tr>" + \
    "<tr><td></td><td></td><td></td><td>2002</td><td></td><td colspan=\"3\">2003</td></tr>" + \
    row("", "", "", "", "NT$", "", "NT$", "", "US$") + \
    row("Cash and cash equivalents", "", "", "", "67,790.2", "", "102,988.9", "", "3,030.0") + \
    row("Receivables", "", "", "", "16,285.4", "", "25,348.9", "", "745.8") + \
    row("Total current assets", "", "", "", "84,075.6", "", "128,337.8", "", "3,775.8") + \
    row("Property", "", "", "", "200,000.0", "", "210,000.0", "", "6,178.3") + \
    row("TOTAL ASSETS", "", "", "", "284,075.6", "", "338,337.8", "", "9,954.1") + \
    row("Total liabilities", "", "", "", "84,075.6", "", "98,337.8", "", "2,893.4") + \
    row("Total shareholders' equity", "", "", "", "200,000.0", "", "240,000.0", "", "7,060.7") + \
    row("TOTAL LIABILITIES AND SHAREHOLDERS' EQUITY", "", "", "", "284,075.6", "", "338,337.8", "", "9,954.1") + \
    "</table>"


def read(html, form="20-F", end=date(2003, 12, 31)):
    readings, notes = read_document(html, accession="a", form=form, filed=date(2004, 5, 28), document_end=end,
                                    fiscal_year_end="1231", year_offset=0)
    return readings, notes


def test_a_convenience_translation_is_left_out_and_each_year_keeps_its_own_column():
    readings, notes = read(BALANCE)
    by_year = {r.fiscal_year: r for r in readings}
    assert sorted(by_year) == ["2002", "2003"] and len(readings) == 2
    assert by_year["2002"].lines[0]["value"] == 67_790.2e6 and by_year["2003"].lines[0]["value"] == 102_988.9e6
    assert all(r.currency == "TWD" and r.status == "kept" for r in readings)
    assert any("convenience translation" in note for note in notes)


# TSMC's FY2002 20-F: its year row is a cell left of the figures throughout ("2000" over the Notes column, "2001" over
# 2000's figures), while its currency row lines up.
INCOME = ("<p>CONSOLIDATED STATEMENTS OF INCOME (In Millions of New Taiwan and U.S. Dollars, Except Shares)</p><table>"
          "<tr><td></td><td></td><td>Notes</td><td></td><td colspan=\"7\">Year Ended December 31</td></tr>"
          "<tr><td></td><td></td><td>2000</td><td></td><td>2001</td><td></td><td colspan=\"3\">2002</td></tr>" +
          row("", "", "", "", "NT$", "", "NT$", "", "NT$", "", "US$") +
          row("NET SALES", "", "", "", "166,197.6", "", "125,884.9", "", "162,301.2", "", "4,677.3") +
          row("COST OF SALES", "", "21", "", "87,609.6", "", "92,228.1", "", "109,988.1", "", "3,169.7") +
          row("GROSS PROFIT", "", "", "", "78,588.0", "", "33,656.8", "", "52,313.1", "", "1,507.6") +
          row("INCOME BEFORE INCOME TAX", "", "", "", "78,588.0", "", "33,656.8", "", "52,313.1", "", "1,507.6") +
          row("INCOME TAX", "", "", "", "(10,000.0", ")", "(5,000.0", ")", "(7,000.0", ")", "(201.6", ")") +
          row("NET INCOME", "", "", "", "68,588.0", "", "28,656.8", "", "45,313.1", "", "1,306.0") +
          "</table>")


def test_years_printed_off_their_columns_are_taken_in_order():
    readings, _ = read(INCOME, end=date(2002, 12, 31))
    sales = {r.fiscal_year: r.lines[0]["value"] for r in readings}
    assert sales == {"2000": 166_197.6e6, "2001": 125_884.9e6, "2002": 162_301.2e6}


def test_a_reconciliation_to_us_gaap_is_not_the_balance_sheet():
    reconciliation = ("<p>A reconciliation of the significant balance sheet accounts to the amounts determined under "
                      "U.S. GAAP is as follows (In Millions of New Taiwan Dollars):</p><table>"
                      "<tr><td></td><td>2002</td><td>2003</td></tr>" +
                      row("Total assets", "As reported", "284,075.6", "338,337.8") + "</table>")
    readings, _ = read(BALANCE + reconciliation)
    assert len(readings) == 2 and readings[0].lines[0]["label"] == "Cash and cash equivalents"


def test_currencies_and_the_basis_are_read_from_the_printed_words():
    assert currencies_named("In Millions of New Taiwan and U.S. Dollars") == ["TWD", "USD"]
    assert currencies_named("(Amounts in thousands) EUR EUR USD") == ["EUR", "USD"]
    opinion = ("In our opinion, such consolidated financial statements present fairly, in all material respects, the "
               "financial position of the Company in conformity with accounting principles generally accepted in the "
               "Republic of China. Accounting principles generally accepted in the Republic of China vary in certain "
               "respects from accounting principles generally accepted in the United States of America.")
    assert stated_basis(opinion) == ROC_GAAP
    assert stated_basis("<p>All figures are based on US GAAP.</p>") == US_GAAP
    assert stated_basis("present fairly, in all material respects, … in conformity with International Financial "
                        "Reporting Standards as issued by the International Accounting Standards Board.") == IFRS
    assert stated_basis("present fairly … in conformity with the Regulations Governing the Preparation of Financial "
                        "Reports by Securities Issuers and IFRSs endorsed by the Financial Supervisory Commission of the "
                        "Republic of China.") == TAIWAN_IFRS


def test_assets_and_liabilities_printed_side_by_side():
    # TSMC's consolidated half-year balance sheets: liabilities to the right of the assets on each row.
    rows = [("Cash and cash equivalents", "150,978", "172,486", "Short-term loans", "33,140", "18,082"),
            ("Receivables", "50,000", "40,000", "Accounts payable", "11,710", "11,552"),
            ("Inventories", "20,000", "18,000", "Total liabilities", "44,850", "29,634"),
            ("Total current assets", "220,978", "230,486", "Capital stock", "259,000", "259,000"),
            ("Property, plant and equipment", "562,838", "450,290", "Retained earnings", "479,966", "392,142"),
            ("Other assets", "0", "0", "Total shareholders' equity", "738,966", "651,142"),
            ("TOTAL", "783,816", "680,776", "TOTAL", "783,816", "680,776")]
    html = ("<p>CONSOLIDATED BALANCE SHEETS JUNE 30, 2011 AND 2010 (In Millions of New Taiwan Dollars)</p><table>"
            "<tr><td></td><td>2011</td><td>2010</td><td></td><td>2011</td><td>2010</td></tr>" +
            "".join("<tr>" + cells(*row) + "</tr>" for row in rows) + "</table>")
    readings, _ = read(html, form="6-K", end=date(2011, 6, 30))
    assert [(r.fiscal_year, r.status) for r in readings] == [("2011", "kept"), ("2010", "kept")]
    assert any(line["label"] == "Short-term loans" and line["value"] == 33_140e6 for line in readings[0].lines)


def test_a_note_number_before_each_label_is_no_statement_beside_it():
    rows = [(label, "", n, "", a, "", b) for label, n, a, b in (
        ("Cash and cash equivalents", "4", "1,904,609", "1,655,857"), ("Accounts receivable, net", "5", "302,572", "672,762"),
        ("Inventories, net", "6", "817,192", "1,071,385"), ("Total assets", "", "3,024,373", "3,400,004"), ("Total liabilities", "7", "1,024,373", "1,243,549"),
        ("Total shareholders' equity", "", "2,000,000", "2,156,455"),
        ("Total liabilities and shareholders' equity", "", "3,024,373", "3,400,004"))]
    html = ("<p>Consolidated Balance Sheets (in thousands)</p><table><tr><td></td><td></td><td>Notes</td><td></td>"
            "<td>2005</td><td></td><td>2006</td></tr>" + "".join("<tr>" + cells(*row) + "</tr>" for row in rows) + "</table>")
    readings, _ = read(html, end=date(2006, 12, 31))
    assert sorted((r.fiscal_year, r.status) for r in readings) == [("2005", "kept"), ("2006", "kept")]
