"""Archived PDF reports laid out as text by their words' positions, then read by the plain-text reader."""
from datetime import date

from statement_extractor.readers.pdf.layout import _glyphs, _line_text, _page_text
from statement_extractor.readers.printed.marks import without_marks
from statement_extractor.readers.text.read import read_document


def word(text, x0, x1, top=100.0, size=10.0):
    return {"text": text, "x0": x0, "x1": x1, "top": top, "size": size}


def test_figures_by_their_right_edge_and_phrases_one_space_apart():
    # TSMC's FY2001 20-F: a heading's words spaced wide, figures aligned on their right edges, a bracket set apart.
    unit = 5.0
    assert _line_text([word("December", 400, 440), word("31,", 450, 461)], unit).strip() == "December 31,"
    first = _line_text([word("Cash", 80, 100), word("38,840.2", 355, 395), word("37,556.3", 417, 457)], unit)
    second = _line_text([word("Other", 80, 105), word("(", 362, 365), word("44.0)", 375, 395), word("1.3", 442, 457)], unit)
    assert first.index("38,840.2") + len("38,840.2") == second.index("(44.0)") + len("(44.0)")
    assert first.rstrip().endswith("37,556.3") and second.rstrip().endswith("1.3")


def test_superscripts_page_numbers_and_glyph_codes():
    words = [word("Consolidated", 80, 140), word("Operations", 150, 200), word("1", 201, 204, size=6.0)]
    assert [w["text"] for w in without_marks(words)] == ["Consolidated", "Operations"]
    assert _line_text([word("-", 300, 303), word("4", 305, 310), word("-", 312, 315)], 5.0) == ""
    assert _glyphs("SHAREHOLDERS(cid:146) EQUITY") == "SHAREHOLDERS’ EQUITY"


TSMC_Q4_1998 = """
                                 TAIWAN SEMICONDUCTOR MANUFACTURING CO., LTD.
                                         UNCONSOLIDATED BALANCE SHEET
                                       As of December 31,   1998 and  1997
                                                                                          (1)
                   (Figures in Million of New Taiwan dollars (NTD) and U.S. dollars (USD))
                                                 Dec-98                     Dec-97              YOY
                 ASSETS                        USD          NTD        %         NTD        %           Move     %
         Cash                                     256          8,292     7%        2,534      2%       5,758     227%
         Total Current Assets                     815         26,378    21%       23,791     22%       2,588     11%
         Long Term Investment                     542         17,538    14%       19,220     18%      -1,683     -9%
         Total Assets                           1,357         43,916   100%       43,011    100%         905      2%
         Total Liabilities                        357         11,551    26%       10,100     23%       1,451     14%
         Shareholders' Equity                   1,000         32,365    74%       32,911     77%        -546     -2%
         Total Liabilities and Shareholders' Equity  1,357    43,916   100%       43,011    100%         905      2%
"""


def test_a_quarterly_statement_dated_by_month_and_year_with_its_translation_left_out():
    readings, _ = read_document(TSMC_Q4_1998, accession="a", form="6-K", filed=date(1999, 1, 30),
                                document_end=date(1998, 12, 31), fiscal_year_end="1231", year_offset=0)
    cash = {r.fiscal_year: next(l["value"] for l in r.lines if not l["is_heading"]) for r in readings}
    assert cash == {"1998": 8_292e6, "1997": 2_534e6}
    assert all(r.currency == "TWD" and r.scope == "parent" for r in readings)
