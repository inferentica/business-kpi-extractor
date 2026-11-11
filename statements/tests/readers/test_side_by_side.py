from datetime import date

from statement_extractor.readers.html.read import read_document


def cells(*items):
    return "".join(f"<td>{item}</td>" for item in items)


def row(*items):
    return "<tr>" + cells(*items) + "</tr>"


# TIM's 6-K quarterly information as printed: the parent company's columns beside the group's, the totals on their
# headings above their lines, each column's month without its day ("June 2023 | December 2022").
BALANCE = ("""<table>
<tr><td colspan="6">TIM S.A. and TIM S.A. and SUBSIDIARY</td></tr>
<tr><td colspan="6">BALANCE SHEETS</td></tr>
<tr><td colspan="6">June 30, 2023 and December 31, 2022</td></tr>
<tr><td colspan="6">(In thousands of reais)</td></tr>
<tr><td></td><td></td><td colspan="2">Parent Company</td><td colspan="2">Consolidated</td></tr>
<tr><td></td><td>Note</td><td>June 2023</td><td>December 2022</td><td>June 2023</td><td>December 2022</td></tr>"""
           + row("Assets", "", "100", "90", "120", "110")
           + row("Current assets", "", "40", "30", "50", "45")
           + row("Cash and cash equivalents", "4", "15", "10", "20", "15")
           + row("Trade accounts receivable", "5", "25", "20", "30", "30")
           + row("Non-current assets", "", "60", "60", "70", "65")
           + row("Property, plant and equipment", "", "60", "60", "70", "65")
           + row("Liabilities and Shareholders&#180; equity", "", "100", "90", "120", "110")
           + row("Total liabilities", "", "70", "65", "90", "85")
           + row("Current liabilities", "", "30", "25", "40", "35")
           + row("Suppliers", "", "20", "15", "25", "20")
           + row("Loans", "", "10", "10", "15", "15")
           + row("Non-current liabilities", "", "40", "40", "50", "50")
           + row("Loans", "", "40", "40", "50", "50")
           + row("Share capital", "", "20", "20", "20", "20")
           + row("Reserves", "", "10", "5", "10", "5")
           + row("Total shareholders&#180; equity", "", "30", "25", "30", "25")
           + "</table>")


def test_parent_and_group_columns_side_by_side():
    readings, _notes = read_document(BALANCE, accession="0001292814-23-003323", form="6-K", filed=date(2023, 7, 31),
                                     document_end=date(2023, 6, 30), fiscal_year_end="1231", year_offset=0)
    got = {(r.period_end, r.scope): (r.status, next(line["value"] for line in r.lines if line["value"] is not None)) for r in readings if r.statement == "balance"}
    assert got == {(date(2023, 6, 30), "parent"): ("kept", 100_000), (date(2022, 12, 31), "parent"): ("kept", 90_000),
                   (date(2023, 6, 30), "consolidated"): ("kept", 120_000),
                   (date(2022, 12, 31), "consolidated"): ("kept", 110_000)}


def test_the_reports_name_is_no_side_by_side_heading():
    # "the individual and consolidated quarterly information" under every page names the report, not two scopes.
    one = BALANCE.replace("<td colspan=\"2\">Parent Company</td><td colspan=\"2\">Consolidated</td>", "") \
        .replace("(In thousands of reais)", "(In thousands of reais) See the individual and consolidated quarterly information")
    readings, _notes = read_document(one, accession="x", form="6-K", filed=date(2023, 7, 31),
                                     document_end=date(2023, 6, 30), fiscal_year_end="1231", year_offset=0)
    assert {r.scope for r in readings if r.statement == "balance"} == {"consolidated"}


def test_a_release_titling_its_results_the_groups_scopes_its_untitled_statements():
    # Azul's releases: "Consolidated Financial Results … the following income statement", then "Cash Flow Statement – IFRS".
    from statement_extractor.readers.html.parse import parse_html
    from statement_extractor.readers.printed.find import find_statements
    from statement_extractor.readers.printed.scope import statement_scope
    rows = "".join(row(label, "100", "90") for label in ("Cash flows from operating activities", "Net cash from operating activities",
                                                          "Net cash used in investing activities", "Net cash used in financing activities",
                                                          "Cash at the beginning of the period", "Cash at the end of the period"))
    page = ("<p>Consolidated Financial Results The following income statement should be read with the comments</p>"
            "<table><tr><td></td><td>3Q24</td><td>3Q23</td></tr>" + row("Net income", "5", "4") + "</table>"
            "<p>Third Quarter Results 2024 Cash Flow Statement – IFRS</p><table><tr><td></td><td>3Q24</td><td>3Q23</td></tr>" + rows + "</table>")
    tables = parse_html(page)
    found = [("income", [tables[0]]), ("cash_flow", [tables[1]])]
    assert statement_scope("cash_flow", [tables[1]], "6-K", found) == "consolidated"
    alone = [("cash_flow", [tables[1]])]
    assert statement_scope("cash_flow", [tables[1]], "6-K", alone) == "parent"  # nothing titled the group's
