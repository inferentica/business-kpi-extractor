"""One company's stored figures and filings, and the one way to change them. Steps read them here, read-only, and hand
back the rows they want written: nothing else writes values or filings to the control plane."""
from __future__ import annotations

from decimal import Decimal

from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType

from .ai import KpiSpec
from .quality.stated import stated_in_filing
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
    # What a periodic report's figures are reported in (xbrl/basis.py); an earnings release takes its report's.
    accounting_standard: str | None = None
    currency: str | None = None


def failed_reading(ref: FilingRef, error: Exception, spec_version: int | None = None) -> FilingReading:
    return FilingReading(ref, "failed", spec_version=spec_version, notes=[f"{type(error).__name__}: {error}"[:300]], error=error)


class CompanyStore:
    def __init__(self, control, symbol: str, result, log=print, language: str = "en"):
        self.control = control
        self.symbol = symbol
        self.language = language  # the source's filing language: labels are kept as reported, in it
        self.result = result
        self.log = log
        self._values: dict[tuple, MappingProxyType] = {}
        self._filings: dict[str, dict] = {}
        self.owner_rejected: list[dict] = []
        # Each listed KPI's reported periods (group key, KPI key) -> (since, until): a figure outside them is not kept.
        self.windows: dict[tuple[str, str], tuple[str | None, str | None]] = {}
        self._loaded: dict[tuple, MappingProxyType] = {}
        # What the control plane holds for each row, as last loaded or sent; and, while held, the rows written since.
        self._remote: dict[tuple, MappingProxyType] = {}
        self._holding = False
        self._touched: set[tuple] = set()
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
        self._remote = dict(self._values)
        self._filings.clear()
        self._filings.update({filing["accession"]: filing for filing in filings})

    def write(self, rows: list) -> None:
        for start in range(0, len(rows), _BATCH):
            self._send(values=rows[start:start + _BATCH])

    def hold(self) -> None:
        """From here, written rows change the company's figures in memory only, until release(). Steps that each settle
        a part of the figures (one flags a breakdown, a later one stands it back) were each a stored change: every run
        left dated versions that lasted seconds (KKR 835 in a day). Readings recorded per filing are never held."""
        self._holding, self._touched = True, set()

    def release(self) -> None:
        """Sends each held row whose final state differs from what the control plane holds, once."""
        self._holding = False
        rows = [dict(self._values[key]) for key in sorted(self._touched)
                if key in self._values and _changed(self._values[key], self._remote.get(key))]
        self._touched = set()
        for start in range(0, len(rows), _BATCH):
            self.control.call("store", symbol=self.symbol, filings=[], values=rows[start:start + _BATCH])
        self._remote.update({value_key(row): MappingProxyType(row) for row in rows})
        self.result.values += len(rows)

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
            earlier = [value for value in self._values.values()
                       if value["method"] == method and value.get("source_accession") == ref.accession
                       and value["validation_status"] != "rejected"]
            records = _keep_groups(records, earlier)
            fresh = {value_key(record) for record in records}
            had = {value_key(value) for value in earlier}
            # Figures the new reading put under keys the earlier one did not have: a renamed line takes its old line's place.
            read_again = {(record["fiscal_year"], record["fiscal_period"], _rounded(record["value"])) for record in records
                          if value_key(record) not in had}
            records = records + [
                {**value, "validation_status": "rejected", "notes": ["replaced by a newer reading of this filing"]}
                for value in earlier
                if value_key(value) not in fresh and not self._still_stated(value, read_again)
                # A group not read this run keeps its rows (flagged, and so not served): they are why it is not read.
                and value["group_key"].removeprefix(AI_GROUP_PREFIX) not in (unread_groups or set())
            ]
        records = cell_copies(records, self.windows)
        filing = {
            "accession": ref.accession, "form": ref.form, "filed_at": ref.filed.isoformat(), "document_role": ref.role,
            "period_end": reading.period_end.isoformat() if reading.period_end else None, "fiscal_year": reading.fiscal_year,
            "fiscal_period": reading.fiscal_period, "status": reading.status, "spec_version": reading.spec_version,
            "value_count": sum(1 for record in records if record["validation_status"] != "rejected"), "attempts": min(_STORED_ATTEMPTS, previous.get("attempts", 0) + 1) if previous else 1,
            "notes": [note[:300] for note in reading.notes][:30], "source_url": ref.source_url,
            "accounting_standard": reading.accounting_standard, "currency": reading.currency, "language": self.language,
        }
        self._send(filings=[filing], values=records)
        self._filings[ref.accession] = filing
        if not reading.counted:
            return
        self.result.filings += 1
        self.result.needs_review += sum(1 for record in records if record["validation_status"] == "needs_review")
        self.log(f"{self.symbol}: {ref.form} {ref.filed} {reading.status} ({filing['value_count']} values)")

    def _still_stated(self, value, read_again: set[tuple]) -> bool:
        """A verified figure the new reading of its filing did not produce, kept: its filing states it where it was read,
        the new reading put that figure under no other key, and its KPI is still listed. A later reading missing a figure
        is no evidence against it (KKR's Q4 2019 capital invested, $9.8B, dropped by a re-read on 2026-11-09)."""
        if value["validation_status"] != "verified" or value.get("method") != "ai":
            return False
        if (value["fiscal_year"], value["fiscal_period"], _rounded(value["value"])) in read_again:
            return False  # read again under another key or group: that reading takes its place
        if self.windows and (value["group_key"], value["kpi_key"]) not in self.windows:
            return False  # its KPI left the list
        return stated_in_filing(value.get("unit") or "", float(value["value"]), value.get("locator"), value.get("notes"),
                                value.get("kpi_label"))

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

    def reread_disagreements(self) -> list[dict]:
        """Verified figures that a new reading of their own filing now gives differently, held for the owner with the
        figure served before in a note. The retrace of 2026-11-09 re-read 790 filings: it fixed old misreadings and made
        new ones (Salesforce's cRPO read as its total RPO, JPMorgan's client lines one row down) alike, and no check tells
        the two apart. The same filing read two ways is a question, not an answer."""
        rows = []
        for key, before in self._loaded.items():
            now = self._values.get(key)
            if (now is None or before["validation_status"] != "verified" or now["validation_status"] != "verified"
                    or before.get("owner_decision") or now.get("source_accession") != before.get("source_accession")):
                continue
            if rounding_of(now["value"], before["value"]):
                # The same figure rounded (the release's headline for its table's cell: KKR's uncalled commitments,
                # 107,592 then 108,000 million): the exact one is kept.
                rows.append(dict(before))
            elif not rounding_of(before["value"], now["value"]) and _differs(now["value"], before["value"]):
                rows.append({**now, "validation_status": "needs_review",
                             "notes": [*(now.get("notes") or []), f"{REREAD_NOTE} {_figure(before['value'])}"]})
        return rows

    def carry(self, carried: dict) -> None:
        """A finished filing moved to a new KPI list version, its readings kept."""
        self._send(filings=[carried])
        self._filings[carried["accession"]] = {**self._filings[carried["accession"]], "spec_version": carried["spec_version"]}

    def name_registrant(self, accession: str, name: str) -> None:
        """The registrant's name on a report's cover, kept with the filing as it is stored: Quotiva takes the company's
        legal name from its newest report's (owner, 2026-11-13). Sent only when new or changed."""
        previous = self._filings.get(accession)
        if previous is None or previous.get("registrant_name") == name:
            return
        self._send(filings=[{**previous, "registrant_name": name}])
        self._filings[accession] = {**previous, "registrant_name": name}

    def statements(self, accession: str, lines: list[dict]) -> None:
        self.control.call("store_statements", symbol=self.symbol, accession=accession, lines=lines)

    def spec(self, payload: dict) -> None:
        self.control.call("store", symbol=self.symbol, spec=payload)

    def rule_evidence(self, rows: list[dict]) -> list:
        """Learned rules' evidence from this company, and each rule's state after it (the service moves states)."""
        states: list = []
        for start in range(0, len(rows), 500):
            reply = self.control.call("store", symbol=self.symbol, filings=[], values=[], ruleEvidence=rows[start:start + 500])
            states = (reply or {}).get("rules") or states
        return states

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
        if self._holding and not filings:
            self._apply(values)
            self._touched.update(value_key(value) for value in values)
            return
        self.control.call("store", symbol=self.symbol, filings=filings or [], values=values)
        self._apply(values)
        self._remote.update({value_key(value): self._values[value_key(value)] for value in values
                             if value_key(value) in self._values})
        self.result.values += len(values)

    def _apply(self, values: list[dict]) -> None:
        """The rows as the control plane keeps them, in memory."""
        for value in values:
            existing = self._values.get(value_key(value))
            if existing and _keeps_verified(existing, value):
                continue  # as store_business_kpis does: another filing's unchecked reading never replaces a verified one
            # As it does for what the owner decided. A re-read of a report once withdrew SpaceX's confirmed Q2 2025 here but
            # not there, so its year-ago quarter looked empty and came back beside them under the segment's old name.
            if (existing and existing.get("owner_decision")) or value_key(value) in self._owner_rejected:
                continue
            self._values[value_key(value)] = MappingProxyType(value)


