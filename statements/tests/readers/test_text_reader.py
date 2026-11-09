from datetime import date

from statement_extractor.checks.schedules import parse_schedule, schedule_check
from statement_extractor.readers.text.read import read_document

# Apple's Q2 FY1995 10-Q as filed: spans over months, month-days, years; labels that wrap before and after figures;
# an unlabelled subtotal; "$" apart from its figures.
INCOME = """
                  CONSOLIDATED STATEMENTS OF INCOME (Unaudited)
                 (Dollars in millions, except per share amounts)
<TABLE>
<CAPTION>
                            THREE MONTHS ENDED        SIX MONTHS ENDED
                            March 31,    April 1,    March 31,    April 1,
                                 1995        1994         1995        1994
<S>                        <C>         <C>          <C>         <C>
Net sales                    $  2,652    $  2,077     $  5,484    $  4,546

Costs and expenses:
Cost of sales                   1,957       1,578        3,975       3,455
Research and development          143         134          275         286
Selling, general and              386         330          801         705
administrative
Restructuring costs                --          --         (17)          --

                                2,486       2,042        5,034       4,446

Operating income                  166          35          450         100
Interest and other income
   (expense), net                 (50)         (7)         (35)         (7)

Income before provision           116          28          415          93
for income taxes
Provision for income               43          11          154          36
taxes

Net income                 $       73 $        17    $     261   $      57
</TABLE>
"""

# Apple's FY1994 balance sheet: both grand totals printed without a label.
BALANCE = """
Consolidated Balance Sheets
                                                   (Dollars in thousands)
<TABLE>
<CAPTION>
September 30, 1994, and September 24, 1993             1994             1993
<S>                                              <C>             <C>
Assets:
Current assets:
  Cash and cash equivalents                      $ 1,203,488     $   676,413
  Inventories                                      3,272,964       3,661,942
    Total current assets                           4,476,452       4,338,355
Other assets                                         826,294         833,057
                                                 -----------     -----------
                                                 $ 5,302,746     $ 5,171,412
Liabilities and Shareholders' Equity:
Current liabilities:
  Accounts payable                                 1,944,305       2,515,202
Long-term debt                                       975,140         629,832
Shareholders' equity:
  Retained earnings                                2,383,301       2,026,378
                                                 -----------     -----------
                                                 $ 5,302,746     $ 5,171,412
</TABLE>
"""

SCHEDULE = """<TABLE> <S> <C>
<ARTICLE>           5
<MULTIPLIER>                            1,000
<PERIOD-TYPE>                           YEAR
<FISCAL-YEAR-END>                                        SEP-30-1994
<PERIOD-END>                                             SEP-30-1994
<TOTAL-ASSETS>                                           5,302,746
<NET-INCOME>                                               310,178
</TABLE>"""


def read(text, fiscal_year_end, end, form="10-Q"):
    readings, notes = read_document(text, accession="a", form=form, filed=date(1995, 5, 15), document_end=end,
                                    fiscal_year_end=fiscal_year_end, year_offset=0)
    return {(r.statement, r.fiscal_year, r.fiscal_period, r.duration): r for r in readings}, notes


def test_quarter_and_six_month_columns_with_wrapped_labels():
    readings, _ = read(INCOME, "0930", date(1995, 3, 31))
    quarter = readings[("income", "1994", "Q2", "quarter")]
    six = readings[("income", "1994", "Q2", "ytd")]
    values = {line["label"]: line["value"] for line in quarter.lines}
    assert values["Net sales"] == 2_077e6 and values["Net income"] == 17e6
    assert values["Selling, general and administrative"] == 330e6
    assert values["Interest and other income (expense), net"] == -7e6
    assert values["Income before provision for income taxes"] == 28e6
    assert values["Restructuring costs"] == 0
    assert {line["label"]: line["value"] for line in six.lines}["Net income"] == 57e6
    assert quarter.status == "kept" and six.status == "kept"


