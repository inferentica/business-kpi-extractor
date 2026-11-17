"""Fresh KPI lists (Propose's, as for a new company) scored against the lists the owner edited by hand.

Counted one to one: each fresh line names at most one owner line and the reverse, by the same label first, then by
labels that agree both ways. Before 2026-11-16 one fresh line could name many (JPMorgan's 21 named 41 of 52), which
put recall at 72% where it was about half. Recall counts only the owner's lines the company still reports (a figure
in the newest quarter its list holds); lines the company stopped reporting are history no draft from the latest
release can name, and are counted apart. A line the same run serves from XBRL is covered, whatever the list says."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from ..readers.extract import labels_agree

REMOVED = Path("evals/removed_groups.json")  # groups the owner took off lists Propose wrote (2026-10)
# A line is current when its newest figure is within this many days of the newest figure its list holds.
CURRENT_DAYS = 100
_UNIT_WORDS = re.compile(r"\((?:in )?(?:thousands|millions|billions|%|percent|\$[^)]*|[a-z ]*)\)|\b(?:in )?(?:thousands|millions|billions)\b", re.I)


def _kpis(groups: list[dict]) -> list[tuple[str, str, str]]:
    """Each listed line but a group's total: its group key, key and label without unit words."""
    return [(str(group.get("key") or ""), str(kpi.get("key") or ""), label) for group in groups or [] for kpi in group.get("kpis") or []
            if not (group.get("total_kpi") and kpi.get("key") == group.get("total_kpi"))
            and (label := _UNIT_WORDS.sub("", str(kpi.get("label") or "")).strip())]


_NANOMETER = re.compile(r"(\d)\s*-?\s*nanometers?\b")


def _norm(label: str) -> str:
    # "7-nanometer" is "7nm" (TSMC's nodes, written either way by the same model on two runs).
    return " ".join(re.findall(r"[a-z0-9]+", _NANOMETER.sub(r"\1nm", label.lower()).replace("&", " and ")))


def _same_kpi(a: str, b: str) -> bool:
    # Labels written differently for the same KPI ("Non-strategic" and "Non-strategic revenue", "(in thousands)").
    return labels_agree(a, b) and labels_agree(b, a)


def match_one_to_one(owner: list[str], fresh: list[str]) -> dict[int, int]:
    """Owner line index -> fresh line index: the same label first, then labels that agree both ways, each line once."""
    matched: dict[int, int] = {}
    used: set[int] = set()
    for test in (lambda a, b: _norm(a) == _norm(b), _same_kpi):
        for i, label in enumerate(owner):
            if i in matched:
                continue
            j = next((j for j, other in enumerate(fresh) if j not in used and test(label, other)), None)
            if j is not None:
                matched[i], used = j, used | {j}
    return matched


def current_lines(spec: dict, values: list[dict] | None) -> set[tuple[str, str]] | None:
    """The owner's lines the company still reports: a verified figure within CURRENT_DAYS of the newest figure any of
    the list's lines holds. None when the figures are not known (every line then counts)."""
    if not values:
        return None
    listed = {(group, key) for group, key, _label in _kpis(spec.get("groups"))}
    newest: dict[tuple[str, str], date] = {}
    for value in values:
        line = (str(value.get("group_key") or "").removeprefix("kpi_"), str(value.get("kpi_key") or ""))
        if line in listed and value.get("validation_status") == "verified" and value.get("period_end"):
            end = date.fromisoformat(str(value["period_end"])[:10])
            newest[line] = max(end, newest.get(line, end))
    if not newest:
        return None
    latest = max(newest.values())
    return {line for line, end in newest.items() if (latest - end).days <= CURRENT_DAYS}


