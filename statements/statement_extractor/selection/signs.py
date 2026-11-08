"""Signs the filings disagree on for the same amount."""
from __future__ import annotations

from ..domain.model import Reading
from .lines import line_keys


def settle_signs(winner: Reading, kept: list[Reading]) -> Reading:
    """A line the filings agree on in amount but not in sign takes the sign its own filing prints it with, where that
    filing's printed statement was read, else the sign most presentations give it. An XBRL sign comes from the filing's
    presentation (a negated label), and a filing can drop it: JPM's FY2012 10-K tags its 2010 "total credit losses
    recognized in income" as 100 where its printed statement, and the FY2010 and FY2011 10-Ks, show (100)."""
    printed = next((reading for reading in kept if reading is not winner and reading.accession == winner.accession
                    and reading.source != "xbrl"), None)
    if printed is not None:
        return _signs_from(winner, printed)
    if len(kept) < 3:
        return winner
    others = [line_keys(reading.lines) for reading in kept if reading is not winner]
    # A filer can retag a line between filings (JPM's credit losses moved to "…AvailableforsaleSecurities"); the
    # printed label still names it.
    labelled = [{line["label"].lower(): line["value"] for line in reading.lines if not line["is_heading"]}
                for reading in kept if reading is not winner]
    lines = []
    flipped = 0
    seen: dict[str, int] = {}
    for line in sorted(winner.lines, key=lambda item: item["line"]):
        line = dict(line)
        if not line["is_heading"] and line["value"]:
            occurrence = seen.get(line["concept"], 0)
            seen[line["concept"]] = occurrence + 1
            key = f"{line['concept']}#{occurrence}" if occurrence else line["concept"]
            values = []
            for keyed, by_label in zip(others, labelled):
                value = keyed.get(key)
                if value is None:
                    value = by_label.get(line["label"].lower())
                if value is not None and abs(abs(value) - abs(line["value"])) < 0.5:
                    values.append(value)
            opposite = sum(1 for value in values if (value < 0) != (line["value"] < 0))
            if opposite > len(values) - opposite + 1:  # most presentations, the newest's included, print the other sign
                line["value"] = -line["value"]
                flipped += 1
        lines.append(line)
    if not flipped:
        return winner
    from dataclasses import replace
    return replace(winner, lines=lines)


def _signs_from(winner: Reading, printed: Reading) -> Reading:
    """Each line's sign as the filing prints it, matched by label and amount."""
    from dataclasses import replace
    by_label = {}
    for line in printed.lines:
        if not line["is_heading"] and line["value"]:
            by_label.setdefault(line["label"].lower(), line["value"])
    lines, flipped = [], 0
    for line in winner.lines:
        line = dict(line)
        shown = by_label.get(line["label"].lower())
        if line["value"] and shown is not None and abs(abs(shown) - abs(line["value"])) < 0.5 and (shown < 0) != (line["value"] < 0):
            line["value"] = -line["value"]
            flipped += 1
        lines.append(line)
    return replace(winner, lines=lines) if flipped else winner


def sign_ties(readings: list[Reading]) -> set[str]:
    """Filings whose XBRL figure for a line is the served one and disagrees in sign with another presentation, with no
    majority either way: the filing's printed statement is read to settle it."""
    by_key: dict[tuple, list[Reading]] = {}
    for reading in readings:
        if reading.status == "kept":
            by_key.setdefault(reading.key, []).append(reading)
    found = set()
    for group in by_key.values():
        if len(group) < 2:
            continue
        winner = max(group, key=lambda item: (item.filed, item.own, item.source == "xbrl"))
        if winner.source != "xbrl":
            continue
        mine = {line["label"].lower(): line["value"] for line in winner.lines if not line["is_heading"] and line["value"]}
        for other in group:
            if other is winner:
                continue
            for line in other.lines:
                value, label = line["value"], line["label"].lower()
                if value and label in mine and abs(abs(value) - abs(mine[label])) < 0.5 and (value < 0) != (mine[label] < 0):
                    found.add(winner.accession)
    return found
