from datetime import date

import pytest

from kpi_extractor.fiscal import FiscalCalendarError, fiscal_label, learn_year_offset, nominal_quarter_end_before


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
