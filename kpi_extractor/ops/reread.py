"""Selective re-reads: a rule change names the filings it can affect, and a run reads only those again, with today's
rules and the KPI list as it is. Each selector picks a company's filings from its stored figures, once, before anything
is read again. A re-read never overwrites a served figure it now reads differently: the store holds it for the owner
with the figure served before (CompanyStore.reread_disagreements), so a re-read can only add evidence.

    python -m kpi_extractor --symbols KKR,APO --reread size-holds

Add a selector with the rule change it serves; a learned rule's demotion will name the figures it read."""
from __future__ import annotations

from collections.abc import Callable

from ..quality.reasons import has
from ..readers.trace import untraced
from ..rules.scale import _PER_UNIT

# Holds a figure's size alone once raised, which a figure the filing confirms no longer stays under (2026-11-10).
_SIZE_HOLDS = ("ratio_jump", "part_of_table", "far_above_quarter", "scale_outlier")


def _untraced(value: dict) -> bool:
    return untraced(value)


def _size_held(value: dict) -> bool:
    return (value.get("method") == "ai" and value.get("validation_status") == "needs_review" and not value.get("owner_decision")
            and has(value.get("notes"), *_SIZE_HOLDS))


def _repeat_held(value: dict) -> bool:
    return (value.get("validation_status") == "needs_review" and not value.get("owner_decision")
            and has(value.get("notes"), "same_as_earlier"))


def _sum_held(value: dict) -> bool:
    return (value.get("validation_status") == "needs_review" and not value.get("owner_decision")
            and has(value.get("notes"), "parts_differ", "lines_differ"))


def _place_held(value: dict) -> bool:
    return (value.get("validation_status") == "needs_review" and not value.get("owner_decision")
            and has(value.get("notes"), "place_not_named"))


# Holds the wave 2 audit's repairs set (2026-11-13): read again once PRs 48 and 52 refuse what misread them.
_AUDIT_HOLDS = ("shared_cell", "percent_from_amounts")


def _audit_held(value: dict) -> bool:
    return (value.get("validation_status") == "needs_review" and not value.get("owner_decision")
            and has(value.get("notes"), *_AUDIT_HOLDS))


def _unit_price_row(value: dict) -> bool:
    """A price whose row states its unit (Exxon's "Brent ($/b)") read with a scale or held, before such a row kept its
    units (2026-11-13: $74.35 was served as 0.000074); or a figure held for a place its section's title names."""
    locator = value.get("locator") or {}
    if value.get("validation_status") == "rejected" or value.get("owner_decision"):
        return False
    if _PER_UNIT.search(str(locator.get("row_label") or "")):
        factor = (locator.get("scale_from") or {}).get("factor")
        return factor not in (None, 1) or value.get("validation_status") == "needs_review"
    return _place_held(value)


def _series_rescaled(value: dict) -> bool:
    """A figure put on its series' scale by code rather than read at a scale its filing states, before that ended
    (2026-11-15): Visa's processed transactions, 29,321 for 29.3 billion. Read again with today's scale rules; a new
    reading that differs from the figure served is held for the owner, as every re-read's is."""
    return value.get("validation_status") != "rejected" and not value.get("owner_decision") and has(value.get("notes"), "rescaled")


def _reread_differs(value: dict) -> bool:
    """A figure a re-read held because it read the figure served before differently: read once more after a reader fix
    (ServiceNow's cRPO, re-read at its column's "$ millions" before a heading row's "($ billions)" counted, 2026-11-15).
    A held figure is no longer compared with what was served, so the new reading stands on its own checks."""
    return value.get("validation_status") == "needs_review" and not value.get("owner_decision") and has(value.get("notes"), "reread_differs")


def _scale_disputed(value: dict) -> bool:
    """A figure read at its table's scale where the AI gave another: read again after a scale rule changes which words
    decide (ServiceNow's Q4 2021 cRPO, $5.7 billion, served as $5.7 million before a header inside the table counted)."""
    return (value.get("validation_status") != "rejected" and not value.get("owner_decision")
            and any(str(note).startswith("table declares scale ") for note in value.get("notes") or []))


def _segment_page_held(value: dict) -> bool:
    """A company-wide metric held for having been read from a segment's page whose label the company's table prints
    with other figures (BNY's average loans from its Securities Services page, 2026-11-15): read again, so the company's
    own row is read."""
    return value.get("validation_status") == "needs_review" and not value.get("owner_decision") and has(value.get("notes"), "segment_page")


