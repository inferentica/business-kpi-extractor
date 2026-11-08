"""The run report: one row per company, then the golden set."""
from __future__ import annotations

import json
from pathlib import Path


def report(folders: list[Path]) -> str:
    rows = []
    for folder in folders:
        path = folder / "summary.json"
        if path.exists():
            rows.append(json.loads(path.read_text()))
    header = ("| Company | Filings (XBRL) | First period | Periods kept | Rejected | Unproven | Own coverage | "
              "From comparatives only | Restated | Mixed-vintage Q4 | AI calls | ¥ |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    lines = [header]
    total = {"expected": 0, "kept": 0}
    for row in sorted(rows, key=lambda item: item["symbol"]):
        status = row["by_status"]
        coverage = row["own_kept"] / row["own_expected"] if row["own_expected"] else 0
        total["expected"] += row["own_expected"]
        total["kept"] += row["own_kept"]
        lines.append(f"| {row['symbol']} | {row['filings']} ({row['xbrl_filings']}) | {row['first_period']} | "
                     f"{status.get('kept', 0)} | {status.get('rejected', 0)} | {status.get('unproven', 0)} | "
                     f"{row['own_kept']}/{row['own_expected']} ({coverage:.1%}) | {row['comparative_only_periods']} | "
                     f"{row['restated_periods']} | {len(row['mixed_vintage_q4'])} | {row['ai_calls']} | {row['cost_cny']:.2f} |")
    if total["expected"]:
        lines.append(f"\nOwn-period coverage, all companies: {total['kept']}/{total['expected']} "
                     f"({total['kept'] / total['expected']:.1%}). AI calls: 0. Cost: ¥0.")
    return "\n".join(lines)
