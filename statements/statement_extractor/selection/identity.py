"""One key per line across a company's eras: the same line under the same key whatever filing gave it (Phase 6).

Two presentations of one period that print the same figure on one line each name the same line: a filing's columns
for an earlier period are the next filing's comparatives, so a line followed from filing to filing keeps its key
though its label changes ("Net sales" in 1995, "Total net sales" in 2008), and an XBRL filing's comparative columns name
the concept of the printed line before it (Apple's 2007-08 figures in its FY2009 10-K and its FY2008 10-K). A figure
printed twice in one statement says nothing (a subtotal equal to its only line), and two lines one statement prints
side by side are never one line, and one period alone joins two lines only when their labels share a word. Each line then takes its group's XBRL concept, the newest tagged one where a filer
retagged it (SalesRevenueNet before Revenues), else its newest printed key. Keys only: no figure changes."""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..domain.basis import ifrs_family

_PRINTED = "printed_"
_PLAIN = {"and", "of", "the", "in", "net", "total", "to", "from", "for", "on", "at", "by", "other", "others", "less", "note", "notes",
          "with", "year", "period", "respectively", "shares", "share", "value", "par"}


def _stems(label: str) -> set[str]:
    """A label's words by their first five letters, its plain words left out ("Purchases" and "Purchase" agree)."""
    return {word[:5] for word in re.findall(r"[a-z]+", label.lower()) if word not in _PLAIN and len(word) > 2}


def _node(statement: str, line: dict) -> tuple:
    return statement, line["concept"], line.get("member") or ""


def _figures(reading) -> dict[float, tuple]:
    """Each figure the reading prints on exactly one line, by its absolute value; never a per-share figure or one
    under 1,000 (TSMC's earnings per share, restated for each stock dividend, meet other per-share lines by chance),
    nor a line whose key the reading prints twice ("Others" under assets and under equity) or whose label is only
    plain words."""
    seen: Counter = Counter()
    keys: Counter = Counter(_node(reading.statement, line) for line in reading.lines if not line.get("is_heading"))
    nodes = {}
    for line in reading.lines:
        value = line.get("value")
        if line.get("is_heading") or not value or abs(value) < 1000:
            continue
        seen[abs(value)] += 1
        node = _node(reading.statement, line)
        plain = node[1].startswith(_PRINTED) and not _stems(str(line.get("label") or ""))
        nodes[abs(value)] = None if keys[node] > 1 or plain else node
    return {value: node for value, node in nodes.items() if seen[value] == 1 and node is not None}


def _same(left: float, right: float) -> bool:
    """Equal at the coarser of the two figures' printed precision (millions printed, thousands tagged)."""
    places = min(_zeros(left), _zeros(right), 6)
    return abs(left - right) <= 0.5 * 10 ** places if places else abs(left - right) < 0.5


def _zeros(value: float) -> int:
    whole = int(round(value))
    count = 0
    while whole and whole % 10 == 0 and count < 9:
        whole //= 10
        count += 1
    return count


class _Groups:
    def __init__(self):
        self.parent: dict[tuple, tuple] = {}
        self.members: dict[tuple, set] = {}

    def find(self, node: tuple) -> tuple:
        if node not in self.parent:
            self.parent[node], self.members[node] = node, {node}
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def join(self, left: tuple, right: tuple, together: dict[tuple, set]) -> None:
        a, b = self.find(left), self.find(right)
        if a == b:
            return
        if any(together.get(node, set()) & self.members[b] for node in self.members[a]):
            return  # printed side by side somewhere: two lines
        self.parent[b] = a
        self.members[a] |= self.members.pop(b)


def line_identity(readings: list) -> dict[tuple, str]:
    """(statement, concept, member) -> the line's key for the company, from its kept consolidated readings."""
    kept = [reading for reading in readings if reading.status == "kept" and reading.scope == "consolidated"]
    together: dict[tuple, set] = defaultdict(set)
    newest: dict[tuple, str] = {}
    words: dict[tuple, set] = defaultdict(set)
    for reading in sorted(kept, key=lambda item: item.filed):
        nodes = {_node(reading.statement, line) for line in reading.lines if not line.get("is_heading")}
        for line in reading.lines:
            if not line.get("is_heading"):
                words[_node(reading.statement, line)] |= _stems(str(line.get("label") or ""))
        for node in nodes:
            together[node] |= nodes - {node}
            newest[node] = reading.filed.isoformat()
    periods: dict[tuple, list] = defaultdict(list)
    for reading in kept:
        periods[(reading.key, ifrs_family(reading.basis), reading.currency)].append(reading)
    votes: Counter = Counter()
    for group in periods.values():
        printed = [_figures(reading) for reading in group]
        for index, left in enumerate(printed):
            for right in printed[index + 1:]:
                for value, node in left.items():
                    match = [other for figure, other in right.items() if _same(value, figure)]
                    if len(match) == 1 and match[0] != node and match[0][2] == node[2]:
                        votes[tuple(sorted((node, match[0])))] += 1
    groups = _Groups()
    for (left, right), count in votes.most_common():
        # One period alone can print two lines' figures alike by chance (TSMC's "Loss on lease modification" and
        # "Others"): then the labels must share a word too. Two periods or more name one line whatever its wording
        # ("Capital expenditures", then "Purchase of property, plant and equipment").
        if count >= 2 or words[left] & words[right]:
            groups.join(left, right, together)
    keys = {}
    for root in list(groups.members):
        members = groups.members[root]
        tagged = [node for node in members if not node[1].startswith(_PRINTED)]
        named = [node for node in members if words.get(node)]  # never a plain "Others" where a named key exists
        pick = max(tagged or named or members, key=lambda node: newest.get(node, ""))
        for node in members:
            keys[node] = pick[1]
    return keys


def apply_identity(chosen: list[dict], readings: list) -> None:
    """Each served line's `key`: its concept as the company's eras share it (its own where it stands alone)."""
    keys = line_identity(readings)
    for record in chosen:
        for lines in [record.get("lines") or []] + [other.get("lines") or [] for other in record.get("other_bases") or []]:
            for line in lines:
                if not line.get("is_heading"):
                    line["key"] = keys.get((record["statement"], line["concept"], line.get("member") or ""), line["concept"])
