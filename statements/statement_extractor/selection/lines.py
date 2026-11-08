"""Lines keyed so the same line pairs across presentations of one period."""
from __future__ import annotations


def line_keys(lines: list[dict]) -> dict[str, float | None]:
    """Lines keyed by concept, member and occurrence (a concept shown twice, opening and closing cash, stays two
    lines; Visa's earnings per share for each class of stock is a line of its own)."""
    seen: dict[str, int] = {}
    keyed = {}
    for line in sorted(lines, key=lambda item: item["line"]):
        if line["is_heading"]:
            continue
        concept = f"{line['concept']}[{line['member']}]" if line.get("member") else line["concept"]
        occurrence = seen.get(concept, 0)
        seen[concept] = occurrence + 1
        keyed[f"{concept}#{occurrence}" if occurrence else concept] = line["value"]
    return keyed
