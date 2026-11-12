from datetime import date

from statement_extractor.readers.printed.ranges import counted_start


def test_whole_months_back_from_a_months_last_day():
    # Round 80: about 25,000 readings opened a day off, spans counted back in days.
    assert counted_start(date(2014, 6, 30), 182) == date(2014, 1, 1)  # six months, not from 31 December
    assert counted_start(date(2022, 12, 31), 364) == date(2022, 1, 1)  # a year, not from 2 January
    assert counted_start(date(2026, 1, 31), 91) == date(2025, 11, 1)  # Scotiabank's quarter to January
    assert counted_start(date(2010, 3, 31), 364) == date(2009, 4, 1)  # James Hardie's year to March
    assert counted_start(date(2015, 9, 30), 273) == date(2015, 1, 1)
    assert counted_start(date(2016, 2, 29), 91) == date(2015, 12, 1)


def test_days_back_where_a_period_ends_inside_its_month_or_runs_in_weeks():
    assert counted_start(date(2024, 9, 1), 364) == date(2023, 9, 3)  # Costco's 52-week year
    assert counted_start(date(2014, 6, 29), 91) == date(2014, 3, 31)  # ASML's quarter to its last Sunday
    assert counted_start(date(2025, 5, 31), 84, weekly=True) == date(2025, 3, 9)  # "Twelve weeks ended May 31"
