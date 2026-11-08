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
