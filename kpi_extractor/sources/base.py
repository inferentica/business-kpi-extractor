"""What the pipeline needs from a source of filings, whatever regulator publishes them: the company, its filings, their
documents and their tagged facts. SEC EDGAR is the one source today (sources/sec.py); a later source (SEDAR+, ESEF,
EDINET) implements the same methods, and the pipeline does not change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol


@dataclass
class CompanyProfile:
    symbol: str
    cik: int
    name: str
    fiscal_year_end: str
    foreign: bool
    sic: int | None = None


@dataclass
class FilingRef:
    accession: str
    form: str
    filed: date
    role: str  # periodic_report | earnings_release
    source_url: str
    period_end: date | None = None
    exhibits: list = field(default_factory=list, repr=False)
    filing: object = field(default=None, repr=False)  # the source's own filing object
    # An 8-K Item 2.02 is an earnings release by definition; anything else is classified by the AI first.
    confirmed: bool = True


@dataclass
class SourceCompany:
    """A company as one source knows it: the profile the pipeline reads, and the source's own handle for listing."""
    profile: CompanyProfile
    handle: Any = field(default=None, repr=False)


class FilingSource(Protocol):
    """One source of filings. Exhibits are the source's attachment objects; each has `document`, `document_type`,
    `description` and `url`. Tagged facts are the source's parsed XBRL (facts as a frame, entity information)."""

    name: str
    # The language its filings are written in (ISO 639-1): labels are kept as reported, in it.
    language: str
    # Where its documents live: a value's source_url must start with one of these to be stored.
    url_prefixes: tuple[str, ...]

    # With the company's archive (when its ID is known before the source is asked), from what is archived there.
    def open_company(self, symbol: str, archive=None) -> SourceCompany: ...

    # A filer the company's earlier filings came from (its ID changed in a reorganization), with that filer's archive.
    def open_filer(self, cik: int, profile: CompanyProfile, archive=None) -> SourceCompany: ...

    def periodic_reports(self, company: SourceCompany, since: date, known_urls: dict[str, str] | None = None,
                         archive=None) -> list[FilingRef]: ...

    def earnings_releases(self, company: SourceCompany, since: date, archive=None, passed: list[FilingRef] | None = None,
                          known_urls: dict[str, str] | None = None) -> list[FilingRef]: ...

    def prospectus(self, company: SourceCompany, first_report: date | None) -> FilingRef | None: ...

    def report_html(self, ref: FilingRef, archive=None) -> str: ...

    def exhibit_html(self, exhibit) -> str | None: ...

    def is_own_document(self, exhibit) -> bool: ...

    def classification_excerpt(self, ref: FilingRef) -> str: ...

    def tagged_facts(self, ref: FilingRef, archive=None): ...

    def archive_tagged_facts(self, ref: FilingRef, archive) -> None: ...
