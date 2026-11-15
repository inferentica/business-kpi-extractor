"""Arelle, the reference XBRL processor, as a second reader of each XBRL filing (a check only: it never changes what a
reading holds or its status).

Two questions for every statement column read from a filing's XBRL:
- facts: is each figure we serve one of the filing's own facts for that line and period (any sign, either currency it
  is tagged in)? A line Arelle finds tagged with other values only is a misread.
- calc: does the filing's own calculation file hold for the column (XBRL Calculations 1.1, round-to-nearest, in the
  filing's statement roles and its company-wide contexts)? Arelle names each sum that does not.
Each answers pass, fail or none (nothing to compare), beside the reader's own checks, so the two can be compared over
whole runs before Arelle's verdict is allowed to decide anything.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from ..domain.model import FAIL, NONE, PASS

TIMEOUT = 120  # seconds for one try at a filing; a filing stalled twice is left unchecked
ARELLE_BUDGET = 2700  # seconds of Arelle per company; filings after it are left unchecked
WORKERS = 3  # filings Arelle reads at once, beside the reader (each in a process of its own)


def instance_url(filing) -> str | None:
    """The filing's XBRL instance: the one it filed (EX-101.INS), else EDGAR's extract of its inline XBRL (*_htm.xml),
    else the inline document itself."""
    files = list(getattr(getattr(filing, "attachments", None), "data_files", None) or [])
    for wanted in (lambda a: a.document_type == "EX-101.INS",
                   lambda a: str(a.document).lower().endswith("_htm.xml")):
        found = next((attachment for attachment in files if wanted(attachment)), None)
        if found is not None:
            return found.url
    document = getattr(filing, "document", None)
    return getattr(document, "url", None)


def run_arelle(url: str, cache: str, timeout: int = TIMEOUT) -> dict:
    """One filing through Arelle in a process of its own (checks/arelle_run.py)."""
    try:
        done = subprocess.run([sys.executable, "-m", "statement_extractor.checks.arelle_run", url, cache],
                              capture_output=True, text=True, timeout=timeout, env=os.environ.copy())
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {timeout} s"}
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    if not lines:
        return {"error": f"no result (exit {done.returncode}): {done.stderr.strip()[-160:]}"}
    return json.loads(lines[-1])


def _local(concept: str) -> str:
    """A concept by its name alone: Arelle and edgartools may write a filer's own prefix differently."""
    return concept.split("_", 1)[-1] if "_" in concept else concept


class Facts:
    def __init__(self, result: dict):
        self.values: dict[tuple, list[float]] = {}
        for concept, start, end, dimensioned, value, _ in result.get("facts", []):
            self.values.setdefault((_local(concept), start, end, bool(dimensioned)), []).append(float(value))
        self.inconsistent: dict[tuple, list[str]] = {}
        for concept, start, end, dimensioned, reported, computed, _ in result.get("calc", []):
            if not dimensioned:
                self.inconsistent.setdefault((_local(concept), start, end), []).append(
                    f"{_local(concept)} {reported} vs {computed}")
        self.sums = {_local(concept) for concept in result.get("sums", [])}

    def candidates(self, concept: str, reading, member: bool) -> list[float]:
        end = reading.period_end.isoformat()
        if reading.period_start is None:
            return self.values.get((_local(concept), None, end, member), [])
        start = reading.period_start
        # Opening and closing cash: a filer tags the opening balance on the day before the period, its first day
        # (Visteon's 2010 fresh start) or its second (Visteon's 2009).
        instants = [end] + [(start + timedelta(days=shift)).isoformat() for shift in (-1, 0, 1)]
        keys = [(start.isoformat(), end)] + [(None, day) for day in instants]
        return [value for begin, stop in keys for value in self.values.get((_local(concept), begin, stop, member), [])]

    def member_balances(self, concept: str, reading) -> list[float]:
        """A flow statement's balance tagged only under a member (Lloyds' 2017 opening cash as "previously stated",
        Cemig's "restated"), which the reader keeps too (readers/xbrl/read.py)."""
        if reading.period_start is None:
            return []
        days = [reading.period_end] + [reading.period_start + timedelta(days=shift) for shift in (-1, 0, 1)]
        return [value for day in days for value in self.values.get((_local(concept), None, day.isoformat(), True), [])]


def _same(left: float, right: float) -> bool:
    return abs(abs(left) - abs(right)) <= 1e-6 * max(1.0, abs(left), abs(right))


