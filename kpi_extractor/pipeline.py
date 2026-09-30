"""One company end to end: XBRL breakdowns, the KPI list, earnings documents, the list's upkeep, then derived periods.

AI decides and points; code reads and proves. Every AI answer is checked before it changes anything stored:
- Flash reads each earnings document. When its reading matches where each KPI was found last quarter and every check
  passes, it stands. Otherwise Pro reads the document independently and values stand only where the two agree or
  where Pro's review picks one of the two readings.
- Pro picks the rows of an XBRL breakdown the rules cannot reconcile; the rows must add up to reported revenue.
- Pro explains a flagged jump; the explanation counts only with a quote found in the document.
- Pro audits each new quarter against the KPI list and may add KPIs or retire ones no longer reported.
"""
from __future__ import annotations

import copy
import re
import string
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import ai
from .ai import AiResponseError, Located, Spec
from .control import ControlError
from .derive import derive_periods
from .document import Document, clean, parse_document
from .extract import ReadValue, _listed_kpi, check_period, describe, locator_hint, read_values, validate_groups
from .fiscal import fiscal_label, learn_year_offset
from .replay import replay
from .sec import FilingRef, classification_excerpt, company_profile, earnings_releases, exhibit_html, periodic_reports
from .xbrl import XbrlCandidate, drop_overlaps, extract_breakdowns, official_labels, remove_subtotals

# About 30k tokens: enough for any earnings release, and for the revenue sections of a full financial report.
MAX_DOCUMENT_CHARS = 120_000
AI_GROUP_PREFIX = "kpi_"
AI_GROUP_ORDER = 20
_DONE = {"processed", "needs_review", "skipped"}
_MAX_ATTEMPTS = 3
_NOT_EARNINGS = "classified as not an earnings document"
_AI_PENDING = "AI review unavailable; retried next run"
_READ_BY_AI = "read by AI"
MAX_YEAR_RATIO = 3.0
_READ_BY_REPLAY = "read by replay"
_NOT_REPORTED = "not reported: "


@dataclass
class SymbolResult:
    symbol: str
    filings: int = 0
    values: int = 0
    needs_review: int = 0
    ai_calls: int = 0
    pro_reads: int = 0
    flash_only_reads: int = 0
    replayed_reads: int = 0
    # Breakdown years with a full year and three quarters but no fourth: surfaced in the run summary, never silent.
    q4_gaps: list[str] = field(default_factory=list)
    # Values read from earnings documents that are not rejected, and how many of them are verified (shown to users).
    release_values: int = 0
    release_verified: int = 0
    errors: list[str] = field(default_factory=list)


def value_key(value: dict) -> tuple[str, str, str, str]:
    return value["group_key"], value["kpi_key"], value["fiscal_year"], value["fiscal_period"]