def test_unlabelled_grand_totals_balance():
    readings, _ = read(BALANCE, "0930", date(1994, 9, 30), form="10-K")
    assert readings[("balance", "1994", "FY", "instant")].status == "kept"
    assert readings[("balance", "1993", "FY", "instant")].status == "kept"


def test_the_schedule_must_agree_with_the_statement():
    readings, _ = read(BALANCE, "0930", date(1994, 9, 30), form="10-K")
    schedule = parse_schedule(SCHEDULE)
    assert schedule.total_assets == 5_302_746_000 and schedule.duration == "year"
    assert schedule_check(readings[("balance", "1994", "FY", "instant")], [schedule]).result == "pass"
    wrong = parse_schedule(SCHEDULE.replace("5,302,746", "5,312,746"))
    assert schedule_check(readings[("balance", "1994", "FY", "instant")], [wrong]).result == "fail"
    assert schedule_check(readings[("balance", "1993", "FY", "instant")], [schedule]) is None


# Coca-Cola's 1994 10-Q: capitals wrapped over three lines, a share heading over mixed-case lines, figures with no
# leading zero, and the scale caption naming "per share".
KO_1994 = """
                  CONDENSED CONSOLIDATED STATEMENTS OF INCOME
                      (In millions except per share data)

<TABLE>
<CAPTION>
                        Three Months Ended June 30,  Six Months Ended June 30,
                            1994          1993         1994           1993
<S>                       <C>           <C>          <C>           <C>
NET OPERATING REVENUES    $ 4,342       $ 3,899      $  7,694      $  6,955
Cost of goods sold          1,667         1,464         2,909         2,557
OPERATING INCOME            2,675         2,435         4,785         4,398

INCOME BEFORE INCOME
 TAXES AND CHANGE IN
 ACCOUNTING PRINCIPLE       1,107           980         1,867         1,636

Income taxes                  349           302           588           504

NET INCOME                $   758       $   678      $  1,279      $  1,132

INCOME PER SHARE
Before change in
 accounting principle     $   .59       $   .52      $    .99      $    .87

AVERAGE SHARES
 OUTSTANDING                1,292         1,303         1,294         1,304
</TABLE>
"""


def test_capitals_wrapped_and_per_share_headings():
    readings, _ = read(KO_1994, "1231", date(1994, 6, 30))
    quarter = readings[("income", "1994", "Q2", "quarter")]
    values = {line["label"]: line["value"] for line in quarter.lines}
    assert values["INCOME BEFORE INCOME TAXES AND CHANGE IN ACCOUNTING PRINCIPLE"] == 1_107e6
    assert values["Before change in accounting principle"] == 0.59
    assert values["AVERAGE SHARES OUTSTANDING"] == 1_292e6
    assert quarter.status == "kept"


# Apple's 1996 10-Q: a wrapped label ended with tabs, and figures one space apart.
def _apple_line(label, first, second):
    return f"{label}{first:>{62 - len(label)}}{second:>12}\n"  # figures end at 62 and 74


APPLE_TABS = (
    "                     CONSOLIDATED STATEMENTS OF CASH FLOWS\n"
    "                                  (In thousands)\n"
    "<TABLE>\n<CAPTION>\n"
    "                                                         Nine Months Ended\n"
    "                                                         July 1,    June 25,\n"
    "                                                          1994        1993\n"
    "<S>                                             <C>         <C>\n"
    + _apple_line("Cash and cash equivalents, beginning of the period", "676,413", "498,557")
    + _apple_line("Net income", "195,523", "(4,218)")
    + _apple_line("  Cash generated by (used for) operations", "494,957", "(251,739)")
    + "Investments:\n"
    + "Purchase of short-term investments" + " " * 19 + "(257,228) (1,359,796)\n"
    + "Purchase of property, plant, and equipment, net of\n"
    + " retirements\t\t\t\t\t" + " " * 5 + "(123,375)" + " " * 3 + "(165,407)\n"
    + _apple_line("  Cash generated by (used for) investment activities", "(29,521)", "308,047")
    + _apple_line("  Cash used for financing activities", "(154)", "(161,681)")
    + _apple_line("Total cash generated (used)", "465,282", "(105,373)")
    + _apple_line("Cash and cash equivalents, end of the period", "1,141,695", "393,184")
    + "</TABLE>\n")


