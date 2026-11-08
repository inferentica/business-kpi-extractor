"""The source adapter: the pipeline reaches filings only through a FilingSource, and stores only a source's documents."""
import inspect
from datetime import date

import pytest

from kpi_extractor.pipeline import SymbolPipeline
from kpi_extractor.sources import accepts_url, source
from kpi_extractor.sources.base import CompanyProfile, FilingRef, FilingSource, SourceCompany
from kpi_extractor.sources import sec


class Control:
    def __init__(self, cik=None):
        self.calls, self.cik = [], cik

    def call(self, operation, **payload):
        self.calls.append(operation if operation != "archive_list" else f"archive_list {payload['cik']}")
        return {"cik": self.cik} if operation == "symbol_state" else {}


class FakeSource:
    """A source with one company and no filings: what a run asks of it, and nothing else."""
    name = "fake"
    url_prefixes = ("https://filings.example.test/",)

    def __init__(self):
        self.asked = []

    def open_company(self, symbol, archive=None):
        self.asked.append("open_company")
        self.opened_with = archive
        return SourceCompany(CompanyProfile(symbol=symbol, cik=1, name="Example Co", fiscal_year_end="1231", foreign=False))

    def periodic_reports(self, company, since, known_urls=None, archive=None):
        self.asked.append("periodic_reports")
        return []

    def earnings_releases(self, company, since, archive=None, passed=None, known_urls=None):
        self.asked.append("earnings_releases")
        return []

    def prospectus(self, company, first_report):
        self.asked.append("prospectus")
        return None

    def report_html(self, ref, archive=None):
        raise AssertionError("no report to read")

    def exhibit_html(self, exhibit):
        raise AssertionError("no exhibit to read")

    def is_own_document(self, exhibit):
        return False

    def classification_excerpt(self, ref):
        raise AssertionError("nothing to classify")

    def tagged_facts(self, ref, archive=None):
        raise AssertionError("no XBRL to read")

    def archive_tagged_facts(self, ref, archive):
        raise AssertionError("nothing to archive")


def test_sec_implements_every_method_a_source_needs():
    wanted = {name for name, member in inspect.getmembers(FilingSource) if inspect.isfunction(member) and not name.startswith("_")}
    assert wanted == {"open_company", "periodic_reports", "earnings_releases", "prospectus", "report_html", "exhibit_html",
                      "is_own_document", "classification_excerpt", "tagged_facts", "archive_tagged_facts"}
    for name in wanted:
        assert callable(getattr(source("sec"), name)), name
    assert source() is sec.SEC and sec.SEC.url_prefixes == ("https://www.sec.gov/",)


def test_a_run_reaches_filings_only_through_its_source(monkeypatch):
    def no_edgar(*args, **kwargs):
        raise AssertionError("EDGAR reached outside the source")
    monkeypatch.setattr(sec, "Company", no_edgar)
    fake = FakeSource()
    pipeline = SymbolPipeline(Control(), "EXCO", quarters=4, force=False, today=date(2026, 11, 8), log=lambda *_: None, source=fake)
    pipeline.run()
    assert fake.asked[:3] == ["open_company", "periodic_reports", "earnings_releases"]
    assert pipeline.profile.name == "Example Co"


def test_the_store_keeps_only_a_sources_own_documents():
    assert accepts_url(None)
    assert accepts_url("https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/a.htm")
    assert not accepts_url("https://investor.example.com/deck.pdf")
    assert not accepts_url("https://www.sec.gov.example.com/a.htm")


def test_filing_references_keep_their_old_import_path():
    assert sec.FilingRef is FilingRef and sec.CompanyProfile is CompanyProfile


@pytest.mark.parametrize("registered, listed", [(1, ["archive_list 1"]), (7, ["archive_list 7", "archive_list 1"]),
                                                 (None, ["archive_list 1"])])
def test_the_registrys_cik_opens_the_archive_before_the_company(registered, listed):
    # With the registry's CIK the source gets the archive (its filing list) to open the company from; a CIK SEC's
    # company does not have gives way to SEC's, and without one the archive opens after the company, as before.
    control, fake = Control(registered), FakeSource()
    pipeline = SymbolPipeline(control, "EXCO", quarters=4, force=False, today=date(2026, 11, 8), log=lambda *_: None, source=fake)
    pipeline.run()
    assert [call for call in control.calls if call.startswith("archive_list")] == listed
    assert (fake.opened_with.cik if fake.opened_with else None) == registered
    assert pipeline.archive.cik == 1
