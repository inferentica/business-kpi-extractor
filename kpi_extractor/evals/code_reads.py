"""How much of the release figures code reads with no AI at all, how many it gets wrong, and how fast (Phase 4).

Each company's earnings releases are read again by the real pipeline with the AI switched off: every document goes
through code first, as in production (replay from the nearest quarter's places, then each code reader), and what would
have gone to the AI is left unread. The company's stored figures stay as they are for the whole run, so each quarter is
read only from places its earlier quarters were verified at, never from this run's own readings, and no quarter after it
lends its places. Nothing is written to production and no AI call is made (¥0).

Each verified release figure is then scored against code's reading of the same filing:
  right   code served the same figure;
  wrong   code served another figure (must stay 0);
  held    code read it but its checks held it, so it is not served;
  missed  code did not read it (the replay outcomes say why, bucket by bucket).
A figure code serves that the store does not serve is listed apart (extra): it is checked against its filing before any
reader change counts it.

Releases are read as each one arrives (--order arrival, the default): one at a time in date order, each replayed from
the quarter just before it, as production reads a new release. --order backfill reads them as a company's first run
does, in four chains of one fiscal quarter each, so each release replays from the same quarter a year earlier.

A company with nothing stored (one never read before) is read from scratch the same way, with no AI: its KPI list is
the AI's to write, so what code reads of it (XBRL breakdowns and tags, release tables, charts) is listed in full for a
reading by hand against its filings (eval-results/code-reads-new.json).

The latest four quarters of every company and five companies picked by hash are held out: readers are tuned on the rest
and the held-out part confirms each change.

    python -m kpi_extractor.evals.code_reads --symbols TSM,JPM --quarters 12
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType

from ..ai import Located
from ..ops.cli import company_pool
from ..ops.control import ControlPlane
from ..pipeline import SymbolPipeline, SymbolResult
from ..readers.trace import CODE_READERS
from ..sources.sec import configure_identity
from ..quality.checks import check_period, validate_groups
from ..readers.extract import _row_label, read_values
from ..xbrl import member_key
from ..readers.replay import _source_template, row_gone
from ..steps.replay_reading import in_full_groups, left_for_ai
from ..steps.shared import AI_GROUP_PREFIX, _days, _same, value_key
from ..store import rounding_of

UNCONFIRMED = "not confirmed where it was or by the year-ago column"

HELD_OUT_COMPANIES = 5
HELD_OUT_QUARTERS = 4
_READ = ("processed", "needs_review")


def held_out_companies(symbols: list[str], count: int = HELD_OUT_COMPANIES) -> set[str]:
    """Companies kept out of tuning, picked by a hash of the ticker: the same ones every run, chosen by nobody."""
    return set(sorted(symbols, key=lambda symbol: hashlib.md5(symbol.encode()).hexdigest())[:count])


class CodeOnlyControl:
    """The control plane for state and the filing archive; every write stays here and every AI request is refused."""

    def __init__(self, plane: ControlPlane):
        self.plane = plane
        self.refused: Counter = Counter()
        self._lock = threading.Lock()

    @property
    def run_id(self):
        return self.plane.run_id

    def call(self, operation: str, **payload) -> dict:
        if operation in ("store", "store_statements"):
            return {}
        return self.plane.call(operation, **payload)

    def ai(self, symbol: str, purpose: str, *_args, **_kwargs) -> str:
        with self._lock:
            self.refused[purpose] += 1
        return ""


class CodeOnlyPipeline(SymbolPipeline):
    """The pipeline reading the company's releases again with code alone. While the releases are read the stored figures
    are frozen, so each quarter replays only from verified places; after, the tested filings hold only what code read, and
    the steps that fill figures by code (XBRL tags, quarter tables, quoted rows) and settle them run as in production."""

    def __init__(self, control: CodeOnlyControl, symbol: str, quarters: int, log=print, order: str = "arrival"):
        super().__init__(control, symbol, quarters, False, log=log, reread="code-only")
        self.order = order
        self.readings: dict[str, list[dict]] = {}
        self.timings: list[float] = []
        self.left: Counter = Counter()  # group (or metric KPI) keys left for the AI
        self.kinds: dict[str, str] = {}
        # Why each figure code tried was not read, with the place it was looked for: (accession, "group.kpi") -> both.
        self.misses: dict[tuple[str, str], dict] = {}
        self._reread_picked = None
        # Frozen: a reading is recorded, never applied, so later quarters replay only from verified stored places.
        self.store._apply = lambda values: None
        record = self.store.record

        def capture(reading, unread_groups=None):
            if reading.ref.accession in (self._reread_picked or set()):
                self.readings.setdefault(reading.ref.accession, []).extend(
                    dict(row) for row in reading.records if row.get("validation_status") in ("verified", "needs_review"))
            return record(reading, unread_groups)
        self.store.record = capture

    def _process_releases(self, releases, spec, version):
        self.kinds = {group.key: group.kind for group in spec.groups}
        if self.order == "backfill":
            processed = super()._process_releases(releases, spec, version)
        else:  # one release at a time, as a new release is read: the quarter before it is its reference
            processed = [ref for release in sorted(releases, key=lambda ref: ref.period_end or date.min)
                         for ref in super()._process_releases([release], spec, version)]
        self._code_only_state()
        return processed

    def _code_only_state(self) -> None:
        """The tested filings' release figures as code read them, in place of the stored ones (the owner's included: a
        figure the owner decided is not one code read), and the store live again for the steps after."""
        tested, values = self._reread_picked or set(), self.store._values
        for key in [key for key, value in values.items() if value.get("source_accession") in tested
                    and value.get("method") == "ai" and value.get("validation_status") != "rejected"]:
            del values[key]
        for rows in self.readings.values():
            for row in rows:
                values[value_key(row)] = MappingProxyType(row)
        del self.store._apply  # the class's own again

    def _replay_part(self, document, part, locators, expected, currency, annual=False):
        found = super()._replay_part(document, part, locators, expected, currency, annual)
        if isinstance(found, str):
            accession = getattr(self._local, "accession", None)
            lacking = _closest_table(document, locators) if found == "table rows not found" else None
            for key, locator in locators.items():
                place = {name: locator.get(name) for name in ("row_label", "quote", "table", "header") if locator.get(name)}
                if lacking:
                    place["closest"] = lacking
                self.misses[(accession, key)] = {"why": found, "place": place, "document": document.source_url.rsplit("/", 1)[-1]}
        return found

    def _checked(self, document, part, located, expected, currency, mark=None, annual=False):
        found = super()._checked(document, part, located, expected, currency, mark, annual)
        if found == UNCONFIRMED:
            started = time.perf_counter()  # a diagnosis, not reading: kept out of code's time
            keys = {group.key for group in part.groups}
            read = [item for item in read_values(document, part, located, currency)[0] if item.group.key in keys]
            found = f"not confirmed: {self._unconfirmed_why(located, read, part, expected, document)}"
            self._local.diagnosis = getattr(self._local, "diagnosis", 0.0) + time.perf_counter() - started
        return found

    def _unconfirmed_why(self, located, read, part, expected, document) -> str:
        """Which of the confirmation's conditions (AiReading._confirmed_groups) a code reading failed."""
        if located.annual and not check_period(located, expected):
            return "a year's reading not confirmed by last year's place or year-ago column"
        if check_period(located, expected) or located.annual or self._reports_full_year(read, expected):
            return "the reading's period is in doubt"
        earlier = self._earlier_values(expected)
        last = self._last_locators(expected, _source_template(document.source_url))
        target = (expected - timedelta(days=365)).isoformat()
        stored = {f"{v['group_key'].removeprefix(AI_GROUP_PREFIX)}.{v['kpi_key']}": float(v["value"])
                  for v in self.values.values() if v["validation_status"] == "verified" and v["method"] in ("ai", "derived")
                  and v["fiscal_period"] != "FY" and abs(_days(v["period_end"], target)) <= 12}
        for group in part.groups:
            items = [item for item in read if item.group.key == group.key]
            parts = [item for item in items if not (item.is_total or item.kpi.key == group.total_kpi)]
            keys = {f"{item.group.key}.{item.kpi.key}" for item in items}
            whole = any(item.locator.get("whole_table") for item in items)
            listed = {f"{group.key}.{kpi.key}" for kpi in group.kpis}
            gone = [key for key in last if key.split(".", 1)[0] == group.key and key not in keys and (whole or key in listed)
                    and not row_gone(document, last[key])]
            if gone:
                return f"a figure read last quarter is missing now ({gone[0].split('.', 1)[1][:40]})"
            if any(self._repeats(item, document, earlier.get(f"{group.key}.{item.kpi.key}", [])) for item in parts):
                return "a figure repeats an earlier period's"
            trial = copy.deepcopy(items)
            validate_groups(trial, self._previous(expected))
            held = next((item for item in trial if item.status != "verified"), None)
            if held is not None:
                return f"a check holds it ({(held.notes or ['unstated'])[-1][:60]})"
            for item in parts:
                if self._same_place_as_last(item, expected, document):
                    continue
                before = stored.get(f"{group.key}.{item.kpi.key}")
                if before is None:
                    return "moved, and no year-ago figure is stored"
                if not self._year_ago_cell_matches(item, document, before):
                    return "moved, and its year-ago column differs from the stored figure"
        return "other"

    def _untried(self, document, spec, expected, done: set[str]) -> None:
        """Why each KPI replay never tried was left: the group-level reasons the replay counts but does not name."""
        accession = getattr(self._local, "accession", None)
        last = self._last_locators(expected, _source_template(document.source_url))
        tried = {key.split(".", 1)[0] for known, key in self.misses if known == accession}  # a table's miss is its group's
        for group in spec.groups:
            for kpi in group.kpis:
                key = f"{group.key}.{kpi.key}"
                if key in done or group.key in done or (accession, key) in self.misses or (group.kind != "metric" and group.key in tried):
                    continue
                if not last:
                    why = "no earlier reading of this kind of document"
                elif not any(known.split(".", 1)[0] == group.key for known in last):
                    why = "group not read in this kind of document before"
                elif key not in last:
                    why = "this KPI not read in this kind of document before"
                else:
                    why = "group held back: a listed KPI not looked for yet"
                self.misses[(accession, key)] = {"why": why, "place": {}, "document": document.source_url.rsplit("/", 1)[-1]}

    def _reread_due(self, accession: str) -> bool:
        if self._reread_picked is None:
            self._reread_picked = tested_filings(self.values, self.filings)
            self.log(f"{self.symbol}: {len(self._reread_picked)} releases to read with code alone")
        return accession in self._reread_picked

    def _ai_history(self, before: date, source: str | None = None) -> dict[str, dict]:
        """Only quarters before the one being read: a later quarter's place is never lent to an earlier one here."""
        cutoff = before.isoformat()
        history = super()._ai_history(before, source)
        return {key: value for key, value in history.items() if value["period_end"] < cutoff}

    def _read(self, text, document, spec, expected, version=None):
        """Code first, as AiReading._read, with what is left for the AI left unread."""
        currency = self._default_currency() or document.declared_currency() or document.marked_currency()
        self._local.diagnosis = 0.0
        self._local.replayed = False
        self._local.second_read = None
        started = time.perf_counter()
        replayed, located, done = self._replay_groups(document, spec, expected, currency)
        self.timings.append(time.perf_counter() - started - self._local.diagnosis)
        open_spec = left_for_ai(spec, done) if done else spec
        self._untried(document, spec, expected, done)
        if open_spec is None:
            self._local.replayed = True
            self.result.replayed_reads += 1
        else:
            for group in open_spec.groups:
                self.left[group.key] += len(group.kpis)
        return in_full_groups(replayed, spec), [], located or Located()


