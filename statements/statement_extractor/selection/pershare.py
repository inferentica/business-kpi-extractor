"""Per-share figures a filer tagged at its statement's money scale. A tag can carry the scale of the figures around it:
PAC's 20-Fs of 2020 to 2022 tag earnings per share it prints as 10.1986 pesos as 10,198.6, and Picpay's 2025 20-F tags
the 5,457 reais it prints as 5,457,000 (scale 3 on a per-share fact). Where an XBRL filing's per-share figure is a
thousand or a million times another presentation's, the filing's printed statement is read (pipeline/second_look.py);
where that statement prints the figure a thousand or a million times smaller, the printed figure is served and a check
on the reading says what was tagged. A split moves share counts the other way and is no such case. A tagged figure a
power of ten below the printed one is left as tagged: pence printed beside a tag in pounds (Lloyds), or a printed
reader that took a table's scale for a per-share row."""
from __future__ import annotations

from collections import defaultdict

from ..domain.basis import ifrs_family
from ..domain.labels import label_key
from ..domain.line_fields import MONEY, PER_SHARE_KIND, SHARES, _by_label, places
from ..domain.model import PASS, Check, Reading

POWERS = (3, 6)  # a thousand or a million: the money scales a statement prints in


def scale_ties(readings: list[Reading]) -> set[str]:
    """XBRL filings with a per-share figure a thousand or a million times another presentation's of the same period,
    where the two presentations' share counts do not move the other way (a split): their printed statements are read."""
    by_key: dict[tuple, list[Reading]] = defaultdict(list)
    for reading in readings:
        if reading.status == "kept":
            by_key[reading.key].append(reading)
    found: set[str] = set()
    for group in by_key.values():
        for tagged in group:
            if tagged.source != "xbrl" or tagged.accession in found:
                continue
            mine = [value for _, _, value, _ in _figures(tagged, PER_SHARE_KIND)]
            for other in group:
                if other is tagged:
                    continue
                for _, _, shown, decimals in _figures(other, PER_SHARE_KIND):
                    power = next((power for value in mine if (power := scaled(value, shown, decimals))), 0)
                    if power and not _split(tagged, other, power):
                        found.add(tagged.accession)
                        break
                if tagged.accession in found:
                    break
    return found


def printed_per_share(readings: list[Reading]) -> int:
    """Each XBRL reading's per-share figure that its own filing's printed statement, on the same basis and scope, prints
    a thousand or a million times smaller takes the printed figure; returns how many were taken."""
    printed: dict[tuple, list[Reading]] = defaultdict(list)
    for reading in readings:
        if reading.source != "xbrl" and reading.status == "kept":
            printed[(reading.accession, reading.key, ifrs_family(reading.basis), reading.scope)].append(reading)
    taken = 0
    for reading in readings:
        if reading.source != "xbrl":
            continue
        statements = printed.get((reading.accession, reading.key, ifrs_family(reading.basis), reading.scope))
        if not statements:
            continue
        shown_lines = [figure for statement in statements for figure in _figures(statement, PER_SHARE_KIND)]
        notes = []
        seen: dict[str, int] = {}
        for line in reading.lines:
            if line["is_heading"] or not line["value"] or _kind(line) != PER_SHARE_KIND:
                continue
            label = label_key(line["label"])
            occurrence = seen.get(label, 0)
            seen[label] = occurrence + 1
            same = [figure for figure in shown_lines if figure[1] == label]
            if occurrence < len(same):
                candidates = [same[occurrence]]
            else:  # a printed label of its own ("Earnings per share – basic and diluted"): one figure it can be
                candidates = list({figure[2]: figure for figure in shown_lines
                                   if scaled(line["value"], figure[2], figure[3])}.values())
            if len(candidates) != 1:
                continue
            _, _, shown, decimals = candidates[0]
            if not scaled(line["value"], shown, decimals):
                continue
            notes.append(f"{line['label']}: printed {shown:g}, tagged {line['value']:g}")
            line["value"] = shown
            line["decimals"] = decimals if decimals is not None else places(shown)
        if notes:
            reading.checks.append(Check("per_share", PASS, "; ".join(notes)[:500]))
            taken += len(notes)
    return taken


def scaled(tagged: float, shown: float, decimals: int | None) -> int:
    """3 or 6 where the tagged figure is the shown one times a thousand or a million, to the shown figure's rounding
    (1,241.6 for 1.24 printed), and within a tenth of it: a figure of a few cents is never another's rounding (0.88
    is no thousand times 0.006); else 0."""
    if not tagged or not shown or (tagged < 0) != (shown < 0):
        return 0
    digits = decimals if decimals is not None and decimals >= 0 else places(shown)
    slack = 0.5 * 10 ** -digits + 1e-9 * abs(shown)
    for power in POWERS:
        scaled_down = abs(tagged) / 10 ** power
        if abs(scaled_down - abs(shown)) <= min(slack, 0.1 * abs(shown)):
            return power
    return 0


def _split(tagged: Reading, other: Reading, power: int) -> bool:
    """The two presentations' share counts at the inverse ratio: a split (Broadcom's 10-for-1) or a reverse split."""
    mine = _figures(tagged, SHARES)
    for concept, label, shares, _ in _figures(other, SHARES):
        for own_concept, own_label, own, _ in mine:
            if (own_concept == concept or own_label == label) and abs(own * 10 ** power / shares - 1) <= 0.02:
                return True
    return False


def _figures(reading: Reading, kind: str) -> list[tuple[str, str, float, int | None]]:
    return [(str(line.get("concept") or ""), label_key(line["label"]), line["value"], line.get("decimals"))
            for line in reading.lines if not line["is_heading"] and line["value"] and _kind(line) == kind]


def _kind(line: dict) -> str:
    """A line's kind: from its unit once lines are finished, else as its reader stated it, else its tag and label."""
    unit = line.get("unit")
    if unit:
        return PER_SHARE_KIND if unit.endswith("/share") else SHARES if unit == "shares" else MONEY
    return line.get("unit_kind") or _by_label(str(line.get("concept") or ""), str(line.get("label_en") or line["label"]))
