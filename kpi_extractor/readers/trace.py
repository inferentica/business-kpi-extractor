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


# Figures read by code from a table, a tag or another document: traced by their reader, with no AI call of their own.
CODE_READERS = ("release_table", "quarter_table", "quoted_row", "tagged", "prospectus", "year_ago")


def untraced(value: dict) -> bool:
    """A served AI figure with no trace to what read it: located before AI calls were logged (2026-11-06), so neither a
    call, a replayed reading nor a code reader names it. An owner's figure is traced by the owner's decision."""
    locator = value.get("locator") or {}
    return (value.get("method") == "ai" and value.get("validation_status") != "rejected" and not value.get("owner_decision")
            and not locator.get("ai_call") and not locator.get("replayed_from")
            and not any(locator.get(reader) for reader in CODE_READERS))
