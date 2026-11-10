"""What a release prints of its own quarter, by its statements' titles and column dates, before a figure is read: the
gate counts a quarter's statement only where a release printed it (Lloyds' first and third quarter updates print no
cash flow statement; before 2013 its half-year reports print six months, not the second quarter's three)."""
from __future__ import annotations

from datetime import date, timedelta

from ...domain.periods import label_period

MARK = "titled: "


def titled_notes(kind: str, periods: dict, document_end: date | None, fiscal_year_end: str, year_offset: int) -> list[str]:
    """A note for each of the document's own periods a found statement prints; with none dated, one for its kind."""
    own = {label_period(period, fiscal_year_end, year_offset) for period in periods.values()
           if document_end is not None and abs(period.end - document_end) <= timedelta(days=7)} - {None}
    if not own:
        return [f"{MARK}{kind}"]
    return [f"{MARK}{kind} {' '.join(label)}" for label in sorted(own)]


def split_titled(notes: list[str]) -> tuple[set[tuple[str, ...]], list[str]]:
    """(statement and period printed, as (kind,) or (kind, year, period, duration)), and the other notes."""
    titled = {tuple(note[len(MARK):].split()) for note in notes if note.startswith(MARK)}
    return titled, [note for note in notes if not note.startswith(MARK)]
