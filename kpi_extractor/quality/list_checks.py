"""Automatic checks on a company's first KPI list (p4-listchecks): a list that passes reads its whole history at once; a
flagged one reads only its newest quarters, and its flags show on Quotiva Review until the owner accepts or edits it.
The owner reviews only flagged lists, never every company's (2026-11-18).

Two checks, each from evidence a wave showed:
- unread lines: its newest quarters found no figure for many of its lines (Caterpillar's wave 3 list, 9 of 14);
- a sector line the filing names but the list lacks: a bank's CET1 ratio, net interest margin or efficiency ratio, an
  insurer's combined, loss, expense or medical cost ratio, an asset manager's fee-related earnings (the owner keeps
  these, 2026-11-17), asked only where the document Propose read names it."""
from __future__ import annotations

import re

FIRST_QUARTERS = 4  # a new list's newest quarters, read before its checks decide the rest
UNREAD_SHARE = 0.4
MIN_LINES = 2

SECTOR_LINES: dict[str, tuple[tuple[str, str], ...]] = {
    "bank": (("the CET1 ratio", r"\bCET1\b|common equity tier 1"), ("the net interest margin", r"net interest margin"),
             ("the efficiency ratio", r"efficiency ratio")),
    "insurance": (("the combined ratio", r"combined ratio"), ("the loss ratio", r"\bloss ratio"),
                  ("the expense ratio", r"expense ratio"), ("the medical cost ratio", r"medical (?:care |cost |loss )ratio")),
    "asset_manager": (("fee-related earnings", r"fee[- ]related earnings"),),
}


def _labels(spec) -> list[str]:
    return [kpi.label for group in spec.groups for kpi in group.kpis]


def sector_lines_missing(sector_key: str | None, text: str, spec) -> list[dict]:
    """A flag for each line the company's sector keeps that its document names and its list does not."""
    labels = " | ".join(_labels(spec))
    flags = []
    for name, pattern in SECTOR_LINES.get(sector_key or "", ()):
        if re.search(pattern, text or "", re.I) and not re.search(pattern, labels, re.I):
            flags.append({"check": "sector_line", "text": f"The filing gives {name}; the list has no line for it.",
                          "lines": []})
    return flags


def unread_lines(spec, values, quarters: set[tuple[str, str]]) -> dict | None:
    """A flag when the newest quarters found no figure for a share of the list's lines (stored rows: group_key, kpi_key,
    fiscal_year, fiscal_period, validation_status)."""
    lines = [(f"kpi_{group.key}", kpi.key, kpi.label) for group in spec.groups for kpi in group.kpis
             if kpi.key != group.total_kpi]
    found = {(v["group_key"], v["kpi_key"]) for v in values
             if v["validation_status"] == "verified" and (v["fiscal_year"], v["fiscal_period"]) in quarters}
    unread = [label for group, key, label in lines if (group, key) not in found]
    if len(lines) < MIN_LINES or len(unread) < len(lines) * UNREAD_SHARE:
        return None
    return {"check": "unread_lines", "lines": unread[:60],
            "text": f"Its newest {len(quarters)} quarters found no figure for {len(unread)} of its {len(lines)} lines."}
