"""One company's stored figures and filings, and the one way to change them. Steps read them here, read-only, and hand
back the rows they want written: nothing else writes values or filings to the control plane."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType

from .ai import KpiSpec
from .sources import accepts_url
from .sources.base import FilingRef
from .steps.shared import AI_GROUP_PREFIX, CHART_METHOD, value_key

# The most attempts the store accepts for a filing: a count past it would fail the company's whole store.
_STORED_ATTEMPTS = 100
# Rows sent in one call.
_BATCH = 400


def _source_document(value) -> bool:
    """A value points to one of its source's own documents (SEC EDGAR's today), or to none."""
    return accepts_url(value.get("source_url"))


def _keeps_verified(existing, incoming) -> bool:
    # A figure read from a slide's chart is kept too against the same filing's unchecked reading of its text.
    return (existing["validation_status"] == "verified" and incoming["validation_status"] == "needs_review"
            and (existing.get("source_accession") != incoming.get("source_accession") or existing.get("method") == CHART_METHOD))


@dataclass
class FilingReading:
    """What came of reading one filing: its rows and how it ended."""
    ref: FilingRef
    status: str
    records: list[dict] = field(default_factory=list)
    period_end: date | None = None
    fiscal_year: str | None = None
    fiscal_period: str | None = None
    spec_version: int | None = None
    notes: list[str] = field(default_factory=list)
    counted: bool = True
    error: Exception | None = None


def failed_reading(ref: FilingRef, error: Exception, spec_version: int | None = None) -> FilingReading:
    return FilingReading(ref, "failed", spec_version=spec_version, notes=[f"{type(error).__name__}: {error}"[:300]], error=error)


class CompanyStore:
    def __init__(self, control, symbol: str, result, log=print):
        self.control = control
        self.symbol = symbol
        self.result = result
        self.log = log
        self._values: dict[tuple, MappingProxyType] = {}
        self._filings: dict[str, dict] = {}
        self.owner_rejected: list[dict] = []
        # Each listed KPI's reported periods (group key, KPI key) -> (since, until): a figure outside them is not kept.
        self.windows: dict[tuple[str, str], tuple[str | None, str | None]] = {}
        self._loaded: dict[tuple, MappingProxyType] = {}
        self._owner_rejected: set[tuple] = set()
        self.values = MappingProxyType(self._values)
        self.filings = MappingProxyType(self._filings)

    def load(self, values: list[dict] = (), filings: list[dict] = (), owner_rejected: list[dict] = ()) -> None:
        """What the control plane holds for the company, in place of anything held before. It does not send rejected rows;
        `owner_rejected` names those the owner rejected, which no reading may bring back."""
        self.owner_rejected = list(owner_rejected)
        self._owner_rejected = {value_key(row) for row in owner_rejected}
        self._values.clear()
        self._values.update({value_key(value): MappingProxyType(dict(value)) for value in values})
        self._loaded = dict(self._values)
        self._filings.clear()
        self._filings.update({filing["accession"]: filing for filing in filings})

    def write(self, rows: list) -> None:
        for start in range(0, len(rows), _BATCH):
            self._send(values=rows[start:start + _BATCH])

    def record(self, reading: FilingReading, unread_groups: set[str] | None = None) -> None:
        """A filing and its rows, in one call. A re-read replaces the filing's earlier reading: values it no longer
        produces (a KPI key the new list renamed, a figure now read differently, rows an older extractor kept) stop being
        served. Only a reading that finished replaces the last one: a failed or empty attempt keeps the good values it had."""
        ref, records = reading.ref, reading.records
        if reading.error is not None:
            self.result.errors.append(f"{ref.accession}: {reading.notes[0]}")
        previous = self._filings.get(ref.accession)
        method = "ai" if ref.role == "earnings_release" else "xbrl"
        if reading.status in ("processed", "needs_review"):
            fresh = {value_key(record) for record in records}
            records = records + [
                {**value, "validation_status": "rejected", "notes": ["replaced by a newer reading of this filing"]}
                for value in self._values.values()
                if value["method"] == method and value.get("source_accession") == ref.accession
                and value["validation_status"] != "rejected" and value_key(value) not in fresh
                # A group not read this run keeps its rows (flagged, and so not served): they are why it is not read.
                and value["group_key"].removeprefix(AI_GROUP_PREFIX) not in (unread_groups or set())
            ]
        filing = {
            "accession": ref.accession, "form": ref.form, "filed_at": ref.filed.isoformat(), "document_role": ref.role,
            "period_end": reading.period_end.isoformat() if reading.period_end else None, "fiscal_year": reading.fiscal_year,
            "fiscal_period": reading.fiscal_period, "status": reading.status, "spec_version": reading.spec_version,
            "value_count": sum(1 for record in records if record["validation_status"] != "rejected"), "attempts": min(_STORED_ATTEMPTS, previous.get("attempts", 0) + 1) if previous else 1,
            "notes": [note[:300] for note in reading.notes][:30], "source_url": ref.source_url,
        }
        self._send(filings=[filing], values=records)
        self._filings[ref.accession] = filing
        if not reading.counted:
            return
        self.result.filings += 1
        self.result.needs_review += sum(1 for record in records if record["validation_status"] == "needs_review")
        self.log(f"{self.symbol}: {ref.form} {ref.filed} {reading.status} ({filing['value_count']} values)")

    def record_or_fail(self, reading: FilingReading, unread_groups: set[str] | None = None) -> None:
        """As record; a reading that cannot be stored marks its filing failed, so one bad filing does not stop the company."""
        try:
            self.record(reading, unread_groups)
        except Exception as error:  # noqa: BLE001
            if reading.error is not None:
                raise
            self.record(failed_reading(reading.ref, error, reading.spec_version), unread_groups)

    def served_changes(self) -> dict:
        """What this run changed among the figures served: every run says so, so a change that should have changed
        nothing (a refactor, a new reader) shows itself without anyone comparing tables."""
        served = lambda rows: {key: row for key, row in rows.items() if row["validation_status"] == "verified"}  # noqa: E731
        before, after = served(self._loaded), served(self._values)
        added = [key for key in after if key not in before]
        removed = [key for key in before if key not in after]
        changed = [key for key in after if key in before and float(after[key]["value"]) != float(before[key]["value"])]
        renamed = [key for key in after if key in before and (after[key].get("kpi_label"), after[key].get("group_label"))
                   != (before[key].get("kpi_label"), before[key].get("group_label"))]
        if not (added or removed or changed or renamed):
            return {}
        return {"added": len(added), "removed": len(removed), "changed": len(changed), "renamed": len(renamed),
                "examples": [{"change": kind, "group": key[0], "kpi": key[1], "year": key[2], "period": key[3]}
                             for kind, keys in (("changed", changed), ("removed", removed), ("added", added), ("renamed", renamed))
                             for key in keys[:5]][:10]}

    def carry(self, carried: dict) -> None:
        """A finished filing moved to a new KPI list version, its readings kept."""
        self._send(filings=[carried])
        self._filings[carried["accession"]] = {**self._filings[carried["accession"]], "spec_version": carried["spec_version"]}

    def statements(self, accession: str, lines: list[dict]) -> None:
        self.control.call("store_statements", symbol=self.symbol, accession=accession, lines=lines)

    def spec(self, payload: dict) -> None:
        self.control.call("store", symbol=self.symbol, spec=payload)

    def curation(self, group_key: str, members_key: str, chosen: list[str]) -> None:
        self.control.call("store", symbol=self.symbol, filings=[], values=[], curations=[
            {"group_key": group_key, "members_key": members_key, "chosen": chosen}])

    def _in_window(self, value: dict) -> bool:
        """A figure for a period its KPI is reported for, or one set aside (the reason it is written at all)."""
        window = self.windows.get((value.get("group_key"), value.get("kpi_key")))
        if not window or value.get("validation_status") == "rejected" or not value.get("period_end"):
            return True
        since, until = window
        return KpiSpec.model_construct(since=since, until=until).reported_at(value["period_end"])

    def _send(self, *, filings: list[dict] | None = None, values: list | None = None) -> None:
        # Stored rows are read-only, so a step may hand one back as it is: it goes out as a plain record.
        values = list({value_key(value): dict(value) for value in values or []}.values())  # one row per key per write
        # The owner's mark is the control plane's to keep: it clears it itself when a row may change.
        values = [{key: item for key, item in value.items() if key != "owner_decision"} for value in values]
        # A note once: a check that settles a part each run, which the whole breakdown then holds back, added its note
        # again every run until JPM's reached the control plane's 40 and its run failed.
        values = [{**value, "notes": list(dict.fromkeys(value["notes"]))} if value.get("notes") else value for value in values]
        # A figure from outside SEC filings (Unity's segment spreadsheet on its investor site) was entered by the owner:
        # the extractor never rewrites it, and the control plane takes only SEC sources, so carrying one in a write
        # failed the whole run. A figure worked out from one keeps it out of its source.
        values = [value if _source_document(value) else {**value, "source_url": None}
                  for value in values if _source_document(value) or value.get("method") == "derived"]
        outside = [value for value in values if not self._in_window(value)]
        if outside:
            self.log(f"{self.symbol}: {len(outside)} readings outside their KPI's reported periods not kept")
            values = [value for value in values if self._in_window(value)]
        if not values and not filings:
            return
        self.control.call("store", symbol=self.symbol, filings=filings or [], values=values)
        for value in values:
            existing = self._values.get(value_key(value))
            if existing and _keeps_verified(existing, value):
                continue  # as store_business_kpis does: another filing's unchecked reading never replaces a verified one
            # As it does for what the owner decided. A re-read of a report once withdrew SpaceX's confirmed Q2 2025 here but
            # not there, so its year-ago quarter looked empty and came back beside them under the segment's old name.
            if (existing and existing.get("owner_decision")) or value_key(value) in self._owner_rejected:
                continue
            self._values[value_key(value)] = MappingProxyType(value)
        self.result.values += len(values)