def _closest_table(document, locators: dict[str, dict]) -> dict:
    """For a whole table replay found nowhere: the table holding most of last quarter's rows, and the rows it lacks."""
    wanted = {member_key(locator["row_label"]): locator["row_label"] for locator in locators.values() if locator.get("row_label")}
    total = next((locator.get("total_row_label") for locator in locators.values() if locator.get("total_row_label")), None)
    best = None
    for table in document.tables.values():
        labels = {member_key(_row_label(row, 1)) for row in table.rows}
        lacking = [label for key, label in wanted.items() if key not in labels]
        if total and member_key(total) not in labels:
            lacking.append(f"total: {total}")
        if best is None or len(lacking) < len(best[1]):
            best = (table.id, lacking)
    return {"table": best[0], "lacks": best[1][:6]} if best else {}


def tested_filings(values, filings) -> set[str]:
    """The filings holding verified release figures (the store keeps a release's figures as method "ai", whether the AI,
    a replay or a code reader read them; the company state names no filing's role). Only the ones the release flow reads
    again are scored: a prospectus's figures are read elsewhere."""
    with_figures = {value.get("source_accession") for value in values.values()
                    if value.get("method") == "ai" and value.get("validation_status") == "verified"}
    return {accession for accession, filing in filings.items() if filing.get("status") in _READ and accession in with_figures}


