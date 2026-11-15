"""The document Locate reads: in a long document sent whole today, the parts where each listed KPI sat last
(p4-finpages), the same text for both readings so they share the prompt cache."""
from __future__ import annotations

import re
from datetime import date

from ..ai import Spec
from ..documents.document import Document
from .ai_reading import MAX_DOCUMENT_CHARS
from .shared import _identity_text

# A document below this is read whole: narrowing it saves little. Above it, Locate reads the tables where each KPI sat
# last when they are under 60% of it (a bank's supplement: 42,000-67,000 tokens a call, whole).
NARROW_ABOVE_CHARS = 40_000


def _table_distance(table: str, last: str) -> int:
    """How far apart two tables or two passages sit ("B_T16" and "B_T18": 2); another exhibit or kind, or no id, last."""
    here, there = re.fullmatch(r"(\w+_[TP])(\d+)", table), re.fullmatch(r"(\w+_[TP])(\d+)", last)
    if not here or not there or here.group(1) != there.group(1):
        return 10_000
    return abs(int(here.group(2)) - int(there.group(2)))


class LocateDocument:
    @staticmethod
    def _asked_keys(spec: Spec) -> list[str]:
        return [f"{group.key}.{kpi.key}" for group in spec.groups for kpi in group.kpis if kpi.key != group.total_kpi]

    def _locate_document(self, document: Document, spec: Spec) -> str:
        """The document Locate reads, the same for both readings so they share the prompt cache: the tables where each
        KPI sat last (_last_places) in a long document, else the document as any task reads it (_prompt_document);
        the whole one again once a narrowed first reading missed a KPI (_read_ai)."""
        key = str(id(document))
        if key not in self._whole_documents and (key in self._narrowed or (ids := self._last_places(document, spec))):
            text = self._narrowed.get(key) or self._narrowed.setdefault(key, document.render_ids(ids, MAX_DOCUMENT_CHARS))
        else:
            text = self._prompt_document(document, spec)
        self._sent_documents[key] = text
        return text

    def _last_places(self, document: Document, spec: Spec) -> set[str] | None:
        """The parts of a document sent whole today (40,000 to 120,000 characters: a bank's or an asset manager's
        supplement) where each listed KPI is: the tables with a row its label or latest row label starts, the two
        nearest the one it was read from when the label recurs ("Total net revenue" on page after page), the passages
        naming it, and the part it was last read from with its neighbours when nothing names it (Apollo's highlight
        bullets). None when the document is short or long (Select already narrows it), a KPI was never read, or the
        parts are not under 60% of it: the usual document then goes."""
        whole = len(document.render())
        if whole <= NARROW_ABOVE_CHARS or not document.fits(MAX_DOCUMENT_CHARS):
            return None
        history = self._ai_history(date.max)
        found: set[str] = set()
        for key in self._asked_keys(spec):
            group_key, kpi_key = key.split(".", 1)
            kpi = next(k for g in spec.groups if g.key == group_key for k in g.kpis if k.key == kpi_key)
            last = (history.get(key) or {}).get("locator") or {}
            names = {_identity_text(kpi.label), _identity_text(str(last.get("row_label") or ""))} - {""}
            tables = [t for t, table in document.tables.items()
                      if any(row and any(_identity_text(row[0]).startswith(name) for name in names) for row in table.rows)]
            blocks = [b for b, block in document.blocks.items() if any(len(n) >= 8 and n in _identity_text(block.text) for n in names)]
            place = str(last.get("table") or last.get("block") or "")
            if not tables and not blocks:
                near = [part for part in document.order if place and _table_distance(part, place) <= 1]
                if not near:
                    return None  # never read, or nowhere like where it was
                found |= set(near)
                continue
            found |= {*sorted(tables, key=lambda t: _table_distance(t, str(last.get("table") or "")))[:2], *blocks[:3]}
        chosen = sum(len((document.tables.get(key) or document.blocks[key]).render()) for key in found)
        return found if chosen <= whole * 0.6 else None
