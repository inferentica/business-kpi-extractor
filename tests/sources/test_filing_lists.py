"""A company opens from the filing list Bedrock's worker keeps in the archive, asking SEC nothing, while that list is fresh
and complete; otherwise from SEC, as before."""
import copy
import gzip
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from kpi_extractor.sources import sec
from kpi_extractor.sources.archive import Archive

CIK = 320193
# Apple's list as SEC served it in November 2026, cut to eleven filings; the oldest three stand for an older page.
LISTING = json.loads((Path(__file__).parents[1] / "fixtures" / "sec_filing_list_aapl.json").read_text())
PAGE = "CIK0000320193-submissions-001.json"
NOW = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
LIST_KEY = f"filings/{CIK}/submissions/CIK0000320193.json.gz"
PAGE_KEY = f"filings/{CIK}/submissions/{PAGE}.gz"


def _split():
    """The list with its three oldest filings moved to an older page, as SEC splits a long history."""
    listing = copy.deepcopy(LISTING)
    recent = listing["filings"]["recent"]
    page = {field: values[-3:] for field, values in recent.items()}
    for field in recent:
        recent[field] = recent[field][:-3]
    listing["filings"]["files"] = [{"name": PAGE, "filingCount": 3, "filingFrom": "2023-11-03", "filingTo": "2024-11-01"}]
    return listing, page


class Control:
    def __init__(self, files, written):
        self.files, self.written = files, written

    def call(self, operation, **payload):
        assert operation == "archive_list"
        return {"files": self.files, "written": self.written}


def _archive(monkeypatch, stored: dict[str, bytes], written: dict[str, datetime] | None = None, logged=None):
    links = {key: f"https://r2.example.test/{key}" for key in stored}
    times = {key: when.isoformat().replace("+00:00", "Z") for key, when in (written or {}).items()}
    archive = Archive(Control(links, times), "AAPL", CIK, log=(logged.append if logged is not None else lambda *_: None))

    class Reply:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    by_link = {links[key]: body for key, body in stored.items()}
    monkeypatch.setattr("kpi_extractor.sources.archive.urllib.request.urlopen", lambda link, timeout=0: Reply(by_link[link]))
    return archive


def _gz(data) -> bytes:
    return gzip.compress(json.dumps(data).encode())


def _no_sec(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("SEC was asked")
    monkeypatch.setattr(sec, "company_profile", refuse)
    monkeypatch.setattr("edgar.httprequests.download_json", refuse)
    monkeypatch.setattr("edgar.entity.core.find_cik", refuse)


def _fresh(monkeypatch, logged=None, **extra):
    listing, page = _split()
    stored = {LIST_KEY: _gz(listing), PAGE_KEY: _gz(page), **extra}
    written = {LIST_KEY: NOW - timedelta(minutes=20), PAGE_KEY: NOW - timedelta(days=3)}
    archive = _archive(monkeypatch, stored, written, logged)
    monkeypatch.setattr("kpi_extractor.sources.archive.datetime", _Clock)
    return archive


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def test_a_fresh_archived_list_opens_the_company_and_lists_its_older_page_without_asking_sec(monkeypatch):
    archive = _fresh(monkeypatch)
    _no_sec(monkeypatch)
    company = sec.SEC.open_company("AAPL", archive)
    assert (company.profile.cik, company.profile.name, company.profile.fiscal_year_end) == (CIK, "Apple Inc.", "0926")
    assert company.profile.foreign is False and company.profile.sic == 3571
    reports = sec.SEC.periodic_reports(company, date(2023, 1, 1), known_urls={
        accession: f"https://www.sec.gov/Archives/{accession}.htm" for accession in LISTING["filings"]["recent"]["accessionNumber"]})
    # The 2023 and 2024 annual reports are on the older page.
    assert [ref.accession for ref in reports if ref.form == "10-K"] == [
        "0000320193-26-000026", "0000320193-25-000079", "0000320193-24-000123", "0000320193-23-000106"]
    assert archive.lists == 1


def test_a_list_older_than_ninety_minutes_gives_way_to_sec(monkeypatch):
    logged: list[str] = []
    listing, page = _split()
    archive = _archive(monkeypatch, {LIST_KEY: _gz(listing), PAGE_KEY: _gz(page)},
                       {LIST_KEY: NOW - timedelta(hours=3), PAGE_KEY: NOW - timedelta(days=3)}, logged)
    monkeypatch.setattr("kpi_extractor.sources.archive.datetime", _Clock)
    monkeypatch.setattr(sec, "company_profile", lambda symbol: ("from SEC", None))
    assert sec.SEC.open_company("AAPL", archive).profile == "from SEC"
    assert logged == ["AAPL: archived filing list is 3.0 h old; listing filings from SEC"]


def test_a_filing_archived_since_the_list_was_fetched_sends_the_company_to_sec(monkeypatch):
    # The filing feed's worker archived a new filing's submission after the hourly list fetch: the list cannot name it.
    logged: list[str] = []
    archive = _fresh(monkeypatch, logged, **{f"filings/{CIK}/raw/0000320193-26-000031.txt.gz": b"x"})
    monkeypatch.setattr(sec, "company_profile", lambda symbol: ("from SEC", None))
    assert sec.SEC.open_company("AAPL", archive).profile == "from SEC"
    assert logged == ["AAPL: archived filing list lacks 0000320193-26-000031, filed since; listing filings from SEC"]


def test_a_filing_the_list_names_keeps_it_in_use(monkeypatch):
    archive = _fresh(monkeypatch, **{f"filings/{CIK}/raw/0000320193-26-000026.txt.gz": b"x"})
    _no_sec(monkeypatch)
    assert sec.SEC.open_company("AAPL", archive).profile.cik == CIK


def test_a_missing_older_page_or_write_time_sends_the_company_to_sec(monkeypatch):
    listing, _page = _split()
    monkeypatch.setattr(sec, "company_profile", lambda symbol: ("from SEC", None))
    monkeypatch.setattr("kpi_extractor.sources.archive.datetime", _Clock)
    no_page = _archive(monkeypatch, {LIST_KEY: _gz(listing)}, {LIST_KEY: NOW - timedelta(minutes=5)})
    assert sec.SEC.open_company("AAPL", no_page).profile == "from SEC"
    # An older control plane lists no write times: the list's age is unknown.
    untimed = _archive(monkeypatch, {LIST_KEY: _gz(LISTING)})
    assert sec.SEC.open_company("AAPL", untimed).profile == "from SEC"
    assert no_page.lists == untimed.lists == 0


def test_without_an_archive_the_company_comes_from_sec(monkeypatch):
    monkeypatch.setattr(sec, "company_profile", lambda symbol: ("from SEC", None))
    assert sec.SEC.open_company("AAPL").profile == "from SEC"


@pytest.mark.parametrize("unreadable", [b"not gzip", _gz({"filings": {}})])
def test_an_unreadable_list_sends_the_company_to_sec(monkeypatch, unreadable):
    archive = _archive(monkeypatch, {LIST_KEY: unreadable}, {LIST_KEY: NOW})
    monkeypatch.setattr("kpi_extractor.sources.archive.datetime", _Clock)
    monkeypatch.setattr(sec, "company_profile", lambda symbol: ("from SEC", None))
    assert sec.SEC.open_company("AAPL", archive).profile == "from SEC"
