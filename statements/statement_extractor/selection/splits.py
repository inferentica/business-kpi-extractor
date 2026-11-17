"""Per-share figures and share counts on today's share basis (Phase 6; owner, 2026-11-15: split-adjusted, the figure
as printed kept beside it).

The splits come from the company's own filings: after a split, its next reports present every earlier period's
earnings per share divided by the split's ratio and its share counts multiplied by it. Between two presentations of
one period, a ratio that basic and diluted earnings per share agree on, confirmed by the share counts moving by its
inverse (TSMC's stock dividends, Coca-Cola's 2012 two-for-one) or a clean ratio over two periods or more (Alphabet's
2022 twenty-for-one, its share counts printed only in a note), is a split, dated by the first filing presenting it.
A restatement moves earnings, not shares (Coca-Cola's 2019 0.97, ASML's 2018 0.95): never a split. A served figure
from a filing before a split is divided (per share) or multiplied (shares) by the ratios of every split after it."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from fractions import Fraction
from statistics import median

from ..domain.model import REGULATOR_SOURCES as REGULATORS
from ..domain.printed_labels import PER_SHARE, SHARE_COUNT

_KIND = re.compile(r"\b(basic|diluted|primary|fully\s+diluted)\b", re.I)
_NAMES = {"primary": "basic", "fully diluted": "diluted"}


def _kind(found) -> str:
    word = re.sub(r"\s+", " ", found.group(1).lower())
    return _NAMES.get(word, word)


def _classified(lines: list[dict]) -> dict[int, tuple[str, str]]:
    """Each per-share or share-count line of an income statement, by position: ("eps" or "shares", basic or diluted).
    A printed "Basic" under a shares heading is a share count: of two lines of one kind, the far larger."""
    found: dict[int, tuple[str, str]] = {}
    by_kind: dict[str, list[int]] = defaultdict(list)
    for index, line in enumerate(lines):
        value, label, concept = line.get("value"), str(line.get("label") or ""), str(line.get("concept") or "")
        # Basic or diluted by the label, else by the tag: Broadcom's "Net income per share" twice, its tags apart.
        kind = _KIND.search(label) or _KIND.search(re.sub(r"([a-z])([A-Z])", r"\1 \2", concept.split("_", 1)[-1]))
        if line.get("is_heading") or value is None or kind is None:
            continue
        if "WeightedAverage" in concept or SHARE_COUNT.search(label) and not re.search(r"per\s+share", label, re.I):
            found[index] = ("shares", _kind(kind))
        elif PER_SHARE.search(label) or "PerShare" in concept:
            by_kind[_kind(kind)].append(index)
    for kind, indexes in by_kind.items():
        values = sorted(indexes, key=lambda index: abs(lines[index]["value"]))
        smallest = abs(lines[values[0]]["value"]) or 1
        for index in values:
            # A share count, never a figure a share: far larger than the kind's other line, or over 100,000 (EOG's
            # 2005 10-K prints only its counts under "Basic" and "Diluted"; no share earns 100,000 even in won).
            large = abs(lines[index]["value"]) > 1000 * smallest or abs(lines[index]["value"]) >= 100_000
            found[index] = ("shares" if large else "eps", kind)
    return found


def _member(line: dict) -> str:
    """A share class by its words in any order: Visa's "Common Stock Class A" and, later, "Class A common stock"."""
    return " ".join(sorted(re.findall(r"[a-z0-9]+", str(line.get("member") or "").lower())))


def _without_outliers(shown: list) -> list:
    """A period's presentations without one its neighbours agree against (one filing's figures misread: KEPCO's
    2005 per-share figures doubled, then halved again by the next report)."""
    kept = []
    for index, (filed, value) in enumerate(shown):
        if 0 < index < len(shown) - 1:
            before, after = shown[index - 1][1], shown[index + 1][1]
            if before and after and abs(before - after) <= 0.01 * abs(after) and abs(value - after) > 0.01 * abs(after):
                continue
        kept.append((filed, value))
    return kept


