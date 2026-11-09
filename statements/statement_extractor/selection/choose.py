"""One statement per period from every filing that presents it: the newest filing's, among those whose arithmetic
holds. A later filing presents a period again as its comparative column, restated where the company restated it, so
the newest presentation is the company's own last word on that period. Which filing gave it is recorded, and how it
differs from the period's own filing."""
from __future__ import annotations

from collections import defaultdict

from ..domain.model import Reading
from .lines import line_keys
from .signs import settle_signs


_RESTATED = 1e-6  # a line differs when it moved by more than this share of itself (and by more than one unit)


def differences(later: Reading, earlier: Reading) -> dict:
    """How a later presentation of a period differs from its own filing's: lines restated, lines whose sign alone
    differs (a presentation choice, JPM's impairment line printed as a loss in one filing and a charge in another),
    and lines added or removed."""
    new, old = line_keys(later.lines), line_keys(earlier.lines)
    both = [key for key in new.keys() & old.keys() if new[key] is not None and old[key] is not None]
    moved = [key for key in both if abs(new[key] - old[key]) > max(1.0, _RESTATED * max(abs(new[key]), abs(old[key])))]
    sign_only = [key for key in moved if abs(abs(new[key]) - abs(old[key])) <= max(1.0, _RESTATED * abs(new[key]))]
    return {"changed": sorted(set(moved) - set(sign_only)), "sign_only": sorted(sign_only),
            "added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys())}


def choose(readings: list[Reading], printed: dict[str, dict[str, list[float]]] | None = None) -> list[dict]:
    """Per statement period: the chosen reading (or why none), its source, and how it differs from the period's own
    filing. Sorted newest period first."""
    groups: dict[tuple, list[Reading]] = defaultdict(list)
    for reading in readings:
        groups[reading.key].append(reading)
    chosen = []
    for key, group in groups.items():
        kept = [reading for reading in group if reading.status == "kept"]
        own = next((reading for reading in sorted(group, key=lambda item: item.filed) if reading.own), None)
        winner = max(kept, key=lambda item: (item.filed, item.own, item.source == "xbrl")) if kept else None
        record = {
            "statement": key[0], "fiscal_year": key[1], "fiscal_period": key[2], "duration": key[3],
            "period_end": max(reading.period_end for reading in group).isoformat(),
            "status": "kept" if winner else ("rejected" if any(r.status == "rejected" for r in group) else "unproven"),
            "presentations": len(group), "kept_presentations": len(kept),
            "own_accession": own.accession if own else None, "own_status": own.status if own else None,
            "rejections": sorted({f"{reading.accession}: {check.name} {check.detail}"[:200] for reading in group
                                  for check in reading.checks if check.result == "fail"})[:5],
        }
        if winner is not None:
            winner = settle_signs(winner, kept, (printed or {}).get(winner.accession))
            record.update({
                "source_accession": winner.accession, "source_form": winner.form, "source_filed": winner.filed.isoformat(),
                "from_comparative": not winner.own, "currency": winner.currency, "basis": winner.basis,
                "lines": winner.lines,
            })
            other = _other_bases(winner, kept)
            if other:
                record["other_bases"] = other
            if own is not None and own is not winner:
                record["versus_own"] = differences(winner, own)
        chosen.append(record)
    return sorted(chosen, key=lambda item: (item["period_end"], item["statement"], item["duration"]), reverse=True)


def _other_bases(winner: Reading, kept: list[Reading]) -> list[dict]:
    """The newest presentation of the period on each other basis: a change of basis restates the years before it
    (TSMC's FY2013 20-F prints 2012 on IFRS), and the period as first reported on the old basis stays on record."""
    newest: dict[str, Reading] = {}
    for reading in kept:
        if reading.basis and reading.basis != winner.basis:
            if reading.basis not in newest or reading.filed > newest[reading.basis].filed:
                newest[reading.basis] = reading
    return [{"basis": basis, "source_accession": reading.accession, "source_filed": reading.filed.isoformat(),
             "currency": reading.currency, "lines": reading.lines} for basis, reading in sorted(newest.items())]
