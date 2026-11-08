from datetime import date

from statement_extractor.periods import Period, label_period, opening_instant, parse_period, presented_periods


def test_parse_and_durations():
    assert parse_period("instant_2009-09-26") == Period(date(2009, 9, 26))
    year = parse_period("duration_2008-09-28_2009-09-26")
    assert year.duration == "year" and year.days == 364
    assert parse_period("duration_2010-03-28_2010-06-26").duration == "quarter"
    assert parse_period("duration_2009-09-27_2010-06-26").duration == "ytd"
    assert parse_period("duration_2010-01-01_2010-08-31").duration is None  # an eight-month transition period
    assert parse_period("nonsense") is None


def test_labels_follow_the_company_calendar():
    # Apple's fiscal year ends on the last Saturday of September.
    assert label_period(parse_period("duration_2008-09-28_2009-09-26"), "0926", 0) == ("2009", "FY", "year")
    assert label_period(parse_period("duration_2009-09-27_2010-06-26"), "0926", 0) == ("2010", "Q3", "ytd")
    assert label_period(parse_period("duration_2010-03-28_2010-06-26"), "0926", 0) == ("2010", "Q3", "quarter")
    # A year-end balance sheet is the year's, as a 10-K stores it.
    assert label_period(Period(date(2009, 9, 26)), "0926", 0) == ("2009", "FY", "instant")
    assert label_period(Period(date(2010, 6, 26)), "0926", 0) == ("2010", "Q3", "instant")


def test_presented_periods_drop_a_period_only_one_line_carries():
    rows = [{"values": {"duration_2008-09-28_2009-09-26": 1, "duration_2007-09-30_2008-09-27": 1}} for _ in range(5)]
    rows.append({"values": {"duration_2006-10-01_2007-09-29": 1}})
    assert [period.end for period in presented_periods(rows, instants=False)] == [date(2009, 9, 26), date(2008, 9, 27)]


def test_opening_balance_is_the_day_before_the_period_starts():
    instants = {date(2008, 9, 27): "instant_2008-09-27", date(2009, 9, 26): "instant_2009-09-26"}
    assert opening_instant(parse_period("duration_2008-09-28_2009-09-26"), instants) == "instant_2008-09-27"
    assert opening_instant(parse_period("duration_2008-10-01_2009-09-26"), instants) == "instant_2008-09-27"
    assert opening_instant(Period(date(2009, 9, 26)), instants) is None
