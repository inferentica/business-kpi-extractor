"""How a printed line reads (readers/printed/line_kind.py): an amount, an amount a share or a share count. Each case is
a filing the reader misread before round 125's review, its stored figure given beside the filing's own."""
from datetime import date

from statement_extractor.domain.printed_labels import PER_SHARE, SHARE_COUNT
from statement_extractor.readers.html.read import read_document as read_html
from statement_extractor.readers.text.read import read_document as read_text


def _lines(readings, end):
    reading = next(r for r in readings if r.statement == "income" and r.period_end == end)
    return {line["label"]: (line.get("unit_kind"), line["value"]) for line in reading.lines if line["value"] is not None}


# Oshkosh's Q1 FY2004 10-Q (0000897069-04-000227): the scale caption wrapped over two lines, its tail "per share
# amounts)" read as a heading, so net sales were stored as 426,336 a share; and its dividends a share by class.
OSHKOSH = """
                   CONDENSED CONSOLIDATED STATEMENTS OF INCOME
                                   (Unaudited)
<TABLE>
<CAPTION>
                                                                           Three Months Ended
                                                                              December 31,
                                                                           2003              2002
                                                                           ----              ----
                                                                           (In thousands, except
                                                                             per share amounts)

<S>                                                                    <C>              <C>
Net sales                                                              $  493,194       $  426,336
Cost of sales                                                             404,772          368,697
                                                                        ---------        ---------
Gross income                                                               88,422           57,639
Operating expenses:
   Selling, general and administrative                                     40,031           35,675
                                                                        ---------        ---------
Operating income                                                           48,391           21,964
Provision for income taxes                                                 18,693            8,672
                                                                        ---------        ---------
Net income                                                             $   29,698       $   13,292
                                                                        =========        =========
Earnings per share                                                     $     0.85       $     0.33

Cash dividends:
   Class A Common Stock                                                $  0.05000       $  0.03750
   Common Stock                                                        $  0.05750       $  0.04313
</TABLE>
"""


def test_a_wrapped_scale_caption_is_no_per_share_heading():
    readings, _ = read_text(OSHKOSH, accession="a", form="10-Q", filed=date(2004, 2, 11), document_end=date(2003, 12, 31),
                            fiscal_year_end="0930", year_offset=0)
    lines = _lines(readings, date(2002, 12, 31))
    assert lines["Net sales"] == ("money", 426_336_000)
    assert lines["Gross income"] == ("money", 57_639_000)
    assert lines["Earnings per share"] == ("per_share", 0.33)
    # A share class under a dividends heading is its dividend a share.
    assert lines["Class A Common Stock"] == ("per_share", 0.0375)


# Century Aluminum's Q1 2001 10-Q (0000891554-01-502777): a share count's heading over two lines, so Basic and Diluted
# were stored as 20,339 and 20,450 a share; RPM's 2003 10-Q: a per-share line that starts "BASIC" is no count.
CENTURY = """
                    CONSOLIDATED STATEMENTS OF OPERATIONS
                (Dollars in thousands, except per share amounts)
<TABLE>
<CAPTION>
                                                       Three months ended March 31,
                                                          2001             2000
                                                          ----             ----
<S>                                                   <C>              <C>
NET SALES ...................................       $ 160,217        $ 163,020
NET INCOME ..................................       $   3,060        $   5,773
                                                      =========        ========
EARNINGS PER COMMON SHARE:
 Basic .......................................       $    0.15        $   0.28
 Diluted ......................................       $    0.15        $   0.28
WEIGHTED AVERAGE COMMON SHARES
 OUTSTANDING
 Basic ........................................          20,360          20,339
 Diluted ......................................          20,401          20,450
BASIC AND DILUTED EARNINGS PER SHARE OF COMMON STOCK    $    0.15        $   0.28
DIVIDENDS PER COMMON SHARE ....................       $    0.05        $   0.05
Total comprehensive income ...................       $   3,100        $   5,800
</TABLE>
"""


def test_a_share_count_heading_over_two_lines_counts_its_basic_and_diluted():
    readings, _ = read_text(CENTURY, accession="a", form="10-Q", filed=date(2001, 5, 15), document_end=date(2001, 3, 31),
                            fiscal_year_end="1231", year_offset=0)
    reading = next(r for r in readings if r.statement == "income" and r.period_end == date(2000, 3, 31))
    kinds = [(line["label"], line.get("unit_kind"), line["value"]) for line in reading.lines if line["value"] is not None]
    assert ("Basic", "per_share", 0.28) in kinds and ("Diluted", "per_share", 0.28) in kinds
    assert ("Basic", "shares", 20_339_000) in kinds and ("Diluted", "shares", 20_450_000) in kinds
    assert ("BASIC AND DILUTED EARNINGS PER SHARE OF COMMON STOCK", "per_share", 0.28) in kinds
    # A per-share heading's reach ends at a money line printed after its block (Welltower's comprehensive income).
    assert ("Total comprehensive income", "money", 5_800_000) in kinds