REREAD_NOTE = "a new reading of this filing differs from the figure served before:"


def _rounded(value) -> float:
    return round(float(value), 6)


def _keep_groups(records: list[dict], earlier: list[dict]) -> list[dict]:
    """A breakdown read again keeps the group its earlier reading of the filing used. The KPI list may name a breakdown
    anew (Apple's products became kpi_revenue_by_product_type), and a re-read under the new name moved its lines out of
    their series. A group new to the filing's readings takes an earlier one when the earlier one is not in the new
    reading, its lines share their keys, and every shared line reads the same figure. Groups both readings have stay."""
    read_now = {record["group_key"] for record in records}
    new_groups = read_now - {value["group_key"] for value in earlier}
    before: dict[str, dict[tuple, float]] = {}
    template: dict[str, dict] = {}
    for value in earlier:
        if value["group_key"] in read_now or value.get("group_kind") != "revenue_breakdown":
            continue
        before.setdefault(value["group_key"], {})[(value["kpi_key"], value["fiscal_year"], value["fiscal_period"])] = \
            _rounded(value["value"])
        template.setdefault(value["group_key"], value)
    moves: dict[str, str] = {}
    for group in new_groups:
        rows = {(r["kpi_key"], r["fiscal_year"], r["fiscal_period"]): _rounded(r["value"]) for r in records
                if r["group_key"] == group and r.get("group_kind") == "revenue_breakdown"}
        if not rows:
            continue
        matches = [old for old, lines in before.items()
                   if (shared := set(rows) & set(lines)) and 2 * len(shared) >= len(rows)
                   and all(rows[key] == lines[key] for key in shared)]
        if len(matches) == 1 and matches[0] not in moves.values():
            moves[group] = matches[0]
    if not moves:
        return records
    kept = []
    for record in records:
        old = moves.get(record["group_key"])
        if old is None:
            kept.append(record)
            continue
        model = template[old]
        kept.append({**record, "group_key": old, "group_label": model["group_label"], "group_order": model.get("group_order"),
                     "group_kind": model["group_kind"]})
    return kept


