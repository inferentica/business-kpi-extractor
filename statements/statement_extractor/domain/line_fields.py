"""Each line's unit, rounding, taxonomy and sign beside its figure (Phase 7, s7-writes; statements/docs/storage.md, "Line
fields"), as the SEC's Financial Statement Data Sets carry them for a value: `unit` an ISO 4217 code, `shares`,
`<code>/share` or `pure`; `decimals` the figure's rounding as tagged or printed (-6 for millions); `taxonomy` the XBRL
taxonomy and its year (`us-gaap/2024`); `negated` a line shown with its tag's sign reversed. A reader sets what only it
knows (a fact's unit and decimals, a printed scale); `finish_lines` gives every line the rest once the currency is known.
Fields no reader knows stay null, never guessed."""
from __future__ import annotations

import math
import re

from .printed_labels import PER_SHARE, SHARE_COUNT

MONEY, PER_SHARE_KIND, SHARES, PURE = "money", "per_share", "shares", "pure"
STANDARD_PREFIXES = ("us-gaap", "ifrs-full", "srt", "dei")
# The fields a served line may carry (Quotiva's statement_lines_schema); a reader's own notes stay in the evidence.
SERVED = ("line", "key", "concept", "taxonomy", "member", "label", "label_en", "level", "is_heading", "negated", "value",
          "printed_value", "unit", "decimals", "split_factor")


def places(printed: float) -> int:
    """The digits a figure is printed with after the decimal point (up to six): 2.97 has two, 1,234 none."""
    for digits in range(7):
        if abs(printed * 10 ** digits - round(printed * 10 ** digits)) < 1e-6 * max(1.0, abs(printed) * 10 ** digits):
            return digits
    return 6


def printed_decimals(printed: float | None, scale: float) -> int | None:
    """A printed figure's rounding in whole units: 1,234 in millions is -6, 1,234.5 in millions -5, 2.97 a share 2."""
    if printed is None or not scale or scale <= 0:
        return None
    return places(printed) - round(math.log10(scale))


def fact_kind(unit_ref: str | None, currency: str | None, concept: str, label: str) -> str:
    """A tagged figure's kind, from its fact's unit (`usd`, `usdPerShare`, `shares`, `pure`) and currency, else its tag
    and label: filers name their units freely (`U_iso4217USD_xbrlishares` is dollars a share)."""
    unit = str(unit_ref or "").lower()
    if not unit:
        return _by_label(concept, label)  # no fact for it: its tag and label decide
    # The fact's unit first: "Basic" under a share-count heading is a count, its unit `shares`.
    if "share" in unit and ("per" in unit or currency) or currency and "PerShare" in concept:
        return PER_SHARE_KIND
    if currency:
        return MONEY
    return SHARES if "share" in unit else PURE


def _by_label(concept: str, label: str) -> str:
    """A line's kind where no reader stated it: a regulator's form (CVM's 3.99 section is earnings a share) or print."""
    if "PerShare" in concept or concept.startswith("cvm_3.99") or PER_SHARE.search(label):
        return PER_SHARE_KIND
    if "NumberOfShares" in concept or "WeightedAverage" in concept or SHARE_COUNT.search(label) and not re.search(
            r"per\s+share|earnings|income|loss|capital|premium|reserve|treasury|stock\b|value", label, re.I):
        return SHARES
    return MONEY


def printed_fields(printed: float | None, scale: float, per_share: bool, shares: bool) -> dict:
    """A printed line's kind and rounding, from the scale its figure was read in."""
    if printed is None:
        return {}
    kind = PER_SHARE_KIND if per_share else SHARES if shares else MONEY
    return {"unit_kind": kind, "decimals": printed_decimals(printed, scale)}


def unit_of(kind: str, currency: str | None) -> str | None:
    if kind == SHARES:
        return "shares"
    if kind == PURE:
        return "pure"
    if not currency:
        return None
    return f"{currency}/share" if kind == PER_SHARE_KIND else currency


def taxonomy_of(concept: str, versions: dict[str, str] | None = None) -> str | None:
    """A tag's taxonomy: its prefix with the year the filing's schema imports (`us-gaap/2024`), the prefix alone for a
    regulator's IFRS tags or a filer's own extension; none for a printed line or a regulator's account code."""
    if "_" not in concept or concept.startswith(("printed_", "cvm_", "dart_", "bmv_", "cmf_")):
        return None
    prefix = concept.split("_", 1)[0]
    return (versions or {}).get(prefix) or prefix


def finish_lines(readings: list) -> None:
    """Every line's unit, taxonomy, rounding and sign, after the readings' currencies are settled."""
    for reading in readings:
        for line in reading.lines:
            kind = line.pop("unit_kind", None)
            if line.get("is_heading"):
                line["unit"] = None
                continue
            kind = kind or _by_label(str(line.get("concept") or ""), str(line.get("label_en") or line.get("label") or ""))
            line["unit"] = unit_of(kind, reading.currency)
            line.setdefault("taxonomy", taxonomy_of(str(line.get("concept") or "")))
            line.setdefault("decimals", None)
            line.setdefault("negated", False)


def served_line(line: dict) -> dict:
    """A line as Quotiva serves it: the schema's fields only."""
    served = {field: line[field] for field in SERVED if field in line}
    served.setdefault("key", served["concept"])  # a heading has no key across eras: its own concept
    served.setdefault("unit", None)
    return served
