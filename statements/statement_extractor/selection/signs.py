"""Signs the filings disagree on for the same amount."""
from __future__ import annotations

from ..domain.model import Reading
from .lines import line_keys


def settle_signs(winner: Reading, kept: list[Reading]) -> Reading:
    """A line the filings agree on in amount but not in sign takes the sign most of them print it with. The sign comes
    from each filing's presentation (a negated label), and a filing can drop it: JPM's FY2012 10-K shows its 2010
    "total credit losses recognized in income" as 100 where the FY2010 and FY2011 10-Ks show (100), as printed."""
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
