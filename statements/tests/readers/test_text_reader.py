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
