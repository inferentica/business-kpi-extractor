"""The official revenue breakdowns (segments, products, geography) of one 10-Q, 10-K or 20-F instance."""

from __future__ import annotations

from typing import Callable
import pandas as pd
import re

from .labels import _as_date, _axis_name, _fiscal_year, _member_labels, _snake, _title, drop_declared_parents, humanize, local_name
from .members import _by_key, _PRODUCT_OR_SERVICE, _two_partitions, drop_overlaps, member_key, remove_subtotals, subtotal_tree
from .model import _CONSOLIDATION_NAMES, _IGNORED_AXES, _NEUTRAL_MEMBERS, _PART_AXES, _RECONCILING, _SEGMENT_AXES, _STANDARD_AXES, _UNALLOCATED, MAX_RECONCILIATION_ERROR, MAX_UNALLOCATED, REVENUE_CONCEPTS, XbrlBreakdowns, XbrlCandidate, XbrlGroup


def extract_breakdowns(facts: pd.DataFrame, entity: dict, form: str,
                       label: Callable[[str], str | None] = lambda _qname: None,
                       children: dict[str, set[str]] | None = None) -> XbrlBreakdowns | None:
    period_end = _as_date(entity.get("document_period_end_date"))
    if period_end is None:
        return None
    annual = form.upper().startswith(("10-K", "20-F", "40-F"))
    frame = facts[facts["concept"].isin(REVENUE_CONCEPTS) & (facts["period_type"] == "duration")].copy()
    if frame.empty:
        return XbrlBreakdowns(period_end, annual, _fiscal_year(entity), [], None)
    frame["_start"] = pd.to_datetime(frame["period_start"], errors="coerce")
    frame["_end"] = pd.to_datetime(frame["period_end"], errors="coerce")
    days = (frame["_end"] - frame["_start"]).dt.days
    duration = days.between(330, 400) if annual else days.between(80, 100)
    prior_end = frame["_end"].dt.date.map(lambda end: end is not None and not pd.isna(end) and 358 <= (period_end - end).days <= 372)
    prior_frame = frame[duration & prior_end & frame["numeric_value"].notna()]
    ytd_frame = frame[(frame["_end"].dt.date == period_end) & days.between(250, 290) & frame["numeric_value"].notna()]
    prior_ytd_frame = frame[prior_end & days.between(250, 290) & frame["numeric_value"].notna()]
    in_period = (frame["_end"].dt.date == period_end) & duration
    frame = frame[in_period & frame["numeric_value"].notna()]
    dimension_columns = [column for column in frame.columns if column.startswith("dim_")]
    axis_qnames = {_axis_name(column): column[4:].replace("_", ":", 1) for column in dimension_columns}
    labels = _member_labels(facts)

    # Facts tagged "operating segments" on the consolidation axis are the segments' own measure: alone, they are the
    # consolidated figures (a filer that tags every line so); beside consolidated facts of the same concept and axis,
    # they are another table (KKR's segment revenues beside its GAAP fees), read only where no consolidated one is.
    pure_totals: dict[str, float] = {}
    segment_totals: dict[str, float] = {}
    currencies: dict[str, str] = {}
    tagged: list[tuple[str, dict[str, str], float, bool]] = []
    for _, fact in frame.iterrows():
        dimensions = {_axis_name(column): str(fact[column]) for column in dimension_columns if pd.notna(fact[column])}
        kept = {axis: member for axis, member in dimensions.items() if (axis, local_name(member)) not in _NEUTRAL_MEMBERS}
        segment_level = len(kept) != len(dimensions)
        dimensions = kept
        concept = fact["concept"]
        value = float(fact["numeric_value"])
        currency = fact.get("currency") or fact.get("unit_ref")
        if isinstance(currency, str) and currency:
            currencies[concept] = currency.upper().replace("ISO4217:", "")
        if not dimensions:
            (segment_totals if segment_level else pure_totals).setdefault(concept, value)
        else:
            tagged.append((concept, dimensions, value, segment_level))
    totals = {**segment_totals, **pure_totals}

    # Candidate breakdowns per axis: facts tagged with that axis alone, and facts tagged with it plus an axis that only
    # ever takes one value alongside it (Netflix tags every region "Streaming"), which then adds nothing.
    candidates: dict[str, list[tuple[str, dict[str, float]]]] = {}
    single: dict[tuple[str, str], dict[str, float]] = {}
    paired: dict[tuple[str, frozenset], list[tuple[dict[str, str], float]]] = {}
    single_by_level: dict[bool, dict[tuple[str, str], dict[str, float]]] = {False: {}, True: {}}
    paired_levels: dict[tuple[str, frozenset], set[bool]] = {}
    paired_pure: dict[tuple[str, frozenset], list[tuple[dict[str, str], float]]] = {}
    paired_segment: dict[tuple[str, frozenset], list[tuple[dict[str, str], float]]] = {}
    # (axis, concept) → the one (axis, member) its facts are also tagged with (Alphabet's products, all "Google
    # Services"), so the year-ago and to-date figures are read from facts tagged the same way.
    fixed_for: dict[tuple[str, str], tuple[str, str]] = {}
    # (axis, concept) → the fixed axis and its other members that complete the split (Netflix's DVD beside its Streaming
    # regions), so the year-ago and to-date columns are completed the same way.
    siblings_for: dict[tuple[str, str], tuple[str, set[str]]] = {}
    eliminations: dict[str, dict[str, float]] = {}
    # Reconciling items tagged on the consolidation axis beside one more axis (Qualcomm since 2025: "Nonreportable
    # segments" under All Other Segments, "Unallocated revenues" under a licensing dispute): concept → {dimensions: value}.
    reconciling: dict[str, dict[frozenset, float]] = {}
    for concept, dimensions, value, level in tagged:
        if set(dimensions) == {"ConsolidationItemsAxis"} and value:
            items = eliminations.setdefault(concept, {})
            items[dimensions["ConsolidationItemsAxis"]] = items.get(dimensions["ConsolidationItemsAxis"], 0.0) + value
        if len(dimensions) == 2 and local_name(dimensions.get("ConsolidationItemsAxis", "")) in _RECONCILING:
            reconciling.setdefault(concept, {}).setdefault(frozenset(dimensions.items()), value)
        if any(axis in _IGNORED_AXES for axis in dimensions):
            continue
        if len(dimensions) == 1:
            axis, member = next(iter(dimensions.items()))
            single.setdefault((concept, axis), {}).setdefault(member, value)
            single_by_level[level].setdefault((concept, axis), {}).setdefault(member, value)
        elif len(dimensions) == 2:
            paired.setdefault((concept, frozenset(dimensions)), []).append((dimensions, value))
            paired_levels.setdefault((concept, frozenset(dimensions)), set()).add(level)
            (paired_segment if level else paired_pure).setdefault((concept, frozenset(dimensions)), []).append((dimensions, value))
    # The segments' own measure on an axis no consolidated fact uses (KKR's 2018 business lines) is checked against
    # its own total; consolidated members that add up to their own total stand alone, without the segment-level facts
    # beside them (KKR's GAAP fees beside its segment revenues).
    segment_level: set[tuple[str, str]] = set()
    pure_only: set[tuple[str, str]] = set()
    part_of: dict[tuple[str, str], float] = {}
    for key in list(single):
        pure, own = single_by_level[False].get(key), single_by_level[True].get(key)
        if own and not pure:
            segment_level.add(key)
            continue
        if not (pure and own):
            continue
        if key[1] in _SEGMENT_AXES:
            # The segments' own measure over the GAAP split of revenue by segment, when it covers every segment and
            # adds up to its own total (Apollo's Principal Investing, which earns no GAAP revenue); as a company's own
            # segment revenue is its revenue by segment.
            whole = segment_totals.get(key[0])
            leaves = drop_overlaps(remove_subtotals(own, whole), whole) if whole else {}
            if whole and len(leaves) >= len(pure) and abs(sum(leaves.values()) - whole) <= abs(whole) * MAX_RECONCILIATION_ERROR:
                single[key] = own
                segment_level.add(key)
            continue
        # The consolidated lines alone, when they share members with the segments' own (KKR's fees: each line
        # consolidated and again by segment, two tables) or add up exactly to the concept's figure for one segment
        # (KKR's fees since 2021: Fees and Other of its Asset Management segment, its insurer's revenue beside it),
        # which is then their total; else one split (UnitedHealth's segments beside one consolidated line).
        figures = {**single_by_level[True].get((key[0], "StatementBusinessSegmentsAxis"), {}),
                   **single_by_level[False].get((key[0], "StatementBusinessSegmentsAxis"), {})}
        whole = sum(pure.values())
        part = next((value for value in figures.values() if value and abs(whole - value) <= max(abs(value) * 1e-6, 0.5)), None)
        if part is not None or set(pure) & set(own):
            single[key] = pure
            pure_only.add(key)
            if part is not None:
                part_of[key] = part
    for key, levels in paired_levels.items():
        if levels == {True}:
            segment_level.update((key[0], axis) for axis in key[1] if (key[0], axis) not in single_by_level[False])

    # (concept, axis) → member → the (concept, dimensions) of the reconciling facts joined as that row, and the
    # consolidated revenue the split then adds up to.
    joined: dict[tuple[str, str], dict[str, list[tuple[str, frozenset]]]] = {}
    joined_totals: dict[tuple[str, str], float] = {}

    def total_for(concept: str, axis: str | None = None) -> float | None:
        if (concept, axis) in joined_totals:
            return joined_totals[(concept, axis)]
        if (concept, axis) in part_of:
            return part_of[(concept, axis)]
        if (concept, axis) in segment_level and segment_totals.get(concept):
            return segment_totals[concept]
        among = pure_totals if (concept, axis) in pure_only else totals
        return among.get(concept) or next((among[other] for other in REVENUE_CONCEPTS if other in among), None)

    # A member tagged only beside the one member of another axis it belongs to (Intel's 2021 "DCG platform", only with
    # its Data Center Group) joins the axis's members with that figure; one tagged beside several is never added up.
    beside: dict[tuple[str, str], dict[str, list[float]]] = {}
    for (concept, _axes), items in paired.items():
        for dimensions, value in items:
            for axis, member in dimensions.items():
                beside.setdefault((concept, axis), {}).setdefault(member, []).append(value)
    def adds_up(rows: dict[str, float], total: float | None) -> bool:
        leaves = drop_overlaps(remove_subtotals(rows, total), total)
        return bool(total) and abs(sum(leaves.values()) - total) <= abs(total) * MAX_RECONCILIATION_ERROR

    def reconciling_rows(concept: str, axis: str) -> tuple[dict[str, float], dict[str, list[tuple[str, frozenset]]]]:
        """The reconciling facts as rows of a segment split: one on the segment axis is its member (All Other Segments),
        one on another axis is its consolidation item ("Reconciling items"). Each set of dimensions is read once, from
        the segments' own concept first (Qualcomm tags its segments as contract revenue and these as revenues)."""
        rows: dict[str, float] = {}
        sources: dict[str, list[tuple[str, frozenset]]] = {}
        seen: set[frozenset] = set()
        for name in dict.fromkeys((concept, *REVENUE_CONCEPTS)):
            for dimensions, value in reconciling.get(name, {}).items():
                if dimensions in seen:
                    continue
                seen.add(dimensions)
                tags = dict(dimensions)
                other = next(key for key in tags if key != "ConsolidationItemsAxis")
                member = tags[other] if other == axis else tags["ConsolidationItemsAxis"]
                rows[member] = rows.get(member, 0.0) + value
                sources.setdefault(member, []).append((name, dimensions))
        return rows, sources

    consolidated = next((pure_totals[name] for name in REVENUE_CONCEPTS if name in pure_totals), None)
    for (concept, axis), members in single.items():
        extra = {member: values[0] for member, values in beside.get((concept, axis), {}).items()
                 if member not in members and len(values) == 1}
        # Only when the axis's own members fall short of revenue and the extra ones complete it.
        if extra and not adds_up(members, total_for(concept, axis)) and adds_up({**members, **extra}, total_for(concept, axis)):
            members = {**members, **extra}
        # Segments short of revenue by the reconciling items tagged beside them: joined only when the split then adds
        # up exactly to consolidated revenue (Qualcomm's QCT, QTL and QSI with its nonreportable segments and
        # unallocated revenues), never in place of a line the split already has.
        if axis in _SEGMENT_AXES and consolidated and not adds_up(members, total_for(concept, axis)):
            rows, sources = reconciling_rows(concept, axis)
            if rows and not set(rows) & set(members) \
                    and abs(sum(members.values()) + sum(rows.values()) - consolidated) <= max(abs(consolidated) * 1e-6, 0.5):
                members = {**members, **rows}
                joined[(concept, axis)] = sources
                joined_totals[(concept, axis)] = consolidated
        candidates.setdefault(axis, []).append((concept, members))
    for (concept, axes), items in paired.items():
        found = False
        # Consolidated lines tagged under one segment that add up exactly to its figure (KKR's fees since 2021: Fees
        # and Other of its Asset Management segment, beside segment-level figures under another member): a split of
        # that figure, with it as the total.
        segment_axis = next((axis for axis in axes if axis in _SEGMENT_AXES), None)
        pure_items = paired_pure.get((concept, axes), [])
        # Only beside segment-level figures: lines of one segment alone (Alphabet's Google Services) are completed by
        # the other segments instead.
        if segment_axis and pure_items and paired_levels.get((concept, axes)) == {False, True}:
            other = next(axis for axis in axes if axis != segment_axis)
            split = None
            # Each segment's consolidated lines on their own (KKR's Q1 2024 tags two stray lines under another member).
            for member in sorted({dimensions[segment_axis] for dimensions, _v in pure_items}):
                lines: dict[str, float] = {}
                for dimensions, value in pure_items:
                    if dimensions[segment_axis] == member:
                        lines.setdefault(dimensions[other], value)
                figure = single.get((concept, segment_axis), {}).get(member)
                # A line beside its segment-level twin (KKR's "Investment": −$277 million against the segments' +$277
                # million in 2022, the same $198 million in 2023) is no part of the split.
                twins = {key for key in lines if any(d[other] == key for d, _v in paired_segment.get((concept, axes), []))}
                if figure and abs(sum(lines.values()) - figure) > max(abs(figure) * 1e-6, 0.5) and twins:
                    lines = {key: value for key, value in lines.items() if key not in twins}
                if figure and len(lines) >= 2 and abs(sum(lines.values()) - figure) <= max(abs(figure) * 1e-6, 0.5):
                    split = (member, lines, figure)
                    break
            if split:
                member, lines, figure = split
                candidates.setdefault(other, []).append((concept, lines))
                fixed_for.setdefault((other, concept), (segment_axis, member))
                part_of[(concept, other)] = figure
                pure_only.add((concept, other))
                continue
        for fixed in axes:
            if len({dimensions[fixed] for dimensions, _value in items}) != 1:
                continue
            other = next(axis for axis in axes if axis != fixed)
            members: dict[str, float] = {}
            for dimensions, value in items:
                members.setdefault(dimensions[other], value)
            # The fixed member's siblings tagged alone on its axis are the rest of revenue (Netflix's regions are its
            # Streaming revenue, DVD the rest until 2023): joined only when the split then adds up and not before. A
            # segment axis is left to _completed_by_segments (Alphabet's products under Google Services).
            the_member = items[0][0][fixed]
            siblings = {member: value for member, value in single.get((concept, fixed), {}).items() if member != the_member}
            whole = total_for(concept, other)
            if fixed not in _SEGMENT_AXES and siblings and not adds_up(members, whole) \
                    and adds_up({**members, **siblings}, whole):
                members = {**members, **siblings}
                siblings_for[(other, concept)] = (fixed, set(siblings))
            candidates.setdefault(other, []).append((concept, members))
            fixed_for.setdefault((other, concept), (fixed, the_member))
            found = True
        # Products tagged under segments where the filer typed one segment wrong (AMD's FY2025 10-K files Gaming under a
        # "Gaming" segment, Client under "Client and Gaming"): the product lines alone, which _completed_by_segments
        # keeps only when they add up exactly to one real segment.
        part = next((axis for axis in axes if axis in _PART_AXES), None)
        if not found and part and any(axis in _SEGMENT_AXES for axis in axes):
            members = {}
            for dimensions, value in items:
                members.setdefault(dimensions[part], value)
            if len(members) == len(items):
                candidates.setdefault(part, []).append((concept, members))

    def reconciled(axis: str, concept: str, members: dict[str, float]) -> dict[str, float]:
        """A segment split with its eliminations as one more row, when only that makes it add up to revenue."""
        total = total_for(concept, axis)
        members = drop_declared_parents(members, total, children or {})
        if axis not in _SEGMENT_AXES or not total or not eliminations.get(concept):
            return members
        def adds_up(rows: dict[str, float], overlaps: bool = True) -> bool:
            leaves = remove_subtotals(rows, total)
            leaves = drop_overlaps(leaves, total) if overlaps else leaves
            return abs(sum(leaves.values()) - total) <= abs(total) * MAX_RECONCILIATION_ERROR
        with_eliminations = {**members, **eliminations[concept]}
        # Every segment with the eliminations beats the segments adding up only once one is dropped: Intel Foundry's
        # revenue is nearly all intersegment, so without it the others came within 0.1% of revenue.
        if not adds_up(members, overlaps=False) and adds_up(with_eliminations, overlaps=False):
            return with_eliminations
        return with_eliminations if not adds_up(members) and adds_up(with_eliminations) else members

    def score(candidate: tuple[str, dict[str, float]], axis: str = ""):
        concept, members = candidate
        members = reconciled(axis, concept, members)
        total = total_for(concept, axis)
        leaves = drop_overlaps(remove_subtotals(members, total), total)
        reconciles = bool(total) and abs(sum(leaves.values()) - total) <= abs(total) * MAX_RECONCILIATION_ERROR
        # Lines with a figure: a line tagged at zero (KKR's Principal Activities fees) is no part of the split.
        return (reconciles, sum(1 for value in leaves.values() if value), concept in totals)

    best_by_axis = {axis: max(options, key=lambda option, axis=axis: score(option, axis)) for axis, options in candidates.items()}

    def leaf_sum(axis: str, concept: str, members: dict[str, float]) -> float:
        total = total_for(concept, axis)
        return sum(drop_overlaps(remove_subtotals(reconciled(axis, concept, members), total), total).values())

    sums_by_concept: dict[str, list[float]] = {}
    for axis, (concept, members) in best_by_axis.items():
        if len(members) >= 2 and total_for(concept, axis):
            sums_by_concept.setdefault(concept, []).append(leaf_sum(axis, concept, members))

    def unallocated(axis: str, concept: str, members: dict[str, float]) -> float | None:
        """Revenue outside the split, when the split falls short and another split of the same figure agrees."""
        total = total_for(concept, axis)
        if not total:
            return None
        own = leaf_sum(axis, concept, members)
        gap = total - own
        # Recorded whatever its size, so every period of the series carries it and its quarters add up to its year.
        if abs(gap) <= abs(total) * 1e-9 or abs(gap) > abs(total) * MAX_UNALLOCATED:
            return None
        agreeing = [other for other in sums_by_concept.get(concept, []) if abs(other - own) <= abs(own) * 1e-5]
        return gap if len(agreeing) >= 2 else None  # itself and at least one other split

    def frame_total(source: pd.DataFrame, concept: str, level: bool = False) -> float | None:
        if not dimension_columns:
            return None
        if level and (own := _plain_total(source, concept, dimension_columns, True)) is not None:
            return own
        plain = source[source[dimension_columns].isna().all(axis=1)]
        for name in (concept, *REVENUE_CONCEPTS):
            values = plain[plain["concept"] == name]["numeric_value"]
            if len(values):
                return float(values.iloc[0])
        return None

    # Plain figures of the period that sit beside revenue (Alphabet's "Hedging gains (losses)", tagged
    # RevenueNotFromContractWithCustomer): what a gap the splits leave is, when one of them matches it.
    plain = _plain_revenue_figures(facts, period_end, annual, label)

    def outside_name(gap: float) -> str | None:
        return next((name for value, name in plain if abs(value - gap) <= max(abs(gap) * 1e-6, 0.5)), None)

    groups: list[XbrlGroup] = []
    rejected: list[XbrlCandidate] = []
    for axis, (concept, members) in best_by_axis.items():
        outside_label: str | None = None
        if len(members) < 2:
            continue
        # Every breakdown must add up to reported revenue; one that cannot be checked is not kept.
        total = total_for(concept, axis)
        if not total:
            continue
        standard = _STANDARD_AXES.get(axis)
        axis_label = label(axis_qnames.get(axis, axis)) or humanize(axis)
        own_level = True if (concept, axis) in segment_level else False if (concept, axis) in pure_only else None
        # A company's own axis tagged "operating segments" is its segments (KKR's 2018 business lines, its segments
        # before and after), named without the axis's words ("Private Markets Business Line" is Private Markets).
        suffix = None
        if standard is None and own_level and not any(other in _SEGMENT_AXES for other in best_by_axis):
            standard, suffix = _STANDARD_AXES["StatementBusinessSegmentsAxis"], _title(axis_label)
        # An axis already named for revenue ("Revenue by Major Market") is not prefixed again.
        axis_title = _title(axis_label)
        key, title, order = standard or (f"x_{_snake(axis)}", axis_title if axis_title.lower().startswith("revenue")
                                         else f"Revenue by {axis_title}", 10)

        def bare(name: str) -> str:
            return name[:-len(suffix)].rstrip(" -–,") if suffix and name.lower().endswith(suffix.lower()) and len(name) > len(suffix) else name

        def consolidation_name(member: str) -> str | None:
            """The filing's own name for a consolidation item, unless it only has the taxonomy's long one."""
            if local_name(member) not in _CONSOLIDATION_NAMES and member not in eliminations.get(concept, {}):
                return None
            own = label(member)
            if own and "reconciling" in own.lower():
                # One series whether a year's filing says "Segment Reconciling Items" or not; the corporate member stays
                # Corporate whatever its label (Coca-Cola's 2025 10-K labels it with reconciling items, its table says
                # "Corporate", and its quarters' CorporateMember lines are the same series).
                return "Corporate" if local_name(member) == "CorporateNonSegmentMember" else "Reconciling items"
            if own and len(own) <= 40 and not own.lower().startswith("segment reporting"):
                return own
            return _CONSOLIDATION_NAMES.get(local_name(member)) or own

        def named(rows: dict[str, float]) -> list[tuple[str, str, float]]:
            out = []
            for member, value in sorted(rows.items(), key=lambda item: -item[1]):
                name = (outside_label or "Unallocated") if member == _UNALLOCATED else consolidation_name(member) \
                    or label(member) or labels.get(member) or humanize(member)
                name = bare(name)
                out.append((member_key(name), name, value))
            return out

        def elements(rows: dict[str, float]) -> dict[str, str]:
            return {member_key(bare(consolidation_name(member) or label(member) or labels.get(member) or humanize(member))): member
                    for member in rows}

        outside = unallocated(axis, concept, members)
        outside_label = outside_name(outside) if outside is not None else None
        members = reconciled(axis, concept, members)
        sources = joined.get((concept, axis), {})
        consolidation = [member for member in members if member in eliminations.get(concept, {}) and member not in sources]
        if outside is not None:
            leaf_members = set(drop_overlaps(remove_subtotals(members, total), total))
            members = {**members, _UNALLOCATED: outside}

        def frame_members(source: pd.DataFrame) -> dict[str, float]:
            rows = _single_axis_members(source, concept, axis, dimension_columns, fixed_for.get((axis, concept)), own_level)
            if rows and (axis, concept) in siblings_for:
                fixed_axis, extra = siblings_for[(axis, concept)]
                alone = _single_axis_members(source, concept, fixed_axis, dimension_columns, None, own_level)
                rows.update({member: value for member, value in alone.items() if member in extra})
            if consolidation and rows:
                items = _consolidation_items(source, concept, dimension_columns)
                rows.update({member: items[member] for member in consolidation if member in items})
            if sources and rows:
                rows.update(_reconciling_values(source, sources, dimension_columns))
            if outside is not None and rows and (whole := frame_total(source, concept, own_level)) is not None:
                rows[_UNALLOCATED] = whole - sum(value for member, value in rows.items() if member in leaf_members)
            return rows

        fine, coarse = _two_partitions(members, total)
        leaves = drop_overlaps(remove_subtotals(fine, total), total)
        error = (sum(leaves.values()) - total) / total if len(leaves) >= 2 else None
        prior = _by_key(named(frame_members(prior_frame)))
        ytd = _by_key(named(frame_members(ytd_frame)))
        ytd_total = _plain_total(ytd_frame, concept, dimension_columns, own_level) if dimension_columns else None
        if outside is not None:
            ytd_total = frame_total(ytd_frame, concept, own_level)
        prior_ytd = _by_key(named(frame_members(prior_ytd_frame)))
        prior_ytd_total = _plain_total(prior_ytd_frame, concept, dimension_columns, own_level) if dimension_columns else None
        if outside is not None:
            prior_ytd_total = frame_total(prior_ytd_frame, concept, own_level)
        prior_total = frame_total(prior_frame, concept, own_level)
        if sources:
            # Joined with its reconciling items, the split is of consolidated revenue in every period.
            ytd_total, prior_ytd_total, prior_total = (frame_total(source, concept)
                                                       for source in (ytd_frame, prior_ytd_frame, prior_frame))
        if error is not None and abs(error) <= MAX_RECONCILIATION_ERROR:
            # Products against services (Amazon, Microsoft) is a view of its own beside the product lines, not a
            # coarser version of them: both are published, so neither depends on the AI listing it.
            split = coarse and all(_PRODUCT_OR_SERVICE.match(key) for key, _name, _value in named(coarse))
            # A row of nothing all year (Broadcom's 2019 "Intercompany revenue") is not a part of revenue; a business
            # at zero this quarter but not to date (Nvidia's licensing after its Q1) stays, so its year adds up.
            kept = {key for key, _name, _value in named({member: 1.0 for member in consolidation})}
            rows = [row for row in named(leaves) if row[2] != 0 or ytd.get(row[0]) or prior.get(row[0]) or row[0] in kept]
            groups.append(XbrlGroup(key, title, order, rows, total, error, currencies.get(concept), concept,
                                    prior, {**elements(coarse), **elements(leaves)}, ytd, ytd_total, prior_ytd,
                                    prior_ytd_total, [] if split or not coarse else named(coarse), prior_total))
            if split:
                groups.append(XbrlGroup(
                    "product_service", "Revenue by Product and Service", order, named(coarse), total,
                    (sum(coarse.values()) - total) / total, currencies.get(concept), concept, prior, elements(coarse),
                    ytd, ytd_total, prior_ytd, prior_ytd_total, prior_total=prior_total))
            continue
        # Worth an AI review only when it could plausibly be a breakdown: a standard axis, or a custom one whose rows
        # are of the right order of magnitude.
        positive = sum(value for value in members.values() if value > 0)
        # Two lines may still be one segment's split (AMD's Client and Gaming, by product since 2025): kept to be completed
        # by the other segments below, never sent for review alone.
        if 2 <= len(members) <= 40 and (standard or 0.5 * total <= positive <= 3 * total or len(members) == 2):
            rejected.append(XbrlCandidate(key, title, order, concept, total, currencies.get(concept),
                                          {k: (n, v) for k, n, v in named(members)}, prior, elements(members),
                                          ytd, ytd_total, prior_ytd, prior_ytd_total))
    for candidate in list(rejected):
        completed = _completed_by_segments(candidate, groups)
        if completed is not None:
            groups.append(completed)
            rejected.remove(candidate)
    rejected = [candidate for candidate in rejected if len(candidate.members) >= 3]
    groups.sort(key=lambda group: (group.order, group.key))
    _name_regional_views(groups)
    total_revenue = next((totals[concept] for concept in REVENUE_CONCEPTS if concept in totals), None)
    return XbrlBreakdowns(period_end, annual, _fiscal_year(entity), groups, total_revenue, rejected)


