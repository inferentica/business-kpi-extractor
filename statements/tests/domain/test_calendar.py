from datetime import date

from statement_extractor.domain.calendar import year_end_for


def test_a_filing_takes_the_year_end_of_the_annual_report_that_closes_its_year():
    # Goldman Sachs: years ended on the last Friday of November until 2008, then in December.
    annual = [date(2007, 11, 30), date(2008, 11, 28), date(2009, 12, 31), date(2010, 12, 31)]
    assert year_end_for(date(2008, 8, 29), annual, "1231") == "1128"
    assert year_end_for(date(2008, 11, 28), annual, "1231") == "1128"
    assert year_end_for(date(2009, 6, 26), annual, "1231") == "1231"
    assert year_end_for(date(2011, 3, 31), annual, "1231") == "1231"  # no annual report yet: the usual year end


def test_a_52_53_week_year_keeps_its_usual_year_end():
    annual = [date(2006, 9, 30), date(2007, 9, 29)]
    assert year_end_for(date(2007, 6, 30), annual, "0926") == "0926"