def _clean(ratio: float) -> float | None:
    """A split's ratio exactly, where it is a whole number or a simple fraction (3-for-2, 5-for-4) or the inverse of
    one: Apple's 7.004 measured from rounded figures is 7."""
    for value, inverse in ((ratio, False), (1 / ratio, True)):
        near = Fraction(value).limit_denominator(4)
        if near >= Fraction(5, 4) and abs(float(near) - value) <= 0.01 * value:  # per-share figures printed to the cent
            return 1 / float(near) if inverse else float(near)
    return None


def split_ledger(readings: list) -> list[tuple[date, float, str]]:
    """The company's splits, (first filing presenting it, ratio, share class or ""), oldest first: Visa split its
    class A shares four-for-one in 2015, not its classes B and C."""
    presented: dict[tuple, list] = defaultdict(list)
    places: dict[tuple, int] = {}
    for reading in readings:
        if reading.status != "kept" or reading.statement != "income" or reading.scope != "consolidated":
            continue
        seen = set()
        for index, (measure, kind) in _classified(reading.lines).items():
            member = _member(reading.lines[index])
            if (measure, kind, member) not in seen and reading.lines[index]["value"]:
                seen.add((measure, kind, member))
                presented[(reading.key, measure, kind, member)].append((reading.filed, reading.lines[index]["value"]))
                places[(reading.filed, reading.lines[index]["value"])] = _places(reading.lines[index])
    marks: dict[tuple, dict] = defaultdict(lambda: defaultdict(list))
    rounding: dict[tuple, list] = defaultdict(list)  # each earnings move with what rounding alone could do to it
    for (period, measure, kind, member), shown in presented.items():
        shown = _without_outliers(sorted(set(shown)))
        for (was, older), (filed, newer) in zip(shown, shown[1:]):
            if older and newer and older / newer > 0:
                ratio = older / newer if measure == "eps" else newer / older
                # Never a figure read at another scale (1,000 to one), nor a ratio a restatement makes (0.99).
                if 0.015 <= abs(ratio - 1) and 0.01 < ratio < 100:
                    marks[(filed, member)][measure].append((ratio, period))
                    if measure == "eps":
                        rounding[(filed, member)].append(
                            (ratio, _rounding(older, places.get((was, older), 2), newer, places.get((filed, newer), 2))))
    events: list[tuple[date, float, str]] = []
    for filed, member in sorted(marks):
        earnings, shares = marks[(filed, member)]["eps"], marks[(filed, member)]["shares"]
        if len(earnings) < 2:
            continue
        ratio = median(value for value, _ in earnings)
        proven = _proven(shares, rounding[(filed, member)])
        if proven is not None:
            ratio = proven  # share counts give the ratio; earnings printed to the cent meet it within their rounding
        elif any(abs(value - ratio) > 0.01 * ratio for value, _ in earnings):
            continue  # earnings moving by different ratios: a restatement, no split
        confirmed = proven is not None or any(abs(value - ratio) <= 0.01 * ratio for value, _ in shares) or (
            _clean(ratio) is not None and len({period for _, period in earnings}) >= 2)
        if not confirmed:
            continue
        # Dated by the first filing that presents it, confirmed or not: Apple's 10-Q of May 2005 already halves its
        # earlier earnings per share, though only its 10-K of 2006 shows the split in two periods.
        first = min((when for (when, other), measures in marks.items() if other == member and 0 <= (filed - when).days <= 450
                     and any(abs(value - ratio) <= 0.01 * ratio for values in measures.values() for value, _ in values)),
                    default=filed)
        if any(other == member and abs(seen_ratio - ratio) <= 0.01 * ratio and abs((first - when).days) <= 450
               for when, seen_ratio, other in events):
            continue  # the same split, presented again by a later filing
        events.append((first, _clean(ratio) or ratio, member))
    # A split undone within two years is one filing's misreading, no split (Helix's 2006 "reverse" and "forward").
    noise = {index for index, (when, ratio, member) in enumerate(events) for other, (later, back, same) in enumerate(events)
             if other != index and same == member and abs((later - when).days) <= 730 and abs(ratio * back - 1) <= 0.02}
    return sorted(event for index, event in enumerate(events) if index not in noise)


