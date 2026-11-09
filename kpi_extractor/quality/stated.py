"""Whether a figure's own filing states it where it was read. The owner's rule (2026-11-09): a figure is judged only by
whether it is faithful to its filing, never by whether it looks reasonable. A sharp change between periods is no
reason to hold a figure the filing itself states (Palantir's TCV closed went from $248M to $792M, Visa's cross-border
volume growth from -21% to 53% out of the 2020 slump); a jump stays a misread guard only for figures nothing in the
filing confirms."""
from __future__ import annotations

from ..documents.document import parse_number

# A scale adopted to fit the series rather than stated by the filing: such a figure is not the filing's own statement.
_SCALE_FROM_SERIES = "to match the series"


def _same(left: float, right: float) -> bool:
    return abs(left - right) <= 1e-9 * max(abs(left), abs(right), 1.0)


def stated_in_filing(unit: str, value: float, locator: dict | None, notes: list[str] | None = None) -> bool:
    """True when the filing states this very figure where it was read:
    - its quote holds the value text, and that text is the figure, scale word included ("$792 million" is 792,000,000;
      a bare "792" with the scale taken from elsewhere does not count);
    - or the table cell it was read from, named by its row, holds the figure at the scale the table states (the read
      cell is the first of the row's numbers, kept as printed)."""
    if any(_SCALE_FROM_SERIES in note for note in notes or []):
        return False
    at = locator or {}
    quote, text = str(at.get("quote") or ""), str(at.get("value_text") or "")
    if quote and text and text in quote:
        parsed = parse_number(text)
        if parsed is not None and _same(parsed.value, value) and (parsed.scale_word or parsed.percent or unit != "currency"
                                                                   or abs(value) < 1e5):
            return True
    numbers = at.get("row_numbers") or []
    if at.get("row_label") and numbers and isinstance(numbers[0], (int, float)) and numbers[0]:
        scale = at.get("scale")
        scale = float(scale) if isinstance(scale, (int, float, str)) and str(scale).replace(".", "", 1).isdigit() else 1.0
        if unit in ("percent", "ratio"):
            return _same(float(numbers[0]), value)
        return _same(float(numbers[0]) * scale, value)
    return False
