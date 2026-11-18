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


# A regulator's form is public on its own market's day: Brazil's CVM, Korea's DART, Chile's CMF, Mexico's BMV.
_ZONES = {"cvm": ZoneInfo("America/Sao_Paulo"), "dart": ZoneInfo("Asia/Seoul"), "cmf": ZoneInfo("America/Santiago"),
          "bmv": ZoneInfo("America/Mexico_City")}
_LATE = 3  # days after its filing date beyond which an acceptance stamp is a re-acceptance, never its publication


def available_from(accession: str, filed: str, accepted: dict[str, str | None], source: str | None = None) -> str:
    """When the source became public: EDGAR's acceptance time, where it states one. Else the end of its filing day,
    never earlier than it was public, so a point-in-time query never sees it too soon: a regulator's form on its own
    market's day; a filing EDGAR stamps at midnight New York, a date with no time (2,017 rows of filings before April
    2002); one stamped more than three days after its filing date, a re-acceptance (Salesforce's 10-Q filed as of June 1,
    2020, under an accession accepted February 24, 2022)."""
    day = date.fromisoformat(str(filed)[:10])
    stamp = accepted.get(accession)
    if stamp:
        when = datetime.fromisoformat(str(stamp))
        local = (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(_EASTERN)
        dated_only = (local.hour, local.minute, local.second) == (0, 0, 0)
        if not dated_only and (local.date() - day).days <= _LATE:
            return str(stamp)
    zone = _ZONES.get(str(source or "").lower(), _EASTERN)
    return datetime.combine(day, time(23, 59, 59), zone).astimezone(timezone.utc).isoformat()


def _row(record: dict, source: dict, primary: bool, accepted: dict) -> dict:
    accession = source["source_accession"]
    own = record.get("own_accession")
    return {
        **{field: record[field] for field in _PERIOD}, "basis": source["basis"], "is_primary": primary,
        "period_start": source.get("period_start"), "period_end": source.get("period_end") or record["period_end"],
        "currency": source["currency"], "scope": source["scope"], "source": source["source"],
        "source_accession": accession, "source_form": source["source_form"], "source_filed": source["source_filed"],
        "available_from": available_from(accession, source["source_filed"], accepted, source.get("source")),
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
