"""Scores a run's chosen statements against the golden set (evals/golden.json): values checked against the printed
filings, across eras and kinds of company. Each entry names a period and a line (by concept for an XBRL-era entry,
else by a pattern on its label) and the value the newest filing presenting that period prints. An entry marked
"absent" names a line that must not be served.

correct: the served figure equals the printed one to its printed precision, sign included.
wrong: a figure is served and differs.
missing: the period is not kept, or no line matches.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

def _golden_path() -> Path:
    """The golden set beside the package (an editable install), else in the checkout it runs from."""
    for path in (Path(__file__).resolve().parents[2] / "evals" / "golden.json", Path.cwd() / "statements" / "evals" / "golden.json",
                 Path.cwd() / "evals" / "golden.json"):
        if path.exists():
            return path
    return Path.cwd() / "statements" / "evals" / "golden.json"


GOLDEN = _golden_path()


def _key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", label.lower().replace("’", "'")).strip()


def _line(entry: dict, lines: list[dict]) -> dict | None:
    candidates = [line for line in lines if not line["is_heading"] and line["value"] is not None]
    if entry.get("concept"):
        same = [line for line in candidates if line["concept"] == entry["concept"] and line.get("member") == entry.get("member")]
        # Opening and closing cash share a concept (Costco): the line printed with the entry's label is the one.
        found = next((line for line in same if _key(line["label"]) == _key(entry["label"])), same[0] if same else None)
        if found is not None:
            return found
        if entry.get("era") == "xbrl":
            # Served from the filing's printed statement (its XBRL failed its checks: PepsiCo's FY2011 equity), the
            # line is the one printed with the entry's label.
            wording = {_key(entry["label"]), _key(entry.get("printed_label") or entry["label"])}
            return next((line for line in candidates if _key(line["label"]) in wording
                         or entry.get("printed_label") and _key(line["label"]).startswith(_key(entry["printed_label"]))), None)
    pattern = re.compile(entry["label"] if not entry.get("concept") else re.escape(entry["label"]), re.I)
    return next((line for line in candidates if pattern.search(line["label"].replace("’", "'"))), None)


def score(folders: list[Path], golden: Path = GOLDEN) -> dict:
    entries = json.loads(golden.read_text())["entries"]
    chosen: dict[str, dict] = {}
    for folder in folders:
        path = folder / "chosen.json"
        if path.exists():
            chosen[folder.name] = {(item["statement"], item["fiscal_year"], item["fiscal_period"], item["duration"]): item
                                   for item in json.loads(path.read_text()) if item["status"] == "kept"}
    results = []
    for entry in entries:
        if entry["symbol"] not in chosen:
            continue
        period = chosen[entry["symbol"]].get((entry["statement"], entry["fiscal_year"], entry["fiscal_period"], entry["duration"]))
        line = _line(entry, period["lines"]) if period else None
        if entry.get("absent"):
            outcome = "correct" if line is None else "wrong"
        elif line is None:
            outcome = "missing"
        else:
            tolerance = 0.5 * float(entry.get("scale") or 1) + 1e-9
            outcome = "correct" if abs(line["value"] - entry["value"]) <= tolerance else "wrong"
        results.append({**entry, "outcome": outcome, "served": line["value"] if line else None})
    return {"results": results, "summary": _summary(results)}


def _summary(results: list[dict]) -> dict:
    by: dict[str, Counter] = defaultdict(Counter)
    for result in results:
        by[f"{result['symbol']}"][result["outcome"]] += 1
        by[f"era {result['era']}"][result["outcome"]] += 1
        by["all"][result["outcome"]] += 1
    return {key: dict(counts) for key, counts in by.items()}


def report_text(scored: dict) -> str:
    summary = scored["summary"]
    lines = ["| Golden set | Correct | Wrong | Missing |", "|---|---|---|---|"]
    keys = sorted((key for key in summary if not key.startswith("era") and key != "all"))
    keys += sorted(key for key in summary if key.startswith("era")) + ["all"]
    for key in keys:
        counts = summary.get(key, {})
        lines.append(f"| {key} | {counts.get('correct', 0)} | {counts.get('wrong', 0)} | {counts.get('missing', 0)} |")
    wrong = [result for result in scored["results"] if result["outcome"] == "wrong"]
    for result in wrong[:20]:
        lines.append(f"\nWrong: {result['symbol']} {result['statement']} {result['fiscal_year']} {result['fiscal_period']} "
                     f"{result['label'][:60]}: served {result['served']}, printed {result['value']}"
                     + (" (must not be served)" if result.get("absent") else ""))
    return "\n".join(lines)
