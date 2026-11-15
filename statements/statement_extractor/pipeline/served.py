"""The served snapshot of one company (Phase 7, s7-writes): what Quotiva serves, in the shape statements_store takes
(statements/docs/storage.md). One row per kept statement period and basis, the chosen basis primary, the newest
presentation on each other basis beside it; each row stamped with when its source became public (`available_from`,
EDGAR's acceptance time), its lines with the schema's fields only. Written as served.json beside the run's files."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from ..domain.line_fields import served_line

_EASTERN = ZoneInfo("America/New_York")
_PERIOD = ("statement", "fiscal_year", "fiscal_period", "duration")


def available_from(accession: str, filed: str, accepted: dict[str, str | None]) -> str:
    """When the source became public: EDGAR's acceptance time; else (a regulator's form, an archived report) the end of
    its filing day in New York, never earlier than it was public, so a point-in-time query never sees it too soon."""
    if accepted.get(accession):
        return str(accepted[accession])
    day = date.fromisoformat(str(filed)[:10])
    return datetime.combine(day, time(23, 59, 59), _EASTERN).astimezone(timezone.utc).isoformat()


def _row(record: dict, source: dict, primary: bool, accepted: dict) -> dict:
    accession = source["source_accession"]
    own = record.get("own_accession")
    return {
        **{field: record[field] for field in _PERIOD}, "basis": source["basis"], "is_primary": primary,
        "period_start": source.get("period_start"), "period_end": record["period_end"],
        "currency": source["currency"], "scope": source["scope"], "source": source["source"],
        "source_accession": accession, "source_form": source["source_form"], "source_filed": source["source_filed"],
        "available_from": available_from(accession, source["source_filed"], accepted),
        "from_comparative": bool(source["from_comparative"]), "own_accession": own if own != accession else None,
        "witness": source["witness"], "derived_from": record.get("derived_from") if primary else None,
        "checks": source["checks"], "lines": [served_line(line) for line in source["lines"]],
    }


def served_snapshot(symbol: str, cik: int | None, chosen: list[dict], filings: list[dict],
                    ledger: list[tuple[date, float, str]], summary: dict) -> dict:
    accepted = {record["accession"]: record.get("accepted") for record in filings}
    periods, unserved = [], []
    for record in chosen:
        if record.get("status") != "kept" or not record.get("lines"):
            continue
        for row in [_row(record, record, True, accepted)] + [
                _row(record, other, False, accepted) for other in record.get("other_bases") or []]:
            # A presentation whose currency its filing does not state is never served: Quotiva refuses it, and a
            # currency guessed would be worse than none. Listed in the read's summary instead.
            if row["currency"]:
                periods.append(row)
            else:
                unserved.append({**{field: row[field] for field in (*_PERIOD, "basis", "source_accession")},
                                 "why": "no currency stated"})
    filed = [str(record["filed"])[:10] for record in filings if record.get("filed")]
    coverage = {field: summary.get(field) for field in (
        "periods", "by_status", "own_expected", "own_kept", "quarters_expected", "quarters_kept", "first_period",
        "witnesses", "ai_calls", "cost_cny")}
    return {
        "cik": cik, "symbol": symbol, "periods": periods,
        "splits": [{"first_filed": when.isoformat(), "ratio": ratio, "share_class": member} for when, ratio, member in ledger],
        "read": {"last_filed": max(filed) if filed else None, "coverage": {**coverage, "unserved": unserved}},
    }
