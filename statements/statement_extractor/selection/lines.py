"""Lines keyed so the same line pairs across presentations of one period."""
from __future__ import annotations


def line_keys(lines: list[dict]) -> dict[str, float | None]:
    """Lines keyed by concept and occurrence (a concept shown twice, opening and closing cash, stays two lines)."""
    seen: dict[str, int] = {}
    keyed = {}
    for line in sorted(lines, key=lambda item: item["line"]):
        if line["is_heading"]:
            continue
        occurrence = seen.get(line["concept"], 0)
        seen[line["concept"]] = occurrence + 1
        keyed[f"{line['concept']}#{occurrence}" if occurrence else line["concept"]] = line["value"]
    return keyed
