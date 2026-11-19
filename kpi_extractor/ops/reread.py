"""Selective re-reads: a rule change names the filings it can affect, and a run reads only those again, with today's
rules and the KPI list as it is. Each selector picks a company's filings from its stored figures, once, before anything
is read again. A re-read never overwrites a served figure it now reads differently: the store holds it for the owner
with the figure served before (CompanyStore.reread_disagreements), so a re-read can only add evidence.

    python -m kpi_extractor --symbols KKR,APO --reread size-holds

Add a selector with the rule change it serves; a learned rule's demotion will name the figures it read."""
from __future__ import annotations

import re

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


# Holds the wave 3 audit set (2026-11-17, owner's "Fix and hold"): read again once PR 178's checks refuse what misread them.
_WAVE3_HOLD = "held 2026-11-17 (wave 3 audit"


def _wave3_held(value: dict) -> bool:
    return (value.get("validation_status") == "needs_review" and not value.get("owner_decision")
            and any(str(note).startswith(_WAVE3_HOLD) for note in value.get("notes") or []))


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
    "wave3-holds": ("figures the wave 3 audit held: Northern Trust's charge-off ratio, State Street's net flows and backlog, "
                    "McDonald's 2016-18 segment lines", _wave3_held),
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
    # Picture pages that went unread (past the page cap, or a page that could not be fetched or recognized).
    "pages-not-read": ("filings whose picture pages were not all read", lambda filing: any(
        str(note).startswith("pages not read: ") for note in filing.get("notes") or [])),
}


def _reports_without_segments(values: dict) -> set[str]:
    """Periodic reports holding segment operating income from XBRL but no revenue by segment: their split went unread
    (Intel's 2022-2023 10-Qs, tagged on the consolidated-entities axis, before the reader took that axis as neutral)."""
    groups: dict[str, set] = {}
    for value in values.values():
        if value.get("method") == "xbrl" and value.get("source_accession"):
            groups.setdefault(value["source_accession"], set()).add(value.get("group_key"))
    return {accession for accession, keys in groups.items() if "segment_income" in keys and "segments" not in keys}


def _reports_missing_split(values: dict) -> set[str]:
    """Periodic reports whose quarter is missing from a revenue split the company has from XBRL before and after it (a
    quarter or a year's figure): the report's split went unread (Alphabet's Q1 2018 10-Q, its product split refused until the
    Google segment's own unnamed figure completed it, PR 170), leaving that quarter and its Q4 without the split."""
    periods: dict[str, set[str]] = {}
    report_period: dict[str, str] = {}
    sources: dict[tuple[str, str], set[str]] = {}  # (split, quarter end) -> the filings its figures came from
    year_of: dict[str, str] = {}  # quarter end -> fiscal year
    listed: dict[tuple[str, str], set[str]] = {}  # (split, fiscal year) -> its verified periods
    for value in values.values():
        if value.get("method") == "derived" and value.get("validation_status") == "verified":
            # A worked-out Q4 counts as the year's Q4.
            listed.setdefault((str(value.get("group_key") or ""), str(value.get("fiscal_year"))), set()).add(str(value.get("fiscal_period")))
        if value.get("method") != "xbrl" or not value.get("source_accession"):
            continue
        accession, end = value["source_accession"], str(value["period_end"])[:10]
        if value.get("fiscal_period") != "FY":  # a year's figures bound the series, but name no report's quarter
            report_period[accession] = max(end, report_period.get(accession, end))  # its own quarter, after its year-ago ones
        key = str(value.get("group_key") or "")
        if value.get("group_kind") == "revenue_breakdown" and value.get("validation_status") == "verified" \
                and not key.endswith("_levels"):
            periods.setdefault(key, set()).add(end)
            listed.setdefault((key, str(value.get("fiscal_year"))), set()).add(str(value.get("fiscal_period")))
            if value.get("fiscal_period") != "FY":
                sources.setdefault((key, end), set()).add(accession)
                year_of[end] = str(value.get("fiscal_year"))
    from ..readers.segment_income import LEFT_OVER_KEY

    # A report whose own quarter's split is held only from a later report's year-ago columns, in a year whose Q4 is
    # missing (Alphabet's Q2 and Q3 2018 10-Qs, refused until PR 170): read again, the year's quarters stand on the
    # figures first reported, as the year does, so its Q4 can be worked out on one basis.
    no_q4 = {(key, year) for (key, year), found in listed.items() if {"FY", "Q1", "Q2", "Q3"} <= found and "Q4" not in found}
    own_missing = {accession for accession, end in report_period.items()
                   if any(end in ends and accession not in sources.get((key, end), set()) and (key, year_of.get(end)) in no_q4
                          for key, ends in periods.items())}

    # A report whose split holds a line still unnamed ("Unallocated"): its name comes from the report's table, once a
    # reader can find it (Alphabet's Q1 2018 "Google other revenues", in the column after the year-ago one).
    unnamed = {value["source_accession"] for value in values.values() if value.get("method") == "xbrl"
               and value.get("kpi_key") == LEFT_OVER_KEY and value.get("validation_status") != "rejected"
               and value.get("source_accession") and value.get("group_kind") == "revenue_breakdown"}
    return unnamed | own_missing | {accession for accession, end in report_period.items()
                      if any(end not in ends and min(ends) < end < max(ends) for ends in periods.values())}