def test_tabs_and_figures_one_space_apart():
    readings, _ = read(APPLE_TABS, "0930", date(1994, 7, 1))
    values = {line["label"]: line["value"] for line in readings[("cash_flow", "1994", "Q3", "ytd")].lines}
    assert values["Purchase of property, plant, and equipment, net of retirements"] == -123_375e3
    assert values["Purchase of short-term investments"] == -257_228e3


# Chase's 1995 10-Q: the three months and the six months as two income statements, each titled with its span.
CHASE = """
<TABLE>
<CAPTION>
                                                CONSOLIDATED STATEMENT OF INCOME
                                                   Three Months Ended June 30,
                                              (in millions, except per share data)
                                                                                        1995                       1994
   <S>                                                                             <C>                         <C>
   Total Interest Income                                                               2,767                      2,219
   Total Interest Expense                                                              1,605                      1,034
   Net Interest Income                                                                 1,162                      1,185
   Income Before Income Tax Expense                                                      567                        511
   Income Tax Expense                                                                    227                        204
   Net Income                                                                       $    340                   $    307
</TABLE>
<TABLE>
<CAPTION>
                                                CONSOLIDATED STATEMENT OF INCOME
                                                    Six Months Ended June 30,
                                              (in millions, except per share data)
                                                                                        1995                       1994
   <S>                                                                             <C>                         <C>
   Total Interest Income                                                               5,433                      4,309
   Total Interest Expense                                                              3,082                      1,975
   Net Interest Income                                                                 2,351                      2,334
   Income Before Income Tax Expense                                                    1,397                      1,021
   Income Tax Expense                                                                    559                        408
   Net Income                                                                       $    838                   $    613
</TABLE>
"""


def test_a_statement_printed_again_for_other_periods_is_read():
    readings, _ = read(CHASE, "1231", date(1995, 6, 30))
    assert {line["label"]: line["value"] for line in readings[("income", "1995", "Q2", "quarter")].lines}["Net Income"] == 340e6
    assert {line["label"]: line["value"] for line in readings[("income", "1995", "Q2", "ytd")].lines}["Net Income"] == 838e6


# Coca-Cola's 2003 10-Q: a bottler's name wrapped onto a line further in, inside a subtotal.
KO_2003 = """
                    CONDENSED CONSOLIDATED BALANCE SHEETS
                                (In millions)
<TABLE>
<CAPTION>
                                              June 30,      December 31,
                                                  2003              2002
<S>                                           <C>               <C>
TOTAL CURRENT ASSETS                            9,225             7,352
INVESTMENTS AND OTHER ASSETS
      Equity method investments
          Coca-Cola Enterprises Inc.            1,084               972
          Coca-Cola Hellenic Bottling
            Company S.A.                        1,066               872
          Coca-Cola Amatil Limited                589               492
      Other assets                              2,978             2,694
                                                5,717             5,030
TOTAL ASSETS                               $   14,942        $   12,382
TOTAL LIABILITIES                               8,000             7,000
SHARE-OWNERS' EQUITY                            6,942             5,382
TOTAL LIABILITIES AND SHARE-OWNERS' EQUITY $   14,942        $   12,382
</TABLE>
"""


def test_a_name_wrapped_onto_a_line_further_in():
    readings, _ = read(KO_2003, "1231", date(2003, 6, 30))
    balance = readings[("balance", "2003", "Q2", "instant")]
    assert {line["label"]: line["value"] for line in balance.lines}["Coca-Cola Hellenic Bottling Company S.A."] == 1_066e6
    assert balance.status == "kept"


