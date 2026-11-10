"""Selective re-reads: a rule change names the filings it can affect, and a run reads only those again, with today's
rules and the KPI list as it is. Each selector picks a company's filings from its stored figures, once, before anything
is read again. A re-read never overwrites a served figure it now reads differently: the store holds it for the owner
with the figure served before (CompanyStore.reread_disagreements), so a re-read can only add evidence.

    python -m kpi_extractor --symbols KKR,APO --reread size-holds

Add a selector with the rule change it serves; a learned rule's demotion will name the figures it read."""
from __future__ import annotations

import re
from collections.abc import Callable

from ..readers.trace import untraced
from ..rules.identity import PLACE_NOT_NAMED
from ..steps.release_flow import SCALE_OUTLIER

# Holds a figure's size alone once raised, which a figure the filing confirms no longer stays under (2026-11-10).
_SIZE_HOLD = re.compile(r"^changed [\d.]+x in a quarter$|likely part of the table$|^total far above the quarter before"
                        r"|^" + re.escape(SCALE_OUTLIER))


def _untraced(value: dict) -> bool:
    return untraced(value)


def _size_held(value: dict) -> bool:
    return (value.get("method") == "ai" and value.get("validation_status") == "needs_review" and not value.get("owner_decision")
            and any(_SIZE_HOLD.search(note or "") for note in value.get("notes") or []))


def _repeat_held(value: dict) -> bool:
    return (value.get("validation_status") == "needs_review" and not value.get("owner_decision")
            and any(str(note).startswith("the same figure as ") and str(note).endswith(": check the column")
                    for note in value.get("notes") or []))


def _sum_held(value: dict) -> bool:
    return (value.get("validation_status") == "needs_review" and not value.get("owner_decision")
            and any(str(note).startswith(("parts differ from the total", "lines differ from ")) for note in value.get("notes") or []))


def _place_held(value: dict) -> bool:
    return (value.get("validation_status") == "needs_review" and not value.get("owner_decision")
            and any(str(note).startswith(PLACE_NOT_NAMED) for note in value.get("notes") or []))


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
    # A demoted learned rule's figures (ops/learned.py): read again by the AI.
    "rule-anchor-phrase": ("figures the anchor-phrase rule read", _read_by_anchor_rule),
}


# Selectors that pick filings themselves: a filing that failed outright holds no figures to pick it by.
FILING_SELECTORS: dict[str, tuple[str, Callable[[dict], bool]]] = {
    # KKR's Q3 2018 release failed four times on 2026-11-09 when the AI's reading outran the control plane's time limit,
    # before Quotiva's own project allowed longer calls; past its attempts, no run reads it again unless picked.
    "failed-filings": ("filings that failed to read and are no longer retried", lambda filing: filing.get("status") == "failed"),
}


def picked_filings(name: str, values: dict, filings: dict | None = None) -> set[str]:
    """The accessions of the filings the selector picks: those holding figures it picks, or, for a filing selector, the
    filings themselves."""
    if name in FILING_SELECTORS:
        picks = FILING_SELECTORS[name][1]
        return {accession for accession, filing in (filings or {}).items() if picks(filing)}
    if name not in SELECTORS:
        raise ValueError(f"Unknown re-read selector {name!r}; one of {', '.join(sorted({*SELECTORS, *FILING_SELECTORS}))}")
    picks = SELECTORS[name][1]
    return {value["source_accession"] for value in values.values() if value.get("source_accession") and picks(value)}