# Apple's Q1 FY1998 10-Q (0000320193-98-000003): a count label wrapped over three rows, its last row starting
# lower-case, stored as 139,839 a share; BCBP's 2009 10-Q: a bare "diluted" after a count; Alcon's 2004 release:
# "Basic weighted average common".
APPLE = """
                     CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS
                     (In thousands, except per share amounts)
<TABLE>
<CAPTION>
                                                    Three Months Ended
                                               December 26,      December 27,
                                                   1997              1996
<S>                                            <C>               <C>
Net sales                                       $1,578,000        $2,129,000
Net income (loss)                               $   47,000        $ (120,000)
Basic earnings (loss) per share                   $  0.37           $ (0.97)
Diluted earnings (loss) per share                 $  0.33           $ (0.97)
Common and common equivalent shares
used in the calculations of diluted
earnings (loss) per share (in thousands)          139,839           124,532
Weighted average number of common shares
outstanding- basic                                127,989           124,532
diluted                                           139,839           124,532
Basic weighted average common                     127,989           124,532
</TABLE>
"""


def test_count_labels_wrapped_or_named_by_their_words_are_counts():
    readings, _ = read_text(APPLE, accession="a", form="10-Q", filed=date(1998, 2, 9), document_end=date(1997, 12, 26),
                            fiscal_year_end="0926", year_offset=0)
    reading = next(r for r in readings if r.statement == "income" and r.period_end == date(1997, 12, 26))
    kinds = [(line["label"], line.get("unit_kind"), line["value"]) for line in reading.lines if line["value"] is not None]
    assert ("Basic earnings (loss) per share", "per_share", 0.37) in kinds
    counts = [value for label, kind, value in kinds if kind == "shares"]
    assert counts == [139_839_000, 127_989_000, 139_839_000, 127_989_000], kinds
    assert not [label for label, kind, value in kinds if kind == "per_share" and value > 1]


def test_share_counts_name_what_they_count_and_basic_ebitda_is_money():
    for money in ("Dividends on cumulative preferred shares", "Change in redemption value of redeemable preferred shares",
                  "(-) Repurchase of shares", "Payments to acquire or redeem entity's shares", "Employees' and managers' profit shares"):
        assert not SHARE_COUNT.search(money), money
    for count in ("Weighted average shares outstanding", "Registered common shares ('000)", "Basic shares",
                  "Ordinary shares in issue (weighted-average - basic)", "Shares used in computing earnings per share",
                  "Number of shares", "Shares per ADR"):
        assert SHARE_COUNT.search(count), count
    assert not PER_SHARE.search("Basic EBITDA") and PER_SHARE.search("Basic") and PER_SHARE.search("Diluted earnings")
    # A figure a share is no count, its count word (basic) beside it or not: Microchip's EPS were multiplied by its
    # 2021 split as if they were counts in round 125.
    assert not SHARE_COUNT.search("Basic net income per common share attributable to Microchip Technology")


# Andina's Q3 2026 release (0001104659-26-119205): a margin printed with "%" in a cell of its own, stored as money.
MARGIN = """<html><body>
<p>CONSOLIDATED INCOME STATEMENT</p><p>(In millions of Chilean pesos)</p>
<table>
<tr><td></td><td>3Q 2026</td><td>3Q 2025</td></tr>
<tr><td>Net sales</td><td>632,692</td><td></td><td>598,100</td><td></td></tr>
<tr><td>Gross profit</td><td>250,001</td><td></td><td>240,002</td><td></td></tr>
<tr><td>Gross margin</td><td>39.5</td><td>%</td><td>40.1</td><td>%</td></tr>
<tr><td>Net income</td><td>46,820</td><td></td><td>44,260</td><td></td></tr>
<tr><td>Net margin</td><td>7.4</td><td>%</td><td>7.4</td><td>%</td></tr>
</table></body></html>"""


def test_a_figure_before_a_percent_cell_is_no_figure():
    from statement_extractor.readers.html.parse import parse_html
    rows = [row for table in parse_html(MARGIN) for row in table.rows]
    by_label = {row.label: row.figures for row in rows if row.label}
    assert len(by_label["Net sales"]) == 2
    assert by_label["Gross margin"] == {} and by_label["Net margin"] == {}