# Words that name a place: a company whose every segment is one (Apple's Americas, Europe, Greater China, Japan and Rest
# of Asia Pacific) reports by region.
_PLACE = re.compile(r"\b(americas?|north|south|latin|europe|european|emea|apac|asia|pacific|china|japan|india|africa|"
                    r"middle east|international|united states|u\.s\.|us|canada|mexico|brazil|korea|taiwan|germany|"
                    r"united kingdom|uk|france|rest of world|other countries|countries|regions?|greater)\b", re.IGNORECASE)


def _name_regional_views(groups: list[XbrlGroup]) -> None:
    """Segments that are regions read as "Revenue by Region", and the country split beside them as "Revenue by Country",
    so two geographic views never pass as one by segment and one by geography."""
    segments = next((group for group in groups if group.key == "segments"), None)
    if segments is None or not segments.members or not all(_PLACE.search(name) for _key, name, _value in segments.members):
        return
    segments.label = "Revenue by Region"
    for group in groups:
        if group.key == "geography":
            group.label = "Revenue by Country"


def _completed_by_segments(candidate: XbrlCandidate, groups: list[XbrlGroup]) -> XbrlGroup | None:
    """A split tagged inside one segment only (Alphabet's Search, YouTube, Network and subscriptions add up to Google
    Services, while Cloud and Other Bets appear only as segments), completed by the company's other segments and its
    balancing rows, when its lines add up exactly to that segment and the whole then adds up to revenue."""
    segments = next((group for group in groups if group.key == "segments"), None)
    if segments is None or candidate.key == "segments" or any(group.key == candidate.key for group in groups):
        return None
    rows = {key: value for key, (_label, value) in candidate.members.items()}
    for key, label, part in segments.members:
        if not part:
            continue
        leaves = drop_overlaps(remove_subtotals(rows, part), part)
        if len(leaves) < 2 or abs(sum(leaves.values()) - part) > abs(part) * 1e-5:
            continue
        others = [(k, n, v) for k, n, v in segments.members if k != key]
        if any(k in leaves for k, _n, _v in others):
            return None  # a line and a segment under one name: not one split
        members = [(k, candidate.members[k][0], v) for k, v in sorted(leaves.items(), key=lambda item: -item[1])] + others
        error = (sum(v for _k, _n, v in members) - segments.total) / segments.total if segments.total else None
        if error is None or abs(error) > MAX_RECONCILIATION_ERROR:
            return None
        def merged(lines: dict[str, float], rest: dict[str, float]) -> dict[str, float]:
            return {**{k: v for k, v in lines.items() if k in leaves}, **{k: v for k, v in rest.items() if k != key}}
        parents, subtotals = subtotal_tree(rows, leaves, key)
        return XbrlGroup(candidate.key, candidate.label, candidate.order, members, segments.total, error,
                         segments.currency, candidate.concept, merged(candidate.prior, segments.prior),
                         {**segments.elements, **candidate.elements},
                         merged(candidate.year_to_date, segments.year_to_date), segments.year_to_date_total,
                         merged(candidate.prior_year_to_date, segments.prior_year_to_date), segments.prior_year_to_date_total,
                         prior_total=segments.prior_total, parents=parents,
                         levels=[(k, candidate.members[k][0], v) for k, v in sorted(subtotals.items(), key=lambda item: -item[1])],
                         level_prior={k: v for k, v in candidate.prior.items() if k in subtotals},
                         level_year_to_date={k: v for k, v in candidate.year_to_date.items() if k in subtotals})
    return None


