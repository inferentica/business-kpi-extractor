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


def test_a_release_reports_the_latest_quarter_end_five_days_before_it():
    from datetime import date
    from statement_extractor.domain.calendar import quarter_end_before
    assert quarter_end_before(date(2003, 4, 16), "1231") == date(2003, 3, 31)
    assert quarter_end_before(date(2010, 1, 3), "1231") == date(2009, 9, 30)
    assert quarter_end_before(date(2010, 2, 20), "0926") == date(2009, 12, 26)
