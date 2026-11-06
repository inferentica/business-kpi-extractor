"""Scores the AI prompts one by one against cases with known answers (evals/prompts.json), writing nothing to production.

Each case is a stored figure verified by the checks (about 0.7% of them are wrong, by the Phase 0 audit), re-found in
today's parse of its filing, with a wrong answer planted beside it:
- review: the figure's cell against the year-ago column of its row (or a neighbouring row), as A and B in an order
  fixed by the case: the right pick scores, "none" or the planted cell does not.
- explain: steady figures given a planted near-doubling (or halving) that the document cannot explain, which must be
  refused, and genuine explained changes (the stored ones), which should stand.
- select: long documents and the tables their verified figures came from, which Select should keep.
Each document is one call per prompt, as in production: its cases are asked together.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from . import ai
from .ai import AiResponseError, Spec
from .control import ControlPlane
from .document import Document, clean, parse_document, parse_number
from .extract import _row_label, describe, explanation_supports, previous_from_jump
from .sec import company_profile, configure_identity
from .steps.ai_reading import MAX_DOCUMENT_CHARS, AiReading
from .steps.shared import _document_text

CASES = Path("evals/prompts.json")
_FETCH = Lock()


def _fetch(url: str) -> bytes:
    with _FETCH:  # one SEC request at a time, well under its rate limit
        request = urllib.request.Request(url, headers={"User-Agent": os.environ.get("SEC_USER_AGENT", "TradeTracker")})
        for attempt in range(4):
            try:
                body = urllib.request.urlopen(request, timeout=120).read()
                time.sleep(0.15)
                return body
            except Exception:  # noqa: BLE001
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


class Runner:
    def __init__(self, plane: ControlPlane):
        self.plane = plane
        self.documents: dict[tuple[str, str], Document] = {}
        self.names: dict[str, str] = {}
        self.calls = 0

    def document(self, url: str, prefix: str) -> Document:
        if (url, prefix) not in self.documents:
            self.documents[(url, prefix)] = parse_document(_fetch(url), url, prefix=prefix)
        return self.documents[(url, prefix)]

    def company(self, symbol: str) -> str:
        if symbol not in self.names:
            try:
                self.names[symbol] = f"{company_profile(symbol)[0].name} ({symbol})"
            except Exception:  # noqa: BLE001
                self.names[symbol] = symbol
        return self.names[symbol]

    def ask(self, symbol: str, purpose: str, text: str, schema, thinking_first: bool):
        """As the pipeline asks (AiReading._ask): a second, reasoning try when the first answer does not parse."""
        error = None
        for thinking in (thinking_first, True):
            answer = self.plane.ai(symbol, purpose, ai.SYSTEM, text, thinking, "flash", prompt_version=ai.prompt_version(purpose))
            self.calls += 1
            try:
                return ai.parse(answer, schema)
            except AiResponseError as failure:
                error = failure
        raise error

    # --- review -------------------------------------------------------------------------------------------------
    def review(self, cases: list[dict]) -> list[dict]:
        document = self.document(cases[0]["url"], cases[0]["prefix"])
        lines, keys, outcomes = [], {}, []
        for index, case in enumerate(cases):
            table = document.tables[case["table"]]
            sides = []
            for row, col in (case["right"], case["wrong"]):
                cell = parse_number(table.rows[row][col])
                locator = {"table": table.id, "row": row, "col": col, "row_label": _row_label(table.rows[row], col)[:80]}
                sides.append(SimpleNamespace(locator=locator, value=(cell.value if cell else 0) * case["scale"]))
            right_first = int(hashlib.md5(f"{case['symbol']}{case['period_end']}{case['kpi']['key']}".encode()).hexdigest(), 16) % 2 == 0
            a, b = sides if right_first else sides[::-1]
            key = f"{case['kpi']['group']}.{case['kpi']['key']}#{index}"
            keys[key] = (case, "A" if right_first else "B")
            lines.append(f"- {key} ({case['kpi']['group_label']} / {case['kpi']['label']}): A = {describe(document, a)}; "
                         f"B = {describe(document, b)}")
        pointed = {case["table"] for case in cases}
        text = ai.prompt(self.company(cases[0]["symbol"]), document.render_ids(pointed, MAX_DOCUMENT_CHARS, opening=3),
                         ai.REVIEW_TASK, "Disputed KPIs:\n" + "\n".join(lines))
        answer = self.ask(cases[0]["symbol"], "review", text, ai.Review, thinking_first=True)
        for key, (case, right) in keys.items():
            choice = answer.choices.get(key, "none")
            outcome = "right" if choice == right else "none" if choice == "none" else "planted"
            outcomes.append({"symbol": case["symbol"], "period_end": case["period_end"], "kpi": case["kpi"]["label"],
                             "wrong_kind": case["wrong_kind"], "outcome": outcome, "reason": answer.reasons.get(key)})
        return outcomes

    # --- explain ------------------------------------------------------------------------------------------------
    def explain(self, cases: list[dict]) -> list[dict]:
        document = self.document(cases[0]["url"], cases[0]["prefix"])
        if document.fits(MAX_DOCUMENT_CHARS):
            body = document.render()
        else:
            body = document.render_ids({case.get("table") or case.get("block") for case in cases} - {None}, MAX_DOCUMENT_CHARS, opening=6)
        keys, lines = {}, []
        for index, case in enumerate(cases):
            key = f"{case['kpi']['group']}.{case['kpi']['key']}#{index}"
            keys[key] = case
            previous = previous_from_jump(case["jump"], case["value"])
            shown = f", previous {previous:.4g}" if previous is not None else ""
            lines.append(f"- {key} ({case['kpi']['group_label']} / {case['kpi']['label']}): now {case['value']:g}{shown}; {case['jump']}")
        text = ai.prompt(self.company(cases[0]["symbol"]), body, ai.EXPLAIN_TASK, "\n".join(lines))
        answer = self.ask(cases[0]["symbol"], "explain", text, ai.Explanations, thinking_first=True)
        haystack = _document_text(document)
        outcomes = []
        for key, case in keys.items():
            verdict = answer.items.get(key)
            quote = clean(verdict.quote or "") if verdict else ""
            said = bool(verdict and verdict.legitimate)
            # What production keeps: a yes with a quote found in the document that supports the figure.
            previous = previous_from_jump(case["jump"], case["value"])
            stands = said and bool(quote) and quote in haystack and explanation_supports(quote, case["kpi"]["label"], case["value"], previous)
            outcomes.append({"symbol": case["symbol"], "period_end": case["period_end"], "kpi": case["kpi"]["label"],
                             "planted": case["planted"], "said_real": said, "stands": stands, "quote": quote[:160],
                             "growth": "growth" in case["kpi"]["label"].lower()})
        return outcomes

    # --- select -------------------------------------------------------------------------------------------------
    def select(self, case: dict) -> list[dict]:
        document = self.document(case["url"], case["prefix"])
        try:
            spec = Spec.model_validate({"groups": case["groups"], "names": {}})
        except Exception:  # noqa: BLE001
            spec = Spec.model_construct(groups=[ai.GroupSpec.model_construct(**g, kpis=[ai.KpiSpec.model_construct(**k) for k in g["kpis"]])
                                                for g in case["groups"]], names={})
        text = ai.prompt(self.company(case["symbol"]), document.outline(), ai.SELECT_TASK, f"KPIs:\n{ai.kpi_lines(spec)}")
        answer = self.ask(case["symbol"], "select", text, ai.Selection, thinking_first=False)
        chosen = {item for item in answer.ids if item in document.tables or item in document.blocks}
        # Production adds the tables with a row named for a listed KPI (no history here, so labels only).
        stub = SimpleNamespace(_ai_history=lambda _day: {})
        named = AiReading._sections_naming_kpis(stub, document, spec)
        needed = set(case["needed"])
        return [{"symbol": case["symbol"], "url": case["url"], "needed": len(needed), "select_kept": len(needed & chosen),
                 "with_names_kept": len(needed & (chosen | named)), "chosen": len(chosen),
                 "missed": sorted(needed - (chosen | named))}]


def summarize(results: dict[str, list[dict]]) -> dict:
    review, explain, select = results.get("review", []), results.get("explain", []), results.get("select", [])
    by_kind = defaultdict(Counter)
    for item in review:
        by_kind[item["wrong_kind"]][item["outcome"]] += 1
    planted = [item for item in explain if item["planted"] and not item.get("growth")]
    growth = [item for item in explain if item["planted"] and item.get("growth")]
    genuine = [item for item in explain if not item["planted"]]
    needed = sum(item["needed"] for item in select)
    return {
        "review": {"cases": len(review), "right": sum(1 for i in review if i["outcome"] == "right"),
                   "planted": sum(1 for i in review if i["outcome"] == "planted"),
                   "none": sum(1 for i in review if i["outcome"] == "none"), "byKind": {k: dict(v) for k, v in by_kind.items()}},
        "explain": {"planted": len(planted), "plantedSaidReal": sum(1 for i in planted if i["said_real"]),
                    "plantedStands": sum(1 for i in planted if i["stands"]), "genuine": len(genuine),
                    "genuineStands": sum(1 for i in genuine if i["stands"]),
                    "growthPlanted": len(growth), "growthPlantedStands": sum(1 for i in growth if i["stands"])},
        "select": {"documents": len(select), "tablesNeeded": needed, "selectKept": sum(i["select_kept"] for i in select),
                   "withNamesKept": sum(i["with_names_kept"] for i in select),
                   "documentsComplete": sum(1 for i in select if i["with_names_kept"] == i["needed"])},
    }


def lines_of(summary: dict, calls: int) -> list[str]:
    r, e, s = summary["review"], summary["explain"], summary["select"]
    pct = lambda a, b: f"{a / b:.0%}" if b else "—"
    out = [f"Prompt scores; AI calls {calls}", "",
           f"Review: {r['right']}/{r['cases']} right ({pct(r['right'], r['cases'])}), {r['planted']} took the planted cell, {r['none']} kept neither"]
    out += [f"  {kind}: {dict(counts)}" for kind, counts in r["byKind"].items()]
    out += [f"Explain: planted false jumps passed {e['plantedStands']}/{e['planted']} ({pct(e['plantedStands'], e['planted'])}); "
            f"said real before the quote checks {e['plantedSaidReal']}; genuine changes kept {e['genuineStands']}/{e['genuine']}; "
            f"growth-rate KPIs (a planted jump may be real there) passed {e['growthPlantedStands']}/{e['growthPlanted']}",
            f"Select: kept {s['selectKept']}/{s['tablesNeeded']} needed tables alone, {s['withNamesKept']} with the KPI-name rule; "
            f"{s['documentsComplete']}/{s['documents']} documents complete"]
    return out


def main(argv: list[str] | None = None) -> int:
    configure_identity()
    cases = json.loads(CASES.read_text())
    plane = ControlPlane(os.environ["BUSINESS_KPI_WORKFLOW_URL"])
    symbols = sorted({case["symbol"] for case in cases})
    started = plane.call("start", runId=None, triggerType="eval", symbols=symbols, quarters=1, forceRefresh=False)
    plane.run_id = started["runId"]
    runner = Runner(plane)
    jobs = []
    for purpose in ("review", "explain"):
        grouped = defaultdict(list)
        for case in cases:
            if case["purpose"] == purpose:
                grouped[(case["url"], case["prefix"])].append(case)
        for batch in grouped.values():
            for start in range(0, len(batch), 12):  # as many as a filing's flagged values, at most
                jobs.append((purpose, batch[start:start + 12]))
    jobs += [("select", case) for case in cases if case["purpose"] == "select"]
    results: dict[str, list[dict]] = defaultdict(list)
    failures = Counter()

    def run(job):
        purpose, work = job
        try:
            return purpose, getattr(runner, purpose)(work)
        except Exception as error:  # noqa: BLE001
            failures[f"{purpose}: {type(error).__name__}"] += 1
            print(f"{purpose} failed: {error}"[:300], flush=True)
            return purpose, []

    with ThreadPoolExecutor(4) as pool:
        for purpose, outcomes in pool.map(run, jobs):
            results[purpose].extend(outcomes)
    summary = summarize(results)
    summary["failures"] = dict(failures)
    plane.call("finish", summary={"prompts": summary, "aiCalls": runner.calls,
                                  "promptVersions": {p: ai.prompt_version(p) for p in ("review", "explain", "select")}})
    text = "\n".join(lines_of(summary, runner.calls) + ([f"Failures: {dict(failures)}"] if failures else []))
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(text + "\n")
    Path("eval-results").mkdir(exist_ok=True)
    Path("eval-results/prompts.json").write_text(json.dumps({"summary": summary, "results": results}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