def _consolidation_items(frame: pd.DataFrame, concept: str, dimension_columns: list[str]) -> dict[str, float]:
    """A concept's facts tagged on the consolidation axis alone, other than its operating segments, by member
    (intersegment eliminations, corporate items, reconciling items)."""
    items: dict[str, float] = {}
    for _, fact in frame[frame["concept"] == concept].iterrows():
        dimensions = {_axis_name(column): str(fact[column]) for column in dimension_columns if pd.notna(fact[column])}
        if set(dimensions) == {"ConsolidationItemsAxis"} and local_name(dimensions["ConsolidationItemsAxis"]) != "OperatingSegmentsMember":
            member = dimensions["ConsolidationItemsAxis"]
            items[member] = items.get(member, 0.0) + float(fact["numeric_value"])
    return items


def _reconciling_values(frame: pd.DataFrame, sources: dict[str, list[tuple[str, frozenset]]],
                        dimension_columns: list[str]) -> dict[str, float]:
    """The joined reconciling rows in another period's facts: each row's facts tagged exactly as in the period read."""
    wanted = {(name, dimensions): member for member, tags in sources.items() for name, dimensions in tags}
    values: dict[tuple[str, frozenset], float] = {}
    for _, fact in frame[frame["concept"].isin({name for name, _d in wanted})].iterrows():
        dimensions = frozenset((_axis_name(column), str(fact[column])) for column in dimension_columns if pd.notna(fact[column]))
        if (fact["concept"], dimensions) in wanted:
            values.setdefault((fact["concept"], dimensions), float(fact["numeric_value"]))
    rows: dict[str, float] = {}
    for key, value in values.items():
        rows[wanted[key]] = rows.get(wanted[key], 0.0) + value
    return rows


