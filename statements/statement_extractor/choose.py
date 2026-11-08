"""One statement per period from every filing that presents it: the newest filing's, among those whose arithmetic
holds. A later filing presents a period again as its comparative column, restated where the company restated it, so
the newest presentation is the company's own last word on that period. Which filing gave it is recorded, and how it
differs from the period's own filing."""
from __future__ import annotations

from collections import defaultdict

from .model import Reading

_RESTATED = 1e-6  # a line differs when it moved by more than this share of itself (and by more than one unit)


def line_keys(lines: list[dict]) -> dict[str, float | None]:
    """Lines keyed by concept and occurrence (a concept shown twice, opening and closing cash, stays two lines)."""
    seen: dict[str, int] = {}
    keyed = {}
    for line in sorted(lines, key=lambda item: item["line"]):
        if line["is_heading"]:
            continue
        occurrence = seen.get(line["concept"], 0)
        seen[line["concept"]] = occurrence + 1
        keyed[f"{line['concept']}#{occurrence}" if occurrence else line["concept"]] = line["value"]
    return keyed


def differences(later: Reading, earlier: Reading) -> dict:
    new, old = line_keys(later.lines), line_keys(earlier.lines)
    changed = [key for key in new.keys() & old.keys()
               if new[key] is not None and old[key] is not None
               and abs(new[key] - old[key]) > max(1.0, _RESTATED * max(abs(new[key]), abs(old[key])))]
    return {"changed": sorted(changed), "added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys())}


def choose(readings: list[Reading]) -> list[dict]:
    """Per statement period: the chosen reading (or why none), its source, and how it differs from the period's own
    filing. Sorted newest period first."""
    groups: dict[tuple, list[Reading]] = defaultdict(list)
    for reading in readings:
        groups[reading.key].append(reading)
    chosen = []
    for key, group in groups.items():
        kept = [reading for reading in group if reading.status == "kept"]
        own = next((reading for reading in sorted(group, key=lambda item: item.filed) if reading.own), None)
        winner = max(kept, key=lambda item: (item.filed, item.own)) if kept else None
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
            winner = _settle_signs(winner, kept)
            record.update({
                "source_accession": winner.accession, "source_form": winner.form, "source_filed": winner.filed.isoformat(),
                "from_comparative": not winner.own, "currency": winner.currency, "lines": winner.lines,
            })
            if own is not None and own is not winner:
                record["versus_own"] = differences(winner, own)
        chosen.append(record)
    return sorted(chosen, key=lambda item: (item["period_end"], item["statement"], item["duration"]), reverse=True)


def _settle_signs(winner: Reading, kept: list[Reading]) -> Reading:
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


def mixed_vintage_q4s(chosen: list[dict]) -> list[str]:
    """Fourth quarters the serving derives as the year less nine months, where one of the two was restated and the
    other not: their lines may follow different presentations, so the difference would mix them."""
    by_key = {(item["statement"], item["fiscal_year"], item["fiscal_period"], item["duration"]): item for item in chosen}
    found = []
    for (statement, year, period, duration), item in by_key.items():
        if period != "FY" or duration != "year" or statement == "balance" or item["status"] != "kept":
            continue
        nine = by_key.get((statement, year, "Q3", "ytd"))
        if not nine or nine["status"] != "kept":
            continue
        restated = [bool((part.get("versus_own") or {}).get("changed")) for part in (item, nine)]
        if restated[0] != restated[1]:
            found.append(f"{statement} {year}")
    return sorted(found)
