"""Records and writes to the control plane: every value and filing goes through here."""
from __future__ import annotations

from datetime import date

from ..sec import FilingRef
from .shared import AI_GROUP_PREFIX, CHART_METHOD, value_key

# The most attempts the store accepts for a filing: a count past it would fail the company's whole store.
_STORED_ATTEMPTS = 100

def _sec_source(value: dict) -> bool:
    url = value.get("source_url")
    return url is None or str(url).startswith("https://www.sec.gov/")


def _keeps_verified(existing: dict, incoming: dict) -> bool:
    # A figure read from a slide's chart is kept too against the same filing's unchecked reading of its text.
    return (existing["validation_status"] == "verified" and incoming["validation_status"] == "needs_review"
            and (existing.get("source_accession") != incoming.get("source_accession") or existing.get("method") == CHART_METHOD))



class Storage:
    """Records and writes to the control plane: every value and filing goes through here."""

    def _record(self, *, ref: FilingRef, period_end: date, source_url: str | None = None, **fields) -> dict:
        return {
            "symbol": self.symbol, **fields, "period_end": period_end.isoformat(),
            "source_accession": ref.accession, "source_url": source_url or ref.source_url,
            "filed_at": ref.filed.isoformat(),
        }

    def _store_filing(self, ref: FilingRef, status: str, *, records: list[dict] | None = None,
                      period_end: date | None = None, fiscal_year: str | None = None, fiscal_period: str | None = None,
                      spec_version: int | None = None, notes: list[str] | None = None, counted: bool = True) -> None:
        records = records or []
        previous = self.filings.get(ref.accession)
        # A re-read replaces the filing's earlier reading: values it no longer produces (a KPI key the new list renamed,
        # a figure now read differently, rows an older extractor kept) stop being served.
        # Only a reading that finished replaces the last one: a failed or empty attempt keeps the good values it had.
        method = "ai" if ref.role == "earnings_release" else "xbrl"
        if status in ("processed", "needs_review"):
            fresh = {value_key(record) for record in records}
            records = records + [
                {**value, "validation_status": "rejected", "notes": ["replaced by a newer reading of this filing"]}
                for value in self.values.values()
                if value["method"] == method and value.get("source_accession") == ref.accession
                and value["validation_status"] != "rejected" and value_key(value) not in fresh
                # A group not read this run keeps its rows (flagged, and so not served): they are why it is not read.
                and value["group_key"].removeprefix(AI_GROUP_PREFIX) not in (self._dead or set())
            ]
        filing = {
            "accession": ref.accession, "form": ref.form, "filed_at": ref.filed.isoformat(), "document_role": ref.role,
            "period_end": period_end.isoformat() if period_end else None, "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period, "status": status, "spec_version": spec_version,
            "value_count": sum(1 for record in records if record["validation_status"] != "rejected"), "attempts": min(_STORED_ATTEMPTS, previous.get("attempts", 0) + 1) if previous else 1,
            "notes": [note[:300] for note in (notes or [])][:30], "source_url": ref.source_url,
        }
        self._store(filings=[filing], values=records)
        self.filings[ref.accession] = filing
        if not counted:
            return
        self.result.filings += 1
        self.result.needs_review += sum(1 for record in records if record["validation_status"] == "needs_review")
        self.log(f"{self.symbol}: {ref.form} {ref.filed} {status} ({filing['value_count']} values)")

    def _fail_filing(self, ref: FilingRef, error: Exception, spec_version: int | None = None) -> None:
        message = f"{type(error).__name__}: {error}"[:300]
        self.result.errors.append(f"{ref.accession}: {message}")
        self._store_filing(ref, "failed", spec_version=spec_version, notes=[message])

    def _store(self, *, filings: list[dict] | None = None, values: list[dict] | None = None) -> None:
        values = list({value_key(value): value for value in values or []}.values())  # one row per key per write
        # A note once: a check that settles a part each run, which the whole breakdown then holds back, added its note
        # again every run until JPM's reached the control plane's 40 and its run failed.
        values = [{**value, "notes": list(dict.fromkeys(value["notes"]))} if value.get("notes") else value for value in values]
        # A figure from outside SEC filings (Unity's segment spreadsheet on its investor site) was entered by the owner:
        # the extractor never rewrites it, and the control plane takes only SEC sources, so carrying one in a write
        # failed the whole run. A figure worked out from one keeps it out of its source.
        values = [value if _sec_source(value) else {**value, "source_url": None}
                  for value in values if _sec_source(value) or value.get("method") == "derived"]
        if not values and not filings:
            return
        self.control.call("store", symbol=self.symbol, filings=filings or [], values=values)
        for value in values:
            existing = self.values.get(value_key(value))
            if existing and _keeps_verified(existing, value):
                continue  # as store_business_kpis does: another filing's unchecked reading never replaces a verified one
            self.values[value_key(value)] = value
        self.result.values += len(values)
