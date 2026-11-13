from datetime import date

import pytest

from kpi_extractor.domain.fiscal import FiscalCalendarError, fiscal_label, learn_year_offset, nominal_quarter_end_before


@pytest.mark.parametrize(("period_end", "year_end", "annual", "expected"), [
    (date(2026, 6, 30), "1231", False, ("2026", "Q2")),
    (date(2026, 12, 31), "1231", True, ("2026", "FY")),
    (date(2026, 7, 26), "0125", False, ("2027", "Q2")),  # NVIDIA's 52/53-week year ends in late January
    (date(2026, 1, 25), "0125", True, ("2026", "FY")),
    (date(2025, 12, 27), "0926", False, ("2026", "Q1")),  # Apple
    (date(2026, 6, 27), "0926", False, ("2026", "Q3")),
    (date(2026, 2, 1), "0131", True, ("2026", "FY")),  # 53-week retail year ending a day late
])
def test_fiscal_label(period_end, year_end, annual, expected):
    assert fiscal_label(period_end, year_end, annual=annual) == expected


def test_fiscal_label_applies_the_company_naming_offset():
    # Home Depot calls the year ending February 2026 "fiscal 2025".
    assert fiscal_label(date(2026, 2, 1), "0201", annual=True, year_offset=-1) == ("2025", "FY")


def test_fiscal_label_rejects_dates_off_the_calendar():
    with pytest.raises(FiscalCalendarError):
        fiscal_label(date(2026, 5, 15), "1231")
    with pytest.raises(FiscalCalendarError):
        fiscal_label(date(2026, 6, 30), "1231", annual=True)


def test_nominal_quarter_end_before():
    assert nominal_quarter_end_before(date(2026, 7, 16), "1231") == date(2026, 6, 30)
    assert nominal_quarter_end_before(date(2026, 7, 3), "1231") == date(2026, 3, 31)
    assert nominal_quarter_end_before(date(2026, 1, 28), "1231") == date(2025, 12, 31)
    assert nominal_quarter_end_before(date(2026, 7, 30), "0926") == date(2026, 6, 26)


def test_learn_year_offset():
    assert learn_year_offset([(date(2026, 7, 26), "2027", False)], "0125") == 0
    assert learn_year_offset([(date(2026, 2, 1), "2025", True)], "0201") == -1
    assert learn_year_offset([], "1231") == 0


@pytest.mark.parametrize(("period_end", "year_end", "expected"), [
    # PepsiCo: 12, 12, 12 and 16 weeks to the last Saturday of December.
    (date(2025, 3, 22), "1227", ("2025", "Q1")), (date(2025, 6, 14), "1227", ("2025", "Q2")),
    (date(2025, 9, 6), "1227", ("2025", "Q3")), (date(2025, 12, 27), "1227", ("2025", "Q4")),
    # Costco: 12, 12, 12 and 16 weeks to the Sunday nearest August 31.
    (date(2024, 11, 24), "0831", ("2025", "Q1")), (date(2025, 2, 16), "0831", ("2025", "Q2")),
    (date(2025, 5, 11), "0831", ("2025", "Q3")), (date(2025, 8, 31), "0831", ("2025", "Q4")),
    # Kroger: a 16-week first quarter, then three of 12 weeks, to the Saturday nearest January 31.
    (date(2025, 5, 24), "0131", ("2026", "Q1")), (date(2025, 8, 16), "0131", ("2026", "Q2")),
    (date(2025, 11, 8), "0131", ("2026", "Q3")), (date(2026, 1, 31), "0131", ("2026", "Q4")),
    # 13-week retail quarters and a 14-week fourth quarter in a 53-week year (Broadcom's fiscal 2023).
    (date(2025, 4, 30), "0131", ("2026", "Q1")), (date(2023, 10, 29), "1029", ("2023", "Q4")),
])
def test_fiscal_label_knows_week_based_calendars(period_end, year_end, expected):
    assert fiscal_label(period_end, year_end) == expected


