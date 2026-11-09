"""Signs the filings disagree on for the same amount."""
from __future__ import annotations

from ..domain.labels import label_key
from ..domain.model import Reading
from .lines import line_keys


def settle_signs(winner: Reading, kept: list[Reading], printed: dict[str, list[float]] | None = None) -> Reading:
    """A line the filings agree on in amount but not in sign takes the sign its own filing prints it with, where that
    filing's printed statement was read, else the sign most presentations give it. An XBRL sign comes from the filing's
    presentation (a negated label), and a filing can drop it: JPM's FY2012 10-K tags its 2010 "total credit losses
    recognized in income" as 100 where its printed statement, and the FY2010 and FY2011 10-Ks, show (100)."""
    # The same filing's printed statement on the same basis (ASML's 6-Ks print IFRS beside US GAAP, its own signs).
    statement = next((reading for reading in kept if reading is not winner and reading.accession == winner.accession
                      and reading.source != "xbrl" and reading.basis == winner.basis), None)
    if statement is not None:
        winner = _signs_from(winner, statement)
    if printed:
        shown = set() if statement is None else {label_key(line["label"]) for line in statement.lines}
        disputed = _disputed(winner, [reading for reading in kept if reading is not winner]) - shown
        settled = _signs_from_tables(winner, printed, disputed)
        if settled is not winner or statement is not None:
            return settled
    elif statement is not None:
        return winner
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
    """Each line's sign as the filing prints it, matched by label, occurrence and amount: Salesforce prints "Excess tax
    benefits from employee stock plans" twice, (6,018) under operating and 6,018 under financing activities."""
    from dataclasses import replace
    by_label: dict[str, list[float]] = {}
    for line in printed.lines:
        if not line["is_heading"] and line["value"]:
            by_label.setdefault(label_key(line["label"]), []).append(line["value"])
    lines, flipped, seen = [], 0, {}
    for line in winner.lines:
        line = dict(line)
        label = label_key(line["label"])  # "plant, and equipment" in XBRL, "plant and equipment" printed (Broadcom)
        occurrence = seen.get(label, 0)
        if not line["is_heading"] and line["value"]:
            seen[label] = occurrence + 1
        printed_values = by_label.get(label, [])
        shown = printed_values[occurrence] if occurrence < len(printed_values) else None
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


_SCALES = (1.0, 1e3, 1e6, 1e9)


def _disputed(winner: Reading, others: list[Reading]) -> set[str]:
    """Labels another presentation shows at the winner's amount with the other sign."""
    mine = {label_key(line["label"]): line["value"] for line in winner.lines if not line["is_heading"] and line["value"]}
    found = set()
    for reading in others:
        for line in reading.lines:
            key, value = label_key(line["label"]), line["value"]
            if value and key in mine and abs(abs(value) - abs(mine[key])) < 0.5 and (value < 0) != (mine[key] < 0):
                found.add(key)
    return found


def _signs_from_tables(winner: Reading, printed: dict[str, list[float]], disputed: set[str]) -> Reading:
    """A disputed line's sign from the figure printed under its label anywhere in its filing, matched by amount at
    any of the usual scales; a line printed with both signs is left as it is."""
    from dataclasses import replace
    lines, flipped = [], 0
    for line in winner.lines:
        line = dict(line)
        value = line["value"]
        if value and not line["is_heading"] and label_key(line["label"]) in disputed:
            shown = [figure for figure in printed.get(label_key(line["label"]), [])
                     if any(abs(abs(figure) * scale - abs(value)) <= 0.5 * scale for scale in _SCALES)]
            signs = {figure < 0 for figure in shown if figure}
            if len(signs) == 1 and signs.pop() != (value < 0):
                line["value"] = -value
                flipped += 1
        lines.append(line)
    return replace(winner, lines=lines) if flipped else winner
