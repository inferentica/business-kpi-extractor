"""What a figure's notes mean, in one place. A note is the text shown with a figure; a check decides by the note's
reason: a hold on the figure itself, a hold it has from its group, a proof, or an aside (a mark that says nothing
against the figure). Every wording a check decides by is written here once, with how a note is matched to it: the
whole note, its start, or a pattern. A note no reason matches is display text, and still counts as a note where a check
asks whether a figure is held only for some reasons (an unknown note never clears a figure)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..documents.image_pages import ONE_READER, SUMS_TO_AGREED_TOTAL
from ..rules.identity import PLACE_NOT_NAMED, ROW_NAMED_TWICE
from ..rules.scale import SCALE_HEADING_UNSURE, SCALE_NOT_STATED
from ..rules.wording import SIGN_NOT_STATED

HOLD, GROUP, PROOF, ASIDE = "hold", "group", "proof", "aside"


@dataclass(frozen=True)
class Reason:
    code: str
    kind: str
    text: str
    how: str = "whole"  # "whole": the note is the text; "start": it starts with it; "within": it holds it; "pattern"

    def matches(self, note: str) -> bool:
        note = str(note)
        if self.how == "whole":
            return note == self.text
        if self.how == "start":
            return note.startswith(self.text)
        if self.how == "within":
            return self.text in note
        return re.search(self.text, note) is not None


# Wordings the steps write.
SCALE_OUTLIER = "amount far from its other periods or from revenue: a scale not stated"
REREAD_NOTE = "a new reading of this filing differs from the figure served before:"
SHARED_CELL = "read from the same table cell as another listed metric"
SEGMENT_TABLE = "read from a table headed for one segment"
PRECISE_FIGURE = "the full figure, printed rounded elsewhere"
OUTSIDE_REPORTED_PERIODS = "outside the KPI's reported periods"
ANNUAL_REFILED = "full-year figures, re-filed from Q4"
LABELS_CONTRADICTED = "member labels contradict later filings"
OTHER_PART_NOTE = "another part of this breakdown needs review"
NO_XBRL_TOTAL_NOTE = "total matches no figure the company reported in XBRL"
NO_REVENUE_NOTE = "no total revenue to reconcile against"
EQUALS_XBRL = "equals a figure reported in XBRL"
ADDS_TO_XBRL = "parts add up to a figure reported in XBRL"
WITHIN_XBRL = "parts make up nearly all of a figure reported in XBRL"
CONFIRMED_LATER = "confirmed by the next year's release"
RESTATED_LATER = "restated in the next year's release"
TIES_OUT = "its row gives last year's accepted figure for the quarter: it stands on its own"
STATED = "stated in its filing"
REVIEWED = "confirmed by review"
EXPLAINED = "change explained: "
RESCALED = "to match the series"
EXPLAINED_UNPROVEN = "its change was explained by the AI; its filing does not state it"
UNNAMED_XBRL = "it equals no XBRL line that names it"
SIZE_HOLD_LIFTED = "held only for how far it moved or its size, which holds no figure now"

_REASONS = (
    # The figure's own holds.
    # The wordings quality.checks writes: "changed 3.1x in a quarter", "moved +26 in a quarter", "moved +30.5 points in a
    # quarter". Never a note that only starts alike: the owner's "moved from Other Spread Products by the owner".
    Reason("jump", HOLD, r"^(?:changed [\d.]+x|moved [+-]?[\d.,]+(?: points)?) in a quarter$", "pattern"),
    Reason("ratio_jump", HOLD, r"^changed [\d.]+x in a quarter$", "pattern"),  # a jump by ratio, not a small count's move
    Reason("same_as_earlier", HOLD, r"^the same figure as .*: check the column$", "pattern"),
    Reason("scale_outlier", HOLD, SCALE_OUTLIER, "start"),
    Reason("scale_not_stated", HOLD, SCALE_NOT_STATED),
    Reason("scale_heading_unsure", HOLD, SCALE_HEADING_UNSURE, "start"),
    Reason("sign_not_stated", HOLD, SIGN_NOT_STATED),
    Reason("place_not_named", HOLD, PLACE_NOT_NAMED, "start"),
    Reason("row_named_twice", HOLD, ROW_NAMED_TWICE, "start"),
    Reason("shared_cell", HOLD, SHARED_CELL, "start"),
    Reason("segment_page", HOLD, SEGMENT_TABLE, "start"),
    Reason("percent_from_amounts", HOLD, "a percent read from an amounts row", "start"),
    Reason("part_of_table", HOLD, r"likely part of the table$", "pattern"),
    Reason("far_above_quarter", HOLD, "total far above the quarter before", "start"),
    Reason("one_ocr_reader", HOLD, ONE_READER, "start"),
    Reason("reread_differs", HOLD, REREAD_NOTE, "start"),
    Reason("outside_periods", HOLD, OUTSIDE_REPORTED_PERIODS),
    Reason("annual_refiled", HOLD, ANNUAL_REFILED),
    Reason("explained_unproven", HOLD, EXPLAINED_UNPROVEN),
    Reason("unnamed_xbrl", HOLD, UNNAMED_XBRL),
    Reason("labels_contradicted", HOLD, LABELS_CONTRADICTED),
    # Holds a row has from its group.
    Reason("parts_differ", GROUP, "parts differ from the total by", "start"),
    Reason("lines_differ", GROUP, "lines differ from ", "start"),
    Reason("other_part", GROUP, OTHER_PART_NOTE),
    Reason("no_xbrl_total", GROUP, NO_XBRL_TOTAL_NOTE),
    Reason("no_revenue", GROUP, NO_REVENUE_NOTE),
    # Proofs.
    Reason("equals_xbrl", PROOF, EQUALS_XBRL, "start"),
    Reason("adds_to_xbrl", PROOF, ADDS_TO_XBRL, "start"),
    Reason("within_xbrl", PROOF, WITHIN_XBRL, "start"),
    Reason("confirmed_later", PROOF, CONFIRMED_LATER, "start"),
    Reason("restated_later", PROOF, RESTATED_LATER, "start"),
    Reason("ties_out", PROOF, TIES_OUT, "start"),
    Reason("tagged_exact", PROOF, r"^the [\w-]+'s exact (?:figure|year less its three quarters);", "pattern"),
    Reason("stated", PROOF, STATED),
    Reason("agreed_ocr_total", PROOF, SUMS_TO_AGREED_TOTAL, "start"),
    # Asides: a review's or a check's mark.
    Reason("reviewed", ASIDE, REVIEWED, "start"),
    Reason("settled", ASIDE, "settled by the checks", "start"),
    Reason("precise_figure", ASIDE, PRECISE_FIGURE, "start"),
    Reason("checked", ASIDE, "checked against ", "start"),
    Reason("explained", ASIDE, EXPLAINED, "start"),
    Reason("rescaled", ASIDE, RESCALED, "within"),
    Reason("size_lifted", ASIDE, SIZE_HOLD_LIFTED),
)
REASONS = {reason.code: reason for reason in _REASONS}
assert len(REASONS) == len(_REASONS), "a reason code is used twice"


def _reasons(codes: Iterable[str]) -> list[Reason]:
    return [REASONS[code] for code in codes]


def is_(note: str, *codes: str) -> bool:
    """Whether a note gives one of these reasons."""
    return any(reason.matches(note) for reason in _reasons(codes))


def has(notes: Iterable[str] | None, *codes: str) -> bool:
    """Whether any note gives one of these reasons."""
    return any(is_(note, *codes) for note in notes or [])


def note_with(notes: Iterable[str] | None, *codes: str) -> str | None:
    """The first note giving one of these reasons."""
    return next((note for note in notes or [] if is_(note, *codes)), None)


def without(notes: Iterable[str] | None, *codes: str) -> list[str]:
    """The notes, less those giving one of these reasons."""
    return [note for note in notes or [] if not is_(note, *codes)]


def only(notes: Iterable[str] | None, codes: Iterable[str], ignoring: Iterable[str] = ()) -> bool:
    """Whether there are notes besides those ignored, and every one of them gives one of `codes`: a note no reason
    matches is a hold of its own here."""
    left = without(notes, *ignoring)
    return bool(left) and all(is_(note, *codes) for note in left)


def codes_of(notes: Iterable[str] | None) -> list[str | None]:
    """Each note's reason code, None for display text."""
    return [next((reason.code for reason in _REASONS if reason.matches(note)), None) for note in notes or []]


GROUP_HOLDS = ("parts_differ", "other_part", "no_xbrl_total", "no_revenue")
UNANCHORED = ("no_xbrl_total", "no_revenue")
PROOFS = ("equals_xbrl", "adds_to_xbrl", "confirmed_later", "restated_later", "within_xbrl", "ties_out")
ASIDES = ("reviewed", "settled", "checked")
# Holds no check raises since 2026-11-16 (the owner's rule: a figure is judged only by its filing), kept to read old notes.
SIZE_HOLDS = ("jump", "scale_outlier", "part_of_table", "far_above_quarter")
