from datetime import date

from statement_extractor.selection.choose import choose
from statement_extractor.selection.vintage import mixed_vintage_q4s
from statement_extractor.domain.model import Check, Reading


def reading(accession, filed, value, own, result="pass", statement="income", period="FY", duration="year"):
    return Reading(statement=statement, duration=duration, fiscal_year="2009", fiscal_period=period,
                   period_end=date(2009, 9, 26), period_start=None, currency="USD", accession=accession, form="10-K",
                   filed=filed, own=own, source="xbrl",
                   lines=[{"line": 0, "concept": "us-gaap_Revenues", "label": "Net sales", "level": 0,
                           "is_heading": False, "value": value}],
                   checks=[Check("calc", result)])


def test_the_newest_filing_whose_arithmetic_holds_wins_and_its_source_is_recorded():
    own = reading("own", date(2009, 10, 27), 36_537e6, own=True)
    restated = reading("later", date(2010, 10, 27), 42_905e6, own=False)
    broken = reading("newest", date(2011, 10, 26), 1.0, own=False, result="fail")
    [chosen] = choose([own, restated, broken])
    assert chosen["status"] == "kept" and chosen["source_accession"] == "later" and chosen["from_comparative"]
    assert chosen["own_accession"] == "own"
    assert chosen["versus_own"]["changed"] == ["us-gaap_Revenues"]
    assert chosen["rejections"] and chosen["rejections"][0].startswith("newest")


def test_no_kept_reading_says_why():
    [chosen] = choose([reading("a", date(2009, 10, 27), 1, own=True, result="fail")])
    assert chosen["status"] == "rejected" and "lines" not in chosen
    [chosen] = choose([reading("a", date(2009, 10, 27), 1, own=True, result="none")])
    assert chosen["status"] == "unproven"


def test_a_fourth_quarter_from_a_restated_year_and_an_unrestated_nine_months_is_flagged():
    year = [reading("own", date(2009, 10, 27), 100, own=True), reading("later", date(2010, 10, 27), 110, own=False)]
    nine = [reading("q3", date(2009, 7, 22), 70, own=True, period="Q3", duration="ytd")]
    assert mixed_vintage_q4s(choose(year + nine)) == ["income 2009"]


def test_a_sign_most_filings_print_wins_over_the_newest_that_dropped_it():
    # JPM's 2010 credit losses recognized in income: (100) in the FY2010 and FY2011 10-Ks, 100 in the FY2012 one.
    year = [reading("fy10", date(2011, 2, 28), -100e6, own=True), reading("fy11", date(2012, 2, 29), -100e6, own=False),
            reading("fy12", date(2013, 2, 28), 100e6, own=False)]
    [chosen] = choose(year)
    assert chosen["source_accession"] == "fy12" and chosen["lines"][0]["value"] == -100e6
    assert "versus_own" not in chosen or not chosen["versus_own"]["changed"]


def test_a_retagged_line_is_matched_by_its_label_for_its_sign():
    year = [reading("fy10", date(2011, 2, 28), -100e6, own=True), reading("fy11", date(2012, 2, 29), -100e6, own=False),
            reading("fy12", date(2013, 2, 28), 100e6, own=False)]
    year[2].lines[0]["concept"] = "us-gaap_RevenuesRetagged"
    [chosen] = choose(year)
    assert chosen["lines"][0]["value"] == -100e6


def test_the_filings_own_printed_statement_settles_a_sign():
    # JPM's FY2012 10-K: XBRL tags 2010 credit losses as 100, its printed statement shows (100).
    xbrl = reading("fy12", date(2013, 2, 28), 100e6, own=False)
    printed = reading("fy12", date(2013, 2, 28), -100e6, own=False)
    printed.source = "html"
    older = reading("fy11", date(2012, 2, 29), -100e6, own=False)
    [chosen] = choose([older, xbrl, printed])
    assert chosen["source_accession"] == "fy12" and chosen["lines"][0]["value"] == -100e6


def test_a_sign_only_change_is_not_a_restatement():
    own = reading("own", date(2011, 2, 28), -27e6, own=True)
    later = reading("later", date(2012, 2, 29), 27e6, own=False)
    [chosen] = choose([own, later])
    assert chosen["versus_own"]["changed"] == [] and chosen["versus_own"]["sign_only"] == ["us-gaap_Revenues"]


def test_a_sign_disagreement_asks_for_the_printed_statement():
    from statement_extractor.selection.signs import sign_ties
    older = reading("fy11", date(2012, 2, 29), -100e6, own=False)
    newer = reading("fy12", date(2013, 2, 28), 100e6, own=False)
    assert sign_ties([older, newer]) == {"fy12"}
    assert sign_ties([older, reading("fy12", date(2013, 2, 28), -100e6, own=False)]) == set()


