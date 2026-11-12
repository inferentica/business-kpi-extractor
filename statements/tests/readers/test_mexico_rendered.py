from datetime import date

from statement_extractor.readers.html.parse import parse_html
from statement_extractor.readers.printed.mexico_rendered import is_rendered, rendered_readings

_INFO = ("<p>Date of end of reporting period: 2016-03-31 Description of presentation currency: MXN Level of rounding "
         "used in financial statements: THOUSANDS OF MEXICAN PESOS Consolidated: Yes Number of quarter: 1</p>")


def _table(context: str, header: list[str] | None, rows: list[tuple]) -> str:
    head = "<tr>" + "".join(f"<td>{cell}</td>" for cell in header) + "</tr>" if header else ""
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<p>{context}</p><table>{head}{body}</table>"


def _document() -> str:
    # Televisa's 6-K of May 2016 (kr6k1qfinan_grupo.htm), cut down: its balance sheet's second page headed with the
    # income statement's dates, a note's number before a figure, costs as positive amounts.
    close = ["Concept", "Close Current Quarter 2016-03-31", "Close Previous Exercise 2015-12-31"]
    span = ["Concept", "Accumulated Current Year 2016-01-01 - 2016-03-31", "Accumulated Previous Year 2015-01-01 - 2015-03-31"]
    balance = _table("13 of 77 [210000] Statement of financial position, current/non-current", close, [
        ("Assets [abstract]", "", ""), ("Cash and cash equivalents", "53,565,171,000", "49,397,126,000"),
        ("Other current non-financial assets", "[1] 38,451,612,000", "40,540,950,000"),
        ("Total current assets", "92,016,783,000", "89,938,076,000"), ("Total non-current assets", "195,104,622,000", "191,535,678,000"),
        ("Total assets", "287,121,405,000", "281,473,754,000"), ("Total current liabilities", "50,482,172,000", "49,028,180,000")])
    second = _table("14 of 77 " + " ".join(span), None, [
        ("Total non-current liabilities", "135,000,000,000", "132,000,000,000"),
        ("Total liabilities", "185,482,172,000", "181,028,180,000"), ("Total equity", "101,639,233,000", "100,445,574,000"),
        ("Total equity and liabilities", "287,121,405,000", "281,473,754,000")])
    income = _table("15 of 77 [310000] Statement of comprehensive income, profit or loss, by function of expense", span, [
        ("Revenue", "21,740,979,000", "19,859,363,000"), ("Cost of sales", "12,146,219,000", "11,134,679,000"),
        ("Gross profit", "9,594,760,000", "8,724,684,000"), ("Profit (loss)", "1,100,000,000", "2,000,000,000"),
        ("Profit (loss), attributable to owners of parent", "900,000,000", "1,700,000,000"),
        ("Profit (loss), attributable to non-controlling interests", "200,000,000", "300,000,000")])
    oci = _table("16 of 77 [410000] Statement of comprehensive income, OCI components presented net of tax", span, [
        ("Profit (loss)", "1,100,000,000", "2,000,000,000"), ("Other comprehensive income", "10,000,000", "20,000,000")])
    return _INFO + balance + second + income + oci


def _read():
    content = _document()
    return rendered_readings(parse_html(content), content, accession="0000895345-16-000476", form="6-K",
                             filed=date(2016, 5, 3), document_end=date(2016, 3, 31), fiscal_year_end="1231", year_offset=0)


def test_a_printed_xbrl_report_is_read_by_its_roles():
    content = _document()
    assert is_rendered(content)
    readings = {(r.statement, r.period_end): r for r in _read()}
    assert set(readings) == {("balance", date(2016, 3, 31)), ("balance", date(2015, 12, 31)),
                             ("income", date(2016, 3, 31)), ("income", date(2015, 3, 31))}  # not the OCI statement
    balance = readings["balance", date(2016, 3, 31)]
    assert (balance.currency, balance.scope, balance.own, balance.status) == ("MXN", "consolidated", True, "kept")
    values = {line["label"]: line["value"] for line in balance.lines}
    assert values["Other current non-financial assets"] == 38_451_612_000  # the note's "[1]" left out
    assert values["Total equity and liabilities"] == 287_121_405_000  # the second page, though dated otherwise
    income = readings["income", date(2016, 3, 31)]
    assert (income.fiscal_period, income.duration, income.status) == ("Q1", "quarter", "kept")
    assert [line["concept"] for line in income.lines][:2] == ["ifrs-full_Revenue", "ifrs-full_CostOfSales"]


def test_a_balance_that_does_not_add_up_is_rejected():
    content = _document().replace("287,121,405,000</td><td>281,473,754,000</td></tr></table><p>15 of 77",
                                  "287,121,406,000</td><td>281,473,754,000</td></tr></table><p>15 of 77")
    readings = rendered_readings(parse_html(content), content, accession="x", form="6-K", filed=date(2016, 5, 3),
                                 document_end=date(2016, 3, 31), fiscal_year_end="1231", year_offset=0)
    assert next(r for r in readings if r.statement == "balance" and r.own).status == "kept"  # within the rounding
    content = content.replace("287,121,406,000</td><td>281,473,754,000</td></tr></table><p>15 of 77",
                              "297,121,405,000</td><td>281,473,754,000</td></tr></table><p>15 of 77")
    readings = rendered_readings(parse_html(content), content, accession="x", form="6-K", filed=date(2016, 5, 3),
                                 document_end=date(2016, 3, 31), fiscal_year_end="1231", year_offset=0)
    assert next(r for r in readings if r.statement == "balance" and r.own).status == "rejected"


def test_a_release_without_the_reports_header_is_not_rendered():
    assert not is_rendered("<p>[210000] Statement of financial position</p><table></table>")


def test_a_6k_carrying_the_printed_report_is_read_through_the_pipeline():
    # Round 66: the reader's own name for the statements it read hid the pipeline's list of document kinds, and every
    # such 6-K came back unreadable.
    from types import SimpleNamespace

    from statement_extractor.pipeline.printed import read_printed
    document = SimpleNamespace(document="kr6k1qfinan_grupo.htm", document_type="6-K", extension=".htm", content=_document())
    ref = SimpleNamespace(form="6-K", accession="0000895345-16-000476", filed=date(2016, 5, 3), filing=SimpleNamespace(attachments=[document]))
    readings, notes, kinds = read_printed(ref, "1231", 0, document_end=date(2016, 3, 31))
    assert not any("unreadable" in note for note in notes)
    assert {(r.statement, r.period_end) for r in readings
            if any(line["concept"].startswith("ifrs-full_") for line in r.lines)} >= {
        ("balance", date(2016, 3, 31)), ("income", date(2016, 3, 31))}
    assert "html" in kinds