class SymbolPipeline:
    def __init__(self, control, symbol: str, quarters: int, force: bool, today: date | None = None, log=print):
        self.control = control
        self.symbol = symbol
        self.force = force
        self.since = (today or date.today()) - timedelta(days=92 * quarters + 120)
        self.log = log
        self.result = SymbolResult(symbol)
        self.filings: dict[str, dict] = {}
        self.values: dict[tuple, dict] = {}
        self.names: dict = {}
        self._xbrl_cache: dict[str, Future] = {}
        self._exhibits: dict[str, Future] = {}
        # Downloads and XBRL parsing start ahead of the filing being worked on.
        self._prefetch = ThreadPoolExecutor(max_workers=3)
        self._documents: dict[str, Document] = {}
        self._prompt_documents: dict[str, str] = {}
        self._curations: dict[tuple, list[str]] = {}
        self._replayed = False

    @property
    def company(self) -> str:
        return f"{self.profile.name} ({self.symbol})"

    def run(self) -> SymbolResult:
        try:
            return self._run()
        finally:
            self._prefetch.shutdown(wait=False, cancel_futures=True)

    def _run(self) -> SymbolResult:
        state = self.control.call("symbol_state", symbol=self.symbol)
        self.filings = {filing["accession"]: filing for filing in state.get("filings") or []}
        self.values = {value_key(value): value for value in state.get("values") or []}
        self.names = (state.get("spec") or {}).get("names") or {}
        self.profile, company = company_profile(self.symbol)
        reports = sorted(periodic_reports(company, self.since), key=lambda ref: ref.filed)
        for ref in reversed(reports):
            if self.force or self._pending(ref):
                self._xbrl_cache[ref.accession] = self._prefetch.submit(ref.filing.xbrl)
        self.offset = self._year_offset(reports)
        for ref in reports:
            self._process_report(ref)
        releases = [ref for ref in sorted(earnings_releases(company, self.profile, self.since), key=lambda ref: ref.filed)
                    if self._is_earnings_document(ref)]
        for ref in releases:
            if self.force or self._pending(ref):
                for exhibit in ref.exhibits[:6]:
                    self._exhibit(exhibit)
        spec, version, proposed = self._spec(state.get("spec"), releases)
        if spec is not None:
            processed = [ref for ref in releases if self._process_release(ref, spec, version)]
            latest_end = releases[-1].period_end if releases else None
            if not proposed and any(ref.period_end == latest_end for ref in processed):
                previous_spec = spec
                updated = self._maintain(spec, version, [ref for ref in releases if ref.period_end == latest_end])
                if updated is not None:
                    spec, version = updated
                    if only_adds(previous_spec, spec):
                        # New KPIs are looked for in the last four quarters; older filings keep their readings and
                        # simply move to the new list version (a full re-read took TSMC 38 of 63 minutes).
                        cutoff = latest_end - timedelta(days=370)
                        for ref in releases:
                            if ref.period_end >= cutoff:
                                self._process_release(ref, spec, version)
                            else:
                                self._carry_to_version(ref, version)
                    else:
                        for ref in releases:
                            self._process_release(ref, spec, version)
        if spec is not None:
            self._adopt_listed_keys(spec)
        self._refile_annuals()
        self._recheck_flagged()
        self._unify_series()
        self._derive()
        self._harmonize_labels()
        self.result.q4_gaps = self._q4_gaps()
        live = [v for v in self.values.values() if v["method"] == "ai" and v["validation_status"] != "rejected"]
        self.result.release_values = len(live)
        self.result.release_verified = sum(1 for v in live if v["validation_status"] == "verified")
        if len(live) >= 5 and self.result.release_verified < 0.5 * len(live):
            self.log(f"{self.symbol}: only {self.result.release_verified} of {len(live)} release values are verified")
        if self.result.q4_gaps:
            self.log(f"{self.symbol}: no Q4 for {', '.join(self.result.q4_gaps[:10])}")
        return self.result

    # Periodic reports: official XBRL breakdowns, with rows chosen by the AI where the rules fall short.

    def _pending(self, ref: FilingRef) -> bool:
        """Whether a filing still has to be read (a spec change can still re-read a finished release)."""
        previous = self.filings.get(ref.accession)
        return not previous or (previous["status"] not in _DONE and previous.get("attempts", 0) < _MAX_ATTEMPTS)

    def _xbrl(self, ref: FilingRef):
        if ref.accession not in self._xbrl_cache:
            self._xbrl_cache[ref.accession] = self._prefetch.submit(ref.filing.xbrl)
        return self._xbrl_cache[ref.accession].result()

    def _exhibit(self, exhibit) -> Future:
        if exhibit.url not in self._exhibits:
            self._exhibits[exhibit.url] = self._prefetch.submit(exhibit_html, exhibit)
        return self._exhibits[exhibit.url]

    def _year_offset(self, reports: list[FilingRef]) -> int:
        for ref in reversed(reports):
            try:
                xbrl = self._xbrl(ref)
            except Exception:  # noqa: BLE001 - an unreadable report just cannot vote
                continue
            if xbrl is None:
                continue
            entity = xbrl.entity_info or {}
            period_end = entity.get("document_period_end_date")
            if period_end and entity.get("fiscal_year"):
                return learn_year_offset(
                    [(date.fromisoformat(str(period_end)[:10]), str(entity["fiscal_year"]), False)],
                    self.profile.fiscal_year_end,
                )
        return 0

    def _process_report(self, ref: FilingRef) -> None:
        previous = self.filings.get(ref.accession)
        if not self.force and previous and (previous["status"] in _DONE or previous.get("attempts", 0) >= _MAX_ATTEMPTS):
            return
        try:
            xbrl = self._xbrl(ref)
            if xbrl is None:
                self._store_filing(ref, "skipped", notes=["no XBRL"])
                return
            breakdowns = extract_breakdowns(xbrl.facts.to_dataframe(), xbrl.entity_info or {}, ref.form,
                                            official_labels(getattr(xbrl, "element_catalog", None)))
            if breakdowns is None:
                self._store_filing(ref, "skipped", notes=["no document period"])
                return
            fiscal_year, fiscal_period = fiscal_label(breakdowns.period_end, self.profile.fiscal_year_end,
                                                      annual=breakdowns.annual, year_offset=self.offset)
            groups = [(group, []) for group in breakdowns.groups]
            taken = {group.key for group in breakdowns.groups}
            ai_pending = False
            for candidate in breakdowns.rejected:
                if candidate.key in taken:
                    continue
                try:
                    group = self._curate(candidate)
                except ControlError:
                    # The AI is unavailable (e.g. out of credit): keep every reconciled breakdown now and let the
                    # next run retry the review, instead of losing the whole filing.
                    ai_pending = True
                    continue
                if group is not None:
                    groups.append((group, ["rows chosen by review"]))
                    taken.add(group.key)
            records = []
            for group, extra_notes in groups:
                named = self.names.get(group.key) or {}
                # Rows that land on one key ("Other" and "All other") are parts of it: they add up.
                # Their nine months to date add up the same way, or Q4 = year − nine months would be wrong.
                merged: dict[str, tuple[str, str, float, float | None]] = {}
                for reported, label, value in group.members:
                    member = reported
                    ytd = group.year_to_date.get(reported)
                    if member in merged:
                        first, first_label, total, ytd_total = merged[member]
                        merged[member] = (first, first_label, total + value,
                                          None if ytd is None or ytd_total is None else ytd_total + ytd)
                    else:
                        merged[member] = (reported, label, value, ytd)
                for order, (member, (reported, label, value, ytd)) in enumerate(merged.items()):
                    records.append(self._record(
                        group_key=group.key, group_label=named.get("label") or group.label, group_kind="revenue_breakdown",
                        group_order=group.order, kpi_key=member, kpi_label=(named.get("members") or {}).get(member) or label,
                        kpi_order=order, unit="currency", value=value, currency=group.currency, method="xbrl",
                        validation_status="verified", reconciliation_error_pct=group.reconciliation_error,
                        notes=extra_notes, ref=ref, fiscal_year=fiscal_year, fiscal_period=fiscal_period,
                        period_end=breakdowns.period_end, locator={
                            "concept": group.concept, "total": group.total, "member": group.elements.get(reported),
                            "prior": group.prior.get(reported),
                            **({"ytd": ytd, "ytd_total": group.year_to_date_total}
                               if ytd is not None and group.year_to_date_total else {}),
                        },
                    ))
            for group, _notes in groups:
                records += self._restated_prior(group, breakdowns.annual, breakdowns.period_end, ref)
            status = "failed" if ai_pending else "processed" if records else "skipped"
            notes = [_AI_PENDING] if ai_pending else [] if records else ["no revenue breakdown reconciles to reported revenue"]
            self._store_filing(ref, status, records=records, period_end=breakdowns.period_end,
                               fiscal_year=fiscal_year, fiscal_period=fiscal_period, notes=notes)
        except Exception as error:  # noqa: BLE001 - one bad filing must not stop the company
            self._fail_filing(ref, error)

    def _restated_prior(self, group, annual: bool, period_end: date, ref: FilingRef) -> list[dict]:
        """The year-ago period as this filing restates it, when the company has since recast the breakdown (Microsoft's
        FY2023 10-K split out Dynamics; its FY2024 10-Qs restate the FY2023 quarters that way). The year and its
        quarters then share one basis and Q4 can be derived. A period whose stored figures already match is untouched;
        restated rows must add up to that period's reported revenue."""
        target = (period_end - timedelta(days=365)).isoformat()
        stored = [v for v in self.values.values() if v["method"] == "xbrl" and v["group_key"] == group.key
                  and v["validation_status"] == "verified" and (v["fiscal_period"] == "FY") == annual
                  and abs(_days(v["period_end"], target)) <= 12]
        if not stored or not group.prior:
            return []
        total = float((stored[0].get("locator") or {}).get("total") or 0)
        restated = drop_overlaps(remove_subtotals(dict(group.prior), total), total) if total else {}
        if len(restated) < 2 or abs(sum(restated.values()) - total) > abs(total) * 0.001:
            return []
        if sorted(round(float(v["value"])) for v in stored) == sorted(round(value) for value in restated.values()):
            return []  # already on the current basis
        base = stored[0]
        rows = [{**base, "kpi_key": key, "kpi_label": dict((k, l) for k, l, _v in group.members).get(key, key),
                 "kpi_order": order, "value": value, "source_accession": ref.accession, "source_url": ref.source_url,
                 "filed_at": ref.filed.isoformat(), "validation_status": "verified", "reconciliation_error_pct": 0,
                 "notes": [f"restated in the {ref.form} filed {ref.filed}"],
                 "locator": {"concept": group.concept, "total": total, "member": group.elements.get(key)}}
                for order, (key, value) in enumerate(sorted(restated.items(), key=lambda item: -item[1]))]
        rows += [{**v, "validation_status": "rejected", "notes": [f"restated in the {ref.form} filed {ref.filed}"]}
                 for v in stored if v["kpi_key"] not in restated]
        return rows

    def _curate(self, candidate: XbrlCandidate):
        """Pro picks the rows that make up revenue; the same rows are reused for later filings with the same rows."""
        cache_key = (candidate.key, frozenset(candidate.members))
        if cache_key in self._curations:
            return candidate.group(self._curations[cache_key])
        rows = "\n".join(f"- {key}: {label} = {value:,.0f}" for key, (label, value) in candidate.members.items())
        data = (f"Breakdown: {candidate.label} ({candidate.concept})\nReported total revenue: {candidate.total:,.0f}\n"
                f"Rows (member key: label = amount):\n{rows}")
        try:
            answer = self._ask(ai.prompt(self.company, "(XBRL facts, listed below)", ai.CURATE_TASK, data),
                               "curate", ai.Curation, thinking_first=True, model="pro")
        except AiResponseError:
            return None
        self._curations[cache_key] = answer.members
        return candidate.group(answer.members)

    # Which furnished filings are earnings documents.

    def _is_earnings_document(self, ref: FilingRef) -> bool:
        if ref.confirmed:
            return True
        previous = self.filings.get(ref.accession)
        if previous:
            return _NOT_EARNINGS not in (previous.get("notes") or [])
        try:
            answer = self._ask(ai.prompt(self.company, classification_excerpt(ref), ai.CLASSIFY_TASK),
                               "classify", ai.Classification, thinking_first=False, model="flash")
        except (AiResponseError, ControlError):
            return False  # nothing is recorded, so the next run classifies it again
        if answer.kind == "other":
            self._store_filing(ref, "skipped", notes=[_NOT_EARNINGS])
            return False
        return True

    # The KPI list: proposed once, then audited every new quarter.

    def _spec(self, row: dict | None, releases: list[FilingRef]) -> tuple[Spec | None, int, bool]:
        if row and not self.force:
            stored = Spec.model_validate({"groups": row["groups"], "names": row.get("names") or {}})
            spec, version = ai.normalize_spec(stored), int(row["version"])
            if spec.model_dump() != stored.model_dump():
                # A stored list that breaks the rules is repaired once; its filings are then read with the new version.
                version += 1
                self._store_spec(spec, row.get("names") or {}, version, row.get("source_accession") or "")
                self.log(f"{self.symbol}: KPI list v{version}: breakdowns that cannot reconcile became metrics")
            return spec, version, False
        if not releases:
            return None, 0, False
        latest = releases[-1]
        latest_quarter = [ref for ref in releases if ref.period_end == latest.period_end]
        breakdowns = self._xbrl_breakdowns()
        # Foreign issuers tag XBRL only once a year, so their quarterly breakdowns must still come from releases.
        covered = [] if self.profile.foreign else [
            f"{group['label']}: {', '.join(group['members'].values())}" for group in breakdowns.values()
        ]
        to_name = [
            f"- {key}: {group['label']} (" + "; ".join(f"{member} = {label}" for member, label in group["members"].items()) + ")"
            for key, group in breakdowns.items()
        ]
        try:
            document = self._document(*latest_quarter)
            text = self._prompt_document(document, None)
            # Pro, reasoning first: the KPI list and names shape every later quarter.
            current = Spec.model_validate({"groups": row["groups"]}) if row else None
            spec = self._ask(ai.prompt(self.company, text, ai.PROPOSE_TASK, ai.propose_data(covered, to_name, current)),
                             "propose", Spec, thinking_first=True, model="pro")
        except Exception as error:  # noqa: BLE001
            self.result.errors.append(f"KPI list: {error}"[:300])
            return None, 0, False
        if current is not None:
            spec = keep_current_groups(spec, current)
        spec = ai.normalize_spec(spec)
        # Names may only rename breakdowns and members that exist.
        names = {
            key: {"label": name.label, "members": {m: l for m, l in name.members.items() if m in breakdowns[key]["members"]}}
            for key, name in spec.names.items() if key in breakdowns
        }
        version = (int(row["version"]) if row else 0) + 1
        self._store_spec(spec, names, version, latest.accession)
        self.names = names
        self.log(f"{self.symbol}: KPI list v{version} with {sum(len(g.kpis) for g in spec.groups)} KPIs")
        return spec, version, True

    def _maintain(self, spec: Spec, version: int, latest_quarter: list[FilingRef]) -> tuple[Spec, int] | None:
        """Pro audits the newest quarter against the list: KPIs the documents report that the list lacks, and KPIs the
        company stopped reporting. Additions only extend the list; existing keys never change meaning."""
        document = self._document(*latest_quarter)
        period_end = max(ref.period_end for ref in latest_quarter).isoformat()
        captured = sorted(
            f"- {v['group_key'].removeprefix(AI_GROUP_PREFIX)}.{v['kpi_key']} ({v['group_label']} / {v['kpi_label']}) = {v['value']}"
            for v in self.values.values() if v["method"] == "ai" and abs(_days(v["period_end"], period_end)) <= 12
        )
        data = f"KPI list:\n{ai.kpi_lines(spec)}\n\nCaptured for this quarter:\n" + ("\n".join(captured) or "- nothing")
        try:
            answer = self._ask(ai.prompt(self.company, self._prompt_document(document, spec), ai.MAINTAIN_TASK, data),
                               "maintain", ai.Maintenance, thinking_first=True, model="pro")
            updated = apply_maintenance(spec, answer)
        except (AiResponseError, ControlError, ValueError) as error:
            self.result.errors.append(f"KPI list audit: {error}"[:300])
            return None
        if updated is None:
            return None
        self._store_spec(updated, self.names, version + 1, latest_quarter[-1].accession)
        self.log(f"{self.symbol}: KPI list v{version + 1} after audit ({sum(len(g.kpis) for g in updated.groups)} KPIs)")
        return updated, version + 1

    def _store_spec(self, spec: Spec, names: dict, version: int, accession: str) -> None:
        self.control.call("store", symbol=self.symbol, spec={
            "version": version, "groups": [group.model_dump() for group in spec.groups], "names": names,
            "sourceAccession": accession,
        })

    def _xbrl_breakdowns(self) -> dict[str, dict]:
        """Each XBRL breakdown's latest title and members (key → label)."""
        latest: dict[str, str] = {}
        for value in self.values.values():
            if value["method"] == "xbrl" and value["period_end"] >= latest.get(value["group_key"], ""):
                latest[value["group_key"]] = value["period_end"]
        breakdowns: dict[str, dict] = {}
        for value in sorted(self.values.values(), key=lambda item: item["kpi_order"]):
            if value["method"] == "xbrl" and value["period_end"] == latest.get(value["group_key"]):
                group = breakdowns.setdefault(value["group_key"], {"label": value["group_label"], "members": {}})
                group["members"][value["kpi_key"]] = value["kpi_label"]
        return breakdowns

    def _harmonize_labels(self) -> None:
        """One name per XBRL series across every period: the company's approved name, else its latest label."""
        latest: dict[tuple[str, str], dict] = {}
        for value in self.values.values():
            if value["group_key"].startswith(AI_GROUP_PREFIX):
                continue
            key = (value["group_key"], value["kpi_key"])
            if value["period_end"] >= latest.get(key, {}).get("period_end", ""):
                latest[key] = value
        group_latest: dict[str, dict] = {}
        for (group_key, _kpi), value in latest.items():
            if value["period_end"] >= group_latest.get(group_key, {}).get("period_end", ""):
                group_latest[group_key] = value
        renamed = []
        for value in self.values.values():
            if value["group_key"].startswith(AI_GROUP_PREFIX):
                continue
            named = self.names.get(value["group_key"]) or {}
            label = named.get("label") or group_latest[value["group_key"]]["group_label"]
            member = ((named.get("members") or {}).get(value["kpi_key"])
                      or latest[(value["group_key"], value["kpi_key"])]["kpi_label"])
            if (label, member) != (value["group_label"], value["kpi_label"]):
                renamed.append({**value, "group_label": label, "kpi_label": member})
        for start in range(0, len(renamed), 400):
            self._store(values=renamed[start:start + 400])

    # Earnings documents: KPIs the AI points to.

    def _process_release(self, ref: FilingRef, spec: Spec, version: int) -> bool:
        previous = self.filings.get(ref.accession)
        if not self.force and previous and previous.get("spec_version") == version and (
                previous["status"] in _DONE or previous.get("attempts", 0) >= _MAX_ATTEMPTS):
            return False
        if not spec.groups:
            self._store_filing(ref, "skipped", spec_version=version, notes=["no KPIs tracked for this company"])
            return True
        try:
            expected = ref.period_end
            fiscal_year, fiscal_period = fiscal_label(expected, self.profile.fiscal_year_end, year_offset=self.offset)
            document = self._document(ref)
            period_hint = f"quarter ended about {expected} (fiscal {fiscal_period} {fiscal_year})"

            def text() -> str:  # built only when the AI reads, so a replayed quarter costs no selection call
                return ai.prompt(self.company, self._prompt_document(document, spec), ai.LOCATE_TASK,
                                 ai.locate_data(period_hint, spec, self._hints(expected)))

            read, problems, located = self._read(text, document, spec, expected, version)
            how = _READ_BY_REPLAY if self._replayed else _READ_BY_AI
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
            self._explain_jumps(read, document, spec)
            records = self._release_records(read, spec, ref, document, fiscal_year, fiscal_period, period_end, period_problem)
            verified = sum(1 for record in records if record["validation_status"] == "verified")
            status = "processed" if verified else "needs_review" if records else "skipped"
            unreported = _unreported(spec, read)
            notes = [how, *([_NOT_REPORTED + ", ".join(unreported)] if unreported else []), *problems[:20]]
            self._store_filing(ref, status, records=records, spec_version=version, period_end=period_end,
                               fiscal_year=fiscal_year, fiscal_period=fiscal_period, notes=notes)
        except Exception as error:  # noqa: BLE001
            self._fail_filing(ref, error, spec_version=version)
        return True

    def _carry_to_version(self, ref: FilingRef, version: int) -> None:
        filing = self.filings.get(ref.accession)
        if not filing or filing.get("status") not in _DONE:
            return
        carried = {"accession": ref.accession, "spec_version": version, "status": filing["status"],
                   "attempts": filing.get("attempts", 1), "value_count": filing.get("value_count", 0),
                   "notes": filing.get("notes") or []}
        self._store(filings=[carried])
        self.filings[ref.accession] = {**filing, "spec_version": version}

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
        records = []
        for item in read:
            if is_total(item):
                continue
            notes, status = list(item.notes), item.status
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
                locator={**item.locator, "total": total} if total else item.locator, source_url=document.source_url,
            ))
        return records

    def _read(self, text, document: Document, spec: Spec, expected: date, version: int | None = None):
        """Code first: last quarter's rows and sentences read again, standing only on the checks a Flash reading must
        pass. Then Flash. Its reading stands alone only when every KPI was found where it was found last quarter, no
        value repeats an earlier period's number (the tell of a prior-year column) and every check passes; otherwise
        Pro reads the document independently, values stand where the two agree, and Pro reviews the rest."""
        currency = self._default_currency()
        self._replayed = False
        replayed = self._replay(document, spec, expected, version, currency)
        if replayed is not None:
            self._replayed = True
            self.result.replayed_reads += 1
            return replayed[0], [], replayed[1]
        text = text() if callable(text) else text
        flash = self._ask(text, "locate", Located, thinking_first=False, model="flash")
        flash_read, flash_problems = read_values(document, spec, flash, currency)
        invalid = [problem for problem in flash_problems if _INVALID_POINTER.search(problem)]
        if invalid:
            # One retry with the reader told which pointers failed (e.g. a text block cited as a table).
            retry_text = text + "\n\nYour previous answer had pointers that do not hold:\n" + "\n".join(
                f"- {_explain_pointer(problem, document)}" for problem in invalid[:20])
            flash = self._ask(retry_text, "locate", Located, thinking_first=False, model="flash")
            flash_read, flash_problems = read_values(document, spec, flash, currency)
        if self._flash_is_enough(flash, flash_read, spec, expected, document) or (
                not flash.tables and not flash.values and self._kind_reports_nothing(document, expected, version)):
            self.result.flash_only_reads += 1
            return flash_read, [f"flash {problem}" for problem in flash_problems], flash
        self.result.pro_reads += 1
        pro = self._ask(text, "locate", Located, thinking_first=False, model="pro")
        pro_read, pro_problems = read_values(document, spec, pro, currency)
        by_flash = {f"{item.group.key}.{item.kpi.key}": item for item in flash_read}
        by_pro = {f"{item.group.key}.{item.kpi.key}": item for item in pro_read}
        agreed: list[ReadValue] = []
        disputed: dict[str, tuple[ReadValue | None, ReadValue | None]] = {}
        for key in [*by_flash, *[key for key in by_pro if key not in by_flash]]:
            a, b = by_flash.get(key), by_pro.get(key)
            if a and b and _same(a.value, b.value):
                agreed.append(a)
            else:
                disputed[key] = (a, b)
        problems = [f"flash {problem}" for problem in flash_problems] + [f"pro {problem}" for problem in pro_problems]
        if disputed:
            lines = [f"- {key}: A = {describe(document, a) if a else 'not reported'}; "
                     f"B = {describe(document, b) if b else 'not reported'}" for key, (a, b) in disputed.items()]
            review = self._ask(ai.prompt(self.company, self._prompt_document(document, spec), ai.REVIEW_TASK,
                                         "Disputed KPIs:\n" + "\n".join(lines)),
                               "review", ai.Review, thinking_first=True, model="pro")
            for key, (a, b) in disputed.items():
                chosen = {"A": a, "B": b}.get(review.choices.get(key, "none"))
                if chosen is None:
                    problems.append(f"{key}: readings disagree and review kept neither")
                    continue
                chosen.notes.append("confirmed by review")
                agreed.append(chosen)
        located = flash if flash.period_end else pro
        return agreed, problems, located

    def _replay(self, document: Document, spec: Spec, expected: date, version: int | None, currency: str | None):
        """Last quarter's reading repeated by code, when the same KPI list read it. KPIs that list has but last quarter
        did not report are looked for by the AI at least every other quarter, so a newly reported KPI is not missed."""
        if version is None:
            return None
        source = _source_template(document.source_url)
        last = self._last_locators(expected, source)
        if not last:
            return None
        accession = next((value.get("source_accession") for key, value in self._ai_history(expected, source).items()
                          if key in last), None)
        filing = self.filings.get(accession or "") or {}
        if filing.get("spec_version") != version:
            return None
        whole = {key.split(".", 1)[0] for key, locator in last.items() if locator.get("whole_table")}
        missing = {f"{group.key}.{kpi.key}" for group in spec.groups if group.key not in whole for kpi in group.kpis
                   if kpi.key != group.total_kpi} - last.keys()
        if missing:
            notes = filing.get("notes") or []
            reported = next((note for note in notes if note.startswith(_NOT_REPORTED)), _NOT_REPORTED)
            if _READ_BY_AI not in notes or not missing <= set(reported.removeprefix(_NOT_REPORTED).split(", ")):
                return None
        located = replay(document, last, expected)
        if located is None:
            return None
        read, problems = read_values(document, spec, located, currency)
        if problems or not self._flash_is_enough(located, read, spec, expected, document) or self._far_above_quarters(read, expected):
            return None
        return read, located

    def _kind_reports_nothing(self, document: Document, expected: date, version: int | None) -> bool:
        """Whether both models already read this kind of document for this KPI list and found nothing in it (TSMC's
        earnings release once its amounts come from the quarterly report). Flash pointing at nothing again then stands;
        a pointer that does not hold (a partial table) still goes to Pro."""
        if version is None:
            return False
        source = _source_template(document.source_url)
        return any(filing.get("spec_version") == version and filing["status"] == "skipped"
                   and _READ_BY_AI in (filing.get("notes") or []) and filing.get("value_count", 0) == 0
                   and _source_template(filing.get("source_url") or "") == source
                   and (filing.get("period_end") or "9999") < expected.isoformat()
                   for filing in self.filings.values())

    def _flash_is_enough(self, located: Located, read: list[ReadValue], spec: Spec, expected: date,
                         document: Document) -> bool:
        if check_period(located, expected) or located.annual or self._reports_full_year(read, expected):
            return False
        last = self._last_locators(expected, _source_template(document.source_url))
        if not last:
            return False  # a company's first quarters, and a new kind of document, are always read twice
        keys = {f"{item.group.key}.{item.kpi.key}" for item in read}
        if any(key not in keys for key in last):
            return False  # something found last quarter is missing now
        earlier = self._earlier_values(expected)
        for item in read:
            key = f"{item.group.key}.{item.kpi.key}"
            if item.is_total or item.kpi.key == item.group.total_kpi:
                continue  # totals are not stored; the parts must still add up to them
            if key not in last or not _same_place(item.locator, last[key]):
                return False
            if item.kpi.unit in ("currency", "count") and any(_same(item.value, value) for value in earlier.get(key, [])):
                return False  # the same number as an earlier period: likely a prior-period column
        trial = copy.deepcopy(read)
        validate_groups(trial, self._previous(expected))
        return all(item.status == "verified" for item in trial)

    def _explain_jumps(self, read: list[ReadValue], document: Document, spec: Spec) -> None:
        """Values flagged only for a sharp change get a reading by Pro; a real change backed by a quote stands."""
        flagged = [item for item in read if item.status == "needs_review"
                   and item.notes and all(_is_jump(note) for note in item.notes if note != "confirmed by review")]
        if not flagged:
            return
        lines = "\n".join(f"- {item.group.key}.{item.kpi.key} ({item.group.label} / {item.kpi.label}): now {item.value:g}"
                          f"; {'; '.join(item.notes)}" for item in flagged)
        try:
            answer = self._ask(ai.prompt(self.company, self._prompt_document(document, spec), ai.EXPLAIN_TASK, lines),
                               "explain", ai.Explanations, thinking_first=True, model="pro")
        except (AiResponseError, ControlError):
            return  # the flagged values stay needs_review
        haystack = _document_text(document)
        for item in flagged:
            verdict = answer.items.get(f"{item.group.key}.{item.kpi.key}")
            quote = clean(verdict.quote or "") if verdict else ""
            if verdict and verdict.legitimate and len(quote) >= 20 and quote in haystack:
                item.status = "verified"
                item.notes = [*item.notes, f"change explained: {quote[:200]}"]

    # Documents.

    def _ask(self, text: str, purpose: str, schema, thinking_first: bool, model: str = "flash"):
        last_error = None
        for thinking in (thinking_first, True):
            answer = self.control.ai(self.symbol, purpose, ai.SYSTEM, text, thinking, model)
            self.result.ai_calls += 1
            if not answer:
                raise AiResponseError("AI is unavailable")
            try:
                return ai.parse(answer, schema)
            except AiResponseError as error:
                last_error = error
        raise last_error

    def _prompt_document(self, document: Document, spec: Spec | None) -> str:
        """The document as the AI reads it: whole when it fits, else the sections Flash picks from its outline."""
        key = str(id(document))
        if key in self._prompt_documents:
            return self._prompt_documents[key]
        if document.fits(MAX_DOCUMENT_CHARS):
            text = document.render()
        else:
            wanted = ai.kpi_lines(spec) if spec else "(no list yet: revenue breakdowns and operating metrics)"
            try:
                answer = self._ask(ai.prompt(self.company, document.outline(), ai.SELECT_TASK, f"KPIs:\n{wanted}"),
                                   "select", ai.Selection, thinking_first=False, model="flash")
                ids = {item for item in answer.ids if item in document.tables or item in document.blocks}
            except AiResponseError:
                ids = set()
            text = document.render_ids(ids, MAX_DOCUMENT_CHARS) if ids else document.render_for_prompt(MAX_DOCUMENT_CHARS)
        self._prompt_documents[key] = text
        return text

    def _document(self, *refs: FilingRef) -> Document:
        """The exhibits of one or more filings as one document; each exhibit's ids carry their own letter prefix."""
        key = "+".join(ref.accession for ref in refs)
        if key not in self._documents:
            combined: Document | None = None
            exhibits = [exhibit for ref in refs for exhibit in ref.exhibits[:6]][:26]
            for index, exhibit in enumerate(exhibits):
                raw = self._exhibit(exhibit).result()
                if raw is None:
                    continue
                part = parse_document(raw, exhibit.url, prefix=f"{string.ascii_uppercase[index]}_")
                if combined is None:
                    combined = part
                else:
                    combined.extend(part)
            if combined is None:
                raise ValueError("no readable exhibit")
            self._documents[key] = combined
        return self._documents[key]

    # History used by the checks and hints.

    def _ai_history(self, before: date, source: str | None = None) -> dict[str, dict]:
        """The latest AI value of each KPI before a date (optionally only from one kind of document)."""
        latest: dict[str, dict] = {}
        for value in self.values.values():
            if value["method"] != "ai" or value["period_end"] >= before.isoformat():
                continue
            if source is not None and _source_template(value.get("source_url") or "") != source:
                continue
            key = f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}"
            if value["period_end"] > latest.get(key, {}).get("period_end", ""):
                latest[key] = value
        return latest

    def _hints(self, before: date) -> dict[str, str]:
        return {key: hint for key, value in self._ai_history(before).items() if (hint := locator_hint(value.get("locator")))}

    def _last_locators(self, before: date, source: str) -> dict[str, dict]:
        """Where each KPI was found the previous quarter in the same kind of document (TSMC's earnings release and its
        financial report carry different KPIs, and are compared only with their own kind)."""
        history = {key: value for key, value in self._ai_history(before, source).items()}
        if not history:
            return {}
        newest = max(value["period_end"] for value in history.values())
        return {key: value["locator"] for key, value in history.items()
                if value["period_end"] == newest and value.get("locator") and value["validation_status"] == "verified"}

    def _far_above_quarters(self, read: list[ReadValue], period_end: date) -> bool:
        """Whether a breakdown's total is several times the quarter just before it (an annual report read as a
        quarter). Only the adjacent quarter counts: a fast grower's Q4 is far above its quarters of years ago."""
        for item in read:
            if item.group.kind != "revenue_breakdown" or not (item.is_total or item.kpi.key == item.group.total_kpi):
                continue
            before = self._quarter_total_before(AI_GROUP_PREFIX + item.group.key, period_end.isoformat())
            if before and item.value >= 2.5 * before:
                return True
        return False

    def _quarter_total_before(self, group_key: str, period_end: str) -> float | None:
        """The reported total of the group's latest quarter within 120 days before period_end."""
        candidates = [v for v in self.values.values()
                      if v["group_key"] == group_key and v["fiscal_period"] in ("Q1", "Q2", "Q3", "Q4")
                      and v["validation_status"] != "rejected" and (v.get("locator") or {}).get("total")
                      and 0 < _days(period_end, v["period_end"]) <= 120]
        latest = max(candidates, key=lambda v: v["period_end"], default=None)
        return float(latest["locator"]["total"]) if latest else None

    def _refile_annuals(self) -> None:
        """Repairs full-year figures stored as Q4 before these checks existed: every AI value from that filing moves
        to FY, and the Q4 rows are rejected so Q4 is derived as FY − Q1 − Q2 − Q3."""
        annual_totals = {v["period_end"]: float(v["locator"]["total"]) for v in self.values.values()
                         if v["method"] == "xbrl" and v["fiscal_period"] == "FY" and (v.get("locator") or {}).get("total")}
        misfiled = set()
        for v in self.values.values():
            total = (v.get("locator") or {}).get("total")
            if v["method"] != "ai" or v["fiscal_period"] != "Q4" or v["validation_status"] == "rejected" or not total:
                continue
            total = float(total)
            annual = next((a for end, a in annual_totals.items() if abs(_days(end, v["period_end"])) <= 12), None)
            header = str((v.get("locator") or {}).get("header") or "")
            if (annual and abs(total - annual) <= abs(annual) * 0.01) or _ANNUAL_HEADER.search(header):
                misfiled.add((v["source_accession"], v["fiscal_year"]))
        if not misfiled:
            return
        moved = []
        for v in list(self.values.values()):
            if v["method"] == "ai" and v["fiscal_period"] == "Q4" and (v["source_accession"], v["fiscal_year"]) in misfiled:
                # A jump flagged while the year was mistaken for a quarter says nothing about the year.
                notes = [note for note in (v.get("notes") or []) if not _is_jump(note)]
                status = "verified" if v["validation_status"] == "needs_review" and not notes else v["validation_status"]
                moved.append({**v, "fiscal_period": "FY", "validation_status": status,
                              "notes": [*notes, "full-year figures, re-filed from Q4"]})
                moved.append({**v, "validation_status": "rejected", "notes": ["full-year figures filed as Q4; see FY"]})
        for start in range(0, len(moved), 400):
            self._store(values=moved[start:start + 400])
        self.log(f"{self.symbol}: re-filed {len(misfiled)} annual report(s) stored as Q4")

    def _adopt_listed_keys(self, spec: Spec) -> None:
        """Stored rows of a table read whole move to the key of the listed KPI they are ("3-nanometer" → nm3), the key a
        row-by-row reading uses, so one quarter never holds a figure twice and years line up for Q4. No AI involved."""
        groups = {AI_GROUP_PREFIX + group.key: group for group in spec.groups}
        moved = []
        for v in list(self.values.values()):
            group = groups.get(v["group_key"])
            if group is None or v["method"] != "ai" or v["validation_status"] == "rejected":
                continue
            kpi = _listed_kpi(group, v["kpi_label"]) or _listed_kpi(group, v["kpi_key"])
            if kpi is None or kpi.key == v["kpi_key"]:
                continue
            target = self.values.get((v["group_key"], kpi.key, v["fiscal_year"], v["fiscal_period"]))
            if target and target["validation_status"] == "verified" and v["validation_status"] != "verified":
                moved.append({**v, "validation_status": "rejected", "notes": [f"same figure as {kpi.key}"]})
                continue
            moved.append({**v, "kpi_key": kpi.key, "kpi_label": kpi.label})
            moved.append({**v, "validation_status": "rejected", "notes": [f"continued as {kpi.key}"]})
        for start in range(0, len(moved), 400):
            self._store(values=moved[start:start + 400])

    def _unify_series(self) -> None:
        """One key per business across a company's whole XBRL history, so Q4 = year − quarters always lines up.
        Two kinds of evidence link keys: the same XBRL element (a label change: "Dynamics" became "Dynamics products and
        cloud services"), and a member whose restated prior-year figure equals, to the unit, what another key reported
        for that year (a new element: Microsoft's "Xbox" restates FY2025 "Gaming"). Microsoft moved Search between two
        elements and back, so neither alone suffices; together they link the whole history. A linked set that ever has
        two of its keys in one period is two businesses after all and is left alone. The series takes its latest key,
        so it carries the latest name."""
        rows = [v for v in self.values.values() if v["method"] == "xbrl" and v["validation_status"] != "rejected"]
        parent: dict[tuple[str, str], tuple[str, str]] = {}

        def find(node):
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(a, b):
            parent[find(a)] = find(b)

        by_element: dict[tuple[str, str], tuple[str, str]] = {}
        index: dict[tuple[str, bool], list[dict]] = {}
        for v in rows:
            node = (v["group_key"], v["kpi_key"])
            find(node)
            element = (v.get("locator") or {}).get("member")
            if element:
                if (v["group_key"], element) in by_element:
                    union(node, by_element[(v["group_key"], element)])
                else:
                    by_element[(v["group_key"], element)] = node
            index.setdefault((v["group_key"], v["fiscal_period"] == "FY"), []).append(v)
        for v in rows:
            prior = (v.get("locator") or {}).get("prior")
            if not prior:
                continue
            target = (date.fromisoformat(v["period_end"][:10]) - timedelta(days=365)).isoformat()
            matches = {(o["group_key"], o["kpi_key"]) for o in index.get((v["group_key"], v["fiscal_period"] == "FY"), [])
                       if abs(_days(o["period_end"], target)) <= 12 and _same(float(o["value"]), float(prior))}
            if len(matches) == 1:
                union((v["group_key"], v["kpi_key"]), matches.pop())
        components: dict[tuple[str, str], list[dict]] = {}
        for v in rows:
            components.setdefault(find((v["group_key"], v["kpi_key"])), []).append(v)
        rekeyed = []
        for members in components.values():
            keys = {v["kpi_key"] for v in members}
            if len(keys) < 2:
                continue
            periods = [(v["fiscal_year"], v["fiscal_period"]) for v in members]
            if len(periods) != len(set(periods)):
                continue  # two of its keys in one period: different businesses after all
            canonical = max(members, key=lambda v: (v["period_end"], v["fiscal_period"] == "FY"))["kpi_key"]
            for v in members:
                if v["kpi_key"] != canonical:
                    rekeyed.append({**v, "kpi_key": canonical})
                    rekeyed.append({**v, "validation_status": "rejected", "notes": [f"continued as {canonical}"]})
        for start in range(0, len(rekeyed), 400):
            self._store(values=rekeyed[start:start + 400])
        if rekeyed:
            self.log(f"{self.symbol}: {len(rekeyed) // 2} XBRL values joined to their series")

    def _q4_gaps(self) -> list[str]:
        periods: dict[tuple[str, str], set[str]] = {}
        for v in self.values.values():
            if v["group_kind"] == "revenue_breakdown" and v["validation_status"] == "verified":
                periods.setdefault((v["group_label"], v["fiscal_year"]), set()).add(v["fiscal_period"])
        return sorted(f"{label} {year}" for (label, year), found in periods.items()
                      if {"FY", "Q1", "Q2", "Q3"} <= found and "Q4" not in found)

    def _recheck_flagged(self) -> None:
        """Clears flags that stored figures themselves disprove, without reading anything again:
        - a full year flagged for jumping against a quarter is compared with the year before instead;
        - a table read whole without its total row reconciles to a figure the same filing reports for the same period
          (TSMC's geography to its net revenue, its nodes to wafer revenue), to within 0.001%."""
        cleared = []
        by_filing: dict[tuple[str, str], list[dict]] = {}
        for v in self.values.values():
            if v["method"] == "ai" and v["validation_status"] != "rejected":
                by_filing.setdefault((v.get("source_accession") or "", v["period_end"]), []).append(v)
        for v in self.values.values():
            if v["method"] != "ai" or v["validation_status"] != "needs_review" or v["fiscal_period"] != "FY":
                continue
            notes = v.get("notes") or []
            if notes and all(_is_jump(note) and note.endswith("in a quarter") for note in notes):
                key = f"{v['group_key'].removeprefix(AI_GROUP_PREFIX)}.{v['kpi_key']}"
                before = self._previous(date.fromisoformat(v["period_end"][:10]), annual=True).get(key)
                siblings = by_filing.get((v.get("source_accession") or "", v["period_end"]), [])
                parts = sum(float(o["value"]) for o in siblings if o["group_key"] == v["group_key"])
                total = float((v.get("locator") or {}).get("total") or 0)
                if total and abs(parts - total) <= abs(total) * 0.001:
                    # A new node ramping 3.5× in a year is growth; the year's table adding up exactly is the check.
                    cleared.append({**v, "validation_status": "verified", "notes": ["the year's breakdown reconciles"]})
                elif before is None or 1 / MAX_YEAR_RATIO <= float(v["value"]) / before <= MAX_YEAR_RATIO:
                    cleared.append({**v, "validation_status": "verified", "notes": ["checked against the prior year"]})
        for (accession, _end), rows in by_filing.items():
            for group_key in {v["group_key"] for v in rows if v["group_kind"] == "revenue_breakdown"}:
                members = [m for m in rows if m["group_key"] == group_key]
                total = sum(float(m["value"]) for m in members)
                for m in members:
                    notes = m.get("notes") or []
                    if (m["validation_status"] == "needs_review" and notes and all(_is_jump(note) for note in notes)
                            and total and abs(float(m["value"])) < 0.01 * abs(total)):
                        cleared.append({**m, "validation_status": "verified", "notes": ["immaterial row; the total checks it"]})
            groups: dict[str, list[dict]] = {}
            for v in rows:
                groups.setdefault(v["group_key"], []).append(v)
            for group_key, members in groups.items():
                if not all(m["validation_status"] == "needs_review" and (m.get("locator") or {}).get("whole_table")
                           and (m.get("notes") or []) == ["no total revenue to reconcile against"] for m in members):
                    continue
                parts = sum(float(m["value"]) for m in members)
                anchors = [float(o["value"]) for o in rows if o["group_key"] != group_key and o["validation_status"] == "verified"]
                anchors += [float((o.get("locator") or {}).get("total")) for o in rows
                            if o["group_key"] != group_key and o["validation_status"] == "verified" and (o.get("locator") or {}).get("total")]
                if len(members) >= 2 and any(abs(parts - anchor) <= abs(anchor) * 1e-5 for anchor in anchors):
                    cleared += [{**m, "validation_status": "verified", "locator": {**m["locator"], "total": parts},
                                 "reconciliation_error_pct": 0, "notes": ["reconciled to another figure in the same report"]}
                                for m in members]
        for start in range(0, len(cleared), 400):
            self._store(values=cleared[start:start + 400])

    def _reports_full_year(self, read: list[ReadValue], period_end: date) -> bool:
        """Whether a breakdown in this document adds up to the fiscal year's revenue in XBRL (an annual report the
        AI took for a quarter), whatever the AI said."""
        annual_totals = [
            float(value["locator"]["total"]) for value in self.values.values()
            if value["method"] == "xbrl" and value["fiscal_period"] == "FY" and (value.get("locator") or {}).get("total")
            and abs(_days(value["period_end"], period_end.isoformat())) <= 12
        ]
        if not annual_totals:
            return False
        sums: dict[str, float] = {}
        for item in read:
            if item.group.kind != "revenue_breakdown":
                continue
            if item.kpi.key == item.group.total_kpi:
                sums[item.group.key] = item.value
            elif item.group.total_kpi is None:
                sums[item.group.key] = sums.get(item.group.key, 0.0) + item.value
        return any(abs(total - annual) <= abs(annual) * 0.01 for total in sums.values() for annual in annual_totals)

    def _earlier_values(self, before: date) -> dict[str, list[float]]:
        earlier: dict[str, list[float]] = {}
        for value in self.values.values():
            if value["method"] == "ai" and value["period_end"] < (before - timedelta(days=20)).isoformat():
                key = f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}"
                earlier.setdefault(key, []).append(float(value["value"]))
        return earlier

    def _previous(self, before: date, annual: bool = False) -> dict[str, float]:
        """The latest verified value of each AI KPI in the period before, for jump checks: the quarter before a
        quarter, the year before a year (a year is never compared with a quarter)."""
        window_start = (before - timedelta(days=400 if annual else 120)).isoformat()
        latest: dict[str, dict] = {}
        for value in self.values.values():
            if (value["method"] != "ai" or value["validation_status"] != "verified"
                    or (value["fiscal_period"] == "FY") != annual
                    or not window_start <= value["period_end"] < before.isoformat()):
                continue
            key = f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}"
            if value["period_end"] > latest.get(key, {}).get("period_end", ""):
                latest[key] = value
        return {key: float(value["value"]) for key, value in latest.items()}

    def _default_currency(self) -> str | None:
        currencies = [value.get("currency") for value in self.values.values() if value["method"] == "xbrl"]
        return next((currency for currency in reversed(currencies) if currency), None)

    # Derived periods and storage.

    def _derive(self) -> None:
        changed = []
        derived = derive_periods(list(self.values.values()))
        produced = {value_key(record) for record in derived}
        # Derived values whose inputs are gone or were replaced (e.g. a Q4 from a rejected reading) are withdrawn.
        changed.extend({**value, "validation_status": "rejected", "notes": ["inputs no longer available"]}
                       for key, value in self.values.items()
                       if value["method"] == "derived" and value["validation_status"] != "rejected" and key not in produced)
        for record in derived:
            existing = self.values.get(value_key(record))
            if existing and existing["method"] != "derived" and existing["validation_status"] == "verified":
                continue  # a reported figure wins; a flagged partial reading (TSMC's release percentages) does not
            if (existing and float(existing["value"]) == float(record["value"])
                    and existing["validation_status"] == record["validation_status"]):
                continue
            changed.append(record)
        for start in range(0, len(changed), 400):
            self._store(values=changed[start:start + 400])

    def _record(self, *, ref: FilingRef, period_end: date, source_url: str | None = None, **fields) -> dict:
        return {
            "symbol": self.symbol, **fields, "period_end": period_end.isoformat(),
            "source_accession": ref.accession, "source_url": source_url or ref.source_url,
            "filed_at": ref.filed.isoformat(),
        }

    def _store_filing(self, ref: FilingRef, status: str, *, records: list[dict] | None = None,
                      period_end: date | None = None, fiscal_year: str | None = None, fiscal_period: str | None = None,
                      spec_version: int | None = None, notes: list[str] | None = None) -> None:
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
            ]
        filing = {
            "accession": ref.accession, "form": ref.form, "filed_at": ref.filed.isoformat(), "document_role": ref.role,
            "period_end": period_end.isoformat() if period_end else None, "fiscal_year": fiscal_year,
            "fiscal_period": fiscal_period, "status": status, "spec_version": spec_version,
            "value_count": sum(1 for record in records if record["validation_status"] != "rejected"), "attempts": (previous.get("attempts", 0) + 1) if previous else 1,
            "notes": [note[:300] for note in (notes or [])][:30], "source_url": ref.source_url,
        }
        self._store(filings=[filing], values=records)
        self.filings[ref.accession] = filing
        self.result.filings += 1
        self.result.needs_review += sum(1 for record in records if record["validation_status"] == "needs_review")
        self.log(f"{self.symbol}: {ref.form} {ref.filed} {status} ({filing['value_count']} values)")

    def _fail_filing(self, ref: FilingRef, error: Exception, spec_version: int | None = None) -> None:
        message = f"{type(error).__name__}: {error}"[:300]
        self.result.errors.append(f"{ref.accession}: {message}")
        self._store_filing(ref, "failed", spec_version=spec_version, notes=[message])

    def _store(self, *, filings: list[dict] | None = None, values: list[dict] | None = None) -> None:
        values = list({value_key(value): value for value in values or []}.values())  # one row per key per write
        self.control.call("store", symbol=self.symbol, filings=filings or [], values=values)
        for value in values:
            existing = self.values.get(value_key(value))
            if existing and _keeps_verified(existing, value):
                continue  # as store_business_kpis does: another filing's unchecked reading never replaces a verified one
            self.values[value_key(value)] = value
        self.result.values += len(values)


