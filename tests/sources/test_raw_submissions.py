"""A filing's full submission comes from the archive when the filing feed's worker has put it there, and from SEC only
when it has not."""
import gzip
from types import SimpleNamespace

from kpi_extractor.sources import sec
from kpi_extractor.sources.archive import Archive

import importlib.util
from pathlib import Path

# The submission and filing the SEC retry tests use.
_spec = importlib.util.spec_from_file_location("sec_retry_cases", Path(__file__).with_name("test_sec_retries.py"))
_cases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cases)
SUBMISSION, _filing = _cases.SUBMISSION, _cases._filing


class Control:
    def __init__(self, files):
        self.files = files

    def call(self, operation, **payload):
        assert operation == "archive_list"
        return {"files": self.files}


def _archive(monkeypatch, stored: dict[str, bytes]):
    links = {key: f"https://r2.example.test/{key}" for key in stored}
    archive = Archive(Control(links), "X", 1, log=lambda *_: None)

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


def _no_sec(monkeypatch, filing):
    """SEC may not be asked: edgartools' own read uses the submission a filing already holds, and only that."""
    def held(self):
        if self._sgml is None:
            raise AssertionError("SEC was asked")
        return self._sgml

    def refuse(*args, **kwargs):
        raise AssertionError("SEC was asked")
    monkeypatch.setattr(type(filing), "sgml", held)
    monkeypatch.setattr(sec.httpx, "get", refuse)


def _sec_answers(monkeypatch, filing, asked):
    """SEC's read, recorded: it holds the submission on the filing as edgartools does."""
    def read(self):
        if self._sgml is None:
            asked.append("sec")
            sec._hold_submission(self, SUBMISSION)
        return self._sgml
    monkeypatch.setattr(type(filing), "sgml", read)


def test_an_archived_submission_is_read_without_asking_sec(monkeypatch):
    filing = _filing()
    _no_sec(monkeypatch, filing)
    archive = _archive(monkeypatch, {"filings/1/raw/0000000001-26-000001.txt.gz": gzip.compress(SUBMISSION.encode())})
    sec.load_submission(filing, archive)
    assert filing._sgml.accession_number == "0000000001-26-000001"
    assert archive.submissions == 1
    # Everything read from the submission afterwards needs no request either.
    assert "The report" in sec.SEC.report_html(SimpleNamespace(filing=filing), archive)


def test_a_filing_not_in_the_archive_is_read_from_sec(monkeypatch):
    filing = _filing()
    asked = []
    _sec_answers(monkeypatch, filing, asked)
    archive = _archive(monkeypatch, {})
    sec.load_submission(filing, archive)
    assert asked == ["sec"] and archive.submissions == 0


def test_a_damaged_archive_copy_falls_back_to_sec(monkeypatch):
    filing = _filing()
    asked = []
    _sec_answers(monkeypatch, filing, asked)
    archive = _archive(monkeypatch, {"filings/1/raw/0000000001-26-000001.txt.gz": b"not gzip"})
    sec.load_submission(filing, archive)
    assert asked == ["sec"]


def test_a_new_report_is_listed_from_its_archived_submission(monkeypatch):
    filing = _filing()
    _no_sec(monkeypatch, filing)
    archive = _archive(monkeypatch, {"filings/1/raw/0000000001-26-000001.txt.gz": gzip.compress(SUBMISSION.encode())})

    class Listed:
        def get_filings(self, **kwargs):
            return [filing]
    refs = sec.periodic_reports(Listed(), __import__("datetime").date(2025, 1, 1), None, archive)
    assert refs[0].source_url.endswith("report.htm")