def test_a_sign_printed_only_in_a_note_settles_it():
    # JPM's FY2012 10-K tags its 2010 credit losses as 100 and prints (100) only in a note, not on the statement's face.
    from statement_extractor.readers.html.parse import parse_html
    from statement_extractor.readers.printed.sign_index import printed_figures
    note = parse_html("<table><tr><td></td><td>2012</td><td>2011</td><td>2010</td></tr>"
                      "<tr><td>Net sales (a)</td><td>(28)</td><td>(76)</td><td>(100)</td></tr></table>")
    xbrl = reading("fy12", date(2013, 2, 28), 100e6, own=False)
    older = reading("fy11", date(2012, 2, 29), -100e6, own=False)
    [chosen] = choose([older, xbrl], {"fy12": printed_figures(note)})
    assert chosen["source_accession"] == "fy12" and chosen["lines"][0]["value"] == -100e6
    # Without a dispute the note never changes a sign.
    agreed = reading("fy11", date(2012, 2, 29), 100e6, own=False)
    [chosen] = choose([agreed, reading("fy12", date(2013, 2, 28), 100e6, own=False)], {"fy12": printed_figures(note)})
    assert chosen["lines"][0]["value"] == 100e6


def test_a_comparative_column_takes_its_periods_own_dates():
    from statement_extractor.selection.period_dates import align_dates
    own = reading("fy06", date(2006, 12, 1), 1, own=True)
    own.period_end, own.period_start = date(2006, 9, 30), date(2005, 9, 25)
    later = reading("fy07", date(2007, 11, 15), 1, own=False)
    later.period_end, later.period_start = date(2006, 9, 29), date(2005, 9, 30)  # dated from the FY2007 header
    far = reading("fy08", date(2008, 11, 5), 1, own=False)
    far.period_end = date(2006, 12, 31)
    aligned = {r.accession: r for r in align_dates([own, later, far])}
    assert aligned["fy07"].period_end == date(2006, 9, 30) and aligned["fy07"].period_start == date(2005, 9, 25)
    assert aligned["fy08"].period_end == date(2006, 12, 31)  # more than a week apart: another period, left alone


def test_a_period_restated_on_a_new_basis_keeps_its_first_basis_on_record():
    # TSMC 2012: reported on ROC GAAP in its FY2012 20-F, printed again on IFRS by the FY2013 one.
    first = reading("fy2012", date(2013, 4, 2), 506_249e6, own=True)
    first.basis = "ROC GAAP"
    again = reading("fy2013", date(2014, 4, 14), 506_754e6, own=False)
    again.basis = "IFRS"
    [chosen] = choose([first, again])
    assert chosen["basis"] == "IFRS" and chosen["source_accession"] == "fy2013"
    assert [(item["basis"], item["source_accession"]) for item in chosen["other_bases"]] == [("ROC GAAP", "fy2012")]


def test_a_label_printed_twice_takes_each_occurrences_own_sign():
    # Salesforce FY2012 cash flows: the excess tax benefit is taken away under operating and added under financing.
    from statement_extractor.selection.signs import _signs_from
    def flows(source, operating, financing):
        found = reading("crm", date(2014, 3, 5), 0, own=False, statement="cash_flow")
        found.source = source
        found.lines = [{"line": 0, "concept": "c", "label": "Excess tax benefits from employee stock plans", "level": 0,
                        "is_heading": False, "value": operating},
                       {"line": 1, "concept": "c", "label": "Excess tax benefits from employee stock plans", "level": 0,
                        "is_heading": False, "value": financing}]
        return found
    settled = _signs_from(flows("xbrl", -6018, -6018), flows("html", -6018, 6018))
    assert [line["value"] for line in settled.lines] == [-6018, 6018]


def test_among_releases_the_annual_reports_basis_wins():
    # ASML 2011 Q2: its US GAAP release, and a December 2012 6-K printing the quarter again on IFRS.
    annual = reading("20-F", date(2012, 2, 14), 1, own=True)
    annual.basis, annual.form = "US GAAP", "20-F"
    first = reading("q2", date(2011, 7, 13), 827.1e6, own=True, period="Q2", duration="ytd")
    first.basis, first.form = "US GAAP", "6-K"
    again = reading("ifrs", date(2012, 12, 14), 826.9e6, own=False, period="Q2", duration="ytd")
    again.basis, again.form = "IFRS", "6-K"
    chosen = {item["fiscal_period"]: item for item in choose([annual, first, again])}
    assert chosen["Q2"]["source_accession"] == "q2" and chosen["Q2"]["other_bases"][0]["basis"] == "IFRS"
