"""Which printed lines are subtotals of the lines above them, and where each one's lines start."""
from __future__ import annotations

import re

from .tolerance import close


# Subtotals that sum lines under more than one heading.
_SPANS_HEADINGS = re.compile(r"^total\b|comprehensive\s+income|\bfor\s+the\s+(year|period)\b", re.I)


def subtotals(printed) -> dict[int, int]:
    """Where each subtotal's lines start: a printed "Total …", or a line equal to the two or more lines just above it
    (an unlabelled net figure). An earlier subtotal counts as one line."""
    starts: dict[int, int] = {}
    for index, (label, _level, value) in enumerate(printed):
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
            total += -earlier_value if re.match(r"^less\b", earlier_label, re.I) else earlier_value
            sums.append((position, total))
            position = starts.get(position, position) - 1
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
