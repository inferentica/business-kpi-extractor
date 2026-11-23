"""One period's XBRL facts read as a breakdown's figures: one axis's members, and the figure with no axis."""

from __future__ import annotations

import re

import pandas as pd

from .labels import _axis_name, local_name
from .model import _NEUTRAL_MEMBERS, _SEGMENT_AXES, MAX_RECONCILIATION_ERROR


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


# A consolidation member for the segments' revenue before intersegment sales come out (GE Vernova's
# "OperatingSegmentsExcludingIntersegmentEliminationMember"): the figures its segment summary prints.
_BEFORE_ELIMINATIONS = re.compile(r"segment.*(excluding|before|including)intersegment", re.I)
_ELIMINATIONS_AND_OTHER = "kpi:EliminationsAndOtherMember"


def printed_segment_revenues(facts: pd.DataFrame) -> pd.DataFrame:
    """Segment revenue as the filing's segment summary prints it, where the filer tags it twice.

    GE Vernova tags each segment's external revenue on the operating-segments member (Power 4,007 in Q1 2024) and its
    segment revenue, intersegment sales included, on a member of its own (Power 4,035); its summary of reportable
    segments prints the second, with one "Eliminations and other" line (-65) to total revenue (7,260). The reader took
    the first, with Corporate's 12 as a fourth line, in every period it stored (2026-11-23). Where both are tagged the
    printed figures replace the external ones, and the consolidation items (eliminations +78 as tagged, Corporate 12)
    become that one line: total revenue less the segments' own total, both tagged figures."""
    columns = [column for column in facts.columns if column.startswith("dim_")]
    consolidation = next((column for column in columns if _axis_name(column) == "ConsolidationItemsAxis"), None)
    if consolidation is None or facts.empty:
        return facts
    named = facts[consolidation].dropna().astype(str)
    before = {member for member in named.unique() if _BEFORE_ELIMINATIONS.search(local_name(member))}
    if not before:
        return facts
    segment_columns = [column for column in columns if _axis_name(column) in _SEGMENT_AXES]
    others = [column for column in columns if column != consolidation]
    operating = next((member for member in named.unique() if local_name(member) == "OperatingSegmentsMember"),
                     "us-gaap:OperatingSegmentsMember")
    tagged = facts[others].notna()
    one_segment = tagged[segment_columns].sum(axis=1).eq(1) & tagged.sum(axis=1).eq(1) if segment_columns else tagged.sum(axis=1).lt(0)
    alone = tagged.sum(axis=1).eq(0)
    member = facts[consolidation].astype(str).where(facts[consolidation].notna(), "")
    is_before = member.isin(before)
    keys = ["concept", "period_start", "period_end"]
    out = facts.copy()
    drop: list = []
    added: list[dict] = []
    for _key, rows in facts[is_before & one_segment].groupby(keys, dropna=False):
        concept, start, end = _key
        same = (facts["concept"] == concept) & (facts["period_start"] == start) & (facts["period_end"] == end)
        segments_total = facts[same & is_before & alone]
        whole = facts[same & ~facts[consolidation].notna() & tagged.sum(axis=1).eq(0)]
        if segments_total.empty or whole.empty:
            continue
        before_total, revenue = float(segments_total["numeric_value"].iloc[0]), float(whole["numeric_value"].iloc[0])
        if abs(rows["numeric_value"].astype(float).sum() - before_total) > max(abs(before_total) * MAX_RECONCILIATION_ERROR, 1.0):
            continue  # the segments' own figures do not make up their own total: left as tagged
        external = same & member.eq(operating) & one_segment
        items = same & alone & facts[consolidation].notna() & ~is_before & ~member.eq(operating)
        out.loc[rows.index, consolidation] = operating
        drop += list(facts.index[external | items]) + list(segments_total.index)
        if abs(revenue - before_total) > 0.5:
            row = segments_total.iloc[0].to_dict()
            row.update({consolidation: _ELIMINATIONS_AND_OTHER, "numeric_value": revenue - before_total})
            added.append(row)
    if not drop and not added:
        return facts
    out = out.drop(index=drop)
    return pd.concat([out, pd.DataFrame(added)], ignore_index=True) if added else out.reset_index(drop=True)