def _plain_revenue_figures(facts: pd.DataFrame, period_end, annual: bool, label: Callable[[str], str | None]) -> list[tuple[float, str]]:
    """Undimensioned revenue-like figures of the period other than revenue itself, with the filing's own names."""
    source = facts[(facts["period_type"] == "duration") & facts["numeric_value"].notna()]
    source = source[source["concept"].astype(str).str.contains("Revenue") & ~source["concept"].isin(REVENUE_CONCEPTS)]
    if source.empty:
        return []
    start = pd.to_datetime(source["period_start"], errors="coerce")
    end = pd.to_datetime(source["period_end"], errors="coerce")
    days = (end - start).dt.days
    keep = (end.dt.date == period_end) & (days.between(330, 400) if annual else days.between(80, 100))
    dimensions = [column for column in source.columns if column.startswith("dim_")]
    if dimensions:
        keep &= source[dimensions].isna().all(axis=1)
    figures = []
    for _, fact in source[keep].iterrows():
        own = fact.get("label")
        name = own if isinstance(own, str) and own.strip() else label(str(fact["concept"])) or humanize(str(fact["concept"]))
        figures.append((float(fact["numeric_value"]), name.strip()))
    return figures


def _single_axis_members(frame: pd.DataFrame, concept: str, axis: str, dimension_columns: list[str],
                         fixed: tuple[str, str] | None = None, segment_level: bool | None = None) -> dict[str, float]:
    """Members of one axis for one concept, from facts tagged with that axis alone, or with it and the one member of
    another axis its period's facts all carry (fixed); only the consolidated facts (segment_level False), only the
    segments' own (True), or both (None)."""
    members: dict[str, float] = {}
    for _, fact in frame[frame["concept"] == concept].iterrows():
        dimensions = {_axis_name(column): str(fact[column]) for column in dimension_columns if pd.notna(fact[column])}
        kept = {a: m for a, m in dimensions.items() if (a, local_name(m)) not in _NEUTRAL_MEMBERS}
        if (segment_level is not None and (len(kept) != len(dimensions)) != segment_level) or axis not in kept:
            continue
        if len(kept) == 1 or (fixed and len(kept) == 2 and kept.get(fixed[0]) == fixed[1]):
            members.setdefault(kept[axis], float(fact["numeric_value"]))
    return members


def _plain_total(frame: pd.DataFrame, concept: str, dimension_columns: list[str], segment_level: bool | None = False) -> float | None:
    """The concept's figure with no axis but, for the segments' own measure, "operating segments"."""
    rows = frame[frame["concept"] == concept]
    for _, fact in rows.iterrows():
        dimensions = {_axis_name(column): str(fact[column]) for column in dimension_columns if pd.notna(fact[column])}
        if all((a, local_name(m)) in _NEUTRAL_MEMBERS for a, m in dimensions.items()) and bool(dimensions) == bool(segment_level):
            return float(fact["numeric_value"])
    return None
