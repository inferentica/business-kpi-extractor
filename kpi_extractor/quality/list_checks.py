"""Automatic checks on a company's first KPI list (p4-listchecks). They never hold extraction back: every list reads its
whole history, and what the checks find goes to the list's Left out on Quotiva Review, where the owner adds back what
they want (owner, 2026-11-18: a list waiting for the owner blocks extraction, which defeats the point).

- Lines its newest filings never report: none of the newest four filings has the line. Each reading this run gives
  the lines it found no line for, in full; a filing read before gives its notes. A line a reader found and a check
  refused is in the filing and stays (Caterpillar's Sales by Application, refused in 2026-11 for a header misread).
  The lines found missing come off the list before the history is read.
- A sector line the filing names but the list lacks: a bank's CET1 ratio, net interest margin or efficiency ratio, an
  insurer's combined, loss, expense or medical cost ratio, an asset manager's fee-related earnings (the owner keeps
  these, 2026-11-17), asked only where the document Propose read names it."""
from __future__ import annotations

import re
from datetime import date

_NOT_REPORTED = "not reported: "
_REFUSED = re.compile(r"(?:[a-z0-9]+ )?([a-z0-9_]+\.[a-z0-9_]+): ")  # "flash group.kpi: why", "group.kpi: why"
FIRST_QUARTERS = 4  # a first list's newest quarters, read before its unread lines are taken off
REASON_UNREAD = "not in its newest four filings"
REASON_SECTOR = "the filing gives it; its sector keeps it"

SECTOR_LINES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "bank": (("CET1 ratio", r"\bCET1\b|common equity tier 1", "percent"), ("Net interest margin", r"net interest margin", "percent"),
             ("Efficiency ratio", r"efficiency ratio", "percent")),
    "insurance": (("Combined ratio", r"combined ratio", "percent"), ("Loss ratio", r"\bloss ratio", "percent"),
                  ("Expense ratio", r"expense ratio", "percent"),
                  ("Medical cost ratio", r"medical (?:care |cost |loss )ratio", "percent")),
    "asset_manager": (("Fee-related earnings", r"fee[- ]related earnings", "currency"),),
}


def _key(label: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", label.lower())).strip("_")


def sector_lines_missing(sector_key: str | None, text: str, spec) -> list[dict]:
    """Left-out lines for what the company's sector keeps, its document names, and its list does not have."""
    labels = " | ".join(kpi.label for group in spec.groups for kpi in group.kpis)
    return [{"group": None, "groupLabel": None, "kind": None, "key": _key(name), "label": name, "unit": unit,
             "reason": REASON_SECTOR, "source": "rule", "at": date.today().isoformat()}
            for name, pattern, unit in SECTOR_LINES.get(sector_key or "", ())
            if re.search(pattern, text or "", re.I) and not re.search(pattern, labels, re.I)]


def refused_lines(problems: list[str]) -> set[str]:
    """The lines ("group.kpi") a reader found and a check refused, from a reading's problems ("flash
    revenue_by_application.power_generation: column headed 2025, ..."): the filing gives them."""
    return {match.group(1) for problem in problems if (match := _REFUSED.match(str(problem)))}


def noted_unreported(notes: list[str] | None) -> set[str] | None:
    """A filing read before this run: the lines its notes give as not reported and no reader found, or None when
    nothing read it. The note is cut at 300 characters, so a line cut off it counts as found."""
    notes = [str(note) for note in notes or []]
    if not any(note.startswith(("read by", _NOT_REPORTED)) for note in notes):
        return None
    noted = next((note[len(_NOT_REPORTED):] for note in notes if note.startswith(_NOT_REPORTED)), "")
    return set(noted.split(", ")) - {""} - refused_lines(notes)


def unread_lines(spec, readings: list[set[str] | frozenset[str] | None]) -> set[tuple[str, str]]:
    """(group, kpi) of the list's lines none of the newest filings has: each filing read gives the lines it found no
    line for (a line a check refused is found), None for a filing nothing read. Stored figures are no measure: a line
    read from the row of a stored series is kept under that series' key (Caterpillar's retail sales growth, T-Mobile's
    postpaid accounts)."""
    read = [keys for keys in readings if keys is not None]
    if not read:
        return set()
    return {(group.key, kpi.key) for group in spec.groups for kpi in group.kpis
            if kpi.key != group.total_kpi and all(f"{group.key}.{kpi.key}" in keys for keys in read)}
