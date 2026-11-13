"""Filings and documents before they are read: what is pending, read-ahead, XBRL and exhibits from the archive."""
from __future__ import annotations

import string
from concurrent.futures import Future
from datetime import date

from ..documents.document import Document, parse_document
from ..domain.fiscal import learn_year_offset, offset_lead
from ..readers.trace import untraced
from ..sources.base import FilingRef
from .shared import _DONE, _MAX_ATTEMPTS, _XBRL_READER, _folder

_XBRL_KEPT = 6
_OFFSET_REPORTS = 6  # the most reports the fiscal-year vote reads


class FilingLoading:
    """Filings and documents before they are read: what is pending, read-ahead, XBRL and exhibits from the archive."""

    def _reread_due(self, accession: str) -> bool:
        """In a selective re-read (ops/reread.py), whether this filing is one the run's selector picked: it is read again
        with today's checks. Picked once, before anything is read again, so the run's own readings never change it."""
        name = getattr(self, "reread", None)
        if not name:
            return False
        if getattr(self, "_reread_picked", None) is None:
            from ..ops.reread import picked_filings
            self._reread_picked = picked_filings(name, self.values, getattr(self, "filings", {}))
            getattr(self, "log", print)(f"{getattr(self, 'symbol', '')}: {len(self._reread_picked)} filings to read again ({name})")
        return accession in self._reread_picked

    def _pending(self, ref: FilingRef) -> bool:
        """Whether a filing still has to be read (a spec change can still re-read a finished release). In a selective
        re-read only what its selector picked: whatever else an ordinary run would read waits for one, so the run
        costs and changes what its selector says (GS's pending list-version re-read came along with a 2-filing trial)."""
        if getattr(self, "reread", None):
            return self._reread_due(ref.accession)
        previous = self.filings.get(ref.accession)
        return not previous or (previous["status"] not in _DONE and previous.get("attempts", 0) < _MAX_ATTEMPTS) or (
            ref.role == "periodic_report" and _XBRL_READER not in (previous.get("notes") or []))

    def _read_ahead(self, ref: FilingRef) -> None:
        if ref.accession not in self._xbrl_cache:
            self._xbrl_cache[ref.accession] = self._prefetch.submit(self._load_xbrl, ref)

    def _xbrl(self, ref: FilingRef):
        # The most recently used last; beyond a few, the least recently used are dropped (read again from the archive
        # if needed). One parsed ahead and not yet used is never dropped.
        future = self._xbrl_cache.pop(ref.accession, None) or self._prefetch.submit(self._load_xbrl, ref)
        self._xbrl_cache[ref.accession] = future
        self._xbrl_used.add(ref.accession)
        used = [accession for accession in self._xbrl_cache if accession in self._xbrl_used]
        for accession in used[:max(0, len(used) - _XBRL_KEPT)]:
            del self._xbrl_cache[accession]
            self._xbrl_used.discard(accession)
        return future.result()

    def _archive_xbrl(self, ref: FilingRef) -> None:
        self.source.archive_tagged_facts(ref, self._archive_of(ref))

    def _load_xbrl(self, ref: FilingRef):
        """A filing's XBRL, through its source (which keeps it in the archive)."""
        return self.source.tagged_facts(ref, self._archive_of(ref))

    def _exhibit(self, exhibit) -> Future:
        if exhibit.url not in self._exhibits:
            self._exhibits[exhibit.url] = self._prefetch.submit(self.source.exhibit_html, exhibit)
        return self._exhibits[exhibit.url]

    def _year_offset(self, reports: list[FilingRef]) -> int:
        """The company's fiscal-year naming, voted by its newest reports until one offset leads by two votes (a report's
        own fiscal year, an annual report's counting twice). One report can be mistagged: Oracle's 10-Q for the quarter
        ended August 31, 2026 names fiscal 2026, its 10-K and earlier 10-Qs the year each closes, so every period was
        named a year early."""
        samples: list[tuple[date, str, bool]] = []
        for ref in list(reversed(reports))[:_OFFSET_REPORTS]:
            try:
                xbrl = self._xbrl(ref)
            except Exception:  # noqa: BLE001 - an unreadable report just cannot vote
                continue
            entity = (xbrl.entity_info or {}) if xbrl is not None else {}
            period_end = entity.get("document_period_end_date")
            if not (period_end and entity.get("fiscal_year")):
                continue
            samples.append((date.fromisoformat(str(period_end)[:10]), str(entity["fiscal_year"]),
                            str(entity.get("fiscal_period") or "").upper() == "FY"))
            if offset_lead(samples, self.profile.fiscal_year_end) >= 2:
                break
        return learn_year_offset(samples, self.profile.fiscal_year_end)

    def _document(self, *refs: FilingRef) -> Document:
        """The exhibits of one or more filings as one document; each exhibit's ids carry their own letter prefix."""
        key = "+".join(ref.accession for ref in refs)
        if key not in self._documents:
            combined: Document | None = None
            exhibits = [exhibit for ref in refs for exhibit in ref.exhibits[:6]][:26]
            for index, exhibit in enumerate(exhibits):
                raw = self._waiting(self._exhibit(exhibit).result)
                if raw is None:
                    continue
                part = parse_document(raw, exhibit.url, prefix=f"{string.ascii_uppercase[index]}_")
                if combined is None:
                    combined = part
                else:
                    combined.extend(part)
            if combined is None:
                raise ValueError("no readable exhibit")
            self._documents[key] = combined
        return self._documents[key]
