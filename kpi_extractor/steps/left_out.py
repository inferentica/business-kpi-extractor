"""What a company's KPI list left out, and why: shown on Quotiva Review's company page with Add back (p4-leftout).

Three sources, newest first, one entry per line:
- propose: measures Propose saw in the documents and chose not to list, with its reason ("capital ratio");
- rule / audit: lines the extractor's rules took off a list (the size cap, a repeat of an XBRL breakdown, an audit
  addition that broke a rule) and lines the quarterly audit retired as no longer reported;
- owner: lines the owner removed in Quotiva Review, recorded by public.review_list_apply, never here.
A line the list holds again (Add back, or a later proposal) is never shown as left out."""
from __future__ import annotations

import re
from datetime import date

from ..ai import Spec

REASON_CAP = "over the list's size cap"
REASON_XBRL = "repeats an XBRL breakdown"
REASON_RETIRED = "no longer reported"
REASON_NOT_ADDED = "the audit's addition broke a list rule"
MAX_LEFT_OUT = 60


def _today() -> str:
    return date.today().isoformat()


def _plain(label: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", label.lower().replace("&", " and ")))


def _key(label: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", label.lower())).strip("_")[:40] or "metric"


def entry(group, kpi, reason: str, source: str, at: str | None = None) -> dict:
    """One left-out line from a list group and KPI (GroupSpec, KpiSpec)."""
    return {"group": group.key, "groupLabel": group.label, "kind": group.kind, "key": kpi.key, "label": kpi.label,
            "unit": kpi.unit, "reason": reason, "source": source, "at": at or _today()}


def dropped(before: Spec | None, after: Spec | None, reason: str, source: str = "rule") -> list[dict]:
    """The lines `before` lists that `after` does not."""
    if before is None or after is None:
        return []
    kept = {(group.key, kpi.key) for group in after.groups for kpi in group.kpis}
    return [entry(group, kpi, reason, source) for group in before.groups for kpi in group.kpis
            if (group.key, kpi.key) not in kept]


def from_propose(spec: Spec) -> list[dict]:
    """What Propose's reply says it saw and left out, placed in the group it names when the list has it."""
    groups = {group.key: group for group in spec.groups}
    lines = []
    for item in spec.left_out:
        group = groups.get(item.group or "")
        lines.append({"group": item.group or None, "groupLabel": group.label if group else None,
                      "kind": group.kind if group else None, "key": _key(item.label), "label": item.label,
                      "unit": item.unit, "reason": item.reason, "source": "propose", "at": _today()})
    return lines


def merge(new: list[dict], previous: list[dict] | None, spec: Spec) -> list[dict]:
    """The list's left-out lines: this run's first, then those stored before, each once, none the list holds now."""
    listed = {(group.key, kpi.key) for group in spec.groups for kpi in group.kpis}
    listed_labels = {_plain(kpi.label) for group in spec.groups for kpi in group.kpis}
    seen: set = set()
    lines = []
    for line in [*new, *(previous or [])]:
        if not isinstance(line, dict) or not line.get("label"):
            continue
        place, label = (line.get("group"), line.get("key")), _plain(str(line["label"]))
        if place in listed or label in listed_labels or place in seen or label in seen:
            continue
        seen.update({place, label})
        lines.append(line)
    return lines[:MAX_LEFT_OUT]
