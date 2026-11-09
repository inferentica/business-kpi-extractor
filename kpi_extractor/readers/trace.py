"""How a figure read from a filing traces to what read it: the AI call that located it, or for a figure replayed from
last quarter's place, the filing whose reading placed it and the AI call that first found that place."""
from __future__ import annotations


def stamp_call(read: list, call: str | None) -> None:
    """The AI call that located these figures, stored with each (its prompt version and reply are logged under it)."""
    if call:
        for item in read:
            item.locator["ai_call"] = call


def trace_replay(read: list, earlier: dict[str, dict], history: dict[str, dict], accession: str | None) -> None:
    """Where each replayed figure came from: the filing whose reading placed it, and the AI call that first found that
    place (carried down a chain of replays), so a figure no AI read in this filing still traces to a prompt."""
    for item in read:
        key = f"{item.group.key}.{item.kpi.key}"
        before = earlier.get(key) or next(iter(earlier.values()), {})  # a row a whole table gained since: its table's
        item.locator["replayed_from"] = (history.get(key) or {}).get("source_accession") or accession
        if placed := before.get("ai_call") or before.get("placed_by_call"):
            item.locator["placed_by_call"] = placed