def origin(value: dict) -> str:
    """What read a stored figure: the AI, a replay of an earlier reading, a code reader, or the owner."""
    locator = value.get("locator") or {}
    if value.get("owner_decision"):
        return "owner"
    if locator.get("replayed_from"):
        return "replay"
    if any(locator.get(reader) for reader in CODE_READERS) or locator.get("rule"):
        return "code"
    return "ai"


def _key(value: dict) -> tuple:
    return (value["group_key"], value["kpi_key"], value["fiscal_year"], value["fiscal_period"])


def score_company(symbol: str, state_values: list[dict], kinds: dict[str, str], tested: set[str], final: dict,
                  held_out: bool, misses: dict | None = None) -> list[dict]:
    """One row per verified release figure of the tested filings, scored on the company's figures as code alone left
    them; and one per figure code serves that the store does not."""
    truth = [value for value in state_values if value.get("method") == "ai" and value.get("validation_status") == "verified"
             and value.get("source_accession") in tested]
    quarters = sorted({value["period_end"][:7] for value in truth}, reverse=True)[:HELD_OUT_QUARTERS]
    stored = {value_key(value): value for value in state_values}
    kind = lambda group: "metric" if kinds.get(group, "metric") == "metric" else "breakdown"  # noqa: E731
    rows = []
    for value in truth:
        accession, expected = value["source_accession"], float(value["value"])
        now = final.get(value_key(value))
        found = float(now["value"]) if now is not None and now.get("validation_status") != "rejected" else None
        if found is None:
            outcome = "missed"
        elif now["validation_status"] != "verified":
            outcome = "held"
        elif abs(found - expected) <= max(abs(expected) * 1e-6, 1e-9):
            outcome = "right"
        else:
            # A rounded copy of the figure (a headline beside its table's cell) is the filing's too, but not the same
            # figure: counted apart, never as right.
            outcome = "rounded" if rounding_of(found, expected) or rounding_of(expected, found) else "wrong"
        group = value["group_key"].removeprefix(AI_GROUP_PREFIX)
        miss = (misses or {}).get((accession, f"{group}.{value['kpi_key']}")) if outcome == "missed" else None
        rows.append({
            "why": (miss or {}).get("why") or ("document not read by code" if outcome == "missed" else ""), "miss": miss,
            "symbol": symbol, "accession": accession, "group": group, "kpi": value["kpi_key"],
            "label": f"{value.get('group_label')} / {value.get('kpi_label')}", "period": f"{value['fiscal_year']}-{value['fiscal_period']}",
            "kind": kind(group), "origin": origin(value), "expected": expected, "found": found, "outcome": outcome,
            "readBy": reader(now) if outcome in ("right", "rounded", "wrong") else "",
            # Where code read a figure that is not right: its filing and place, so a wrong one is found at once.
            "foundAt": {"accession": now.get("source_accession"), "document": str(now.get("source_url") or "").rsplit("/", 1)[-1],
                        **{name: (now.get("locator") or {}).get(name) for name in ("table", "row", "col", "row_label", "quote", "replayed_from")
                           if (now.get("locator") or {}).get(name) is not None}} if now is not None and outcome != "right" else None,
            "notes": "; ".join((now.get("notes") or [])[:3]) if now is not None and outcome != "right" else "",
            "set": "held out" if held_out or value["period_end"][:7] in quarters else "tuning",
        })
    for key, row in final.items():
        before = stored.get(key)
        if row.get("validation_status") != "verified" or row.get("method") != "ai" or (before or {}).get("validation_status") == "verified":
            continue
        group = row["group_key"].removeprefix(AI_GROUP_PREFIX)
        rows.append({"symbol": symbol, "accession": row.get("source_accession"), "group": group, "kpi": row["kpi_key"],
                     "label": f"{row.get('group_label')} / {row.get('kpi_label')}", "period": f"{row['fiscal_year']}-{row['fiscal_period']}",
                     "kind": kind(group), "origin": "", "expected": float(before["value"]) if before else None,
                     "found": float(row["value"]), "outcome": "extra", "readBy": reader(row), "why": "", "miss": None,
                     "notes": f"stored {before['validation_status']}" if before else "not stored",
                     "set": "held out" if held_out else "tuning"})
    return rows


