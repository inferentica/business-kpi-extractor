"""Official revenue breakdowns (segments, products, geography) from a 10-Q/10-K/20-F XBRL instance."""
from __future__ import annotations

from .model import (  # noqa: F401 - the package's names, as when it was one module
    _CONSOLIDATION_NAMES, _IGNORED_AXES, _NEUTRAL_MEMBERS, _PART_AXES, _SEGMENT_AXES, _STANDARD_AXES,
    _UNALLOCATED, MAX_RECONCILIATION_ERROR, MAX_UNALLOCATED, REVENUE_CONCEPTS, XbrlBreakdowns, XbrlCandidate,
    XbrlGroup)
from .labels import (  # noqa: F401 - the package's names, as when it was one module
    _as_date, _axis_name, _descendants, _fiscal_year, _member_labels, _SMALL_WORDS,
    _snake, _title, drop_declared_parents, humanize, local_name, member_children,
    official_labels, presented_labels)
from .members import (  # noqa: F401 - the package's names, as when it was one module
    _by_key, _is_subset_sum, _leaves_summing_to, _PRODUCT_OR_SERVICE, _RESIDUAL, _two_partitions,
    drop_overlaps, member_key, remove_subtotals, subtotal_tree)
from .breakdowns import (  # noqa: F401 - the package's names, as when it was one module
    _completed_by_segments, _consolidation_items, _name_regional_views, _PLACE, _plain_revenue_figures, _plain_total,
    _single_axis_members, extract_breakdowns)