def test_a_release_is_matched_to_the_companys_own_quarter_end():
    # Costco reported its third quarter (ended May 11, 2025) on May 29, before the month-end guess of May 31; its
    # reports give last year's third quarter (May 12, 2024), so this year's is about 52 weeks later.
    assert nominal_quarter_end_before(date(2025, 5, 29), "0831") == date(2025, 2, 28)
    assert nominal_quarter_end_before(date(2025, 5, 29), "0831", own_ends=[date(2024, 5, 12), date(2025, 2, 16)]) == date(2025, 5, 11)
    # Kroger's 16-week first quarter ended May 24, 2025: its own end replaces the April 30 guess.
    assert nominal_quarter_end_before(date(2025, 6, 20), "0131", own_ends=[date(2024, 5, 25)]) == date(2025, 5, 24)
    # A filed quarter's own end wins over the projection, and a month-end company is unchanged.
    assert nominal_quarter_end_before(date(2025, 10, 9), "1227", own_ends=[date(2025, 9, 6), date(2024, 9, 7)]) == date(2025, 9, 6)
    assert nominal_quarter_end_before(date(2026, 7, 16), "1231", own_ends=[date(2025, 6, 30), date(2026, 3, 31)]) == date(2026, 6, 30)


def test_report_period_ends_reads_the_filing_index():
    from types import SimpleNamespace

    from kpi_extractor.sources.sec import report_period_ends

    filings = [SimpleNamespace(period_of_report="2025-05-11"), SimpleNamespace(report_date=date(2024, 5, 12)), SimpleNamespace()]
    company = SimpleNamespace(get_filings=lambda **_: filings)
    assert report_period_ends(company, date(2025, 1, 1)) == (date(2024, 5, 12), date(2025, 5, 11))


def test_a_stated_year_end_far_from_the_annual_reports_gives_way():
    from datetime import date as day
    from types import SimpleNamespace
    from kpi_extractor.sources.sec import annual_report_year_end, year_ends_agree
    report = lambda end: SimpleNamespace(get_filings=lambda form: SimpleNamespace(latest=lambda n: SimpleNamespace(period_of_report=end)))
    assert annual_report_year_end(report("2025-12-31"), "0513") == "1231"  # Sun Communities
    assert annual_report_year_end(report("2026-01-31"), "0130") == "0130"  # Dollar Tree's 52/53 weeks: a day off stands
    assert annual_report_year_end(report("2026-01-02"), "1231") == "1231"  # across the new year
    assert year_ends_agree("0926", day(2025, 9, 27)) and not year_ends_agree("0513", day(2025, 12, 31))
    assert annual_report_year_end(SimpleNamespace(get_filings=lambda form: (_ for _ in ()).throw(ValueError())), "0630") == "0630"


def test_the_annual_reports_form_decides_whether_results_come_on_6_k():
    from types import SimpleNamespace
    from kpi_extractor.sources.sec import files_as_foreign
    assert files_as_foreign(SimpleNamespace(is_foreign=False), SimpleNamespace(form="40-F"))  # Bank of Nova Scotia
    assert not files_as_foreign(SimpleNamespace(is_foreign=True), SimpleNamespace(form="10-K"))  # JFrog, Tronox
    assert files_as_foreign(SimpleNamespace(is_foreign=True), SimpleNamespace(form="20-F"))
    assert files_as_foreign(SimpleNamespace(is_foreign=True), None)  # no annual report yet


def test_a_filing_days_after_a_quarter_closes_reports_that_quarter():
    from datetime import date as day
    from kpi_extractor.sources.sec import release_period
    assert release_period(day(2025, 10, 2), "1231") == day(2025, 9, 30)    # Tesla's Q3 deliveries, two days on
    assert release_period(day(2025, 10, 22), "1231") == day(2025, 9, 30)   # an ordinary results release
    assert release_period(day(2025, 12, 10), "1231") == day(2025, 9, 30)   # late in the window, still the quarter before
    assert release_period(day(2025, 7, 2), "0630") == day(2025, 6, 30)     # a June year end the same way


