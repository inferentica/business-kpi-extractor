"""A regulator that cannot be reached leaves no year unread where a copy was kept (round 61: OpenDART timed out for
Shinhan's 2025-26 reports, and other filings took their periods with other figures)."""
import json
import urllib.error

import pytest

from statement_extractor.sources import regulators


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("STATEMENTS_ARCHIVE_CACHE", str(tmp_path))
    monkeypatch.setenv("DART_API_KEY", "test")
    monkeypatch.setattr(regulators, "DOWN", set())
    monkeypatch.setattr(regulators, "KEPT_SERVED", [])
    monkeypatch.setattr(regulators.time, "sleep", lambda seconds: None)
    return tmp_path / "regulators"


def _timeout(*args, **kwargs):
    raise urllib.error.URLError("timed out")


def test_a_fresh_year_unreachable_serves_its_kept_copy(cache, monkeypatch):
    cache.mkdir()
    year = regulators.date.today().year
    (cache / f"dart_X_{year}_11013_CFS.json").write_text(json.dumps([{"account_nm": "kept"}]))
    monkeypatch.setattr(regulators.urllib.request, "urlopen", _timeout)
    assert regulators.dart_rows("X", year, "11013") == [{"account_nm": "kept"}]
    assert regulators.KEPT_SERVED == [f"dart_X_{year}_11013_CFS.json"]


def test_nothing_kept_still_fails(cache, monkeypatch):
    monkeypatch.setattr(regulators.urllib.request, "urlopen", _timeout)
    with pytest.raises(OSError):
        regulators.dart_rows("X", regulators.date.today().year, "11013")


def test_a_host_that_failed_every_try_is_asked_once_after(cache, monkeypatch):
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(regulators.urllib.request, "urlopen", counted)
    for _ in range(2):
        with pytest.raises(OSError):
            regulators._get("https://opendart.fss.or.kr/api/x")
    assert len(calls) == 4 + 1
