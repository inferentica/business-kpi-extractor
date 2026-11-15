from datetime import date
from types import SimpleNamespace

from statement_extractor.pipeline.releases import own_quarter


def _reading(end, period="Q1", own=False):
    return SimpleNamespace(period_end=end, fiscal_period=period, own=own)


def test_a_late_quarterly_report_is_its_own_quarters():
    # Copel's Q1 2008 report, filed 10 July 2008 and so listed as the Q2 release.
    found = [_reading(date(2008, 3, 31)), _reading(date(2007, 12, 31), "FY"), _reading(date(2007, 3, 31))]
    assert own_quarter(found, date(2008, 6, 30)) == date(2008, 3, 31)


def test_a_release_with_its_own_quarter_or_an_annual_report_keeps_its_date():
    assert own_quarter([_reading(date(2008, 6, 30), "Q2", own=True), _reading(date(2008, 3, 31))], date(2008, 6, 30)) is None
    assert own_quarter([_reading(date(2007, 12, 31), "FY")], date(2008, 3, 31)) is None  # an annual report furnished in April
    assert own_quarter([_reading(date(2007, 6, 30), "Q2")], date(2008, 6, 30)) is None  # a year back: another document's
    assert own_quarter([], date(2008, 6, 30)) is None