def _chart_share_quarters(values: dict) -> set[str]:
    """The other filings of a quarter whose figures a slide's shares still stand in for: its report, whose amounts the
    shares kept out while they blocked an unchecked reading (TSMC's Q3 2026 technology nodes, 2026-11-13)."""
    from ..store import chart_share
    held = {(value["fiscal_year"], value["fiscal_period"]): value.get("source_accession") for value in values.values()
            if chart_share(value) and value.get("validation_status") != "rejected"}
    return {value["source_accession"] for value in values.values()
            if value.get("source_accession") and (value["fiscal_year"], value["fiscal_period"]) in held
            and value["source_accession"] != held[(value["fiscal_year"], value["fiscal_period"])] and not chart_share(value)}


# Selectors that pick filings by what all their figures together lack.
SET_SELECTORS: dict[str, tuple[str, Callable[[dict], set[str]]]] = {
    "reports-without-segments": ("periodic reports with segment income from XBRL but no revenue by segment", _reports_without_segments),
    "chart-share-quarters": ("the reports of quarters a slide's shares still stand in for", _chart_share_quarters),
    "reports-missing-split": ("periodic reports whose quarter is missing from an XBRL revenue split held before and after it, "
                              "whose split holds a line still unnamed, or whose own quarter's split is held only from a later report",
                              _reports_missing_split),
}


_NOT_A_PERCENT = re.compile(r"^(?:[a-z]+ )?([a-z0-9_]+)\.([a-z0-9_]+): '.*' is not a percentage$")


def _bare_ratio_filings(values: dict, filings: dict) -> set[str]:
    """Filings where a reading was refused as not a percentage and its line has no served figure for that quarter:
    a ratio's row printed without its % sign, refused before rules.table read it as one (Wells Fargo's efficiency
    ratio, 10 quarters, 2026-11-19). A quarter whose line is served elsewhere is not read again."""
    served = {(value["group_key"], value["kpi_key"], str(value.get("period_end"))[:10]) for value in values.values()
              if value.get("validation_status") == "verified"}
    picked = set()
    for accession, filing in filings.items():
        end = str(filing.get("period_end") or "")[:10]
        for note in filing.get("notes") or []:
            if (match := _NOT_A_PERCENT.match(str(note))) and end \
                    and (f"kpi_{match.group(1)}", match.group(2), end) not in served:
                picked.add(accession)
    return picked


# Selectors that pick filings by their notes and the figures served.
NOTE_SET_SELECTORS: dict[str, tuple[str, Callable[[dict, dict], set[str]]]] = {
    "bare-ratios": ("filings where a ratio was refused as not a percentage and its quarter has no served figure",
                    _bare_ratio_filings),
}


def picked_filings(name: str, values: dict, filings: dict | None = None) -> set[str]:
    """The accessions of the filings the selector picks: those holding figures it picks, or, for a filing selector, the
    filings themselves."""
    if name in NOTE_SET_SELECTORS:
        return NOTE_SET_SELECTORS[name][1](values, filings or {})
    if name in SET_SELECTORS:
        return SET_SELECTORS[name][1](values)
    if name in FILING_SELECTORS:
        picks = FILING_SELECTORS[name][1]
        return {accession for accession, filing in (filings or {}).items() if picks(filing)}
    if name not in SELECTORS:
        raise ValueError(f"Unknown re-read selector {name!r}; one of {', '.join(sorted({*SELECTORS, *FILING_SELECTORS, *SET_SELECTORS, *NOTE_SET_SELECTORS}))}")
    picks = SELECTORS[name][1]
    return {value["source_accession"] for value in values.values() if value.get("source_accession") and picks(value)}
