"""Held figures their filing states stand (quality/stated.py): a figure held only for a sharp change between periods,
whose own filing shows that very figure where it was read, is verified. Run over the stored figures, so figures held
before the rule (Palantir's Q2 2022 TCV, Visa's Q3 2021 cross-border growth) settle without reading anything again.
And served figures that stood on a clear no filing proves are judged again by their filings (2026-11-15)."""
from __future__ import annotations

from ..quality.stated import stated_in_filing
from ..quality.reasons import ASIDES, EXPLAINED_UNPROVEN, STATED, UNNAMED_XBRL, has, only, without
from .shared import equals_named_line, xbrl_figures


def held_for_jumps_only(notes: list[str]) -> bool:
    return only(notes, ["jump"], ignoring=ASIDES)


class StatedFigures:
    def _stand_stated_figures(self) -> list[dict]:
        stood = []
        for v in self.values.values():
            notes = list(v.get("notes") or [])
            if (v["validation_status"] == "needs_review" and not v.get("owner_decision") and held_for_jumps_only(notes)
                    and stated_in_filing(v["unit"], float(v["value"]), v.get("locator"), notes, v.get("kpi_label"))):
                stood.append({**v, "validation_status": "verified", "notes": [STATED]})
        if stood:
            self.log(f"{self.symbol}: {len(stood)} figures held for a sharp change stand, their filing states them")
        return stood

    def _withdraw_unproven_clears(self) -> list[dict]:
        """Served figures that stood on a clear no filing proves, before such clears ended (2026-11-15):
        - a jump the AI explained: the figure stands if its filing states it where it was read, else it is held again,
          until a later filing proves it (KKR's Q2 2021 new capital raised, Goldman's Q2 2017 assets under supervision);
        - a figure proven by equalling an XBRL figure that is not its line goes back to review, where a breakdown's
          parts adding up may prove it again (Goldman's Q1 2016 advisory and underwriting, Investment Banking's sum);
          Wells Fargo's Q4 2024 debt securities loss equalled its segments' reconciling items.
        A series run rescaled to fit its neighbours is left to be read again (ops.reread series-rescaled)."""
        official = xbrl_figures(self.values.values())
        changed = []
        for v in self.values.values():
            if v["method"] != "ai" or v["validation_status"] != "verified" or v.get("owner_decision"):
                continue
            notes = v.get("notes") or []
            if has(notes, "explained") and not has(notes, "stated"):
                if stated_in_filing(v["unit"], float(v["value"]), v.get("locator"), notes, v.get("kpi_label")):
                    changed.append({**v, "notes": [*notes, STATED]})
                else:
                    changed.append({**v, "validation_status": "needs_review", "notes": [*notes, EXPLAINED_UNPROVEN]})
            elif has(notes, "equals_xbrl") and not equals_named_line(official, v):
                if stated_in_filing(v["unit"], float(v["value"]), v.get("locator"), notes, v.get("kpi_label")):
                    changed.append({**v, "notes": [*without(notes, "equals_xbrl"), STATED]})
                else:
                    changed.append({**v, "validation_status": "needs_review", "notes": [*without(notes, "equals_xbrl"), UNNAMED_XBRL]})
        if changed:
            self.log(f"{self.symbol}: {len(changed)} served figures judged again by their filings, their old clear ended")
        return changed