def keep_current_groups(proposed: Spec, current: Spec) -> Spec:
    """A re-proposed list never silently drops a group the current list tracks (TSMC's platform and geography once
    vanished on a re-read); only the quarterly audit retires what a company stopped reporting."""
    keys = {group.key for group in proposed.groups}
    labels = {(group.kind, group.label.lower()) for group in proposed.groups}
    kept = [group for group in current.groups
            if group.key not in keys and (group.kind, group.label.lower()) not in labels]
    if not kept:
        return proposed
    groups = [*(group.model_dump() for group in proposed.groups), *(group.model_dump() for group in kept)]
    return Spec.model_validate({"groups": groups[:ai.MAX_GROUPS], "names": proposed.names})


def only_adds(before: Spec, after: Spec) -> bool:
    """Whether a new list keeps every KPI of the old one as it was, adding only."""
    kept = {(group.key, group.kind, kpi.key, kpi.unit) for group in after.groups for kpi in group.kpis}
    return all((group.key, group.kind, kpi.key, kpi.unit) in kept for group in before.groups for kpi in group.kpis)


def apply_maintenance(spec: Spec, answer: ai.Maintenance) -> Spec | None:
    """The list with the audit's additions and retirements, or None when nothing changes. Existing keys keep their
    meaning; a new key that collides with an existing one is ignored."""
    groups = [group.model_copy(deep=True) for group in spec.groups]
    by_key = {group.key: group for group in groups}
    changed = False
    for group_key, kpis in answer.add_kpis.items():
        group = by_key.get(group_key)
        if group is None:
            continue
        existing = {kpi.key for kpi in group.kpis}
        for kpi in kpis:
            unit_ok = group.kind == "metric" or kpi.unit == ("percent" if group.kind == "mix" else "currency")
            if kpi.key not in existing and unit_ok and len(group.kpis) < ai.MAX_KPIS_PER_GROUP:
                group.kpis.append(kpi)
                existing.add(kpi.key)
                changed = True
    amounts = {_dimension(group.label) for group in groups if group.kind == "revenue_breakdown"}
    for group in answer.add_groups:
        if group.kind == "mix" and _dimension(group.label) in amounts:
            continue  # shares of a breakdown already tracked as amounts add nothing (TSMC's technology mix)
        if group.key not in by_key and len(groups) < ai.MAX_GROUPS:
            groups.append(group)
            by_key[group.key] = group
            changed = True
    for retired in answer.retire:
        group_key, _, kpi_key = retired.partition(".")
        group = by_key.get(group_key)
        if group and kpi_key and len(group.kpis) > 1 and any(kpi.key == kpi_key for kpi in group.kpis):
            group.kpis = [kpi for kpi in group.kpis if kpi.key != kpi_key]
            if group.total_kpi == kpi_key:
                group.total_kpi = None
            changed = True
    if not changed:
        return None
    return ai.normalize_spec(Spec.model_validate({"groups": [group.model_dump() for group in groups], "names": spec.names}))


