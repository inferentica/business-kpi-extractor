"""Earnings documents in turn: which are read, by how many readers at once, and how their readings are stored."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

from .. import ai
from ..ai import Spec
from ..document import Document, clean
from ..checks import check_period, date_order_findings, validate_groups
from ..extract import ReadValue, explanation_supports, falls, forward_looking, net_gross_mismatch
from ..fiscal import fiscal_label
from ..sec import FilingRef
from ..xbrl import member_key
from ..store import FilingReading, failed_reading
from .shared import (AI_GROUP_ORDER, AI_GROUP_PREFIX, _DONE, _MAX_ATTEMPTS, _NOT_REPORTED, _READ_BY_AI, _annual_header, _chain_of, _document_text, _is_jump, value_key)

_READ_BY_REPLAY = "read by replay"
# Stamped on every earnings release read. A release an older reader left with a tracked group unread is read again
# when this changes (the newest quarter's locators are replayed first, so a fixed reader seldom needs the AI): bump it
# with a release-reader fix, e.g. ASML's slide statements, which once left 2025's quarters unread. Releases read before
# stamping began are never re-read by it, so a bump cannot re-read every company's history.
_RELEASE_READER = "release reader 5"
# Stamped on a release read without some of the list's groups (see _readable): read again once they are read once more.
_GROUPS_NOT_READ = "groups not read: "
# Why the AI found no value for each unreported KPI (Locate's reason codes), and what sent a reading to a second reader.
_MISSING_WHY = "missing reasons: "
_SECOND_READ = "second read: "
_RELEASE_READER_PREFIX = "release reader "
# Releases a company's run reads at once, after its newest (see _process_releases).
RELEASE_READERS = 4
# A group read this many quarters without one verified period is not read again (see _readable).
DEAD_GROUP_QUARTERS = 8

def _annual_columns(read: list[ReadValue]) -> bool:
    """Whether the breakdown was read from a column its header calls a year ("Twelve Months Ended", "Year Ended")."""
    headers = [str(item.locator.get("header") or "") for item in read if item.group.kind == "revenue_breakdown"]
    return bool(headers) and all(_annual_header(header) for header in headers)


def _unreported(spec: Spec, read: list[ReadValue]) -> list[str]:
    """The listed KPIs a document did not report, apart from breakdowns read as whole tables (their rows vary)."""
    whole = {item.group.key for item in read if item.locator.get("whole_table")}
    found = {f"{item.group.key}.{item.kpi.key}" for item in read}
    return [f"{group.key}.{kpi.key}" for group in spec.groups if group.key not in whole for kpi in group.kpis
            if kpi.key != group.total_kpi and f"{group.key}.{kpi.key}" not in found]


def stored_reading_fails(value: dict, read_by: int = 0) -> bool:
    """Whether a served AI reading is one reader 3 rejects or reads differently: a fall stored as a rise ("decreased by
    16%" served as 16), or a net figure under a gross KPI. Its release is read again once, stamped or not."""
    if value.get("method") != "ai" or value.get("validation_status") != "verified" or value.get("owner_decision"):
        return False
    # Each rule re-reads only releases read before the reader that brought it: reader 3 (signs, net and gross),
    # reader 5 (forecasts, explanations).
    if read_by < 5 and _reader5_rejects(value):
        return True
    if read_by >= 3:
        return False
    locator = value.get("locator") or {}
    quote, value_text = clean(str(locator.get("quote") or "")), clean(str(locator.get("value_text") or ""))
    if value.get("unit") == "percent" and float(value.get("value") or 0) > 0 and quote and value_text and falls(quote, value_text):
        return True
    label = str(value.get("kpi_label") or "")
    if quote and value_text and net_gross_mismatch(quote[:quote.find(value_text)][-80:], label):
        return True
    return bool(locator.get("row_label")) and net_gross_mismatch(str(locator["row_label"]), label) is not None


def _reader5_rejects(value: dict) -> bool:
    """A forecast or guidance read as the figure, or a sharp change cleared by a sentence that does not explain it."""
    locator = value.get("locator") or {}
    quote, value_text = clean(str(locator.get("quote") or "")), clean(str(locator.get("value_text") or ""))
    if quote and value_text and value_text in quote and forward_looking(quote[:quote.find(value_text)]):
        return True
    explained = next((note.removeprefix("change explained: ") for note in value.get("notes") or [] if note.startswith("change explained: ")), None)
    return explained is not None and not explanation_supports(clean(explained), str(value.get("kpi_label") or ""), float(value.get("value") or 0))


def release_reread_due(previous: dict | None) -> bool:
    """Whether a finished release should be read again, once, by the reader that fixes what an older one missed:
    * reader 1 reads every tracked group: a release an earlier stamped reader left with a group unread (one read
      before stamping began is left alone);
    * reader 2 reads a quote from a table laid out as text (Meta's highlight bullets): a release where a reading was
      dropped as "unknown text block", whatever came of it."""
    if not previous or previous.get("status") not in _DONE:
        return False
    notes = previous.get("notes") or []
    stamp = next((note for note in notes if note.startswith(_RELEASE_READER_PREFIX)), None)
    version = int(stamp.removeprefix(_RELEASE_READER_PREFIX)) if stamp and stamp.removeprefix(_RELEASE_READER_PREFIX).isdigit() else None
    if (version is None or version < 2) and any("unknown text block" in note for note in notes):
        return True
    return (previous.get("status") == "processed" and version is not None and version < 1
            and any(note.startswith(_NOT_REPORTED) for note in notes))



class ReleaseFlow:
    """Earnings documents in turn: which are read, by how many readers at once, and how their readings are stored."""

    def _read_release(self, ref: FilingRef, spec: Spec, version: int) -> FilingReading | None:
        previous = self.filings.get(ref.accession)
        if not self.force and previous and previous.get("spec_version") == version and (
                previous["status"] in _DONE or previous.get("attempts", 0) >= _MAX_ATTEMPTS) and not release_reread_due(previous) \
                and not self._reader3_due(previous, ref.accession) and not self._skipped_groups_due(previous, ref.accession, spec):
            return None
        if not spec.groups:
            return FilingReading(ref, "skipped", spec_version=version, notes=["no KPIs tracked for this company"])
        listed = spec
        if ref.period_end != self._newest_release:
            spec = self._readable(spec)  # the newest quarter always reads every group: a fixed bug lets it verify
        skipped = [group.key for group in listed.groups if group.key not in {kept.key for kept in spec.groups}]
        try:
            expected = ref.period_end
            fiscal_year, fiscal_period = fiscal_label(expected, self.profile.fiscal_year_end, year_offset=self.offset)
            document = self._document(ref)
            period_hint = f"quarter ended about {expected} (fiscal {fiscal_period} {fiscal_year})"

            def text(subset: Spec | None = None) -> str:  # built only when the AI reads: a replayed quarter costs none
                return ai.prompt(self.company, self._prompt_document(document, spec), ai.LOCATE_TASK,
                                 ai.locate_data(period_hint, subset or spec, self._hints(expected)))

            read, problems, located = self._read(text, document, spec, expected, version)
            self._keep_listed_series(read)
            how = _READ_BY_REPLAY if getattr(self._local, "replayed", False) else _READ_BY_AI
            period_problem = check_period(located, expected)
            period_end = expected if period_problem else date.fromisoformat(located.period_end[:10])
            # A document counts as annual only on evidence: its breakdown adds up to the XBRL full year, or its
            # column says so. A total far above the quarter before is only a reason to look again.
            if not located.annual and (self._reports_full_year(read, period_end) or _annual_columns(read)):
                located.annual = True
                problems.append("breakdown reports the full year: stored as the fiscal year")
            elif not located.annual and fiscal_period == "Q4" and self._far_above_quarters(read, period_end):
                for item in read:
                    if item.group.kind == "revenue_breakdown":
                        item.status = "needs_review"
                        item.notes.append("total far above the quarter before: check the period")
            if located.annual:
                # An annual report: its values are the full year, and Q4 is derived from them.
                fiscal_year, fiscal_period = fiscal_label(period_end, self.profile.fiscal_year_end, annual=True,
                                                          year_offset=self.offset)
            validate_groups(read, self._previous(period_end, annual=located.annual))
            self._flag_partial_tables(read, period_end, document)
            self._explain_jumps(read, document, spec)
            records = self._release_records(read, spec, ref, document, fiscal_year, fiscal_period, period_end, period_problem)
            verified = sum(1 for record in records if record["validation_status"] == "verified")
            status = "processed" if verified else "needs_review" if records else "skipped"
            unreported = _unreported(spec, read)
            reasons = {key: located.missing_reasons.get(key, "unstated") for key in unreported} if how == _READ_BY_AI else {}
            second = getattr(self._local, "second_read", None) if how == _READ_BY_AI else None
            notes = [how, _RELEASE_READER, *([_NOT_REPORTED + ", ".join(unreported)] if unreported else []),
                     *([_MISSING_WHY + ", ".join(f"{key}={code}" for key, code in reasons.items())] if reasons else []),
                     *([_SECOND_READ + second] if second else []),
                     *([_GROUPS_NOT_READ + ", ".join(skipped)] if skipped else []), *problems[:20]]
            return FilingReading(ref, status, records=records, spec_version=version, period_end=period_end,
                                 fiscal_year=fiscal_year, fiscal_period=fiscal_period, notes=notes)
        except Exception as error:  # noqa: BLE001
            return failed_reading(ref, error, version)

    def _reader3_due(self, previous: dict, accession: str) -> bool:
        """A release read before reader 3 that holds a served reading reader 3 would reject (see stored_reading_fails)."""
        stamp = next((note for note in previous.get("notes") or [] if note.startswith(_RELEASE_READER_PREFIX)), "")
        read_by = int(stamp.removeprefix(_RELEASE_READER_PREFIX)) if stamp.removeprefix(_RELEASE_READER_PREFIX).isdigit() else 0
        return any(value.get("source_accession") == accession and stored_reading_fails(value, read_by) for value in self.values.values())

    def _keep_listed_series(self, read: list[ReadValue]) -> None:
        """A row a whole-table reading labels by its own name that the group already holds, under that row label, as a
        listed KPI, is that KPI: JPMorgan's 2016 "Middle Market Banking" was stored as Commercial & Specialized Industries,
        and a re-read without this split it into a series of its own (and replaced 34 served figures). Only a label that
        points to one listed KPI of the group counts."""
        listed: dict[str, dict[str, object]] = {}
        for item in read:
            listed.setdefault(item.group.key, {kpi.key: kpi for kpi in item.group.kpis})
        aliases: dict[tuple[str, str], set[str]] = {}
        for value in self.values.values():
            group = value["group_key"].removeprefix(AI_GROUP_PREFIX)
            label = (value.get("locator") or {}).get("row_label")
            if value["method"] != "ai" or group not in listed or not label or value["kpi_key"] not in listed[group]:
                continue
            aliases.setdefault((group, member_key(str(label))), set()).add(value["kpi_key"])
        for item in read:
            if item.is_total or item.kpi.key in listed[item.group.key] or not item.locator.get("row_label"):
                continue
            keys = aliases.get((item.group.key, member_key(str(item.locator["row_label"]))), set())
            if len(keys) == 1:
                item.kpi = listed[item.group.key][next(iter(keys))]

    def _skipped_groups_due(self, previous: dict, accession: str, spec: Spec) -> bool:
        """A release read while a group of today's list was not read (see _readable) and that group is read again now:
        JPMorgan's Revenue by Product went unread for 2016 to 2020 after a list fix, and the owner typed 216 figures in.
        A release stamped before reader 4 has no record of what it left out, so a listed group with nothing stored from it
        and not named as unreported counts as left out. Read again once: reader 4 stamps it."""
        notes = previous.get("notes") or []
        dead = self._dead or set()
        live = {group.key for group in spec.groups if group.key not in dead}
        marked = next((note for note in notes if note.startswith(_GROUPS_NOT_READ)), None)
        if marked is not None:
            return bool(live & set(marked.removeprefix(_GROUPS_NOT_READ).split(", ")))
        stamp = next((note for note in notes if note.startswith(_RELEASE_READER_PREFIX)), "")
        version = stamp.removeprefix(_RELEASE_READER_PREFIX)
        if not version.isdigit() or int(version) >= 4:
            return False  # read before stamping began (left alone, as for other re-reads), or by reader 4 already
        named = " ".join(notes)
        held = {value["group_key"].removeprefix(AI_GROUP_PREFIX) for value in self.values.values()
                if value.get("source_accession") == accession}
        return any(group not in held and f"{group}." not in named for group in live)

    def _read_and_record_release(self, ref: FilingRef, spec: Spec, version: int) -> bool:
        reading = self._read_release(ref, spec, version)
        if reading is None:
            return False
        self.store.record_or_fail(reading, self._dead)
        return True

    def _readable(self, spec: Spec) -> Spec:
        """The list without groups that have never verified in at least eight quarters read (JPMorgan's income-statement
        lines listed as a breakdown): reading them again would only flag them again, at a reasoning read each quarter. Decided
        once per run (see _process_releases), before re-reading a filing could withdraw their history. Used for older
        quarters only: the newest is read whole every run, so a group that failed for an extractor bug verifies again
        once the bug is fixed, and is read in full from then on."""
        dead = self._dead if self._dead is not None else self._never_verified()
        if not dead or all(group.key in dead for group in spec.groups):
            return spec
        return Spec.model_validate({"groups": [g.model_dump() for g in spec.groups if g.key not in dead],
                                    "names": spec.names})

    def _never_verified(self) -> set[str]:
        periods: dict[str, set[tuple[str, str]]] = {}
        verified: set[str] = set()
        for v in self.values.values():
            if v["method"] == "ai" and v["validation_status"] != "rejected" and v["fiscal_period"] != "FY":
                group = v["group_key"].removeprefix(AI_GROUP_PREFIX)
                periods.setdefault(group, set()).add((v["fiscal_year"], v["fiscal_period"]))
                if v["validation_status"] == "verified":
                    verified.add(group)
        return {group for group, seen in periods.items() if len(seen) >= DEAD_GROUP_QUARTERS and group not in verified}

    def _waiting(self, wait):
        """Lets other readers work while this one waits on the AI or a download, when it holds the state lock."""
        if not getattr(self._local, "holding", False):
            return wait()
        self._state.release()
        try:
            return wait()
        finally:
            self._state.acquire()

    def _process_releases(self, refs: list[FilingRef], spec: Spec, version: int) -> list[FilingRef]:
        """Reads releases in four chains, one per fiscal quarter (every Q1 oldest to newest, every Q2, ...), the chains
        side by side. Each read then has the same quarter a year earlier already read in this run: the reference for
        where each KPI sits, and the year-ago figure its column must match. Readings stored by an earlier run for a
        filing this run is still to re-read are never a reference. Afterwards the quarters just read are checked against
        each other in date order, as reading them one by one would have."""
        before = {key for key, value in self.values.items() if value["method"] == "ai"}
        self._dead = self._never_verified()
        pending = [ref for ref in refs if self.force or self._pending(ref) or
                   (self.filings.get(ref.accession) or {}).get("spec_version") != version]
        done: set[str] = set()

        def read(ref: FilingRef) -> None:
            if self._read_and_record_release(ref, spec, version):
                done.add(ref.accession)

        def chain(quarter_refs: list[FilingRef]) -> None:
            for ref in quarter_refs:
                with self._state:
                    self._local.holding = True
                    self._local.chained = True  # history lookups see this chain's own earlier quarters only
                    try:
                        read(ref)
                    finally:
                        self._local.holding = False
                        self._local.chained = False

        if len(pending) <= 2:
            for ref in refs:
                read(ref)
        else:
            chains: dict[int, list[FilingRef]] = {}
            for ref in sorted(refs, key=lambda ref: ref.period_end or date.min):
                chains.setdefault(_chain_of(ref.period_end or date.min), []).append(ref)
            with ThreadPoolExecutor(max_workers=RELEASE_READERS) as pool:
                list(pool.map(chain, chains.values()))
            flagged, jumps = self._check_read_series({key for key, value in self.values.items() if value["method"] == "ai"} - before
                                                     | {key for key in before if self.values[key].get("source_accession") in done})
            self.store.write(flagged)
            self.store.write(self._explain_stored(jumps, refs))
        return [ref for ref in refs if ref.accession in done]

    def _check_read_series(self, keys: set[tuple]) -> tuple[list[dict], set[tuple]]:
        """The jump checks of quarters read side by side, run again in date order: each value against the quarter before
        it (a share against its last share, an amount against three times or a third of its last), and an amount equal
        to its own figure a year earlier (the tell of a prior-year column). A breakdown or mix is flagged whole. Gives the
        rows to flag and the keys of those that jumped."""
        flagged = date_order_findings(self.values.values(), keys, value_key)
        groups = {(k[0], k[2], k[3]) for k in flagged if self.values[k]["group_kind"] != "metric"}
        changed = []
        for key, value in self.values.items():
            group_period = (key[0], key[2], key[3])
            if key in flagged or (group_period in groups and value["method"] == "ai" and value["validation_status"] == "verified"):
                note = flagged.get(key, "another part of this breakdown needs review")
                changed.append({**value, "validation_status": "needs_review", "notes": [*(value.get("notes") or []), note]})
        return changed, {key for key, note in flagged.items() if _is_jump(note)}

    def _explain_stored(self, keys: set[tuple], refs: list[FilingRef]) -> list[dict]:
        """Jumps the date-order check found, given the reading-time treatment: the reasoning reader is asked whether each is a real
        change, and one backed by a quote found in the document stands (its breakdown's other parts are released by
        _release_held_breakdowns). One reasoning call per release with such a jump."""
        by_accession = {ref.accession: ref for ref in refs}
        jumps: dict[str, list[dict]] = {}
        for key in keys:
            value = self.values.get(key)
            if value and value.get("source_accession") in by_accession:
                jumps.setdefault(value["source_accession"], []).append(value)
        def explain(item: tuple[str, list[dict]]):
            accession, rows = item
            try:
                document = self._document(by_accession[accession])
                lines = "\n".join(
                    f"- {v['group_key'].removeprefix(AI_GROUP_PREFIX)}.{v['kpi_key']} ({v['group_label']} / {v['kpi_label']}): "
                    f"now {float(v['value']):g}; {'; '.join(n for n in v.get('notes') or [] if _is_jump(n))}" for v in rows)
                answer = self._ask(ai.prompt(self.company, self._prompt_document(document, None), ai.EXPLAIN_TASK, lines),
                                   "explain", ai.Explanations, thinking_first=True, model="flash")
                return rows, document, answer
            except Exception:  # noqa: BLE001 - unexplained, they stay flagged
                return rows, None, None

        cleared = []
        with ThreadPoolExecutor(max_workers=RELEASE_READERS) as pool:  # one reasoning call per release, side by side
            answers = list(pool.map(explain, jumps.items()))
        for rows, document, answer in answers:
            if answer is None:
                continue
            haystack = _document_text(document)
            for v in rows:
                verdict = answer.items.get(f"{v['group_key'].removeprefix(AI_GROUP_PREFIX)}.{v['kpi_key']}")
                quote = clean(verdict.quote or "") if verdict else ""
                if verdict and verdict.legitimate and quote in haystack and explanation_supports(quote, v["kpi_label"], float(v["value"])):
                    call = self._call_of(answer)
                    cleared.append({**v, "validation_status": "verified",
                                    "notes": [n for n in v.get("notes") or [] if not _is_jump(n)] + [f"change explained: {quote[:200]}"],
                                    **({"locator": {**(v.get("locator") or {}), "explain_call": call}} if call else {})})
        return cleared

    def _release_records(self, read: list[ReadValue], spec: Spec, ref: FilingRef, document: Document, fiscal_year: str,
                         fiscal_period: str, period_end: date, period_problem: str | None) -> list[dict]:
        # A breakdown's total row is its reconciliation target, not a segment of its own.
        def is_total(item: ReadValue) -> bool:
            return item.is_total or item.kpi.key == item.group.total_kpi

        totals = {item.group.key: item.value for item in read if item.group.kind == "revenue_breakdown" and is_total(item)}
        parts_sum: dict[str, float] = {}
        for item in read:
            if item.group.key in totals and not is_total(item):
                parts_sum[item.group.key] = parts_sum.get(item.group.key, 0.0) + item.value
        # A breakdown or mix is published whole or not at all: one flagged part flags them all, so the last complete
        # reading stays in place (the store never lets an unchecked row replace a verified one).
        open_groups = {item.group.key for item in read if item.group.kind != "metric" and not is_total(item)
                       and item.status != "verified"}
        records = []
        for item in read:
            if is_total(item):
                continue
            notes, status = list(item.notes), item.status
            if item.group.key in open_groups and status == "verified":
                status, notes = "needs_review", [*notes, "another part of this breakdown needs review"]
            if period_problem:
                status, notes = "needs_review", [*notes, period_problem]
            total = totals.get(item.group.key)
            records.append(self._record(
                group_key=AI_GROUP_PREFIX + item.group.key, group_label=item.group.label,
                group_kind=item.group.kind, group_order=AI_GROUP_ORDER + spec.groups.index(item.group),
                kpi_key=item.kpi.key, kpi_label=item.kpi.label,
                kpi_order=item.group.kpis.index(item.kpi) if item.kpi in item.group.kpis else 100 + int(item.locator.get("row", 0)),
                unit=item.kpi.unit, value=item.value, currency=item.currency, method="ai",
                validation_status=status, reconciliation_error_pct=(parts_sum[item.group.key] - total) / total if total else None,
                notes=notes, ref=ref, fiscal_year=fiscal_year, fiscal_period=fiscal_period, period_end=period_end,
                locator={**item.locator, "total": total} if total else item.locator,
                source_url=document.url_of(item.locator.get("table") or item.locator.get("block")),
            ))
        return records
