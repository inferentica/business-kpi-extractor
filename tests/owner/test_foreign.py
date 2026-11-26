"""A foreign filer's 6-Ks for an owner-built company (owner/foreign.py): interim reports and results releases are told
apart by code, from Arm's own filings of November 2024 and May 2025."""
from datetime import date
from types import SimpleNamespace

from kpi_extractor.owner.foreign import is_interim_report, prints_total, results_releases
from kpi_extractor.owner.resolve import Fact
from kpi_extractor.sources.base import FilingRef


def _filing(filed, reported, xbrl=True, form="6-K"):
    return SimpleNamespace(form=form, is_xbrl=xbrl, report_date=reported, filing_date=date.fromisoformat(filed))


def test_an_interim_report_is_a_6k_with_xbrl_dated_at_a_quarter_end_before_its_filing():
    assert is_interim_report(_filing("2026-11-04", "2026-09-30"))  # Arm's Q2 fiscal 2027 statements
    assert not is_interim_report(_filing("2026-11-04", "2026-11-04"))  # its shareholder letter, the same day
    assert not is_interim_report(_filing("2025-09-10", "2025-09-10"))  # AGM results: cover XBRL, the meeting's date
    assert not is_interim_report(_filing("2026-11-04", "2026-09-30", xbrl=False))
    assert not is_interim_report(_filing("2026-11-04", "2026-09-30", form="8-K"))
    assert not is_interim_report(_filing("2026-11-04", "2026-09-15"))  # not a month end


def test_a_letter_prints_total_revenue_in_millions_after_its_footnote_marker():
    letter = "Q2 FYE27 Q2 FYE26 Y/Y% Total revenue (2) $1,443 $1,135 27% License and other revenue 773 515"
    assert prints_total(letter, 1_443_000_000)
    assert prints_total(letter.replace("revenue (2)", "revenues"), 1_443_000_000)
    assert not prints_total("Total revenue $11,443 million", 1_443_000_000)
    assert not prints_total("Total revenue $1,443.6 million", 1_443_000_000)


def _ref(accession, form, filed, end, role="earnings_release", confirmed=False):
    return FilingRef(accession=accession, form=form, filed=date.fromisoformat(filed), role=role, source_url="",
                     period_end=date.fromisoformat(end), confirmed=confirmed)


def _revenue(value, start, end):
    return Fact("RevenueFromContractWithCustomerExcludingAssessedTax", date.fromisoformat(start), date.fromisoformat(end),
                frozenset(), value)


def test_only_the_6k_that_prints_its_reports_revenue_is_a_release():
    q2 = _ref("interim-q2", "6-K", "2024-11-07", "2024-09-30", role="periodic_report", confirmed=True)
    letter = _ref("letter", "6-K", "2024-11-06", "2024-09-30")
    appointment = _ref("cpo", "6-K", "2024-11-07", "2024-09-30")  # a new chief people officer, the same week
    facts = {"interim-q2": [_revenue(844e6, "2024-07-01", "2024-09-30")]}
    texts = {"letter": "Total revenue $844 $806 5%", "cpo": "Arm announces the appointment of a chief people officer"}
    found = results_releases([letter, appointment, q2], [q2], lambda r: facts.get(r.accession), lambda r: texts.get(r.accession))
    assert [(r.accession, r.confirmed) for r in found] == [("letter", True)]


def test_a_fourth_quarter_letter_is_confirmed_by_its_20f_year_less_nine_months():
    q3 = _ref("interim-q3", "6-K", "2025-02-05", "2024-12-31", role="periodic_report", confirmed=True)
    annual = _ref("20-f", "20-F", "2025-05-28", "2025-03-31", role="periodic_report", confirmed=True)
    letter = _ref("q4-letter", "6-K", "2025-05-07", "2025-03-31")
    facts = {"interim-q3": [_revenue(2_773e6, "2024-04-01", "2024-12-31")],
             "20-f": [_revenue(4_014e6, "2024-04-01", "2025-03-31")]}
    found = results_releases([letter], [q3, annual], lambda r: facts.get(r.accession),
                             lambda r: "Total revenue $1,241 $928 34%")
    assert [r.accession for r in found] == ["q4-letter"]
    # before the 20-F is filed, nothing confirms it: it waits for the run after the report
    assert results_releases([letter], [q3], lambda r: facts.get(r.accession), lambda r: "Total revenue $1,241") == []