def test_one_mistagged_report_does_not_rename_every_fiscal_year():
    from kpi_extractor.domain.fiscal import offset_lead
    # Oracle (year ends May 31): its 10-Q for August 31, 2026 names fiscal 2026; its 10-K and Q3 10-Q the year they close.
    samples = [(date(2026, 8, 31), "2026", False), (date(2026, 5, 31), "2026", True), (date(2026, 2, 28), "2026", False)]
    assert learn_year_offset(samples[:1], "0531") == -1
    assert offset_lead(samples[:2], "0531") == 1  # not yet settled: one more report is read
    assert learn_year_offset(samples, "0531") == 0 and offset_lead(samples, "0531") == 2
    assert fiscal_label(date(2026, 8, 31), "0531", year_offset=0) == ("2027", "Q1")
    # A tie goes to the newest report.
    assert learn_year_offset([(date(2026, 7, 26), "2027", False), (date(2026, 4, 26), "2026", False)], "0125") == 0


def test_the_window_reads_the_earlier_reports_of_its_first_fiscal_year():
    # Amazon: the window opens on its Q2 2016 10-Q, whose year-ago columns give Q2 2015; FY2015 comes from the next 10-K.
    # Q4 2015, the year less three quarters, needs Q1 2015, which only the Q1 2016 10-Q gives: it is read too, and
    # nothing older (the FY2015 10-K and Q3 2015 10-Q stay out).
    from types import SimpleNamespace
    from kpi_extractor.domain.fiscal import with_first_year
    ref = lambda form, filed: SimpleNamespace(form=form, filed=filed)
    reports = [ref("10-Q", date(2015, 10, 23)), ref("10-K", date(2016, 1, 29)), ref("10-Q", date(2016, 4, 29)),
               ref("10-Q", date(2016, 7, 29)), ref("10-Q", date(2016, 10, 28))]
    kept = with_first_year(reports, date(2016, 5, 15), "1231")
    assert [item.filed for item in kept] == [date(2016, 4, 29), date(2016, 7, 29), date(2016, 10, 28)]
    # Apple, fiscal years ending in late September: a window opening on its fiscal Q3 2016 10-Q takes fiscal Q1 and Q2.
    apple = [ref("10-K", date(2015, 10, 28)), ref("10-Q", date(2016, 1, 27)), ref("10-Q", date(2016, 4, 27)),
             ref("10-Q", date(2016, 7, 27))]
    assert [item.filed for item in with_first_year(apple, date(2016, 5, 15), "0924")] == \
        [date(2016, 1, 27), date(2016, 4, 27), date(2016, 7, 27)]
    # No fiscal calendar known: the window as it was.
    assert [item.filed for item in with_first_year(reports, date(2016, 5, 15), None)] == [date(2016, 7, 29), date(2016, 10, 28)]


def test_the_release_window_also_starts_at_its_first_fiscal_years_q1():
    # Releases carry their own period: a window opening on the Q2 2016 release reads the Q1 2016 one too, not Q4 2015's.
    from types import SimpleNamespace
    from kpi_extractor.domain.fiscal import with_first_year
    release = lambda filed, end: SimpleNamespace(form="8-K", filed=filed, period_end=end)
    releases = [release(date(2016, 1, 28), date(2015, 12, 31)), release(date(2016, 4, 28), date(2016, 3, 31)),
                release(date(2016, 7, 28), date(2016, 6, 30)), release(date(2016, 10, 27), date(2016, 9, 30))]
    kept = with_first_year(releases, date(2016, 5, 15), "1231")
    assert [item.period_end for item in kept] == [date(2016, 3, 31), date(2016, 6, 30), date(2016, 9, 30)]