def _read_by_anchor_rule(value: dict) -> bool:
    return (value.get("locator") or {}).get("rule") == "anchor-phrase" and value.get("validation_status") != "rejected"


SELECTORS: dict[str, tuple[str, Callable[[dict], bool]]] = {
    "untraced": ("AI figures read before calls were traced", _untraced),
    "size-holds": ("figures held for their size alone, before checks judged only against the filing", _size_held),
    "repeat-holds": ("figures held for repeating an earlier period's figure, before a repeat stood when its source "
                     "states one period", _repeat_held),
    "sum-holds": ("figures held because their breakdown's parts or lines did not add up, read again once the KPI list "
                  "says which line sits within which (KKR's capital markets fees within its transaction fees)", _sum_held),
    "place-holds": ("figures held because their row named no place their KPI names, before a place written two ways "
                    "counted as named (Coca-Cola's EMEA row reads Europe, Middle East & Africa)", _place_held),
    "audit-holds": ("figures the wave 2 audit held: two listed metrics read from one cell (Exxon's \"Worldwide\" rows), "
                    "a percent read from an amounts row (Wells Fargo's allowance coverage)", _audit_held),
    "unit-price-rows": ("prices whose row states its unit (Exxon's \"Brent ($/b)\") read with a scale or held, and figures "
                        "held for a place their section's title names", _unit_price_row),
    "series-rescaled": ("figures put on their series' scale by code, read again at the scale their filing states",
                        _series_rescaled),
    "reread-differs": ("figures a re-read held for reading the served figure differently, read once more after a reader fix",
                       _reread_differs),
    "scale-disputed": ("figures read at their table's scale where the AI gave another, read again after a scale rule fix",
                       _scale_disputed),
    "segment-pages": ("company-wide metrics held for having been read from a segment's page", _segment_page_held),
    # A demoted learned rule's figures (ops/learned.py): read again by the AI.
    "rule-anchor-phrase": ("figures the anchor-phrase rule read", _read_by_anchor_rule),
}


# Selectors that pick filings themselves: a filing that failed outright holds no figures to pick it by.
FILING_SELECTORS: dict[str, tuple[str, Callable[[dict], bool]]] = {
    # KKR's Q3 2018 release failed four times on 2026-11-09 when the AI's reading outran the control plane's time limit,
    # before Quotiva's own project allowed longer calls; past its attempts, no run reads it again unless picked.
    "failed-filings": ("filings that failed to read and are no longer retried", lambda filing: filing.get("status") == "failed"),
}


def _reports_without_segments(values: dict) -> set[str]:
    """Periodic reports holding segment operating income from XBRL but no revenue by segment: their split went unread
    (Intel's 2022-2023 10-Qs, tagged on the consolidated-entities axis, before the reader took that axis as neutral)."""
    groups: dict[str, set] = {}
    for value in values.values():
        if value.get("method") == "xbrl" and value.get("source_accession"):
            groups.setdefault(value["source_accession"], set()).add(value.get("group_key"))
    return {accession for accession, keys in groups.items() if "segment_income" in keys and "segments" not in keys}


# Selectors that pick filings by what all their figures together lack.
SET_SELECTORS: dict[str, tuple[str, Callable[[dict], set[str]]]] = {
    "reports-without-segments": ("periodic reports with segment income from XBRL but no revenue by segment", _reports_without_segments),
}


def picked_filings(name: str, values: dict, filings: dict | None = None) -> set[str]:
    """The accessions of the filings the selector picks: those holding figures it picks, or, for a filing selector, the
    filings themselves."""
    if name in SET_SELECTORS:
        return SET_SELECTORS[name][1](values)
    if name in FILING_SELECTORS:
        picks = FILING_SELECTORS[name][1]
        return {accession for accession, filing in (filings or {}).items() if picks(filing)}
    if name not in SELECTORS:
        raise ValueError(f"Unknown re-read selector {name!r}; one of {', '.join(sorted({*SELECTORS, *FILING_SELECTORS, *SET_SELECTORS}))}")
    picks = SELECTORS[name][1]
    return {value["source_accession"] for value in values.values() if value.get("source_accession") and picks(value)}