_ANNUAL_HEADER = re.compile(r"twelve months|<12 months>|<5[23] weeks>|fifty-(two|three) weeks|years? ended|fiscal years?|full year")


def _annual_columns(read: list[ReadValue]) -> bool:
    """Whether the breakdown was read from a column its header calls a year ("Twelve Months Ended", "Year Ended")."""
    headers = [str(item.locator.get("header") or "") for item in read if item.group.kind == "revenue_breakdown"]
    return bool(headers) and all(_ANNUAL_HEADER.search(header) for header in headers)


def _keeps_verified(existing: dict, incoming: dict) -> bool:
    return (existing["validation_status"] == "verified" and incoming["validation_status"] == "needs_review"
            and existing.get("source_accession") != incoming.get("source_accession"))


def _unreported(spec: Spec, read: list[ReadValue]) -> list[str]:
    """The listed KPIs a document did not report, apart from breakdowns read as whole tables (their rows vary)."""
    whole = {item.group.key for item in read if item.locator.get("whole_table")}
    found = {f"{item.group.key}.{item.kpi.key}" for item in read}
    return [f"{group.key}.{kpi.key}" for group in spec.groups if group.key not in whole for kpi in group.kpis
            if kpi.key != group.total_kpi and f"{group.key}.{kpi.key}" not in found]


