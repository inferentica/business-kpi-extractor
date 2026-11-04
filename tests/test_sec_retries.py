from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from edgar import Filing
from edgar.exceptions import FilingNotFoundError, SECIdentityError
from edgar.sgml.sgml_parser import SECHTMLResponseError

from kpi_extractor import sec

SUBMISSION = """<SEC-DOCUMENT>0000000001-26-000001.txt : 20260102
<SEC-HEADER>0000000001-26-000001.hdr.sgml : 20260102
ACCESSION NUMBER:\t\t0000000001-26-000001
CONFORMED SUBMISSION TYPE:\t10-Q
PUBLIC DOCUMENT COUNT:\t\t1
FILED AS OF DATE:\t\t20260102
</SEC-HEADER>
<DOCUMENT>
<TYPE>10-Q
<SEQUENCE>1
<FILENAME>report.htm
<DESCRIPTION>10-Q
<TEXT>
<html><body>The report</body></html>
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>
"""


@pytest.fixture
def waits(monkeypatch):
    monkeypatch.setenv("EDGAR_IDENTITY", "TradeTracker admin@inferentica.com")
    waited = []
    monkeypatch.setattr(sec.time, "sleep", waited.append)
    return waited


def _failing(*errors, then="read"):
    remaining = list(errors)

    def read():
        if remaining:
            raise remaining.pop(0)
        return then
    return read


def _filing():
    return Filing(cik=1, company="Company", form="10-Q", filing_date="2026-01-02", accession_no="0000000001-26-000001")


def test_a_temporary_failure_is_read_again(waits):
    read = _failing(SECHTMLResponseError("error page"), httpx.ReadTimeout("slow"))
    assert sec._with_retries(read) == "read"
    assert waits == list(sec._SEC_WAITS[:2])


def test_it_gives_up_with_the_last_failure(waits):
    read = _failing(*[SECHTMLResponseError(str(number)) for number in range(9)])
    with pytest.raises(SECHTMLResponseError, match="3"):
        sec._with_retries(read)
    assert waits == list(sec._SEC_WAITS)


@pytest.mark.parametrize("error", [SECIdentityError("rejected"), FilingNotFoundError("gone"), ValueError("a bug")])
def test_what_trying_again_cannot_cure_fails_at_once(waits, error):
    with pytest.raises(type(error)):
        sec._with_retries(_failing(error))
    assert waits == []


def test_a_bad_reply_is_read_again_past_the_cache(waits, monkeypatch):
    filing = _filing()
    monkeypatch.setattr(filing, "sgml", _failing(SECHTMLResponseError("error page"), then=None), raising=False)
    fetched = []

    def get(url, **kwargs):
        fetched.append(url)
        return httpx.Response(200, text=SUBMISSION, request=httpx.Request("GET", url))
    monkeypatch.setattr(sec.httpx, "get", get)
    sec.load_submission(filing)
    assert fetched == [filing.text_url]
    assert filing._sgml.accession_number == "0000000001-26-000001"
    assert [attachment.document for attachment in filing._sgml.attachments] == ["report.htm"]


def test_an_error_page_on_the_fresh_read_is_tried_again(waits, monkeypatch):
    filing = _filing()
    monkeypatch.setattr(filing, "sgml", _failing(httpx.ReadTimeout("slow"), then=None), raising=False)
    replies = ["<html><body>Please try later</body></html>", SUBMISSION]

    def get(url, **kwargs):
        return httpx.Response(200, text=replies.pop(0), request=httpx.Request("GET", url))
    monkeypatch.setattr(sec.httpx, "get", get)
    sec.load_submission(filing)
    assert filing._sgml.accession_number == "0000000001-26-000001"
    assert waits == list(sec._SEC_WAITS[:2])


def test_listing_reports_survives_a_bad_reply(waits, monkeypatch):
    filing = _filing()
    state = {"failed": False}
    real = type(filing).sgml

    def sgml(self):
        if not state["failed"]:
            state["failed"] = True
            raise SECHTMLResponseError("error page")
        return real(self)
    monkeypatch.setattr(type(filing), "sgml", sgml)
    monkeypatch.setattr(sec.httpx, "get", lambda url, **kwargs: httpx.Response(200, text=SUBMISSION, request=httpx.Request("GET", url)))

    class Listed:
        def get_filings(self, **kwargs):
            return [filing]
    refs = sec.periodic_reports(Listed(), date(2025, 1, 1))
    assert [ref.accession for ref in refs] == ["0000000001-26-000001"]
    assert refs[0].source_url.endswith("report.htm")


def test_a_recorded_report_is_listed_without_its_submission(monkeypatch):
    filing = _filing()

    def sgml(self):
        raise AssertionError("downloaded")
    monkeypatch.setattr(type(filing), "sgml", sgml)

    class Listed:
        def get_filings(self, **kwargs):
            return [filing]
    known = {filing.accession_no: "https://www.sec.gov/Archives/edgar/data/1/1/report.htm"}
    refs = sec.periodic_reports(Listed(), date(2025, 1, 1), known)
    assert refs[0].source_url == known[filing.accession_no] and refs[0].filing is filing


def test_a_report_read_from_edgar_rides_out_a_bad_reply(waits, monkeypatch):
    # Listed without its submission (it was recorded), then read: the read gets the retries listing no longer gives it.
    from kpi_extractor.pipeline import SymbolPipeline
    filing = _filing()
    real = type(filing).sgml
    state = {"failed": False}

    def sgml(self):
        if not state["failed"]:
            state["failed"] = True
            raise SECHTMLResponseError("error page")
        return real(self)
    monkeypatch.setattr(type(filing), "sgml", sgml)
    monkeypatch.setattr(type(filing), "xbrl", lambda self: "read")
    monkeypatch.setattr(sec.httpx, "get", lambda url, **kwargs: httpx.Response(200, text=SUBMISSION, request=httpx.Request("GET", url)))
    pipeline = SymbolPipeline(None, "X", quarters=4, force=False, log=lambda *_: None)
    assert pipeline._load_xbrl(SimpleNamespace(filing=filing)) == "read"
    assert filing._sgml.accession_number == "0000000001-26-000001" and waits == [sec._SEC_WAITS[0]]
