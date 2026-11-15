"""Fourth quarters a company never states on their own, derived as the year less the nine months (Phase 6, vintage).

The year served is its newest presentation, often a 10-K two years on that restates it; the nine months served come
from the next year's third-quarter 10-Q. Their difference would mix two presentations. A fourth quarter is derived
from one vintage instead: the nine months' newest kept presentation, and the year as the first annual report filed
after it presents it, so that both stand as the company's books stood then. Lines pair by their key across eras
(selection/identity.py); a line only one of the two prints, a per-share figure or a share count is left without a
value, never guessed. Each derived quarter names both filings it came from. A quarter a filing states itself is never
replaced."""
from __future__ import annotations

from ..domain.printed_labels import PER_SHARE, SHARE_COUNT


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
    label = str(line.get("label") or "")
    return not (PER_SHARE.search(label) or SHARE_COUNT.search(label))


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
        nine = max(nines, key=lambda reading: reading.filed)
        years = [reading for reading in kept if reading.key == (statement, year, "FY", "year") and reading.filed > nine.filed
                 and reading.basis == nine.basis and reading.currency == nine.currency]
        if not years:
            continue
        whole = min(years, key=lambda reading: reading.filed)
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
            "currency": whole.currency, "basis": whole.basis, "scope": whole.scope, "witness": "derived",
            "lines": [line for line in lines if line.get("is_heading") or line["value"] is not None],
        })
    return derived
