"""A breakdown's members as figures: their keys, subtotals, overlaps and the partitions that add up to a total."""

from __future__ import annotations

from itertools import combinations
import math
import re

from .model import MAX_RECONCILIATION_ERROR


def subtotal_tree(rows: dict[str, float], leaves: dict[str, float], segment: str) -> tuple[dict[str, str], dict[str, float]]:
    """Inside one segment's split: each line's parent, the smallest subtotal the filing tags that holds it (Search and
    YouTube in Google properties; it and Network in Google advertising), else the segment; and those subtotals."""
    positive = [key for key, value in leaves.items() if value > 0]
    covers: dict[str, frozenset] = {}
    for key, value in rows.items():
        if key in leaves or value <= 0:
            continue
        found = _leaves_summing_to(leaves, positive, value)
        if found and len(found) < len(positive):
            covers[key] = found
    sets = {**{key: frozenset([key]) for key in leaves}, **covers}
    parents: dict[str, str] = {}
    for key, held in sets.items():
        holders = [other for other, cover in covers.items() if other != key and held < cover]
        parents[key] = min(holders, key=lambda other: rows[other]) if holders else segment
    return parents, {key: rows[key] for key in covers}


def _leaves_summing_to(leaves: dict[str, float], keys: list[str], target: float) -> frozenset | None:
    for size in range(2, min(len(keys), 10) + 1):
        for combo in combinations(keys, size):
            if abs(sum(leaves[key] for key in combo) - target) <= abs(target) * 1e-5:
                return frozenset(combo)
    return None


def _by_key(rows: list[tuple[str, str, float]]) -> dict[str, float]:
    """Values by member key, adding rows whose labels share a key ("Other" and "All other") instead of keeping one."""
    out: dict[str, float] = {}
    for key, _label, value in rows:
        out[key] = out.get(key, 0.0) + value
    return out


_PRODUCT_OR_SERVICE = re.compile(r"^(?:net)?(?:products?|services?(?:andother)?)(?:sales|revenues?)?$")


def _two_partitions(members: dict[str, float], total: float | None) -> tuple[dict[str, float], dict[str, float]]:
    """(finer, coarser) when one axis carries two complete splits of revenue (Microsoft tags Product / Service and
    other beside its product lines; Broadcom's 10-K tags countries beside the regions its 10-Qs use), else (members, {})."""
    if not total or len(members) < 4:
        return members, {}
    tolerance = abs(total) * MAX_RECONCILIATION_ERROR
    keys = list(members)
    if abs(sum(members.values()) - 2 * total) > 2 * tolerance:
        # Regions beside the countries they are made of (Adobe's 10-K tags Americas beside United States and Other
        # Americas, Asia beside Japan and Other Asia; its 10-Qs give the regions only): the split with the regions is
        # the coarser one.
        parents = [key for key in keys if members[key] > 0 and any(
            abs(sum(members[other] for other in combo) - members[key]) <= tolerance
            for size in (2, 3) for combo in combinations([other for other in keys if other != key], size))]
        leaves = [key for key in keys if key not in parents]
        if parents and abs(sum(members[key] for key in leaves) - total) <= tolerance:
            used: set[str] = set()
            for parent in sorted(parents, key=lambda key: -members[key]):
                combo = next((combo for size in (2, 3) for combo in combinations([key for key in leaves if key not in used], size)
                              if abs(sum(members[key] for key in combo) - members[parent]) <= tolerance), None)
                if combo is None:
                    return members, {}
                used.update(combo)
            coarse = {key: value for key, value in members.items() if key not in used}
            if len(coarse) >= 2 and abs(sum(coarse.values()) - total) <= tolerance:
                return {key: members[key] for key in leaves}, coarse
        return members, {}
    for size in (2, 3, 4):
        for combo in combinations(keys, size):
            if len(keys) - size >= size and abs(sum(members[key] for key in combo) - total) <= tolerance:
                coarse = {key: members[key] for key in combo}
                return {key: value for key, value in members.items() if key not in combo}, coarse
    return members, {}


def drop_overlaps(members: dict[str, float], total: float | None) -> dict[str, float]:
    """Drops up to two detail rows that overlap others (Meta lists "U.S." beside "US & Canada") when, without them, the
    breakdown adds up to reported revenue and with them it does not."""
    if not total or len(members) < 3:
        return members
    tolerance = abs(total) * MAX_RECONCILIATION_ERROR
    excess = sum(members.values()) - total
    if abs(excess) <= tolerance:
        return members
    keys = list(members)
    for size in (1, 2):
        for combo in combinations(keys, size):
            if len(keys) - size >= 2 and abs(excess - sum(members[key] for key in combo)) <= tolerance:
                return {key: value for key, value in members.items() if key not in combo}
    return members


_RESIDUAL = re.compile(r"^(?:all)?others?(?:countries|country|regions?|areas?|products?|segments?)?(?:notseparatelydisclosed)?$")


def member_key(label: str) -> str:
    """A member's identity across filings: its label normalized, so a 10-K's "ComputeAndNetworkingSegmentMember"
    and a 10-Q's "ComputeAndNetworkingMember" (both "Compute & Networking") are one series, as are "Other countries"
    and "All other countries not separately disclosed"."""
    base = re.sub(r"\s*\([^)]*\)\s*$", "", label) or label  # "Asia-Pacific (APAC)" is "Asia-Pacific"
    key = re.sub(r"[^a-z0-9]+", "", base.lower().replace("&", "and"))
    key = re.sub(r"segment$", "", key) or key
    return "other" if _RESIDUAL.match(key) else key[:100]


def remove_subtotals(members: dict[str, float], total: float | None) -> dict[str, float]:
    """Drops members that are the sum of other members (e.g. Data Center = Hyperscale + AI Clouds + ...), keeping leaves.

    Parents are removed largest first, and only while the remaining members still reconcile no worse than before.
    """
    remaining = dict(members)
    for member, value in sorted(members.items(), key=lambda item: -abs(item[1])):
        others = {key: other for key, other in remaining.items() if key != member}
        if value <= 0 or len(others) < 2:
            continue
        if not _is_subset_sum([other for other in others.values() if other > 0], value):
            continue
        if total:
            before = abs(sum(remaining.values()) - total)
            after = abs(sum(others.values()) - total)
            if after > before and before <= abs(total) * MAX_RECONCILIATION_ERROR:
                continue
        remaining = others
    return remaining


def _is_subset_sum(values: list[float], target: float, relative_tolerance: float = 0.0005) -> bool:
    """Whether at least two of values sum to target (bitset subset sum on values scaled to ~100k steps)."""
    if len(values) < 2 or target <= 0:
        return False
    unit = 10 ** max(0, math.floor(math.log10(target)) - 5)
    weights = [round(value / unit) for value in values if 0 < value < target * (1 + relative_tolerance)]
    goal = round(target / unit)
    slack = max(len(weights), round(target * relative_tolerance / unit))
    any_sum = 0  # sums of one or more members
    multi_sum = 0  # sums of two or more members
    for weight in weights:
        multi_sum |= any_sum << weight
        any_sum |= (any_sum << weight) | (1 << weight)
    window = ((1 << (2 * slack + 1)) - 1) << max(0, goal - slack)
    return bool(multi_sum & window)
