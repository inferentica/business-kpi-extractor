"""What a release prints of its own quarter, by its statements' titles and column dates, before a figure is read: the
gate counts a quarter's statement only where a release printed it (Lloyds' first and third quarter updates print no
cash flow statement; before 2013 its half-year reports print six months, not the second quarter's three)."""
from __future__ import annotations

import html
import re
from datetime import date, timedelta

from ...domain.periods import label_period
from .find import _SENTENCE_WORDS, TITLES

MARK = "titled: "
_TAG = re.compile(r"<[^>]+>")
_PERIOD = re.compile(r"\b(for\s+the\s+(quarter|period|three|six|nine|half|year)|months?\s+ended|half-year|as\s+(at|of)|"
                     r"\d{1,2}[./]\d{1,2}[./]\d{2,4}|(19|20)\d{2})\b", re.I)
_FIGURE = re.compile(r"(?<![\w.])\(?-?\d{1,3}(,\d{3})+(\.\d+)?\)?(?![\w.%])|(?<![\w.,])\(?\d{2,}(\.\d+)?\)?(?![\w.,%])")
_SENTENCE = re.compile(r"[a-z]{2,}[.!?]\s+[A-Z]")  # a sentence's end and the next one's start
# The words a statement's heading may run on into: its period or its unit, never a sentence's next word.
_HEADING_WORDS = {"for", "as", "at", "in", "of", "year", "years", "three", "six", "nine", "half", "quarter", "months",
                  "period", "continued", "unaudited", "audited", "and", "(unaudited)", "(continued)"}


def titled_notes(kind: str, periods: dict, document_end: date | None, fiscal_year_end: str, year_offset: int) -> list[str]:
    """A note for each of the document's own periods a found statement prints; with none dated, one for its kind. One
    whose columns are all dated to other periods prints none of the document's (Lloyds TSB's June 2003 6-K, its 2002
    halves restated)."""
    own = {label_period(period, fiscal_year_end, year_offset) for period in periods.values()
           if document_end is not None and abs(period.end - document_end) <= timedelta(days=7)} - {None}
    if not own and periods and document_end is not None and all(period.end < document_end - timedelta(days=7) for period in periods.values()):
        return []
    if not own:
        return [f"{MARK}{kind}"]
    return [f"{MARK}{kind} {' '.join(label)}" for label in sorted(own)]


def headed_kinds(content: str) -> set[str]:
    """The statements a document's text heads, whether or not its tables could be read: a statement's title, a period
    within the next few words and figures after it, as a statement opens ("Income statement For the quarter ended USD m
    31.3.24 31.12.23 … 1,940"), not a sentence naming it ("the balance sheet remains strong"). UBS's reports since 2023
    are pages laid out without tables; there the finder sees nothing, yet the quarter prints all three."""
    text = html.unescape(_TAG.sub(" ", content)) if "<" in content[:5000] else content
    text = re.sub(r"\s+", " ", text)
    kinds = set()
    for kind, pattern in TITLES:
        for match in pattern.finditer(text):
            before = text[max(0, match.start() - 40):match.start()].split()
            after = text[match.end():match.end() + 600]
            following = (after.split() or [""])[0]
            if before and (before[-1].lower() in _SENTENCE_WORDS
                           or len(before) > 1 and before[-2].lower() in _SENTENCE_WORDS and before[-1][:1].isupper()):
                continue  # "Capital and balance sheet", "… compares the Group balance sheet as at …"
            if following[:1].islower() and following.lower() not in _HEADING_WORDS:
                continue  # "income statement commentaries"
            figures = list(_FIGURE.finditer(after))
            if not _PERIOD.search(after[:160]) or len(figures) < 8:
                continue
            if _SENTENCE.search(after[:figures[7].end()]):
                continue  # a sentence before its figures: a section of the narrative (Copel's "Balance Sheet (Liabilities)")
            kinds.add(kind)
            break
    return kinds


def split_titled(notes: list[str]) -> tuple[set[tuple[str, ...]], list[str]]:
    """(statement and period printed, as (kind,) or (kind, year, period, duration)), and the other notes."""
    titled = {tuple(note[len(MARK):].split()) for note in notes if note.startswith(MARK)}
    return titled, [note for note in notes if not note.startswith(MARK)]
