"""Fourth quarters a company never states on their own, derived as the year less the nine months (Phase 6, vintage).

The year served is its newest presentation, often a 10-K two years on that restates it; the nine months served come
from the next year's third-quarter 10-Q. Their difference would mix two presentations. A fourth quarter is derived
from one vintage instead: the nine months' newest kept presentation, and the year as the first annual report filed
after it presents it, so that both stand as the company's books stood then. Lines pair by their key across eras
(selection/identity.py); a line only one of the two prints, a per-share figure or a share count is left without a
value, never guessed. Each derived quarter names both filings it came from. A quarter a filing states itself is never
replaced."""
from __future__ import annotations

from datetime import timedelta

from ..domain.model import REGULATOR_SOURCES as REGULATORS
from ..domain.printed_labels import PER_SHARE, SHARE_COUNT
from .identity import _same


def _key(statement: str, line: dict, keys: dict) -> tuple:
    return keys.get((statement, line["concept"], line.get("member") or ""), line["concept"]), line.get("member") or ""


def _keyed(reading, keys: dict) -> dict[tuple, list]:
    """Each valued line of a reading by its key across eras and member, in order (a cash flow's opening and closing
    cash share one key)."""
    found: dict[tuple, list] = {}
    for line in reading.lines:
        if not line.get("is_heading") and line.get("value") is not None:
            found.setdefault(_key(reading.statement, line, keys), []).append(line)
    return found


def _additive(line: dict) -> bool:
    """A line a year less its nine months gives the fourth quarter of: never a figure a share or a share count, by its
    unit (Coca-Cola's "Effect of dilutive securities (in shares)", a count its label alone does not name) or label."""
    label, unit = str(line.get("label") or ""), str(line.get("unit") or "")
    return not (unit == "shares" or unit.endswith("/share") or PER_SHARE.search(label) or SHARE_COUNT.search(label))


def _agree(left, right, keys: dict) -> bool:
    """Two presentations of one period that print every line they share alike (at the coarser rounding)."""
    mine, theirs = _keyed(left, keys), _keyed(right, keys)
    return all(_same(mine[key][0]["value"], theirs[key][0]["value"]) for key in mine.keys() & theirs.keys()
               if _additive(mine[key][0]))


def _recasts(whole, nine, earlier: list, keys: dict) -> bool:
    """Whether the year's report presents the year before it otherwise than it stood when the nine months were filed."""
    mine = [other for other in earlier if other.accession == whole.accession]
    known = [other for other in earlier if other.filed <= nine.filed and other.basis == whole.basis]
    return bool(mine and known) and not _agree(mine[0], max(known, key=lambda reading: reading.filed), keys)


def _pair(nines: list, years: list, keys: dict, earlier: list = ()):
    """The newest nine months that pair with a year of its vintage: the first annual report after it, filed within
    200 days (the same reporting cycle: Morgan Stanley's 2008 year as its 2011 10-K restates it is not its 2009
    10-Q's), its period opening on the nine months' first day and closing a quarter after them (RPM's 'nine months'
    read off a three-month column is none), from the same kind of source (Andina's 6-K year beside the CMF's form), with no restatement between them."""
    for nine in sorted(nines, key=lambda reading: reading.filed, reverse=True):
        after = [whole for whole in years if whole.filed > nine.filed and whole.basis == nine.basis
                 and whole.currency == nine.currency and (whole.source in REGULATORS) == (nine.source in REGULATORS)]
        if not after:
            continue
        whole = min(after, key=lambda reading: reading.filed)
        quarter = (whole.period_end - nine.period_end).days
        if (whole.filed - nine.filed).days > 200 or not 60 <= quarter <= 105 or (
                nine.period_start and whole.period_start and nine.period_start != whole.period_start):
            continue
        # No restatement between them: the year as last presented before the nine months were filed agrees with the
        # pair's (Vertex's 2002 year restated in March 2004 for a sale, its nine months last printed in November 2003:
        # its own 10-Q and 10-K, the year as first reported, pair instead).
        before = [other for other in years if other.filed <= nine.filed and other.basis == whole.basis]
        if before and not _agree(whole, max(before, key=lambda reading: reading.filed), keys):
            continue
        # Nor a year's report recasting the years before it after the nine months were filed (a business sold and
        # shown as discontinued in the 10-K): its earlier year disagrees with that year as presented before.
        if not _recasts(whole, nine, earlier, keys):
            return nine, whole
    return None


def derive_fourth_quarters(chosen: list[dict], readings: list, keys: dict) -> list[dict]:
    """Derived fourth quarters, for each income statement and cash flow year whose quarter no filing states."""
    served = {(item["statement"], item["fiscal_year"], item["fiscal_period"], item["duration"]): item for item in chosen}
    kept = [reading for reading in readings if reading.status == "kept" and reading.scope == "consolidated"]
    derived = []
    for (statement, year, period, duration), item in served.items():
        if period != "FY" or duration != "year" or statement == "balance" or item["status"] != "kept":
            continue
        if (statement, year, "Q4", "quarter") in served and served[(statement, year, "Q4", "quarter")]["status"] == "kept":
            continue
        nines = [reading for reading in kept if reading.key == (statement, year, "Q3", "ytd")
                 and reading.basis == item.get("basis") and reading.currency == item.get("currency")]
        if not nines:
            continue
        prior = str(int(year) - 1) if year.isdigit() else ""
        pair = _pair(nines, [reading for reading in kept if reading.key == (statement, year, "FY", "year")], keys,
                     [reading for reading in kept if reading.key == (statement, prior, "FY", "year")])
        if pair is None:
            continue  # no year and nine months of one vintage: left underived rather than mixed
        nine, whole = pair
        part, own = _keyed(nine, keys), _keyed(whole, keys)
        lines = []
        for position, line in enumerate(whole.lines):
            key = _key(statement, line, keys)
            value = None
            others, mine = part.get(key) or [], own.get(key) or []
            if line.get("is_heading") or line.get("value") is None or not others or not _additive(line):
                pass
            elif statement == "cash_flow" and len(mine) > 1:
                # A balance, no flow: the quarter opens on the nine months' closing cash and closes on the year's.
                value = others[-1]["value"] if line is mine[0] else line["value"]
            else:
                value = round(line["value"] - others[0]["value"], 6)
            lines.append({**line, "line": position, "value": value, "key": key[0]})
        derived.append({
            "statement": statement, "fiscal_year": year, "fiscal_period": "Q4", "duration": "quarter",
            "period_end": item["period_end"], "status": "kept", "derived": True,
            "derived_from": {"year": whole.accession, "year_filed": whole.filed.isoformat(),
                             "nine_months": nine.accession, "nine_months_filed": nine.filed.isoformat()},
            "source_accession": whole.accession, "source_form": whole.form, "source_filed": whole.filed.isoformat(),
            # The quarter after the nine months; served with the year's source, and no checks of its own (derived).
            "period_start": (nine.period_end + timedelta(days=1)).isoformat(), "source": whole.source,
            "from_comparative": not whole.own, "checks": {"checks": [], "arelle": None},
            "currency": whole.currency, "basis": whole.basis, "scope": whole.scope, "witness": "derived",
            "lines": [line for line in lines if line.get("is_heading") or line["value"] is not None],
        })
    return derived
