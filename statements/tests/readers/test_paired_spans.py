from datetime import date

from statement_extractor.readers.html.read import read_document


def _row(label, *cells):
    return "<tr><td>" + label + "</td>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def _income(title: str, header: str, rows: list) -> str:
    return f"<p>{title}</p><table>{header}" + "".join(rows) + "</table>"


_LINES = [("Revenue", "1,761.3", "3,003.6", "1,557.0", "2,642.9"), ("Cost of sales", "(1,400.0)", "(2,400.0)", "(1,250.0)", "(2,100.0)"),
          ("Gross profit", "361.3", "603.6", "307.0", "542.9"), ("Income before income taxes", "100.0", "200.0", "90.0", "180.0"),
          ("Income tax expense", "(20.0)", "(40.0)", "(18.0)", "(36.0)"), ("Net income", "80.0", "160.0", "72.0", "144.0")]


def _read(content, end, filed):
    readings, _notes = read_document(content, accession="x", form="6-K", filed=filed, document_end=end, fiscal_year_end="1231",
                                     year_offset=0)
    return {(r.period_start, r.period_end): next(line["value"] for line in r.lines if line["label"] == "Revenue")
            for r in readings if r.statement == "income"}


def test_a_date_printed_twice_is_the_quarter_then_the_year_to_date_as_the_title_orders_them():
    # Embraer's 6-K of July 2014: "06.30.2014 | 06.30.2014 | 06.30.2013 | 06.30.2013" (round 75 read both as six months).
    content = _income("Embraer S.A. Condensed Consolidated Statements of Income Quarter and Year to date on June 30, 2014 and "
                      "2013 (In millions of U.S. dollars)",
                      "<tr><td></td><td>06.30.2014</td><td>06.30.2014</td><td>06.30.2013</td><td>06.30.2013</td></tr>",
                      [_row(*line) for line in _LINES])
    assert _read(content, date(2014, 6, 30), date(2014, 7, 30)) == {
        (date(2014, 4, 1), date(2014, 6, 30)): 1_761.3e6, (date(2014, 1, 1), date(2014, 6, 30)): 3_003.6e6,
        (date(2013, 4, 1), date(2013, 6, 30)): 1_557.0e6, (date(2013, 1, 1), date(2013, 6, 30)): 2_642.9e6}


def test_a_years_heading_over_its_quarter_and_nine_months_gives_each_its_column():
    # Shinhan's Q3 2015 report: "2015" over the three-month and nine-month columns (round 75 read one quarter).
    content = _income("SHINHAN FINANCIAL GROUP CO., LTD. AND SUBSIDIARIES Consolidated Interim Statements of Comprehensive "
                      "Income For the three-month and nine-month periods ended September 30, 2015 and 2014 (In millions of won)",
                      "<tr><td></td><td colspan='3'>2015</td><td colspan='2'>2014</td></tr>",
                      # the won sign before the first column only (round 76 took it for a second currency)
                      [_row(_LINES[0][0], "W", *_LINES[0][1:])] + [_row(line[0], "", *line[1:]) for line in _LINES[1:]])
    assert _read(content, date(2015, 9, 30), date(2015, 11, 16)) == {
        (date(2015, 7, 1), date(2015, 9, 30)): 1_761.3e6, (date(2015, 1, 1), date(2015, 9, 30)): 3_003.6e6,
        (date(2014, 7, 1), date(2014, 9, 30)): 1_557.0e6, (date(2014, 1, 1), date(2014, 9, 30)): 2_642.9e6}


def test_a_translation_beside_its_currency_is_never_a_quarter():
    content = _income("Consolidated Income Statements For the three and nine months ended September 30, 2011 and 2010. "
                      "Amounts expressed in millions of U.S. dollars ($) and in millions of Mexican pesos (Ps.).",
                      "<tr><td></td><td colspan='4'>2011</td><td colspan='2'>2010</td></tr>",
                      [_row("Revenue", "$", "1,761.3", "Ps.", "3,003.6", "Ps.", "1,557.0")] +
                      [_row(label, "", a, "", b, "", c) for label, a, b, c, _d in _LINES[1:]])
    assert all(start is None or (end - start).days > 200 for start, end in _read(content, date(2011, 9, 30), date(2011, 11, 1)))


def test_the_groups_statements_are_kept_beside_the_holding_companys():
    # Shinhan's 6-Ks file the holding company's statements (EX-99.1) before the group's (EX-99.2); the group's were
    # dropped as the same periods printed again (round 76).
    from types import SimpleNamespace

    from statement_extractor.pipeline import printed

    def exhibit(name, kind, title):
        content = _income(title + " For the three-month and nine-month periods ended September 30, 2015 and 2014 "
                          "(In millions of won)", "<tr><td></td><td colspan='2'>2015</td><td colspan='2'>2014</td></tr>",
                          [_row(*line) for line in _LINES])
        return SimpleNamespace(document=name, document_type=kind, extension=".htm", content=content)
    attachments = [SimpleNamespace(document="d6k.htm", document_type="6-K", extension=".htm", content="<p>Form 6-K</p>"),
                   exhibit("dex991.htm", "EX-99.1", "SHINHAN FINANCIAL GROUP CO., LTD. Separate Interim Statements of Comprehensive Income"),
                   exhibit("dex992.htm", "EX-99.2", "SHINHAN FINANCIAL GROUP CO., LTD. AND SUBSIDIARIES Consolidated Interim "
                           "Statements of Comprehensive Income")]
    printed._NAMES["1263043"] = ["SHINHAN FINANCIAL GROUP CO LTD"]
    ref = SimpleNamespace(form="6-K", accession="0001193125-15-377588", filed=date(2015, 11, 16),
                          filing=SimpleNamespace(attachments=attachments, cik=1263043, company="SHINHAN FINANCIAL GROUP CO LTD"))
    readings, _notes, _kinds = printed.read_printed(ref, "1231", 0, document_end=date(2015, 9, 30))
    nine_months = {r.scope for r in readings if r.statement == "income" and r.period_start == date(2015, 1, 1)}
    assert nine_months == {"parent", "consolidated"}