def test_a_reconciliation_headed_income_statement_is_not_the_statement():
    # Chase's 1998 10-K: an MD&A table reconciling reported results to an operating basis, longer than the statement.
    rows = "".join(f"  Line {n:<36}{n * 10:>10}{n:>10}{n * 10 + n:>10}\n" for n in range(1, 12))
    reconciliation = ("<TABLE>\n<CAPTION>\n" + " " * 48 + "1998\n"
                      "Year Ended December 31,                   Reported  Special  Operating\n"
                      "(in millions, except per share data)\n<S> <C> <C> <C>\n"
                      "INCOME STATEMENT\n" + rows + "Income Before Income Tax Expense" + " " * 16 + "500        10       510\n"
                      "Net Income" + " " * 38 + "300        10       310\n</TABLE>\n")
    statement = ("Consolidated Statement of Income\n<TABLE>\n<CAPTION>\nYear ended December 31, (in millions)" + " " * 11
                 + "1998\n<S> <C>\n" + "".join(f"Item {n:<43}{n:>10}\n" for n in range(1, 4))
                 + "Income Before Income Tax Expense" + " " * 18 + "5,930\nIncome Tax Expense" + " " * 32 + "2,148\n"
                 + "NET INCOME" + " " * 38 + "$ 3,782\n</TABLE>\n")
    readings, _ = read(reconciliation + statement, "1231", date(1998, 12, 31), form="10-K")
    values = {line["label"]: line["value"] for line in readings[("income", "1998", "FY", "year")].lines}
    assert values["NET INCOME"] == 3_782e6


def test_one_year_over_each_column_in_order():
    # Apple's FY1997 10-K: "1997" sits four characters off its column, "1996" three.
    text = ("CONSOLIDATED BALANCE SHEETS\n(DOLLARS IN MILLIONS)\n<TABLE>\n<CAPTION>\n"
            "SEPTEMBER 26, 1997 AND SEPTEMBER 27, 1996" + " " * 59 + "1997       1996\n<S> <C> <C>\n"
            + "".join(f"{label + '.' * (98 - len(label))}  {a:>9}  {b:>9}\n" for label, a, b in (
                ("Cash and cash equivalents", "$   1,230", "$   1,552"), ("Total current assets", "3,424", "5,224"),
                ("Other assets", "309", "140"), ("Total assets", "$   4,233", "$   5,364"),
                ("Total liabilities", "2,033", "2,306"), ("Total shareholders' equity", "1,200", "2,058"),
                ("Total liabilities and shareholders' equity", "$   4,233", "$   5,364"))) + "</TABLE>\n")
    readings, _ = read(text, "0930", date(1997, 9, 26), form="10-K")
    values = {line["label"]: line["value"] for line in readings[("balance", "1997", "FY", "instant")].lines}
    assert values["Cash and cash equivalents"] == 1_230e6


# ASML's FY2002 20-F: note numbers in the margin, a currency under each year and a translation of the latest year.
ASML_2002 = """<TABLE>
<CAPTION>
CONSOLIDATED STATEMENTS OF OPERATIONS
                                                     For the year ended December 31
                                                    2001            2002           2002
                                                     EUR             EUR            USD
                                         (Amounts in thousands, except per share data)
<S>                                               <C>             <C>            <C>
        Total net sales......................     1,589,247       1,958,672      2,053,976
3       Total cost of sales..................     1,558,234       1,491,068      1,563,620
        Income (loss) before income taxes....      (594,219)       (130,824)      (137,190)
17      (Provision for) benefits from taxes..       179,017          42,779         44,861
        Net income (loss)....................      (415,202)        (88,045)       (92,329)
</TABLE>
"""


def test_margin_note_numbers_and_a_translated_year():
    readings, notes = read(ASML_2002, "1231", date(2002, 12, 31), form="20-F")
    assert sorted(key[1] for key in readings) == ["2001", "2002"]
    latest = readings[("income", "2002", "FY", "year")]
    assert [line["label"] for line in latest.lines][:2] == ["Total net sales", "Total cost of sales"]
    assert latest.lines[1]["level"] == latest.lines[0]["level"] and latest.currency == "EUR"
    assert latest.lines[0]["value"] == 1_958_672e3 and latest.status == "kept"