def reader(value: dict) -> str:
    """Which code read a figure this run: replay, or the code reader its locator names."""
    locator = value.get("locator") or {}
    if locator.get("replayed_from"):
        return "replay"
    return next((name for name in CODE_READERS if locator.get(name)), "rule" if locator.get("rule") else "other")


def read_company(plane: ControlPlane, symbol: str, quarters: int, held_out: bool, order: str = "arrival") -> dict:
    """One company in its own process: its scored rows, the replay outcomes, the time code took, AI requests refused."""
    control = CodeOnlyControl(plane)
    started = time.perf_counter()
    pipeline = CodeOnlyPipeline(control, symbol, quarters, log=lambda line: print(line, flush=True), order=order)
    try:
        result = pipeline.run()
    except Exception as error:  # noqa: BLE001
        result = SymbolResult(symbol, errors=[f"{type(error).__name__}: {error}"[:300]])
    state = [dict(value) for value in pipeline.store._loaded.values()]
    kinds = pipeline.kinds
    return {
        "symbol": symbol, "rows": score_company(symbol, state, kinds, set(pipeline.readings), dict(pipeline.store._values), held_out, pipeline.misses),
        "outcomes": dict(result.replay_outcomes), "left": dict(pipeline.left), "refused": dict(control.refused),
        "aiCalls": 0, "errors": result.errors[:5], "documents": len(pipeline.timings),
        "codeSeconds": pipeline.timings, "seconds": time.perf_counter() - started,
    }


