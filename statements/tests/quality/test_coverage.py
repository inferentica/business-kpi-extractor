from types import SimpleNamespace

from statement_extractor.quality.coverage import summarise


def test_a_filing_nothing_was_read_from_counts_as_missing():
    profile = SimpleNamespace(name="Apple", cik=320193, fiscal_year_end="0930", foreign=False)
    from datetime import date
    filings = [{"form": "10-K", "filed": "1997-12-05", "fiscal_year": None, "fiscal_period": None, "xbrl": False},
               {"form": "10-K", "filed": "1998-12-23", "fiscal_year": "1998", "fiscal_period": "FY", "xbrl": False}]
    chosen = [{"statement": statement, "fiscal_year": "1998", "fiscal_period": "FY", "duration": duration, "status": "kept",
               "period_end": "1998-09-25"}
              for statement, duration in (("income", "year"), ("balance", "instant"), ("cash_flow", "year"))]
    summary = summarise("AAPL", profile, date(1993, 1, 1), filings, [], chosen, 1.0)
    assert summary["own_expected"] == 6 and summary["own_kept"] == 3
    assert summary["own_missing"] == ["10-K 1997-12-05 unread"]
