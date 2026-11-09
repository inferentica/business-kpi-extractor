"""Held figures their filing states stand (quality/stated.py): a figure held only for a sharp change between periods,
whose own filing shows that very figure where it was read, is verified. Run over the stored figures, so figures held
before the rule (Palantir's Q2 2022 TCV, Visa's Q3 2021 cross-border growth) settle without reading anything again."""
from __future__ import annotations

from ..quality.stated import stated_in_filing
from .shared import _is_jump

STATED = "stated in its filing"
# Notes that say nothing against the figure itself: an earlier review's or a check's mark.
_ASIDE = ("confirmed by review", "settled by the checks", "checked against ")


def held_for_jumps_only(notes: list[str]) -> bool:
    return any(_is_jump(note) for note in notes) and all(_is_jump(note) or note.startswith(_ASIDE) for note in notes)


class StatedFigures:
    def _stand_stated_figures(self) -> list[dict]:
        stood = []
        for v in self.values.values():
            notes = list(v.get("notes") or [])
            if (v["validation_status"] == "needs_review" and not v.get("owner_decision") and held_for_jumps_only(notes)
                    and stated_in_filing(v["unit"], float(v["value"]), v.get("locator"), notes)):
                stood.append({**v, "validation_status": "verified", "notes": [STATED]})
        if stood:
            self.log(f"{self.symbol}: {len(stood)} figures held for a sharp change stand, their filing states them")
        return stood
