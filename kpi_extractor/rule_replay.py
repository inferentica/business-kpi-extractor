"""Replays a trial's readings with the code as it is now, with no AI: each figure the trial read is read again from its
own filing, at the cell or quote its reading pointed to, and every figure whose value or status would change is listed.

A rule change is tried here before any rerun: in seconds it shows what the change does to some two thousand figures the
AI already placed (the period rule turned away Blue Bird's "second quarter of fiscal 2026" bookings, found this way).

    python -m kpi_extractor.rule_replay eval-results/trial.json [--symbols MP,FDX] [--cache DIR] [--out replay.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass

from .ai import Located, Spec
from .audit_check import Fetcher
from .extract import SCALE_NOT_STATED, read_values
from .steps.shared import AI_GROUP_PREFIX

# Kinds the replay can read again one figure at a time; a reading of a whole breakdown table is replayed row by row.
_KINDS = {"metric": "metric", "revenue_breakdown": "metric", "mix": "metric"}


@dataclass
class Replayed:
    symbol: str
    kpi: str
    period: str
    before: float
    before_status: str
    after: float | None
    after_status: str | None
    why: str = ""

    unreplayable: bool = False

    @property
    def change(self) -> str:
        if self.unreplayable:
            return "unreplayable"
        if self.after is None:
            return "rejected"
        if abs(self.after - self.before) > max(abs(self.before) * 1e-9, 1e-9):
            return "value"
        if self.after_status != self.before_status:
            return "accepted" if self.after_status == "verified" else "held"
        return "same"


class CachedFetcher(Fetcher):
    """Filings fetched once and kept on disk, so a replay after every change costs no SEC requests."""

    def __init__(self, cache: str | None):
        super().__init__()
        self.cache = cache
        if cache:
            os.makedirs(cache, exist_ok=True)

    def html(self, url: str) -> str:
        if url in self.raw:
            return self.raw[url]
        path = os.path.join(self.cache, hashlib.md5(url.encode()).hexdigest() + ".htm") if self.cache else None
        if path and os.path.exists(path) and os.path.getsize(path) > 500:
            self.raw[url] = open(path, encoding="utf-8", errors="ignore").read()
            return self.raw[url]
        text = super().html(url)
        if path:
            open(path, "w", encoding="utf-8").write(text)
        return text


def _located(value: dict, locator: dict) -> tuple[Located, str] | None:
    """The one-figure reading a stored value's locator stands for, and its KPI's dotted key."""
    key = f"{value['group_key'].removeprefix(AI_GROUP_PREFIX)}.{value['kpi_key']}"
    if locator.get("table") and locator.get("row") is not None and locator.get("col") is not None:
        table = re.sub(r"^[A-Z]+_", "", str(locator["table"]))
        reading = {"kpi": key, "table": table, "row": int(locator["row"]), "col": int(locator["col"]),
                   "row_label": locator.get("row_label")}
        if locator.get("scale") is not None:
            reading["scale"] = float(locator["scale"])
        if locator.get("scale_text"):
            reading["scale_text"] = locator["scale_text"]
    elif locator.get("quote") and locator.get("block"):
        reading = {"kpi": key, "block": re.sub(r"^[A-Z]+_", "", str(locator["block"])), "quote": locator["quote"],
                   "value_text": locator.get("value_text") or ""}
    else:
        return None
    located = {"period_end": value["period_end"], "annual": value.get("fiscal_period") == "FY", "values": [reading]}
    return Located.model_validate(located), key


def replay_value(value: dict, fetcher: Fetcher) -> Replayed | None:
    """One stored figure read again; None when it has no single-figure locator (an XBRL tag, a derived figure)."""
    locator = value.get("locator") or {}
    if locator.get("tagged") or value.get("method") != "ai":
        return None
    found = _located(value, locator)
    if found is None:
        return None
    located, key = found
    group_key, kpi_key = key.split(".", 1)
    spec = Spec.model_validate({"groups": [{"key": group_key, "label": value.get("group_label") or group_key,
                                            "kind": _KINDS.get(value.get("group_kind") or "metric", "metric"),
                                            "kpis": [{"key": kpi_key, "label": value.get("kpi_label") or kpi_key,
                                                      "unit": value.get("unit") or "count"}]}]})
    before = Replayed(value["symbol"], f"{value.get('group_label')} / {value.get('kpi_label')}",
                      f"{value.get('fiscal_period')} {value.get('fiscal_year')}", float(value["value"]),
                      value["validation_status"], None, None)
    try:
        document = fetcher.document(value["source_url"])
        if located.values[0].table and located.values[0].table not in document.tables:
            before.why = f"table {located.values[0].table} not in the exhibit"
            return None
        items, problems = read_values(document, spec, located, value.get("currency"))
    except Exception as error:  # noqa: BLE001 - one figure's failure is a result, never the replay's end
        before.why = f"{type(error).__name__}: {error}"[:200]
        return before
    if not items:
        before.why = "; ".join(problems)[:200] or "no figure"
        # A quote from a slide's text or another exhibit of the filing is not in the one exhibit the replay opens.
        before.unreplayable = "quote not found in the document" in before.why
        return before
    item = items[0]
    before.after, before.after_status = float(item.value), item.status
    before.why = "; ".join(item.notes)[:200]
    # A scale the reading quoted beside its table (PNC's "$ in billions") won over the caption; readings stored before
    # 2026-11-08 do not keep that quote, so the caption wins in the replay. Not a change the code made.
    declared = re.search(r"table declares scale ([\d.e+]+), AI said ([\d.e+]+)", before.why)
    if declared and not locator.get("scale_text") and before.after:
        ratio = before.before / before.after
        if abs(ratio - float(declared.group(2)) / float(declared.group(1))) <= 1e-6 * abs(ratio):
            before.unreplayable = True
            before.why = "the reading's scale quote is not stored with the figure"
    # A scale the reading quoted is not kept with stored figures before 2026-11-08: such a figure comes back held for an
    # unstated scale only because the quote is missing, not because a rule changed.
    if before.change == "held" and SCALE_NOT_STATED in item.notes and not locator.get("scale_text"):
        before.after_status = before.before_status
        before.why = "scale quote not recorded with the figure"
    return before


def replay(values_by_symbol: dict[str, list[dict]], fetcher: Fetcher, symbols: set[str] | None = None,
           statuses: tuple[str, ...] = ("verified",)) -> list[Replayed]:
    results = []
    for symbol, values in sorted(values_by_symbol.items()):
        if symbols and symbol not in symbols:
            continue
        for value in values:
            if value.get("validation_status") not in statuses or not value["group_key"].startswith(AI_GROUP_PREFIX):
                continue
            outcome = replay_value({**value, "symbol": symbol}, fetcher)
            if outcome is not None:
                results.append(outcome)
    return results


def report(results: list[Replayed]) -> str:
    counts = Counter(result.change for result in results)
    lines = [f"Rule replay: {len(results)} figures read again — {counts['same']} unchanged, {counts['value']} value changed, "
             f"{counts['rejected']} now rejected, {counts['held']} now held, {counts['accepted']} now accepted; "
             f"{counts['unreplayable']} could not be replayed (quote outside the exhibit, or scale quote not stored)"]
    for result in results:
        if result.change in ("same", "unreplayable"):
            continue
        after = "—" if result.after is None else f"{result.after:,.6g} {result.after_status}"
        lines.append(f"  {result.change:9} {result.symbol} {result.period} {result.kpi[:60]}: {result.before:,.6g} "
                     f"{result.before_status} → {after}  ({result.why})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("trial", help="A trial.json (an Evaluate trial run's artifact)")
    parser.add_argument("--symbols", default="", help="Only these tickers")
    parser.add_argument("--cache", default=os.path.expanduser("~/.cache/kpi-extractor/filings"), help="Where filings are kept")
    parser.add_argument("--held", action="store_true", help="Replay held figures too, to see which a change would accept")
    parser.add_argument("--out", default="", help="Write every result as JSON here")
    args = parser.parse_args(argv)
    trial = json.load(open(args.trial))
    symbols = {s.strip().upper() for s in args.symbols.split(",") if s.strip()} or None
    statuses = ("verified", "needs_review") if args.held else ("verified",)
    results = replay(trial["values"], CachedFetcher(args.cache), symbols, statuses)
    print(report(results))
    if args.out:
        json.dump([result.__dict__ | {"change": result.change} for result in results], open(args.out, "w"), indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
