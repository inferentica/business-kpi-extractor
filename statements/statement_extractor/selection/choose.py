"""One statement per period from every filing that presents it: the newest filing's, among those whose arithmetic
holds. A later filing presents a period again as its comparative column, restated where the company restated it, so
the newest presentation is the company's own last word on that period. Which filing gave it is recorded, and how it
differs from the period's own filing."""
from __future__ import annotations

from collections import defaultdict

from ..domain.model import REGULATOR_SOURCES, Reading
from .lines import line_keys
from .signs import settle_signs
from .ties import tie
from .witnesses import contradicted, verdict


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
    annual_bases = {reading.fiscal_year: reading.basis for reading in sorted(readings, key=lambda item: item.filed)
                    if reading.own and reading.fiscal_period == "FY" and reading.form != "6-K" and reading.basis
                    and reading.source not in REGULATORS}
    reporting, by_year = _reporting_currency(readings), _reporting_currencies(readings)
    groups: dict[tuple, list[Reading]] = defaultdict(list)
    for reading in readings:
        groups[reading.key].append(reading)
    chosen = []
    for key, group in groups.items():
        # A statement in a currency the company does not report in is a convenience translation read as the
        # statement (TSMC's archived Q3 2001 nine months in US$): never served, even where nothing else covers the
        # period.
        kept = [reading for reading in group if reading.status == "kept"
                and (reading.currency is None or reading.currency in by_year.get(key[1], {reporting} - {None}) or not reporting)]
        own = next((reading for reading in sorted(group, key=lambda item: item.filed) if reading.own), None)
        # A report filed with the SEC (10-K, 20-F, 10-Q) wins over a release for the same period: a release's year and
        # year-end columns are unaudited, and the annual report that follows is the company's statement of them.
        # The group's statement wins over the parent company's alone, whatever their filing dates.
        # Among releases, the basis of the annual report closing the year: ASML's December 2012 6-K prints 2011's
        # quarters again on IFRS, beside its US GAAP releases and 20-F.
        annual = annual_bases.get(key[1])
        # A presentation a filing before it and one after it agree against is a misreading (selection/witnesses.py).
        set_aside = contradicted(kept)
        aside = {id(reading) for reading in set_aside}
        eligible = [reading for reading in kept if id(reading) not in aside] or kept
        winner = max(eligible, key=lambda item: (item.scope == "consolidated", _rank(item), not _coded(item),
                                             item.form != "6-K" or annual is None or item.basis == annual, item.filed,
                                             item.own, item.source == "xbrl")) if eligible else None
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
            if winner.source not in REGULATORS:  # a regulator's lines are served with their signs as filed
                winner = settle_signs(winner, kept, (printed or {}).get(winner.accession))
            record.update({
                "source_accession": winner.accession, "source_form": winner.form, "source_filed": winner.filed.isoformat(),
                "from_comparative": not winner.own, "currency": winner.currency, "basis": winner.basis,
                "scope": winner.scope, "witness": verdict(winner, eligible),
                "lines": winner.lines,
            })
            if set_aside:
                record["set_aside"] = sorted(f"{reading.accession} ({reading.filed.isoformat()})" for reading in set_aside)
            other = _other_bases(winner, kept)
            if other:
                record["other_bases"] = other
            if own is not None and own is not winner:
                record["versus_own"] = differences(winner, own)
        chosen.append(record)
    tie(chosen, readings)  # closing cash against the balance sheet, quarters against the year (selection/ties.py)
    return sorted(chosen, key=lambda item: (item["period_end"], item["statement"], item["duration"]), reverse=True)


REGULATORS = REGULATOR_SOURCES  # home regulators' own data (pipeline/regulators.py)


def _rank(reading: Reading) -> int:
    """A report filed with the SEC, then a home regulator's form, then a release: a regulator's quarterly form is the
    reviewed statement a 6-K release translates (Copel's ITR, KEPCO's quarterly report), so it stands with the SEC's
    reports for a quarter; for a year the 20-F, the statement filed with the SEC, stays first and the regulator's
    annual form is its witness."""
    if reading.source in REGULATORS:
        return 1 if reading.fiscal_period == "FY" else 2
    return 0 if reading.form == "6-K" else 2


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


def _reporting_currencies(readings: list[Reading]) -> dict[str, set[str]]:
    """Each fiscal year's reporting currencies: those the annual reports presenting the year state. UBS reported in
    Swiss francs to 2017 and in U.S. dollars from 2018, its 2018 20-F restating 2016 and 2017 in dollars."""
    # Each year's own annual report states its currency (most of its statements), and a report up to two years later
    # may restate it in its own; a translation column read in an annual report (KEPCO's US$) is neither.
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # A rejected reading is a misread, never the company's statement of its currency (Volaris's 2013-14 20-Fs, read
    # from the dollar translation column, rejected).
    for reading in readings:
        if reading.own and reading.form != "6-K" and reading.fiscal_period == "FY" and reading.currency and \
                reading.source not in REGULATORS and len(reading.currency) == 3 and reading.status != "rejected":
            counts[reading.fiscal_year][reading.currency] += 1
    own = {int(year): max(by, key=by.get) for year, by in counts.items()}
    if not own:
        return {}
    first, last = min(own), max(own)
    years: dict[str, set[str]] = {}
    for year in range(min(first, 1990), max(last, 2100) + 1):
        later = {own[other] for other in range(year, year + 3) if other in own}
        # a year with no annual report of its own read: the nearest year's currency
        nearest = own[min(own, key=lambda other: (abs(other - year), other))]
        years[str(year)] = later or {nearest}
        if year not in own:
            years[str(year)].add(nearest)
    return years


def _coded(reading: Reading) -> bool:
    """Read from Mexico's coded template (readers/printed/mexico_coded.py): its labels are machine translations
    ("NET INCOME" over the revenue), so a release's own presentation of the period is served before it."""
    return bool(reading.lines) and reading.lines[0]["concept"].startswith("bmv_")


def _reporting_currency(readings: list[Reading]) -> str | None:
    """The currency of the company's own annual reports (their most common), if they state one."""
    counts: dict[str, int] = defaultdict(int)
    for reading in readings:
        if reading.own and reading.fiscal_period == "FY" and reading.form != "6-K" and reading.currency and \
                reading.source not in REGULATORS and len(reading.currency) == 3 and reading.status != "rejected":
            counts[reading.currency] += 1
    return max(counts, key=counts.get) if counts else None