def compare(reading, facts: Facts) -> dict:
    """Arelle's two verdicts on one column read from XBRL."""
    matched, missing, differ = 0, 0, []
    concepts = set()
    for line in reading.lines:
        if line.get("is_heading") or line.get("value") is None:
            continue
        concepts.add(_local(line["concept"]))
        found = facts.candidates(line["concept"], reading, "member" in line)
        if "member" not in line and not any(_same(line["value"], value) for value in found):
            found = found + facts.member_balances(line["concept"], reading)
        if not found:
            missing += 1
        elif any(_same(line["value"], value) for value in found):
            matched += 1
        else:
            differ.append(f"{_local(line['concept'])} {line['value']:,.0f} vs {found[0]:,.0f}")
    fact_result = FAIL if differ else PASS if matched else NONE
    start = reading.period_start.isoformat() if reading.period_start else None
    wrong = [detail for concept in sorted(concepts)
             for detail in facts.inconsistent.get((concept, start, reading.period_end.isoformat()), [])]
    calc_result = FAIL if wrong else PASS if concepts & facts.sums else NONE
    return {"facts": fact_result, "matched": matched, "missing": missing, "calc": calc_result,
            "detail": "; ".join(differ[:3] + wrong[:3])[:300]}


def check_filing(filing, readings: list, cache: str) -> dict:
    """Arelle's verdicts on every column read from one filing, set on each reading's `arelle`; the filing's record."""
    url = instance_url(filing)
    return _verdicts(url, run_arelle(url, cache) if url else {"error": "no instance"}, readings)


def _verdicts(url: str | None, result: dict, readings: list) -> dict:
    record = {"seconds": result.get("seconds"), "url": url}
    if "error" in result:
        record["error"] = result["error"]
        return record
    facts = Facts(result)
    for reading in readings:
        reading.arelle = compare(reading, facts)
    record.update({"facts": len(result.get("facts", [])), "inconsistent": len(result.get("calc", [])),
                   "errors": result.get("errors", {})})
    return record


class Checks:
    """A company's filings through Arelle, WORKERS at a time while the reader goes on, within ARELLE_BUDGET seconds of
    the first: a filing still waiting then is left unchecked."""

    def __init__(self, cache: str):
        self.cache = cache
        self.pool = ThreadPoolExecutor(WORKERS)
        self.pending: list[tuple[dict, Future]] = []
        self.deadline: float | None = None

    def submit(self, filing, readings: list, record: dict) -> None:
        url = instance_url(filing)  # here, while the reader still holds the filing
        self.deadline = self.deadline or time.time() + ARELLE_BUDGET
        self.pending.append((record, self.pool.submit(self._check, url, readings)))

    def _check(self, url: str | None, readings: list) -> dict:
        left = (self.deadline or 0) - time.time()
        if not url:
            return _verdicts(url, {"error": "no instance"}, readings)
        if left <= 0:
            return _verdicts(url, {"error": "over the company's Arelle budget"}, readings)
        result = run_arelle(url, self.cache, int(min(TIMEOUT, max(left, 1))))
        left = self.deadline - time.time()
        if str(result.get("error", "")).startswith("timed out") and left > 0:
            result = run_arelle(url, self.cache, int(min(TIMEOUT, max(left, 1))))  # once more: a stalled download
        return _verdicts(url, result, readings)

    def finish(self) -> None:
        """Each filing's record, once Arelle has read them all."""
        for record, future in self.pending:
            record["arelle"] = future.result()
        self.pool.shutdown()


def enabled() -> str | None:
    """The cache directory Arelle keeps taxonomies in, when this run checks with it (STATEMENTS_ARELLE_CACHE)."""
    return os.environ.get("STATEMENTS_ARELLE_CACHE") or None


def arelle_summary(readings: list, filings: list[dict], cache: str) -> dict:
    """A company's Arelle verdicts beside its own checks' (reading status by Arelle's calc and facts results), and the
    taxonomy files Arelle fetched, so a later run can have them ready."""
    checked = [reading for reading in readings if reading.arelle]
    records = [record["arelle"] for record in filings if "arelle" in record]
    fetched = sorted(str(path.relative_to(cache)) for path in Path(cache).rglob("*")
                     if path.is_file() and "www.sec.gov" not in path.parts) if Path(cache).is_dir() else []
    return {"filings": len(records), "unchecked": sum(1 for record in records if "error" in record),
            "seconds": round(sum(record.get("seconds") or 0 for record in records), 1), "readings": len(checked),
            "facts": dict(Counter(reading.arelle["facts"] for reading in checked)),
            "calc": dict(Counter(reading.arelle["calc"] for reading in checked)),
            "by_status": dict(Counter(f"{reading.status}/{reading.arelle['calc']}/{reading.arelle['facts']}"
                                      for reading in checked)),
            "taxonomy_files": fetched}
