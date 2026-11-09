"""Reports from before EDGAR held foreign filers' documents, read from the Internet Archive's copies of the companies'
own sites. Foreign private issuers filed on paper until May 2002, so EDGAR holds only placeholders for TSMC's and
ASML's earlier 20-Fs and 6-Ks; the companies published the same reports on their sites as PDFs, and the Wayback
Machine keeps them. The companies' sites refuse automated readers, so they are never fetched directly: only the
Archive's captures, through its documented replay address, which returns the file as captured.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date

_REPLAY = "https://web.archive.org/web/{timestamp}id_/{url}"  # "id_": the capture's own bytes, without the Archive's frame
_CLIENT = "statement-extractor (TradeTracker research; reads archived annual and quarterly reports)"
_WAITS = (5, 20, 60)
_PAUSE = 1.0  # seconds between requests, to go easy on the Archive


@dataclass(frozen=True)
class Archived:
    symbol: str
    form: str  # 20-F filed on paper, ARS (an annual report to shareholders), 6-K (a quarter's results)
    period_end: date
    filed: date
    url: str  # the document's address on the company's site when it was captured
    timestamp: str  # the Wayback Machine capture

    @property
    def accession(self) -> str:
        return f"archive:{self.symbol}:{self.form}:{self.period_end.isoformat()}"

    @property
    def replay(self) -> str:
        return _REPLAY.format(timestamp=self.timestamp, url=self.url)


def archived(symbol: str) -> list[Archived]:
    from .archived_list import ARCHIVED
    return [item for item in ARCHIVED if item.symbol == symbol.upper()]


def fetch(item: Archived) -> bytes:
    """The archived document's bytes, with a few tries when the Archive is slow or busy."""
    import httpx
    for wait in (*_WAITS, None):
        try:
            time.sleep(_PAUSE)
            response = httpx.get(item.replay, headers={"User-Agent": _CLIENT}, timeout=120, follow_redirects=True)
            response.raise_for_status()
            if not response.content.startswith(b"%PDF"):
                raise ValueError("the capture is not a PDF")
            return response.content
        except (httpx.HTTPError, ValueError):
            if wait is None:
                raise
            time.sleep(wait)
    raise RuntimeError("unreachable")