def read_new_company(plane: ControlPlane, symbol: str, quarters: int) -> dict:
    """A company never read before, read from scratch by code alone: every figure the pipeline would have stored."""
    control = CodeOnlyControl(plane)
    started = time.perf_counter()
    pipeline = SymbolPipeline(control, symbol, quarters, False, log=lambda line: print(line, flush=True))
    try:
        result = pipeline.run()
    except Exception as error:  # noqa: BLE001
        result = SymbolResult(symbol, errors=[f"{type(error).__name__}: {error}"[:300]])
    figures = sorted((new_figure(value) for value in pipeline.store._values.values()
                      if value.get("validation_status") != "rejected"), key=lambda row: (row["group"], row["kpi"], row["period"]))
    return {"symbol": symbol, "figures": figures, "refused": dict(control.refused), "errors": result.errors[:5],
            "seconds": time.perf_counter() - started}


def new_figure(value: dict) -> dict:
    locator = value.get("locator") or {}
    return {"group": str(value["group_key"]), "kpi": str(value["kpi_key"]), "label": " / ".join(part for part in (value.get("group_label"), value.get("kpi_label")) if part),
            "period": f"{value['fiscal_year']}-{value['fiscal_period']}", "end": str(value.get("period_end") or ""),
            "value": value.get("value"), "unit": value.get("unit") or "", "method": value.get("method"),
            "status": value.get("validation_status"), "readBy": reader(value), "source": value.get("source_url") or "",
            "quote": str(locator.get("quote") or locator.get("label") or locator.get("concept") or "")[:300]}


