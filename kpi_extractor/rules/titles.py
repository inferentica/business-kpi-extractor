"""Breakdown titles by rule, the same on every run. An XBRL breakdown on a standard axis keeps the title its axis gives
(Revenue by Segment, Product, Geography, Region and Country); the AI names only a company's own axes ("Markets of
Customers" is TSMC's Revenue by Platform), never with a title another breakdown has. A title once stored changes only
by the owner: a list proposed again keeps it, and keeps each tracked group's title."""
from __future__ import annotations

import re

# A company's own axis (breakdowns.py keys it x_<axis>): the only XBRL breakdowns the AI may title.
OWN_AXIS_PREFIX = "x_"
_SMALL = {"and", "of", "the", "for", "&"}


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9&]+", " ", text.lower()).split())


def breakdown_names(proposed: dict, breakdowns: dict[str, dict], stored: dict | None = None) -> dict[str, dict]:
    """The names to store for the company's XBRL breakdowns. `proposed` is the AI's (key → BreakdownName or dict),
    `breakdowns` each breakdown's current title and members (key → {"label", "members"}), `stored` the names the list
    had before. A stored title or row label is kept; a proposed title stands only on a company's own axis and only
    when no other breakdown has it; a proposed row label stands only when no other row of the breakdown has it."""
    stored = stored or {}
    titles = {key: (stored.get(key) or {}).get("label") or group["label"] for key, group in breakdowns.items()}
    names: dict[str, dict] = {}
    for key, group in breakdowns.items():
        before = stored.get(key) or {}
        offered = proposed.get(key)
        offered = offered.model_dump() if hasattr(offered, "model_dump") else (offered or {})
        label = before.get("label")
        if label is None and key.startswith(OWN_AXIS_PREFIX) and offered.get("label"):
            taken = {_plain(title) for other, title in titles.items() if other != key}
            if _plain(offered["label"]) not in taken:
                label = offered["label"]
                titles[key] = label
        members = dict(before.get("members") or {})
        shown = {member: members.get(member) or name for member, name in group["members"].items()}
        for member, name in (offered.get("members") or {}).items():
            if member not in group["members"] or member in members:
                continue
            if _plain(name) in {_plain(text) for other, text in shown.items() if other != member}:
                continue  # two rows of one breakdown never read alike
            members[member] = shown[member] = name
        if label or members:
            names[key] = {**({"label": label} if label else {}), "members": members}
    return names


def keep_titles(proposed_groups: list[dict], current_groups: list[dict]) -> list[dict]:
    """Groups proposed again keep the title each had in the current list (the owner retitles them, never a rerun)."""
    current = {group["key"]: group["label"] for group in current_groups}
    return [{**group, "label": current.get(group["key"], group["label"])} for group in proposed_groups]


def initials(name: str) -> str:
    """ "Consumer & Community Banking" → "CCB"; "Asset & Wealth Management" → "AWM"."""
    return "".join(word[0] for word in re.findall(r"[A-Za-z][\w'-]*", name) if word.lower() not in _SMALL).upper()


def names_segment(title: str, segment: str) -> bool:
    """Whether a title already names the segment: in full, or by its initials as a word of its own (JPMorgan's
    "CCB Revenue by Business" is Consumer & Community Banking's)."""
    if _plain(segment) in _plain(title):
        return True
    short = initials(segment)
    return len(short) >= 2 and re.search(rf"\b{re.escape(short)}\b", title) is not None