def _dimension(label: str) -> str:
    """What a breakdown splits revenue by: "Revenue by Technology Mix" and "Revenue by Technology" → "technology"."""
    words = re.sub(r"\b(wafer|net|revenue|sales|by|mix|share|shares|of|the)\b", " ", label.lower())
    return " ".join(words.split())


def _same(left: float, right: float) -> bool:
    return abs(left - right) <= max(abs(left), abs(right)) * 1e-9


_PERIOD_WORDS = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
                           r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|first|second|third|fourth|q[1-4])\b")


def _template(text: str) -> str:
    """A quote with its numbers and period words blanked: "DAP was 3.60 billion on average for June 2026" and the
    same sentence for September share one template."""
    return re.sub(r"[\d.,]+", "#", _PERIOD_WORDS.sub("@", clean(text).lower()))


def _same_place(now: dict, before: dict) -> bool:
    """Whether a KPI was found where it was last quarter: the same table row label, or the same sentence shape."""
    if "quote" in now and "quote" in before:
        return _template(now["quote"]) == _template(before["quote"])
    if "row_label" in now and "row_label" in before:
        return clean(now["row_label"]).lower() == clean(before["row_label"]).lower() and bool(now["row_label"])
    return False


def _source_template(url: str) -> str:
    """A document's kind from its file name with the dates and numbers taken out ("a2q26e_withguidance.htm")."""
    return re.sub(r"\d+", "", url.rsplit("/", 1)[-1].lower())


_INVALID_POINTER = re.compile(r"unknown (table|text block)|quote not found|not inside the quote|no number in")


def _explain_pointer(problem: str, document: Document) -> str:
    """Retry feedback that names the fix: a text block cited as a table (ASML's statements are slide images whose
    figures come through as text) must be quoted instead."""
    match = re.search(r"unknown table (\S+)", problem)
    if match and match.group(1) in document.blocks:
        block = match.group(1)
        return (f"{problem}: {block} is a text block, not a table. Cite it as "
                f'{{"kpi": ..., "block": "{block}", "quote": "<the label and the numbers, copied exactly>", '
                f'"value_text": "<the one number for the reported period>"}}; the column headers at the start of the '
                "block give the order of the periods.")
    return problem


def _is_jump(note: str) -> bool:
    return note.startswith(("changed ", "moved "))


def _document_text(document: Document) -> str:
    cells = [" ".join(cell for row in table.rows for cell in row) for table in document.tables.values()]
    return clean(" ".join([*(block.text for block in document.blocks.values()), *cells]))


def _days(left: str, right: str) -> int:
    return (date.fromisoformat(left[:10]) - date.fromisoformat(right[:10])).days
