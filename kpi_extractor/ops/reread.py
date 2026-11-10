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
from ..steps.release_flow import SCALE_OUTLIER

# Holds a figure's size alone once raised, which a figure the filing confirms no longer stays under (2026-11-10).
_SIZE_HOLD = re.compile(r"^changed [\d.]+x in a quarter$|likely part of the table$|^total far above the quarter before"
                        r"|^" + re.escape(SCALE_OUTLIER))


def _untraced(value: dict) -> bool:
    return untraced(value)


def _size_held(value: dict) -> bool:
    return (value.get("method") == "ai" and value.get("validation_status") == "needs_review" and not value.get("owner_decision")
            and any(_SIZE_HOLD.search(note or "") for note in value.get("notes") or []))


SELECTORS: dict[str, tuple[str, Callable[[dict], bool]]] = {
    "untraced": ("AI figures read before calls were traced", _untraced),
    "size-holds": ("figures held for their size alone, before checks judged only against the filing", _size_held),
}


def picked_filings(name: str, values: dict) -> set[str]:
    """The accessions of the filings holding figures the selector picks."""
    if name not in SELECTORS:
        raise ValueError(f"Unknown re-read selector {name!r}; one of {', '.join(sorted(SELECTORS))}")
    picks = SELECTORS[name][1]
    return {value["source_accession"] for value in values.values() if value.get("source_accession") and picks(value)}
