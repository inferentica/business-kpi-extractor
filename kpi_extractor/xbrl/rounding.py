"""Splits completed by a member tagged to a coarser place than their own lines."""
from __future__ import annotations


def rounding_unit(value: float) -> float:
    """The place a tagged figure is rounded to, read from its trailing zeros (200,000,000 → 100,000,000)."""
    unit, whole = 1.0, abs(round(value))
    while whole and whole % 10 == 0 and unit < 1e12:
        whole //= 10
        unit *= 10
    return unit


def refined_rest(members: dict[str, float], siblings: dict[str, float], total: float | None) -> dict[str, float] | None:
    """One member completing a split, tagged to a coarser place than the split's own (Netflix's DVD in its 10-Ks, "$0.2
    billion" beside regions to the thousand): what revenue leaves after the split, when that rounds to the tagged
    figure, is the same figure to more places (FY2020: 239.4 million against 200 million). None otherwise."""
    if not total or len(siblings) != 1:
        return None
    member, tagged = next(iter(siblings.items()))
    unit = rounding_unit(tagged)
    rest = total - sum(members.values())
    if unit < 1e6 or rest <= 0 or max(rounding_unit(value) for value in members.values()) >= unit:
        return None
    return {member: rest} if abs(rest - tagged) <= unit / 2 else None