def score_lists(fresh: dict[str, dict], owner: dict[str, dict], captured: dict[str, list[dict]] | None = None) -> dict:
    """Propose scored against the lists the owner edited by hand (see the module's note). `owner` may carry each
    company's served figures under "values"; `captured` is what the run itself read, for lines it serves from XBRL."""
    rows, totals = {}, {"current": 0, "named": 0, "fromXbrl": 0, "history": 0, "fresh": 0, "kept": 0, "ownerAll": 0, "namedAll": 0}
    for symbol, spec in owner.items():
        if symbol not in fresh:
            continue
        mine, theirs = _kpis(spec.get("groups")), _kpis(fresh[symbol].get("groups"))
        matched = match_one_to_one([label for _g, _k, label in mine], [label for _g, _k, label in theirs])
        current = current_lines(spec, spec.get("values"))
        is_current = [current is None or (group, key) in current for group, key, _label in mine]
        # Lines the run serves from XBRL (Adobe's customer groups, Unity's segments) are covered whatever the list says.
        xbrl = [str(v.get("kpi_label") or "") for v in (captured or {}).get(symbol, [])
                if v.get("method") == "xbrl" and v.get("validation_status") == "verified"]
        xbrl = list(dict.fromkeys(xbrl))
        left = [i for i, ok in enumerate(is_current) if ok and i not in matched]
        from_xbrl = match_one_to_one([mine[i][2] for i in left], xbrl)
        covered_by_xbrl = {left[i] for i in from_xbrl}
        named = [i for i, ok in enumerate(is_current) if ok and i in matched]
        row = {"owner": len(mine), "current": sum(is_current), "history": len(mine) - sum(is_current), "fresh": len(theirs),
               "named": len(named), "fromXbrl": len(covered_by_xbrl), "kept": len(matched),
               "notNamed": [mine[i][2] for i, ok in enumerate(is_current) if ok and i not in matched and i not in covered_by_xbrl][:12],
               "notKept": [theirs[j][2] for j in range(len(theirs)) if j not in set(matched.values())][:12]}
        row["recall"] = (row["named"] + row["fromXbrl"]) / row["current"] if row["current"] else None
        row["precision"] = row["kept"] / row["fresh"] if row["fresh"] else None
        rows[symbol] = row
        for key in ("current", "named", "fromXbrl", "history", "fresh", "kept"):
            totals[key] += row[key]
        totals["ownerAll"] += len(mine)
        totals["namedAll"] += len(matched)
    return {"companies": len(rows),
            "recall": (totals["named"] + totals["fromXbrl"]) / totals["current"] if totals["current"] else None,
            "precision": totals["kept"] / totals["fresh"] if totals["fresh"] else None,
            "recallAllLines": totals["namedAll"] / totals["ownerAll"] if totals["ownerAll"] else None,
            **{key: totals[key] for key in ("current", "named", "fromXbrl", "history", "fresh", "kept")},
            "byCompany": rows}


def _same_group(a: str, b: str) -> bool:
    # Group labels share generic words ("Revenue by ..."), so only the same name, or one inside the other, counts.
    x, y = re.sub(r"[^a-z0-9]+", "", a.lower()), re.sub(r"[^a-z0-9]+", "", b.lower())
    return bool(x and y) and (x in y or y in x)


def _lines_of_a_kept_group(group: dict, kept: list[dict]) -> bool:
    """Whether most of a fresh group's lines are the lines of one group the owner kept: the same breakdown under
    another title (UnitedHealth's "Revenue by Business Line" is its kept "Revenue by Customer Type")."""
    labels = [label for _g, _k, label in _kpis([group])]
    for other in kept:
        mine = [label for _g, _k, label in _kpis([other])]
        if labels and mine and 2 * len(match_one_to_one(mine, labels)) >= len(labels):
            return True
    return False


def proposed_again(fresh: dict[str, dict], owner: dict[str, dict], removed: list[dict]) -> list[str]:
    """Groups the owner removed that a fresh list names again: most of a removed group's KPIs, in a group that is not
    one the owner kept, by its title or by its lines."""
    again = []
    for gone in removed:
        spec = fresh.get(gone["symbol"])
        if not spec:
            continue
        kept = owner.get(gone["symbol"], {}).get("groups") or []
        for group in spec.get("groups") or []:
            if any(_same_group(group["label"], other["label"]) for other in kept) or _lines_of_a_kept_group(group, kept):
                continue
            shared = sum(1 for kpi in group.get("kpis") or [] if any(_same_kpi(kpi["label"], label) for label in gone["kpis"]))
            if shared >= max(2, (len(gone["kpis"]) + 1) // 2):
                again.append(f"{gone['symbol']}: {group['label']} (removed {gone['label']})")
    return again
