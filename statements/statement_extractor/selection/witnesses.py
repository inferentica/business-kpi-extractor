"""Filings as witnesses of one another. A period is printed again by later filings; where they agree, the figure is
confirmed by a second reading of a different document. A presentation that disagrees with a filing before it and one
after it, while those two agree, is a misreading of that filing, not a restatement: a restatement carries on into the
filings that follow it. TSMC's FY2002 20-F read FY2000's income into the FY2001 column; its FY2001 and FY2003 20-Fs
print the same FY2001 income, so that presentation is set aside."""
from __future__ import annotations

from ..domain.model import Reading
from .figures import agree, comparable, key_figures


def _figures(reading: Reading) -> dict[str, float]:
    return key_figures(reading.statement, reading.lines)


def _disagree(left: dict, right: dict) -> bool:
    shared = comparable(left, right)
    return bool(shared) and any(not agree(left[name], right[name]) for name in shared)


def _agree(left: dict, right: dict) -> bool:
    shared = comparable(left, right)
    return bool(shared) and all(agree(left[name], right[name]) for name in shared)


def _alike(left: Reading, right: Reading) -> bool:
    """Two presentations that should print the same figures: one scope, basis and currency (TSMC's parent company
    and group, or its ROC GAAP and Taiwan-IFRS figures, differ by right)."""
    return left.accession != right.accession and (left.scope, left.basis, left.currency) == (right.scope, right.basis,
                                                                                            right.currency)


def contradicted(kept: list[Reading]) -> list[Reading]:
    """Presentations a filing before and a filing after them agree against."""
    figures = {id(reading): _figures(reading) for reading in kept}
    found = []
    for reading in kept:
        mine = figures[id(reading)]
        before = [other for other in kept if other.filed < reading.filed and _alike(other, reading)]
        after = [other for other in kept if other.filed > reading.filed and _alike(other, reading)]
        if any(_agree(figures[id(early)], figures[id(late)]) and _disagree(mine, figures[id(early)])
               for early in before for late in after):
            found.append(reading)
    return found


def verdict(winner: Reading, kept: list[Reading]) -> str:
    """confirmed: another filing prints the served key figures; restated: only earlier filings print the period, and
    otherwise (the newest word on it); single: no other filing prints it; contradicted: a later filing differs."""
    mine = _figures(winner)
    others = [reading for reading in kept if _alike(reading, winner) and comparable(mine, _figures(reading))]
    if not others:
        return "single"
    if any(_agree(mine, _figures(other)) for other in others):
        return "confirmed"
    return "restated" if all(other.filed <= winner.filed for other in others) else "contradicted"
