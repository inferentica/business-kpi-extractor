"""Whether a figure's own filing states it where it was read. The owner's rule (2026-11-09): a figure is judged only by
whether it is faithful to its filing, never by whether it looks reasonable. A sharp change between periods is no
reason to hold a figure the filing itself states (Palantir's TCV closed went from $248M to $792M, Visa's cross-border
volume growth from -21% to 53% out of the 2020 slump); a jump stays a misread guard only for figures nothing in the
filing confirms."""
from __future__ import annotations

import re

from ..documents.document import parse_number
from ..rules.wording import falls

# A scale adopted to fit the series rather than stated by the filing: such a figure is not the filing's own statement.
_SCALE_FROM_SERIES = "to match the series"


_WORD = re.compile(r"[a-z]{4,}")

# Scales the filing itself states (rules.scale.ScaleFrom sources); "quoted" and "reader" rest on a reading's words.
FILING_SCALES = frozenset({"number", "row", "column", "heading", "per-unit", "passage", "table", "block", "document"})


def _names(row_label: str, label: str | None) -> bool:
    """Whether a table row's own label names the KPI: a word of four letters or more in common. A row labelled with a
    date (Visa's "September 30, 2017") holds a figure, not this KPI's."""
    if label is None:
        return True
    return bool(set(_WORD.findall(row_label.lower())) & set(_WORD.findall(label.lower())))


def _same(left: float, right: float) -> bool:
    return abs(left - right) <= 1e-9 * max(abs(left), abs(right), 1.0)


def stated_in_filing(unit: str, value: float, locator: dict | None, notes: list[str] | None = None,
                     label: str | None = None) -> bool:
    """True when the filing states this very figure where it was read:
    - its quote holds the value text, and that text is the figure, scale word included ("$792 million" is 792,000,000;
      a bare "792" with the scale taken from elsewhere does not count), or its negative when the quote says it fell
      (Mastercard's "A decrease in cross-border volume of 45%" is -45); a bare number counts when the reading recorded
      the filing's own words for its scale (its locator's scale_from: a row, column, heading, passage, caption or
      document scale, never the reader's word alone);
    - or the table cell it was read from holds the figure at the scale the table states, in a row whose own label names
      the KPI (the read cell is the first of the row's numbers, kept as printed)."""
    if any(_SCALE_FROM_SERIES in note for note in notes or []):
        return False
    at = locator or {}
    quote, text = str(at.get("quote") or ""), str(at.get("value_text") or "")
    if quote and text and text in quote:
        parsed = parse_number(text)
        signed = parsed is not None and (_same(parsed.value, value) or (value < 0 < parsed.value and _same(parsed.value, -value)
                                                                        and falls(quote, text)))
        if signed and (parsed.scale_word or parsed.percent or unit != "currency" or abs(value) < 1e5):
            return True
        scale_from = at.get("scale_from") or {}
        if parsed is not None and not parsed.scale_word and scale_from.get("source") in FILING_SCALES:
            stated = parsed.value * float(scale_from.get("factor") or 1)
            if _same(stated, value) or (value < 0 < stated and _same(stated, -value) and falls(quote, text)):
                return True
    numbers = at.get("row_numbers") or []
    if (at.get("row_label") and _names(str(at["row_label"]), label) and numbers and isinstance(numbers[0], (int, float))
            and numbers[0]):
        scale = at.get("scale")
        scale = float(scale) if isinstance(scale, (int, float, str)) and str(scale).replace(".", "", 1).isdigit() else 1.0
        if unit in ("percent", "ratio"):
            return _same(float(numbers[0]), value)
        return _same(float(numbers[0]) * scale, value)
    return False
