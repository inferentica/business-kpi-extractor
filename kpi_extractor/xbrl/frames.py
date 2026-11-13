"""One period's XBRL facts read as a breakdown's figures: one axis's members, and the figure with no axis."""

from __future__ import annotations

import pandas as pd

from .labels import _axis_name, local_name
from .model import _NEUTRAL_MEMBERS


def _single_axis_members(frame: pd.DataFrame, concept: str, axis: str, dimension_columns: list[str],
                         fixed: tuple[str, str] | None = None, segment_level: bool | None = None) -> dict[str, float]:
    """Members of one axis for one concept, from facts tagged with that axis alone, or with it and the one member of
    another axis its period's facts all carry (fixed), the axis alone first (Exxon's 9M 2022 sales: $305,511 million
    alone, $19 million for Corporate and Financing); only the consolidated facts (segment_level False), only the
    segments' own (True), or both (None)."""
    members: dict[str, float] = {}
    shares: set[str] = set()  # members known so far only with the fixed member
    for _, fact in frame[frame["concept"] == concept].iterrows():
        dimensions = {_axis_name(column): str(fact[column]) for column in dimension_columns if pd.notna(fact[column])}
        kept = {a: m for a, m in dimensions.items() if (a, local_name(m)) not in _NEUTRAL_MEMBERS}
        if (segment_level is not None and (len(kept) != len(dimensions)) != segment_level) or axis not in kept:
            continue
        member = kept[axis]
        if len(kept) == 1 and (member not in members or member in shares):
            members[member] = float(fact["numeric_value"])
            shares.discard(member)
        elif fixed and len(kept) == 2 and kept.get(fixed[0]) == fixed[1] and member not in members:
            members[member] = float(fact["numeric_value"])
            shares.add(member)
    return members


def _plain_total(frame: pd.DataFrame, concept: str, dimension_columns: list[str], segment_level: bool | None = False) -> float | None:
    """The concept's figure with no axis but, for the segments' own measure, "operating segments"."""
    rows = frame[frame["concept"] == concept]
    for _, fact in rows.iterrows():
        dimensions = {_axis_name(column): str(fact[column]) for column in dimension_columns if pd.notna(fact[column])}
        if all((a, local_name(m)) in _NEUTRAL_MEMBERS for a, m in dimensions.items()) and bool(dimensions) == bool(segment_level):
            return float(fact["numeric_value"])
    return None