def new_text(companies: list[dict]) -> str:
    lines = ["New companies, read by code alone (¥0)", "", "| Company | Figures | Verified | Needs review | By method | Seconds |",
             "|---|---|---|---|---|---|"]
    for company in companies:
        figures = company["figures"]
        methods = Counter(figure["method"] for figure in figures)
        lines.append(f"| {company['symbol']} | {len(figures):,} | {sum(f['status'] == 'verified' for f in figures):,} | "
                     f"{sum(f['status'] == 'needs_review' for f in figures):,} | "
                     f"{', '.join(f'{name} {count:,}' for name, count in methods.most_common())} | {company['seconds']:.0f} |")
    errors = {company["symbol"]: company["errors"] for company in companies if company["errors"]}
    if errors:
        lines += ["", "Errors: " + "; ".join(f"{symbol}: {found}" for symbol, found in errors.items())]
    return "\n".join(lines)


def summarize(companies: list[dict]) -> dict:
    rows = [row for company in companies for row in company["rows"]]
    table = defaultdict(Counter)
    for row in rows:
        table[(row["set"], row["kind"])][row["outcome"]] += 1
        table[("all", row["kind"])][row["outcome"]] += 1
        table[("all", "all")][row["outcome"]] += 1
        table[(row["set"], "all")][row["outcome"]] += 1
    by_origin = defaultdict(Counter)
    for row in rows:
        if row["outcome"] != "extra":
            by_origin[row["origin"]][row["outcome"]] += 1
    by_reader = Counter(row["readBy"] for row in rows if row["outcome"] == "right")
    missed_why = Counter((row["kind"], row["why"]) for row in rows if row["outcome"] == "missed")
    timings = [seconds for company in companies for seconds in company["codeSeconds"]]
    outcomes = Counter()
    for company in companies:
        outcomes.update(company["outcomes"])
    return {
        "table": {f"{part}|{kind}": dict(counts) for (part, kind), counts in table.items()},
        "byOrigin": {key: dict(counts) for key, counts in by_origin.items()},
        "outcomes": dict(outcomes.most_common()),
        "rightByReader": dict(by_reader.most_common()),
        "missedWhy": {f"{kind}|{why}": count for (kind, why), count in missed_why.most_common()},
        "byCompany": {company["symbol"]: dict(Counter(row["outcome"] for row in company["rows"])) for company in companies},
        "documents": len(timings),
        "codeMsMedian": round(statistics.median(timings) * 1000, 1) if timings else None,
        "codeMsP90": round(sorted(timings)[int(len(timings) * 0.9)] * 1000, 1) if timings else None,
        "codeSecondsTotal": round(sum(timings), 1),
        "wallSeconds": {company["symbol"]: round(company["seconds"], 1) for company in companies},
        "refused": dict(sum((Counter(company["refused"]) for company in companies), Counter())),
        "errors": {company["symbol"]: company["errors"] for company in companies if company["errors"]},
    }


def _coverage(counts: dict) -> str:
    total = sum(counts.get(outcome, 0) for outcome in ("right", "rounded", "wrong", "held", "missed"))
    return f"{counts.get('right', 0) / total:.1%}" if total else "—"


