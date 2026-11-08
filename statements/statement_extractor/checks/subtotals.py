"""Which printed lines are subtotals of the lines above them, and where each one's lines start."""
from __future__ import annotations

import re

from .tolerance import close


# Labels a subtotal can carry: none at all, a total, a net or gross figure, an income or profit line, one before or
# after something. Apple's 1999 retained earnings (1,499) equals the two lines above it (150 + 1,349) by chance.
_SUBTOTAL_LABEL = re.compile(r"^$|^total\b|\bnet\b|^gross\b|\bincome\b|\bprofit\b|\bloss\b|\bearnings\b|\bbefore\b|"
                             r"\bafter\b|\bcomprehensive\b|^revenues?\b|\bmargin\b|\bactivities\b|^cash\s+(generated|provided|used)\b",
                             re.I)
# Subtotals that sum lines under more than one heading.
_SPANS_HEADINGS = re.compile(r"^total\b|comprehensive\s+income|\bfor\s+the\s+(year|period)\b|\bactivities\b|\boperations\b",
                             re.I)


def subtotals(printed) -> dict[int, int]:
    """Where each subtotal's lines start: a printed "Total …", or a line equal to the two or more lines just above it
    (an unlabelled net figure). An earlier subtotal counts as one line."""
    starts: dict[int, int] = {}
    for index, (label, _level, value) in enumerate(printed):
        if not _SUBTOTAL_LABEL.search(label) and not _repeats_its_heading(printed, index) \
                and not _totals_its_others(printed, index):
            continue  # a line of its own ("Retained earnings"), however its figure happens to add up
        if value is None:
            continue
        sums, position, total = [], index - 1, 0.0
        while position >= 0 and len(sums) < 12:
            earlier_label, _earlier_level, earlier_value = printed[position]
            if earlier_value is None:
                if sums and _SPANS_HEADINGS.search(label):
                    position -= 1
                    continue  # a subtotal over several headed blocks (TSMC's other comprehensive income for the year)
                break
            total += -abs(earlier_value) if re.match(r"^less\b", earlier_label, re.I) else earlier_value
            sums.append((position, total))
            position = block_start(starts, position) - 1
        for count, (start, running) in enumerate(sums, 1):
            if count >= 2 and close(running, value, count + 1):
                starts[index] = start
                break
        else:
            # A difference of the two lines above: JPM's net interest income, interest income less interest expense,
            # both printed as plain figures.
            if index >= 2 and printed[index - 1][2] is not None and printed[index - 2][2] is not None \
                    and close(printed[index - 2][2] - printed[index - 1][2], value, 3) and index - 1 not in starts:
                starts[index] = index - 2
    return starts


def block_start(starts: dict[int, int], index: int) -> int:
    """The first line a subtotal sums, through subtotals inside it (Apple's net property, plant and equipment sums an
    unlabelled gross figure that sums its own lines)."""
    seen = set()
    while index in starts and index not in seen:
        seen.add(index)
        index = starts[index]
    return index


def _repeats_its_heading(printed, index: int) -> bool:
    """A line labelled as the heading its block opens with sums that block: Kroger's "Long-term debt including
    obligations under capital leases" heads its face value and hedge adjustment, and its total repeats the words."""
    key = re.sub(r"[^a-z0-9]+", " ", printed[index][0].lower()).strip()
    for position in range(index - 1, -1, -1):
        earlier_label, _level, earlier_value = printed[position]
        if earlier_value is None:
            return bool(key) and re.sub(r"[^a-z0-9]+", " ", earlier_label.lower()).strip() == key and index - position > 2
    return False


def _totals_its_others(printed, index: int) -> bool:
    """A line named as the "Other …" line just above it, less "Other", totals its block: PepsiCo's goodwill and "Other
    nonamortizable intangible assets", then "Nonamortizable intangible assets"."""
    if index < 2:
        return False
    key = re.sub(r"[^a-z0-9]+", " ", printed[index][0].lower()).strip()
    above = re.sub(r"[^a-z0-9]+", " ", printed[index - 1][0].lower()).strip()
    return bool(key) and above == f"other {key}"
