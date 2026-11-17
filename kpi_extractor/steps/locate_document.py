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
# A KPI whose newest reading is this much older than the list's newest is history the company stopped reporting
# (JPMorgan's Treasury Services, last read 2019): it neither asks for the whole document nor counts as missed.
STOPPED_DAYS = 400
# Past Select's limit, the parts may take this share of what Select sends: they still spare a third of the prompt and
# the Select call (JPMorgan's 83,000 characters against Select's 120,000).
PAST_LIMIT_SHARE = 0.75


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

    def _missed_in_narrowed(self, document: Document, read: list, spec: Spec) -> set[str]:
        """The listed KPIs a reading of a narrowed document did not read: the parts sent may have lost them, so they are
        read again from the whole document (re-check 2026-11-15). Empty when the document went whole."""
        if str(id(document)) not in self._narrowed or str(id(document)) in self._whole_documents:
            return set()
        found = {f"{item.group.key}.{item.kpi.key}" for item in read}
        stopped = self._stopped_keys(spec)
        return {key for key in self._asked_keys(spec) if key not in found and key not in stopped}

    def _placed_history(self) -> dict[str, tuple[str, dict]]:
        """Each KPI's newest AI reading ("group.kpi" -> period end, locator), and the newest that has a place: a figure
        entered or corrected by hand carries none (JPMorgan's advisors, 2026 Q3), and must not hide where it was read."""
        newest: dict[str, str] = {}
        placed: dict[str, tuple[str, dict]] = {}
        for value in list(self.values.values()):
            if value.get("method") != "ai" or value.get("validation_status") == "rejected" or not value.get("period_end"):
                continue
            key = f"{str(value['group_key']).removeprefix('kpi_')}.{value['kpi_key']}"
            end = str(value["period_end"])
            newest[key] = max(end, newest.get(key, end))
            locator = value.get("locator") or {}
            if (locator.get("table") or locator.get("block")) and end >= placed.get(key, ("", {}))[0]:
                placed[key] = (end, locator)
        return {key: (newest[key], placed.get(key, ("", {}))[1]) for key in newest}

    def _stopped_keys(self, spec: Spec) -> set[str]:
        """Listed KPIs the company had stopped reporting by the quarter read: newest reading STOPPED_DAYS before it (or
        before the list's newest, outside a reading). A 2019 filing still asks for Treasury Services, last read 2019."""
        history = self._placed_history()
        ends = [end for end, _locator in history.values()]
        if not ends:
            return set()
        period = getattr(getattr(self, "_local", None), "period", None)
        latest = period if isinstance(period, date) else date.fromisoformat(max(ends)[:10])
        return {key for key in self._asked_keys(spec) if key in history
                and (latest - date.fromisoformat(history[key][0][:10])).days > STOPPED_DAYS}

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
        """The parts of a long document (over 40,000 characters: a bank's or an asset manager's supplement, JPMorgan's
        past 120,000 too) where each listed KPI is: the tables with a row its label or latest row label starts, the two
        nearest the one it was read from when the label recurs ("Total net revenue" on page after page), the passages
        naming it, and the part it was last read from with its neighbours when nothing names it (Apollo's highlight
        bullets). None when the document is short, a KPI was never read or is nowhere like where it was (a KPI noted
        not reported keeps the whole document: Truist's margin went unread when it did not, p4-finpages round 2), or the
        parts are not under 60% of what goes today (the whole document, or Select's choice of at most 120,000
        characters): the usual document then goes. A narrowed reading that misses a KPI reads the whole one again."""
        whole = len(document.render())
        if whole <= NARROW_ABOVE_CHARS:
            return None
        history = self._placed_history()
        stopped = self._stopped_keys(spec)
        found: set[str] = set()
        for key in self._asked_keys(spec):
            if key in stopped:
                continue  # history the company stopped reporting: nothing here to send the whole document for
            group_key, kpi_key = key.split(".", 1)
            kpi = next(k for g in spec.groups if g.key == group_key for k in g.kpis if k.key == kpi_key)
            last = (history.get(key) or ("", {}))[1]
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
        limit = whole * 0.6 if whole <= MAX_DOCUMENT_CHARS else MAX_DOCUMENT_CHARS * PAST_LIMIT_SHARE
        return found if chosen <= limit else None
