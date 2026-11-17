from datetime import date

from statement_extractor.domain.model import Reading
from statement_extractor.domain.naming import Naming, name_readings
from statement_extractor.domain.periods import Period


def reading(start: date | None, end: date, duration: str, form: str = "10-Q") -> Reading:
    return Reading(statement="balance" if start is None else "income", duration=duration, fiscal_year="0",
                   fiscal_period="Q1", period_end=end, period_start=start, currency="USD", accession="x", form=form,
                   filed=end, own=False, source="html")


def test_one_quarterly_slip_never_names_the_years():
    # RPM's 10-Q for the quarter ended August 31, 2026 states fiscal 2026; its 10-Ks name each year for its May end.
    ends = [date(year, 5, 31) for year in range(2020, 2027)]
    samples = [(date(2026, 8, 31), "2026", False)] + [(end, str(end.year), True) for end in ends]
    naming = Naming(ends, "0531", samples)
    assert naming.label(Period(date(2025, 5, 31), date(2024, 6, 1)))[0] == ("2025", "FY", "year")
    assert naming.label(Period(date(2026, 8, 31), date(2026, 6, 1)))[0] == ("2027", "Q1", "quarter")


def test_a_year_named_for_the_year_it_mostly_covers():
    # Kroger's fiscal 2015 ends January 30, 2016.
    ends = [date(2016, 1, 30), date(2017, 1, 28), date(2018, 2, 3)]
    naming = Naming(ends, "0130", [(date(2018, 2, 3), "2017", True), (date(2017, 1, 28), "2016", True)])
    assert naming.label(Period(date(2016, 1, 30), date(2015, 2, 1)))[0] == ("2015", "FY", "year")


GILDAN_ENDS = [date(2012, 9, 30), date(2013, 9, 29), date(2014, 10, 5), date(2016, 1, 3), date(2017, 1, 1),
               date(2017, 12, 31), date(2018, 12, 30)]
GILDAN_STATED = [(date(2017, 12, 31), "2017", True), (date(2018, 12, 30), "2018", True), (date(2017, 1, 1), "2016", True)]


def test_each_era_names_its_own_years():
    # Gildan closed its years in late September or early October until fiscal 2014, then in early January; only the
    # January years are in XBRL.
    naming = Naming(GILDAN_ENDS, "0103", GILDAN_STATED)
    assert naming.label(Period(date(2014, 10, 5), date(2013, 10, 7)))[0] == ("2014", "FY", "year")
    assert naming.label(Period(date(2014, 7, 6), date(2014, 4, 7)))[0] == ("2014", "Q3", "quarter")
    assert naming.label(Period(date(2014, 4, 6), date(2013, 10, 7)))[0] == ("2014", "Q2", "ytd")
    assert naming.label(Period(date(2016, 1, 3), date(2015, 1, 5)))[0] == ("2015", "FY", "year")
    assert naming.label(Period(date(2015, 7, 5), date(2015, 1, 5)))[0] == ("2015", "Q2", "ytd")


def test_the_new_eras_months_before_its_first_year_are_left_out():
    # Gildan's transition quarter to January 4, 2015, and the twelve months to that day it presents again, would both
    # take fiscal 2014, which its October year end already names.
    naming = Naming(GILDAN_ENDS, "0103", GILDAN_STATED)
    assert naming.label(Period(date(2015, 1, 4), date(2014, 10, 6))) == (None, "recast")
    assert naming.label(Period(date(2015, 1, 4), date(2014, 1, 6))) == (None, "recast")
    assert naming.label(Period(date(2015, 1, 4))) == (None, "recast")


def test_a_recast_calendar_quarter_takes_the_old_eras_name():
    # Oshkosh closed its years on September 30 until fiscal 2021, then on December 31; its 2022 10-Qs present the
    # calendar quarters of 2021 again.
    ends = [date(2019, 9, 30), date(2020, 9, 30), date(2021, 9, 30), date(2022, 12, 31), date(2023, 12, 31)]
    naming = Naming(ends, "1231", [(date(2022, 12, 31), "2022", True), (date(2021, 9, 30), "2021", True)])
    assert naming.label(Period(date(2021, 3, 31), date(2021, 1, 1)))[0] == ("2021", "Q2", "quarter")
    assert naming.label(Period(date(2021, 6, 30), date(2021, 1, 1))) == (None, "year to date")  # six months from January
    assert naming.label(Period(date(2021, 6, 30), date(2020, 10, 1)))[0] == ("2021", "Q3", "ytd")
    assert naming.label(Period(date(2021, 12, 31), date(2021, 10, 1))) == (None, "recast")  # the transition quarter
    assert naming.label(Period(date(2022, 3, 31), date(2022, 1, 1)))[0] == ("2022", "Q1", "quarter")


def test_name_readings_relabels_and_leaves_out():
    naming = Naming(GILDAN_ENDS, "0103", GILDAN_STATED)
    kept, left_out = name_readings([reading(date(2014, 4, 7), date(2014, 7, 6), "quarter"),
                                    reading(date(2014, 10, 6), date(2015, 1, 4), "quarter")], naming)
    assert [(item.fiscal_year, item.fiscal_period) for item in kept] == [("2014", "Q3")]
    assert left_out == {"recast": 1}


def test_a_lone_odd_year_end_is_no_era():
    ends = [date(2010, 12, 31), date(2011, 12, 31), date(2012, 6, 30), date(2013, 12, 31)]
    naming = Naming(ends, "1231")
    assert len(naming.eras) == 1
    assert naming.label(Period(date(2012, 12, 31), date(2012, 1, 1)))[0] == ("2012", "FY", "year")
