from datetime import date
from types import SimpleNamespace

from statement_extractor.pipeline import printed
from statement_extractor.readers.html.parse import parse_html


def _statement(title: str, scale: str, cash: str) -> str:
    return (f"<p>{title}</p><p>As at 31 December</p><p>{scale}</p><table>"
            "<tr><td></td><td>2020</td><td>2019</td></tr>"
            f"<tr><td>Cash and cash equivalents</td><td>{cash}</td><td>900</td></tr>"
            "<tr><td>Other assets</td><td>1,000</td><td>1,100</td></tr>"
            f"<tr><td>Total assets</td><td>{int(cash.replace(',', '')) + 1000:,}</td><td>2,000</td></tr>"
            "<tr><td>Total liabilities</td><td>1,200</td><td>1,100</td></tr>"
            "<tr><td>Total equity</td><td>" + f"{int(cash.replace(',', '')) + 1000 - 1200:,}" + "</td><td>900</td></tr>"
            "<tr><td>Total equity and liabilities</td><td>" + f"{int(cash.replace(',', '')) + 1000:,}" + "</td><td>2,000</td></tr></table>")


# The rest of an auditors' report, pages before the statements it opens.
_SIGNED = "<p>" + "Our audits included performing procedures to assess the risks of material misstatement. " * 12 + "</p>"
# A table of FEMSA's notes, between its statements and Heineken's report.
_NOTE = "<p>Note 9. Investments</p><table><tr><td></td><td>2020</td></tr><tr><td>Heineken Group</td><td>75,075</td></tr></table>"
# FEMSA's FY2020 20-F, cut down: its own statements after its auditors' report, then Heineken's, its equity investee's,
# after theirs, headed with no company name.
_COVER = ("<p>Commission file number 333-08752 FOMENTO ECON&#211;MICO MEXICANO, S.A.B. DE C.V. (Exact name of registrant as "
          "specified in its charter) Mexican Economic Development, Inc. (Translation of registrant's name into English)</p>")
_FEMSA = ("<p>We have audited the accompanying consolidated statements of financial position of Fomento Econ&#243;mico Mexicano, "
          "S.A.B. de C.V. and subsidiaries (the Company) as of December 31, 2020 and 2019.</p>" + _SIGNED
          + _statement("FOMENTO ECON&#211;MICO MEXICANO, S.A.B. DE C.V. AND SUBSIDIARIES Consolidated Statements of Financial Position",
                       "In millions of Mexican pesos (Ps.)", "1,000"))
_HEINEKEN = ("<p>To the shareholders of Heineken N.V. We have audited the accompanying consolidated statements of financial "
             "position of Heineken N.V. and subsidiaries (the \"Company\") as of December 31, 2020 and 2019.</p>" + _SIGNED
             + _statement("Consolidated Statement of Financial Position", "In millions of EUR", "2,000"))


def test_each_table_carries_the_company_its_auditors_report_names():
    tables = parse_html(_COVER + _FEMSA + _HEINEKEN)
    # The first report opens FEMSA's own statements; Heineken's report, after them, opens Heineken's.
    assert [table.audited_of for table in tables] == ["", "Heineken N.V."]


def test_an_annual_reports_other_company_is_left_out_against_its_cover():
    # The reader keeps one table per statement in a document, so the 20-F here carries Heineken's balance sheet only, as
    # FEMSA's FY2011 20-F did in effect (its own balance sheet unread, "no single scale stated").
    printed._NAMES["1061736"] = ["MEXICAN ECONOMIC DEVELOPMENT INC"]
    document = SimpleNamespace(document="fmx-20201231x20f.htm", document_type="20-F", extension=".htm",
                               content=_COVER + _FEMSA.split("<p>FOMENTO")[0] + _NOTE + _HEINEKEN)
    filing = SimpleNamespace(attachments=[document], cik=1061736, company="MEXICAN ECONOMIC DEVELOPMENT INC",
                             period_of_report="2020-12-31")
    ref = SimpleNamespace(form="20-F", accession="0001558370-21-004287", filed=date(2021, 4, 23), filing=filing)
    readings, notes, _kinds = printed.read_printed(ref, "1231", 0)
    assert any("another company's statements left out (Heineken N.V.)" in note for note in notes)
    assert readings == []
    printed._NAMES["1061736"] = ["HEINEKEN NV"]  # the same document filed by Heineken itself: its own statements
    readings, notes, _kinds = printed.read_printed(ref, "1231", 0)
    assert {reading.period_end for reading in readings} == {date(2020, 12, 31), date(2019, 12, 31)}


def test_reports_relied_on_before_the_registrants_statements_are_the_registrants():
    # CEMIG's FY2015 20-F: its auditors' report, then the reports they relied on for its investees, then its statements
    # (round 72 left them out as Norte Energia's).
    reports = ("<p>We have audited the accompanying consolidated balance sheets of Companhia Energ&#233;tica de Minas "
               "Gerais&#8212;CEMIG and subsidiaries (the Company) as of December 31, 2015 and 2014.</p>"
               "<p>We have audited the accompanying consolidated balance sheet of Madeira Energia S.A. &#8211; MESA and its "
               "subsidiary (the Company) as of December 31, 2015.</p>"
               "<p>We have audited the balance sheet of Norte Energia S.A. (the Company) as of December 31, 2015.</p>" + _SIGNED)
    tables = parse_html(reports + _statement("CONSOLIDATED STATEMENTS OF FINANCIAL POSITION", "In millions of Brazilian reais", "1,000"))
    assert [table.audited_of for table in tables] == [""]


def test_a_statements_own_words_are_no_company():
    # KT's FY2009 20-F: "the accompanying statements of Financial Position of KT Corporation".
    tables = parse_html(_FEMSA.replace("consolidated statements of financial position of Fomento Econ&#243;mico Mexicano, "
                                       "S.A.B. de C.V. and subsidiaries", "statements of Financial Position of KT Corporation")
                        + _HEINEKEN)
    assert [table.audited_of for table in tables] == ["", "Heineken N.V."]