def _proven(shares: list, earnings: list) -> float | None:
    """A split share counts prove: they all move by one ratio a split declares, within 1%, and every earnings move
    meets it within what rounding to the printed cent does (Kroger's 2015 two-for-one: its quarterly EPS of 0.69
    printed again as 0.34 measures 2.03). None where they do not."""
    if not shares or not earnings:
        return None
    middle = median(value for value, _ in shares)
    if any(abs(value - middle) > 0.01 * middle for value, _ in shares):
        return None
    ratio = _declared(middle)
    if ratio is None or abs(ratio - middle) > 0.01 * middle:
        return None
    return ratio if all(abs(value - ratio) <= max(0.01, slack) * ratio for value, slack in earnings) else None


def _declared(ratio: float) -> float | None:
    """The ratio a split declares nearest a measured one: a whole number up to 100, or a fraction of ten or less over
    two to four (3-for-2, 5-for-4), or the inverse of one (a reverse split)."""
    value = ratio if ratio >= 1 else 1 / ratio
    candidates = [Fraction(round(value))] if round(value) <= 100 else []
    near = Fraction(value).limit_denominator(4)
    if near.denominator > 1 and near.numerator <= 10:
        candidates.append(near)
    if not candidates:
        return None
    best = float(min(candidates, key=lambda candidate: abs(float(candidate) - value)))
    if best < 1.2:
        return None
    return best if ratio >= 1 else 1 / best


def _places(line: dict) -> int:
    """The decimal places a per-share figure is printed to: its tag's, else its own digits."""
    decimals = line.get("decimals")
    if isinstance(decimals, int) and decimals >= 0:
        return decimals
    text = f"{abs(float(line.get('value') or 0)):.6f}".rstrip("0")
    return max(2, len(text.split(".")[1])) if "." in text else 2


def _rounding(older: float, older_places: int, newer: float, newer_places: int) -> float:
    """How far rounding alone can move a ratio of two printed figures: half the last place of each (Kroger's
    quarterly earnings of 0.69 and 0.34 after its 2015 two-for-one measure 2.03), at most 3%."""
    if min(abs(older), abs(newer)) < 0.10:
        return 0.0  # half a cent is anything to a figure under ten cents
    return min(0.03, 0.5 * 10 ** -older_places / abs(older) + 0.5 * 10 ** -newer_places / abs(newer))


def adjust_for_splits(chosen: list[dict], ledger: list[tuple[date, float, str]]) -> None:
    """Each served per-share figure and share count on today's share basis, its printed figure kept beside it: every
    line a share or of shares by its unit (Apple's dividends per share), and those the income statement's labels and
    tags name, on every basis served. A split of all the company's shares moves each class's figures too (Alphabet's
    2022 twenty-for-one); one class's split, that class's alone (Visa's class A in 2015)."""
    if not ledger:
        return
    for record in chosen:
        if not record.get("lines") or not record.get("source_filed"):
            continue
        for served in [record, *(record.get("other_bases") or [])]:
            if served.get("lines") and served.get("source_filed"):
                _adjust(served["lines"], date.fromisoformat(served["source_filed"][:10]), ledger,
                        record["statement"] == "income")


def _adjust(lines: list[dict], filed: date, ledger: list[tuple[date, float, str]], income: bool) -> None:
    measures = {index: measure for index, (measure, _kind) in _classified(lines).items()} if income else {}
    for index, line in enumerate(lines):
        unit = str(line.get("unit") or "")
        measure = "eps" if unit.endswith("/share") else "shares" if unit == "shares" else measures.get(index)
        if measure is None or line.get("value") is None or "split_factor" in line:
            continue
        factor = 1.0
        for when, ratio, member in ledger:
            if when > filed and member in ("", _member(line)):
                factor *= ratio
        if abs(factor - 1) < 1e-9:
            continue
        line["printed_value"] = line["value"]
        line["value"] = round(line["value"] / factor, 6) if measure == "eps" else round(line["value"] * factor)
        line["split_factor"] = round(factor, 6)
