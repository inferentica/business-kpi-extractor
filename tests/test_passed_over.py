from datetime import date, timedelta
from types import SimpleNamespace

from kpi_extractor import sec
from kpi_extractor.sec import CompanyProfile, earnings_releases


def _filing(accession, filed):
    return SimpleNamespace(accession_no=accession, form="6-K", filing_date=filed, filing_url=f"https://sec.gov/{accession}")


def test_recent_6ks_that_are_not_candidates_are_passed_over(monkeypatch):
    today = date.today()
    quarter_end = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)
    in_window = quarter_end + timedelta(days=8)
    filings = [_filing("no-exhibit", in_window), _filing("release", in_window), _filing("old", today - timedelta(days=60))]
    if in_window > today:
        return  # the first week after a quarter end has no in-window day yet
    company = SimpleNamespace(get_filings=lambda **_: filings)
    exhibit = SimpleNamespace(url="https://sec.gov/ex99.htm")
    monkeypatch.setattr(sec, "_archived_exhibits", lambda filing, archive: [exhibit] if filing.accession_no == "release" else [])
    profile = CompanyProfile("TSM", 1, "TSMC", "1231", True)
    passed = []
    refs = earnings_releases(company, profile, date(2020, 1, 1), passed=passed)
    assert [ref.accession for ref in refs] == ["release"]
    assert [ref.accession for ref in passed] == ["no-exhibit"]