def report_text(summary: dict) -> str:
    order = {"arrival": "each release as it arrives", "backfill": "as a backfill"}.get(summary.get("order"), "")
    lines = [f"Code reads, no AI (¥0){' · ' + order if order else ''}", "", "| Set | Figures | Right | Rounded | Wrong | Held | Missed | Coverage | Extra |",
             "|---|---|---|---|---|---|---|---|---|"]
    for part in ("tuning", "held out", "all"):
        for kind in ("metric", "breakdown", "all"):
            counts = summary["table"].get(f"{part}|{kind}") or {}
            total = sum(counts.get(outcome, 0) for outcome in ("right", "rounded", "wrong", "held", "missed"))
            if not total and not counts.get("extra"):
                continue
            name = {"metric": "operating metrics", "breakdown": "breakdowns", "all": "all"}[kind]
            lines.append(f"| {part} · {name} | {total:,} | {counts.get('right', 0):,} | {counts.get('rounded', 0):,} | {counts.get('wrong', 0):,} | "
                         f"{counts.get('held', 0):,} | {counts.get('missed', 0):,} | {_coverage(counts)} | {counts.get('extra', 0):,} |")
    lines += ["", "| Read in production by | Figures | Code right | Wrong | Coverage |", "|---|---|---|---|---|"]
    for key, counts in sorted(summary["byOrigin"].items()):
        total = sum(counts.values())
        lines.append(f"| {key} | {total:,} | {counts.get('right', 0):,} | {counts.get('wrong', 0):,} | {_coverage(counts)} |")
    lines += ["", "Right, by the code that read it: " + ", ".join(f"{name} {count:,}" for name, count in summary.get("rightByReader", {}).items())]
    lines += ["", f"Speed: {summary['documents']:,} documents, code {summary['codeMsMedian']} ms median, "
              f"{summary['codeMsP90']} ms p90, {summary['codeSecondsTotal']} s in all",
              f"AI requests refused (none made): {summary['refused'] or 'none'}", "",
              "| Replay outcome | Count |", "|---|---|"]
    lines += [f"| {why} | {count:,} |" for why, count in summary["outcomes"].items()]
    lines += ["", "| Missed figures, by why | Kind | Figures |", "|---|---|---|"]
    lines += [f"| {key.split('|', 1)[1]} | {key.split('|', 1)[0]} | {count:,} |" for key, count in summary.get("missedWhy", {}).items()]
    lines += ["", "| Company | Right | Wrong | Held | Missed | Extra |", "|---|---|---|---|---|---|"]
    for symbol, counts in sorted(summary["byCompany"].items()):
        lines.append(f"| {symbol} | {counts.get('right', 0)} | {counts.get('wrong', 0)} | {counts.get('held', 0)} | "
                     f"{counts.get('missed', 0)} | {counts.get('extra', 0)} |")
    if summary["errors"]:
        lines += ["", "Errors: " + "; ".join(f"{symbol}: {errors}" for symbol, errors in summary["errors"].items())]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", required=True, help="Comma-separated tickers")
    parser.add_argument("--quarters", type=int, default=12)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--order", choices=("arrival", "backfill"), default="arrival")
    args = parser.parse_args(argv)
    configure_identity()
    symbols = sorted({symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()})
    plane = ControlPlane(os.environ["BUSINESS_KPI_WORKFLOW_URL"])
    started = plane.call("start", runId=None, triggerType="eval", symbols=symbols, quarters=args.quarters, forceRefresh=False)
    plane.run_id = started["runId"]
    # A company with nothing stored is read from scratch (once, in the arrival pass) and listed for checking by hand.
    fresh = [symbol for symbol in symbols if not (plane.call("symbol_state", symbol=symbol).get("values"))]
    if fresh and args.order == "arrival":
        with company_pool(max(1, min(args.workers, len(fresh)))) as pool:
            new = list(pool.map(read_new_company, [plane] * len(fresh), fresh, [args.quarters] * len(fresh)))
        Path("eval-results").mkdir(exist_ok=True)
        Path("eval-results/code-reads-new.json").write_text(json.dumps(new, default=str, indent=1))
        print(new_text(new))
        if path := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(path, "a") as out:
                out.write(new_text(new) + "\n\n")
    symbols = [symbol for symbol in symbols if symbol not in fresh]
    held = held_out_companies(symbols)
    with company_pool(max(1, min(args.workers, len(symbols)))) as pool:
        companies = list(pool.map(read_company, [plane] * len(symbols), symbols, [args.quarters] * len(symbols),
                                  [symbol in held for symbol in symbols], [args.order] * len(symbols)))
    summary = {**summarize(companies), "heldOutCompanies": sorted(held), "quarters": args.quarters, "order": args.order}
    plane.call("finish", summary={"codeReads": summary})
    text = report_text(summary)
    print(text)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as out:  # both orders' reports, one after the other
            out.write(text + "\n\n")
    Path("eval-results").mkdir(exist_ok=True)
    name = "code-reads.json" if args.order == "arrival" else f"code-reads-{args.order}.json"
    Path(f"eval-results/{name}").write_text(json.dumps(
        {"summary": summary, "rows": [row for company in companies for row in company["rows"]]}, default=str, indent=1))
    wrong = summary["table"].get("all|all", {}).get("wrong", 0)
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