def _significant(value) -> int:
    digits = Decimal(repr(float(value))).normalize().as_tuple().digits
    return max(1, len(digits))


def rounding_of(rough, exact) -> bool:
    """Whether one figure is another rounded to fewer significant digits (5 for 5.25; 108,000 million for 107,592)."""
    rough, exact = float(rough), float(exact)
    places = _significant(rough)
    return rough != exact and places < _significant(exact) and float(f"{exact:.{places}g}") == rough


def _differs(value, before) -> bool:
    """Apart by more than rounding (half a percent)."""
    value, before = float(value), float(before)
    return abs(value - before) > 0.005 * max(abs(value), abs(before))


def _figure(value) -> str:
    value = float(value)
    return f"{value:,.0f}" if value.is_integer() else f"{value:,}"


# What a stored row is: a held row equal to the control plane's in these is not sent again.
_STORED_FIELDS = ("value", "validation_status", "method", "unit", "currency", "notes", "locator", "source_accession",
                  "source_url", "period_end", "group_label", "group_kind", "group_order", "kpi_label", "kpi_order",
                  "reconciliation_error_pct")


def _changed(row, stored) -> bool:
    if stored is None:
        return True
    for field in _STORED_FIELDS:
        mine, theirs = row.get(field), stored.get(field)
        if field in ("value", "reconciliation_error_pct") and mine is not None and theirs is not None:
            if float(mine) != float(theirs):
                return True
        elif (mine or None) != (theirs or None):
            return True
    return False


def cell_copies(records: list[dict], listed: object) -> list[dict]:
    """A row a reading found that its list does not have, read from the very cell one of the list's KPIs of its group was
    read from in the same period, is that KPI's copy, not a line of its own: set aside, so it is never served beside it
    (UnitedHealthcare's "UnitedHealthcare Employer & Individual" row, stored again beside the list's Domestic)."""
    if not listed:
        return records
    def cell(record: dict) -> tuple | None:
        locator = record.get("locator") or {}
        if not isinstance(locator, dict) or locator.get("table") is None or locator.get("row") is None or locator.get("col") is None:
            return None
        return (record["group_key"], record["fiscal_year"], record["fiscal_period"], str(locator["table"]), int(locator["row"]), int(locator["col"]))
    owners = {cell(record): record for record in records if record["validation_status"] != "rejected"
              and (record["group_key"], record["kpi_key"]) in listed and cell(record) is not None}
    out = []
    for record in records:
        owner = owners.get(cell(record)) if (record["group_key"], record["kpi_key"]) not in listed else None
        if owner is not None and record["validation_status"] != "rejected" and float(owner["value"]) == float(record["value"]):
            record = {**record, "validation_status": "rejected",
                      "notes": [*(record.get("notes") or []), f"the same cell as {owner.get('kpi_label') or owner['kpi_key']}"]}
        out.append(record)
    return out
