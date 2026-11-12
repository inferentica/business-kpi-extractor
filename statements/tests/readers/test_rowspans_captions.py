from datetime import date

from statement_extractor.checks.subtotals import subtotals
from statement_extractor.readers.html.parse import parse_html
from statement_extractor.readers.html.read import read_document


def _row(label, *cells):
    return "<tr><td>" + label + "</td>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def test_a_rowspan_holds_its_columns_in_the_rows_below():
    # Enel Chile's Q2 2017 release: the label and "Note" span the heading's three rows; the dates below start after them
    # (round 78 dated its 2017 columns 2016).
    html = ("<p>Consolidated Statements of Comprehensive Income For the six and three month periods ended June 30, 2017 "
            "and 2016 (In thousands of Chilean pesos)</p><table>"
            "<tr><td rowspan='3'>STATEMENTS OF INCOME</td><td rowspan='3'>Note</td><td colspan='2'>For the six month "
            "periods ended</td><td colspan='2'>For the three month periods ended</td></tr>"
            "<tr><td>6-30-2017</td><td>6-30-2016</td><td>6-30-2017</td><td>6-30-2016</td></tr>"
            "<tr><td>ThCh$</td><td>ThCh$</td><td>ThCh$</td><td>ThCh$</td></tr>"
            + _row("Revenues", "25", "1,202,535,659", "1,273,888,430", "615,898,560", "649,381,297")
            + _row("Raw materials and consumables used", "26", "(700,000,000)", "(750,000,000)", "(350,000,000)", "(380,000,000)")
            + _row("Profit before tax", "", "502,535,659", "523,888,430", "265,898,560", "269,381,297")
            + _row("Income tax expense", "27", "(100,000,000)", "(110,000,000)", "(50,000,000)", "(55,000,000)")
            + _row("Profit for the period", "", "402,535,659", "413,888,430", "215,898,560", "214,381,297") + "</table>")
    dates = parse_html(html)[0].header[1]
    assert [cell.start for cell in dates] == [2, 3, 4, 5]
    readings, _notes = read_document(html, accession="x", form="6-K", filed=date(2017, 7, 28), document_end=date(2017, 6, 30),
                                     fiscal_year_end="1231", year_offset=0)
    revenue = {(r.period_end, r.duration): next(line["value"] for line in r.lines if line["label"] == "Revenues") for r in readings}
    assert revenue[date(2017, 6, 30), "quarter"] == 615_898_560e3 and revenue[date(2016, 6, 30), "quarter"] == 649_381_297e3
    assert revenue[date(2017, 6, 30), "ytd"] == 1_202_535_659e3


def test_a_scale_caption_naming_its_currency_first_is_no_per_share_heading():
    # Woori's "(Korean Won in millions, except per share data)" in a row of its own: every line under it was read as a
    # figure per share, left unscaled and out of the checks (round 78: "no income before taxes and net income").
    html = ("<p>WOORI FINANCE HOLDINGS CO., LTD. AND SUBSIDIARIES CONSOLIDATED STATEMENTS OF COMPREHENSIVE INCOME FOR THE "
            "THREE MONTHS ENDED JUNE 30, 2014 AND 2013</p><table><tr><td></td><td>2014</td><td>2013</td></tr>"
            "<tr><td>(Korean Won in millions, except per share data)</td></tr>"
            + _row("Interest income", "2,325,326", "2,300,000") + _row("Interest expense", "(1,203,533)", "(1,200,000)")
            + _row("Net income before income tax expense", "308,795", "250,000") + _row("Income tax expense", "84,014", "60,000")
            + _row("Net income", "224,781", "190,000") + _row("Basic earnings per share", "260", "200") + "</table>")
    readings, _notes = read_document(html, accession="x", form="6-K", filed=date(2014, 8, 14), document_end=date(2014, 6, 30),
                                     fiscal_year_end="1231", year_offset=0)
    first = next(r for r in readings if r.period_end == date(2014, 6, 30))
    values = {line["label"]: line["value"] for line in first.lines}
    assert values["Net income"] == 224_781e6 and values["Basic earnings per share"] == 260
    assert first.status == "kept"


def test_an_ifrs_group_of_one_line_is_its_subtotal():
    # Woori prints "Items that will not be reclassified to profit or loss 7,585" under its only line.
    printed = [("Net income", 0, 773443.0), ("Remeasurement of the net defined benefit liability", 0, 7585.0),
               ("Items that will not be reclassified to profit or loss", 0, 7585.0), ("Loss on available-for-sale", 0, -187847.0),
               ("Loss on cash flow hedge", 0, -39177.0), ("Items subsequently reclassified to net income", 0, -227024.0),
               ("Other comprehensive income (loss), net of tax", 0, -219439.0)]
    assert subtotals(printed).get(2) == 1
