"""Fourth quarters derived from a year and nine months of different presentations."""
from __future__ import annotations


def mixed_vintage_q4s(chosen: list[dict]) -> list[str]:
    """Fourth quarters the serving derives as the year less nine months, where one of the two was restated and the
    other not: their lines may follow different presentations, so the difference would mix them."""
    by_key = {(item["statement"], item["fiscal_year"], item["fiscal_period"], item["duration"]): item for item in chosen}
    found = []
    for (statement, year, period, duration), item in by_key.items():
        if period != "FY" or duration != "year" or statement == "balance" or item["status"] != "kept":
            continue
        nine = by_key.get((statement, year, "Q3", "ytd"))
        if not nine or nine["status"] != "kept":
            continue
        restated = [bool((part.get("versus_own") or {}).get("changed")) for part in (item, nine)]
        if restated[0] != restated[1]:
            found.append(f"{statement} {year}")
    return sorted(found)
