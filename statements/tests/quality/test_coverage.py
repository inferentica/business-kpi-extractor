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


def test_release_quarters_are_counted_from_the_first_releases_year():
    from statement_extractor.quality.coverage import release_coverage
    filings = [{"form": "6-K", "release_quarter": ["2002", "Q3"]}, {"form": "6-K", "release_quarter": ["2003", "Q1"]},
               {"form": "6-K", "release_quarter": None}]
    kept = {("income", "2002", f"Q{n}", "quarter") for n in range(1, 5)} | {("income", "2003", "Q1", "quarter"),
                                                                            ("balance", "2003", "Q1", "instant"),
                                                                            ("cash_flow", "2003", "Q1", "quarter")}
    found = release_coverage(filings, kept, parent={("income", "2002", "Q1", "quarter")})
    # 2002: four incomes, three balance sheets and three cash flows; 2003 Q1: three.
    assert (found["quarters_expected"], found["quarters_kept"], found["quarters_parent_only"]) == (13, 7, 1)
    assert "2002 Q1 balance" in found["quarters_missing"]


def test_a_quarter_derivable_on_a_shared_basis_counts_apart():
    from statement_extractor.quality.coverage import release_coverage
    filings = [{"form": "6-K", "release_quarter": ["2013", "Q4"]}]
    kept = {("income", "2013", "Q1", "quarter"), ("income", "2013", "Q2", "ytd"), ("income", "2013", "Q3", "ytd"),
            ("income", "2013", "FY", "year")}
    bases = {("income", "2013", "Q1", "quarter"): {"Taiwan-IFRS"}, ("income", "2013", "Q2", "ytd"): {"Taiwan-IFRS"},
             ("income", "2013", "Q3", "ytd"): {"Taiwan-IFRS"}, ("income", "2013", "FY", "year"): {"IFRS", "Taiwan-IFRS"}}
    found = release_coverage(filings, kept, bases=bases)
    assert found["quarters_derivable"] == 3 and "2013 Q2 income" not in found["quarters_missing"]
    bases[("income", "2013", "FY", "year")] = {"IFRS"}
    assert release_coverage(filings, kept, bases=bases)["quarters_derivable"] == 2


def test_a_quarter_counts_only_the_statements_its_releases_print():
    from statement_extractor.quality.coverage import release_coverage
    # Lloyds 2011: Q1 and Q3 updates print no statements, the half-year prints six months, the year's results its
    # three months' income; a release that could not be opened counts every statement.
    filings = [{"form": "6-K", "release_quarter": ["2011", "Q1"], "titled": [], "notes": []},
               {"form": "6-K", "release_quarter": ["2011", "Q2"], "notes": [],
                "titled": [["income", "2011", "Q2", "ytd"], ["balance", "2011", "Q2", "instant"],
                           ["cash_flow", "2011", "Q2", "ytd"]]},
               {"form": "6-K", "release_quarter": ["2011", "Q3"], "note": "unreadable (HTTPError: 500)"},
               {"form": "6-K", "release_quarter": ["2011", "Q4"], "titled": [["income"]], "notes": []}]
    kept = {("balance", "2011", "Q2", "instant"), ("cash_flow", "2011", "Q2", "ytd")}
    found = release_coverage(filings, kept)
    assert (found["quarters_expected"], found["quarters_kept"]) == (6, 2)
    assert found["quarters_missing"] == ["2011 Q3 income", "2011 Q3 balance", "2011 Q3 cash_flow", "2011 Q4 income"]
    assert found["quarters_unprinted_list"] == ["2011 Q1 income", "2011 Q1 balance", "2011 Q1 cash_flow", "2011 Q2 income"]


def test_a_found_statement_notes_the_own_periods_it_prints():
    from datetime import date
    from statement_extractor.domain.periods import Period
    from statement_extractor.readers.printed.titled import split_titled, titled_notes
    periods = {1: Period(date(2011, 6, 30), date(2011, 1, 1)), 2: Period(date(2010, 6, 30), date(2010, 1, 1))}
    notes = titled_notes("income", periods, date(2011, 6, 30), "1231", 0) + ["income: no single scale stated"]
    assert split_titled(notes) == ({("income", "2011", "Q2", "ytd")}, ["income: no single scale stated"])
    assert titled_notes("balance", {}, date(2011, 6, 30), "1231", 0) == ["titled: balance"]


def test_a_statement_heading_in_the_text_counts_without_tables():
    from statement_extractor.readers.printed.titled import headed_kinds
    figures = " ".join(f"{n},{n:03d}" for n in range(1, 13))
    # UBS since 2023: pages laid out without tables, the statements headed in the text.
    page = f"<div>Income statement</div><div>For the quarter ended USD m Note 31.3.24 31.12.23 31.3.23 {figures}</div>"
    assert headed_kinds(page) == {"income"}
    # A sentence naming a statement, or a heading run into a sentence, is no statement.
    assert headed_kinds(f"compares the Group balance sheet as at 31 March 2011 with {figures}") == set()
    assert headed_kinds(f"income statement commentaries compare the three months ended 31 March 2011 {figures}") == set()
    assert headed_kinds("Balance sheet as at 31 March 2011: ratios 148% 154% 116% 120% 49% 50% 61% 62% 70%") == set()


def test_a_year_the_company_filed_nothing_for_is_not_counted():
    from statement_extractor.quality.coverage import release_coverage
    # Alcon: releases to the first quarter of 2011, then nothing until its spin-off in 2019.
    filings = [{"form": "6-K", "release_quarter": ["2011", "Q1"], "titled": [], "notes": []},
               {"form": "20-F", "fiscal_year": "2010", "fiscal_period": "FY"},
               {"form": "6-K", "release_quarter": ["2013", "Q1"], "titled": [], "notes": []}]
    found = release_coverage(filings, set())
    # 2012's four quarters are no registrant's; 2011's and 2013's first quarter count (2011 Q2-Q4 too: it filed then).
    assert found["quarters_not_filing"] == 4
