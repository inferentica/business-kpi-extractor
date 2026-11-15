"""The IFRS taxonomy's statements as its presentation roles lay them out: which elements each statement shows, in
what order and at what depth. A filing whose XBRL carries no presentation of its own (CMF's, BMV's under CNBV's
template) is read through them. Fetched on the runner into the run's cache, never kept in the repository: the IFRS
Foundation limits the files' reproduction (domain/ifrs_labels.py)."""
from __future__ import annotations

import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

VERSION = "2024-03-27"
_BASE = "https://xbrl.ifrs.org/taxonomy/{v}/full_ifrs/linkbases/{standard}/pre_{standard}_{v}_role-{role}.xml"
# Each statement's roles: the balance sheet by current and non-current or by liquidity, the income statement by
# function or by nature of expense, the cash flows by the direct or the indirect method.
ROLES = {"balance": (("ias_1", "210000"), ("ias_1", "220000")),
         "income": (("ias_1", "310000"), ("ias_1", "320000")),
         "cash_flow": (("ias_7", "510000"), ("ias_7", "520000"))}
_XLINK = "{http://www.w3.org/1999/xlink}"


def parse_role(content: bytes) -> list[tuple[str, int]]:
    """(element name without "ifrs-full_", depth) in the role's order: a depth-first walk of its arcs by their order."""
    root = ET.fromstring(content)
    located, children, has_parent = {}, {}, set()
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "loc":
            located[element.get(_XLINK + "label")] = element.get(_XLINK + "href", "").rsplit("#", 1)[-1]
        elif tag == "presentationArc":
            parent, child = element.get(_XLINK + "from"), element.get(_XLINK + "to")
            children.setdefault(parent, []).append((float(element.get("order") or 0), child))
            has_parent.add(child)
    found: list[tuple[str, int]] = []

    def walk(label: str, depth: int) -> None:
        found.append((located.get(label, label).removeprefix("ifrs-full_"), depth))
        for _order, child in sorted(children.get(label, [])):
            walk(child, depth + 1)

    for top in [label for label in children if label not in has_parent]:
        walk(top, 0)
    return found


def statement_roles() -> dict[str, list[list[tuple[str, int]]]]:
    """Each statement's roles' elements; empty where they cannot be read."""
    folder = os.environ.get("STATEMENTS_ARCHIVE_CACHE")
    kept = Path(folder, "regulators", f"ifrs-roles-{VERSION}.json") if folder else None
    if kept is not None and kept.exists():
        return json.loads(kept.read_text())
    roles: dict[str, list[list[tuple[str, int]]]] = {}
    for statement, parts in ROLES.items():
        for standard, role in parts:
            url = _BASE.format(v=VERSION, standard=standard, role=role)
            request = urllib.request.Request(url, headers={"User-Agent": "TradeTracker statements"})
            with urllib.request.urlopen(request, timeout=120) as response:
                roles.setdefault(statement, []).append(parse_role(response.read()))
    if kept is not None:
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(json.dumps(roles))
    return roles
