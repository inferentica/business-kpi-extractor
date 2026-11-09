"""Replays whose source has since been traced carry its AI call. A replay is a figure read by code from its own filing at
the place an earlier filing's reading found, a year before (trace_replay records that filing and the call that placed
it, carried down a chain of replays). When the chain's root was read before calls were traced and is traced later (a
retrace), the replays below it keep no call: TSMC's 106 on 2026-11-09. Each run carries the call down again."""
from __future__ import annotations

from .shared import value_key

_MOST_LINKS = 12  # a chain is one link a year; twelve covers any history kept


class TraceLinks:
    def _link_replays(self) -> list[dict]:
        rows = {value_key(v): v for v in self.values.values() if v["validation_status"] != "rejected"}
        linked: dict[tuple, dict] = {}
        for _ in range(_MOST_LINKS):
            found = False
            for key, v in list(rows.items()):
                at = v.get("locator") or {}
                if v["method"] != "ai" or not at.get("replayed_from") or at.get("placed_by_call") or at.get("ai_call"):
                    continue
                source = rows.get((v["group_key"], v["kpi_key"], str(int(v["fiscal_year"]) - 1), v["fiscal_period"]))
                if not source or source.get("source_accession") != at["replayed_from"]:
                    continue
                placed = (source.get("locator") or {}).get("ai_call") or (source.get("locator") or {}).get("placed_by_call")
                if placed:
                    rows[key] = linked[key] = {**v, "locator": {**at, "placed_by_call": placed}}
                    found = True
            if not found:
                break
        if linked:
            self.log(f"{self.symbol}: {len(linked)} replayed figures traced to the call that placed their source")
        return list(linked.values())
